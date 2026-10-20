"""Check shared-device validation and paired application failure cleanup."""

import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packet_radio.config import load_config
from packet_radio.hardware import shared


def configs():
    result = []
    for i, peer in enumerate("ab"):
        c = load_config(f"configs/v1_1/peer-{peer}-simple.yaml")
        result.append(replace(c, sdr=replace(c.sdr, device={"type": "x300"}, channel=i)))
    return result


@pytest.mark.parametrize("change", ["count", "same_channel", "device", "type", "rate"])
def test_invalid_shared_pair_never_opens_hardware(monkeypatch, change):
    pair = configs()
    if change == "count":
        pair.pop()
    elif change == "same_channel":
        pair[1] = replace(pair[1], sdr=replace(pair[1].sdr, channel=0))
    elif change == "device":
        pair[1] = replace(
            pair[1], sdr=replace(pair[1].sdr, device={"type": "x300", "addr": "other"})
        )
    elif change == "type":
        pair = [replace(c, sdr=replace(c.sdr, device={"type": "b200"})) for c in pair]
    else:
        pair[1] = replace(pair[1], sdr=replace(pair[1].sdr, sample_rate=1e6))
    factory = Mock()
    monkeypatch.setattr(shared, "UHDDeviceSession", factory)
    with pytest.raises(ValueError):
        shared.SharedRadios(pair)
    factory.assert_not_called()


def test_second_channel_failure_closes_first_and_owner(monkeypatch):
    first = Mock()
    owner = Mock()
    owner.open.side_effect = [first, RuntimeError("channel unavailable")]
    monkeypatch.setattr(shared, "UHDDeviceSession", Mock(return_value=owner))
    buffer = Mock()
    buffer.close.side_effect = first.close
    monkeypatch.setattr(shared, "BufferedRadio", Mock(return_value=buffer))
    with pytest.raises(RuntimeError, match="channel unavailable"):
        shared.SharedRadios(configs())
    buffer.close.assert_called_once()
    first.close.assert_called_once()
    owner.close.assert_called_once()


def test_peer_failure_stops_and_closes_both(monkeypatch):
    from packet_radio import app as application

    owner = SimpleNamespace(radios=[object(), object()], close=Mock())
    monkeypatch.setattr(shared, "SharedRadios", lambda _: owner)
    apps = []

    class App:
        def __init__(self, config, **kwargs):
            self.node = config.node_id
            self.stopped = threading.Event()
            self.close = Mock()
            apps.append(self)

        def run(self):
            if self.node == 1:
                raise OSError("lost hardware")
            assert self.stopped.wait(2)

        def stop(self):
            self.stopped.set()

    monkeypatch.setattr(application, "RadioApp", App)
    with pytest.raises(RuntimeError, match="peer failed") as exc:
        shared.run_pair(configs())
    assert isinstance(exc.value.__cause__, OSError)
    assert all(a.stopped.is_set() for a in apps)
    for a in apps:
        a.close.assert_called_once()
    owner.close.assert_called_once()


def test_cli_passes_peer_and_common_status_overrides(monkeypatch):
    from packet_radio import __main__ as cli

    run = Mock()
    monkeypatch.setattr(shared, "run_pair", run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "packet-radio",
            "--config",
            "configs/v1_1/peer-a-simple.yaml",
            "--peer-config",
            "configs/v1_1/peer-b-simple.yaml",
            "--no-status-http",
            "--status",
            "json",
        ],
    )
    cli.main()
    pair = run.call_args.args[0]
    assert [c.node_id for c in pair] == [1, 2]
    assert all(c.status.output == "json" and not c.status.http_enabled for c in pair)


def test_cli_preserves_each_peers_status_without_overrides(monkeypatch):
    from packet_radio import __main__ as cli

    a, b = configs()
    b = replace(b, status=replace(b.status, output="json", interval_seconds=7))
    run = Mock()
    monkeypatch.setattr(shared, "run_pair", run)
    monkeypatch.setattr(cli, "load_config", lambda path: a if path == "a" else b)
    monkeypatch.setattr("sys.argv", ["packet-radio", "--config", "a", "--peer-config", "b"])
    cli.main()
    assert [c.status for c in run.call_args.args[0]] == [a.status, b.status]


def test_shared_ready_waits_for_both_status_services(monkeypatch):
    from packet_radio import app as application

    owner = SimpleNamespace(radios=[object(), object()], close=Mock())
    monkeypatch.setattr(shared, "SharedRadios", lambda _: owner)
    ready_nodes = []
    observed = []

    class App:
        def __init__(self, config, **kwargs):
            self.node = config.node_id
            self.stopped = threading.Event()
            self.statistics = SimpleNamespace(snapshot=lambda: {"shutdown_errors": 0})

        def run(self, *, on_ready):
            ready_nodes.append(self.node)
            on_ready()
            assert self.stopped.wait(2)

        def stop(self):
            self.stopped.set()

        def close(self):
            pass

    instances = []

    def factory(*args, **kwargs):
        app = App(*args, **kwargs)
        instances.append(app)
        return app

    def ready():
        observed.append(set(ready_nodes))
        for app in instances:
            app.stop()

    monkeypatch.setattr(application, "RadioApp", factory)
    shared.run_pair(configs(), on_ready=ready)
    assert observed == [{1, 2}]
    owner.close.assert_called_once()
