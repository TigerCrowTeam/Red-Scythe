"""Exercise command entry points with real configs, keys, captures and UDP."""

import json
import socket
import sys
from unittest.mock import Mock

import pytest

from packet_radio import __main__ as radio_cli
from packet_radio.cli import key_cli, payload_cli, replay, validate
from packet_radio.modems import BPSKModem
from packet_radio.protocol.authentication import load_private_key
from packet_radio.protocol.framing import RadioPacket
from packet_radio.recording import CaptureRecorder
from tests.integration.test_end_to_end import node


def invoke(monkeypatch, module, *args):
    monkeypatch.setattr(sys, "argv", [module.__name__, *map(str, args)])
    module.main()


def test_radio_cli_applies_overrides(monkeypatch):
    run = Mock()
    monkeypatch.setattr(radio_cli, "run", run)
    invoke(
        monkeypatch,
        radio_cli,
        "--config",
        "configs/examples/peer-a-bpsk.yaml",
        "--status",
        "json",
        "--status-interval",
        "3",
        "--status-http-port",
        "9876",
        "--no-status-http",
    )
    config = run.call_args.args[0]
    assert config.status.output == "json"
    assert config.status.interval_seconds == 3
    assert config.status.http_port == 9876
    assert config.status.http_enabled is False


@pytest.mark.parametrize(
    "options,message",
    [
        (["--status-interval", "0"], "positive"),
        (["--status-http-port", "65536"], "between 1 and 65535"),
    ],
)
def test_radio_cli_rejects_invalid_overrides(monkeypatch, capsys, options, message):
    run = Mock()
    monkeypatch.setattr(radio_cli, "run", run)
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, radio_cli, "--config", "configs/examples/peer-a-bpsk.yaml", *options)
    assert error.value.code == 1
    assert message in capsys.readouterr().err
    run.assert_not_called()


def test_radio_cli_requires_explicit_unauthenticated_opt_in(monkeypatch, capsys):
    monkeypatch.setattr(radio_cli, "load_config", lambda _: node("leader", 1))
    run = Mock()
    monkeypatch.setattr(radio_cli, "run", run)
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, radio_cli, "--config", "unused")
    assert error.value.code == 1
    assert "authentication.private_key_file" in capsys.readouterr().err
    run.assert_not_called()
    invoke(monkeypatch, radio_cli, "--config", "unused", "--allow-unauthenticated")
    run.assert_called_once()


@pytest.mark.parametrize("failure", [OSError("device gone"), RuntimeError("worker failed")])
def test_radio_cli_reports_runtime_failure(monkeypatch, capsys, failure):
    monkeypatch.setattr(radio_cli, "run", Mock(side_effect=failure))
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, radio_cli, "--config", "configs/examples/peer-a-bpsk.yaml")
    assert error.value.code == 1
    assert str(failure) in capsys.readouterr().err


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "qpsk", "msk", "gmsk"])
def test_validate_cli_checks_paired_profiles(monkeypatch, capsys, modulation):
    invoke(
        monkeypatch,
        validate,
        f"configs/examples/peer-a-{modulation}.yaml",
        f"configs/examples/peer-b-{modulation}.yaml",
    )
    output = capsys.readouterr().out
    assert "network valid: 2 nodes" in output
    assert f"{modulation}: center=9.15e+08 Hz" in output
    if modulation in ("2fsk", "msk", "gmsk"):
        assert "frequency deviation" in output
    if modulation == "gmsk":
        assert "Gaussian BT=0.3, span=4 symbols" in output


def test_validate_cli_rejects_multiple_hardware_profiles(monkeypatch, capsys):
    check = Mock()
    monkeypatch.setattr(validate, "validate_hardware", check)
    with pytest.raises(SystemExit) as error:
        invoke(
            monkeypatch,
            validate,
            "configs/examples/peer-a-bpsk.yaml",
            "configs/examples/peer-b-bpsk.yaml",
            "--hardware",
        )
    assert error.value.code == 1
    assert "exactly one" in capsys.readouterr().err
    check.assert_not_called()


def test_validate_hardware_closes_device(monkeypatch, capsys):
    device = Mock(actual_settings={"serial": "test-device"})
    monkeypatch.setattr(validate, "UHDRadio", Mock(return_value=device))
    invoke(monkeypatch, validate, "configs/examples/peer-a-bpsk.yaml", "--hardware")
    assert "hardware valid:" in capsys.readouterr().out
    device.close.assert_called_once()


def test_key_cli_generate_verify_inspect_and_refuse_overwrite(monkeypatch, tmp_path, capsys):
    invoke(monkeypatch, key_cli, "generate", 1, tmp_path, "--label", "test")
    generated = json.loads(capsys.readouterr().out)
    private, public = generated["private_key"], generated["public_key"]
    original = (tmp_path / "node1-test-private.pem").read_bytes()
    assert (tmp_path / "node1-test-private.pem").stat().st_mode & 0o777 == 0o600
    invoke(monkeypatch, key_cli, "verify", private, public)
    assert json.loads(capsys.readouterr().out)["fingerprint"] == generated["fingerprint"]
    invoke(monkeypatch, key_cli, "inspect", public)
    assert json.loads(capsys.readouterr().out)["fingerprint"] == generated["fingerprint"]
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, key_cli, "generate", 1, tmp_path, "--label", "test")
    assert error.value.code == 1
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "node1-test-private.pem").read_bytes() == original


