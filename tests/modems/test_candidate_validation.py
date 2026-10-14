import numpy as np
import pytest

from packet_radio.modems import BPSKModem, QPSKModem
from packet_radio.modems.fec import FECDecodeResult
from packet_radio.protocol.framing import RadioPacket, has_valid_crc


@pytest.mark.parametrize("kind", [BPSKModem, QPSKModem])
def test_crc_bad_first_fec_candidate_does_not_discard_recoverable_burst(kind, monkeypatch):
    wire = RadioPacket(1, 2, 1, b"candidate recovery\x00\xff").encode()
    damaged = wire[:-1] + bytes([wire[-1] ^ 1])
    tx, rx = kind(8), kind(8)
    rx.candidate_validator = has_valid_crc
    decode = rx.protection.decode
    attempts = []

    def miscorrect_once(data):
        attempts.append(data)
        if len(attempts) == 1:
            return FECDecodeResult(damaged, 3)
        return decode(data)

    monkeypatch.setattr(rx.protection, "decode", miscorrect_once)
    assert list(rx.feed(tx.modulate(wire))) == [wire]
    assert len(attempts) > 1
    assert rx.fec_corrected_bits == 0  # rejected candidate corrections are not accepted


@pytest.mark.parametrize("kind", [BPSKModem, QPSKModem])
def test_crc_rejected_burst_retains_packet_diagnostics_and_next_burst(kind):
    valid = RadioPacket(1, 2, 7, b"valid\x00").encode()
    invalid = valid[:-1] + bytes([valid[-1] ^ 1])
    tx, rx = kind(8), kind(8)
    rx.candidate_validator = has_valid_crc
    assert list(rx.feed(tx.modulate(invalid))) == [invalid]
    rx.reset()
    assert rx.candidate_validator is has_valid_crc
    assert list(rx.feed(np.concatenate((np.zeros(19), tx.modulate(valid))))) == [valid]


def test_crc_helper_does_not_replace_packet_authentication():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    signed = RadioPacket(1, 2, 1, b"signed").encode(key)
    assert has_valid_crc(signed)
    assert not has_valid_crc(b"")
    assert not has_valid_crc(signed[:-1])
    with pytest.raises(ValueError, match="verification failed"):
        RadioPacket.decode(signed, {1: (Ed25519PrivateKey.generate().public_key(),)})
