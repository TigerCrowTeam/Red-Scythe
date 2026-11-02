"""Verify automatic hop capacity, saved settings, runtime rejection and UI values."""

from dataclasses import replace
from pathlib import Path

import pytest

from packet_radio.app import RadioApp
from packet_radio.config import (
    config_from_snapshot,
    configuration_snapshot,
    load_config,
    validate_config,
)
from packet_radio.configuration.draft import RadioDraft, validate_pair
from packet_radio.configuration.forms import FormSession
from packet_radio.configuration.storage import save_project
from packet_radio.endpoint import MemoryEndpoint
from packet_radio.hop_budget import maximum_hop_rate, payload_airtime, timing_reserve
from packet_radio.simulation import SimulatedChannel, SimulatedClock
from tests.configuration.test_draft import persisted

ROOT = Path(__file__).resolve().parents[2]


def draft(rate=28, peer="a"):
    subject = RadioDraft.load(ROOT / f"configs/v1_1/peer-{peer}-x310-hopping.yaml")
    subject.set("profile.packet.payload_sizing", "automatic")
    subject.set("profile.frequency.guard_seconds", 0.001)
    subject.set("node.runtime.hardware_timed_tx_lead_seconds", 0.008)
    subject.set("node.runtime.hardware_tx_batch_packets", 16)
    subject.set("profile.frequency.dwell_seconds", 1 / rate)
    return subject


@pytest.mark.parametrize(
    "rate,expected", [(8, 1024), (16, 912), (26, 160), (27, 116), (28, 75), (29, 36)]
)
def test_capacity_uses_encoded_samples_and_preserves_ceiling(rate, expected):
    report = draft(rate).validate()
    assert report.valid, report.issues
    config = report.config
    assert config.endpoint.max_payload_bytes == expected
    assert report.derived.effective_payload_bytes == expected
    assert config.emulation["packet"]["max_payload_bytes"] == 1024
    available = config.frequency.interval_seconds - timing_reserve(config)
    assert payload_airtime(config, expected) <= available
    if expected < 1024:
        assert payload_airtime(config, expected + 1) > available
    assert config_from_snapshot(configuration_snapshot(config)) == config


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("profile.waveform.bit_rate_bps", 125000, 17),
        ("profile.packet.frame.preamble_repetitions", 64, 43),
        ("profile.packet.frame.crc", "crc32-iso-hdlc", 73),
        ("profile.packet.max_payload_bytes", 20, 20),
    ],
)
def test_dependent_edits_recalculate_capacity(key, value, expected):
    subject = draft()
    subject.set(key, value)
    report = subject.validate()
    assert report.valid, report.issues
    assert report.config.endpoint.max_payload_bytes == expected


def test_fec_padding_is_included():
    subject = draft()
    subject.set("profile.protection.scheme", "extended-hamming-8-4")
    report = subject.validate()
    assert report.valid, report.issues
    config = report.config
    size = config.endpoint.max_payload_bytes
    assert size < 75
    available = config.frequency.interval_seconds - timing_reserve(config)
    assert payload_airtime(config, size) <= available < payload_airtime(config, size + 1)


def test_calculated_inclusive_rate_bound_fits_one_byte_and_rejects_faster():
    subject = draft()
    subject.set("profile.frequency.guard_seconds", 0.1)
    subject.set("profile.frequency.dwell_seconds", 0.5)
    rate = maximum_hop_rate(subject.validate().config)
    assert rate < 8
    subject.set("profile.frequency.dwell_seconds", 1 / rate)
    report = subject.validate()
    assert report.valid, report.issues
    assert report.config.endpoint.max_payload_bytes == 1
    subject.set("profile.frequency.dwell_seconds", 1 / (rate + 0.000001))
    assert not subject.validate().valid


@pytest.mark.parametrize(
    "key,value",
    [
        ("profile.packet.payload_sizing", "unknown"),
        ("profile.frequency.dwell_seconds", 0.01),
        ("profile.frequency.guard_seconds", 0.0009),
        ("node.hardware.device.type", "b200"),
        ("profile.packet.frame.fixed_payload_bytes", 1024),
    ],
)
def test_invalid_or_unusable_automatic_settings_are_rejected(key, value):
    subject = draft()
    subject.set(key, value)
    assert not subject.validate().valid


def test_save_reload_and_peer_agreement(tmp_path):
    subject, path = persisted(tmp_path, draft())
    config = load_config(path)
    assert config.endpoint.max_payload_bytes == 75
    assert config.emulation["packet"]["payload_sizing"] == "automatic"
    assert subject.profile["packet"]["max_payload_bytes"] == 1024
    saved = save_project(draft(), tmp_path / "saved/node.yaml")
    assert load_config(saved.node_path).endpoint.max_payload_bytes == 75
    assert validate_pair(draft(peer="a"), draft(peer="b")).valid
    peer = draft(peer="b")
    peer.set("node.runtime.hardware_timed_tx_lead_seconds", 0.02)
    assert not validate_pair(draft(), peer).valid
    with pytest.raises(ValueError, match="resolved packet settings"):
        validate_config(replace(config, endpoint=replace(config.endpoint, max_payload_bytes=76)))


