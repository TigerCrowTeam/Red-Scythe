"""No-stream UHD preflight, range rejection, actual settings and shared clocks."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from packet_radio.configuration.forms import FormSession
from packet_radio.hardware import preflight


class Device:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.coerce = None
        self.fixed_bandwidth = False

    def get_num_mboards(self):
        return 1

    def __getattr__(self, name):
        if name.startswith("get_usrp_"):
            return lambda channel: {"mboard_serial": "fake", "mboard_id": "X310"}
        action, direction, field = name.split("_", 2)
        if field == "num_channels":
            return lambda: 2
        if field == "antennas":
            return lambda ch: ["RX2", "TX/RX"] if direction == "rx" else ["TX/RX"]
        ranges = {
            "rates": (100e3, 20e6),
            "freq_range": (70e6, 6e9),
            "gain_range": (0, 30),
            "bandwidth_range": (100e3, 20e6),
        }
        if field in ranges:
            low, high = ranges[field]
            if field == "bandwidth_range" and self.fixed_bandwidth:
                low = high = 160e6
            return lambda ch: SimpleNamespace(start=lambda: low, stop=lambda: high, step=lambda: 0)
        assert field in ("rate", "freq", "bandwidth", "gain", "antenna"), (
            f"Unexpected UHD operation: {name}"
        )
        if action == "set":

            def setter(value, channel):
                self.calls.append((name, value, channel))
                self.values[direction, field, channel] = value

            return setter

        def getter(channel):
            value = self.values[direction, field, channel]
            return value + 20 if self.coerce == field else value

        return getter


@pytest.fixture
def setup(monkeypatch):
    device = Device()
    monkeypatch.setattr(preflight, "make_uhd_device", lambda api, args: device)
    api = SimpleNamespace(types=SimpleNamespace(TuneRequest=lambda value: value))
    config = FormSession.new().report.config
    return device, api, config


def test_probe_actuals_fixed_filter_and_every_hop_without_streams(setup):
    device, api, config = setup
    device.fixed_bandwidth = True
    config = replace(config, frequency=replace(config.frequency, frequencies_hz=(915e6, 916e6)))
    result = preflight.probe(api, [config])
    assert result["ok"] and result["warnings"]
    assert result["nodes"][0]["actual"]["rx_rate"] == config.sdr.sample_rate
    assert result["nodes"][0]["actual"]["rx_bandwidth"] == 160e6
    assert len(result["nodes"][0]["frequencies"]) == 2
    assert len(result["capabilities"]["channels"]["tx"]) == 2
    assert all("stream" not in call[0] and "time" not in call[0] for call in device.calls)


@pytest.mark.parametrize("field", ["rate", "freq"])
def test_actual_rate_or_frequency_coercion_rejects_compatibility(setup, field):
    device, api, config = setup
    device.coerce = field
    with pytest.raises(ValueError, match="coerced"):
        preflight.probe(api, [config])


@pytest.mark.parametrize("changes", [{"channel": 2}, {"rx_gain": 99}, {"rx_antenna": "invalid"}])
def test_invalid_capabilities_fail_before_setting_hardware(setup, changes):
    device, api, config = setup
    with pytest.raises(ValueError):
        preflight.probe(api, [replace(config, sdr=replace(config.sdr, **changes))])
    assert not device.calls


def test_worker_missing_uhd_has_actionable_error(monkeypatch, capsys):
    import io
    import json

    def missing():
        raise RuntimeError("UHD Python bindings are required")

    monkeypatch.setattr(preflight, "load_uhd", missing)
    monkeypatch.setattr(preflight.sys, "stdin", io.StringIO('{"action":"discover"}'))
    preflight.main()
    line = capsys.readouterr().out.strip()
    result = json.loads(line.removeprefix(preflight.RESULT_PREFIX))
    assert not result["ok"]
    assert "UHD Python bindings" in result["error"] and "connection" in result["error"]
