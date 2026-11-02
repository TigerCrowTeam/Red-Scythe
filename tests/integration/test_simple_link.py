"""Exercise simple operating modes through the real application and waveform path."""

from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest

from packet_radio.app import RadioApp
from packet_radio.config import (
    AuthenticationConfig,
    EndpointConfig,
    FrequencyConfig,
    LinkConfig,
    RadioConfig,
    SDRConfig,
    validate_config,
)
from packet_radio.endpoint import MemoryEndpoint
from packet_radio.protocol.simple import FrameSettings
from packet_radio.protocol.simple_link import SimpleLinkSettings, SimpleProfile
from packet_radio.simulation import SimulatedChannel, SimulatedClock
from packet_radio.status import render_endpoint


def config(node=1, **kwargs):
    return RadioConfig(
        node,
        3 - node,
        "follower" if node == 1 else "leader",
        SDRConfig(
            modulation="2fsk",
            sample_rate=2_000_000,
            bandwidth=1_000_000,
            frequency_deviation_hz=125_000,
        ),
        FrequencyConfig((915_000_000,), 0, guard_seconds=0),
        **kwargs,
    )


def pair(mode_a="transmit-only", mode_b="receive-only", *, settings=None, cfg=None):
    clock = SimulatedClock()
    channel = SimulatedChannel(sample_rate=2_000_000, seed=1101)
    apps = []
    for i, mode in enumerate((mode_a, mode_b), 1):
        app = RadioApp(
            cfg or config(i),
            sdr=channel.radio(),
            endpoint=MemoryEndpoint(),
            processing=SimpleProfile(link=settings or SimpleLinkSettings(operation=mode)),
            wall_clock=lambda: clock.wall,
            monotonic_clock=lambda: clock.monotonic,
        )
        app._retune()
        apps.append(app)
    return clock, channel, apps


def test_1000_exact_packets_each_direction_no_control_traffic():
    clock, channel, apps = pair("bidirectional", "bidirectional")
    rng = np.random.default_rng(1101)
    payloads = [bytes(range(256)), b"same", b"same", b"\x55" * 32 + bytes.fromhex("a7c35a9e")]
    payloads += [bytes(rng.integers(0, 256, 64, dtype=np.uint8)) for _ in range(996)]
    try:
        for tx, rx in (apps, apps[::-1]):
            for payload in payloads:
                clock.advance(0.05)
                tx.endpoint.inject(payload)
                tx.step()
                for _ in range(20):
                    rx.step()
            assert list(rx.endpoint.inbound) == payloads
        for app in apps:
            stats = app.statistics.snapshot()
            assert stats["packets_transmitted"] == stats["packets_received"] == 1000
            assert stats["acknowledgements_sent"] == stats["retries"] == 0
            assert stats["synchronization_losses"] == 0
        clock.advance(60)
        for app in apps:
            app.step()
        assert channel.stats["transmissions"] == 2000
    finally:
        for app in apps:
            app.close()


@pytest.mark.parametrize("mode", ["transmit-only", "receive-only", "bidirectional"])
def test_direction_health_and_idle_without_peer(mode):
    clock, channel, apps = pair(mode, "receive-only")
    a, b = apps
    try:
        clock.advance(100)
        for app in apps:
            app.step()
        assert channel.stats["transmissions"] == 0
        assert render_endpoint("/healthz", a.statistics.snapshot(), "follower", 1)[0] == 200
        assert a.statistics.snapshot()["synchronized"] is None
        assert a.statistics.snapshot()["configuration"]["packet_format"] == "simple-frame-v1"
        a.endpoint.inject(b"hello")
        a.step()
        b.step()
        assert list(b.endpoint.inbound) == ([] if mode == "receive-only" else [b"hello"])
        if mode == "receive-only":
            assert a.statistics.snapshot()["mode_rejections"] == 1
            assert a.statistics.snapshot()["samples_transmitted"] == 0
        else:
            assert a.statistics.snapshot()["packets_transmitted"] == 1
        a.close()
        assert render_endpoint("/healthz", a.statistics.snapshot(), "follower", 1)[0] == 503
    finally:
        for app in apps:
            app.close()


