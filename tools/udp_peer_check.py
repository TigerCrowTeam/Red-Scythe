"""Measure an already-running radio's local UDP endpoint on each of two hosts.

Run matching probes with a shared run ID, UTC start time and workload settings.
Each host controls only its local endpoint; radio startup, keys and SDR ownership
stay with packet-radio. Reports measure local delivery and stream counters.
Echo/reflector mode measures RTT on one monotonic clock, never cross-host latency.
"""

import argparse
import hashlib
import json
import math
import socket
import struct
import time
from pathlib import Path
from urllib.request import urlopen

from packet_radio.config import configuration_snapshot, load_config
from packet_radio.protocol.registry import resolve_processing

HEADER = struct.Struct("!4s8sBBQH")
STREAM_COUNTERS = (
    "rx_overflows",
    "rx_buffer_drops",
    "rx_stream_errors",
    "decoder_output_drops",
    "tx_timeouts",
    "tx_underflows",
    "tx_stream_errors",
    "timed_transmit_late",
    "retry_exhausted",
    "endpoint_input_errors",
    "endpoint_output_errors",
)


def payload(run_id, source, destination, sequence, size):
    """Identify every datagram and verify all bytes without synchronized clocks."""
    if not HEADER.size <= size <= 1024:
        raise ValueError(f"payload must be {HEADER.size}..1024 bytes")
    token = hashlib.sha256(run_id.encode()).digest()[:8]
    return HEADER.pack(b"RCHK", token, source, destination, sequence, size) + bytes(
        (i + sequence + source) % 256 for i in range(size - HEADER.size)
    )


def fetch_status(url):
    with urlopen(url.rstrip("/") + "/status", timeout=3) as response:
        return json.load(response)