def test_optional_auto_setting_can_be_added_as_a_node_override():
    subject = RadioDraft.load(ROOT / "configs/v1_1/peer-a-x310-hopping.yaml")
    subject.set(
        "node.overrides",
        {
            "packet": {"payload_sizing": "automatic"},
            "frequency": {"guard_seconds": 0.03, "dwell_seconds": 1 / 8},
        },
    )
    report = subject.validate()
    assert report.valid, report.issues
    assert report.config.endpoint.max_payload_bytes == 990


def test_form_recalculates_and_hop_limit_uses_smallest_payload():
    form = FormSession(draft())
    assert form.value("profile.packet.payload_sizing") == "automatic"
    assert form.value("derived.effective_payload_bytes") == 75
    assert form.draft.limits("derived.hop_rate").maximum == maximum_hop_rate(form.report.config)
    assert form.draft.limits("derived.hop_rate").maximum == 29
    form.change("derived.hop_rate", 27)
    assert form.value("derived.effective_payload_bytes") == 116
    form.change("profile.frequency.mode", "fixed")
    assert form.report.valid
    assert form.value("profile.packet.payload_sizing") == "manual"


def test_runtime_rejects_oversize_without_truncation_and_still_rejects_late_bursts():
    subject = draft(28)
    subject.set("node.runtime.hardware_timed_io", False)
    subject.set("node.runtime.decoder_worker_enabled", False)
    config = subject.validate().config
    clock = SimulatedClock()
    clock.wall = clock.monotonic = 0.01
    app = RadioApp(
        config,
        sdr=SimulatedChannel(sample_rate=2e6).radio(),
        endpoint=MemoryEndpoint(),
        wall_clock=lambda: clock.wall,
        monotonic_clock=lambda: clock.monotonic,
    )
    try:
        app._retune()
        assert not app._queue_local_payload(b"x" * (config.endpoint.max_payload_bytes + 1))
        assert app.statistics.snapshot()["invalid_payloads"] == 1
        assert app._queue_local_payload(b"x" * config.endpoint.max_payload_bytes)
        clock.wall = clock.monotonic = config.frequency.interval_seconds - 0.002
        app._transmit_one()
        assert app.statistics.snapshot()["hop_burst_rejections"] == 1
        assert app.statistics.snapshot()["packets_transmitted"] == 0
    finally:
        app.close()


@pytest.mark.parametrize("rate", [29.000001, 29.5, 30])
def test_automatic_rate_above_product_cap_is_rejected(rate):
    report = draft(rate).validate()
    assert not report.valid
    assert "must not exceed 29 hops/s" in str(report.issues)


@pytest.mark.parametrize(
    "frequencies,valid",
    [
        ([914e6, 915e6, 916e6], True),
        ([1000e6, 841e6, 900e6], True),  # 159 MHz span + 1 MHz signal
        ([841e6, 1000e6 + 1], False),
        ([840e6, 1000e6], False),  # centers alone fit, signal does not
        ([9e6, 10e6], False),
        ([6e9, 6e9 + 1], False),
        ([10e6, 11e6], True),
        ([5999e6, 6000e6], True),
    ],
)
def test_x310_hop_table_hardware_envelope(frequencies, valid):
    subject = draft()
    subject.set("profile.frequency.frequencies_hz", frequencies)
    report = subject.validate()
    assert report.valid == valid, report.issues
    if not valid:
        assert "profile.frequency.frequencies_hz" in report.issues[0].fields


def test_x310_hop_span_revalidated_when_signal_bandwidth_changes(tmp_path):
    subject = draft()
    subject.set("profile.frequency.frequencies_hz", [841e6, 1000e6])
    assert subject.validate().valid
    subject.set("profile.waveform.bandwidth_hz", 2e6)
    assert not subject.validate().valid
    _, path = persisted(tmp_path, subject)
    with pytest.raises(ValueError, match="hop span plus signal bandwidth"):
        load_config(path)


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_modulation_switch_preserves_automatic_hops_and_recalculates(modulation):
    form = FormSession(draft(28))
    frequency = dict(form.draft.profile["frequency"])
    form.change("profile.waveform.modulation", modulation)
    assert form.report.valid, form.report.issues
    assert form.draft.profile["frequency"] == frequency
    assert form.selection.packet_format == "simple-frame-v1"
    assert form.draft.profile["packet"]["payload_sizing"] == "automatic"
    config = form.report.config
    assert config.sdr.modulation == modulation
    size = config.endpoint.max_payload_bytes
    available = config.frequency.interval_seconds - timing_reserve(config)
    assert payload_airtime(config, size) <= available < payload_airtime(config, size + 1)
    assert config_from_snapshot(configuration_snapshot(config)) == config