@pytest.mark.parametrize("source", ["iq", "frames"])
@pytest.mark.parametrize("authenticated", [False, True])
def test_replay_cli_decodes_real_capture(monkeypatch, tmp_path, capsys, source, authenticated):
    options = []
    signing = None
    if authenticated:
        private, public = key_cli.generate_key_pair(2, tmp_path / "keys", "test")
        signing = load_private_key(private)
        options = ["--public-key", f"2={public}"]
    wire = RadioPacket(2, 1, 7, b"opaque\x00\xff", session_id=1).encode(signing)
    capture = tmp_path / "capture"
    recorder = CaptureRecorder(
        capture, sample_rate=1_000_000, samples_per_symbol=4, authenticated=authenticated
    )
    recorder.record_frame(wire)
    recorder.record_iq(BPSKModem(4).modulate(wire))
    recorder.close()
    invoke(monkeypatch, replay, capture, "--source", source, *options)
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is True
    assert result["source"] == 2 and result["sequence"] == 7
    assert result["payload_base64"] == "b3BhcXVlAP8="


@pytest.mark.parametrize(
    "options,message", [([], "requires --public-key"), (["--public-key", "bad"], "NODE_ID=PATH")]
)
def test_replay_cli_rejects_missing_or_malformed_identity(
    monkeypatch, tmp_path, capsys, options, message
):
    CaptureRecorder(
        tmp_path, sample_rate=1_000_000, samples_per_symbol=4, authenticated=True
    ).close()
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, replay, tmp_path, *options)
    assert error.value.code == 1
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("source", ["text", "file"])
def test_payload_cli_sends_exact_bytes(tmp_path, capsys, source):
    payload = "radio café".encode() if source == "text" else bytes(range(256))
    path = tmp_path / "payload.bin"
    path.write_bytes(payload)
    options = ["--text", payload.decode()] if source == "text" else ["--file", str(path)]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1)
        payload_cli.main(["send", "--port", str(receiver.getsockname()[1]), *options])
        assert receiver.recvfrom(65535)[0] == payload
    assert json.loads(capsys.readouterr().out)["submitted_bytes"] == len(payload)


@pytest.mark.parametrize(
    "args,code,message",
    [
        (["send", "--hex", "zz"], 1, "hex"),
        (["send", "--text", ""], 1, "1..1024"),
        (["send", "--text", "x" * 1025], 1, "1..1024"),
        (["send", "--text", "x", "--port", "0"], 2, "port"),
        (["receive", "--count", "0"], 1, "count"),
        (["receive", "--timeout", "nan"], 1, "timeout"),
    ],
)
def test_payload_cli_rejects_invalid_input(capsys, args, code, message):
    with pytest.raises(SystemExit) as error:
        payload_cli.main(args)
    assert error.value.code == code
    assert message in capsys.readouterr().err


def test_payload_cli_receives_binary_datagram(monkeypatch, capsys):
    factory = socket.socket
    with factory(socket.AF_INET, socket.SOCK_DGRAM) as sender:

        class ReadySocket(socket.socket):
            def bind(self, address):
                # Bind an ephemeral port to avoid fixed-port assumptions. Send
                # only after bind, so the test needs neither polling nor sleeps.
                super().bind((address[0], 0))
                sender.sendto(b"\x00\xffopaque", self.getsockname())

        monkeypatch.setattr(payload_cli.socket, "socket", ReadySocket)
        payload_cli.main(["receive", "--timeout", "1"])
    assert json.loads(capsys.readouterr().out) == {"bytes": 8, "hex": "00ff6f7061717565"}


def test_payload_cli_receive_timeout_is_reported(monkeypatch, capsys):
    class EphemeralSocket(socket.socket):
        def bind(self, address):
            super().bind((address[0], 0))

    monkeypatch.setattr(payload_cli.socket, "socket", EphemeralSocket)
    with pytest.raises(SystemExit) as error:
        payload_cli.main(["receive", "--timeout", "0.01"])
    assert error.value.code == 1
    assert "timed out" in capsys.readouterr().err


def test_conformance_cli_success_and_corrupted_fixture(monkeypatch, tmp_path, capsys):
    from pathlib import Path

    from packet_radio.cli import conformance

    invoke(monkeypatch, conformance)
    assert "valid protocol v5 vectors:" in capsys.readouterr().out
    fixture = json.loads(Path("fixtures/protocol_v5.json").read_text())
    fixture["vectors"][0]["wire_hex"] = "00"
    path = tmp_path / "corrupted.json"
    path.write_text(json.dumps(fixture))
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, conformance, path)
    assert error.value.code == 1
    assert "signed wire bytes changed" in capsys.readouterr().err
