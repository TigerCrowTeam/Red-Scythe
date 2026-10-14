"""Publish radio statistics through logs and a local HTTP service.

Formatting helpers render snapshots as text, JSON or Prometheus metrics.
render_endpoint selects status, metrics and synchronization-health responses.
StatusHTTPServer serves those responses on a background thread; StatusService
controls the optional server and the interval for periodic status logging.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from packet_radio.config import StatusConfig
from packet_radio.statistics import LinkStatistics

LOG = logging.getLogger(__name__)


def format_text(snapshot: dict[str, Any]) -> str:
    frequency = snapshot.get("active_frequency_hz")
    frequency_text = "unknown" if frequency is None else f"{frequency / 1e6:.3f} MHz"
    return (
        f"modulation={snapshot.get('modulation') or 'unknown'} "
        f"bit_rate={snapshot.get('configured_bit_rate_bps')} bps "
        f"delivered={snapshot.get('delivered_payload_bps', 0):.1f} bps "
        f"acquired={snapshot.get('receiver_acquired', False)} "
        f"peer_active={snapshot.get('peer_active', False)} "
        f"delivering={snapshot.get('application_delivering', False)} "
        f"frequency={frequency_text} synchronized={snapshot['synchronized']} "
        f"signal_power={snapshot['estimated_signal_power']:.4g} "
        f"queue={snapshot['queue_depth']} pending_ack={snapshot['awaiting_acknowledgements']} "
        f"tx={snapshot['packets_transmitted']} rx={snapshot['packets_received']} "
        f"retries={snapshot['retries']} crc_failures={snapshot['crc_failures']} "
        f"drops={snapshot['queue_drops']} invalid={snapshot['invalid_packets']} "
        f"rx_stream_errors={snapshot['rx_stream_errors']} "
        f"tx_stream_errors={snapshot['tx_stream_errors']} "
        f"overflows={snapshot['rx_overflows']} underflows={snapshot['tx_underflows']} "
        f"late_tx={snapshot['timed_transmit_late']}"
    )


def format_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


def format_prometheus(snapshot: dict[str, Any], role: str, node_id: int) -> str:
    lines = [
        "# HELP packet_radio_info Static node information.",
        "# TYPE packet_radio_info gauge",
        f'packet_radio_info{{role="{role}",node_id="{node_id}"}} 1',
    ]
    modulation = json.dumps(str(snapshot.get("modulation") or "unknown"))
    lines.extend(
        [
            "# TYPE packet_radio_waveform_info gauge",
            f"packet_radio_waveform_info{{modulation={modulation}}} 1",
        ]
    )
    for name, value in sorted(snapshot.items()):
        if value is None or not isinstance(value, (int, float, bool)):
            continue
        numeric = int(value) if isinstance(value, bool) else value
        metric = f"packet_radio_{name}"
        metric_type = "counter" if name in LinkStatistics.COUNTERS else "gauge"
        lines.append(f"# TYPE {metric} {metric_type}")
        lines.append(f"{metric} {numeric}")
    return "\n".join(lines) + "\n"


def render_endpoint(
    path: str, snapshot: dict[str, Any], role: str, node_id: int
) -> tuple[int, str, str]:
    if path == "/status":
        return 200, "application/json", format_json(snapshot) + "\n"
    if path == "/metrics":
        return (
            200,
            "text/plain; version=0.0.4",
            format_prometheus(snapshot, role, node_id),
        )
    if path == "/healthz":
        code = (
            200
            if (
                snapshot["synchronized"]
                and snapshot.get("radio_ready")
                and snapshot.get("peer_active")
            )
            else 503
        )
        return code, "application/json", format_json({"ok": code == 200}) + "\n"
    return 404, "application/json", '{"error":"not found"}\n'


class StatusHTTPServer:
    def __init__(
        self,
        statistics: LinkStatistics,
        role: str,
        node_id: int,
        host: str,
        port: int,
    ):
        self.statistics = statistics
        self.role = role
        self.node_id = node_id
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                snapshot = owner.statistics.snapshot()
                code, content_type, body = render_endpoint(
                    self.path, snapshot, owner.role, owner.node_id
                )
                owner._respond(self, code, content_type, body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="packet-radio-status",
            daemon=True,
        )

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, code: int, content_type: str, body: str) -> None:
        encoded = body.encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(encoded)))
        handler.end_headers()
        handler.wfile.write(encoded)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        # shutdown() waits for serve_forever(), so it deadlocks if startup
        # failed before the worker began serving.
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


class StatusService:
    def __init__(
        self,
        statistics: LinkStatistics,
        config: StatusConfig,
        role: str,
        node_id: int,
        clock=time.monotonic,
    ):
        self.statistics = statistics
        self.config = config
        self.clock = clock
        self.next_report = clock() + config.interval_seconds
        self.http = (
            StatusHTTPServer(statistics, role, node_id, config.http_host, config.http_port)
            if config.http_enabled
            else None
        )

    def start(self) -> None:
        if self.http:
            self.http.start()
            LOG.info("status HTTP listening on %s:%d", self.config.http_host, self.http.port)

    def maybe_report(self) -> None:
        if self.config.output == "off" or self.clock() < self.next_report:
            return
        snapshot = self.statistics.snapshot()
        output = format_json(snapshot) if self.config.output == "json" else format_text(snapshot)
        LOG.info("status: %s", output)
        self.next_report = self.clock() + self.config.interval_seconds

    def close(self) -> None:
        if self.http:
            self.http.close()
