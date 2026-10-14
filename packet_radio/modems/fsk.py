"""Implement continuous-phase binary FSK with rectangular frequency pulses.

FSK2Modem turns protected packet bits into two frequency tones at the configured
deviation. Its receiver uses phase differences to estimate instantaneous
frequency, searches symbol timing and preambles, and decodes candidate packets.
StreamingModem provides receive buffering, protection and arrival timestamps.
"""

import math

import numpy as np
from reedsolo import ReedSolomonError

from packet_radio.modems.fec import UncorrectableFECError

from .base import StreamingModem


class FSK2Modem(StreamingModem):
    """Reuse bounded IQ buffering/timestamps and protection, with an FSK detector."""

    receive_delay_samples = 0

    def __init__(self, samples_per_symbol=8, *, frequency_deviation_hz, **kwargs):
        super().__init__(samples_per_symbol, **kwargs)
        if self.sps < 4 or self.bit_repetitions != 1 or self.rrc_rolloff:
            raise ValueError("2FSK requires >=4 samples/symbol, no repetition or RRC shaping")
        if not math.isfinite(frequency_deviation_hz) or not (
            0 < frequency_deviation_hz < self.sample_rate / 4
        ):
            raise ValueError("2FSK frequency_deviation_hz must be positive and below sample_rate/4")
        self.frequency_deviation_hz = frequency_deviation_hz

    def modulate(self, payload: bytes) -> np.ndarray:
        if len(payload) > self.max_frame:
            raise ValueError("frame exceeds modem limit")
        wire = self.envelope.encode(self.protection.encode(payload))
        bits = np.unpackbits(np.frombuffer(wire, dtype=np.uint8))
        tones = (bits.astype(np.float64) * 2 - 1) * self.frequency_deviation_hz
        increments = np.repeat(tones + self.carrier_offset_hz, self.sps)
        phase = np.cumsum(increments) * (2 * np.pi / self.sample_rate)
        return np.exp(1j * phase).astype(np.complex64)

    def _find_frame(self) -> tuple[bytes | None, int] | None:
        preamble = self._preamble_bits
        minimum = len(preamble) + 16
        if len(self._samples) < minimum * self.sps:
            return None
        # A phase difference belongs to the later sample. Preserve sample indices
        # for accurate arrival timestamps and for arbitrary RX block boundaries.
        frequencies = np.empty(len(self._samples), dtype=np.float32)
        frequencies[1:] = np.angle(self._samples[1:] * self._samples[:-1].conj()) * (
            self.sample_rate / (2 * np.pi)
        )
        frequencies[0] = frequencies[1]
        known = preamble.astype(np.float32) * 2 - 1
        sync = self._sync_bits.astype(np.int16) * 2 - 1
        candidates = []
        for timing in range(self.sps):
            count = (len(frequencies) - timing) // self.sps
            if count < minimum:
                continue
            symbols = frequencies[timing : timing + count * self.sps].reshape(-1, self.sps).mean(1)
            bits = (symbols >= 0).astype(np.int16) * 2 - 1
            scores = np.correlate(bits, sync, "valid")
            starts = np.flatnonzero(scores >= len(sync) - 8) - len(self._training_bits)
            for start in starts:
                if start < 0:
                    continue
                training = symbols[start : start + len(preamble)]
                fit = self._fit_preamble(training, known)
                if fit is None:
                    continue
                midpoint, residual = fit
                candidates.append(
                    (
                        int(start) * self.sps + timing,
                        residual,
                        timing,
                        int(start),
                        midpoint,
                        symbols,
                    )
                )
        # Group neighboring timing hypotheses for a burst, process bursts in time
        # order, and prefer the alignment closest to the expected two tones.
        candidates.sort(key=lambda c: c[0])
        groups: list[list[tuple[int, float, int, int, float, np.ndarray]]] = []
        for candidate in candidates:
            if not groups or candidate[0] - groups[-1][0][0] > self.sps * 2:
                groups.append([])
            groups[-1].append(candidate)
        for group in groups:
            rejected = None
            failed = None
            incomplete = False
            for frame_start, _score, timing, start, midpoint, symbols in sorted(
                group, key=lambda c: c[1]
            ):
                bits = (symbols >= midpoint).astype(np.uint8)
                length_start = start + len(preamble)
                if len(bits) < length_start + 16:
                    incomplete = True
                    continue
                length = int.from_bytes(
                    np.packbits(bits[length_start : length_start + 16]).tobytes(), "big"
                )
                if not 0 < length <= self.protection.encoded_size(self.max_frame):
                    continue
                end = length_start + 16 + length * 8
                if len(bits) < end:
                    incomplete = True
                    continue
                consumed = timing + end * self.sps + self.receive_delay_samples
                if consumed > len(self._samples):
                    # The best complete payload candidate still needs its shaping
                    # tail. Do not accept an earlier, worse timing hypothesis just
                    # because its estimated end falls inside this receive block.
                    return None
                encoded = np.packbits(bits[length_start + 16 : end]).tobytes()
                try:
                    result = self.protection.decode(encoded)
                except (UncorrectableFECError, ReedSolomonError):
                    failed = consumed
                    continue
                if self.candidate_validator is not None and not self.candidate_validator(
                    result.payload
                ):
                    if rejected is None:
                        rejected = (result, consumed, frame_start)
                    continue
                self.fec_corrected_bits += result.corrected_bits
                self._last_frame_start_sample = frame_start - self.receive_delay_samples
                return result.payload, consumed
            if incomplete:
                return None
            if rejected is not None:
                result, consumed, frame_start = rejected
                self.fec_corrected_bits += result.corrected_bits
                self._last_frame_start_sample = frame_start - self.receive_delay_samples
                return result.payload, consumed
            if failed is not None:
                self.fec_uncorrectable_frames += 1
                return None, failed
        return None

    def _fit_preamble(self, training, known):
        """Estimate frequency midpoint and timing score for rectangular pulses."""
        low = float(np.mean(training[known < 0]))
        high = float(np.mean(training[known > 0]))
        deviation = (high - low) / 2
        midpoint = (high + low) / 2
        if not 0.45 * self.frequency_deviation_hz < deviation < 1.5 * self.frequency_deviation_hz:
            return None
        if abs(midpoint) > self.frequency_deviation_hz / 2:
            return None
        errors = np.count_nonzero((training >= midpoint) != self._preamble_bits)
        if errors > 24:
            return None
        residual = float(np.mean((training - midpoint - known * self.frequency_deviation_hz) ** 2))
        return midpoint, residual
