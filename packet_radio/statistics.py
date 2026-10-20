"""Collect operational counters and gauges for a radio node.

LinkStatistics synchronizes updates from application and worker threads and
returns JSON-compatible snapshots with counters, gauges and elapsed uptime.
These snapshots supply periodic logs, HTTP status, metrics and test assertions.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from copy import deepcopy
from typing import Any

from packet_radio.diagnostics import TimingDiagnostics


class LinkStatistics:
    """Thread-safe link counters and gauges with JSON-friendly snapshots."""

    COUNTERS = (
        "shutdown_errors",
        "mode_rejections",
        "transmit_drops",
        "control_queue_drops",
        "retry_exhausted",
        "unexpected_peer_packets",
        "wrong_destination_packets",
        "payload_packets_received",
        "payload_bytes_received",
        "payload_packets_delivered",
        "payload_bytes_delivered",
        "invalid_payloads",
        "endpoint_input_errors",
        "endpoint_output_errors",
        "packets_enqueued",
        "packets_transmitted",
        "packets_received",
        "bytes_transmitted",
        "bytes_received",
        "samples_transmitted",
        "airtime_seconds_transmitted",
        "invalid_packets",
        "candidate_rejections",
        "protocol_version_mismatches",
        "authentication_failures",
        "replay_rejections",
        "sync_freshness_rejections",
        "crc_failures",
        "fec_corrected_bits",
        "fec_uncorrectable_frames",
        "retries",
        "acknowledgements_sent",
        "acknowledgements_received",
        "duplicates_suppressed",
        "queue_drops",
        "queue_expired",
        "channel_busy_deferrals",
        "transmit_errors",
        "tx_underflows",
        "tx_stream_errors",
        "tx_timeouts",
        "rx_overflows",
        "rx_timeouts",
        "rx_stream_errors",
        "rx_buffer_drops",
        "rx_buffer_flushes",
        "timed_transmissions",
        "timed_transmit_late",
        "host_tx_late_rejections",
        "tx_lead_rejections",
        "tx_lead_deferrals",
        "uhd_tx_late_events",
        "timed_receive_windows",
        "scheduled_tdd_transmissions",
        "frequency_hops",
        "hop_burst_rejections",
        "hop_slots_missed",
        "hop_clock_reversals",
        "hop_tune_deadlines_missed",
        "tdd_opportunities_missed",
        "recording_failures",
        "synchronization_losses",
        "reacquisitions",
        "decoder_iq_blocks",
        "decoder_iq_samples",
        "decoder_frames",
        "decoder_timestamp_discontinuities",
        "decoder_output_drops",
    )

    def __init__(
        self,
        monotonic_clock=time.monotonic,
        activity_timeout_seconds=15.0,
        *,
        timing_diagnostics=False,
    ):
        self.timing = TimingDiagnostics(timing_diagnostics, monotonic_clock)
        self._clock = monotonic_clock
        self._started_at = monotonic_clock()
        self._lock = threading.Lock()
        self._activity_timeout = activity_timeout_seconds
        self._events: dict[str, float] = {}
        self._radio_info: dict[str, Any] = {}
        self._deliveries: deque[tuple[float, float]] = deque()
        self._rate_window = 5.0
        self._counters: dict[str, int | float] = {name: 0 for name in self.COUNTERS}
        self._gauges: dict[str, str | int | float | bool | None] = {
            "requires_peer": True,
            "operation": "bidirectional",
            "input_kind": "payload",
            "modulation": None,
            "configured_bit_rate_bps": None,
            "configured_center_frequency_hz": None,
            "sample_rate_hz": None,
            "radio_ready": False,
            "queue_depth": 0,
            "queue_airtime_seconds": 0.0,
            "oldest_pending_packet_age_ms": 0.0,
            "control_queue_depth": 0,
            "queue_capacity": 0,
            "awaiting_acknowledgements": 0,
            "estimated_signal_power": 0.0,
            "active_frequency_hz": None,
            "frequency_slot": None,
            "hop_retune_ms": None,
            "transmit_allowed": False,
            "synchronized": True,
            "hardware_clock_offset_ns": None,
            "hardware_clock_drift_ppm": None,
            "hardware_clock_observations": 0,
            "last_fec_failure_score": None,
            "last_fec_failure_corrected_score": None,
            "scheduled_tx_lead_ms": None,
            "scheduled_tx_target_ns": None,
            "decoder_queue_depth": 0,
            "decoder_realtime_utilization": 0.0,
            "decoder_processing_p95_ms": 0.0,
            "decoder_processing_max_ms": 0.0,
        }

    def increment(self, name: str, amount: int | float = 1) -> None:
        with self._lock:
            if name not in self._counters:
                raise KeyError(f"unknown statistics counter: {name}")
            self._counters[name] += amount
            if name == "payload_bytes_delivered" and amount > 0:
                now = self._clock()
                self._events["delivery"] = now
                self._prune_deliveries(now)
                self._deliveries.append((now, float(amount)))

    def set_gauge(self, name: str, value: str | int | float | bool | None) -> None:
        with self._lock:
            if name not in self._gauges:
                raise KeyError(f"unknown statistics gauge: {name}")
            self._gauges[name] = value

    def set_radio_info(self, configuration: dict, hardware: dict) -> None:
        """Keep a detached startup configuration and hardware readback for status."""
        with self._lock:
            self._radio_info = deepcopy({"configuration": configuration, "hardware": hardware})

    def set_runtime_identity(self, identity: dict) -> None:
        with self._lock:
            self._radio_info["runtime"] = deepcopy(identity)

    def set_hardware_readback(self, actual: dict) -> None:
        with self._lock:
            self._radio_info["hardware"] = {"actual_settings": deepcopy(actual)}

    def mark_activity(self, event: str) -> None:
        """Record validated frame acquisition or accepted peer traffic."""
        if event not in ("frame", "peer"):
            raise ValueError(f"unknown activity event: {event}")
        with self._lock:
            self._events[event] = self._clock()

    def _prune_deliveries(self, now: float) -> None:
        while self._deliveries and self._deliveries[0][0] <= now - self._rate_window:
            self._deliveries.popleft()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            uptime = max(0.0, now - self._started_at)
            self._prune_deliveries(now)
            ages = {key: max(0.0, now - value) for key, value in self._events.items()}
            interval = min(uptime, self._rate_window)
            activity = {}
            for event, flag in (
                ("frame", "receiver_acquired"),
                ("peer", "peer_active"),
                ("delivery", "application_delivering"),
            ):
                age = ages.get(event)
                activity[flag] = age is not None and age <= self._activity_timeout
                activity[f"last_{event}_age_seconds"] = age
            return {
                **({"timing_diagnostics": self.timing.snapshot()} if self.timing.enabled else {}),
                **deepcopy(self._radio_info),
                **self._counters,
                **self._gauges,
                **activity,
                "delivered_payload_bps": (
                    sum(size for _, size in self._deliveries) * 8 / interval if interval else 0.0
                ),
                "throughput_window_seconds": min(uptime, self._rate_window),
                "activity_timeout_seconds": self._activity_timeout,
                "uptime_seconds": uptime,
            }
