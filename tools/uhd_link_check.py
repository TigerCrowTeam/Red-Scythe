"""Local two-USRP UDP characterization, requiring the attenuated bench profiles.

This actively transmits. It uses separate application processes, temporary signed
identities and real localhost UDP endpoints. It is not two-host release acceptance.
"""

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import platform
import socket
import sys
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packet_radio.app import RadioApp
from packet_radio.config import load_config, validate_network
from packet_radio.protocol.framing import PacketKind


def report_configuration(config):
    """Describe effective settings without retaining generated key/state paths."""
    result = asdict(config)
    result["authentication"]["private_key_file"] = "<temporary>"
    result["authentication"]["public_key_files"] = {
        str(k): "<temporary>" for k in config.authentication.public_key_files
    }
    result["authentication"]["replay_state_file"] = "<temporary>"
    return result


def install_packet_audit(app):
    """Out-of-band IDs for tiny payload tests; never add bytes to RF/UDP packets."""
    audit = {"tx": [], "rx": []}
    transmitted = app.link.transmitted
    is_duplicate = app.link.is_duplicate
    send = app.endpoint.send
    current = [None]

    def identity(packet):
        return f"{packet.source}:{packet.session_id}:{packet.sequence}"

    def record_tx(queued, **kwargs):
        if queued.packet.kind == PacketKind.DATA and queued.attempts == 0:
            audit["tx"].append([identity(queued.packet), queued.packet.payload.hex()])
        transmitted(queued, **kwargs)

    def record_candidate(packet):
        duplicate = is_duplicate(packet)
        current[0] = packet if not duplicate else None
        return duplicate

    def record_delivery(payload):
        send(payload)
        packet = current[0]
        if packet is None:
            raise RuntimeError("delivery without audited packet identity")
        audit["rx"].append([identity(packet), payload.hex()])

    app.link.transmitted = record_tx
    app.link.is_duplicate = record_candidate
    app.endpoint.send = record_delivery
    return audit


def correlate_tiny_payloads(sent, transmitted, delivered, udp):
    """Reject ambiguous evidence instead of guessing wrapped one-byte IDs."""
    ordered = list(sent.items())
    if len(transmitted) != len(ordered) or any(
        bytes.fromhex(row[1]) != expected[0] for row, (_seq, expected) in zip(transmitted, ordered)
    ):
        raise ValueError("tiny-payload TX audit does not match submitted FIFO")
    identities = {row[0]: seq for row, (seq, _expected) in zip(transmitted, ordered)}
    if len(identities) != len(transmitted):
        raise ValueError("duplicate TX packet identity")
    if len(delivered) != len(udp):
        raise ValueError("tiny-payload UDP count differs from delivery audit")
    received, latencies = {}, []
    duplicates = changed = 0
    for row, (data, arrival) in zip(delivered, udp):
        seq = identities.get(row[0])
        if seq is None or data != bytes.fromhex(row[1]) or data != sent[seq][0]:
            changed += 1
        elif seq in received:
            duplicates += 1
        else:
            received[seq] = data
            latencies.append(arrival - sent[seq][1])
    return received, latencies, duplicates, changed


def worker(config, pipe, audit_packets=False):
    app = None
    try:
        app = RadioApp(config)
        audit = install_packet_audit(app) if audit_packets else None
        muted = [False]

        def gate(method):
            def transmit(samples, *args, **kwargs):
                return method(np.zeros_like(samples) if muted[0] else samples, *args, **kwargs)

            return transmit

        for method in ("transmit", "transmit_at", "transmit_with_lead"):
            setattr(app.sdr, method, gate(getattr(app.sdr, method)))
        hardware = app.sdr.radio
        version = getattr(hardware.api, "get_version_string", lambda: None)()
        pipe.send(
            (
                "ready",
                {
                    "time": time.monotonic(),
                    "actual_settings": hardware.actual_settings,
                    "uhd_version": version,
                    "numpy_version": np.__version__,
                },
            )
        )
        while True:
            if pipe.poll():
                command = pipe.recv()
                if command == "stop":
                    break
                if command in ("mute", "unmute"):
                    muted[0] = command == "mute"
                    pipe.send((command, app.statistics.snapshot()))
                if command == "clear_audit":
                    if audit is not None:
                        audit["tx"].clear()
                        audit["rx"].clear()
                    pipe.send((command, None))
                if command == "snapshot":
                    snapshot = app.statistics.snapshot()
                    snapshot["process_cpu_seconds"] = time.process_time()
                    snapshot["recovering"] = app.recovering
                    if audit is not None:
                        snapshot["packet_audit"] = audit
                    pipe.send(("snapshot", snapshot))
            app.step()
        app.close()
        snapshot = app.statistics.snapshot()
        if snapshot["shutdown_errors"]:
            raise RuntimeError("radio shutdown failed")
        pipe.send(("stopped", snapshot))
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        if app is not None:
            app.close()
        pipe.close()


