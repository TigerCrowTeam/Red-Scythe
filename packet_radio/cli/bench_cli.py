"""Coordinate repeatable two-computer RF checks over optional SSH control.

The pair command owns both worker/radio processes. Each worker generates and
verifies its own local UDP data, so SSH never substitutes for the radio link.
All waits are bounded and sources are left unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import shlex
import sys
import time
from pathlib import Path
from uuid import uuid4

from packet_radio.modems.registry import WAVEFORMS
from packet_radio.runtime.bench import worker, workload


async def read(process, timeout=75):
    line = await asyncio.wait_for(process.stdout.readline(), timeout)
    if not line:
        raise RuntimeError(f"bench worker exited ({process.returncode})")
    result = json.loads(line)
    if result.get("error"):
        raise RuntimeError(str(result))
    return result


async def send(process, value):
    process.stdin.write((json.dumps(value) + "\n").encode())
    await process.stdin.drain()


async def stop(process):
    """Require the worker to acknowledge clean teardown; kill only owned processes on timeout."""
    try:
        if process.returncode is None:
            await send(process, {"action": "stop"})
        final = await read(process, 12)
        await asyncio.wait_for(process.wait(), 5)
        return final.get("stopped") == "Stopped" and process.returncode == 0
    except (OSError, RuntimeError, ValueError, asyncio.TimeoutError):
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        return False


async def pair(args):
    rows = []
    modes = [
        ("A to B", "send", "receive"),
        ("B to A", "receive", "send"),
        ("simultaneous", "duplex", "duplex"),
    ]
    for modulation in args.modulations:
        processes = []
        try:
            common = ["-m", "packet_radio.cli.bench_cli", "worker", "--modulation", modulation]
            if args.experiment:
                common += ["--experiment-json", json.dumps(args.experiment)]
            if args.allow_unauthenticated:
                common.append("--allow-unauthenticated")
            remote = shlex.join(
                [
                    args.peer_python,
                    *common,
                    "--config",
                    args.peer_config,
                    "--clock-offset",
                    str(args.peer_clock_offset),
                ]
            )
            commands = [
                [sys.executable, *common, "--config", str(args.config.resolve())],
                [
                    "ssh",
                    "-T",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    "-o",
                    "ServerAliveInterval=5",
                    "-o",
                    "ServerAliveCountMax=3",
                    "--",
                    args.peer_ssh,
                    remote,
                ],
            ]
            for command in commands:
                processes.append(
                    await asyncio.create_subprocess_exec(
                        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
                    )
                )
            ready = await asyncio.gather(*(read(p) for p in processes))
            a, b = ready
            if a["node"] != b["peer"] or b["node"] != a["peer"] or a["serial"] == b["serial"]:
                raise ValueError("node identities or SDR serials do not describe a two-radio pair")
            if a["configuration"]["emulation"] != b["configuration"]["emulation"]:
                raise ValueError("resolved on-air profiles differ between hosts")
            print(f"{modulation.upper()}: both radios ready, {a['bit_rate']:g} bit/s", flush=True)
            if b.get("clock_offset_ns") is not None:
                print(
                    f"  Follower hardware-clock offset: {b['clock_offset_ns'] / 1e9:.6f} s; RF synchronized: {b.get('synchronized')}",
                    flush=True,
                )
            if args.experiment:
                if any(
                    r["configuration"].get("benchmark_experiment") != args.experiment for r in ready
                ):
                    raise ValueError("workers did not apply the requested experimental schedule")
                print(f"  EXPERIMENT: {args.experiment}", flush=True)
            checks = list(modes)
            if args.recovery:
                if (
                    a["configuration"]["emulation"]["frequency"].get("synchronization")
                    != "rf-leader"
                ):
                    raise ValueError("recovery check requires RF leader hopping")
                if a["configuration"]["timing_role"] != "leader":
                    raise ValueError("recovery check requires the local node to be leader")
                checks.append(("after recovery", "duplex", "duplex"))
            for label, mode_a, mode_b in checks:
                if label == "after recovery":
                    print("  Testing leader outage and RF reacquisition...", flush=True)
                    await send(processes[0], {"action": "pause"})
                    await read(processes[0], 20)
                    await asyncio.sleep(17)
                    await send(processes[1], {"action": "status"})
                    lost = await read(processes[1])
                    if lost.get("synchronized") or lost.get("transmit_allowed"):
                        raise RuntimeError(
                            "follower did not inhibit TX after losing RF synchronization"
                        )
                    await send(processes[0], {"action": "resume"})
                    await read(processes[0])
                    began = time.monotonic()
                    acquired = None
                    while time.monotonic() - began < 40:
                        await send(processes[1], {"action": "status"})
                        acquired = await read(processes[1])
                        if acquired.get("synchronized") and acquired.get(
                            "reacquisitions", 0
                        ) > lost.get("reacquisitions", 0):
                            break
                        await asyncio.sleep(0.2)
                    else:
                        raise RuntimeError("follower did not reacquire the restarted leader")
                    print(
                        f"  RF reacquired in {time.monotonic() - began:.2f} seconds after leader restart",
                        flush=True,
                    )

                token = uuid4().hex
                for process, mode in zip(processes, (mode_a, mode_b)):
                    await send(
                        process,
                        dict(
                            action="run",
                            run_id=token,
                            duration=args.duration,
                            rate=args.rate,
                            size=args.payload,
                            mode=mode,
                        ),
                    )
                results = await asyncio.gather(*(read(p, args.duration + 20) for p in processes))
                passed = all(r["passed"] for r in results)
                row = dict(
                    modulation=modulation,
                    traffic=label,
                    duration=args.duration,
                    payload=args.payload,
                    rate=args.rate,
                    results=results,
                    passed=passed,
                )
                rows.append(row)
                print(
                    f"  {label}: {'PASS' if passed else 'FAIL'} · "
                    + " · ".join(
                        f"{host} received {r['received']}/{r['expected']}, errors {sum(r['errors'].values())}"
                        for host, r in zip(("A", "B"), results)
                    ),
                    flush=True,
                )
                if not passed:
                    print(json.dumps(results), flush=True)
                    # Retries/backlog from a failed phase would contaminate the next run ID.
                    break
        finally:
            clean = await asyncio.gather(*(stop(p) for p in processes))
            if not all(clean) and sys.exc_info()[0] is None:
                raise RuntimeError("a bench worker did not acknowledge clean shutdown")
    if args.summary:
        with args.summary.open("x") as out:
            out.write(
                "# Two-computer RF results\n\n| Modulation | Traffic | Seconds | Payload bytes | Packets/s per sender | A received | B received | Result |\n|---|---|---:|---:|---:|---|---|---|\n"
            )
            for row in rows:
                a, b = row["results"]
                out.write(
                    f"| {row['modulation']} | {row['traffic']} | {row['duration']:g} | {row['payload']} | {row['rate']:g} | {a['received']}/{a['expected']} | {b['received']}/{b['expected']} | {'PASS' if row['passed'] else 'FAIL'} |\n"
                )
    return all(row["passed"] for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    local = sub.add_parser(
        "worker", help="local stdin-controlled test worker (normally launched by pair)"
    )
    local.add_argument("--config", required=True, type=Path)
    local.add_argument("--modulation", choices=tuple(WAVEFORMS))
    local.add_argument("--allow-unauthenticated", action="store_true")
    local.add_argument("--clock-offset", type=float, default=0.0)
    local.add_argument("--experiment-json", default="{}", help=argparse.SUPPRESS)
    paired = sub.add_parser(
        "pair", help="start both B205 nodes and check sequential and simultaneous RF traffic"
    )
    paired.add_argument("--config", type=Path, required=True)
    paired.add_argument(
        "--peer-ssh", required=True, help="SSH user@host; key authentication must already work"
    )
    paired.add_argument(
        "--peer-python", required=True, help="absolute UHD Python interpreter path on the peer"
    )
    paired.add_argument("--peer-config", required=True, help="absolute node YAML path on the peer")
    paired.add_argument(
        "--modulations", nargs="+", choices=tuple(WAVEFORMS), default=list(WAVEFORMS)
    )
    paired.add_argument(
        "--duration", type=float, default=60, help="seconds per traffic direction/mode (default 60)"
    )
    paired.add_argument(
        "--rate", type=float, default=5, help="packets/second per transmitting node"
    )
    paired.add_argument("--payload", type=int, default=256)
    paired.add_argument("--summary", type=Path, help="new Markdown file for final summaries only")
    paired.add_argument("--allow-unauthenticated", action="store_true")
    paired.add_argument(
        "--peer-clock-offset",
        type=float,
        default=0.0,
        help="test-only peer process clock skew in seconds; system clock unchanged",
    )
    paired.add_argument(
        "--recovery",
        action="store_true",
        help="also stop/restart the RF leader and verify follower reacquisition",
    )
    paired.add_argument(
        "--experimental-hop-rate",
        type=float,
        help="test-only B205 rate, 1..30 hops/s; normal limits unchanged",
    )
    paired.add_argument(
        "--experimental-guard",
        type=float,
        default=0.025,
        help="test guard in seconds, default .025",
    )
    paired.add_argument(
        "--experimental-sync-lead",
        type=float,
        default=0.01,
        help="test SYNC lead in seconds, default .01",
    )
    args = parser.parse_args()
    try:
        if args.command == "worker":
            asyncio.run(
                worker(
                    args.config,
                    args.modulation,
                    args.allow_unauthenticated,
                    args.clock_offset,
                    json.loads(args.experiment_json),
                )
            )
        else:
            args.experiment = (
                dict(
                    hop_rate=args.experimental_hop_rate,
                    guard=args.experimental_guard,
                    sync_lead=args.experimental_sync_lead,
                    payload=args.payload,
                )
                if args.experimental_hop_rate is not None
                else None
            )
            workload(args.duration, args.rate, args.payload, "duplex")
            if not math.isfinite(args.peer_clock_offset) or abs(args.peer_clock_offset) > 86400:
                raise ValueError("test clock offset must be finite and within one day")
            if args.summary and args.summary.exists():
                raise ValueError("summary destination already exists")
            if (
                args.peer_ssh.startswith("-")
                or not args.peer_python.startswith("/")
                or not args.peer_config.startswith("/")
            ):
                raise ValueError("provide an SSH host and absolute remote paths")
            if not asyncio.run(pair(args)):
                raise SystemExit(1)
    except (ValueError, OSError, RuntimeError, asyncio.TimeoutError) as exc:
        parser.exit(1, f"packet-radio-check: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "check interrupted; owned radios stopped\n")


if __name__ == "__main__":
    main()
