"""Bounded two-host UDP workloads for owned radios; payloads travel only over RF.

Workers accept control messages over stdin, bind local UDP before announcing
readiness, and terminate their radio child on EOF, timeout or failure. Results
contain counts and timing, not a forwarding path between hosts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import socket
import struct
import tempfile
import time
from pathlib import Path

from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.forms import FormSession
from packet_radio.configuration.storage import save_project
from packet_radio.runtime.controller import RadioController

ERRORS = (
    "rx_overflows",
    "rx_buffer_drops",
    "rx_stream_errors",
    "decoder_output_drops",
    "tx_timeouts",
    "tx_underflows",
    "tx_stream_errors",
    "timed_transmit_late",
    "retry_exhausted",
    "transmit_errors",
    "transmit_drops",
    "endpoint_input_errors",
    "endpoint_output_errors",
    "hop_slots_missed",
    "hop_clock_reversals",
    "synchronization_losses",
)
HEADER = struct.Struct("!8sBI")


async def fresh_snapshot(controller, timeout=4):
    """Wait for an owned monitor poll newer than this request, including after restart."""
    requested = time.monotonic()
    while time.monotonic() - requested < timeout:
        if controller.monitor:
            node = controller.monitor.nodes[0]
            if (
                node.state == "Live"
                and node.received_at is not None
                and node.received_at > requested
            ):
                return node.snapshot.copy()
        await asyncio.sleep(0.05)
    raise RuntimeError("no fresh matching radio status")


def prepare_session(config_path, modulation=None):
    """Select a modem preset while retaining comparable source RF rates and bandwidth."""
    session = FormSession(RadioDraft.load(config_path))
    if modulation and modulation != session.selection.modulation:
        source = session.draft.profile["waveform"]
        session.change("profile.waveform.modulation", modulation)
        for name in ("bit_rate_bps", "sample_rate_hz", "bandwidth_hz"):
            session.change("profile.waveform." + name, source[name])
    return session


def payload(run_id: str, source: int, sequence: int, size: int) -> bytes:
    token = hashlib.sha256(run_id.encode()).digest()[:8]
    return HEADER.pack(token, source, sequence) + bytes(
        (i + source + sequence) % 256 for i in range(size - HEADER.size)
    )


def workload(duration: float, rate: float, size: int, mode: str) -> int:
    if not all(math.isfinite(v) for v in (duration, rate)):
        raise ValueError("duration and rate must be finite")
    if not 1 <= duration <= 3600 or not 0 < rate <= 100 or not HEADER.size <= size <= 1024:
        raise ValueError(
            "duration 1..3600 s, rate >0..100 packets/s, payload 13..1024 bytes required"
        )
    if mode not in ("send", "receive", "duplex"):
        raise ValueError("mode must be send, receive or duplex")
    count = int(duration * rate)
    if count < 1:
        raise ValueError("workload must contain at least one packet")
    return count


async def traffic(
    receiver, cfg, *, run_id, duration, rate, size, mode, grace=3, connected=lambda: True
):
    count = workload(duration, rate, size, mode)
    if size > cfg.endpoint.max_payload_bytes:
        raise ValueError("test payload exceeds the configured endpoint maximum")
    receiver.setblocking(False)
    seen = set()
    unexpected = duplicates = sent = 0
    begun = time.monotonic()
    while time.monotonic() - begun < duration + grace:
        if not connected():
            raise ConnectionError("test controller disconnected")
        now = time.monotonic() - begun
        if mode != "receive" and sent < count and now >= sent / rate:
            receiver.sendto(
                payload(run_id, cfg.node_id, sent, size),
                (cfg.endpoint.bind_host, cfg.endpoint.bind_port),
            )
            sent += 1
        for _ in range(64):
            try:
                data = receiver.recv(65535)
            except BlockingIOError:
                break
            if len(data) < HEADER.size:
                unexpected += 1
                continue
            _, _, seq = HEADER.unpack(data[: HEADER.size])
            if mode == "send" or seq >= count or data != payload(run_id, cfg.peer_id, seq, size):
                unexpected += 1
            elif seq in seen:
                duplicates += 1
            else:
                seen.add(seq)
        await asyncio.sleep(0.001)
    expected = count if mode != "send" else 0
    return dict(
        sent=sent,
        received=len(seen),
        expected=expected,
        duplicates=duplicates,
        unexpected=unexpected,
        missing=sorted(set(range(expected)) - seen)[:100],
        missing_count=expected - len(seen),
        passed=len(seen) == expected
        and not (duplicates or unexpected)
        and sent == (count if mode != "receive" else 0),
    )


async def worker(
    config_path, modulation=None, allow_unauthenticated=False, clock_offset=0.0, experiment=None
):
    import sys

    reader = asyncio.StreamReader()
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    if not math.isfinite(clock_offset) or abs(clock_offset) > 86400:
        raise ValueError("test clock offset must be within one day")
    controller = RadioController(
        startup_timeout=60,
        command=[
            sys.executable,
            "-m",
            "packet_radio.runtime.bench_child",
            str(os.getpid()),
            str(clock_offset),
            json.dumps(experiment or {}),
        ],
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def emit(value):
        print(json.dumps(value), flush=True)

    try:
        session = prepare_session(config_path, modulation)
        cfg = session.report.config
        if cfg is None or cfg.sdr.device.get("type") != "b200" or not cfg.sdr.device.get("serial"):
            raise ValueError("bench requires a validated B205 node with an explicit serial")
        if cfg.endpoint.bind_host != "127.0.0.1" or cfg.endpoint.destination_host != "127.0.0.1":
            raise ValueError("bench endpoints must use localhost; payloads must traverse RF")
        if not cfg.status.http_enabled:
            raise ValueError("enable local HTTP status for the bench")
        from packet_radio.runtime.bench_experiment import experiment_config

        experiment_config(cfg, experiment)
        sock.bind((cfg.endpoint.destination_host, cfg.endpoint.destination_port))
        with tempfile.TemporaryDirectory(prefix="packet-radio-bench-") as directory:
            draft = save_project(session.draft, Path(directory) / "node.yaml")

            async def start():
                controller.start([draft], allow_unauthenticated=allow_unauthenticated)
                deadline = time.monotonic() + 65
                while controller.state not in ("Running", "Failed") and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                if controller.state != "Running":
                    raise RuntimeError(controller.error or "radio startup timed out")

            await start()

            async def snapshot():
                return await fresh_snapshot(controller)

            initial = await snapshot()
            from packet_radio.protocol.rf_hopping import enabled as rf_hopping

            if rf_hopping(cfg):
                deadline = time.monotonic() + 40
                while not initial.get("synchronized") and time.monotonic() < deadline:
                    await asyncio.sleep(0.1)
                    initial = await snapshot()
                if not initial.get("synchronized"):
                    raise RuntimeError("RF synchronization was not acquired within 40 seconds")
            emit(
                dict(
                    ready=True,
                    node=cfg.node_id,
                    peer=cfg.peer_id,
                    modulation=cfg.sdr.modulation,
                    bit_rate=cfg.sdr.bit_rate_bps,
                    sample_rate=cfg.sdr.sample_rate,
                    serial=cfg.sdr.device["serial"],
                    configuration=initial["configuration"],
                    clock_offset_ns=initial.get("hardware_clock_offset_ns"),
                    synchronized=initial.get("synchronized"),
                )
            )
            while True:
                # EOF or an abandoned controller releases the SDR; no persistent daemon.
                line = await asyncio.wait_for(reader.readline(), 120)
                if not line:
                    break
                request = json.loads(line)
                if request["action"] == "stop":
                    break
                if request["action"] == "pause":
                    await controller.stop()
                    emit({"paused": not controller.active})
                    continue
                if request["action"] == "resume":
                    await start()
                    emit({"resumed": True})
                    continue
                if request["action"] == "status":
                    emit(await snapshot())
                    continue
                if request["action"] != "run":
                    raise ValueError("unknown bench command")
                if experiment and request["size"] > experiment["payload"]:
                    raise ValueError("workload exceeds the experimental payload ceiling")
                before = await snapshot()
                result = await traffic(
                    sock,
                    cfg,
                    **{k: request[k] for k in ("run_id", "duration", "rate", "size", "mode")},
                    connected=lambda: not reader.at_eof(),
                )
                after = await snapshot()
                result["errors"] = {k: after[k] - before[k] for k in ERRORS}
                result["radio"] = {
                    k: after.get(k)
                    for k in (
                        "synchronized",
                        "frequency_hops",
                        "reacquisitions",
                        "synchronization_losses",
                        "hardware_clock_offset_ns",
                        "hardware_clock_drift_ppm",
                        "hardware_clock_observations",
                    )
                }
                result["passed"] &= not any(result["errors"].values()) and after["radio_ready"]
                if rf_hopping(cfg):
                    result["passed"] &= bool(after["synchronized"])
                    if request["duration"] >= 2 * (
                        1 / experiment["hop_rate"] if experiment else cfg.frequency.interval_seconds
                    ):
                        result["passed"] &= after["frequency_hops"] > before["frequency_hops"]
                emit(result)
    except Exception as exc:
        emit({"error": str(exc), "logs": list(controller.logs)[-8:]})
    finally:
        await controller.stop()
        sock.close()
        transport.close()
        emit({"stopped": controller.state, "error": controller.error})
