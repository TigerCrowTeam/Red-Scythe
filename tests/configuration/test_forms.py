"""Unit conversions and capability changes that must not leak stale settings."""

import pytest

from packet_radio.configuration.catalog import visible_fields
from packet_radio.configuration.forms import FormSession, format_value, parse_value


@pytest.mark.parametrize(
    "key,text,value",
    [
        ("profile.frequency.center_frequency_hz", "915 MHz", 915e6),
        ("profile.waveform.frequency_deviation_hz", "125 kHz", 125000),
        ("profile.waveform.sample_rate_hz", "2 Msps", 2e6),
        ("profile.waveform.bit_rate_bps", "250 kbit/s", 250000),
        ("node.runtime.hardware_timed_tx_lead_seconds", "10 ms", 0.01),
        ("node.node_id", "255", 255),
        ("profile.packet.frame.preamble", "aa 55", "aa55"),
    ],
)
def test_units(key, text, value):
    assert parse_value(key, text) == value
    assert parse_value(key, format_value(key, value)) == value


@pytest.mark.parametrize(
    "key,text",
    [
        ("node.node_id", "1.5"),
        ("node.node_id", "1.00000000000000000000000000001"),
        ("node.node_id", "256"),
        ("node.node_id", "0"),
        ("profile.waveform.bit_rate_bps", "nan"),
        ("profile.waveform.bit_rate_bps", "inf"),
        ("profile.waveform.bit_rate_bps", "1e999"),
        ("profile.waveform.bit_rate_bps", "1e1000000"),
        ("profile.waveform.bit_rate_bps", "1 MHz"),
        ("profile.packet.frame.sync", "abc"),
        ("profile.protection.rs_parity_bytes", "3"),
    ],
)
def test_bad_input_is_not_rounded_or_clamped(key, text):
    with pytest.raises(ValueError):
        parse_value(key, text)


def test_modulation_switch_clears_incompatible_waveform_fields():
    form = FormSession.new()
    for mode in ("gmsk", "qpsk", "2fsk", "bpsk", "msk", "gmsk"):
        form.change("profile.waveform.modulation", mode)
        assert form.report.valid, form.report.issues
        keys = {f.key for f in visible_fields(form.selection, advanced=True)}
        assert ("profile.waveform.frequency_deviation_hz" in keys) == (mode == "2fsk")
        assert ("profile.waveform.gaussian_bt" in keys) == (mode == "gmsk")
    form.change("profile.waveform.gaussian_bt", 0.5)
    form.change("profile.waveform.modulation", "qpsk")
    assert form.draft.profile["waveform"]["gaussian_bt"] is None
    assert form.report.valid


def test_frame_crc_fec_and_frequency_transitions():
    form = FormSession.new()
    for mode in ("fixed", "variable"):
        form.change("derived.length_mode", mode)
        assert form.report.valid, form.report.issues
    form.change("profile.protection.scheme", "extended-hamming-8-4")
    form.change("profile.protection.rs_parity_bytes", 3)
    assert not form.report.valid
    form.change("profile.protection.scheme", "none")
    assert form.report.valid, form.report.issues
    form.change("profile.packet.frame.crc", "none")
    assert not any(f.key.endswith("crc_byte_order") for f in visible_fields(form.selection))
    form.change("profile.frequency.mode", "hopping")
    assert form.report.valid, form.report.issues
    form.change("derived.hop_rate", 100)
    assert not form.report.valid
    form.change("profile.frequency.mode", "fixed")
    assert form.report.valid, form.report.issues
    form.change("profile.waveform.modulation", "qpsk")
    assert form.selection.packet_format == "simple-frame-v1"
    assert form.report.valid


def test_pulse_shape_and_timing_switches_clear_invalid_hidden_fields():
    form = FormSession.new()
    form.change("profile.waveform.modulation", "bpsk")
    form.change("derived.pulse_shaping", "rrc")
    form.change("profile.waveform.rrc_span_symbols", 3)
    assert not form.report.valid
    form.change("derived.pulse_shaping", "rectangular")
    assert form.report.valid
    form.change("profile.packet.format", "generic-v5")
    form.change("profile.link.tdd_period_seconds", 2)
    form.change("profile.link.tdd_guard_seconds", 2)
    assert not form.report.valid
    form.change("profile.link.tdd_period_seconds", 0)
    assert form.report.valid, form.report.issues
    form.change("node.runtime.hardware_timed_io", False)
    assert form.report.valid, form.report.issues


def test_operation_and_device_switches():
    form = FormSession.new()
    form.change("node.operation", "receive-only")
    assert not any(f.key == "node.hardware.tx_gain" for f in visible_fields(form.selection))
    form.change("node.hardware.device.serial", "123")
    form.change("node.hardware.device.type", "x300")
    assert "serial" not in form.draft.node["hardware"]["device"]
    assert form.report.valid


def test_simple_bit_order_switch_clears_unsupported_choice():
    from packet_radio.configuration.catalog import available_choices

    form = FormSession.new()
    form.change("profile.packet.frame.bit_order", "little")
    assert form.report.valid
    form.change("profile.waveform.modulation", "msk")
    assert form.report.valid, form.report.issues
    assert form.value("profile.packet.frame.bit_order") == "big"
    assert available_choices("profile.packet.frame.bit_order", form.selection) == ("big",)
    form.change("profile.waveform.modulation", "2fsk")
    assert available_choices("profile.packet.frame.bit_order", form.selection) == ("big", "little")
