"""Convert packet bytes to and from binary phase-shift keying (BPSK).

BPSKModem builds protected transmit bursts and searches incoming IQ for their
preambles, symbol timing and carrier phase. Its receiver recovers candidate
packet bytes through the shared protection layer. StreamingModem supplies
buffering and timestamps across successive receive blocks.
"""

from __future__ import annotations

import numpy as np
from reedsolo import ReedSolomonError

from .base import StreamingModem
from .burst import SYNC
from .fec import UncorrectableFECError

MAX_SYNC_BIT_ERRORS = 4
# Score the complete training + sync sequence when choosing burst alignment.  The
# leading 0x55 bytes in SYNC are deliberately repetitive and are not distinctive
# enough on their own to choose between nearby timing candidates.
# Training is long enough to retain a distinctive score through a short RF fade.
# Permit up to 48 bad bits across its 304 bits while still requiring substantially
# better than random correlation.
MAX_PREAMBLE_BIT_ERRORS = 48


class BPSKModem(StreamingModem):
    """Coherent BPSK generation and burst acquisition."""

    def modulate(self, payload: bytes) -> np.ndarray:
        if len(payload) > self.max_frame:
            raise ValueError("frame exceeds modem limit")
        wire = self.envelope.encode(self.protection.encode(payload))
        bits = np.unpackbits(np.frombuffer(wire, dtype=np.uint8))
        if self.bit_repetitions > 1:
            bits = np.repeat(bits, self.bit_repetitions)
        symbols = bits.astype(np.float32) * 2.0 - 1.0
        if self._rrc_taps is None:
            samples = np.repeat(symbols, self.sps).astype(np.complex64)
        else:
            impulses = np.zeros(len(symbols) * self.sps, dtype=np.float32)
            impulses[:: self.sps] = symbols
            samples = np.convolve(impulses, self._rrc_taps, mode="full").astype(np.complex64)
            peak = float(np.max(np.abs(samples)))
            if peak:
                samples /= peak
        if self.carrier_offset_hz:
            phase = 2 * np.pi * self.carrier_offset_hz * np.arange(len(samples)) / self.sample_rate
            samples *= np.exp(1j * phase).astype(np.complex64)
        return samples

    def _find_frame(self) -> tuple[bytes | None, int] | None:
        minimum_symbols = (len(SYNC) + 2) * 8
        if len(self._samples) < minimum_symbols * self.sps:
            return None

        # Squaring removes BPSK data modulation. The phase progression of x^2
        # estimates twice the carrier offset, allowing coherent hard decisions.
        squared = self._samples * self._samples
        correlation = np.sum(squared[1:] * np.conj(squared[:-1]))
        phase_step = np.angle(correlation) / 2.0 if correlation else 0.0
        n = np.arange(len(self._samples), dtype=np.float64)
        corrected = self._samples * np.exp(-1j * phase_step * n)
        matched = (
            np.convolve(corrected, self._rrc_taps, mode="same")
            if self._rrc_taps is not None
            else corrected
        )

        rejected: tuple[bytes, int, float, int, float] | None = None
        failed_consumed: int | None = None
        failed_score = float("-inf")
        incomplete_score = float("-inf")
        # Rectangular BPSK is optimally detected by integrating each symbol. Test
        # all integer sample phases using vectorized reshape/mean operations. This
        # is both more robust than choosing a single sample and substantially less
        # expensive than interpolating four fractional offsets per sample phase.
        for timing_index in range(self.sps * self.timing_subsamples):
            timing = timing_index / self.timing_subsamples
            usable = int((len(matched) - timing) // self.sps)
            if usable < minimum_symbols:
                continue
            if self.timing_subsamples == 1 and self._rrc_taps is None:
                timing_sample = int(timing)
                sampled = matched[timing_sample : timing_sample + usable * self.sps]
                symbols = sampled.reshape(-1, self.sps).mean(axis=1).astype(np.complex64)
            elif self.timing_subsamples == 1:
                timing_sample = int(timing)
                symbols = matched[
                    timing_sample : timing_sample + usable * self.sps : self.sps
                ].astype(np.complex64)
            else:
                if self._rrc_taps is None:
                    positions = timing + np.arange(usable * self.sps, dtype=np.float64)
                    axis = np.arange(len(matched), dtype=np.float64)
                    sampled = np.interp(positions, axis, matched.real) + 1j * np.interp(
                        positions, axis, matched.imag
                    )
                    symbols = sampled.reshape(-1, self.sps).mean(axis=1).astype(np.complex64)
                else:
                    positions = timing + self.sps * np.arange(usable, dtype=np.float64)
                    axis = np.arange(len(matched), dtype=np.float64)
                    symbols = (
                        np.interp(positions, axis, matched.real)
                        + 1j * np.interp(positions, axis, matched.imag)
                    ).astype(np.complex64)
            # Estimate residual carrier drift on this symbol-aligned candidate.
            # The block-wide estimate above can be biased by idle samples and
            # adjacent bursts; per-frame tracking prevents phase from rotating
            # across long packet bursts.
            if len(symbols) > 2:
                symbol_squared = symbols * symbols
                residual = np.sum(symbol_squared[1:] * np.conj(symbol_squared[:-1]))
                if residual:
                    residual_step = np.angle(residual) / 2.0
                    symbols = symbols * np.exp(
                        -1j * residual_step * np.arange(len(symbols))
                    ).astype(np.complex64)
            symbol_count = len(symbols)
            if self.bit_repetitions > 1:
                usable_bits = symbol_count // self.bit_repetitions
                if usable_bits < minimum_symbols // 1:
                    continue
                symbols = (
                    symbols[: usable_bits * self.bit_repetitions]
                    .reshape(usable_bits, self.bit_repetitions)
                    .mean(axis=1)
                )
            # The squaring estimator leaves a constant phase and a 180-degree
            # ambiguity. Rotate to the dominant BPSK axis and test both polarities.
            axis = np.angle(np.sum(symbols * symbols)) / 2.0
            rotated = np.real(symbols * np.exp(-1j * axis))
            decisions = rotated >= 0
            # A conducted SDR path can introduce a small residual DC offset or
            # asymmetric gain. Use the observed symbol midpoint as an
            # additional soft-decision candidate, while retaining zero-threshold
            # decisions for the normal balanced case.
            midpoint = float(np.median(rotated))
            spread = float(np.std(rotated))
            # A burst can contain a residual DC step after the B205 AGC settles.
            # Try a small set of thresholds around the observed midpoint so a
            # handful of biased symbols do not turn into an entire uncorrectable
            # FEC frame.  These are cheap hard-decision candidates; the normal
            # zero-threshold path remains first for maximum throughput.
            threshold_delta = min(0.25, max(0.02, spread * 0.08))
            adaptive = rotated >= midpoint
            adaptive_low = rotated >= midpoint - threshold_delta
            adaptive_high = rotated >= midpoint + threshold_delta
            variants: list[np.ndarray] = []
            for candidate in (
                decisions,
                ~decisions,
                adaptive,
                ~adaptive,
                adaptive_low,
                ~adaptive_low,
                adaptive_high,
                ~adaptive_high,
            ):
                candidate = candidate.astype(np.uint8)
                if not any(np.array_equal(candidate, prior) for prior in variants):
                    variants.append(candidate)
            for bits in variants:
                bipolar = bits.astype(np.int16) * 2 - 1
                # Use the short distinctive sync as an inexpensive shortlist,
                # then score the complete preamble only at those positions. A
                # full 304-bit convolution for every phase/threshold candidate
                # added excessive work on live receive buffers.
                sync_bipolar = self._sync_bits.astype(np.int16) * 2 - 1
                sync_correlation = np.convolve(bipolar, sync_bipolar[::-1], mode="valid")
                sync_starts = np.flatnonzero(
                    sync_correlation >= len(self._sync_bits) - 2 * MAX_SYNC_BIT_ERRORS
                )
                preamble_bipolar = self._preamble_bits.astype(np.int16) * 2 - 1
                scored: list[tuple[int, int]] = []
                for sync_start in sync_starts:
                    preamble_start = int(sync_start) - len(self._training_bits)
                    if preamble_start < 0:
                        continue
                    observed = bipolar[preamble_start : int(sync_start) + len(self._sync_bits)]
                    if len(observed) == len(preamble_bipolar):
                        scored.append((int(sync_start), int(np.dot(observed, preamble_bipolar))))
                scored.sort(key=lambda item: item[1], reverse=True)
                full = [
                    item
                    for item in scored
                    if item[1] >= len(self._preamble_bits) - 2 * MAX_PREAMBLE_BIT_ERRORS
                ]
                used_full_preamble = bool(full)
                if used_full_preamble:
                    starts = np.asarray([item[0] for item in full], dtype=np.int64)
                    start_scores = np.asarray([item[1] for item in full], dtype=np.int64)
                else:
                    # A fade can damage training while leaving the distinctive
                    # sync suffix usable. Retain a bounded compatibility fallback
                    # so such a detected-but-bad burst is counted for diagnostics.
                    starts = sync_starts
                    starts = starts[np.argsort(sync_correlation[starts])[::-1]]
                    start_scores = sync_correlation[starts]
                for start, candidate_score_raw in zip(starts, start_scores, strict=True):
                    candidate_score = float(candidate_score_raw)
                    candidates = [bits]
                    last_consumed: int | None = None
                    # If the global phase estimate was biased by idle samples,
                    # refine it from the known alternating training immediately
                    # before this sync. Keep the original decisions first: a bad
                    # training alignment must never hide a decodable frame.
                    training_bits = self._training_bits
                    training_start = start - len(training_bits)
                    if training_start >= 0:
                        observed = symbols[training_start:start]
                        known = training_bits.astype(np.float32) * 2.0 - 1.0
                        # A threshold-derived bit variant can contain a candidate
                        # at the final correlation position while the symbol view
                        # is shorter after repetition grouping. The original hard
                        # decisions remain valid; skip only training refinement.
                        gain = (
                            np.vdot(known, observed) / len(known)
                            if len(observed) == len(known)
                            else 0.0
                        )
                        if abs(gain) > 1e-6:
                            refined = np.real(symbols * (np.conj(gain) / abs(gain))) >= 0
                            refined_bits = refined.astype(np.uint8)
                            if not np.array_equal(refined_bits, bits):
                                candidates.append(refined_bits)
                            # Track residual carrier drift from the known
                            # training symbols. Removing the alternating BPSK
                            # pattern first makes adjacent-sample phase change
                            # a direct estimate of frequency error.
                            training_aligned = observed * known
                            drift = 0.0
                            if len(training_aligned) > 1:
                                drift_corr = np.sum(
                                    training_aligned[1:] * np.conj(training_aligned[:-1])
                                )
                                if drift_corr:
                                    drift = float(np.angle(drift_corr))
                            phase0 = float(np.angle(np.mean(training_aligned)))
                            phase_index = np.arange(len(symbols), dtype=np.float64)
                            phase = phase0 + drift * (phase_index - training_start)
                            tracked = symbols * np.exp(-1j * phase).astype(np.complex64)
                            tracked_bits = np.real(tracked) >= 0
                            tracked_bits = tracked_bits.astype(np.uint8)
                            if not np.array_equal(tracked_bits, bits):
                                candidates.append(tracked_bits)
                    for candidate_bits in candidates:
                        length_start = start + len(self._sync_bits)
                        if len(candidate_bits) < length_start + 16:
                            if used_full_preamble:
                                incomplete_score = max(incomplete_score, candidate_score)
                            continue
                        length = int.from_bytes(
                            np.packbits(candidate_bits[length_start : length_start + 16]).tobytes(),
                            "big",
                        )
                        if length > self.protection.encoded_size(self.max_frame):
                            continue
                        end = length_start + 16 + length * 8
                        if len(candidate_bits) < end:
                            if used_full_preamble:
                                incomplete_score = max(incomplete_score, candidate_score)
                            continue
                        consumed = int(np.ceil(timing + end * self.bit_repetitions * self.sps))
                        last_consumed = consumed
                        encoded = np.packbits(candidate_bits[length_start + 16 : end]).tobytes()
                        try:
                            result = self.protection.decode(encoded)
                        except (UncorrectableFECError, ReedSolomonError):
                            continue
                        frame_start = (
                            timing
                            + (int(start) - len(self._training_bits))
                            * self.bit_repetitions
                            * self.sps
                        )
                        if self.candidate_validator is not None and not self.candidate_validator(
                            result.payload
                        ):
                            # SECDED can miscorrect multiple bit errors. Keep searching
                            # other timing/phase candidates before consuming this burst.
                            if rejected is None or candidate_score > rejected[4]:
                                rejected = (
                                    result.payload,
                                    consumed,
                                    frame_start,
                                    result.corrected_bits,
                                    candidate_score,
                                )
                            continue
                        self.fec_corrected_bits += result.corrected_bits
                        self._last_frame_start_sample = (
                            timing
                            + (int(start) - len(self._training_bits))
                            * self.bit_repetitions
                            * self.sps
                        )
                        return result.payload, consumed
                    # A complete candidate was found but neither phase decision
                    # decoded. Remember its boundary, but test every remaining
                    # timing/alignment candidate before declaring the burst bad.
                    if last_consumed is not None:
                        if candidate_score > failed_score:
                            failed_score = candidate_score
                            failed_consumed = last_consumed
        if rejected is not None and rejected[4] >= incomplete_score:
            # Preserve the packet layer's invalid-frame diagnostics when no
            # alternative decodes; a validator is a selection aid, not authentication.
            self.fec_corrected_bits += rejected[3]
            self._last_frame_start_sample = rejected[2]
            return rejected[0], rejected[1]
        if failed_consumed is not None and failed_score >= incomplete_score:
            self.fec_uncorrectable_frames += 1
            return None, failed_consumed
        if incomplete_score > float("-inf"):
            return None
        return None
