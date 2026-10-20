"""Guard schema inventory and user-visible capability filtering without SDR access."""

import subprocess
import sys
from dataclasses import fields, replace
from pathlib import Path

import pytest

from packet_radio import profiles
from packet_radio.config import AuthenticationConfig, EndpointConfig, RecordingConfig, StatusConfig
from packet_radio.configuration.catalog import (
    CATALOG,
    FIELDS,
    MODULATIONS,
    NUMERIC_LIMITS,
    Selection,
    available_choices,
    compatible_formats,
    visible_fields,
)
from packet_radio.protocol.simple_link import SimpleLinkSettings


def test_numeric_limits_reference_real_numeric_controls():
    for key, (minimum, maximum, multiple_of) in NUMERIC_LIMITS.items():
        field = FIELDS[key]
        assert field.editor in ("number", "integer")
        assert (field.minimum, field.maximum, field.multiple_of) == (
            minimum,
            maximum,
            multiple_of,
        )
        if minimum is not None and maximum is not None:
            assert minimum <= maximum
        if multiple_of is not None:
            assert multiple_of > 0
            assert minimum % multiple_of == 0


@pytest.mark.parametrize(
    "key,minimum,maximum,multiple_of",
    [
        ("node.node_id", 1, 255, None),
        ("node.endpoint.bind_port", 1, 65535, None),
        ("profile.packet.max_payload_bytes", 1, 1024, None),
        ("profile.waveform.rrc_rolloff", 0, 1, None),
        ("profile.protection.rs_parity_bytes", 0, 254, 2),
    ],
)
def test_fixed_input_bounds(key, minimum, maximum, multiple_of):
    field = FIELDS[key]
    assert (field.minimum, field.maximum, field.multiple_of) == (minimum, maximum, multiple_of)


def children(prefix):
    return {
        key[len(prefix) + 1 :]
        for key in FIELDS
        if key.startswith(prefix + ".") and "." not in key[len(prefix) + 1 :]
    }


def names(selection, advanced=True):
    return {field.key for field in visible_fields(selection, advanced=advanced)}


def test_every_parser_owned_field_is_accounted_for():
    # These expected inventories come from the parser, not the catalog builder.
    containers = {"hardware", "runtime", "endpoint", "authentication", "recording", "status"}
    assert children("node") == profiles.NODE_FIELDS - containers
    assert children("node.hardware") == profiles.HARDWARE_FIELDS
    assert children("node.runtime") == profiles.HOST_LINK | {
        "decoder_worker_enabled",
        "decoder_output_batches",
    }
    assert children("profile") == profiles.META
    assert children("profile.waveform") == profiles.MODEM_FIELDS | {
        "sample_rate_hz",
        "bandwidth_hz",
    }
    assert (
        children("profile.frequency")
        == profiles.FIXED_FREQUENCY_FIELDS | profiles.HOPPING_FREQUENCY_FIELDS
    )
    assert children("profile.packet") == profiles.PACKET_FIELDS - {"frame"}
    assert children("profile.packet.frame") == profiles.FRAME_FIELDS
    assert children("profile.protection") == profiles.PROTECTION_FIELDS
    simple = {f.name for f in fields(SimpleLinkSettings)} - {"operation", "input_kind"}
    assert children("profile.link") == {"mode"} | profiles.PORTABLE_LINK | simple
    for section, model in [
        ("endpoint", EndpointConfig),
        ("authentication", AuthenticationConfig),
        ("recording", RecordingConfig),
        ("status", StatusConfig),
    ]:
        expected = {f.name for f in fields(model)}
        if section == "endpoint":
            expected.remove("max_payload_bytes")
        assert children("node." + section) == expected


def test_catalog_has_stable_unique_ids_and_explicit_dispositions():
    assert len(FIELDS) == len(CATALOG)
    for field in CATALOG:
        assert field.key.startswith(("node.", "profile.", "derived."))
        assert field.label and field.section and field.editor and field.help
        assert field.default_hint and field.constraint_hint and field.validation_path
        assert field.persistence
        if field.access != "editable":
            assert field.reason and not field.restart_required
        else:
            assert field.restart_required
    assert FIELDS["derived.pulse_shaping"].persistence == "not persisted"
    assert FIELDS["profile.waveform.bit_rate_bps"].persistence.startswith("profile")


