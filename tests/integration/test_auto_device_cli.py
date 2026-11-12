"""CLI discovery selects the deployment without transmitting during selection."""

import sys
from unittest.mock import AsyncMock, Mock

import pytest

import packet_radio.__main__ as cli
from packet_radio.configuration.preflight import HardwareJob
from packet_radio.hardware import shared
from tests.configuration.test_deployment import B205, X310


def invoke(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["packet-radio", *args])
    cli.main()


def setup(monkeypatch, devices):
    discovery = AsyncMock(return_value={"ok": True, "devices": devices})
    monkeypatch.setattr(HardwareJob, "run", discovery)
    local, pair = Mock(), Mock()
    monkeypatch.setattr(cli, "run", local)
    monkeypatch.setattr(shared, "run_pair", pair)
    return discovery, local, pair


def test_auto_b205_launches_only_local_role_b(monkeypatch):
    discovery, local, pair = setup(monkeypatch, [B205])
    invoke(monkeypatch, "--config", "configs/v1_1/peer-b-b205-two-host.yaml", "--auto-device")
    cfg = local.call_args.args[0]
    assert cfg.node_id == 2 and cfg.peer_id == 1
    assert cfg.sdr.channel == 0 and cfg.sdr.device["serial"] == "USB-A"
    pair.assert_not_called()
    discovery.assert_awaited_once()


def test_auto_x310_launches_complementary_channels(monkeypatch):
    _, local, pair = setup(monkeypatch, [X310])
    invoke(
        monkeypatch,
        "--config",
        "configs/v1_1/peer-a-b205-two-host.yaml",
        "--auto-device",
        "--no-status-http",
    )
    configs = pair.call_args.args[0]
    assert [c.sdr.channel for c in configs] == [0, 1]
    assert all(c.sdr.device == {"type": "x300", "addr": "192.168.10.2"} for c in configs)
    assert all(not c.status.http_enabled for c in configs)
    local.assert_not_called()


def test_ambiguous_discovery_does_not_start_and_serial_resolves_it(monkeypatch, capsys):
    _, local, pair = setup(monkeypatch, [B205, X310])
    args = ("--config", "configs/v1_1/peer-a-b205-two-host.yaml", "--auto-device")
    with pytest.raises(SystemExit):
        invoke(monkeypatch, *args)
    assert "Multiple SDRs" in capsys.readouterr().err
    local.assert_not_called()
    pair.assert_not_called()
    invoke(monkeypatch, *args, "--sdr-serial", "USB-A")
    local.assert_called_once()
    pair.assert_not_called()


def test_b205_cannot_launch_remote_node_as_local_pair(monkeypatch, capsys):
    _, local, pair = setup(monkeypatch, [B205])
    with pytest.raises(SystemExit):
        invoke(
            monkeypatch,
            "--config",
            "configs/v1_1/peer-a-b205-two-host.yaml",
            "--peer-config",
            "configs/v1_1/peer-b-b205-two-host.yaml",
            "--auto-device",
        )
    assert "other computer" in capsys.readouterr().err
    local.assert_not_called()
    pair.assert_not_called()


def test_existing_explicit_x310_pair_never_discovers(monkeypatch):
    discovery, local, pair = setup(monkeypatch, [])
    invoke(
        monkeypatch,
        "--config",
        "configs/v1_1/peer-a-x310-simple.yaml",
        "--peer-config",
        "configs/v1_1/peer-b-x310-simple.yaml",
    )
    pair.assert_called_once()
    local.assert_not_called()
    discovery.assert_not_called()
