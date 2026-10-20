"""Bounded asynchronous reads of existing status endpoints for owned radio sessions.

Each node is polled independently at most twice per second. Identity, PID and
advancing uptime checks prevent other or cached sessions from appearing live.
Failures retain historical counters but never label them as current RF activity.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from packet_radio.runtime.deadline import wait_with_timeout
from packet_radio.runtime.identity import config_identity

MAX_BODY = 1024 * 1024


async def fetch_status(host, port):
    """Read one bounded HTTP response; no proxies, redirects or control requests."""
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1" if host == "0.0.0.0" else "::1"
    reader, writer = await asyncio.open_connection(host, port, limit=16384)
    try:
        writer.write(b"GET /status HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        header = await reader.readuntil(b"\r\n\r\n")
        lines = header.decode("ascii").split("\r\n")
        if lines[0].split()[1] != "200":
            raise ValueError("Status endpoint did not return HTTP 200")
        headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
        length = next((v for k, v in headers.items() if k.lower() == "content-length"), None)
        if length is None or not 0 < int(length) <= MAX_BODY:
            raise ValueError("Missing or oversized status response length")
        body = await reader.readexactly(int(length))
        snapshot = json.loads(body)
        if not isinstance(snapshot, dict):
            raise ValueError("Status response is not an object")
        return snapshot
    finally:
        writer.close()
        # Closing the transport releases the socket even when the poll is cancelled.


@dataclass
class NodeStatus:
    node_id: int
    channel: int
    enabled: bool
    snapshot: dict[str, Any] = field(default_factory=dict)
    received_at: float | None = None
    error: str = "Waiting for the first matching snapshot."
    stopped: bool = False

    @property
    def state(self):
        if self.stopped:
            return "Stopped (historical)"
        if not self.enabled:
            return "Disabled"
        if self.error:
            return "Stale" if self.snapshot else "Unavailable"
        if self.received_at is None or time.monotonic() - self.received_at > 2:
            return "Stale"
        return "Live"


class StatusMonitor:
    def __init__(self, configs, run_id, pid, *, interval=0.5, timeout=0.4):
        self.configs = configs
        self.run_id = run_id
        self.pid = pid
        self.interval = max(0.5, interval)
        self.timeout = timeout
        self.nodes = [NodeStatus(c.node_id, c.sdr.channel, c.status.http_enabled) for c in configs]

    async def poll(self, index):
        config, node = self.configs[index], self.nodes[index]
        if not node.enabled:
            node.error = (
                "Enable HTTP status in the saved node settings and restart for live readings."
            )
            return
        try:
            snapshot = await wait_with_timeout(
                fetch_status(config.status.http_host, config.status.http_port), self.timeout
            )
            identity = snapshot.get("runtime")
            expected = {
                "run_id": self.run_id,
                "config_sha256": config_identity(config),
                "pid": self.pid,
                "node_id": config.node_id,
                "channel": config.sdr.channel,
            }
            if identity != expected:
                raise ValueError(
                    "Session/configuration mismatch; check the saved status port and restart"
                )
            uptime = snapshot.get("uptime_seconds")
            if (
                not isinstance(uptime, (int, float))
                or isinstance(uptime, bool)
                or not math.isfinite(uptime)
                or uptime < 0
                or (node.snapshot and uptime <= node.snapshot["uptime_seconds"])
            ):
                raise ValueError(
                    "Non-advancing or invalid snapshot; check the radio logs and restart"
                )
            node.snapshot = snapshot
            node.received_at = time.monotonic()
            node.error = ""
        except (
            OSError,
            ValueError,
            IndexError,
            asyncio.TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as exc:
            node.error = f"{type(exc).__name__}: {str(exc) or 'status request timed out'}. Check HTTP host/port and radio logs."

    async def _watch(self, index):
        while True:
            started = time.monotonic()
            await self.poll(index)
            await asyncio.sleep(max(0, self.interval - (time.monotonic() - started)))

    async def run(self):
        tasks = [asyncio.create_task(self._watch(i)) for i in range(len(self.nodes))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for node in self.nodes:
                node.stopped = True
