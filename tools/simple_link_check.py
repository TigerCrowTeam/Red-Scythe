"""Check ACK-free simple 2FSK on an attenuated two-endpoint UHD bench.

Loads schema-2 rate profiles directly, with schema-1 development API compatibility. Runs each
physical direction sequentially, verifies payloads and lack of control traffic,
and checks submitted IQ bytes and device-time burst spacing. Prints final
summaries only; creates no result files. This is a short integration check, not
the five-minute sustained qualification defined in the emulation contract.
"""

from __future__ import annotations

import argparse
import threading
import time
from dataclasses import replace

import numpy as np

from packet_radio.app import RadioApp
from packet_radio.config import AuthenticationConfig, LinkConfig, RecordingConfig, load_config
from packet_radio.endpoint import MemoryEndpoint
from packet_radio.hardware.shared import SharedRadios
from packet_radio.protocol.simple_link import SimpleLinkSettings, SimpleProfile

STREAM_ERRORS = (
    "rx_overflows",
    "rx_buffer_drops",
    "rx_stream_errors",
    "decoder_output_drops",
    "tx_timeouts",
    "tx_underflows",
    "tx_stream_errors",
    "timed_transmit_late",
    "transmit_errors",
    "transmit_drops",
)


def run_direction(
    tx_path: str,
    rx_path: str,
    count: int,
    interval: float,
    framed=False,
    interrupt_seconds=0,
    shared_device=False,
    timing_diagnostics=False,
):
    shared = None
    configs = [load_config(tx_path), load_config(rx_path)]
    apps = []
    threads = []
    stop = threading.Event()
    failures = []
    submissions = []
    muted = threading.Event()
    lost_submissions = []
    recovery_seconds = None
    injection_times = []
    arrival_times = []
    max_queue = [0, 0]

    def record_queue(index, app):
        worker = app.decoder_worker
        if worker is not None:
            max_queue[index] = max(max_queue[index], worker.diagnostics()["decoder_queue_depth"])

    def run(app, index):
        try:
            while not stop.is_set():
                app.step()
                record_queue(index, app)
        except BaseException as exc:
            failures.append(exc)
            stop.set()

    try:
        if shared_device:
            shared = SharedRadios(configs)
        for i, (cfg, mode) in enumerate(zip(configs, ("transmit-only", "receive-only"))):
            sdr = None if shared is None else shared.radios[i]
            input_kind = "frame" if framed and mode == "transmit-only" else "payload"
            if cfg.schema_version == 2:
                if cfg.packet_format != "simple-frame-v1":
                    raise ValueError("this check requires the frozen simple-frame-v1 profile")
                cfg = replace(cfg, operation=mode, input_kind=input_kind)
                # Resolve all framing/protection settings from YAML, with no injected profile.
                app = RadioApp(
                    cfg,
                    endpoint=MemoryEndpoint(),
                    sdr=sdr,
                    timing_diagnostics=timing_diagnostics,
                )
            else:
                cfg = replace(
                    cfg,
                    authentication=AuthenticationConfig(),
                    recording=RecordingConfig(),
                    link=LinkConfig(hardware_timed_io=True, max_retries=0),
                )
                app = RadioApp(
                    cfg,
                    timing_diagnostics=timing_diagnostics,
                    endpoint=MemoryEndpoint(),
                    sdr=sdr,
                    processing=SimpleProfile(
                        link=SimpleLinkSettings(
                            operation=mode,
                            input_kind=input_kind,
                        )
                    ),
                )
            apps.append(app)
            app._retune()
            thread = threading.Thread(target=run, args=(app, len(apps) - 1), daemon=True)
            threads.append(thread)
            thread.start()
        tx, rx = apps
        deliver = rx.endpoint.send

        def record_arrival(payload):
            arrival_times.append(time.monotonic())
            deliver(payload)

        rx.endpoint.send = record_arrival
        submit = tx.sdr.transmit_with_lead

        def capture(samples, lead_ns):
            if muted.is_set():
                target = submit(np.zeros_like(samples), lead_ns)
                lost_submissions.append(target)
                return target
            target = submit(samples, lead_ns)
            # Rectangular 2FSK: decode the submitted phase increments independently.
            phases = np.angle(samples[1:] * np.conj(samples[:-1]))
            sps = tx.config.sdr.samples_per_symbol
            bits = (phases[sps // 2 - 1 :: sps] > 0).astype(np.uint8)
            burst = np.packbits(bits, bitorder="big").tobytes()
            submissions.append((target, len(samples), burst))
            return target

        tx.sdr.transmit_with_lead = capture
        time.sleep(0.5)
        baseline = [app.statistics.snapshot() for app in apps]
        payloads = [i.to_bytes(4, "big") + bytes(range(252)) for i in range(count)]
        # Boundary and repeated-payload checks follow the steady traffic.
        payloads += [b"\x00", bytes(range(64)), bytes(range(256)) * 4, b"same", b"same"]
        cpu_start = time.process_time()
        start = time.monotonic()
        for i, payload in enumerate(payloads):
            remaining = start + i * interval - time.monotonic()
            if remaining > 0:
                stop.wait(remaining)
            if failures:
                raise RuntimeError(f"worker failed: {failures}")
            injection_times.append(time.monotonic())
            tx.endpoint.inject(tx.codec.encode(payload) if framed else payload)
        deadline = time.monotonic() + 5
        while (
            len(rx.endpoint.inbound) < len(payloads)
            and time.monotonic() < deadline
            and not failures
        ):
            stop.wait(0.01)
        if interrupt_seconds:
            assert list(rx.endpoint.inbound) == payloads, "pre-interruption delivery failed"
            muted.set()
            lost = b"intentional signal loss"
            tx.endpoint.inject(tx.codec.encode(lost) if framed else lost)
            stop.wait(interrupt_seconds)
            assert len(lost_submissions) == 1, "muted burst was not submitted exactly once"
            assert list(rx.endpoint.inbound) == payloads, "muted packet unexpectedly delivered"
            muted.clear()
            restored = time.monotonic()
            recovered = b"recovery after signal loss"
            payloads.append(recovered)
            injection_times.append(time.monotonic())
            tx.endpoint.inject(tx.codec.encode(recovered) if framed else recovered)
            while (
                len(rx.endpoint.inbound) < len(payloads)
                and time.monotonic() - restored < 5
                and not failures
            ):
                stop.wait(0.002)
            recovery_seconds = time.monotonic() - restored
            assert len(lost_submissions) == 1
        stop.set()
        for thread in threads:
            thread.join(2)
        if any(thread.is_alive() for thread in threads) or failures:
            raise RuntimeError(f"worker did not stop cleanly: {failures}")
        delivered = list(rx.endpoint.inbound)
        if delivered != payloads:
            for app in apps:
                snapshot = app.statistics.snapshot()
                names = (
                    *STREAM_ERRORS,
                    "packets_transmitted",
                    "packets_received",
                    "candidate_rejections",
                    "decoder_timestamp_discontinuities",
                )
                print({name: snapshot[name] for name in names}, flush=True)
            print(
                "Received lengths/prefixes:", [(len(p), p[:4].hex()) for p in delivered], flush=True
            )
        assert delivered == payloads, f"payload delivery mismatch: {len(delivered)}/{len(payloads)}"
        assert len(arrival_times) == len(injection_times) == len(payloads)
        # Steady 256-byte workload only; boundaries and outage recovery are separate.
        latencies_ms = [
            (arrived - injected) * 1000
            for injected, arrived in zip(injection_times[:count], arrival_times[:count])
        ]
        stats = [app.statistics.snapshot() for app in apps]
        for snapshot, before in zip(stats, baseline):
            errors = {
                name: snapshot[name] - before[name]
                for name in STREAM_ERRORS
                if snapshot[name] != before[name]
            }
            assert not errors, f"stream errors: {errors}"
            assert snapshot["retries"] == snapshot["acknowledgements_sent"] == 0
        assert stats[0]["packets_received"] == stats[1]["packets_transmitted"] == 0
        assert stats[1]["samples_transmitted"] == 0
        assert len(submissions) == len(payloads)
        for (_, samples, burst), payload in zip(submissions, payloads):
            wire = tx.codec.encode(payload)
            assert burst == b"\x55" * 32 + bytes.fromhex("a7c35a9e") + wire
            assert samples == (40 + len(payload)) * 8 * tx.config.sdr.samples_per_symbol
        gaps = [
            (b[0] - a[0]) / 1e9 - a[1] / tx.config.sdr.sample_rate
            for a, b in zip(submissions, submissions[1:])
        ]
        assert min(gaps) >= 0.002, f"burst gap below 2ms: {min(gaps)}"
        print(
            f"{tx.config.sdr.device} channel {tx.config.sdr.channel} -> "
            f"{rx.config.sdr.device} channel {rx.config.sdr.channel}: "
            f"{len(delivered)}/{len(payloads)} exact; "
            f"input={'frame' if framed else 'payload'}; no control/retry traffic; "
            f"RX-only TX samples=0; minimum device-target gap={min(gaps) * 1000:.3f} ms; "
            f"submitted bytes/sample counts exact; stream errors=0; rate={tx.config.sdr.bit_rate_bps:g}; "
            f"offered steady payload bps={256 * 8 / interval:g}; "
            f"steady one-way latency min/p95/max ms={min(latencies_ms):.3f}/"
            f"{np.percentile(latencies_ms, 95):.3f}/{max(latencies_ms):.3f}; "
            f"CPU seconds={time.process_time() - cpu_start:.3f}; elapsed seconds={time.monotonic() - start:.3f}; "
            f"sampled max decoder queue batches={max(max_queue)}; "
            f"RX decoder p95/max ms={stats[1]['decoder_processing_p95_ms']:.3f}/"
            f"{stats[1]['decoder_processing_max_ms']:.3f}; "
            f"RX realtime utilization={stats[1]['decoder_realtime_utilization']:.4f}; "
            f"muted packets={len(lost_submissions)}; signal outage={interrupt_seconds:g} s; "
            f"recovery seconds={recovery_seconds}",
            flush=True,
        )
        if timing_diagnostics:
            print("RX timing:", stats[1].get("timing_diagnostics", {}), flush=True)
    finally:
        stop.set()
        for thread in threads:
            thread.join(2)
        for app in apps:
            app.close()
            assert app.statistics.snapshot()["shutdown_errors"] == 0
        if shared is not None:
            shared.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profiles",
        nargs=2,
        default=["configs/bench/peer-a-2fsk.yaml", "configs/bench/peer-b-2fsk.yaml"],
    )
    parser.add_argument("--shared-device", action="store_true")
    parser.add_argument("--timing-diagnostics", action="store_true")
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument(
        "--interrupt-seconds",
        type=float,
        default=0,
        help="mute one TX packet and check signal recovery",
    )
    args = parser.parse_args()
    if args.count < 1 or not np.isfinite(args.interval) or args.interval < 0.05:
        parser.error("count must be positive and interval must be finite and at least .05 s")
    if not np.isfinite(args.interrupt_seconds) or args.interrupt_seconds < 0:
        parser.error("interruption must be finite and nonnegative")
    run_direction(
        *args.profiles,
        args.count,
        args.interval,
        interrupt_seconds=args.interrupt_seconds,
        shared_device=args.shared_device,
        timing_diagnostics=args.timing_diagnostics,
    )
    run_direction(
        *reversed(args.profiles),
        args.count,
        args.interval,
        framed=True,
        interrupt_seconds=args.interrupt_seconds,
        shared_device=args.shared_device,
        timing_diagnostics=args.timing_diagnostics,
    )
    print("Both directions passed, including device close/reopen and clean shutdown.")


if __name__ == "__main__":
    main()
