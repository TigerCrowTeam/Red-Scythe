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


@pytest.mark.parametrize("kind", [BPSKModem, QPSKModem])
@pytest.mark.parametrize("offset", [0, 1, 4, 7])
@pytest.mark.parametrize("gap", [0, 64])
@pytest.mark.parametrize("rolloff", [0.0, 0.35])
def test_earlier_weaker_frame_survives_later_stronger_candidate(kind, offset, gap, rolloff):
    from packet_radio.protocol.framing import ACK_REFERENCE, PacketKind

    tx = kind(8, rrc_rolloff=rolloff)
    rx = kind(8, rrc_rolloff=rolloff)
    rx.candidate_validator = has_valid_crc
    first = RadioPacket(1, 2, 1, ACK_REFERENCE.pack(7, 42), PacketKind.ACK).encode()
    second = RadioPacket(1, 2, 2, bytes(range(150))).encode()
    third = RadioPacket(1, 2, 3, b"following frame").encode()
    early = tx.modulate(first) * 0.6
    # Slightly damage training, leaving the valid frame body untouched. Ranking
    # whole bursts by preamble quality used to consume the later DATA first.
    early[80:88] *= -1
    samples = np.concatenate(
        (
            np.zeros(offset),
            early,
            np.zeros(gap),
            tx.modulate(second),
            np.zeros(64),
            tx.modulate(third),
        )
    )
    decoded = rx.feed_timed(samples, 1_000_000_000)
    assert [wire for wire, _ in decoded] == [first, second, third]
    arrivals = [arrival for _, arrival in decoded]
    assert all(a < b for a, b in zip(arrivals, arrivals[1:]))
    assert list(rx.feed(np.zeros(256))) == []


@pytest.mark.parametrize("kind", [BPSKModem, QPSKModem])
@pytest.mark.parametrize("block_size", [4093, 4096])
@pytest.mark.parametrize("rolloff", [0.0, 0.35])
def test_live_fragmented_coalesced_frames_preserve_order(kind, block_size, rolloff):
    tx = kind(8, sample_rate=2_000_000, rrc_rolloff=rolloff)
    rx = kind(8, sample_rate=2_000_000, rrc_rolloff=rolloff)
    rx.candidate_validator = has_valid_crc
    wires = [
        RadioPacket(1, 2, i, bytes([i]) * size).encode() for i, size in enumerate([12, 256, 64])
    ]
    first = tx.modulate(wires[0]) * 0.006
    first[80:88] *= -1
    samples = np.concatenate(
        (
            np.zeros(8192),
            first,
            np.zeros(64),
            tx.modulate(wires[1]) * 0.01,
            np.zeros(64),
            tx.modulate(wires[2]) * 0.01,
            np.zeros(8192),
        )
    ).astype(np.complex64)
    rng = np.random.default_rng(871)
    samples += (rng.normal(size=len(samples)) + 1j * rng.normal(size=len(samples))) * 1e-6
    decoded = []
    for start in range(0, len(samples), block_size):
        decoded.extend(
            rx.feed_timed(samples[start : start + block_size], 1_000_000_000 + start * 500)
        )
    assert [wire for wire, _ in decoded] == wires
    arrivals = [arrival for _, arrival in decoded]
    assert all(a < b for a, b in zip(arrivals, arrivals[1:]))


@pytest.mark.parametrize("rolloff", [0.0, 0.35])
def test_bpsk_live_frame_is_delivered_before_energy_tail_ends(rolloff):
    tx = BPSKModem(8, sample_rate=2_000_000, rrc_rolloff=rolloff)
    rx = BPSKModem(8, sample_rate=2_000_000, rrc_rolloff=rolloff)
    rx.candidate_validator = has_valid_crc
    wire = RadioPacket(1, 2, 9, bytes(range(256))).encode()
    noise = np.full(8192, 1e-6 + 1e-6j, dtype=np.complex64)
    assert list(rx.feed(noise)) == []
    samples = np.concatenate((tx.modulate(wire) * 0.01, np.full(12000, 0.005)))
    decoded = []
    for start in range(0, len(samples), 4096):
        decoded.extend(rx.feed(samples[start : start + 4096]))
    assert decoded == [wire]
    assert list(rx.feed(noise)) == []


@pytest.mark.parametrize("rolloff", [0.0, 0.35])
@pytest.mark.parametrize("offset", [0, 3, 7])
def test_bpsk_live_partial_final_symbol_does_not_commit_failure(rolloff, offset):
    tx = BPSKModem(8, sample_rate=2_000_000, rrc_rolloff=rolloff)
    rx = BPSKModem(8, sample_rate=2_000_000, rrc_rolloff=rolloff)
    rx.candidate_validator = has_valid_crc
    wire = RadioPacket(1, 2, 10, bytes(range(64))).encode()
    noise = np.full(8192, 1e-6 + 1e-6j, dtype=np.complex64)
    assert list(rx.feed(noise)) == []
    samples = np.concatenate((np.zeros(offset), tx.modulate(wire) * 0.01))
    # No idle transition yet: the last symbol/filter tail is still incomplete.
    assert list(rx.feed(samples[:-1])) == []
    assert rx.fec_uncorrectable_frames == 0
    assert list(rx.feed(np.concatenate((samples[-1:], np.zeros(4096))))) == [wire]
    assert rx.fec_uncorrectable_frames == 0