def test_tx_only_never_decodes_or_delivers_incoming_samples():
    clock, _, apps = pair("transmit-only", "transmit-only")
    try:
        for app in apps:
            app.endpoint.inject(b"hello")
        for app in apps:
            app.step()
        clock.advance(1)
        for app in apps:
            app.step()
        assert all(not app.endpoint.inbound for app in apps)
        assert all(app.statistics.snapshot()["packets_received"] == 0 for app in apps)
    finally:
        for app in apps:
            app.close()


@pytest.mark.parametrize("input_kind", ["payload", "frame"])
def test_framed_input_preserves_exact_bytes_and_payload_validation(input_kind):
    clock, _, apps = pair(
        settings=SimpleLinkSettings(operation="transmit-only", input_kind=input_kind)
    )
    a = apps[0]
    try:
        wire = bytes.fromhex("0004000102ffc8b4")
        payload = bytes.fromhex("000102ff")
        captured = []
        a.sdr.transmit = lambda samples: captured.append(samples.copy())
        a.endpoint.inject(wire if input_kind == "frame" else payload)
        a.step()
        np.testing.assert_array_equal(captured[0], a.modem.modulate(wire))
        for bad in (
            [b"", b"x" * 1025]
            if input_kind == "payload"
            else [b"", wire[:-1], wire + b"x", wire[:-1] + b"x"]
        ):
            a.endpoint.inject(bad)
        clock.advance(1)
        a.step()
        assert len(captured) == 1
        assert a.statistics.snapshot()["invalid_payloads"] > 0
    finally:
        for app in apps:
            app.close()


def test_queue_limits_include_inflight_and_failure_never_retries():
    clock, _, apps = pair(cfg=config(link=LinkConfig(queue_size=2)))
    a = apps[0]
    try:
        assert a._queue_local_payload(b"1")
        q = a.link.next_packet(True, True)
        assert a._queue_local_payload(b"2")
        assert not a._queue_local_payload(b"3")
        assert a.statistics.snapshot()["queue_depth"] == 2
        a.link.requeue(q)
        a.sdr.transmit = Mock(side_effect=OSError("USB failure"))
        a.step()
        assert a.statistics.snapshot()["transmit_errors"] == 1
        assert a.statistics.snapshot()["transmit_drops"] == 1
        assert len(a.link.queue) == 1
        clock.advance(1)
        a.step()
        clock.advance(1)
        a.step()
        assert a.sdr.transmit.call_count == 2
        assert a.statistics.snapshot()["retries"] == 0
        assert a.statistics.snapshot()["queue_depth"] == 0
    finally:
        for app in apps:
            app.close()


def test_gap_interval_turnaround_and_airtime_queue_limit():
    opts = SimpleLinkSettings(
        operation="bidirectional",
        minimum_gap_seconds=0.003,
        minimum_interval_seconds=0.1,
        turnaround_seconds=0.02,
    )
    clock, _, apps = pair(settings=opts, cfg=config(link=LinkConfig(queue_airtime_seconds=0.04)))
    a = apps[0]
    try:
        assert a._queue_local_payload(b"x" * 1024)
        assert not a._queue_local_payload(b"x" * 1024)
        a.step()
        assert a.statistics.snapshot()["packets_transmitted"] == 0
        clock.advance(0.021)
        a.step()
        assert a.statistics.snapshot()["packets_transmitted"] == 1
        assert a._queue_local_payload(b"next")
        clock.advance(0.05)
        a.step()
        assert a.statistics.snapshot()["packets_transmitted"] == 1
        clock.advance(0.091)
        a.step()
        assert a.statistics.snapshot()["packets_transmitted"] == 2
    finally:
        for app in apps:
            app.close()


