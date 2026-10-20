"""Two-host probe logic over real loopback UDP; no radios or hardware claims."""

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from packet_radio.config import configuration_snapshot, load_config
from packet_radio.protocol.registry import resolve_processing
from tools import udp_peer_check as probe


def free_ports():
    sockets = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def status(config):
    processing = resolve_processing(config)
    configuration = configuration_snapshot(config)
    configuration.update(packet_format=processing.name, processing=processing.describe())
    return dict.fromkeys(probe.STREAM_COUNTERS, 0) | {
        "configuration": configuration,
        "radio_ready": True,
        "modulation": config.sdr.modulation,
        "configured_bit_rate_bps": config.sdr.bit_rate_bps,
        "configured_center_frequency_hz": config.center_frequency_hz,
        "sample_rate_hz": config.sdr.sample_rate,
    }


@pytest.mark.parametrize("modes", [("traffic", "traffic"), ("echo", "reflector")])
def test_two_coordinated_probes_exchange_exact_payloads(monkeypatch, modes):
    ports = free_ports()
    configs = [load_config(f"configs/examples/peer-{p}-bpsk.yaml") for p in "ab"]
    configs = [
        replace(c, endpoint=replace(c.endpoint, bind_port=ports[1 - i], destination_port=ports[i]))
        for i, c in enumerate(configs)
    ]
    monkeypatch.setattr(
        probe,
        "fetch_status",
        lambda url: status(configs[0] if url.endswith("peer1") else configs[1]),
    )
    start = time.time() + 0.3
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(
                probe.run_probe,
                c,
                run_id="loopback-test",
                start_at=start,
                duration=0.2,
                rate=20,
                size=64,
                mode=mode,
                grace=0.15,
                status_url=f"http://peer{c.node_id}",
            )
            for c, mode in zip(configs, modes)
        ]
        reports = [f.result(timeout=5) for f in futures]
    assert all(r["passed"] and r["received"] == 4 for r in reports)
    assert not any("one_way" in k for r in reports for k in r)
    if modes[0] == "echo":
        assert reports[0]["round_trip_p95_seconds"] < 1
        assert reports[1]["reflected"] == 4


@pytest.mark.parametrize(
    "change", [{"start_at": 0}, {"rate": float("nan")}, {"size": 1}, {"duration": -1}]
)
def test_invalid_workload_never_opens_socket_or_queries_status(monkeypatch, change):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid workload accessed I/O")

    monkeypatch.setattr(probe, "fetch_status", unexpected)
    monkeypatch.setattr(probe.socket, "socket", unexpected)
    args = dict(run_id="test", start_at=time.time() + 5, rate=1, duration=1, size=64)
    args.update(change)
    with pytest.raises(ValueError):
        probe.run_probe(load_config("configs/examples/peer-a-bpsk.yaml"), **args)


def test_missing_peer_fails_and_releases_delivery_socket(monkeypatch):
    ports = free_ports()
    c = load_config("configs/examples/peer-a-bpsk.yaml")
    c = replace(c, endpoint=replace(c.endpoint, bind_port=ports[0], destination_port=ports[1]))
    monkeypatch.setattr(probe, "fetch_status", lambda url: status(c))
    r = probe.run_probe(
        c, run_id="missing", start_at=time.time() + 0.05, duration=0.05, rate=20, size=64, grace=0
    )
    assert not r["passed"] and r["missing_sequences"] == [0]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", ports[1]))


def test_payload_identifies_run_peer_sequence_and_every_byte():
    original = probe.payload("one", 1, 2, 256, 64)
    assert len(original) == 64
    assert original != probe.payload("two", 1, 2, 256, 64)
    assert original != probe.payload("one", 2, 1, 256, 64)
    assert original != probe.payload("one", 1, 2, 0, 64)
    with pytest.raises(ValueError):
        probe.payload("one", 1, 2, 0, 1)


