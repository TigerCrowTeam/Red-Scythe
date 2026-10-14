"""Define the modem interface and shared streaming receive machinery.

Modem specifies the conversion between packet bytes and complex IQ samples.
StreamingModem supplies packet protection, burst sizing, bounded receive
buffering, timestamp tracking and state reset for the waveform subclasses.
The root-raised-cosine helper creates optional pulse-shaping filter taps.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable

import numpy as np

from .burst import SYNC, TRAINING, BurstEnvelope, PacketProtection


def _root_raised_cosine(sps: int, rolloff: float, span_symbols: int) -> np.ndarray:
    """Return unit-energy odd-length RRC taps sampled at ``sps``."""
    count = span_symbols * sps
    t = np.arange(-count // 2, count // 2 + 1, dtype=np.float64) / sps
    taps = np.empty_like(t)
    for index, value in enumerate(t):
        if abs(value) < 1e-12:
            taps[index] = 1.0 + rolloff * (4.0 / np.pi - 1.0)
        elif abs(abs(value) - 1.0 / (4.0 * rolloff)) < 1e-12:
            taps[index] = (rolloff / np.sqrt(2.0)) * (
                (1.0 + 2.0 / np.pi) * np.sin(np.pi / (4.0 * rolloff))
                + (1.0 - 2.0 / np.pi) * np.cos(np.pi / (4.0 * rolloff))
            )
        else:
            numerator = np.sin(np.pi * value * (1.0 - rolloff)) + 4.0 * rolloff * value * np.cos(
                np.pi * value * (1.0 + rolloff)
            )
            denominator = np.pi * value * (1.0 - (4.0 * rolloff * value) ** 2)
            taps[index] = numerator / denominator
    taps /= np.sqrt(np.sum(taps * taps))
    return taps.astype(np.float32)


class Modem(ABC):
    candidate_validator: Callable[[bytes], bool] | None
    fec_corrected_bits: int
    fec_uncorrectable_frames: int
    last_fec_failure_score: float | None
    last_fec_failure_corrected_score: float | None

    @abstractmethod
    def reset(self) -> None: ...

    @abstractmethod
    def frame_size(self, payload_size: int) -> int: ...

    @abstractmethod
    def sample_count(self, payload_size: int) -> int: ...

    @abstractmethod
    def feed_timed(
        self, samples: np.ndarray, time_ns: int | None
    ) -> list[tuple[bytes, int | None]]: ...

    @abstractmethod
    def modulate(self, payload: bytes) -> np.ndarray: ...

    @abstractmethod
    def feed(self, samples: np.ndarray) -> Iterable[bytes]: ...


class StreamingModem(Modem):
    """Shared protection, bounded IQ buffering, timestamps and symbol pulse setup."""

    training = TRAINING

    def __init__(
        self,
        samples_per_symbol: int = 8,
        max_frame: int = 8192,
        sample_rate: float = 1_000_000,
        carrier_offset_hz: float = 0.0,
        bit_repetitions: int = 1,
        interleaver_depth: int = 16,
        rs_parity_bytes: int = 0,
        rrc_rolloff: float = 0.0,
        rrc_span_symbols: int = 8,
        timing_subsamples: int = 1,
    ):
        if samples_per_symbol < 2:
            raise ValueError("samples_per_symbol must be at least 2")
        if bit_repetitions < 1:
            raise ValueError("bit_repetitions must be at least 1")
        if interleaver_depth < 1:
            raise ValueError("interleaver_depth must be positive")
        self.candidate_validator: Callable[[bytes], bool] | None = None
        self.sps = samples_per_symbol
        self.max_frame = max_frame
        self.sample_rate = sample_rate
        self.carrier_offset_hz = carrier_offset_hz
        self.bit_repetitions = bit_repetitions
        self.interleaver_depth = interleaver_depth
        if rs_parity_bytes < 0 or rs_parity_bytes % 2:
            raise ValueError("rs_parity_bytes must be a non-negative even value")
        self.rs_parity_bytes = rs_parity_bytes
        if not 0.0 <= rrc_rolloff <= 1.0:
            raise ValueError("rrc_rolloff must be between 0 and 1")
        if rrc_rolloff and (rrc_span_symbols < 2 or rrc_span_symbols % 2):
            raise ValueError("rrc_span_symbols must be a positive even value")
        self.rrc_rolloff = rrc_rolloff
        self.rrc_span_symbols = rrc_span_symbols
        if timing_subsamples < 1:
            raise ValueError("timing_subsamples must be positive")
        self.timing_subsamples = timing_subsamples
        self._rrc_taps = (
            _root_raised_cosine(self.sps, rrc_rolloff, rrc_span_symbols) if rrc_rolloff else None
        )
        self.protection = PacketProtection(interleaver_depth, rs_parity_bytes)
        self.envelope = BurstEnvelope(self.training)
        self._samples = np.empty(0, dtype=np.complex64)
        self._sync_bits = np.unpackbits(np.frombuffer(SYNC, dtype=np.uint8))
        self._training_bits = np.unpackbits(np.frombuffer(self.training, dtype=np.uint8))
        self._preamble_bits = np.concatenate((self._training_bits, self._sync_bits))
        self.fec_corrected_bits = 0
        self.fec_uncorrectable_frames = 0
        self._noise_power: float | None = None
        self._burst_active = False
        self._live_block_size = 0
        self._buffer_time_ns: int | None = None
        self._last_frame_start_sample: float | None = None
        self._decoded_arrival_times: list[int | None] = []
        self.last_fec_failure_score: float | None = None
        self.last_fec_failure_corrected_score: float | None = None

    def frame_size(self, payload_size: int) -> int:
        """Return on-air envelope bytes before conversion to waveform samples."""
        return (
            self.envelope.encoded_size(self.protection.encoded_size(payload_size))
            * self.bit_repetitions
        )

    def reset(self) -> None:
        """Discard acquisition/history after a discontinuity; keep cumulative FEC counters."""
        self._samples = np.empty(0, dtype=np.complex64)
        self._noise_power = None
        self._burst_active = False
        self._live_block_size = 0
        self._buffer_time_ns = None
        self._last_frame_start_sample = None
        self._decoded_arrival_times = []
        self.last_fec_failure_score = None
        self.last_fec_failure_corrected_score = None

    def sample_count(self, payload_size: int) -> int:
        base = self.frame_size(payload_size) * 8 * self.sps
        return base + (len(self._rrc_taps) - 1 if self._rrc_taps is not None else 0)

    def feed(self, samples: np.ndarray) -> Iterable[bytes]:
        return [payload for payload, _arrival in self.feed_timed(samples, None)]

    def feed_timed(
        self, samples: np.ndarray, time_ns: int | None
    ) -> list[tuple[bytes, int | None]]:
        incoming = np.asarray(samples, dtype=np.complex64)
        if not len(incoming):
            return []
        if time_ns is not None:
            if self._buffer_time_ns is None or not len(self._samples):
                self._buffer_time_ns = int(time_ns)
            else:
                expected = self._buffer_time_ns + int(len(self._samples) / self.sample_rate * 1e9)
                tolerance = max(1, int(2 / self.sample_rate * 1e9))
                if abs(int(time_ns) - expected) > tolerance:
                    # Never correlate across a timed RX discontinuity.
                    self._samples = np.empty(0, dtype=np.complex64)
                    self._buffer_time_ns = int(time_ns)
                    self._burst_active = False
        self._decoded_arrival_times = []
        power = float(np.mean(np.abs(incoming) ** 2))
        minimum_samples = (len(SYNC) + 2) * 8 * self.sps
        idle = (
            self._noise_power is not None
            # Unit-amplitude simulation/replay streams contain no idle gap;
            # energy gating is only needed for low-level live SDR samples.
            and self._noise_power < 1e-4
            and power <= max(self._noise_power * 3.0, self._noise_power + 1e-12)
        )
        if idle and not self._burst_active and len(self._samples) >= minimum_samples:
            assert self._noise_power is not None
            # Searching an ever-growing idle-noise buffer on every SDR read is
            # quadratic and eventually causes hardware RX overflows. Keep two
            # read-sized blocks so a burst beginning at a block edge retains
            # its complete preamble, but defer correlation until energy rises.
            keep = max(minimum_samples, len(incoming) * 2)
            self._samples = np.concatenate((self._samples, incoming))
            self._keep_tail(keep)
            self._noise_power = 0.98 * self._noise_power + 0.02 * power
            self._live_block_size = max(self._live_block_size, len(incoming))
            return []
        if idle and not self._burst_active:
            assert self._noise_power is not None
            # There is not enough history to contain a frame yet. Avoid an
            # allocation and frame-search pass for every idle SDR block.
            self._noise_power = 0.98 * self._noise_power + 0.02 * power
            self._live_block_size = max(self._live_block_size, len(incoming))
            if len(self._samples) > minimum_samples:
                self._keep_tail(minimum_samples)
            self._samples = np.concatenate((self._samples, incoming))
            self._keep_tail(minimum_samples)
            return []
        self._samples = np.concatenate((self._samples, incoming))
        if self._noise_power is None:
            self._noise_power = power
        elif idle:
            self._noise_power = 0.98 * self._noise_power + 0.02 * power
        else:
            self._burst_active = True
        # Live SDR streams have a low idle-noise floor and explicit gaps between
        # TDD bursts. Searching every growing partial burst repeats the expensive
        # timing/correlation work and can starve the SDR receive stream. Accumulate
        # the burst and search once when the following idle block arrives. Unit-
        # amplitude simulation/replay streams retain immediate streaming decode.
        live_burst = (
            self._noise_power is not None
            and self._noise_power < 1e-4
            and self._burst_active
            and not idle
            and len(incoming) >= self._live_block_size
        )
        if live_burst:
            maximum = self.sample_count(self.max_frame)
            if len(self._samples) > maximum:
                self._keep_tail(maximum)
            return []
        frames: list[bytes] = []
        while True:
            found = self._find_frame()
            if found is None:
                # Bound memory while retaining enough history for the largest frame.
                maximum = self.sample_count(self.max_frame)
                if len(self._samples) > maximum:
                    self._keep_tail(maximum)
                break
            payload, consumed = found
            if payload is not None:
                frames.append(payload)
                arrival = None
                if self._buffer_time_ns is not None and self._last_frame_start_sample is not None:
                    arrival = self._buffer_time_ns + int(
                        self._last_frame_start_sample / self.sample_rate * 1e9
                    )
                self._decoded_arrival_times.append(arrival)
            self._consume(consumed)
        if idle and self._burst_active:
            self._burst_active = False
            keep = max(minimum_samples, len(incoming) * 2)
            self._keep_tail(keep)
        return list(zip(frames, self._decoded_arrival_times, strict=True))

    def _consume(self, count: int) -> None:
        self._samples = self._samples[count:]
        if self._buffer_time_ns is not None:
            self._buffer_time_ns += int(count / self.sample_rate * 1e9)

    def _keep_tail(self, count: int) -> None:
        removed = max(0, len(self._samples) - count)
        if removed:
            self._consume(removed)

    @abstractmethod
    def _find_frame(self) -> tuple[bytes | None, int] | None:
        """Find a packet and consumed sample count in the current IQ buffer."""
        ...
