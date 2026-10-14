"""Schedule and submit outgoing radio bursts.

Functions operate on the owning RadioApp state to select reliable-link traffic,
prepare protected bursts, enforce TDD windows and submit timed transmissions.
The application retains resource ownership and shared synchronization state.
"""

from __future__ import annotations

import logging
import secrets
import struct
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from packet_radio.protocol.framing import PacketKind, RadioPacket

if TYPE_CHECKING:
    from packet_radio.app import RadioApp
LOG = logging.getLogger(__name__)


def new_session_id() -> int:
    """Create a time-ordered boot/session identifier with collision entropy."""
    return ((time.time_ns() // 1_000_000) << 16 | secrets.randbits(16)) & 0xFFFFFFFFFFFFFFFF


def transmit_one(app: RadioApp) -> None:
    state = app.scheduler.state()
    scheduled = app._next_hardware_tdd_opportunity()
    scheduled_channel_allowed = True
    if scheduled is not None:
        # A look-ahead opportunity can fall just beyond a frequency boundary
        # even though the application is still on the old channel. Never queue
        # that burst into the SDR; wait for the retune and boundary guard.
        target_leader_seconds = scheduled[1] / 1e9
        target_state = app.scheduler.state(target_leader_seconds - app.scheduler.clock_offset)
        scheduled_channel_allowed = (
            target_state.transmit_allowed and target_state.frequency_hz == app.current_frequency
        )
    # In scheduled TDD mode the slot assignment is the collision-avoidance
    # mechanism.  RX power from the preceding peer burst can remain in the
    # last sample block and would otherwise defer the entire return slot.
    clear = (
        True
        if app.config.link.tdd_period_seconds > 0
        else app.channel_power < app.config.link.channel_busy_threshold
    )
    if not clear and app.link.queue:
        app.statistics.increment("channel_busy_deferrals")
        app.link.back_off()
    tdd_allowed = (
        scheduled is not None
        if app.config.link.hardware_slot_scheduling
        else app._tdd_transmit_allowed()
    )
    queued = app.link.next_packet(
        clear,
        state.transmit_allowed and scheduled_channel_allowed and tdd_allowed and not app.recovering,
    )
    if queued is None:
        return
    if not queued.sequence_assigned:
        queued.packet = replace(queued.packet, sequence=app.sequence, session_id=app.session_id)
        queued.sequence_assigned = True
        app.sequence = (app.sequence + 1) & 0xFFFFFFFF
        if app.sequence == 0:
            app.session_id = max(app.session_id + 1, new_session_id())
            app.link.session_id = app.session_id
    try:
        target_ns: int | None = scheduled[0] if scheduled is not None else None
        target_leader_ns: int | None = scheduled[1] if scheduled is not None else None
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
        prepared = app._prepared_burst
        if prepared is not None and prepared[0] is queued:
            _, wire, samples = prepared
        else:
            wire = queued.packet.encode(app.signing_key)
            samples = app.modem.modulate(wire)
            if app.config.link.tdd_period_seconds > 0 and queued.packet.kind != PacketKind.SYNC:
                # Keep just one prepared burst if encoding consumes this
                # window. A later window can then admit the full RF burst
                # without paying modulation time again. Never cache SYNC:
                # its authenticated timestamp must match its actual target.
                app._prepared_burst = (queued, wire, samples)
        timed_lead = (
            0.0
            if scheduled is not None
            else (
                app.config.link.hardware_sync_tx_lead_seconds
                if target_ns is not None
                else app.config.link.hardware_timed_tx_lead_seconds
            )
        )
        if not app._tdd_burst_fits(len(samples), timed_lead, target_leader_ns):
            app.link.requeue(queued)
            return
        if app.config.link.hardware_timed_io:
            if target_ns is None:
                # Choose the normal data target after modulation and after
                # acquiring the RX/TX I/O lock. This prevents CPU work and a
                # pending readStream timeout from consuming the short lead.
                target_ns = app.sdr.transmit_with_lead(
                    samples,
                    int(app.config.link.hardware_timed_tx_lead_seconds * 1e9),
                )
            else:
                lead_ns = target_ns - app.sdr.hardware_time()
                app.statistics.set_gauge("scheduled_tx_lead_ms", lead_ns / 1e6)
                app.statistics.set_gauge("scheduled_tx_target_ns", target_ns)
                # A beacon's lead starts before encoding its timestamp. Its
                # remaining lead is necessarily shorter after modulation.
                # Only fixed TDD opportunities require the full slot margin.
                minimum_lead_ns = (
                    int(app.config.link.hardware_timed_tx_lead_seconds * 1e9)
                    if scheduled is not None
                    else 0
                )
                if lead_ns <= 0 or lead_ns < minimum_lead_ns:
                    app.statistics.increment("tdd_opportunities_missed")
                    queued.next_attempt = app.monotonic_clock()
                    app.link.requeue(queued)
                    return
                app.sdr.transmit_at(samples, target_ns)
                app.statistics.increment("scheduled_tdd_transmissions")
        else:
            app.sdr.transmit(samples)
        app._last_tx_end = app.monotonic_clock()
        if app.config.link.tdd_period_seconds > 0:
            period = app.config.link.tdd_period_seconds
            slot = int(app._tdd_time() / period)
            if slot == app._tdd_slot_index:
                app._tdd_bursts_sent += 1
        if scheduled is not None:
            opportunity_id = scheduled[3]
            if (
                app._last_tdd_opportunity_id is not None
                and opportunity_id > app._last_tdd_opportunity_id + 1
                and app.link.queue
            ):
                app.statistics.increment(
                    "tdd_opportunities_missed",
                    opportunity_id - app._last_tdd_opportunity_id - 1,
                )
            app._last_tdd_opportunity_id = opportunity_id
        if app._prepared_burst is not None and app._prepared_burst[0] is queued:
            app._prepared_burst = None
        app.link.transmitted(
            queued,
            encoded_bytes=app.modem.frame_size(len(wire)),
            samples=len(samples),
            airtime_seconds=len(samples) / app.config.sdr.sample_rate,
        )
    except (OSError, ValueError) as exc:
        LOG.error("radio transmission failed: %s", exc)
        app.statistics.increment("transmit_errors")
        queued.next_attempt = app.monotonic_clock() + app.config.link.backoff_max_seconds
        app.link.requeue(queued)


def tdd_transmit_allowed(app: RadioApp) -> bool:
    """Give the leader and follower separate half-duplex transmit windows."""
    period = app.config.link.tdd_period_seconds
    if period <= 0:
        return True
    guard = app.config.link.tdd_guard_seconds
    inter_burst = app.config.link.tdd_inter_burst_seconds
    if inter_burst is None:
        inter_burst = guard
    if app.monotonic_clock() < app._last_tx_end + inter_burst:
        return False
    slot = int(app._tdd_time() / period)
    if slot != app._tdd_slot_index:
        app._tdd_slot_index = slot
        app._tdd_bursts_sent = 0
    burst_limit = (
        app.config.link.tdd_leader_bursts_per_slot
        if app.config.timing_role == "leader"
        else app.config.link.tdd_follower_bursts_per_slot
    )
    if app._tdd_bursts_sent >= burst_limit:
        return False
    phase = app._tdd_time() % period
    midpoint = period / 2.0
    if app.config.link.tdd_staggered_windows:
        if app.config.timing_role == "leader":
            return 0 <= phase < midpoint - guard
        return midpoint <= phase < period - guard
    if app.config.timing_role == "leader":
        return guard <= phase < midpoint - guard
    return midpoint + guard <= phase < period - guard


def tdd_burst_fits(
    app: RadioApp,
    sample_count: int,
    timed_lead_seconds: float | None = None,
    target_leader_ns: int | None = None,
) -> bool:
    """Return whether a complete burst fits before the current TDD boundary."""
    period = app.config.link.tdd_period_seconds
    if period <= 0:
        return True
    guard = app.config.link.tdd_guard_seconds
    phase = (target_leader_ns / 1e9 if target_leader_ns is not None else app._tdd_time()) % period
    midpoint = period / 2.0
    if app.config.link.tdd_staggered_windows and app.config.timing_role == "leader":
        slot_end = midpoint - guard
    elif app.config.link.tdd_staggered_windows:
        slot_end = period - guard
    elif app.config.timing_role == "leader":
        slot_end = midpoint - guard
    else:
        slot_end = period - guard
    # The SDR keeps a short tail in its host/FPGA FIFO after writeStream
    # returns.  Include that drain margin in slot admission; otherwise a
    # packet that technically fits can still occupy the peer's guard time.
    airtime = sample_count / app.config.sdr.sample_rate + 0.001
    if app.config.link.hardware_timed_io and target_leader_ns is None:
        airtime += (
            app.config.link.hardware_timed_tx_lead_seconds
            if timed_lead_seconds is None
            else timed_lead_seconds
        )
    return phase + airtime <= slot_end


def next_hardware_tdd_opportunity(app: RadioApp) -> tuple[int, int, int, int] | None:
    """Return the next exact local/leader TX target within the lookahead.

    Opportunity centers are fixed in leader hardware time. This removes the
    dependency on the Python loop happening to enter a short TDD window.
    """
    link = app.config.link
    if (
        not link.hardware_slot_scheduling
        or not link.hardware_timed_io
        or link.tdd_period_seconds <= 0
        or (app.config.timing_role != "leader" and not app.hardware_clock_sync.ready)
    ):
        return None
    local_now_ns = app.sdr.hardware_time()
    leader_now_ns = (
        local_now_ns
        if app.config.timing_role == "leader"
        else app.hardware_clock_sync.local_to_leader(local_now_ns)
    )
    period_ns = int(round(link.tdd_period_seconds * 1e9))
    midpoint_ns = period_ns // 2
    guard_ns = int(round(link.tdd_guard_seconds * 1e9))
    if app.config.timing_role == "leader":
        start_ns, end_ns = 0, midpoint_ns - guard_ns
        count = link.tdd_leader_bursts_per_slot
    else:
        start_ns, end_ns = midpoint_ns, period_ns - guard_ns
        count = link.tdd_follower_bursts_per_slot
    # Leave host/USB submission margin beyond the device's required lead.
    # Selecting a target exactly at the minimum makes any Python scheduling
    # delay turn an otherwise valid opportunity into a timed-late write.
    submission_margin_ns = int(link.hardware_slot_submission_margin_seconds * 1e9)
    minimum_ns = leader_now_ns + int(link.hardware_timed_tx_lead_seconds * 1e9)
    minimum_ns += submission_margin_ns
    cycle = minimum_ns // period_ns
    candidates: list[tuple[int, int, int]] = []
    for candidate_cycle in (cycle, cycle + 1):
        for ordinal in range(count):
            offset_ns = start_ns + (2 * ordinal + 1) * (end_ns - start_ns) // (2 * count)
            target_ns = candidate_cycle * period_ns + offset_ns
            if target_ns >= minimum_ns:
                candidates.append((target_ns, ordinal, candidate_cycle * count + ordinal))
    if not candidates:
        return None
    leader_target_ns, ordinal, opportunity_id = min(candidates)
    if leader_target_ns - leader_now_ns > int(link.hardware_slot_lookahead_seconds * 1e9):
        return None
    local_target_ns = (
        leader_target_ns
        if app.config.timing_role == "leader"
        else app.hardware_clock_sync.leader_to_local(leader_target_ns)
    )
    return local_target_ns, leader_target_ns, ordinal, opportunity_id


def tdd_time(app: RadioApp) -> float:
    compensation = (
        app.config.link.tdd_receive_delay_compensation_seconds
        if app.config.timing_role != "leader"
        else 0.0
    )
    if app.config.link.hardware_clock_sync and app.hardware_clock_sync.ready:
        local_ns = app.sdr.hardware_time()
        return app.hardware_clock_sync.local_to_leader(local_ns) / 1e9 + compensation
    if app.config.link.hardware_clock_sync and app.config.timing_role == "leader":
        return app.sdr.hardware_time() / 1e9 + compensation
    return app.wall_clock() + app.scheduler.clock_offset + compensation
