"""Prevent host/backend delay from moving a hopping burst beyond its admitted window."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packet_radio.protocol.registry import resolve_processing
from packet_radio.scheduler import FrequencyScheduler
from packet_radio.transmission import hop_transmit_target
from tests.configuration.test_auto_payload import draft


@pytest.mark.parametrize(
    "before,after,slot,allowed",
    [
        (0.057, 0.058, 0, True),  # uncertainty bracket still leaves room
        (0.060, 0.080, 0, False),  # clock read consumes the remaining margin
        (0.075, 0.076, 0, False),  # whole waveform would cross the trailing guard
        (0.074, 0.074, 0, False),  # nominal airtime fits, but the host margin does not
        (0.060, 0.059, 0, False),  # host clock reversed
        (0.120, 0.121, 0, False),  # another slot even if a tune is delayed
        (0.001, 0.002, 0, False),
    ],  # leading guard
)
def test_target_accounts_for_read_uncertainty_and_both_guards(before, after, slot, allowed):
    source = draft(8)
    source.set("profile.frequency.guard_seconds", 0.03)
    source.set("profile.packet.max_payload_bytes", 240)
    cfg = source.validate().config
    processing = resolve_processing(cfg)
    modem = processing.make_modem(cfg.sdr)
    samples = modem.sample_count(processing.codec.payload_size(cfg.endpoint.max_payload_bytes))
    app = SimpleNamespace(
        config=cfg,
        processing=processing,
        scheduler=FrequencyScheduler(cfg.frequency),
        current_frequency=cfg.frequency.frequencies_hz[0],
        wall_clock=Mock(side_effect=[before, after]),
        sdr=SimpleNamespace(hardware_time=Mock(return_value=1_000_000_000)),
    )
    target = hop_transmit_target(app, samples, slot)
    assert target == (1_008_000_000 if allowed else None)


def test_hopping_submission_uses_fixed_target_instead_of_relative_backend_target():
    from packet_radio.app import RadioApp
    from packet_radio.endpoint import MemoryEndpoint
    from packet_radio.simulation import SimulatedChannel, SimulatedClock

    source = draft(8)
    source.set("node.runtime.decoder_worker_enabled", False)
    cfg = source.validate().config
    clock = SimulatedClock()
    clock.wall = clock.monotonic = 0.01
    radio = SimulatedChannel(sample_rate=2e6).radio()
    radio.hardware_time = Mock(return_value=1_000_000_000)
    radio.transmit_at = Mock()
    radio.transmit_with_lead = Mock(side_effect=AssertionError("must not move target"))
    app = RadioApp(
        cfg,
        sdr=radio,
        endpoint=MemoryEndpoint(),
        wall_clock=lambda: clock.wall,
        monotonic_clock=lambda: clock.monotonic,
    )
    try:
        app._retune()
        assert app._queue_local_payload(b"x" * cfg.endpoint.max_payload_bytes)
        clock.wall = clock.monotonic = 0.002
        app._transmit_one()
        radio.transmit_at.assert_not_called()  # Local settling alone is insufficient.
        clock.wall = clock.monotonic = 0.015
        app._transmit_one()
        radio.transmit_at.assert_called_once()
        assert radio.transmit_at.call_args.args[1] == 1_008_000_000
        radio.transmit_with_lead.assert_not_called()
    finally:
        app.close()


@pytest.mark.parametrize("overrun", [False, True])
def test_automatic_tune_deadline_reserves_a_full_settling_guard(overrun):
    from packet_radio.app import RadioApp
    from packet_radio.endpoint import MemoryEndpoint
    from packet_radio.hop_budget import transition_allowance
    from packet_radio.simulation import SimulatedChannel, SimulatedClock

    source = draft(8)
    source.set("profile.frequency.guard_seconds", 0.001)
    source.set("node.runtime.decoder_worker_enabled", False)
    cfg = source.validate().config
    clock = SimulatedClock()
    clock.wall = clock.monotonic = 0.0001
    radio = SimulatedChannel(sample_rate=2e6).radio()
    original = radio.tune

    def tune(frequency):
        original(frequency)
        clock.wall = clock.monotonic = transition_allowance(cfg) + (0.001 if overrun else -0.001)

    radio.tune = tune
    app = RadioApp(
        cfg,
        sdr=radio,
        endpoint=MemoryEndpoint(),
        wall_clock=lambda: clock.wall,
        monotonic_clock=lambda: clock.monotonic,
    )
    try:
        app._retune()
        assert app.statistics.snapshot()["hop_tune_deadlines_missed"] == int(overrun)
        assert app._hop_ready_at == clock.monotonic + cfg.frequency.guard_seconds
    finally:
        app.close()


def test_boundary_during_receive_wait_does_not_consume_queued_packet():
    from packet_radio.app import RadioApp
    from packet_radio.endpoint import MemoryEndpoint
    from packet_radio.simulation import SimulatedChannel, SimulatedClock

    source = draft(8)
    source.set("profile.frequency.guard_seconds", 0.001)
    source.set("node.runtime.decoder_worker_enabled", False)
    source.set("node.runtime.hardware_timed_io", False)
    cfg = source.validate().config
    clock = SimulatedClock()
    clock.wall = clock.monotonic = 0.01
    app = RadioApp(
        cfg,
        sdr=SimulatedChannel(sample_rate=2e6).radio(),
        endpoint=MemoryEndpoint(),
        wall_clock=lambda: clock.wall,
        monotonic_clock=lambda: clock.monotonic,
    )
    try:
        app._retune()
        assert app._queue_local_payload(b"next hop")
        clock.wall = clock.monotonic = cfg.frequency.interval_seconds + 0.02
        app._transmit_one()
        assert len(app.link.queue) == 1
        assert app.statistics.snapshot()["hop_burst_rejections"] == 0
        assert app.statistics.snapshot()["packets_transmitted"] == 0
    finally:
        app.close()
