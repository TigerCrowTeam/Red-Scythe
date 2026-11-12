"""Bounded, cancellable hardware checks in an owned subprocess, independent of Textual.

Discovery and preflight load UHD only inside the disposable worker. Results describe one configuration
snapshot, not RF qualification. No streams, key files or persistent reports are created.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict

from packet_radio.config import RadioConfig, validate_network
from packet_radio.configuration.draft import RadioDraft

RESULT_PREFIX = "PACKET_RADIO_PREFLIGHT="


def snapshot(draft: RadioDraft) -> dict:
    return {"node": draft.node, "profile": draft.profile, "path": str(draft.node_path)}


def resolve(value: dict) -> RadioConfig:
    report = RadioDraft(value["node"], value["profile"], value["path"]).validate()
    if not report.valid:
        raise ValueError(report.issues[0].message)
    assert report.config is not None
    return report.config


def signature(draft: RadioDraft) -> str:
    # Includes all effective settings and paths; never display the serialized value.
    report = draft.validate()
    return (
        json.dumps(asdict(report.config), sort_keys=True)
        if report.valid and report.config is not None
        else "invalid"
    )


def validate_shared_pair(configs: list[RadioConfig]) -> None:
    """Check a prospective same-host paired X310 before acquiring its device."""
    if len(configs) != 2:
        raise ValueError("Select exactly two nodes for a shared X310 check.")
    a, b = configs
    validate_network({"local": a, "peer": b})
    if a.sdr.device != b.sdr.device or a.sdr.device.get("type") != "x300":
        raise ValueError("Paired preflight requires identical explicit X310 device arguments.")
    if not (a.sdr.device.get("addr") or a.sdr.device.get("serial")):
        raise ValueError("Select the shared X310 address or serial explicitly.")
    if a.sdr.channel == b.sdr.channel:
        raise ValueError("Shared X310 nodes must own distinct channels.")
    if a.sdr.sample_rate != b.sdr.sample_rate:
        raise ValueError("Shared X310 nodes must use the same sample rate.")
    ports = [c.endpoint.bind_port for c in configs] + [c.endpoint.destination_port for c in configs]
    if len(set(ports)) != len(ports):
        raise ValueError("Paired nodes require distinct payload input/output UDP ports.")
    if a.status.http_enabled and b.status.http_enabled and a.status.http_port == b.status.http_port:
        raise ValueError("Paired nodes require distinct enabled status HTTP ports.")


class HardwareJob:
    """Own at most one probe child; terminate and reap it on timeout or cancellation."""

    def __init__(self, timeout: float = 30.0, command: list[str] | None = None):
        self.timeout = timeout
        self.command = command or [sys.executable, "-m", "packet_radio.hardware.preflight"]
        self.process: asyncio.subprocess.Process | None = None
        self.running = False

    async def run(self, request: dict) -> dict:
        if self.running:
            raise ValueError("A hardware check is already running. Cancel it or wait.")
        self.running = True
        try:
            # Shield creation so cancellation cannot orphan a child during spawn.
            spawn = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *self.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            )
            try:
                self.process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                self.process = await spawn
                raise
            try:
                stdout, stderr = await asyncio.wait_for(
                    self.process.communicate(json.dumps(request).encode()), self.timeout
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError(
                    f"Hardware check timed out after {self.timeout:g}s. Check power, Ethernet/USB, "
                    "device address and whether another application owns the SDR."
                ) from exc
            for line in reversed(stdout.decode(errors="replace").splitlines()):
                if line.startswith(RESULT_PREFIX):
                    result = json.loads(line[len(RESULT_PREFIX) :])
                    if not isinstance(result, dict) or "ok" not in result:
                        raise RuntimeError("Invalid hardware worker response.")
                    if not result["ok"]:
                        raise RuntimeError(result["error"])
                    if self.process.returncode:
                        break
                    return result
            detail = stderr.decode(errors="replace")[-1500:]
            raise RuntimeError(
                f"Hardware worker exited without a result. Check UHD installation. {detail}"
            )
        finally:
            if self.process is not None and self.process.returncode is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self.process.wait(), 2.0)
                except asyncio.TimeoutError:
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
                    await self.process.wait()
            self.process = None
            self.running = False
