"""Process incoming samples and deliver accepted radio packets.

Functions consume decoder output or IQ from the owning RadioApp, collect receive
diagnostics, validate packet identities and replay windows, process ACKs and
synchronization beacons, and deliver accepted payloads to the local endpoint.
RadioApp retains ownership of resources and shared link state.
"""

from __future__ import annotations

import logging
import math
import struct
from typing import TYPE_CHECKING

import numpy as np

from packet_radio.protocol.framing import (
    BROADCAST_ID,
    AuthenticationError,
    PacketKind,
    ProtocolVersionError,
    RadioPacket,
)
from packet_radio.protocol.replay_protection import ReplayResult

if TYPE_CHECKING:
    from packet_radio.app import RadioApp
LOG = logging.getLogger(__name__)


def receive_radio(app: RadioApp) -> bool:
    received = False
    diagnostics = getattr(app.sdr, "receive_diagnostics", None)
    if diagnostics is not None:
        for name, value in diagnostics().items():
            previous = app._receive_diagnostics.get(name, 0)
            if value > previous:
                app.statistics.increment(name, value - previous)
            app._receive_diagnostics[name] = value
    if app.decoder_worker is not None:
        worker_diagnostics = app.decoder_worker.diagnostics()
        app.channel_power = float(worker_diagnostics.pop("decoder_channel_power"))
        for name, value in worker_diagnostics.items():
            if name in {
                "decoder_queue_depth",
                "decoder_realtime_utilization",
                "decoder_processing_p95_ms",
                "decoder_processing_max_ms",
            }:
                app.statistics.set_gauge(name, value)
                continue
            previous = app._receive_diagnostics.get(name, 0)
            if value > previous:
                app.statistics.increment(name, value - previous)
            app._receive_diagnostics[name] = int(value)
        batches = []
        first = app.decoder_worker.receive(timeout=0.002)
        if first is not None:
            batches.append(first)
            for _ in range(63):
                batch = app.decoder_worker.receive()
                if batch is None:
                    break
                batches.append(batch)
        decoded = [frame for batch in batches for frame in batch.frames]
        corrected = sum(batch.fec_corrected_bits for batch in batches)
        uncorrectable = sum(batch.fec_uncorrectable_frames for batch in batches)
        failure_score = next(
            (batch.failure_score for batch in reversed(batches) if batch.failure_score is not None),
            None,
        )
        failure_corrected_score = next(
            (
                batch.failure_corrected_score
                for batch in reversed(batches)
                if batch.failure_corrected_score is not None
            ),
            None,
        )
    else:
        timed_receive = getattr(app.sdr, "receive_timed", None)
        if timed_receive is None:
            samples = app.sdr.receive()
            sample_time_ns = None
        else:
            block = timed_receive()
            samples = block.samples
            sample_time_ns = block.time_ns
        if app.recorder is not None:
            app.recorder.record_iq(samples)
        app.channel_power = float(np.mean(np.abs(samples) ** 2)) if len(samples) else 0.0
        corrected_before = app.rx_modem.fec_corrected_bits
        uncorrectable_before = app.rx_modem.fec_uncorrectable_frames
        feed_timed = getattr(app.rx_modem, "feed_timed", None)
        if feed_timed is None:
            decoded = [(wire, None) for wire in app.rx_modem.feed(samples)]
        else:
            decoded = feed_timed(samples, sample_time_ns)
        corrected = app.rx_modem.fec_corrected_bits - corrected_before
        uncorrectable = app.rx_modem.fec_uncorrectable_frames - uncorrectable_before
        failure_score = app.rx_modem.last_fec_failure_score
        failure_corrected_score = app.rx_modem.last_fec_failure_corrected_score
    channel_power = getattr(app.sdr, "channel_power", None)
    if channel_power is not None:
        app.channel_power = max(app.channel_power, float(channel_power()))
    app.statistics.set_gauge("estimated_signal_power", app.channel_power)
    if corrected:
        app.statistics.increment("fec_corrected_bits", corrected)
    if uncorrectable:
        app.statistics.increment("fec_uncorrectable_frames", uncorrectable)
        app.statistics.increment("invalid_packets", uncorrectable)
        app.statistics.set_gauge("last_fec_failure_score", failure_score)
        app.statistics.set_gauge(
            "last_fec_failure_corrected_score",
            failure_corrected_score,
        )
    for wire, arrival_time_ns in decoded:
        try:
            packet = RadioPacket.decode(wire, app.verification_keys)
        except ValueError as exc:
            if app.recorder is not None:
                app.recorder.record_frame(wire, str(exc))
            LOG.warning("discarded invalid radio packet: %s", exc)
            app.statistics.increment("invalid_packets")
            if isinstance(exc, AuthenticationError):
                app.statistics.increment("authentication_failures")
            elif isinstance(exc, ProtocolVersionError):
                app.statistics.increment("protocol_version_mismatches")
            elif "CRC" in str(exc):
                app.statistics.increment("crc_failures")
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
                    acknowledgement = app.link.ack_for(packet)
                    if acknowledgement is not None:
                        app.link.enqueue(acknowledgement)
                continue
        if packet.kind == PacketKind.ACK:
            if app.link.acknowledge(packet):
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
        acknowledgement = app.link.ack_for(packet)
        if acknowledgement is not None:
            if app.link.enqueue(acknowledgement):
                LOG.debug(
                    "queued ACK for node %d sequence %d",
                    acknowledgement.destination,
                    acknowledgement.sequence,
                )
        if app.link.is_duplicate(packet):
            LOG.debug(
                "suppressed duplicate from node %d sequence %d", packet.source, packet.sequence
            )
            continue
        try:
            app.endpoint.send(packet.payload)
        except OSError as exc:
            app.statistics.increment("endpoint_output_errors")
            LOG.warning("local payload delivery failed: %s", exc)
        else:
            app.statistics.increment("payload_packets_delivered")
            app.statistics.increment("payload_bytes_delivered", len(packet.payload))
    return received


def sync_is_fresh(app: RadioApp, leader_time: float, received_at: float) -> bool:
    if app.last_sync_leader_time is None or app.last_sync_received_at is None:
        return True
    expected = app.last_sync_leader_time + (received_at - app.last_sync_received_at)
    delta = leader_time - expected
    return (
        -app.config.authentication.sync_max_age_seconds
        <= delta
        <= app.config.authentication.sync_max_future_seconds
    )
