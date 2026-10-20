"""Implement generic-v5 identity, reliability, replay and synchronization behavior.

GenericV5Behavior owns protocol-specific decisions while RadioApp retains resource
ownership. It preserves the existing packet/ACK/SYNC semantics and exposes them
through the link-behavior interface for the selected processing profile.
"""

from __future__ import annotations

import logging
import math
import struct
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from packet_radio.protocol.framing import BROADCAST_ID, PacketKind, RadioPacket
from packet_radio.protocol.reliability import QueuedPacket, ReliableLink
from packet_radio.protocol.replay_protection import ReplayProtector, ReplayResult

if TYPE_CHECKING:
    from packet_radio.app import RadioApp

LOG = logging.getLogger(__name__)


class GenericV5Behavior:
    receive_enabled = True
    requires_peer = True
    operation = "bidirectional"
    input_kind = "payload"

    def input_limit(self, app: RadioApp) -> int:
        return app.config.endpoint.max_payload_bytes

    def initialize(self, app: RadioApp) -> None:
        from packet_radio.transmission import new_session_id

        app.session_id = new_session_id() or 1
        app.replay_protector = ReplayProtector(
            app.config.authentication.replay_window,
            app.config.authentication.replay_state_file,
        )
        app.last_sync_leader_time = None
        app.last_sync_received_at = None

    def make_link(self, app: RadioApp) -> ReliableLink:
        return ReliableLink(
            app.config.node_id,
            app.config.link,
            clock=app.monotonic_clock,
            statistics=app.statistics,
            session_id=app.session_id,
            encoded_size=app._on_air_size,
            airtime=app._packet_airtime,
        )

    def queue(self, app: RadioApp, payload: bytes, destination: int, kind=None) -> bool:
        link = cast(ReliableLink, app.link)
        packet = RadioPacket(
            app.config.node_id,
            destination,
            app.sequence,
            payload,
            PacketKind.DATA if kind is None else kind,
            app.session_id,
        )
        return link.enqueue(packet)

    def receive(self, app: RadioApp, decoded: list[tuple[bytes, int | None]]) -> bool:
        link = cast(ReliableLink, app.link)
        received = False
        for wire, arrival_time_ns in decoded:
            frame_started = app.statistics.timing.start()
            try:
                packet = app.codec.decode(wire, app.verification_keys)
            except ValueError as exc:
                if app.recorder is not None:
                    app.recorder.record_frame(wire, str(exc))
                LOG.warning("discarded invalid radio packet: %s", exc)
                app.statistics.increment("invalid_packets")
                counter = app.codec.error_counter(exc)
                if counter is not None:
                    app.statistics.increment(counter)
                continue
            if app.recorder is not None:
                app.recorder.record_frame(wire)
            app.statistics.mark_activity("frame")
            received = True
            app.statistics.increment("packets_received")
            app.statistics.increment("bytes_received", app.modem.frame_size(len(wire)))
            if packet.source != app.config.peer_id:
                app.statistics.increment("unexpected_peer_packets")
                continue
            allowed_destination = (
                packet.destination == app.config.node_id
                or packet.kind == PacketKind.SYNC
                and packet.destination == BROADCAST_ID
            )
            if not allowed_destination:
                app.statistics.increment("wrong_destination_packets")
                continue
            if (
                packet.kind == PacketKind.DATA
                and not 1 <= len(packet.payload) <= app.config.endpoint.max_payload_bytes
            ):
                app.statistics.increment("invalid_packets")
                continue
            if app.verification_keys is not None:
                replay_result = app.replay_protector.observe(packet)
                if replay_result is not ReplayResult.NEW:
                    if replay_result is ReplayResult.REJECTED:
                        app.statistics.increment("replay_rejections")
                    else:
                        app.statistics.mark_activity("peer")
                        app.statistics.increment("duplicates_suppressed")
                        acknowledgement = link.ack_for(packet)
                        if acknowledgement is not None:
                            link.enqueue(acknowledgement)
                    continue
            if packet.kind == PacketKind.ACK:
                ack_started = app.statistics.timing.start()
                acknowledged = link.acknowledge(packet)
                app.statistics.timing.finish("ack_handling_ms", ack_started)
                if acknowledged:
                    app.statistics.mark_activity("peer")
                    app.last_rx = app.monotonic_clock()
                continue
            if packet.kind == PacketKind.SYNC:
                if (
                    app.config.timing_role == "follower"
                    and packet.source == app.config.peer_id
                    and len(packet.payload) in (8, 16)
                ):
                    leader_time = struct.unpack("!d", packet.payload[:8])[0]
                    received_at = app.monotonic_clock()
                    # Keep the synchronization estimate in the same clock
                    # domain as the scheduler.  ``received_at`` above is
                    # monotonic and is only used for freshness/liveness;
                    # using it for the wall-clock offset would mix unrelated
                    # epochs and shift TDD slot boundaries.
                    received_wall = app.wall_clock()
                    if not math.isfinite(leader_time) or not app._sync_is_fresh(
                        leader_time, received_at
                    ):
                        LOG.warning("discarded stale authenticated synchronization beacon")
                        app.statistics.increment("sync_freshness_rejections")
                        continue
                    if (
                        len(packet.payload) == 16
                        and app.config.link.hardware_clock_sync
                        and arrival_time_ns is not None
                    ):
                        leader_tx_ns = struct.unpack("!Q", packet.payload[8:])[0]
                        estimate = app.hardware_clock_sync.observe(leader_tx_ns, arrival_time_ns)
                        app.statistics.set_gauge("hardware_clock_offset_ns", estimate.offset_ns)
                        app.statistics.set_gauge("hardware_clock_drift_ppm", estimate.drift_ppm)
                        app.statistics.set_gauge("hardware_clock_observations", estimate.samples)
                    app.statistics.mark_activity("peer")
                    app.last_rx = received_at
                    offset = app.scheduler.synchronize(leader_time, received_at=received_wall)
                    app.last_sync_leader_time = leader_time
                    app.last_sync_received_at = received_at
                    LOG.info("synchronized to leader clock (offset %.3f seconds)", offset)
                    if app.recovering:
                        app.statistics.increment("reacquisitions")
                    app.recovering = False
                    app.statistics.set_gauge("synchronized", True)
                    # Synchronization can change the desired hopping slot, but
                    # avoid disrupting RX with a redundant retune on a fixed
                    # channel (or when the synchronized slot is unchanged).
                    if app.scheduler.state().frequency_hz != app.current_frequency:
                        app.current_frequency = None
                continue
            app.statistics.mark_activity("peer")
            app.last_rx = app.monotonic_clock()
            acknowledgement = link.ack_for(packet)
            if acknowledgement is not None:
                if link.enqueue(acknowledgement):
                    LOG.debug(
                        "queued ACK for node %d sequence %d",
                        acknowledgement.destination,
                        acknowledgement.sequence,
                    )
            if link.is_duplicate(packet):
                LOG.debug(
                    "suppressed duplicate from node %d sequence %d", packet.source, packet.sequence
                )
                continue
            try:
                app.endpoint.send(packet.payload)
                app.statistics.timing.finish("frame_handling_to_delivery_ms", frame_started)
            except OSError as exc:
                app.statistics.increment("endpoint_output_errors")
                LOG.warning("local payload delivery failed: %s", exc)
            else:
                app.statistics.increment("payload_packets_delivered")
                app.statistics.increment("payload_bytes_delivered", len(packet.payload))
        return received

    def prepare(self, app: RadioApp, queued: QueuedPacket, target_ns: int | None) -> int | None:
        link = cast(ReliableLink, app.link)
        from packet_radio.transmission import new_session_id

        if not queued.sequence_assigned:
            queued.packet = replace(queued.packet, sequence=app.sequence, session_id=app.session_id)
            queued.sequence_assigned = True
            app.sequence = (app.sequence + 1) & 0xFFFFFFFF
            if app.sequence == 0:
                app.session_id = max(app.session_id + 1, new_session_id())
                link.session_id = app.session_id
        if queued.packet.kind == PacketKind.SYNC:
            if app.config.link.hardware_timed_io and target_ns is None:
                # A sync beacon must embed its target before modulation, so
                # give only this infrequent control packet a longer budget.
                target_ns = app.sdr.hardware_time() + int(
                    app.config.link.hardware_sync_tx_lead_seconds * 1e9
                )
            # Timestamp at airtime, not enqueue time; guard/backoff delays must
            # not look like clock error to recovering nodes.
            sync_time = (
                target_ns / 1e9
                if app.config.link.hardware_clock_sync and target_ns is not None
                else app.wall_clock()
                + (
                    app.config.link.hardware_timed_tx_lead_seconds
                    if app.config.link.hardware_timed_io
                    else 0.0
                )
            )
            payload = struct.pack("!d", sync_time)
            if app.config.link.hardware_clock_sync and target_ns is not None:
                payload += struct.pack("!Q", target_ns)
            queued.packet = RadioPacket(
                queued.packet.source,
                queued.packet.destination,
                queued.packet.sequence,
                payload,
                PacketKind.SYNC,
                queued.packet.session_id,
            )
        return target_ns

    def cacheable(self, queued: QueuedPacket) -> bool:
        return queued.packet.kind != PacketKind.SYNC

    def channel_clear(self, app: RadioApp) -> bool:
        link = cast(ReliableLink, app.link)
        # TDD windows already provide collision avoidance; residual peer RX power
        # must not defer the next return window.
        clear = (
            True
            if app.config.link.tdd_period_seconds > 0
            else app.channel_power < app.config.link.channel_busy_threshold
        )
        if not clear and link.queue:
            app.statistics.increment("channel_busy_deferrals")
            link.back_off()
        return clear

    def transmit_allowed(self, app: RadioApp) -> bool:
        return not app.recovering

    def transmit_failed(self, app: RadioApp, queued: QueuedPacket) -> None:
        link = cast(ReliableLink, app.link)
        queued.next_attempt = app.monotonic_clock() + app.config.link.backoff_max_seconds
        link.requeue(queued)

    def tick(self, app: RadioApp) -> None:
        now = app.monotonic_clock()
        if app.config.timing_role == "leader" and now >= app.next_sync_at:
            app._queue(struct.pack("!d", app.wall_clock()), BROADCAST_ID, kind=PacketKind.SYNC)
            app.next_sync_at = now + 2.0

    def recover(self, app: RadioApp) -> None:
        if app.config.timing_role == "leader":
            return
        now = app.monotonic_clock()
        if not app.recovering:
            if now - app.last_rx < app.config.frequency.sync_loss_seconds:
                return
            LOG.warning("radio synchronization lost; beginning channel scan")
            app.statistics.increment("synchronization_losses")
            app.statistics.set_gauge("synchronized", False)
            app.recovering = True
            app.scan_frequencies = app.scheduler.scan_order()
            app.scan_index = 0
            app.next_scan_at = 0.0
        if now >= app.next_scan_at:
            frequency = app.scan_frequencies[app.scan_index]
            app.scan_index = (app.scan_index + 1) % len(app.scan_frequencies)
            app.sdr.tune(frequency)
            app.current_frequency = frequency
            app.statistics.set_gauge("active_frequency_hz", frequency)
            app.next_scan_at = now + app.config.frequency.reacquire_dwell_seconds

    def sync_is_fresh(self, app: RadioApp, leader_time: float, received_at: float) -> bool:
        if app.last_sync_leader_time is None or app.last_sync_received_at is None:
            return True
        expected = app.last_sync_leader_time + (received_at - app.last_sync_received_at)
        delta = leader_time - expected
        return (
            -app.config.authentication.sync_max_age_seconds
            <= delta
            <= app.config.authentication.sync_max_future_seconds
        )
