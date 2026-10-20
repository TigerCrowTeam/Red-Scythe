"""Independently integrate two FSK tones to test rate, clock and timestamp limits.

The reference uses analytic phase at sample times, including fractional symbol
boundaries, rather than resampling production IQ. CRC uses the standard-library
implementation. Sample-clock scale and carrier-frequency offset are independent.
"""

from copy import deepcopy

import numpy as np
import pytest

from packet_radio.config import load_config, validate_network
from packet_radio.profiles import normalize_definition
from packet_radio.protocol.simple import make_frame_modem
from tools.fidelity_check import reference


@pytest.mark.parametrize("rate", [100000, 250000, 500000])
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
def test_exact_rate_waveform_samples_and_airtime(rate, size):
    payload = bytes(i % 256 for i in range(size))
    wire, independent = reference(payload, rate)
    modem = make_frame_modem(bit_rate=rate, deviation=rate / 2, clock=lambda: 0)
    emitted = modem.modulate(wire)
    expected = (size + 40) * 8 * (2000000 // rate)
    assert len(emitted) == modem.sample_count(len(wire)) == expected
    assert len(emitted) / 2e6 == pytest.approx((size + 40) * 8 / rate)
    np.testing.assert_allclose(emitted, independent, atol=1e-5, rtol=0)


@pytest.mark.parametrize("rate", [100000, 250000, 500000])
@pytest.mark.parametrize(
    "ppm,cfo",
    [(-10, 0), (10, 0), (0, -750), (0, 750), (-10, -750), (-10, 750), (10, -750), (10, 750)],
)
@pytest.mark.parametrize("size", [1, 64, 256, 1024])
def test_independent_clock_and_carrier_errors_with_noise(rate, ppm, cfo, size):
    payload = bytes(np.random.default_rng(1101).integers(0, 256, size, dtype=np.uint8))
    wire, iq = reference(payload, rate, ppm, cfo, 37.35, 0.04)
    modem = make_frame_modem(bit_rate=rate, deviation=rate / 2, clock=lambda: 0)
    decoded = modem.feed_timed(np.r_[iq, np.zeros(200, np.complex64)], 1000000000)
    assert [frame for frame, _ in decoded] == [wire]
    assert abs(decoded[0][1] - (1000000000 + 37.35 / 2e6 * 1e9)) <= 500


@pytest.mark.parametrize("rate", [100000, 250000, 500000])
def test_all_integer_sample_phases_and_fractional_start(rate):
    for phase in range(int(2e6 / rate)):
        wire, iq = reference(bytes(range(256)) * 4, rate, -10, 750, phase + 0.35, 0.04)
        modem = make_frame_modem(bit_rate=rate, deviation=rate / 2, clock=lambda: 0)
        assert list(modem.feed(np.r_[iq, np.zeros(200, np.complex64)])) == [wire]


@pytest.mark.parametrize("rate", [100000, 250000, 500000])
def test_arbitrary_block_boundaries_and_discontinuity(rate):
    wire, iq = reference(bytes(range(256)) * 4, rate, 10, -750, 37.35, 0.04)
    iq = np.r_[iq, np.zeros(200, np.complex64)]
    modem = make_frame_modem(bit_rate=rate, deviation=rate / 2, clock=lambda: 0)
    decoded = []
    cursor = 0
    rng = np.random.default_rng(7)
    while cursor < len(iq):
        end = min(len(iq), cursor + int(rng.integers(1000, 12000)))
        decoded += modem.feed_timed(iq[cursor:end], 1000000000 + cursor * 500)
        cursor = end
    assert [frame for frame, _ in decoded] == [wire]
    assert abs(decoded[0][1] - (1000000000 + 37.35 * 500)) <= 500
    modem.reset()
    assert not modem.feed_timed(iq[:1000], 0)
    assert [frame for frame, _ in modem.feed_timed(iq, 2000000000)] == [wire]


@pytest.mark.parametrize("rate", [100, 500])
def test_rate_profiles_validate_as_pairs_and_reject_nonintegral_override(rate):
    configs = {
        peer: load_config(f"configs/v1_1/peer-{peer}-simple-{rate}k.yaml") for peer in ("a", "b")
    }
    validate_network(configs)
    cfg = configs["a"]
    assert cfg.sdr.bit_rate_bps == rate * 1000
    assert cfg.sdr.samples_per_symbol == 2000 // rate
    from packet_radio.profiles import definition_fields

    definition = deepcopy(cfg.emulation)
    definition["waveform"]["bit_rate_bps"] = 19200
    with pytest.raises(ValueError, match="integral"):
        definition_fields(normalize_definition(definition))