@pytest.mark.parametrize("advanced", [False, True])
@pytest.mark.parametrize("modulation", MODULATIONS)
def test_only_selected_modulations_waveform_controls_are_visible(modulation, advanced):
    selection = Selection(modulation=modulation)
    shown = names(selection, advanced)
    for key in ["modulation", "bit_rate_bps", "sample_rate_hz", "bandwidth_hz"]:
        assert "profile.waveform." + key in shown
    assert ("profile.waveform.frequency_deviation_hz" in shown) == (modulation == "2fsk")
    for key in ["gaussian_bt", "gaussian_span_symbols"]:
        assert ("profile.waveform." + key in shown) == (modulation == "gmsk")
    assert ("derived.pulse_shaping" in shown) == (modulation in ("bpsk", "qpsk"))
    assert "profile.waveform.rrc_span_symbols" not in shown
    assert ("derived.frequency_deviation_hz" in shown) == (modulation in ("msk", "gmsk"))
    assert ("profile.protection.bit_repetitions" in shown) == (modulation == "bpsk" and advanced)


@pytest.mark.parametrize("modulation", ["bpsk", "qpsk"])
def test_rrc_fields_follow_pulse_shaping_selection(modulation):
    selection = Selection(modulation=modulation, rrc=True)
    assert {"profile.waveform.rrc_rolloff", "profile.waveform.rrc_span_symbols"} <= names(selection)
    assert "profile.waveform.rrc_rolloff" not in names(replace(selection, rrc=False))


@pytest.mark.parametrize("modulation", MODULATIONS)
def test_format_choices_match_backend_capabilities(modulation):
    expected = ("generic-v5", "simple-frame-v1") if modulation == "2fsk" else ("generic-v5",)
    assert compatible_formats(modulation) == expected
    assert available_choices("profile.packet.format", Selection(modulation=modulation)) == expected
    if modulation != "2fsk":
        with pytest.raises(ValueError, match="incompatible"):
            Selection(modulation=modulation, packet_format="simple-frame-v1")


def test_fixed_generic_format_does_not_offer_custom_framing_or_hopping():
    selection = Selection()
    assert not any(key.startswith("profile.packet.frame.") for key in names(selection))
    assert available_choices("profile.frequency.mode", selection) == ("fixed",)
    assert available_choices("profile.protection.scheme", selection) == ("extended-hamming-8-4",)
    assert "node.operation" not in names(selection)
    assert "node.input_kind" not in names(selection)
    assert "profile.link.ack_timeout_seconds" in names(selection)
    assert "profile.link.minimum_gap_seconds" not in names(selection)


def test_custom_frame_dependencies_and_deferred_transforms():
    selection = Selection(
        modulation="2fsk", packet_format="simple-frame-v1", fec="none", crc="none"
    )
    shown = names(selection)
    assert {
        "profile.packet.frame.preamble",
        "profile.packet.frame.sync",
        "profile.packet.frame.length_bytes",
    } <= shown
    for key in [
        "profile.packet.frame.crc_byte_order",
        "profile.packet.frame.fixed_payload_bytes",
        "profile.protection.interleaver_depth",
        "profile.protection.rs_parity_bytes",
        "profile.link.ack_timeout_seconds",
        "node.authentication.private_key_file",
    ]:
        assert key not in shown
    enabled = replace(
        selection, fixed_length=True, crc="crc32-iso-hdlc", fec="extended-hamming-8-4"
    )
    assert {
        "profile.packet.frame.crc_byte_order",
        "profile.packet.frame.fixed_payload_bytes",
        "profile.protection.interleaver_depth",
        "profile.protection.rs_parity_bytes",
    } <= names(enabled)
    assert "profile.packet.frame.length_bytes" not in names(enabled)
    assert "profile.packet.frame.length_byte_order" not in names(enabled)
    for key in ["whitening", "differential"]:
        field = FIELDS["profile.packet.frame." + key]
        assert field.access == "deferred" and field.reason and field.key not in names(enabled)


def test_hop_form_replaces_fixed_frequency_and_uses_one_canonical_dwell():
    selection = Selection(
        modulation="2fsk", packet_format="simple-frame-v1", frequency_mode="hopping"
    )
    assert "profile.frequency.center_frequency_hz" not in names(selection)
    assert {
        "profile.frequency.frequencies_hz",
        "profile.frequency.epoch",
        "derived.hop_rate",
    } <= names(selection)
    assert FIELDS["profile.frequency.dwell_seconds"].access == "read_only"
    assert FIELDS["derived.hop_rate"].restart_required
    assert available_choices("profile.frequency.mode", selection) == ("fixed", "hopping")
    assert "profile.frequency.frequencies_hz" not in names(
        replace(selection, frequency_mode="fixed")
    )


