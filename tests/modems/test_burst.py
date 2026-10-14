import numpy as np
import pytest
from reedsolo import RSCodec

from packet_radio.modems import BPSKModem, QPSKModem
from packet_radio.modems.burst import (
    QPSK_TRAINING,
    SYNC,
    TRAINING,
    BurstEnvelope,
    PacketProtection,
)
from packet_radio.modems.fec import encode


@pytest.mark.parametrize("size", [1, 64, 256, 1024])
@pytest.mark.parametrize("parity", [0, 16, 32])
def test_protection_lengths_match_actual_rs_blocks_and_decode(size, parity):
    data = bytes(i % 256 for i in range(size))
    protection = PacketProtection(16, parity)
    expected = encode(data, 16)
    if parity:
        expected = bytes(RSCodec(parity).encode(expected))
    assert protection.encode(data) == expected
    assert protection.encoded_size(size) == len(expected)
    assert protection.decode(expected).payload == data
    envelope = BurstEnvelope()
    wire = envelope.encode(expected)
    assert (
        wire
        == b"\x55" * 32
        + bytes.fromhex("55555555d391")
        + len(expected).to_bytes(2, "big")
        + expected
    )
    assert envelope.decode(wire) == expected
    with pytest.raises(ValueError):
        envelope.decode(wire[:-1])
    with pytest.raises(ValueError):
        envelope.decode(wire + b"\x00")


@pytest.mark.parametrize("kind,training", [(BPSKModem, TRAINING), (QPSKModem, QPSK_TRAINING)])
@pytest.mark.parametrize("parity", [0, 16, 32])
@pytest.mark.parametrize("rolloff", [0, 0.35])
def test_complete_airtime_includes_rs_chunking_and_filter_tail(kind, training, parity, rolloff):
    modem = kind(8, rs_parity_bytes=parity, rrc_rolloff=rolloff)
    data = bytes(range(256)) * 4
    protected = encode(data)
    if parity:
        protected = bytes(RSCodec(parity).encode(protected))
    wire = training + SYNC + len(protected).to_bytes(2, "big") + protected
    bits_per_symbol = 2 if kind is QPSKModem else 1
    count = len(wire) * 8 // bits_per_symbol * 8 + (64 if rolloff else 0)
    samples = modem.modulate(data)
    assert len(samples) == count == modem.sample_count(len(data))
    assert modem.frame_size(len(data)) == len(wire)


@pytest.mark.parametrize("kind", [BPSKModem, QPSKModem])
def test_reset_discards_partial_frame_and_keeps_configuration(kind):
    tx = kind(8)
    rx = kind(8)
    old = tx.modulate(b"old" * 128)
    assert not list(rx.feed(old[: len(old) // 2]))
    rx.fec_corrected_bits = 7
    rx.reset()
    assert rx.fec_corrected_bits == 7
    assert rx.sps == 8
    assert list(rx.feed(tx.modulate(b"fresh"))) == [b"fresh"]


def test_repetition_count_scales_complete_burst():
    modem = BPSKModem(8, bit_repetitions=3, rs_parity_bytes=16, rrc_rolloff=0.35)
    assert modem.sample_count(500) == len(modem.modulate(bytes(500)))


@pytest.mark.parametrize(
    "kind,training",
    [
        (BPSKModem, b"\x55" * 32),
        (
            QPSKModem,
            bytes.fromhex("d37a49c5a612f08e5b31e4972cd068bf91e6235dac7840f7b2c9540e6fa1833d"),
        ),
    ],
)
def test_modulated_symbols_match_independent_protocol_golden_bytes(kind, training):
    import json
    from pathlib import Path

    vector = json.loads(Path("fixtures/protocol_v5.json").read_text())["vectors"][0]
    wire = bytes.fromhex(vector["wire_hex"])
    bits = np.unpackbits(
        np.frombuffer(training + bytes.fromhex(vector["modem_envelope_hex"]), dtype=np.uint8)
    )
    if kind is BPSKModem:
        symbols = np.where(bits, 1.0, -1.0).astype(np.complex64)
    else:
        constellation = np.array([1 + 1j, -1 + 1j, 1 - 1j, -1 - 1j]) / np.sqrt(2)
        indices = bits[0::2] * 2 + bits[1::2]
        symbols = constellation[indices].astype(np.complex64)
    np.testing.assert_allclose(kind(8).modulate(wire), np.repeat(symbols, 8), rtol=0, atol=1e-7)