@pytest.mark.parametrize(
    "change",
    [
        {"sdr": SDRConfig(modulation="unknown")},
        {"link": LinkConfig(hardware_clock_sync=True, hardware_timed_io=True)},
        {"link": LinkConfig(tdd_period_seconds=1)},
        {"frequency": FrequencyConfig((915e6, 916e6), 0, guard_seconds=0)},
        {
            "authentication": AuthenticationConfig(
                private_key_file="missing", public_key_files={1: "a", 2: "b"}
            )
        },
    ],
)
def test_unsupported_combinations_fail_before_resources(monkeypatch, change):
    hardware = Mock()
    endpoint = Mock()
    monkeypatch.setattr("packet_radio.app.UHDRadio", hardware)
    monkeypatch.setattr("packet_radio.app.UDPEndpoint", endpoint)
    with pytest.raises(ValueError):
        RadioApp(replace(config(), **change), processing=SimpleProfile())
    hardware.assert_not_called()
    endpoint.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"operation": "unknown"},
        {"input_kind": "iq"},
        {"minimum_gap_seconds": 0},
        {"minimum_interval_seconds": float("nan")},
        {"turnaround_seconds": -1},
        {"minimum_gap_seconds": True},
    ],
)
def test_invalid_link_settings(kwargs):
    with pytest.raises(ValueError):
        SimpleLinkSettings(**kwargs)


def test_fixed_length_profile_and_received_error_paths():
    profile = SimpleProfile(FrameSettings(length_bytes=0, fixed_payload_bytes=4))
    cfg = config(endpoint=EndpointConfig(max_payload_bytes=4))
    validate_config(cfg, processing=profile)
    app = RadioApp(cfg, sdr=Mock(), endpoint=MemoryEndpoint(), processing=profile)
    try:
        assert not app._queue_local_payload(b"123")
        wire = app.codec.encode(b"1234")
        assert not app.behavior.receive(app, [(wire[:-1] + bytes([wire[-1] ^ 1]), None)])
        assert app.statistics.snapshot()["crc_failures"] == 1
        app.endpoint.send = Mock(side_effect=OSError("local UDP"))
        assert app.behavior.receive(app, [(wire, None)])
        assert app.statistics.snapshot()["endpoint_output_errors"] == 1
        assert app.statistics.snapshot()["payload_packets_delivered"] == 0
    finally:
        app.close()


def test_loss_is_not_retried_and_later_packet_is_delivered():
    clock, channel, apps = pair()
    a, b = apps
    try:
        channel.packet_loss = 1
        a.endpoint.inject(b"lost")
        a.step()
        b.step()
        clock.advance(17)
        channel.packet_loss = 0
        a.endpoint.inject(b"after interruption")
        a.step()
        b.step()
        assert list(b.endpoint.inbound) == [b"after interruption"]
        assert a.statistics.snapshot()["packets_transmitted"] == 2
        assert a.statistics.snapshot()["retries"] == 0
    finally:
        for app in apps:
            app.close()


def test_bad_crc_sample_candidate_is_counted_and_following_frame_recovers():
    from tests.modems.test_simple_fsk import reference

    _, _, apps = pair()
    a, b = apps
    try:
        good = b.codec.encode(b"123456789")
        bad = good[:-1] + bytes([good[-1] ^ 1])
        b.sdr.incoming.append(np.concatenate((reference(bad), np.zeros(128), reference(good))))
        for _ in range(10):
            b.step()
        assert list(b.endpoint.inbound) == [b"123456789"]
        assert b.statistics.snapshot()["candidate_rejections"] >= 1
        assert b.statistics.snapshot()["invalid_packets"] >= 1
        assert b.statistics.snapshot()["samples_transmitted"] == 0
    finally:
        for app in apps:
            app.close()


