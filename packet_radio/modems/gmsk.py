"""Gaussian MSK bursts with explicit FIR state, delay and full transmit tails.

GMSKModem smooths binary frequency pulses with a unit-gain Gaussian FIR before
phase integration. It derives h=0.5 deviation, resets filter/phase state for each
burst and includes the full convolution tail in airtime. The shared discriminator
receiver uses a shaped preamble template and corrects its arrival-time estimate
for the known group delay. BT and span are restricted to tested profile choices.
"""

import math

import numpy as np

from .burst import QPSK_TRAINING
from .msk import MSKModem


def gaussian_taps(samples_per_symbol: int, bt: float, span_symbols: int) -> np.ndarray:
    """Sample the Gaussian impulse response and normalize its DC gain to one."""
    if bt not in (0.3, 0.5) or type(span_symbols) is not int or span_symbols not in (4, 6):
        raise ValueError("GMSK supports gaussian_bt 0.3/0.5 and gaussian_span_symbols 4/6")
    sigma = math.sqrt(math.log(2)) / (2 * math.pi * bt)
    length = span_symbols * samples_per_symbol
    time = (np.arange(length + 1) - length / 2) / samples_per_symbol
    taps = np.exp(-0.5 * (time / sigma) ** 2)
    return taps / taps.sum()


class GMSKModem(MSKModem):
    """Gaussian frequency shaping and template-scored noncoherent reception."""

    # Balanced training avoids concentrating the burst preamble at carrier DC.
    training = QPSK_TRAINING

    def __init__(self, samples_per_symbol=8, *, gaussian_bt=0.3, gaussian_span_symbols=4, **kwargs):
        super().__init__(samples_per_symbol, **kwargs)
        self.gaussian_bt = gaussian_bt
        self.gaussian_span_symbols = gaussian_span_symbols
        self.gaussian_taps = gaussian_taps(self.sps, gaussian_bt, gaussian_span_symbols)
        self.receive_delay_samples = (len(self.gaussian_taps) - 1) // 2
        signs = self._preamble_bits.astype(float) * 2 - 1
        shaped = np.convolve(np.repeat(signs, self.sps), self.gaussian_taps, mode="full")
        start = self.receive_delay_samples
        self._expected_preamble = (
            shaped[start : start + len(signs) * self.sps].reshape(-1, self.sps).mean(1)
        )
        # Ignore edges influenced by reset state and unknown length-field bits.
        self._training_slice = slice(gaussian_span_symbols, -gaussian_span_symbols)

    def sample_count(self, payload_size: int) -> int:
        return super().sample_count(payload_size) + len(self.gaussian_taps) - 1

    def modulate(self, payload: bytes) -> np.ndarray:
        if len(payload) > self.max_frame:
            raise ValueError("frame exceeds modem limit")
        wire = self.envelope.encode(self.protection.encode(payload))
        signs = np.unpackbits(np.frombuffer(wire, dtype=np.uint8)).astype(float) * 2 - 1
        shaped = np.convolve(np.repeat(signs, self.sps), self.gaussian_taps, mode="full")
        frequencies = shaped * self.frequency_deviation_hz + self.carrier_offset_hz
        phase = np.cumsum(frequencies) * (2 * np.pi / self.sample_rate)
        return np.exp(1j * phase).astype(np.complex64)

    def _fit_preamble(self, training, known):
        expected = self._expected_preamble[self._training_slice] * self.frequency_deviation_hz
        observed = training[self._training_slice]
        centered = expected - expected.mean()
        gain = float(np.dot(observed - observed.mean(), centered) / np.dot(centered, centered))
        midpoint = float(np.mean(observed - gain * expected))
        if not 0.5 < gain < 1.5 or abs(midpoint) > self.frequency_deviation_hz / 2:
            return None
        if np.count_nonzero((observed >= midpoint) != (known[self._training_slice] > 0)) > 24:
            return None
        residual = float(np.mean((observed - midpoint - expected) ** 2))
        return midpoint, residual
