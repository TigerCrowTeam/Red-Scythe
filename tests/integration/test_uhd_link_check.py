"""Runner failure paths must report failure without opening hardware."""

import json

import pytest

from tools import uhd_link_check


def test_socket_denial_still_writes_failure_report(tmp_path, monkeypatch):
    output = tmp_path / "result.json"
    monkeypatch.setattr("sys.argv", ["uhd_link_check", "--output", str(output)])

    def denied(*args, **kwargs):
        raise PermissionError("socket access denied")

    monkeypatch.setattr(uhd_link_check.socket, "socket", denied)
    with pytest.raises(PermissionError, match="socket access denied"):
        uhd_link_check.main()
    report = json.loads(output.read_text())
    assert report["errors"] == ["PermissionError('socket access denied')"]
    assert not report.get("traffic_passed")


@pytest.mark.parametrize(
    "options",
    [
        ["--duration", "nan"],
        ["--tx-batch-packets", "16"],
        ["--shared-device", "--tx-batch-packets", "0"],
        ["--shared-device", "--tx-batch-packets", "257"],
        ["--rate", "inf"],
        ["--ack-timeout", "0"],
        ["--tdd-period", "-1"],
        ["--echo", "--restart-follower"],
        ["--tdd-period", "0.2", "--scheduled-tdd"],
    ],
)
def test_invalid_workload_fails_before_socket_or_hardware(tmp_path, monkeypatch, options):
    monkeypatch.setattr(
        "sys.argv", ["uhd_link_check", "--output", str(tmp_path / "result.json"), *options]
    )

    def unexpected(*args, **kwargs):
        pytest.fail("invalid arguments must fail before socket creation")

    monkeypatch.setattr(uhd_link_check.socket, "socket", unexpected)
    with pytest.raises(SystemExit) as exc:
        uhd_link_check.main()
    assert exc.value.code == 2


def test_one_byte_audit_disambiguates_wrapped_values_and_counts_duplicates():
    sent = {i: (bytes([i % 256]), float(i)) for i in range(300)}
    transmitted = [[f"1:99:{i}", data.hex()] for i, (data, _) in sent.items()]
    delivered = [transmitted[0], transmitted[256], transmitted[256], transmitted[299]]
    udp = [(bytes.fromhex(row[1]), 400.0) for row in delivered]
    received, latencies, duplicates, changed = uhd_link_check.correlate_tiny_payloads(
        sent, transmitted, delivered, udp
    )
    assert set(received) == {0, 256, 299}
    assert duplicates == 1
    assert changed == 0
    assert latencies == [400.0, 144.0, 101.0]


def test_one_byte_audit_rejects_missing_or_disagreeing_evidence():
    sent = {0: (b"\x00", 0.0)}
    with pytest.raises(ValueError, match="submitted FIFO"):
        uhd_link_check.correlate_tiny_payloads(sent, [], [], [])
    with pytest.raises(ValueError, match="UDP count"):
        uhd_link_check.correlate_tiny_payloads(sent, [["1:9:0", "00"]], [], [(b"\x00", 1)])
    result = uhd_link_check.correlate_tiny_payloads(
        sent, [["1:9:0", "00"]], [["1:9:0", "00"]], [(b"\x01", 1)]
    )
    assert result[0] == {}
    assert result[3] == 1


def test_packet_audit_keeps_retries_out_of_tx_id_list():
    from types import SimpleNamespace

    from packet_radio.protocol.framing import PacketKind, RadioPacket
    from packet_radio.protocol.reliability import QueuedPacket

    delivered = []
    app = SimpleNamespace(
        link=SimpleNamespace(transmitted=lambda *a, **kw: None, is_duplicate=lambda p: False),
        endpoint=SimpleNamespace(send=delivered.append),
    )
    audit = uhd_link_check.install_packet_audit(app)
    data = RadioPacket(1, 2, 3, b"\x00", session_id=9)
    queued = QueuedPacket(data)
    app.link.transmitted(queued)
    queued.attempts = 1
    app.link.transmitted(queued)
    app.link.transmitted(QueuedPacket(RadioPacket(1, 2, 4, b"", PacketKind.ACK, 9)))
    assert audit["tx"] == [["1:9:3", "00"]]
    assert not app.link.is_duplicate(data)
    app.endpoint.send(data.payload)
    assert delivered == [b"\x00"]
    assert audit["rx"] == [["1:9:3", "00"]]


