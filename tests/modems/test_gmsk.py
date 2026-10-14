"""Independent Gaussian impulse/phase references and frozen GMSK gates."""

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from packet_radio.config import load_config, validate_config, validate_network
from packet_radio.modems.burst import QPSK_TRAINING, SYNC
from packet_radio.modems.fec import encode
from packet_radio.modems.gmsk import GMSKModem
from packet_radio.modems.registry import make_modem
from packet_radio.protocol.framing import RadioPacket, has_valid_crc
from packet_radio.recording import CaptureRecorder, load_manifest, replay_iq


def reference_iq(payload, sps, bt, span):
    """Scalar normal-density convolution and phase integration, independent of TX."""
    sigma = math.sqrt(math.log(2)) / (2 * math.pi * bt)
    taps = [
        math.exp(-((k / sps - span / 2) ** 2) / (2 * sigma**2)) / (math.sqrt(2 * math.pi) * sigma)
        for k in range(span * sps + 1)
    ]
    total = sum(taps)
    taps = [x / total for x in taps]
    protected = encode(payload)
    wire = QPSK_TRAINING + SYNC + len(protected).to_bytes(2, "big") + protected
    pulses = [
        1 if byte & (1 << bit) else -1
        for byte in wire
        for bit in range(7, -1, -1)
        for _ in range(sps)
    ]
    phase = 0.0
    iq = []
    for n in range(len(pulses) + len(taps) - 1):
        frequency = sum(
            taps[k] * pulses[n - k] for k in range(len(taps)) if 0 <= n - k < len(pulses)
        )
        phase += math.pi * frequency / (2 * sps)
        iq.append(complex(math.cos(phase), math.sin(phase)))
    return np.asarray(iq, dtype=np.complex64), np.asarray(taps)


@pytest.mark.parametrize("bt", [0.3, 0.5])
@pytest.mark.parametrize("span", [4, 6])
@pytest.mark.parametrize("sps", [4, 8, 16])
def test_independent_gaussian_phase_tail_and_reset(bt, span, sps):
    wire = RadioPacket(1, 2, 1, bytes(range(32))).encode()
    tx = GMSKModem(sps, sample_rate=2000000, gaussian_bt=bt, gaussian_span_symbols=span)
    reference, taps = reference_iq(wire, sps, bt, span)
    iq = tx.modulate(wire)
    np.testing.assert_allclose(tx.gaussian_taps, taps, atol=1e-15)
    np.testing.assert_allclose(iq, reference, atol=2e-5)
    np.testing.assert_allclose(np.abs(iq), 1, atol=1e-6)
    assert tx.receive_delay_samples == span * sps // 2
    assert len(iq) == tx.sample_count(len(wire)) == tx.frame_size(len(wire)) * 8 * sps + span * sps
    # Sampled FIR response at its named one-sided 3 dB frequency.
    response = abs(sum(taps * np.exp(-2j * np.pi * bt * np.arange(len(taps)) / sps)))
    assert response == pytest.approx(1 / math.sqrt(2), abs=2e-5)
    tx.modulate(b"different burst")
    np.testing.assert_array_equal(tx.modulate(wire), iq)
    rx = GMSKModem(sps, sample_rate=2000000, gaussian_bt=bt, gaussian_span_symbols=span)
    assert rx.feed_timed(reference, 1000000000) == [(wire, 1000000000)]


@pytest.mark.parametrize("bt", [0.3, 0.5])
@pytest.mark.parametrize("span", [4, 6])
@pytest.mark.parametrize("rate", [125000, 250000, 500000])
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
@pytest.mark.parametrize("offset", [-750, 0, 750])
def test_frozen_noise_offset_matrix(bt, span, rate, size, offset):
    rng = np.random.default_rng(8008)
    sps = 2000000 // rate
    wire = RadioPacket(1, 2, 42, rng.bytes(size)).encode()
    tx, rx = [
        GMSKModem(sps, sample_rate=2000000, gaussian_bt=bt, gaussian_span_symbols=span)
        for _ in range(2)
    ]
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


@pytest.mark.parametrize("bt", [0.3, 0.5])
@pytest.mark.parametrize("span", [4, 6])
@pytest.mark.parametrize("offset", [-15625, 15625])
@pytest.mark.parametrize("phase", range(8))
def test_reference_acquisition_timestamp_and_arbitrary_blocks(bt, span, offset, phase):
    wire = RadioPacket(1, 2, 1, b"\x00\xffGMSK reference").encode()
    iq, _ = reference_iq(wire, 8, bt, span)
    iq *= np.exp(1j * (1.3 + 2 * np.pi * offset * np.arange(len(iq)) / 2000000))
    iq = np.concatenate((np.zeros(phase), iq, np.zeros(16)))
    rx = GMSKModem(8, sample_rate=2000000, gaussian_bt=bt, gaussian_span_symbols=span)
    result = []
    for pos in range(0, len(iq), 257):
        result.extend(rx.feed_timed(iq[pos : pos + 257], 1000000000 + pos * 500))
    assert len(result) == 1 and result[0][0] == wire
    assert abs(result[0][1] - (1000000000 + phase * 500)) <= 500


