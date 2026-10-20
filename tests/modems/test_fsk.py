from dataclasses import replace

import numpy as np
import pytest

from packet_radio.config import load_config, validate_config, validate_network
from packet_radio.modems.burst import SYNC, TRAINING
from packet_radio.modems.fec import encode
from packet_radio.modems.fsk import FSK2Modem
from packet_radio.modems.registry import make_modem
from packet_radio.protocol.framing import RadioPacket, has_valid_crc
from packet_radio.recording import CaptureRecorder, replay_iq


def reference_iq(payload, sps=8, deviation=125000, rate=2000000):
    protected = encode(payload)
    wire = TRAINING + SYNC + len(protected).to_bytes(2, "big") + protected
    # Independent scalar phase accumulator, deliberately not the modem's vector path.
    phase = 0.0
    samples = []
    for byte in wire:
        for shift in range(7, -1, -1):
            frequency = deviation if byte & (1 << shift) else -deviation
            for _ in range(sps):
                phase += 2 * np.pi * frequency / rate
                samples.append(complex(np.cos(phase), np.sin(phase)))
    return np.array(samples, dtype=np.complex64), wire


def test_independent_phase_reference_and_frequency_polarity():
    modem = FSK2Modem(8, sample_rate=2000000, frequency_deviation_hz=125000)
    payload = bytes(range(64))
    expected, wire = reference_iq(payload)
    actual = modem.modulate(payload)
    np.testing.assert_allclose(actual, expected, atol=2e-5)
    np.testing.assert_allclose(np.abs(actual), 1, atol=1e-6)
    bits = np.repeat(np.unpackbits(np.frombuffer(wire, dtype=np.uint8)), 8)
    frequencies = np.angle(actual[1:] * actual[:-1].conj()) * 2000000 / (2 * np.pi)
    np.testing.assert_allclose(frequencies, (bits[1:].astype(float) * 2 - 1) * 125000, atol=0.1)
    assert len(actual) == modem.sample_count(len(payload))
    assert list(modem.feed(expected)) == [payload]


