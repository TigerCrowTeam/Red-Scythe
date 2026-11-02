"""Fixed LO lifetime, DSP-only retunes, invalid windows and failed readbacks."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from packet_radio.config import SDRConfig
from packet_radio.hardware.tuning import FrequencyTuner
from packet_radio.hardware.uhd import UHDRadio
from tests.hardware.test_sdr_adapter import uhd  # noqa: F401


@pytest.fixture
def setup(request):
    device, api = request.getfixturevalue("uhd")
    calls, rf = [], {}
    api.types.TuneRequest = lambda f: SimpleNamespace(
        target_freq=f, rf_freq_policy="auto", dsp_freq_policy="auto"
    )
    api.types.TuneRequestPolicy = SimpleNamespace(none="none", manual="manual", auto="auto")
    for direction in ("rx", "tx"):
        setattr(
            device,
            f"get_usrp_{direction}_info",
            lambda channel, d=direction: {"mboard_id": "X310", d + "_id": "UBX-160 v2"},
        )

        def tune(request, channel, d=direction):
            calls.append((d, request.rf_freq_policy, request.target_freq))
            if request.rf_freq_policy == "manual":
                rf[d] = request.rf_freq
            elif request.rf_freq_policy == "auto":
                rf[d] = request.target_freq
            device.values[d, "freq"] = request.target_freq
            return SimpleNamespace(
                actual_rf_freq=rf[d], actual_dsp_freq=request.target_freq - rf[d]
            )

        setattr(device, f"set_{direction}_freq", tune)
    config = SDRConfig(device={"type": "x300"}, bandwidth=1e6)
    return device, api, config, calls, rf


def test_backend_programs_each_lo_once_then_only_dsp_even_after_clock_reset(setup):
    device, api, config, calls, rf = setup
    radio = UHDRadio(config, (914e6, 915e6, 916e6))
    try:
        for freq in (916e6, 915e6, 914e6, 920e6, 914e6):
            radio.tune(freq)
        radio.set_hardware_time(1230000000)
        radio.tune(916e6)
        assert rf == {"rx": 915e6, "tx": 915e6}
        for direction in ("rx", "tx"):
            policies = [policy for d, policy, _ in calls if d == direction]
            assert policies == ["manual"] + ["none"] * 6
        assert radio.actual_settings["tuning_mode"] == "fixed-lo-dsp"
        assert radio.actual_settings["rx_frequency"] == 916e6
        assert radio.actual_settings["rx_lo_hz"] == 915e6
        assert radio.actual_settings["tx_lo_hz"] == 915e6
        assert radio.actual_settings["rx_rf_tune_requests"] == 1
        assert radio.actual_settings["tx_rf_tune_requests"] == 1
        assert radio.actual_settings["rx_dsp_tune_requests"] == 6
        assert radio.actual_settings["tx_dsp_tune_requests"] == 6
        assert radio.rx_active
        count = len(calls)
        with pytest.raises(ValueError, match="outside the fixed LO"):
            radio.tune(1000e6)
        assert len(calls) == count
    finally:
        radio.close()


@pytest.mark.parametrize("bad", ["center", "lo", "nan"])
def test_bad_readback_does_not_fall_back_to_rf_tuning(setup, bad):
    device, api, config, calls, rf = setup
    tuner = FrequencyTuner(api, device, config, (914e6, 916e6))
    tuner.tune("rx", 914e6)
    original = device.set_rx_freq

    def broken(request, channel):
        result = original(request, channel)
        if bad == "center":
            device.values["rx", "freq"] += 100
        elif bad == "lo":
            result.actual_rf_freq += 100
        else:
            result.actual_dsp_freq = float("nan")
        return result

    device.set_rx_freq = broken
    with pytest.raises(ValueError):
        tuner.tune("rx", 916e6)
    assert [policy for _, policy, _ in calls] == ["manual", "none"]


def test_hardware_identity_and_initial_span_checked_before_tuning(setup):
    device, api, config, calls, rf = setup
    with pytest.raises(ValueError, match="outside the fixed LO"):
        FrequencyTuner(api, device, config, (800e6, 1000e6))
    device.get_usrp_tx_info = lambda channel: {"mboard_id": "X310", "tx_id": "UBX-40"}
    with pytest.raises(ValueError, match="UBX-160"):
        FrequencyTuner(api, device, config, (914e6, 916e6))
    assert not calls


@pytest.mark.parametrize("other_device", [False, True])
def test_fixed_frequency_and_other_devices_keep_automatic_tuning(setup, other_device):
    device, api, config, calls, rf = setup
    frequencies = (915e6,)
    if other_device:
        config = replace(config, device={"type": "b200"})
        frequencies = (914e6, 916e6)
    tuner = FrequencyTuner(api, device, config, frequencies)
    tuner.tune("rx", 915e6)
    tuner.tune("rx", 916e6)
    assert [policy for _, policy, _ in calls] == ["auto", "auto"]


def test_continuous_hop_discards_old_iq_without_restarting_stream(setup):
    from tests.hardware.test_sdr_adapter import TimeSpec

    device, api, config, calls, rf = setup
    radio = UHDRadio(config, (914e6, 916e6))
    try:
        initial_commands = len(device.rx.commands)
        device.time = TimeSpec(4, 0.2)
        radio.tune(916e6)
        assert len(device.rx.commands) == initial_commands
        assert radio._receive_after_ns == 4_201_000_000
        assert not len(radio.receive_timed().samples)  # old block starts at 4.200 s
        device.rx.count = 4096
        crossing = radio.receive_timed()
        assert crossing.time_ns == 4_201_000_000
        assert len(crossing.samples) == 3096  # 1 Msps fake: trim exactly 1,000 old samples
        device.rx.count = 3
        original = device.rx.recv

        def fresh(buffer, metadata, timeout):
            count = original(buffer, metadata, timeout)
            metadata.time_spec = TimeSpec(4, 0.202)
            return count

        device.rx.recv = fresh
        assert len(radio.receive_timed().samples) == 3
        radio.set_hardware_time(0)
        assert radio._receive_after_ns is None
    finally:
        radio.close()


def test_continuous_hop_requires_timestamps_and_stops_on_failed_tune(setup):
    device, api, config, calls, rf = setup
    radio = UHDRadio(config, (914e6, 916e6))
    try:
        radio.tune(916e6)
        original = device.rx.recv

        def untimed(buffer, metadata, timeout):
            count = original(buffer, metadata, timeout)
            metadata.has_time_spec = False
            return count

        device.rx.recv = untimed
        with pytest.raises(OSError, match="timestamped"):
            radio.receive_timed()
        device.get_rx_freq = lambda channel: 0
        with pytest.raises(ValueError, match="coerced"):
            radio.tune(914e6)
        assert not radio.rx_active
        assert device.rx.commands[-1].mode == "stop_cont"
    finally:
        radio.close()