def test_explicit_profile_order_fails_before_hardware(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "uhd_link_check",
            "--profiles",
            "configs/bench/peer-b-bpsk.yaml",
            "configs/bench/peer-a-bpsk.yaml",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        uhd_link_check.main()
    assert exc.value.code == 2


def test_effective_configuration_report_preserves_fields_without_key_paths():
    from packet_radio.config import load_config

    config = load_config("configs/bench/peer-a-qpsk.yaml")
    report = uhd_link_check.report_configuration(config)
    assert report["node_id"] == 1
    assert report["sdr"]["rs_parity_bytes"] == 16
    assert report["endpoint"]["max_payload_bytes"] == 1024
    assert "keys/" not in json.dumps(report)
    assert "<temporary>" in json.dumps(report)


@pytest.mark.parametrize("close_fails", [False, True])
def test_worker_acknowledges_stop_only_after_resource_release(monkeypatch, close_fails):
    from types import SimpleNamespace

    from packet_radio.statistics import LinkStatistics

    stats = LinkStatistics()
    events = []

    def close():
        events.append("closed")
        if close_fails:
            stats.increment("shutdown_errors")

    app = SimpleNamespace(
        sdr=SimpleNamespace(
            radio=SimpleNamespace(
                api=SimpleNamespace(get_version_string=lambda: "test-UHD"),
                actual_settings={"device": {"serial": "test"}},
            ),
            transmit=lambda *a: None,
            transmit_at=lambda *a: None,
            transmit_with_lead=lambda *a: None,
        ),
        statistics=stats,
        close=close,
    )
    monkeypatch.setattr(uhd_link_check, "RadioApp", lambda c: app)
    messages = []

    def send(value):
        if value[0] in ("stopped", "error"):
            assert "closed" in events
        messages.append(value)

    pipe = SimpleNamespace(send=send, poll=lambda: True, recv=lambda: "stop", close=lambda: None)
    uhd_link_check.worker(None, pipe)
    assert messages[0][1]["actual_settings"]["device"]["serial"] == "test"
    assert messages[-1][0] == ("error" if close_fails else "stopped")


def test_shared_worker_supports_cooperative_shutdown():
    import threading

    ready = threading.Event()

    def run(*, stop_event):
        ready.set()
        assert stop_event.wait(2)

    worker = uhd_link_check.ThreadPeer(target=run, daemon=True)
    worker.start()
    assert ready.wait(1)
    worker.terminate()
    assert not worker.is_alive()


def test_post_workload_check_catches_recovery_transport_errors():
    from packet_radio.statistics import LinkStatistics

    before = LinkStatistics().snapshot()
    after = dict(before, tx_underflows=1, transmit_errors=1)
    assert uhd_link_check.post_workload_stream_errors([before], [after]) == [
        {"tx_underflows": 1, "transmit_errors": 1}
    ]
    assert uhd_link_check.post_workload_stream_errors([before], [before]) == [{}]


def test_batch_override_reaches_both_configs_before_start(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    radios = [
        SimpleNamespace(radio=SimpleNamespace(tx_mtu=364, actual_settings={})) for _ in range(2)
    ]
    shared = SimpleNamespace(radios=radios, close=Mock())

    def open_shared(configs):
        assert [c.link.hardware_tx_batch_packets for c in configs] == [16, 16]
        return shared

    monkeypatch.setattr(uhd_link_check, "SharedRadios", open_shared)
    monkeypatch.setattr(
        "sys.argv", ["uhd_link_check", "--shared-device", "--tx-batch-packets", "16"]
    )

    def stop_before_start(*args, **kwargs):
        assert args[0].link.hardware_tx_batch_packets == 16
        raise RuntimeError("verified before worker startup")

    monkeypatch.setattr(uhd_link_check, "start", stop_before_start)
    with pytest.raises(RuntimeError, match="verified before worker startup"):
        uhd_link_check.main()
    shared.close.assert_called_once()
