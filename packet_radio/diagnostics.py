"""Collect optional bounded timing summaries without retaining packet contents.

Durations use a supplied host monotonic clock. Hardware lead times are measured
separately by the UHD adapter in its own clock domain. Summaries retain lifetime
count/mean/extrema and a rolling 256-observation p95, not a packet trace.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any


class TimingDiagnostics:
    def __init__(self, enabled: bool = False, clock=time.monotonic):
        self.enabled = enabled
        self.clock = clock
        self._lock = threading.Lock()
        self._metrics: dict[str, dict[str, Any]] = {}

    def start(self) -> float | None:
        return self.clock() if self.enabled else None

    def finish(self, name: str, started: float | None) -> None:
        if started is not None:
            self.observe(name, (self.clock() - started) * 1000)

    def observe(self, name: str, value: float) -> None:
        if not self.enabled:
            return
        if not math.isfinite(value):
            raise ValueError("diagnostic observations must be finite")
        with self._lock:
            metric = self._metrics.get(name)
            if metric is None:
                metric = {
                    "count": 0,
                    "sum": 0.0,
                    "min": value,
                    "max": value,
                    "recent": deque(maxlen=256),
                }
                self._metrics[name] = metric
            metric["count"] += 1
            metric["sum"] += value
            metric["min"] = min(metric["min"], value)
            metric["max"] = max(metric["max"], value)
            metric["recent"].append(value)

    @contextmanager
    def work(self, name: str):
        """Compare elapsed time with this thread's CPU time; neither is GIL wait."""
        if not self.enabled:
            yield
            return
        started, cpu = self.clock(), time.thread_time()
        try:
            yield
        finally:
            elapsed, used = self.clock() - started, time.thread_time() - cpu
            self.observe(f"{name}_wall_ms", elapsed * 1000)
            self.observe(f"{name}_cpu_ms", used * 1000)

    @contextmanager
    def lock(self, name: str, lock):
        """Measure lock acquisition/hold separately, recording after release."""
        if not self.enabled:
            with lock:
                yield
            return
        started = self.clock()
        lock.acquire()
        acquired = self.clock()
        try:
            yield
        finally:
            finished = self.clock()
            lock.release()
            self.observe(f"{name}_wait_ms", (acquired - started) * 1000)
            self.observe(f"{name}_hold_ms", (finished - acquired) * 1000)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = {}
            for name, metric in self._metrics.items():
                recent = sorted(metric["recent"])
                result[name] = {
                    "count": metric["count"],
                    "mean": metric["sum"] / metric["count"],
                    "min": metric["min"],
                    "max": metric["max"],
                    "recent_count": len(recent),
                    "recent_p95": recent[math.ceil(len(recent) * 0.95) - 1],
                }
            return result