def start(config, audit_packets=False):
    parent, child = mp.get_context("spawn").Pipe()
    process = mp.get_context("spawn").Process(target=worker, args=(config, child, audit_packets))
    process.start()
    child.close()
    if not parent.poll(30):
        process.terminate()
        process.join(3)
        parent.close()
        raise RuntimeError("hardware startup timeout")
    kind, value = parent.recv()
    if kind != "ready":
        process.join(3)
        if process.is_alive():
            process.terminate()
            process.join(3)
        parent.close()
        raise RuntimeError(f"child failed: {value}")
    process.hardware_ready_time = value["time"]
    process.hardware_info = value
    return process, parent


def request(peer, command):
    process, pipe = peer
    pipe.send(command)
    if not pipe.poll(5):
        raise RuntimeError(f"{command} response timed out")
    kind, value = pipe.recv()
    if kind == "error":
        raise RuntimeError(value)
    if command == "stop":
        process.join(5)
        if process.is_alive():
            raise RuntimeError("radio process did not release hardware")
        pipe.close()
        if process.exitcode != 0:
            raise RuntimeError(f"radio process exited with {process.exitcode}")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=60)
    profiles = parser.add_mutually_exclusive_group()
    profiles.add_argument("--modulation", choices=("bpsk", "2fsk", "msk", "gmsk", "qpsk"))
    profiles.add_argument(
        "--profiles",
        nargs=2,
        type=Path,
        metavar=("LEADER", "FOLLOWER"),
        help="explicit paired YAML profiles for this local hardware run",
    )
    parser.add_argument("--rate", type=float, default=5)
    parser.add_argument("--payload", type=int, default=256)
    parser.add_argument("--echo", action="store_true", help="separate round-trip echo workload")
    parser.add_argument("--ack-timeout", type=float, help="test-only ACK timeout override")
    parser.add_argument("--tdd-period", type=float, help="test-only host-scheduled TDD period")
    parser.add_argument("--tdd-guard", type=float, default=0.01, help="host TDD guard in seconds")
    parser.add_argument("--timed-tx", action="store_true")
    parser.add_argument("--tx-gain", type=float)
    parser.add_argument("--clock-sync", action="store_true")
    parser.add_argument("--scheduled-tdd", action="store_true")
    parser.add_argument(
        "--interrupt-seconds",
        type=float,
        default=0,
        help="send zero IQ on both TX paths, then test recovery",
    )
    parser.add_argument("--restart-follower", action="store_true")
    parser.add_argument("--output", type=Path, help="optional temporary JSON report")
    args = parser.parse_args()
    for name in (
        "duration",
        "rate",
        "ack_timeout",
        "tdd_period",
        "tdd_guard",
        "tx_gain",
        "interrupt_seconds",
    ):
        value = getattr(args, name)
        if value is not None and not math.isfinite(value):
            parser.error(f"{name} must be finite")
    if args.ack_timeout is not None and args.ack_timeout <= 0:
        parser.error("ACK timeout must be positive")
    if args.tdd_period is not None and (args.tdd_period <= 0 or args.scheduled_tdd):
        parser.error("TDD period must be positive and cannot accompany --scheduled-tdd")
    if args.echo and args.restart_follower:
        parser.error("run echo and restart checks separately")
    if args.interrupt_seconds < 0 or (args.interrupt_seconds and (args.echo or args.payload < 8)):
        parser.error("interruption requires nonnegative seconds and a separate payload >= 8 run")
    if args.payload < 8 and (args.echo or args.restart_follower):
        parser.error("tiny-payload boundary runs must be separate from echo/restart")
    if not 1 <= args.payload <= 1024 or args.duration <= 0 or args.rate <= 0:
        parser.error("payload must be 1..1024; duration and rate must be positive")
    if int(args.duration * args.rate) < 1:
        parser.error("workload must submit at least one packet per direction")
    profile_paths = args.profiles or [
        Path(f"configs/bench/peer-{p}-{args.modulation or 'bpsk'}.yaml") for p in ("a", "b")
    ]
    original_configs = [load_config(path) for path in profile_paths]
    validate_network(dict(enumerate(original_configs)))
    if [c.timing_role for c in original_configs] != ["leader", "follower"]:
        parser.error("profiles must be ordered leader, follower")
    if args.payload > min(c.endpoint.max_payload_bytes for c in original_configs):
        parser.error("payload exceeds a profile's local endpoint limit")
    report = {
        "report_version": 1,
        "profile_sources": [
            {"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in profile_paths
        ],
        "scope": "single-host two-process UDP characterization, not release acceptance",
        "started_unix_seconds": time.time(),
        "python": sys.version,
        "platform": platform.platform(),
        "duration_seconds": args.duration,
        "packets_per_second_per_direction": args.rate,
        "payload_bytes": args.payload,
        "timed_tx": args.timed_tx or args.clock_sync or args.scheduled_tdd,
        "clock_sync": args.clock_sync or args.scheduled_tdd,
        "scheduled_tdd": args.scheduled_tdd,
        "workload": "echo" if args.echo else "bidirectional",
        "errors": [],
    }
    peers = []
    outputs = []
    sender = None
    try:
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        with tempfile.TemporaryDirectory(prefix="radio-link-check-") as directory:
            root = Path(directory)
            public = {}
            for node in (c.node_id for c in original_configs):
                key = Ed25519PrivateKey.generate()
                p = root / f"{node}.pem"
                p.write_bytes(
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                )
                p.chmod(0o600)
                p = root / f"{node}.pub"
                p.write_bytes(
                    key.public_key().public_bytes(
                        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                )
                public[node] = str(p)
            configs = []
            for c in original_configs:
                node = c.node_id
                c = replace(
                    c,
                    authentication=replace(
                        c.authentication,
                        private_key_file=str(root / f"{node}.pem"),
                        public_key_files=public,
                        replay_state_file=str(root / f"{node}-replay.json"),
                    ),
                    link=replace(
                        c.link,
                        hardware_timed_io=c.link.hardware_timed_io
                        or args.timed_tx
                        or args.clock_sync,
                        hardware_clock_sync=c.link.hardware_clock_sync or args.clock_sync,
                    ),
                    status=replace(c.status, http_enabled=False, output="off"),
                )
                if args.scheduled_tdd:
                    c = replace(
                        c,
                        link=replace(
                            c.link,
                            hardware_timed_io=True,
                            hardware_clock_sync=True,
                            hardware_slot_scheduling=True,
                            tdd_period_seconds=0.2,
                            tdd_guard_seconds=0.01,
                            tdd_leader_bursts_per_slot=3,
                            tdd_follower_bursts_per_slot=3,
                            hardware_timed_tx_lead_seconds=0.003,
                            hardware_sync_tx_lead_seconds=0.02,
                            hardware_slot_lookahead_seconds=0.02,
                            hardware_slot_submission_margin_seconds=0.003,
                        ),
                    )
                if args.ack_timeout is not None:
                    c = replace(c, link=replace(c.link, ack_timeout_seconds=args.ack_timeout))
                if args.tdd_period is not None:
                    c = replace(
                        c,
                        link=replace(
                            c.link,
                            tdd_period_seconds=args.tdd_period,
                            tdd_guard_seconds=args.tdd_guard,
                            tdd_leader_bursts_per_slot=8,
                            tdd_follower_bursts_per_slot=8,
                            tdd_inter_burst_seconds=0.002,
                        ),
                    )
                if args.tx_gain is not None:
                    c = replace(c, sdr=replace(c.sdr, tx_gain=args.tx_gain))
                configs.append(c)
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((c.endpoint.destination_host, c.endpoint.destination_port))
                sock.setblocking(False)
                outputs.append(sock)
            validate_network(dict(enumerate(configs)))
            report["timed_tx"] = all(c.link.hardware_timed_io for c in configs)
            report["clock_sync"] = all(c.link.hardware_clock_sync for c in configs)
            report["scheduled_tdd"] = all(c.link.hardware_slot_scheduling for c in configs)
            report["settings"] = [report_configuration(c) for c in configs]
            for config in configs:
                peers.append(start(config, audit_packets=args.payload < 8))
            report["hardware"] = [peer[0].hardware_info for peer in peers]
            ready = max(peer[0].hardware_ready_time for peer in peers)
            acquisition_payloads = [b"\xfeACQUIRE-A", b"\xfdACQUIRE-B"]
            acquisition_arrivals = [None, None]
            for source, c in enumerate(configs):
                sender.sendto(
                    acquisition_payloads[source], (c.endpoint.bind_host, c.endpoint.bind_port)
                )
            while time.monotonic() - ready < 10 and any(v is None for v in acquisition_arrivals):
                for dest, sock in enumerate(outputs):
                    try:
                        data = sock.recv(65535)
                    except BlockingIOError:
                        continue
                    if data == acquisition_payloads[1 - dest]:
                        acquisition_arrivals[dest] = time.monotonic() - ready
                time.sleep(0.002)
            report["acquisition"] = {
                "seconds_after_both_hardware_ready": acquisition_arrivals,
                "passed": all(v is not None and v <= 10 for v in acquisition_arrivals),
            }
            if not report["acquisition"]["passed"]:
                raise RuntimeError("initial bidirectional acquisition exceeded 10 seconds")
            time.sleep(3)
            for sock in outputs:
                while True:
                    try:
                        sock.recv(65535)
                    except BlockingIOError:
                        break
            for peer in peers:
                request(peer, "clear_audit")
            report["before"] = [request(p, "snapshot") for p in peers]
            sent = [{}, {}]
            received = [{}, {}]
            duplicates = [0, 0]
            changed = [0, 0]
            latencies = [[], []]
            tiny_udp = [[], []]

            def drain():
                for dest, sock in enumerate(outputs):
                    while True:
                        try:
                            data = sock.recv(65535)
                        except BlockingIOError:
                            break
                        if args.payload < 8:
                            tiny_udp[dest].append((data, time.monotonic()))
                            continue
                        seq = int.from_bytes(data[:8], "big")
                        source = 0 if args.echo else 1 - dest
                        expected = sent[source].get(seq)
                        if expected is None or data != expected[0]:
                            changed[dest] += 1
                        elif seq in received[dest]:
                            duplicates[dest] += 1
                        else:
                            received[dest][seq] = data
                            latencies[dest].append(time.monotonic() - expected[1])
                            if args.echo and dest == 1:
                                c = configs[1]
                                sender.sendto(data, (c.endpoint.bind_host, c.endpoint.bind_port))

            def inject(seq):
                for source, c in enumerate(configs[:1] if args.echo else configs):
                    width = min(8, args.payload)
                    data = (seq % (1 << (8 * width))).to_bytes(width, "big") + bytes(
                        (j + seq + source) % 256 for j in range(args.payload - width)
                    )
                    sent[source][seq] = (data, time.monotonic())
                    sender.sendto(data, (c.endpoint.bind_host, c.endpoint.bind_port))

            begun = time.monotonic()
            count = int(args.duration * args.rate)
            seq = 0
            next_log = begun + 10
            while seq < count or time.monotonic() - begun < args.duration:
                now = time.monotonic()
                if seq < count and now >= begun + seq / args.rate:
                    inject(seq)
                    seq += 1
                drain()
                if now >= next_log:
                    print(
                        json.dumps(
                            {
                                "seconds": round(now - begun, 1),
                                "sent_each": seq,
                                "received": [
                                    len(r) for r in (tiny_udp if args.payload < 8 else received)
                                ],
                                "counts_verified": args.payload >= 8,
                            }
                        ),
                        flush=True,
                    )
                    next_log = now + 10
                time.sleep(0.001)
            until = time.monotonic() + 5
            while time.monotonic() < until:
                drain()
                time.sleep(0.002)
            report["after"] = [request(p, "snapshot") for p in peers]
            if args.payload < 8:
                drain()
                for dest in range(2):
                    received[dest], latencies[dest], duplicates[dest], changed[dest] = (
                        correlate_tiny_payloads(
                            sent[1 - dest],
                            report["after"][1 - dest]["packet_audit"]["tx"],
                            report["after"][dest]["packet_audit"]["rx"],
                            tiny_udp[dest],
                        )
                    )
            report["traffic"] = {
                "sent_each": seq,
                "received": [len(r) for r in received],
                "missing_sequences_by_destination": [
                    sorted(set(sent[0 if args.echo else 1 - dest]) - set(received[dest]))
                    for dest in range(2)
                ],
                "delivered_payload_bps": [
                    len(r) * args.payload * 8 / args.duration for r in received
                ],
                "duplicates": duplicates,
                "changed_or_unexpected": changed,
                "one_way_same_host_p95_seconds": [
                    sorted(v)[max(0, math.ceil(0.95 * len(v)) - 1)] if v else None
                    for v in latencies
                ],
            }
            if args.echo:
                report["traffic"]["round_trip_p95_seconds"] = report["traffic"][
                    "one_way_same_host_p95_seconds"
                ][0]
                report["traffic"].pop("one_way_same_host_p95_seconds")
                rtt = report["traffic"]["round_trip_p95_seconds"]
                report["latency_passed"] = rtt is not None and rtt <= 1.0
            report["stream_deltas"] = [
                {
                    k: after[k] - before[k]
                    for k in [
                        "rx_overflows",
                        "rx_buffer_drops",
                        "rx_stream_errors",
                        "decoder_output_drops",
                        "tx_timeouts",
                        "tx_underflows",
                        "tx_stream_errors",
                        "timed_transmit_late",
                        "retries",
                        "retry_exhausted",
                    ]
                }
                for before, after in zip(report["before"], report["after"])
            ]
            report["stream_passed"] = all(
                delta[key] == 0
                for delta in report["stream_deltas"]
                for key in (
                    "rx_overflows",
                    "rx_buffer_drops",
                    "rx_stream_errors",
                    "decoder_output_drops",
                    "tx_timeouts",
                    "tx_underflows",
                    "tx_stream_errors",
                    "timed_transmit_late",
                )
            )
            report["cpu_seconds_during_workload_and_grace"] = [
                after["process_cpu_seconds"] - before["process_cpu_seconds"]
                for before, after in zip(report["before"], report["after"])
            ]
            if args.interrupt_seconds:
                for peer in peers:
                    request(peer, "mute")
                time.sleep(args.interrupt_seconds)
                muted_state = [request(peer, "snapshot") for peer in peers]
                for peer in peers:
                    request(peer, "unmute")
                restored = time.monotonic()
                inject(seq)
                while time.monotonic() - restored < 5 and not all(seq in r for r in received):
                    drain()
                    time.sleep(0.002)
                report["interruption"] = {
                    "method": "zero-valued TX IQ on both SDRs; RX streams remain active",
                    "seconds": args.interrupt_seconds,
                    "follower_recovering_before_restore": muted_state[1].get("recovering"),
                    "seconds_after_restore": time.monotonic() - restored,
                    "both_directions_delivered": all(seq in r for r in received),
                }
                seq += 1
            if args.restart_follower:
                request(peers[1], "stop")
                peers[1] = None
                peers[1] = start(configs[1])
                ready = time.monotonic()
                inject(seq)
                until = ready + 5
                while time.monotonic() < until and not all(seq in r for r in received):
                    drain()
                    time.sleep(0.002)
                report["restart"] = {
                    "both_directions_delivered": all(seq in r for r in received),
                    "seconds_after_ready": time.monotonic() - ready,
                }
            report["traffic_passed"] = all(
                n >= 0.99 * report["traffic"]["sent_each"] for n in report["traffic"]["received"]
            ) and not any(duplicates + changed)
            report["shutdown"] = []
            for i, peer in enumerate(peers):
                if peer is not None:
                    report["shutdown"].append(request(peer, "stop"))
                    peers[i] = None
            report["shutdown_passed"] = True
    except BaseException as exc:
        report["errors"].append(repr(exc))
        raise
    finally:
        for peer in peers:
            if peer is not None:
                process, pipe = peer
                if process.is_alive():
                    try:
                        request(peer, "stop")
                    except Exception:
                        process.terminate()
                        process.join(3)
                pipe.close()
        for sock in outputs:
            sock.close()
        if sender is not None:
            sender.close()
        report["finished_unix_seconds"] = time.time()
        if args.output is not None:
            args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in ("settings", "before", "after")},
                indent=2,
            ),
            flush=True,
        )

    return (
        0
        if (
            report.get("shutdown_passed")
            and report.get("traffic_passed")
            and report.get("stream_passed")
            and report.get("latency_passed", True)
            and report.get("interruption", {}).get("both_directions_delivered", True)
            and report.get("restart", {}).get("both_directions_delivered", True)
        )
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
