"""Verify processing boundaries with independently composed bytes and substitutions."""

from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packet_radio.app import RadioApp
from packet_radio.config import load_config
from packet_radio.modems.burst import (
    BurstEnvelope,
    BurstProcessing,
    TransformChain,
)
from packet_radio.modems.fec import FECDecodeResult
from packet_radio.modems.registry import make_modem, waveform
from packet_radio.protocol.codec import GenericV5Codec
from packet_radio.protocol.framing import RadioPacket
from packet_radio.protocol.registry import processing_profile
from tests.integration.test_end_to_end import network, node


class Prefix:
    def encoded_size(self, size):
        return size + 1

    def encode(self, data):
        return b"P" + data

    def decode(self, data):
        assert data[:1] == b"P"
        return FECDecodeResult(data[1:], 2)


class Invert:
    def encoded_size(self, size):
        return size

    def encode(self, data):
        return bytes(b ^ 255 for b in data)

    def decode(self, data):
        return FECDecodeResult(self.encode(data), 3)


def test_transform_order_is_explicit_and_reversed_for_receive():
    chain = TransformChain((Prefix(), Invert()))
    assert chain.encode(b"\x00\xff") == b"\xaf\xff\x00"
    assert chain.encoded_size(2) == 3
    assert chain.decode(b"\xaf\xff\x00") == FECDecodeResult(b"\x00\xff", 5)


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_all_waveforms_use_injected_protection_and_sync(modulation):
    config = load_config(f"configs/examples/peer-a-{modulation}.yaml").sdr
    processing = BurstProcessing(
        TransformChain((Prefix(), Invert())),
        BurstEnvelope(waveform(modulation).training, bytes.fromhex("a7c35a9e")),
    )
    tx = make_modem(config, processing=processing)
    rx = make_modem(config, processing=processing)
    payload = bytes(range(64))
    samples = tx.modulate(payload)
    assert len(samples) == tx.sample_count(len(payload))
    assert tx.frame_size(len(payload)) == len(processing.envelope.training) + 4 + 2 + 65
    received = []
    for offset in range(0, len(samples), 257):
        received.extend(rx.feed(samples[offset : offset + 257]))
    assert received == [payload]


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_selected_generic_profile_preserves_legacy_samples_and_size(modulation):
    config = load_config(f"configs/examples/peer-a-{modulation}.yaml").sdr
    selected = processing_profile("generic-v5")
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    packet = RadioPacket(1, 2, 7, bytes(range(64)), session_id=42)
    wire = selected.codec.encode(packet, key)
    assert wire == packet.encode(key)
    assert selected.codec.payload_size(64, authenticated=True) == len(wire)
    assert selected.codec.decode(wire, {1: (key.public_key(),)}) == packet
    expected = make_modem(config).modulate(wire)
    modem = selected.make_modem(config)
    np.testing.assert_array_equal(modem.modulate(wire), expected)
    assert modem.sample_count(len(wire)) == len(expected)


def test_live_encode_decode_and_candidate_checks_use_selected_codec():
    net = network()
    try:
        for item in net.nodes.values():
            app = item.app
            codec = Mock(wraps=GenericV5Codec())
            app.codec = codec
            app.modem.candidate_validator = codec.candidate_valid
        net.inject("a", b"selected codec\x00\xff")
        net.step(100)
        assert net.received("b") == [b"selected codec\x00\xff"]
        assert net.nodes["a"].app.codec.encode.called
        assert net.nodes["b"].app.codec.decode.called
        assert net.nodes["b"].app.codec.candidate_valid.called
    finally:
        net.close()


def test_unknown_format_is_rejected_before_hardware_or_endpoint(monkeypatch):
    hardware = Mock()
    endpoint = Mock()
    monkeypatch.setattr("packet_radio.app.UHDRadio", hardware)
    monkeypatch.setattr("packet_radio.app.UDPEndpoint", endpoint)
    with pytest.raises(ValueError):
        RadioApp(replace(node("leader", 1), packet_format="simple-frame-v1"))
    hardware.assert_not_called()
    endpoint.assert_not_called()
    with pytest.raises(ValueError, match="unsupported processing profile"):
        processing_profile("simple-frame-v1")
