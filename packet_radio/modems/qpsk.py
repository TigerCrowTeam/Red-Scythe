"""Convert packet bytes to and from quadrature phase-shift keying (QPSK).

QPSKModem maps pairs of bits onto complex symbols and recovers packets using
preamble acquisition, symbol timing and carrier tracking. It shares protection,
buffering and timestamp handling with StreamingModem. QPSK remains supported
for regression coverage alongside the BPSK, 2FSK, MSK and GMSK release focus.
"""

from __future__ import annotations

import numpy as np
from reedsolo import ReedSolomonError

from .base import StreamingModem
from .burst import QPSK_TRAINING
from .fec import UncorrectableFECError

# Arrival, score and the alignment-specific views needed to decode a candidate.
_Candidate = tuple[int, float, int, int, np.ndarray]


class QPSKModem(StreamingModem):
    """Gray-coded coherent QPSK modem with its own acquisition training word."""

    training = QPSK_TRAINING
    length_aware_live_search = True

    # A coarse fourth-power estimate can be biased by the B205's analog-shaped
    # rectangular transitions. The full-preamble phase fit below can recover a
    # rotating candidate, so admit lower correlations while the noise-like
    # training word and candidate cap prevent an unbounded false search.
    _PREAMBLE_THRESHOLD = 0.35
    _MAX_ACQUISITION_CANDIDATES = 8

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._training_bits = np.unpackbits(np.frombuffer(self.envelope.training, dtype=np.uint8))
        self._preamble_bits = np.concatenate((self._training_bits, self._sync_bits))
        self._preamble_symbols = self._map_bits(self._preamble_bits)
        self._preamble_conjugate = np.conj(self._preamble_symbols)
        self._preamble_energy = float(np.vdot(self._preamble_symbols, self._preamble_symbols).real)
        self._preamble_index = np.arange(len(self._preamble_symbols), dtype=np.float64)

    @staticmethod
    def _map_bits(bits: np.ndarray) -> np.ndarray:
        pairs = bits.astype(np.uint8).reshape(-1, 2)
        # Gray order around the constellation: 00, 01, 11, 10.
        real = 1.0 - 2.0 * pairs[:, 1].astype(np.float32)
        imag = 1.0 - 2.0 * pairs[:, 0].astype(np.float32)
        return ((real + 1j * imag) / np.sqrt(2.0)).astype(np.complex64)

    def sample_count(self, payload_size: int) -> int:
        base = self.frame_size(payload_size) * 4 * self.sps
        return base + (len(self._rrc_taps) - 1 if self._rrc_taps is not None else 0)

    @staticmethod
    def _demap_symbols(symbols: np.ndarray) -> np.ndarray:
        bits = np.empty(len(symbols) * 2, dtype=np.uint8)
        bits[0::2] = np.imag(symbols) < 0
        bits[1::2] = np.real(symbols) < 0
        return bits

    def modulate(self, payload: bytes) -> np.ndarray:
        if len(payload) > self.max_frame:
            raise ValueError("frame exceeds modem limit")
        wire = self.processing.encode(payload)
        bits = np.unpackbits(np.frombuffer(wire, dtype=np.uint8))
        if self.bit_repetitions > 1:
            bits = np.repeat(bits, self.bit_repetitions)
        symbols = self._map_bits(bits)
        if self._rrc_taps is None:
            samples = np.repeat(symbols, self.sps).astype(np.complex64)
        else:
            impulses = np.zeros(len(symbols) * self.sps, dtype=np.complex64)
            impulses[:: self.sps] = symbols
            samples = np.convolve(impulses, self._rrc_taps, mode="full").astype(np.complex64)
            peak = float(np.max(np.abs(samples)))
            if peak:
                samples /= peak
        if self.carrier_offset_hz:
            phase = 2 * np.pi * self.carrier_offset_hz * np.arange(len(samples)) / self.sample_rate
            samples *= np.exp(1j * phase).astype(np.complex64)
        return samples

    @staticmethod
    def _decision_directed_track(symbols: np.ndarray, start: int) -> np.ndarray:
        """Track residual payload phase/frequency after open-loop acquisition."""
        tracked = symbols.copy()
        phase = 0.0
        frequency = 0.0
        for index in range(max(0, start), len(tracked)):
            value = tracked[index] * np.exp(-1j * phase)
            reference = complex(
                1.0 if value.real >= 0 else -1.0,
                1.0 if value.imag >= 0 else -1.0,
            ) / np.sqrt(2.0)
            error = float(np.angle(value * np.conj(reference)))
            frequency += 0.002 * error
            phase += frequency + 0.08 * error
            tracked[index] = value
        return tracked

    def _find_frame(self) -> tuple[bytes | None, int] | None:
        preamble_symbols = self._preamble_symbols
        minimum_symbols = len(preamble_symbols) + 8
        if len(self._samples) < minimum_symbols * self.sps:
            return None

        # Fourth power removes QPSK data and resolves coarse carrier frequency.
        fourth = self._samples**4
        # Compare points one symbol apart. Adjacent shaped samples include the
        # deterministic RRC transition trajectory, which biases the fourth-power
        # estimate even when carrier offset is zero.
        frequency_correlation = np.sum(fourth[self.sps :] * np.conj(fourth[: -self.sps]))
        phase_step = (
            np.angle(frequency_correlation) / (4.0 * self.sps) if frequency_correlation else 0.0
        )
        sample_index = np.arange(len(self._samples), dtype=np.float64)
        corrected = self._samples * np.exp(-1j * phase_step * sample_index)
        matched = (
            np.convolve(corrected, self._rrc_taps, mode="same")
            if self._rrc_taps is not None
            else corrected
        )
        # Compute overlapping rectangular symbol averages once for all timing
        # offsets. Double precision keeps subtraction accurate after long bursts.
        averaged = None
        if self._rrc_taps is None:
            cumulative = np.empty(len(matched) + 1, dtype=np.complex128)
            cumulative[0] = 0
            np.cumsum(matched, out=cumulative[1:])
            averaged = ((cumulative[self.sps :] - cumulative[: -self.sps]) / self.sps).astype(
                np.complex64
            )

        hypotheses: list[_Candidate] = []
        rejected: tuple[bytes, int, float, int, float] | None = None
        failed: tuple[float, float, int] | None = None
        incomplete_score = float("-inf")
        for timing in range(self.sps):
            usable = (len(matched) - timing) // self.sps
            if usable < minimum_symbols:
                continue
            if self._rrc_taps is None:
                assert averaged is not None
                symbols = averaged[timing : timing + usable * self.sps : self.sps]
            else:
                symbols = matched[timing : timing + usable * self.sps : self.sps].astype(
                    np.complex64
                )
            correlation = np.correlate(symbols, preamble_symbols, mode="valid")
            # A rolling energy sum is linear in the buffer size, independent
            # of preamble length; retain every timing offset and candidate.
            cumulative_energy = np.empty(len(symbols) + 1, dtype=np.float64)
            cumulative_energy[0] = 0
            np.cumsum(np.abs(symbols) ** 2, out=cumulative_energy[1:])
            energy = (
                cumulative_energy[len(preamble_symbols) :]
                - cumulative_energy[: -len(preamble_symbols)]
            )
            denominator = np.sqrt(np.maximum(energy, 0) * self._preamble_energy)
            scores = np.divide(
                np.abs(correlation),
                denominator,
                out=np.zeros_like(denominator, dtype=np.float32),
                where=denominator > 1e-9,
            )
            starts = np.flatnonzero(scores >= self._PREAMBLE_THRESHOLD)
            starts = starts[np.argsort(scores[starts])[::-1]]
            starts = starts[: self._MAX_ACQUISITION_CANDIDATES]
            for start_raw in starts:
                start = int(start_raw)
                hypotheses.append(
                    (timing + start * self.sps, float(scores[start]), timing, start, symbols)
                )
        # Rank alignment quality only within a burst. Consuming a later,
        # higher-scoring frame first would silently discard an earlier ACK.
        hypotheses.sort(key=lambda item: item[0])
        groups: list[list[_Candidate]] = []
        for hypothesis in hypotheses:
            if not groups or hypothesis[0] - groups[-1][0][0] > 2 * self.sps:
                groups.append([])
            groups[-1].append(hypothesis)
        for group in groups:
            for _, score, timing, start, symbols in sorted(
                group, key=lambda item: item[1], reverse=True
            ):
                observed = symbols[start : start + len(preamble_symbols)]
                aligned = observed * self._preamble_conjugate
                aligned_index = self._preamble_index
                aligned_phase = np.unwrap(np.angle(aligned)).astype(np.float64)
                weights = np.abs(aligned).astype(np.float64) ** 2
                weight_sum = float(np.sum(weights))
                drift = 0.0
                phase0 = float(np.angle(np.mean(aligned)))
                if weight_sum > 1e-12 and len(aligned) > 1:
                    index_mean = float(np.sum(weights * aligned_index) / weight_sum)
                    phase_mean = float(np.sum(weights * aligned_phase) / weight_sum)
                    centered = aligned_index - index_mean
                    denominator = float(np.sum(weights * centered * centered))
                    if denominator > 1e-12:
                        drift = float(
                            np.sum(weights * centered * (aligned_phase - phase_mean)) / denominator
                        )
                        phase0 = phase_mean - drift * index_mean
                symbol_index = np.arange(len(symbols), dtype=np.float64)
                phase = phase0 + drift * (symbol_index - start)
                decided = symbols * np.exp(-1j * phase).astype(np.complex64)
                corrected_preamble = decided[start : start + len(preamble_symbols)]
                corrected_energy = float(np.sum(np.abs(corrected_preamble) ** 2))
                corrected_score = (
                    abs(np.vdot(preamble_symbols, corrected_preamble))
                    / np.sqrt(corrected_energy * len(preamble_symbols))
                    if corrected_energy > 1e-12
                    else 0.0
                )
                # Reject startup impulses and other false coarse candidates
                # before treating their random length/data decisions as an FEC
                # failure. Real B205 bursts from the permissive coarse search
                # become strongly coherent after the full-preamble phase fit.
                if corrected_score < 0.8:
                    continue
                data_symbol_start = start + len(preamble_symbols)
                # The Python carrier loop is a fallback. Avoid running it (and
                # holding the GIL during live streaming) when acquisition's
                # phase/frequency fit already decodes the packet correctly.
                for track in (False, True):
                    candidate = (
                        self._decision_directed_track(
                            decided[: data_symbol_start + self.length_bits // 2], data_symbol_start
                        )
                        if track
                        else decided
                    )
                    bits = self._demap_symbols(candidate)
                    data_bit_start = data_symbol_start * 2
                    if len(bits) < data_bit_start + self.length_bits:
                        self._await_frame_samples(
                            timing + ((data_bit_start + self.length_bits + 1) // 2) * self.sps
                        )
                        incomplete_score = max(incomplete_score, score)
                        continue
                    length_field = np.packbits(
                        bits[data_bit_start : data_bit_start + self.length_bits]
                    ).tobytes()
                    try:
                        length = self.envelope.body_size(length_field)
                    except ValueError:
                        continue
                    maximum = self.protection.encoded_size(self.max_frame)
                    if length > maximum:
                        continue
                    end_bit = data_bit_start + self.length_bits + length * 8
                    if len(decided) * 2 < end_bit:
                        self._await_frame_samples(timing + ((end_bit + 1) // 2) * self.sps)
                        incomplete_score = max(incomplete_score, score)
                        continue
                    end_symbol = (end_bit + 1) // 2
                    if track:
                        # Check the tracked length field first; repeatedly
                        # tracking an incomplete payload delays streaming I/O.
                        bits = self._demap_symbols(
                            self._decision_directed_track(decided[:end_symbol], data_symbol_start)
                        )
                    consumed = int(np.ceil(timing + end_symbol * self.sps))
                    encoded = np.packbits(
                        bits[data_bit_start + self.length_bits : end_bit]
                    ).tobytes()
                    # Full-body tracking may refine the length decisions too.
                    # Restore the field from the same final decisions as the CRC body.
                    length_field = np.packbits(
                        bits[data_bit_start : data_bit_start + self.length_bits]
                    ).tobytes()
                    try:
                        result = self.processing.decode_body(length_field, encoded)
                    except (ValueError, UncorrectableFECError, ReedSolomonError):
                        if failed is None or corrected_score > failed[0]:
                            failed = (float(corrected_score), score, consumed)
                        continue
                    if self.candidate_validator is not None and not self.candidate_validator(
                        result.payload
                    ):
                        if rejected is None or score > rejected[4]:
                            rejected = (
                                result.payload,
                                consumed,
                                timing + start * self.sps,
                                result.corrected_bits,
                                score,
                            )
                        continue
                    self.fec_corrected_bits += result.corrected_bits
                    self._last_frame_start_sample = timing + start * self.sps
                    return result.payload, consumed
        if rejected is not None and rejected[4] >= incomplete_score:
            self.fec_corrected_bits += rejected[3]
            self._last_frame_start_sample = rejected[2]
            return rejected[0], rejected[1]
        if failed is not None and failed[1] >= incomplete_score:
            self.fec_uncorrectable_frames += 1
            self.last_fec_failure_corrected_score = failed[0]
            self.last_fec_failure_score = failed[1]
            return None, failed[2]
        return None
