"""Check project radio profiles against independent bytes, IQ and digital spectra.

Prints final summaries only; never opens an SDR or writes captures/reports. The
analytic reference integrates tone phase at sample times, independently of the
production modulation, framing and protection code. Generic-v5 uses the frozen
wire/envelope vectors; simple frames use standard-library CRC reference bytes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from packet_radio.cli.conformance import validate_fixture
from packet_radio.config import load_config
from packet_radio.protocol.registry import resolve_processing


def reference(payload, rate, ppm=0, cfo=0, phase=0, noise=0):
    import binascii

    body = len(payload).to_bytes(2, "big") + payload
    wire = body + binascii.crc_hqx(body, 0xFFFF).to_bytes(2, "big")
    raw = b"\x55" * 32 + bytes.fromhex("a7c35a9e") + wire
    bits = np.array([(v >> i) & 1 for v in raw for i in range(7, -1, -1)])
    tones = 2 * bits - 1
    symbol_samples = 2e6 / rate * (1 + ppm * 1e-6)
    count = int(np.ceil(len(bits) * symbol_samples + phase))
    t = np.clip((np.arange(count) + 1 - phase) / symbol_samples, 0, len(bits))
    index = np.minimum(t.astype(int), len(bits) - 1)
    integral = np.concatenate(([0], np.cumsum(tones)))
    angle = (
        2
        * np.pi
        * (0.5 * (integral[index] + (t - index) * tones[index]) + cfo * np.arange(count) / 2e6)
    )
    iq = np.exp(1j * angle).astype(np.complex64)
    iq[np.arange(count) + 1 <= phase] = 0
    rng = np.random.default_rng(1101)
    iq += (rng.normal(0, noise, count) + 1j * rng.normal(0, noise, count)).astype(np.complex64)
    return wire, iq


def spectrum(samples, sample_rate):
    """Central 99% digital occupied band and normalized rectangular-window PSD."""
    if not np.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("sample rate must be finite and positive")
    samples = np.asarray(samples)
    if samples.ndim != 1 or not len(samples) or not np.isfinite(samples).all():
        raise ValueError("spectrum requires a finite nonempty IQ vector")
    nfft = 1 << (4 * len(samples) - 1).bit_length()
    power = np.abs(np.fft.fftshift(np.fft.fft(samples, nfft))) ** 2
    total = power.sum()
    if total <= 0:
        raise ValueError("spectrum requires nonzero power")
    power /= total
    cumulative = np.cumsum(power)
    bins = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / sample_rate))
    low, high = bins[np.searchsorted(cumulative, [0.005, 0.995])]
    return float(low), float(high), sample_rate / nfft, power


def compare(samples, expected, raw, rate, deviation, sample_rate=2e6):
    """Enforce the predeclared byte, sample, tone and spectrum tolerances."""
    expected_count = len(raw) * 8 * int(sample_rate / rate)
    assert len(samples) == len(expected) == expected_count, "sample count mismatch"
    error = float(np.max(np.abs(samples - expected)))
    assert error <= 1e-5, f"waveform error {error}"
    assert float(np.max(np.abs(np.abs(samples) - 1))) <= 1e-6, "envelope error"
    sps = int(sample_rate / rate)
    # Include the first increment relative to a known zero initial phase.
    increments = np.angle(samples * np.conj(np.r_[np.complex64(1), samples[:-1]]))
    tones = increments.reshape(-1, sps).mean(1) * sample_rate / (2 * np.pi)
    independent_bits = np.array([(b >> i) & 1 for b in raw for i in range(7, -1, -1)])
    assert np.array_equal(tones >= 0, independent_bits), "tone polarity/bit mismatch"
    tone_error = max(
        abs(float(tones[mask].mean()) - target)
        for mask, target in (
            (independent_bits == 0, -deviation),
            (independent_bits == 1, deviation),
        )
    )
    assert tone_error <= 1, f"tone error {tone_error} Hz"
    observed_rate = len(independent_bits) / (len(samples) / sample_rate)
    assert abs(observed_rate / rate - 1) <= 1e-12, "rate mismatch"
    low, high, bin_hz, power = spectrum(samples, sample_rate)
    ref_low, ref_high, _, ref_power = spectrum(expected, sample_rate)
    assert max(abs(low - ref_low), abs(high - ref_high)) <= bin_hz, "occupied band mismatch"
    spectral_error = float(np.abs(power - ref_power).sum())
    assert spectral_error <= 1e-5, f"spectral power error {spectral_error}"
    return {
        "sample_error": error,
        "tone_error_hz": tone_error,
        "occupied_hz": high - low,
        "bin_hz": bin_hz,
        "spectral_error": spectral_error,
        "duration_ms": len(samples) / sample_rate * 1000,
    }


def generic_reference(raw, rate=250000, deviation=125000, sample_rate=2000000):
    # Scalar bit walk and analytic phase; does not invoke the production FEC/envelope.
    sps = int(sample_rate / rate)
    bits = np.array([(b >> i) & 1 for b in raw for i in range(7, -1, -1)])
    integrated = np.r_[0, np.cumsum(2 * bits - 1)]
    t = (np.arange(len(bits) * sps) + 1) / sps
    index = np.minimum(t.astype(int), len(bits) - 1)
    phase = 2 * np.pi * deviation / rate * (integrated[index] + (t - index) * (2 * bits[index] - 1))
    return np.exp(1j * phase).astype(np.complex64)


def check(root):
    fixture_path = root / "fixtures/protocol_v5.json"
    validate_fixture(fixture_path)
    fixture = json.loads(fixture_path.read_text())
    # Expectations are independent of the profile values being checked.
    specs = {
        "simple": 250000,
        "simple-100k": 100000,
        "simple-500k": 500000,
        "hopping": 250000,
        "generic": 250000,
    }
    results = {}
    for name, rate in specs.items():
        cfg = load_config(root / f"configs/v1_1/peer-a-{name}.yaml")
        assert cfg.sdr.bit_rate_bps == rate and cfg.sdr.sample_rate == 2e6
        assert cfg.sdr.frequency_deviation_hz == rate / 2
        processing = resolve_processing(cfg)
        modem = processing.make_modem(cfg.sdr)
        rows = []
        if name == "generic":
            vectors = [
                (
                    bytes.fromhex(v["wire_hex"]),
                    b"\x55" * 32 + bytes.fromhex(v["modem_envelope_hex"]),
                )
                for v in fixture["vectors"]
            ]
            for wire, raw in vectors:
                expected = generic_reference(raw)
                rows.append(compare(modem.modulate(wire), expected, raw, rate, rate / 2))
                modem.reset()
                assert list(modem.feed(expected)) == [wire]
        else:
            for size in (1, 64, 256, 1024):
                payload = bytes(i % 256 for i in range(size))
                wire, expected = reference(payload, rate)
                assert processing.codec.encode(payload) == wire
                assert processing.codec.decode(wire) == payload
                raw = b"\x55" * 32 + bytes.fromhex("a7c35a9e") + wire
                rows.append(compare(modem.modulate(wire), expected, raw, rate, rate / 2))
                modem.reset()
                assert list(modem.feed(expected)) == [wire]
        results[name] = rows
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    for name, rows in check(args.root).items():
        print(
            f"{name}: {len(rows)} reference bursts passed; "
            f"max sample error={max(r['sample_error'] for r in rows):.3g}; "
            f"max tone error={max(r['tone_error_hz'] for r in rows):.3g} Hz; "
            f"digital OBW99 range={min(r['occupied_hz'] for r in rows):.3f}.."
            f"{max(r['occupied_hz'] for r in rows):.3f} Hz; "
            f"max normalized spectral L1 error={max(r['spectral_error'] for r in rows):.3g}"
        )
    print("PASS: project reference fidelity; no SDR, calibrated RF or target-radio claim.")


if __name__ == "__main__":
    main()
