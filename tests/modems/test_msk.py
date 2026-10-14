"""Independent MSK phase reference and frozen streaming impairment gates."""

from dataclasses import replace

import numpy as np
import pytest

from packet_radio.config import load_config, validate_config, validate_network
from packet_radio.modems.burst import SYNC, TRAINING
from packet_radio.modems.fec import encode
from packet_radio.modems.msk import MSKModem
from packet_radio.modems.registry import make_modem
from packet_radio.protocol.framing import RadioPacket, has_valid_crc
from packet_radio.recording import CaptureRecorder, load_manifest, replay_iq


def reference_iq(payload, sps):
    """Evaluate MSK's analytic phase in each bit, independent of the TX accumulator."""
    protected = encode(payload)
    wire = TRAINING + SYNC + len(protected).to_bytes(2, "big") + protected
    bits = np.unpackbits(np.frombuffer(wire, dtype=np.uint8)).astype(int)
    signs = 2 * bits - 1
    phase_units = 0
    result = []
    for sign in signs:
        # Boundary phase is an integer multiple of pi/2. Sampling includes the
        # bit's right boundary, matching the documented burst sample convention.
        for sample in range(1, sps + 1):
            phase = (phase_units + int(sign) * sample / sps) * np.pi / 2
            result.append(complex(np.cos(phase), np.sin(phase)))
        phase_units = (phase_units + int(sign)) % 4
    return np.asarray(result, dtype=np.complex64), signs


@pytest.mark.parametrize("rate", [125000, 250000, 500000])
def test_independent_reference_phase_steps_constant_envelope_and_burst_reset(rate):
    sps = 2000000 // rate
    modem = MSKModem(sps, sample_rate=2000000)
    packet = RadioPacket(1, 2, 7, bytes(range(256))).encode()
    expected, signs = reference_iq(packet, sps)
    actual = modem.modulate(packet)
    np.testing.assert_allclose(actual, expected, atol=2e-5)
    np.testing.assert_allclose(np.abs(actual), 1, atol=1e-6)
    boundaries = np.concatenate(([1 + 0j], actual[sps - 1 :: sps]))
    np.testing.assert_allclose(
        np.angle(boundaries[1:] * boundaries[:-1].conj()), signs * np.pi / 2, atol=1e-5
    )
    assert modem.frequency_deviation_hz == rate / 4
    assert len(actual) == modem.sample_count(len(packet))
    modem.modulate(b"unrelated burst")
    np.testing.assert_array_equal(modem.modulate(packet), actual)
    assert list(MSKModem(sps, sample_rate=2000000).feed(expected)) == [packet]


@pytest.mark.parametrize("rate", [125000, 250000, 500000])
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
@pytest.mark.parametrize("offset", [-750, 0, 750])
def test_frozen_noise_offset_matrix(rate, size, offset):
    rng = np.random.default_rng(7007)
    sps = 2000000 // rate
    wire = RadioPacket(1, 2, 42, rng.bytes(size)).encode()
    tx, rx = [MSKModem(sps, sample_rate=2000000) for _ in range(2)]
    rx.candidate_validator = has_valid_crc
    iq = np.concatenate((np.zeros(sps - 1), tx.modulate(wire), np.zeros(2 * sps)))
    iq *= np.exp(1j * (0.73 + 2 * np.pi * offset * np.arange(len(iq)) / 2000000))
    iq += 0.08 * (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
    decoded = []
    pos = 0
    while pos < len(iq):
        end = pos + int(rng.integers(1, 8193))
        decoded.extend(rx.feed(iq[pos:end]))
        pos = end
    assert decoded == [wire]


@pytest.mark.parametrize("offset", [-15625, 15625])
@pytest.mark.parametrize("phase", range(8))
def test_independent_reference_acquisition_and_timestamp(offset, phase):
    wire = RadioPacket(1, 2, 1, b"\x00\xffMSK reference").encode()
    iq, _ = reference_iq(wire, 8)
    iq *= np.exp(1j * (1.3 + 2 * np.pi * offset * np.arange(len(iq)) / 2000000))
    iq = np.concatenate((np.zeros(phase), iq, np.zeros(16)))
    rx = MSKModem(8, sample_rate=2000000)
    decoded = []
    for pos in range(0, len(iq), 257):
        decoded.extend(rx.feed_timed(iq[pos : pos + 257], 1000000000 + pos * 500))
    assert len(decoded) == 1 and decoded[0][0] == wire
    assert abs(decoded[0][1] - (1000000000 + phase * 500)) <= 500


@pytest.mark.parametrize(
    "changes",
    [
        {"frequency_deviation_hz": 62500},
        {"frequency_deviation_hz": 0},
        {"samples_per_symbol": 2},
        {"bit_repetitions": 2},
        {"rrc_rolloff": 0.35},
        {"bandwidth": 500000},
    ],
)
def test_invalid_msk_settings_are_rejected(changes):
    config = load_config("configs/examples/peer-a-msk.yaml")
    with pytest.raises(ValueError):
        validate_config(replace(config, sdr=replace(config.sdr, **changes)))


def test_direct_constructor_rejects_deviation_override():
    with pytest.raises(ValueError, match="derives"):
        MSKModem(frequency_deviation_hz=62500)


def test_msk_profiles_and_peer_waveforms_must_match():
    a, b = [load_config(f"configs/examples/peer-{peer}-msk.yaml") for peer in ("a", "b")]
    validate_network({"a": a, "b": b})
    assert make_modem(a.sdr).frequency_deviation_hz == 62500
    b = replace(b, sdr=replace(b.sdr, modulation="2fsk", frequency_deviation_hz=62500))
    with pytest.raises(ValueError):
        validate_network({"a": a, "b": b})


def test_discontinuity_reset_and_back_to_back_bursts():
    tx, rx = [MSKModem(8, sample_rate=2000000) for _ in range(2)]
    one = RadioPacket(1, 2, 8, b"first\x00\xff").encode()
    two = RadioPacket(1, 2, 9, b"second").encode()
    iq = tx.modulate(one)
    assert rx.feed_timed(iq[:2000], 0) == []
    assert rx.feed_timed(iq[2000:], 9000000000) == []
    rx.reset()
    assert list(rx.feed(np.concatenate((iq, tx.modulate(two))))) == [one, two]


def test_capture_replays_derived_msk_parameters(tmp_path):
    config = load_config("configs/examples/peer-a-msk.yaml")
    wire = RadioPacket(1, 2, 3, b"capture\x00").encode()
    recorder = CaptureRecorder(
        tmp_path, sample_rate=2000000, samples_per_symbol=8, modulation="msk", min_free_bytes=0
    )
    recorder.record_iq(make_modem(config.sdr).modulate(wire))
    recorder.close()
    manifest = load_manifest(tmp_path)
    assert manifest["burst_format"] == "msk-burst-v1"
    assert manifest["frequency_deviation_hz"] is None
    assert list(replay_iq(tmp_path)) == [wire]
