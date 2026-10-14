"""Determine the current frequency and guard interval from a shared schedule.

FrequencyScheduler maps time to a configured channel and slot, reports when a
frequency change is due, and orders channels for synchronization recovery.
synchronize applies a leader clock offset. FrequencyState carries the resulting
frequency, slot and transmit permission for the application.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from packet_radio.config import FrequencyConfig


@dataclass(frozen=True)
class FrequencyState:
    index: int
    frequency_hz: float
    slot: int
    transmit_allowed: bool
    seconds_to_change: float


class FrequencyScheduler:
    """Deterministic UTC/POSIX-time frequency schedule shared by every node."""

    def __init__(self, config: FrequencyConfig, clock=time.time):
        self.config = config
        self.clock = clock
        self.clock_offset = 0.0
        count = len(config.frequencies_hz)
        self.sequence_indices = tuple(range(count))
        if config.sequence_mode == "ping_pong" and count > 2:
            self.sequence_indices += tuple(range(count - 2, 0, -1))

    def state(self, now: float | None = None) -> FrequencyState:
        now = (self.clock() if now is None else now) + self.clock_offset
        elapsed = now - self.config.epoch
        slot = int(elapsed // self.config.interval_seconds)
        offset = elapsed % self.config.interval_seconds
        index = self.sequence_indices[slot % len(self.sequence_indices)]
        distance = min(offset, self.config.interval_seconds - offset)
        return FrequencyState(
            index=index,
            frequency_hz=self.config.frequencies_hz[index],
            slot=slot,
            transmit_allowed=distance >= self.config.guard_seconds,
            seconds_to_change=self.config.interval_seconds - offset,
        )

    def scan_order(self, now: float | None = None) -> list[float]:
        """Likely current channel first, then all channels for clock-loss recovery."""
        current = self.state(now).index
        frequencies = self.config.frequencies_hz
        return [
            frequencies[(current + offset) % len(frequencies)] for offset in range(len(frequencies))
        ]

    def synchronize(self, leader_time: float, received_at: float | None = None) -> float:
        """Apply a leader clock estimate; callers should only trust authenticated peers."""
        received_at = self.clock() if received_at is None else received_at
        self.clock_offset = leader_time - received_at
        return self.clock_offset