@pytest.mark.parametrize(
    "changes",
    [
        {"gaussian_bt": None},
        {"gaussian_bt": 0},
        {"gaussian_bt": float("nan")},
        {"gaussian_bt": 0.2},
        {"gaussian_span_symbols": None},
        {"gaussian_span_symbols": 3},
        {"gaussian_span_symbols": 0},
        {"gaussian_span_symbols": 4.0},
        {"samples_per_symbol": 2},
        {"frequency_deviation_hz": 62500},
        {"bit_repetitions": 2},
        {"rrc_rolloff": 0.35},
        {"bandwidth": 500000},
    ],
)
def test_reject_unsupported_parameters(changes):
    config = load_config("configs/examples/peer-a-gmsk.yaml")
    with pytest.raises(ValueError):
        validate_config(replace(config, sdr=replace(config.sdr, **changes)))


@pytest.mark.parametrize("changes", [{"gaussian_bt": 0.5}, {"gaussian_span_symbols": 6}])
def test_peer_shaping_must_match(changes):
    a, b = [load_config(f"configs/examples/peer-{p}-gmsk.yaml") for p in ["a", "b"]]
    validate_network({"a": a, "b": b})
    with pytest.raises(ValueError, match="gaussian"):
        validate_network({"a": a, "b": replace(b, sdr=replace(b.sdr, **changes))})


def test_other_waveforms_reject_gaussian_controls():
    config = load_config("configs/examples/peer-a-msk.yaml")
    with pytest.raises(ValueError, match="only supported for GMSK"):
        validate_config(replace(config, sdr=replace(config.sdr, gaussian_bt=0.3)))


@pytest.mark.parametrize("bt", [0.3, 0.5])
@pytest.mark.parametrize("span", [4, 6])
def test_capture_replay_preserves_shaping_and_rejects_missing_settings(tmp_path, bt, span):
    tx = GMSKModem(8, sample_rate=2000000, gaussian_bt=bt, gaussian_span_symbols=span)
    wire = RadioPacket(1, 2, 1, b"capture\x00\xff").encode()
    recorder = CaptureRecorder(
        tmp_path,
        sample_rate=2000000,
        samples_per_symbol=8,
        modulation="gmsk",
        gaussian_bt=bt,
        gaussian_span_symbols=span,
        min_free_bytes=0,
    )
    recorder.record_iq(tx.modulate(wire))
    recorder.close()
    manifest = load_manifest(tmp_path)
    assert manifest["gaussian_bt"] == bt and manifest["gaussian_span_symbols"] == span
    assert manifest["burst_format"] == "gmsk-burst-v1"
    assert list(replay_iq(tmp_path, chunk_samples=257)) == [wire]
    del manifest["gaussian_bt"]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="requires"):
        load_manifest(tmp_path)


def test_reset_discontinuity_and_consecutive_full_tail_bursts():
    tx, rx = [GMSKModem(8, sample_rate=2000000) for _ in range(2)]
    a, b = [RadioPacket(1, 2, n, bytes(range(n + 8))).encode() for n in [1, 2]]
    iq = tx.modulate(a)
    assert rx.feed_timed(iq[:2000], 0) == []
    assert rx.feed_timed(iq[2000:], 9000000000) == []
    rx.reset()
    combined = np.concatenate((iq, tx.modulate(b)))
    decoded = rx.feed_timed(combined, 1000000000)
    assert decoded == [(a, 1000000000), (b, 1000000000 + len(iq) * 500)]


def test_airtime_includes_gaussian_tail():
    gmsk = load_config("configs/bench/peer-a-gmsk.yaml")
    wire = RadioPacket(1, 2, 0, bytes(1024)).encoded_size(authenticated=True)
    modem = make_modem(gmsk.sdr)
    assert modem.sample_count(wire) / gmsk.sdr.sample_rate == pytest.approx(0.072976)
    # At this limit, MSK would fit but the extra Gaussian tail does not.
    link = replace(gmsk.link, tdd_period_seconds=0.17594)
    with pytest.raises(ValueError, match="cannot fit"):
        validate_config(replace(gmsk, link=link))


def test_application_records_profile_shaping(tmp_path):
    from packet_radio.app import RadioApp
    from packet_radio.config import AuthenticationConfig
    from packet_radio.endpoint import MemoryEndpoint
    from packet_radio.simulation import SimulatedChannel

    config = load_config("configs/examples/peer-a-gmsk.yaml")
    config = replace(
        config,
        authentication=AuthenticationConfig(),
        sdr=replace(config.sdr, gaussian_bt=0.5, gaussian_span_symbols=6),
        recording=replace(config.recording, directory=str(tmp_path), min_free_bytes=0),
    )
    app = RadioApp(config, sdr=SimulatedChannel().radio(), endpoint=MemoryEndpoint())
    try:
        manifest = load_manifest(app.recorder.directory)
        assert manifest["gaussian_bt"] == 0.5
        assert manifest["gaussian_span_symbols"] == 6
    finally:
        app.close()


@pytest.mark.parametrize("bt", [0.3, 0.5])
def test_training_has_little_carrier_dc(bt):
    """Training must survive receivers that suppress a stationary carrier at DC."""
    modem = GMSKModem(8, sample_rate=2000000, gaussian_bt=bt)
    iq = modem.modulate(b"training spectrum")
    start = modem.receive_delay_samples
    training = iq[start : start + len(modem.training) * 8 * modem.sps]
    assert abs(training.mean()) < 0.1