@pytest.mark.parametrize("rate", [125000, 250000, 500000])
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
@pytest.mark.parametrize("offset", [-750, 0, 750])
def test_frozen_impairment_matrix(rate, size, offset):
    rng = np.random.default_rng(6006)
    sps = 2000000 // rate
    payload = rng.bytes(size)
    wire = RadioPacket(1, 2, 42, payload).encode()
    tx = FSK2Modem(sps, sample_rate=2000000, frequency_deviation_hz=rate / 2)
    rx = FSK2Modem(sps, sample_rate=2000000, frequency_deviation_hz=rate / 2)
    rx.candidate_validator = has_valid_crc
    iq = np.concatenate((np.zeros(sps - 1), tx.modulate(wire), np.zeros(2 * sps)))
    iq *= np.exp(1j * (0.73 + 2 * np.pi * offset * np.arange(len(iq)) / 2000000))
    iq += 0.08 * (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
    result = []
    pos = 0
    while pos < len(iq):
        end = pos + int(rng.integers(1, 8193))
        result.extend(rx.feed(iq[pos:end]))
        pos = end
    assert result == [wire]


@pytest.mark.parametrize("offset", [-31250, 31250])
@pytest.mark.parametrize("phase", range(8))
def test_independent_reference_offset_phase_and_timestamp(offset, phase):
    wire = RadioPacket(1, 2, 1, b"\x00\xffreference").encode()
    iq, _ = reference_iq(wire)
    iq *= np.exp(1j * (1.3 + 2 * np.pi * offset * np.arange(len(iq)) / 2000000))
    iq = np.concatenate((np.zeros(phase), iq, np.zeros(16)))
    rx = FSK2Modem(8, sample_rate=2000000, frequency_deviation_hz=125000)
    decoded = []
    for pos in range(0, len(iq), 257):
        decoded.extend(rx.feed_timed(iq[pos : pos + 257], 1000000000 + pos * 500))
    assert len(decoded) == 1
    assert decoded[0][0] == wire
    assert abs(decoded[0][1] - (1000000000 + phase * 500)) <= 500


@pytest.mark.parametrize(
    "changes",
    [
        {"frequency_deviation_hz": None},
        {"frequency_deviation_hz": 0},
        {"frequency_deviation_hz": float("nan")},
        {"frequency_deviation_hz": 500000},
        {"bandwidth": 500000},
        {"samples_per_symbol": 2},
        {"rrc_rolloff": 0.35},
        {"bit_repetitions": 2},
    ],
)
def test_configuration_rejects_unsupported_waveform(changes):
    c = load_config("configs/examples/peer-a-2fsk.yaml")
    with pytest.raises(ValueError):
        validate_config(replace(c, sdr=replace(c.sdr, **changes)))


def test_peer_deviations_must_match():
    a, b = [load_config(f"configs/examples/peer-{n}-2fsk.yaml") for n in ("a", "b")]
    b = replace(b, sdr=replace(b.sdr, frequency_deviation_hz=100000))
    with pytest.raises(ValueError, match="deviation"):
        validate_network({"a": a, "b": b})


def test_reset_discontinuity_multiple_bursts_and_bad_crc():
    rx = FSK2Modem(8, sample_rate=2000000, frequency_deviation_hz=125000)
    rx.candidate_validator = has_valid_crc
    wire = RadioPacket(1, 2, 2, b"\x00\xff").encode()
    iq = rx.modulate(wire)
    assert rx.feed_timed(iq[:2000], 0) == []
    assert rx.feed_timed(iq[2000:], 9000000000) == []
    rx.reset()
    damaged = wire[:-1] + bytes([wire[-1] ^ 1])
    assert list(rx.feed(np.concatenate((rx.modulate(damaged), np.zeros(100), iq)))) == [
        damaged,
        wire,
    ]
    assert rx.candidate_validator is has_valid_crc


def test_capture_replays_configured_deviation(tmp_path):
    c = load_config("configs/examples/peer-a-2fsk.yaml")
    wire = RadioPacket(1, 2, 3, b"capture\x00").encode()
    recorder = CaptureRecorder(
        tmp_path,
        sample_rate=2000000,
        samples_per_symbol=8,
        modulation="2fsk",
        frequency_deviation_hz=125000,
        min_free_bytes=0,
    )
    recorder.record_iq(make_modem(c.sdr).modulate(wire))
    recorder.close()
    assert list(replay_iq(tmp_path)) == [wire]


@pytest.mark.parametrize("modulation", ["2fsk", "msk", "gmsk"])
def test_live_length_aware_decode_preserves_maximum_and_following_frame(modulation):
    cfg = load_config(f"configs/bench/peer-a-{modulation}.yaml")
    tx, rx = make_modem(cfg.sdr), make_modem(cfg.sdr)
    rx.candidate_validator = has_valid_crc
    packets = [
        RadioPacket(1, 2, i, payload).encode()
        for i, payload in enumerate([bytes(range(256)) * 4, b"next"])
    ]
    waves = [tx.modulate(packet) * 0.03 for packet in packets]
    # Above-floor tail and an immediately following packet must neither duplicate
    # the early-delivered maximum frame nor hide the second preamble.
    stream = np.concatenate(
        (
            np.zeros(16384, np.complex64),
            waves[0],
            np.full(4096, 0.001, np.complex64),
            waves[1],
            np.full(16384, 0.001, np.complex64),
            np.zeros(16384, np.complex64),
        )
    )
    rng = np.random.default_rng(104)
    stream += (rng.normal(0, 1e-5, len(stream)) + 1j * rng.normal(0, 1e-5, len(stream))).astype(
        np.complex64
    )
    received = []
    first_at = None
    for start in range(0, len(stream), 4096):
        frames = rx.feed_timed(stream[start : start + 4096], 1000000000 + start * 500)
        if frames and first_at is None:
            first_at = start + 4096
        received.extend(frame for frame, _ in frames)
    assert received == packets
    assert first_at <= 16384 + len(waves[0]) + 8192
    assert len(rx._samples) <= 2 * rx.sample_count(rx.max_frame)