@pytest.mark.parametrize(
    "changed", ["repeat", "shaping", "fec", "recording", "replay", "zero-deviation", "gaussian"]
)
def test_additional_incompatible_parameters_are_not_silently_ignored(changed):
    from packet_radio.config import RecordingConfig

    cfg = config()
    if changed == "zero-deviation":
        cfg = replace(cfg, sdr=replace(cfg.sdr, frequency_deviation_hz=0))
    if changed == "gaussian":
        cfg = replace(cfg, sdr=replace(cfg.sdr, gaussian_bt=0.3))
    if changed == "repeat":
        cfg = replace(cfg, sdr=replace(cfg.sdr, bit_repetitions=2))
    if changed == "shaping":
        cfg = replace(cfg, sdr=replace(cfg.sdr, rrc_rolloff=0.35))
    if changed == "fec":
        cfg = replace(cfg, sdr=replace(cfg.sdr, rs_parity_bytes=16))
    if changed == "recording":
        cfg = replace(cfg, recording=RecordingConfig(directory="/tmp/unused-simple-capture"))
    if changed == "replay":
        cfg = replace(
            cfg, authentication=AuthenticationConfig(replay_state_file="/tmp/unused-replay")
        )
    with pytest.raises(ValueError):
        validate_config(cfg, processing=SimpleProfile())


def test_maximum_preframed_datagram_and_udp_delivery():
    import socket

    from packet_radio.endpoint import UDPEndpoint

    clock, _, apps = pair()
    a, b = apps
    output = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    output.bind(("127.0.0.1", 0))
    output.settimeout(1)
    cfg = EndpointConfig(bind_port=0, destination_port=output.getsockname()[1])
    a.endpoint.close()
    b.endpoint.close()
    a.endpoint = UDPEndpoint(cfg)
    b.endpoint = UDPEndpoint(cfg)
    a.behavior.input_kind = "frame"
    payload = bytes(range(256)) * 4
    try:
        output.sendto(a.codec.encode(payload), a.endpoint.socket.getsockname())
        for _ in range(30):
            a.step()
            b.step()
            clock.advance(0.001)
        assert output.recvfrom(2048)[0] == payload
        assert a.statistics.snapshot()["invalid_payloads"] == 0
        assert a.statistics.snapshot()["packets_transmitted"] == 1
    finally:
        output.close()
        for app in apps:
            app.close()


def test_unsent_deadline_deferral_preserves_packet_without_counting_a_drop():
    from packet_radio.hardware.uhd import TransmitDeadlineError

    _, _, apps = pair(cfg=config(link=LinkConfig(queue_size=2)))
    a, _ = apps
    try:
        assert a._queue_local_payload(b"keep")
        transmit = Mock(side_effect=TransmitDeadlineError("lead exhausted before send"))
        a.sdr.transmit = transmit
        a.step()
        snapshot = a.statistics.snapshot()
        assert snapshot["tx_lead_deferrals"] == 1
        assert snapshot["queue_depth"] == 1
        assert snapshot["transmit_errors"] == 0
        assert snapshot["transmit_drops"] == 0
        a.sdr.transmit = Mock()
        a.step()
        a.sdr.transmit.assert_called_once()
        assert a.statistics.snapshot()["packets_transmitted"] == 1
    finally:
        for app in apps:
            app.close()


def test_simple_queue_expiry_does_not_create_ack_or_retry_traffic():
    clock, _, apps = pair(cfg=config(link=LinkConfig(queue_max_age_seconds=1)))
    a, _ = apps
    try:
        a._queue_local_payload(b"expired")
        clock.advance(1)
        assert a.link.next_packet(False, False) is None
        assert not a.link.queue
        snapshot = a.statistics.snapshot()
        assert snapshot["queue_expired"] == 1
        assert snapshot["packets_transmitted"] == 0
        assert snapshot["acknowledgements_sent"] == 0
        assert snapshot["retries"] == 0
        assert a._queue_local_payload(b"fresh")
        a.sdr.transmit = Mock()
        a.step()
        a.sdr.transmit.assert_called_once()
    finally:
        for app in apps:
            app.close()
