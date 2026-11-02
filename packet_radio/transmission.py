"""Schedule and submit outgoing radio bursts.

Functions operate on the owning RadioApp state to select reliable-link traffic,
prepare protected bursts, enforce TDD windows and submit timed transmissions.
The application retains resource ownership and shared synchronization state.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import TYPE_CHECKING

from packet_radio.hardware.uhd import TransmitDeadlineError
from packet_radio.hop_budget import HOST_MARGIN_SECONDS, automatic, transition_allowance

if TYPE_CHECKING:
    from packet_radio.app import RadioApp
LOG = logging.getLogger(__name__)


def new_session_id() -> int:
    """Create a time-ordered boot/session identifier with collision entropy."""
    return ((time.time_ns() // 1_000_000) << 16 | secrets.randbits(16)) & 0xFFFFFFFFFFFFFFFF


def hop_transmit_target(app: RadioApp, sample_count: int, slot: int) -> int | None:
    """Fix a device-time target within conservative host-time bounds for this hop.

    Bracket the clock read, check both ends of its uncertainty interval, then
    submit an immutable target. Backend preparation may reject a late target,
    but cannot move it later across the trailing guard.
    """
    before = app.wall_clock()
    device_now = app.sdr.hardware_time()
    after = app.wall_clock()
    lead = app.config.link.hardware_timed_tx_lead_seconds
    earliest, latest = before + lead, after + lead
    first, last = app.scheduler.state(earliest), app.scheduler.state(latest)
    airtime = (
        sample_count + app.processing.tx_idle_samples(app.config.sdr)
    ) / app.config.sdr.sample_rate + 0.001
    if automatic(app.config):
        airtime += HOST_MARGIN_SECONDS
    if (
        after < before
        or first.slot != slot
        or last.slot != slot
        or first.frequency_hz != app.current_frequency
        or not first.transmit_allowed
        or (
            automatic(app.config)
            and app.config.frequency.interval_seconds - first.seconds_to_change
            < app.config.frequency.guard_seconds + transition_allowance(app.config)
        )
        or not app.scheduler.burst_fits(airtime, now=latest)
    ):
        LOG.debug(
            "hop target rejected: before=%f after=%f lead=%f airtime=%f slot=%s first=%s last=%s",
            before,
            after,
            lead,
            airtime,
            slot,
            first,
            last,
        )
        return None
    return device_now + int(lead * 1e9)


def transmit_one(app: RadioApp) -> None:
    state = app.scheduler.state()
    if len(app.config.frequency.frequencies_hz) > 1 and (
        state.slot != app._hop_slot or state.frequency_hz != app.current_frequency
    ):
        # The boundary can pass while step() waits for received data. Leave
        # queued packets untouched until the next step retunes this slot.
        return
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
    clear = app.behavior.channel_clear(app)
    tdd_allowed = (
        scheduled is not None
        if app.config.link.hardware_slot_scheduling
        else app._tdd_transmit_allowed()
    )
    selection_started = app.statistics.timing.start()
    # Timed lead may overlap the peer's settling wait; the RF target must not.
    peer_ready = not automatic(app.config) or (
        app.config.frequency.interval_seconds - state.seconds_to_change
        >= app.config.frequency.guard_seconds
        + transition_allowance(app.config)
        - (
            app.config.link.hardware_timed_tx_lead_seconds
            if app.config.link.hardware_timed_io
            else 0
        )
    )
    queued = app.link.next_packet(
        clear,
        state.transmit_allowed
        and peer_ready
        and (
            len(app.config.frequency.frequencies_hz) == 1
            or app.monotonic_clock() >= app._hop_ready_at
        )
        and scheduled_channel_allowed
        and tdd_allowed
        and app.behavior.transmit_allowed(app),
    )
    app.statistics.timing.finish("packet_selection_ms", selection_started)
    if queued is None:
        return
    if app.statistics.timing.enabled:
        app.statistics.timing.observe(
            "selected_packet_age_ms", (app.monotonic_clock() - queued.enqueued_at) * 1000
        )
    try:
        target_ns: int | None = scheduled[0] if scheduled is not None else None
        target_leader_ns: int | None = scheduled[1] if scheduled is not None else None
        target_ns = app.behavior.prepare(app, queued, target_ns)
        prepared = app._prepared_burst
        if prepared is not None and prepared[0] is queued:
            _, wire, samples = prepared
        else:
            started = app.statistics.timing.start()
            wire = app.codec.encode(queued.packet, app.signing_key)
            app.statistics.timing.finish("packet_encode_ms", started)
            started = app.statistics.timing.start()
            samples = app.modem.modulate(wire)
            app.statistics.timing.finish("packet_modulate_ms", started)
            if app.config.link.tdd_period_seconds > 0 and app.behavior.cacheable(queued):
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
        if len(app.config.frequency.frequencies_hz) > 1:
            # Recheck after modulation: CPU work may have consumed the dwell.
            duration = (
                len(samples) + app.processing.tx_idle_samples(app.config.sdr)
            ) / app.config.sdr.sample_rate
            duration += 0.001
            if automatic(app.config):
                duration += HOST_MARGIN_SECONDS
            if app.config.link.hardware_timed_io:
                duration += timed_lead
            fresh = app.scheduler.state()
            if (
                fresh.slot != state.slot
                or fresh.frequency_hz != app.current_frequency
                or not app.scheduler.burst_fits(duration)
                or app.monotonic_clock() < app._hop_ready_at
            ):
                LOG.debug(
                    "hop preparation rejected: duration=%f initial=%s fresh=%s",
                    duration,
                    state,
                    fresh,
                )
                app.statistics.increment("hop_burst_rejections")
                app._prepared_burst = None
                app.behavior.transmit_failed(app, queued)
                return
        if not app._tdd_burst_fits(len(samples), timed_lead, target_leader_ns):
            app.link.requeue(queued)
            return
        submit_started = app.statistics.timing.start()
        if app.config.link.hardware_timed_io:
            if target_ns is None and len(app.config.frequency.frequencies_hz) > 1:
                target_ns = hop_transmit_target(app, len(samples), state.slot)
                if target_ns is None:
                    app.statistics.increment("hop_burst_rejections")
                    app._prepared_burst = None
                    app.behavior.transmit_failed(app, queued)
                    return
                app.sdr.transmit_at(samples, target_ns)
            elif target_ns is None:
                # The backend chooses the target after its own preparation;
                # IQ ownership and stale-event handling must not consume lead.
                target_ns = app.sdr.transmit_with_lead(
                    samples,
                    int(app.config.link.hardware_timed_tx_lead_seconds * 1e9),
                )
                if app.statistics.timing.enabled:
                    app.statistics.set_gauge("scheduled_tx_target_ns", target_ns)
            else:
                lead_ns = target_ns - app.sdr.hardware_time()
                app.statistics.set_gauge("scheduled_tx_lead_ms", lead_ns / 1e6)
                app.statistics.set_gauge("scheduled_tx_target_ns", target_ns)
                # A beacon's lead starts before encoding its timestamp. Its
                # remaining lead is necessarily shorter after modulation.
                # Only fixed TDD opportunities require the full slot margin.
                minimum_lead_ns = int(
                    (
                        app.config.link.hardware_timed_tx_lead_seconds
                        if scheduled is not None
                        else app.config.link.hardware_min_tx_lead_seconds
                    )
                    * 1e9
                )
                if lead_ns <= 0 or lead_ns < minimum_lead_ns:
                    app.statistics.increment("tdd_opportunities_missed")
                    app.statistics.increment("tx_lead_deferrals")
                    queued.next_attempt = app.monotonic_clock()
                    app.link.requeue(queued)
                    return
                app.sdr.transmit_at(samples, target_ns)
                app.statistics.increment("scheduled_tdd_transmissions")
        else:
            app.sdr.transmit(samples)
        app.statistics.timing.finish("radio_transmit_ms", submit_started)
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
    except TransmitDeadlineError:
        # The adapter guarantees no samples were submitted. Keep DATA cached;
        # SYNC is re-encoded next time so its authenticated timestamp stays true.
        app.statistics.increment("tx_lead_deferrals")
        queued.next_attempt = app.monotonic_clock()
        app.link.requeue(queued)
    except (OSError, ValueError) as exc:
        LOG.error("radio transmission failed: %s", exc)
        app.statistics.increment("transmit_errors")
        app.behavior.transmit_failed(app, queued)


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
    total_samples = sample_count + app.processing.tx_idle_samples(app.config.sdr)
    airtime = total_samples / app.config.sdr.sample_rate + 0.001
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