@pytest.mark.parametrize("operation", ["transmit-only", "receive-only", "bidirectional"])
def test_direction_specific_fields(operation):
    selection = Selection(modulation="2fsk", packet_format="simple-frame-v1", operation=operation)
    shown = names(selection)
    for key in [
        "node.hardware.tx_gain",
        "node.endpoint.bind_port",
        "node.input_kind",
        "profile.link.minimum_gap_seconds",
        "node.runtime.hardware_tx_batch_packets",
    ]:
        assert (key in shown) == (operation != "receive-only")
    for key in [
        "node.hardware.rx_gain",
        "node.endpoint.destination_port",
        "node.runtime.decoder_worker_enabled",
    ]:
        assert (key in shown) == (operation != "transmit-only")


def test_hardware_and_advanced_dependencies():
    selection = Selection(
        device_type="b200",
        timed_io=False,
        decoder_worker=False,
        http_status=False,
        status_output="off",
    )
    shown = names(selection)
    assert "node.hardware.device.serial" in shown and "node.hardware.device.addr" not in shown
    assert "node.hardware.device.addr" in names(replace(selection, device_type="x300"))
    for key in [
        "node.runtime.hardware_timed_tx_lead_seconds",
        "node.runtime.decoder_output_batches",
        "node.status.http_port",
        "node.status.interval_seconds",
        "node.recording.iq",
        "profile.link.tdd_guard_seconds",
        "node.runtime.hardware_slot_lookahead_seconds",
    ]:
        assert key not in shown
    enabled = replace(
        selection,
        timed_io=True,
        clock_sync=True,
        tdd=True,
        slot_scheduling=True,
        decoder_worker=True,
        http_status=True,
        status_output="text",
        recording=True,
    )
    for key in [
        "node.runtime.hardware_timed_tx_lead_seconds",
        "node.runtime.decoder_output_batches",
        "node.status.http_port",
        "node.status.interval_seconds",
        "node.recording.iq",
        "profile.link.tdd_guard_seconds",
        "node.runtime.hardware_slot_lookahead_seconds",
    ]:
        assert key in names(enabled)
    assert names(enabled, False) <= names(enabled, True)


def test_switching_modulations_does_not_leak_visibility_or_mutate_prior_selection():
    initial = Selection(modulation="2fsk")
    before = names(initial)
    for modulation in ["gmsk", "qpsk", "2fsk"]:
        current = replace(initial, modulation=modulation)
        assert ("profile.waveform.gaussian_bt" in names(current)) == (modulation == "gmsk")
        assert names(initial) == before
    assert names(current) == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"modulation": "unknown"},
        {"frequency_mode": "hopping"},
        {"operation": "transmit-only"},
        {"fec": "none"},
        {"modulation": "msk", "rrc": True},
        {"device_type": "soapy"},
        {"crc": "invented"},
        {"status_output": "invalid"},
    ],
)
def test_invalid_context_selectors_fail_explicitly(kwargs):
    with pytest.raises(ValueError):
        Selection(**kwargs)


def test_unknown_catalog_key_is_not_silently_accepted():
    with pytest.raises(KeyError):
        available_choices("profile.waveform.typo", Selection())


def test_catalog_import_never_loads_optional_ui_or_uhd():
    code = """
import sys
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'textual', 'uhd'}:
            raise RuntimeError('unexpected optional import: ' + fullname)
sys.meta_path.insert(0, Block())
from packet_radio.configuration.catalog import Selection, visible_fields
from packet_radio.configuration.draft import RadioDraft
assert visible_fields(Selection())
import socket
def deny_socket(*args, **kwargs):
    raise AssertionError('offline validation must not open sockets')
socket.socket = deny_socket
assert RadioDraft.load('configs/v1_1/peer-a-simple.yaml').validate().valid
"""
    subprocess.run(
        [sys.executable, "-c", code], check=True, cwd=Path(__file__).resolve().parents[2]
    )


def test_every_available_control_is_reachable_in_a_supported_context():
    seen = set()
    for modulation in MODULATIONS:
        for device in ("b200", "x300"):
            seen |= names(
                Selection(
                    modulation=modulation,
                    device_type=device,
                    rrc=modulation in ("bpsk", "qpsk"),
                    clock_sync=True,
                    tdd=True,
                    slot_scheduling=True,
                    recording=True,
                )
            )
    for fixed in (False, True):
        for frequency in ("fixed", "hopping"):
            seen |= names(
                Selection(
                    modulation="2fsk",
                    packet_format="simple-frame-v1",
                    fixed_length=fixed,
                    frequency_mode=frequency,
                )
            )
    assert seen == {field.key for field in CATALOG if field.access != "deferred"}


def test_turnaround_only_applies_when_simple_radio_both_receives_and_transmits():
    selection = Selection(modulation="2fsk", packet_format="simple-frame-v1")
    key = "profile.link.turnaround_seconds"
    assert key in names(selection)
    assert key not in names(replace(selection, operation="transmit-only"))
    assert key not in names(replace(selection, operation="receive-only"))