def run_probe(
    config, *, run_id, start_at, duration, rate, size, mode="traffic", grace=5, status_url=None
):
    """Run one side; both final reports are required for two-host acceptance."""
    if not all(math.isfinite(v) for v in (start_at, duration, rate, grace)):
        raise ValueError("workload values must be finite")
    if duration <= 0 or rate <= 0 or grace < 0 or not 1 <= int(duration * rate) <= 1_000_000:
        raise ValueError("workload must contain 1..1000000 packets with positive rate/duration")
    if not run_id or mode not in ("traffic", "echo", "reflector"):
        raise ValueError("a run ID and valid mode are required")
    if not HEADER.size <= size <= min(1024, config.endpoint.max_payload_bytes):
        raise ValueError(f"payload must be {HEADER.size}..endpoint limit bytes")
    if start_at <= time.time():
        raise ValueError("start-at must be a future Unix UTC timestamp")
    url = status_url or f"http://{config.status.http_host}:{config.status.http_port}"
    before = fetch_status(url)
    expected = {
        "modulation": config.sdr.modulation,
        "configured_bit_rate_bps": config.sdr.bit_rate_bps,
        "configured_center_frequency_hz": config.center_frequency_hz,
        "sample_rate_hz": config.sdr.sample_rate,
    }
    if not before.get("radio_ready") or any(before.get(k) != v for k, v in expected.items()):
        raise ValueError("running radio status does not match the requested profile")
    processing = resolve_processing(config)
    expected_configuration = configuration_snapshot(config)
    expected_configuration["packet_format"] = processing.name
    expected_configuration["processing"] = processing.describe()
    if before.get("configuration") != expected_configuration:
        raise ValueError("running radio effective configuration differs from the profile")
    if any(k not in before for k in STREAM_COUNTERS):
        raise ValueError("running radio is missing required diagnostic counters")
    count = int(duration * rate)
    # Include workload settings to reject a mismatched probe using the same run ID.
    identity = f"{run_id}|{duration:g}|{rate:g}|{size}"
    sent_at, received, rtts = {}, set(), []
    duplicates = unexpected = reflected = 0
    destination = (config.endpoint.bind_host, config.endpoint.bind_port)
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender,
    ):
        receiver.bind((config.endpoint.destination_host, config.endpoint.destination_port))
        receiver.setblocking(False)
        while time.time() < start_at:
            # Discard pre-workload traffic rather than counting old buffered packets.
            try:
                receiver.recv(65535)
            except BlockingIOError:
                time.sleep(min(0.02, max(0, start_at - time.time())))
        actual_start = time.time()
        if actual_start - start_at > 1:
            raise ValueError("missed coordinated start by more than one second")
        begun = time.monotonic()
        while time.monotonic() - begun < duration + grace:
            now = time.monotonic()
            sequence = len(sent_at)
            if mode != "reflector" and sequence < count and now - begun >= sequence / rate:
                data = payload(identity, config.node_id, config.peer_id, sequence, size)
                sender.sendto(data, destination)
                sent_at[sequence] = now
            # Bound draining so unrelated UDP traffic cannot starve deadlines/sends.
            for _ in range(64):
                try:
                    data = receiver.recv(65535)
                except BlockingIOError:
                    break
                if len(data) != size:
                    unexpected += 1
                    continue
                seq = HEADER.unpack(data[: HEADER.size])[4]
                source, target = (
                    (config.node_id, config.peer_id)
                    if mode == "echo"
                    else (config.peer_id, config.node_id)
                )
                if seq >= count or data != payload(identity, source, target, seq, size):
                    unexpected += 1
                    continue
                if mode == "echo" and seq not in sent_at:
                    unexpected += 1
                    continue
                if seq in received:
                    duplicates += 1
                    continue
                received.add(seq)
                if mode == "reflector":
                    sender.sendto(data, destination)
                    reflected += 1
                elif mode == "echo":
                    rtts.append(time.monotonic() - sent_at[seq])
            time.sleep(0.001)
    after = fetch_status(url)
    deltas = {k: after[k] - before[k] for k in STREAM_COUNTERS}
    traffic_passed = (
        len(received) >= 0.99 * count
        and duplicates == unexpected == 0
        and (reflected if mode == "reflector" else len(sent_at)) == count
    )
    report = {
        "report_version": 1,
        "scope": "one host of a coordinated two-host UDP check",
        "run_id": run_id,
        "node_id": config.node_id,
        "peer_id": config.peer_id,
        "mode": mode,
        "scheduled_start_unix_seconds": start_at,
        "actual_start_unix_seconds": actual_start,
        "duration_seconds": duration,
        "grace_seconds": grace,
        "packets_per_second": rate,
        "payload_bytes": size,
        "expected_each": count,
        "sent": len(sent_at),
        "reflected": reflected,
        "received": len(received),
        "missing_sequences": sorted(set(range(count)) - received),
        "duplicates": duplicates,
        "changed_or_unexpected": unexpected,
        "delivered_payload_bps": len(received) * size * 8 / duration,
        "stream_deltas": deltas,
        "before": before,
        "after": after,
        "traffic_passed": traffic_passed,
        "stream_passed": not any(deltas.values()),
    }
    if mode == "echo":
        rtt = sorted(rtts)[max(0, math.ceil(0.95 * len(rtts)) - 1)] if rtts else None
        report["round_trip_p95_seconds"] = rtt
        report["latency_passed"] = rtt is not None and rtt <= 1
    report["passed"] = (
        traffic_passed
        and report["stream_passed"]
        and report.get("latency_passed", True)
        and after.get("radio_ready", False)
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start-at", type=float, required=True, help="future Unix UTC seconds")
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--rate", type=float, default=5)
    parser.add_argument("--payload", type=int, default=256)
    parser.add_argument("--mode", choices=("traffic", "echo", "reflector"), default="traffic")
    parser.add_argument("--status-url", help="local radio HTTP status base URL")
    parser.add_argument("--output", type=Path, help="optional temporary JSON report")
    args = parser.parse_args()
    report = {"report_version": 1, "passed": False}
    try:
        config = load_config(args.profile)
        report = run_probe(
            config,
            run_id=args.run_id,
            start_at=args.start_at,
            duration=args.duration,
            rate=args.rate,
            size=args.payload,
            mode=args.mode,
            status_url=args.status_url,
        )
    except (OSError, ValueError, KeyError) as exc:
        report["error"] = str(exc)
    report["profile_source"] = str(args.profile.resolve())
    if args.profile.exists():
        report["profile_sha256"] = hashlib.sha256(args.profile.read_bytes()).hexdigest()
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
