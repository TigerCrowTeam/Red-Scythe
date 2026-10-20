"""Check hop configuration, whole-burst admission and discontinuity recovery."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from packet_radio.app import RadioApp
from packet_radio.config import (
    RecordingConfig,
    config_from_snapshot,
    configuration_snapshot,
    load_config,
    validate_config,
    validate_network,
)
from packet_radio.endpoint import MemoryEndpoint
from packet_radio.profiles import normalize_definition
from packet_radio.scheduler import FrequencyScheduler
from packet_radio.simulation import SimulatedChannel, SimulatedClock

ROOT = Path(__file__).resolve().parents[2]


def config(peer="a"):
    return load_config(ROOT / f"configs/v1_1/peer-{peer}-hopping.yaml")


@pytest.mark.parametrize(
    "key,value",
    [
        ("dwell_seconds", 0.49),
        ("dwell_seconds", float("nan")),
        ("guard_seconds", 0.099),
        ("guard_seconds", 0.5),
        ("guard_seconds", True),
        ("epoch", -1),
        ("epoch", float("inf")),
        ("frequencies_hz", [915e6]),
        ("frequencies_hz", [915e6, 915e6]),
        ("frequencies_hz", [915e6, float("nan")]),
        ("frequencies_hz", [915e6, True]),
        ("frequencies_hz", [915e6, 0]),
        ("frequencies_hz", "915,916"),
        ("synchronization", "beacon"),
        ("sequence_mode", "random"),
        ("repeated_frequencies", "allow"),
        ("extra", 1),
    ],
)
def test_invalid_hop_contract(key, value):
    definition = deepcopy(config().emulation)
    definition["frequency"][key] = value
    with pytest.raises(ValueError):
        normalize_definition(definition)


def test_hop_pair_snapshot_and_network_mismatch():
    a, b = config(), config("b")
    validate_network({"a": a, "b": b})
    assert config_from_snapshot(configuration_snapshot(a)) == a
    definition = deepcopy(b.emulation)
    definition["frequency"]["epoch"] = 1
    b = replace(b, emulation=definition, frequency=replace(b.frequency, epoch=1))
    with pytest.raises(ValueError, match="schedule differs"):
        validate_network({"a": a, "b": b})


def test_future_epoch_blocks_tx_and_exact_boundaries():
    scheduler = FrequencyScheduler(replace(config().frequency, epoch=100))
    assert not scheduler.state(99.5).transmit_allowed
    assert scheduler.state(100).frequency_hz == 914e6
    assert scheduler.state(101).frequency_hz == 915e6
    assert scheduler.state(103).frequency_hz == 914e6
    assert scheduler.burst_fits(0.1, 100.2)
    assert not scheduler.burst_fits(0.1, 100.85)
    assert not scheduler.burst_fits(-1, 100.2)


def test_ping_pong_and_reacquisition_from_absolute_slot():
    scheduler = FrequencyScheduler(replace(config().frequency, sequence_mode="ping_pong"))
    assert [scheduler.state(i + 0.2).index for i in range(7)] == [0, 1, 2, 1, 0, 1, 2]
    assert scheduler.state(1001.2).index == 1


def test_hopping_iq_capture_is_explicitly_rejected_until_segment_metadata_exists():
    with pytest.raises(ValueError, match="discontinuity metadata"):
        validate_config(
            replace(config(), recording=RecordingConfig(directory="/tmp/capture", iq=True))
        )
    validate_config(
        replace(config(), recording=RecordingConfig(directory="/tmp/capture", iq=False))
    )


def test_max_payload_must_fit_available_dwell():
    cfg = config()
    definition = deepcopy(cfg.emulation)
    definition["frequency"]["guard_seconds"] = 0.49
    with pytest.raises(ValueError, match="cannot fit.*hop dwell"):
        validate_config(
            replace(cfg, emulation=definition, frequency=replace(cfg.frequency, guard_seconds=0.49))
        )


def app_at(phase, fast=False):
    cfg = fast_config() if fast else config()
    cfg = replace(
        cfg,
        sdr=replace(cfg.sdr, decoder_worker_enabled=False),
        link=replace(cfg.link, hardware_timed_io=False),
    )
    clock = SimulatedClock()
    clock.wall = phase
    clock.monotonic = phase
    radio = SimulatedChannel(sample_rate=2e6).radio()
    app = RadioApp(
        cfg,
        sdr=radio,
        endpoint=MemoryEndpoint(),
        wall_clock=lambda: clock.wall,
        monotonic_clock=lambda: clock.monotonic,
    )
    app._retune()
    return app, clock


def test_crossing_burst_dropped_without_transmission_or_retry():
    app, clock = app_at(0.2)
    try:
        clock.wall = clock.monotonic = 0.88
        app._queue_local_payload(b"x" * 1024)
        app._transmit_one()
        stats = app.statistics.snapshot()
        assert stats["hop_burst_rejections"] == stats["transmit_drops"] == 1
        assert stats["packets_transmitted"] == stats["retries"] == 0
        clock.wall = clock.monotonic = 1.4
        app._retune()
        clock.monotonic += 0.11
        app._transmit_one()
        assert app.statistics.snapshot()["packets_transmitted"] == 0
    finally:
        app.close()


@pytest.mark.parametrize("fast", [False, True])
def test_modulation_that_consumes_dwell_is_rejected(fast):
    app, clock = app_at(0.2, fast)
    try:
        clock.wall = clock.monotonic = 0.32 if fast else 0.4
        original = app.modem.modulate

        def slow(wire):
            samples = original(wire)
            clock.wall = clock.monotonic = 1.2
            return samples

        app.modem.modulate = slow
        app._queue_local_payload(b"late")
        app._transmit_one()
        assert app.statistics.snapshot()["hop_burst_rejections"] == 1
        assert app.statistics.snapshot()["samples_transmitted"] == 0
    finally:
        app.close()


def test_missed_whole_cycle_still_flushes_and_clock_reversal_is_counted():
    app, clock = app_at(0.2)
    try:
        app.rx_modem.reset = Mock(wraps=app.rx_modem.reset)
        clock.wall = clock.monotonic = 3.2
        app._retune()
        assert app.current_frequency == 914e6
        assert app.statistics.snapshot()["hop_slots_missed"] == 2
        app.rx_modem.reset.assert_called_once()
        clock.wall = 1.2
        app._retune()
        assert app.statistics.snapshot()["hop_clock_reversals"] == 1
        assert app.current_frequency == 915e6
    finally:
        app.close()


def test_post_tune_settling_blocks_tx_even_mid_dwell():
    app, clock = app_at(0.4)
    try:
        app._queue_local_payload(b"waiting")
        app._transmit_one()
        assert app.statistics.snapshot()["packets_transmitted"] == 0
        clock.wall = clock.monotonic = 0.51
        app._transmit_one()
        assert app.statistics.snapshot()["packets_transmitted"] == 1
    finally:
        app.close()


def test_hopping_frame_capture_reconstructs_schedule_and_replays(tmp_path):
    from packet_radio.recording import load_manifest, manifest_processing, replay_frames

    cfg = replace(
        config(),
        sdr=replace(config().sdr, decoder_worker_enabled=False),
        recording=RecordingConfig(directory=str(tmp_path), iq=False),
    )
    radio = SimulatedChannel(sample_rate=2e6).radio()
    app = RadioApp(cfg, sdr=radio, endpoint=MemoryEndpoint())
    try:
        capture = app.recorder.directory
        wire = app.codec.encode(b"captured hopping frame")
        app.recorder.record_frame(wire)
    finally:
        app.close()
    manifest = load_manifest(capture)
    assert manifest["resolved_configuration"]["frequency"]["frequencies_hz"] == [
        914e6,
        915e6,
        916e6,
    ]
    assert (
        manifest_processing(manifest).codec.decode(list(replay_frames(capture))[0])
        == b"captured hopping frame"
    )


def test_validate_cli_accepts_hopping_pair(monkeypatch, capsys):
    from packet_radio.cli import validate

    monkeypatch.setattr(
        "sys.argv",
        [
            "packet-radio-validate",
            str(ROOT / "configs/v1_1/peer-a-hopping.yaml"),
            str(ROOT / "configs/v1_1/peer-b-hopping.yaml"),
        ],
    )
    assert validate.main() is None
    output = capsys.readouterr().out
    assert "hop_rate=1/s" in output
    assert "network valid: 2 nodes" in output


def fast_config(peer="a"):
    return load_config(ROOT / f"configs/v1_1/peer-{peer}-x310-hopping.yaml")


def test_fast_hop_pair_snapshot_and_device_limit():
    a, b = fast_config(), fast_config("b")
    validate_network({"a": a, "b": b})
    assert config_from_snapshot(configuration_snapshot(a)) == a
    assert a.frequency.interval_seconds == 0.5
    assert a.frequency.guard_seconds == 0.1
    assert config().frequency.interval_seconds == 1
    with pytest.raises(ValueError, match="requires an explicitly selected X300"):
        validate_config(replace(a, sdr=replace(a.sdr, device={"type": "b200"})))


def test_fast_hop_airtime_and_schedule_after_interruption():
    cfg = fast_config()
    scheduler = FrequencyScheduler(cfg.frequency)
    from packet_radio.protocol.registry import resolve_processing

    processing = resolve_processing(cfg)
    wire = processing.codec.payload_size(cfg.endpoint.max_payload_bytes, authenticated=False)
    airtime = processing.make_modem(cfg.sdr).sample_count(wire) / cfg.sdr.sample_rate
    assert airtime == pytest.approx(0.034048)
    duration = (
        cfg.link.hardware_timed_tx_lead_seconds
        + airtime
        + processing.tx_idle_samples(cfg.sdr) / cfg.sdr.sample_rate
        + 0.001
    )
    for slot in range(12):
        now = slot * 0.5
        assert not scheduler.state(now + 0.099).transmit_allowed
        assert scheduler.burst_fits(duration, now + 0.2)
        assert scheduler.burst_fits(duration, now + 0.3)
        assert not scheduler.burst_fits(duration, now + 0.38)
        assert not scheduler.state(now + 0.401).transmit_allowed
        assert scheduler.state(now + 0.2).index == slot % 3
    assert scheduler.state(1001.2).index == 2002 % 3


def test_fast_hop_drops_boundary_packet_and_recovers_without_retry():
    cfg = fast_config()
    cfg = replace(
        cfg,
        sdr=replace(cfg.sdr, decoder_worker_enabled=False),
        link=replace(cfg.link, hardware_timed_io=False),
    )
    clock = SimulatedClock()
    clock.wall = clock.monotonic = 0.2
    app = RadioApp(
        cfg,
        sdr=SimulatedChannel(sample_rate=2e6).radio(),
        endpoint=MemoryEndpoint(),
        wall_clock=lambda: clock.wall,
        monotonic_clock=lambda: clock.monotonic,
    )
    try:
        app._retune()
        clock.wall = clock.monotonic = 0.38
        app._queue_local_payload(b"x" * 1024)
        app._transmit_one()
        assert app.statistics.snapshot()["hop_burst_rejections"] == 1
        assert app.statistics.snapshot()["samples_transmitted"] == 0
        clock.wall = clock.monotonic = 2.01
        app._retune()
        assert app.current_frequency == 915e6
        assert app.statistics.snapshot()["hop_slots_missed"] == 3
        app._queue_local_payload(b"recovered")
        app._transmit_one()
        assert app.statistics.snapshot()["samples_transmitted"] == 0
        clock.wall = clock.monotonic = 2.2
        app._transmit_one()
        assert app.statistics.snapshot()["packets_transmitted"] == 1
        assert app.statistics.snapshot()["retries"] == 0
    finally:
        app.close()


@pytest.mark.parametrize(
    "rate,address,phase", [(3, "x", 0.2), (2, None, 0.2), (2, "x", 0.3), (1, "x", float("nan"))]
)
def test_hardware_hop_check_rejects_invalid_arguments_before_open(rate, address, phase):
    from tools.hopping_check import run

    with pytest.raises(ValueError):
        run(6, ["a", "b"], phase, address, rate)


@pytest.mark.parametrize(
    "rate,address",
    [(float("nan"), "x"), (float("inf"), "x"), (0, "x"), (4.1, "x"), (3, None)],
)
def test_experimental_hop_check_rejects_invalid_arguments_before_open(monkeypatch, rate, address):
    from tools import hopping_check

    opened = Mock(side_effect=AssertionError("must not open hardware"))
    monkeypatch.setattr(hopping_check, "SharedRadios", opened)
    with pytest.raises(ValueError, match="experimental rate"):
        hopping_check.run(6, ["a", "b"], shared_address=address, experimental_rate=rate)
    opened.assert_not_called()