@pytest.mark.parametrize("fault", ["duplicate", "corruption", "wrong_run", "stream_error"])
def test_bad_traffic_or_stream_counters_cannot_pass(monkeypatch, fault):
    ports = free_ports()
    c = load_config("configs/examples/peer-a-bpsk.yaml")
    c = replace(c, endpoint=replace(c.endpoint, bind_port=ports[0], destination_port=ports[1]))
    calls = []

    def snapshot(url):
        calls.append(url)
        result = status(c)
        if len(calls) == 2 and fault == "stream_error":
            result["tx_underflows"] = 1
        return result

    monkeypatch.setattr(probe, "fetch_status", snapshot)
    start = time.time() + 0.1

    def inject():
        time.sleep(max(0, start + 0.02 - time.time()))
        data = probe.payload("fault|0.1|10|64", 2, 1, 0, 64)
        if fault == "corruption":
            data = data[:-1] + bytes([data[-1] ^ 1])
        if fault == "wrong_run":
            data = probe.payload("other|0.1|10|64", 2, 1, 0, 64)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(data, ("127.0.0.1", ports[1]))
            if fault == "duplicate":
                sender.sendto(data, ("127.0.0.1", ports[1]))

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(inject)
        report = probe.run_probe(
            c, run_id="fault", start_at=start, duration=0.1, rate=10, size=64, grace=0.05
        )
        future.result(timeout=2)
    assert not report["passed"]
    if fault == "stream_error":
        assert report["traffic_passed"] and not report["stream_passed"]
    else:
        assert not report["traffic_passed"]


def test_profile_mismatch_fails_before_udp(monkeypatch):
    c = load_config("configs/examples/peer-a-qpsk.yaml")
    old = replace(c, sdr=replace(c.sdr, rs_parity_bytes=0))
    monkeypatch.setattr(probe, "fetch_status", lambda url: status(old))

    def unexpected(*args):
        pytest.fail("mismatched profile opened UDP")

    monkeypatch.setattr(probe.socket, "socket", unexpected)
    with pytest.raises(ValueError, match="effective configuration"):
        probe.run_probe(c, run_id="mismatch", start_at=time.time() + 5, duration=1, rate=1, size=64)


@pytest.mark.parametrize(
    "path", ["configs/examples/peer-a-bpsk.yaml", "configs/v1_1/peer-a-simple.yaml"]
)
def test_probe_accepts_actual_application_processing_snapshot(monkeypatch, path):
    from packet_radio.app import RadioApp
    from packet_radio.config import AuthenticationConfig
    from packet_radio.endpoint import MemoryEndpoint
    from packet_radio.simulation import SimulatedChannel

    ports = free_ports()
    c = load_config(path)
    c = replace(
        c,
        authentication=AuthenticationConfig(),
        endpoint=replace(c.endpoint, bind_port=ports[0], destination_port=ports[1]),
    )
    app = RadioApp(c, sdr=SimulatedChannel().radio(), endpoint=MemoryEndpoint())
    try:
        monkeypatch.setattr(probe, "fetch_status", lambda url: app.statistics.snapshot())
        report = probe.run_probe(
            c,
            run_id="actual-status",
            start_at=time.time() + 0.05,
            duration=0.05,
            rate=20,
            size=64,
            grace=0,
        )
        assert report["missing_sequences"] == [0]
        assert not report["passed"]  # No remote endpoint: only configuration acceptance is tested.
    finally:
        app.close()


def test_processing_mismatch_fails_before_udp(monkeypatch):
    c = load_config("configs/examples/peer-a-bpsk.yaml")
    snapshot = status(c)
    snapshot["configuration"]["processing"]["format"] = "wrong-format"
    monkeypatch.setattr(probe, "fetch_status", lambda url: snapshot)

    def unexpected(*args):
        pytest.fail("mismatched processing opened UDP")

    monkeypatch.setattr(probe.socket, "socket", unexpected)
    with pytest.raises(ValueError, match="effective configuration"):
        probe.run_probe(c, run_id="mismatch", start_at=time.time() + 5, duration=1, rate=1, size=64)
