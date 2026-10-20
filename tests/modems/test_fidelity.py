"""Verify profile fidelity tolerances and recovery against independent IQ inputs."""

from pathlib import Path

import numpy as np
import pytest

from packet_radio.protocol.simple import make_frame_modem
from tools.fidelity_check import check, compare, generic_reference, reference, spectrum

ROOT = Path(__file__).resolve().parents[2]


def test_every_shipped_emulation_profile_matches_independent_reference():
    results = check(ROOT)
    assert set(results) == {"simple", "simple-100k", "simple-500k", "hopping", "generic"}
    assert sum(map(len, results.values())) == 19


@pytest.mark.parametrize("mutation", ["invert", "phase", "amplitude", "truncate", "tone"])
def test_fidelity_check_rejects_changed_waveforms(mutation):
    wire, expected = reference(bytes(range(64)), 250000)
    raw = b"\x55" * 32 + bytes.fromhex("a7c35a9e") + wire
    altered = expected.copy()
    if mutation == "invert":
        altered = np.conj(altered)
    elif mutation == "phase":
        altered *= np.exp(0.1j)
    elif mutation == "amplitude":
        altered *= 0.99
    elif mutation == "truncate":
        altered = altered[:-1]
    elif mutation == "tone":
        altered *= np.exp(2j * np.pi * 100 * np.arange(len(altered)) / 2e6)
    with pytest.raises(AssertionError):
        compare(altered, expected, raw, 250000, 125000)


def test_spectrum_known_tones_and_scale_invariance():
    n = 4096
    t = np.arange(n) / 2e6
    iq = np.exp(2j * np.pi * 125000 * t) + np.exp(-2j * np.pi * 125000 * t)
    low, high, width, psd = spectrum(iq, 2e6)
    assert low < -125000 < high and low < 125000 < high
    assert abs(low + high) <= width
    assert np.sum(psd) == pytest.approx(1)
    _, _, _, scaled = spectrum(3 * iq, 2e6)
    np.testing.assert_allclose(scaled, psd, atol=1e-14)


@pytest.mark.parametrize("samples", [[], [0, 0], [float("nan")], [[1, 2]]])
def test_spectrum_rejects_empty_zero_and_invalid_input(samples):
    with pytest.raises(ValueError):
        spectrum(np.asarray(samples), 2e6)


@pytest.mark.parametrize("rate", [100000, 250000, 500000])
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
@pytest.mark.parametrize("timestamped", [False, True])
def test_dropped_body_samples_never_splice_payload_and_next_frame_recovers(rate, size, timestamped):
    wire, iq = reference(bytes(i % 256 for i in range(size)), rate)
    intact, following = reference(b"intact after missing block", rate)
    sps = int(2e6 / rate)
    begin = (36 * 8 + 18) * sps
    end = begin + 4 * sps
    rx = make_frame_modem(bit_rate=rate, deviation=rate / 2, clock=lambda: 0)
    assert rx.feed_timed(iq[:begin], 0 if timestamped else None) == []
    # The timestamp preserves the omitted interval instead of concatenating timelines.
    suffix = np.r_[iq[end:], np.zeros(100 * sps, np.complex64), following]
    decoded = rx.feed_timed(suffix, end * 500 if timestamped else None)
    assert [frame for frame, _ in decoded] == [intact]
    if timestamped:
        expected_start = (len(iq) + 100 * sps) * 500
        assert abs(decoded[0][1] - expected_start) <= 500


@pytest.mark.parametrize("rate", [100000, 250000, 500000])
@pytest.mark.parametrize("damage", ["crc", "length", "truncated"])
def test_malformed_reference_and_following_valid_frame(rate, damage):
    wire, _ = reference(bytes(range(64)), rate)
    if damage == "crc":
        wire = wire[:-1] + bytes([wire[-1] ^ 1])
    elif damage == "length":
        wire = b"\xff\xff" + wire[2:]
    elif damage == "truncated":
        wire = wire[:8]
    bad = generic_reference(
        b"\x55" * 32 + bytes.fromhex("a7c35a9e") + wire, rate=rate, deviation=rate / 2
    )
    good, iq = reference(b"good next packet", rate)
    rx = make_frame_modem(bit_rate=rate, deviation=rate / 2, clock=lambda: 0)
    assert list(rx.feed(np.r_[bad, np.zeros(2000, np.complex64), iq])) == [good]


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf")])
def test_spectrum_invalid_sample_rate(rate):
    with pytest.raises(ValueError):
        spectrum(np.ones(8), rate)
