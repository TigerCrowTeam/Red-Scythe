"""Measure conducted simple-2FSK hopping on two USRPs or one shared X310.

Runs the real application in both directions, checks exact payloads on every
channel, boundary rejection and recovery after an intentionally off-channel RX.
Prints summaries only. Default duration is five minutes plus boundary/recovery
checks. The radios must have crossed TX/RX-to-RX2 cables with 30 dB attenuation.
Timing observations use host UTC and device timestamps, not an RF instrument.
The optional 2 hop/s run selects the separate X310 profile; 1 hop/s stays default.
Experimental rates use an in-memory schedule override after normal initialization,
queue maximum-size packets early on both channels, and never change supported
profiles or GUI limits. They measure this application's limits, not hardware limits.
"""

from __future__ import annotations

import argparse
import logging
import math
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from packet_radio.app import RadioApp
from packet_radio.config import load_config
from packet_radio.endpoint import MemoryEndpoint
from packet_radio.hardware.shared import SharedRadios
from packet_radio.scheduler import FrequencyScheduler

ERRORS = (
    "rx_overflows",
    "rx_buffer_drops",
    "rx_stream_errors",
    "decoder_output_drops",
    "tx_timeouts",
    "tx_underflows",
    "tx_stream_errors",
    "timed_transmit_late",
    "transmit_errors",
)


def run(
    seconds: int,
    serials: list[str],
    first_phase: float = 0.2,
    shared_address=None,
    hop_rate: int = 1,
    *,
    experimental_rate: float | None = None,
):
    if hop_rate not in (1, 2) or (hop_rate == 2 and not shared_address):
        raise ValueError("2 hops/s requires the shared X310")
    if experimental_rate is not None:
        if (
            not shared_address
            or not math.isfinite(experimental_rate)
            or not 1 <= experimental_rate <= 4
        ):
            raise ValueError("experimental rate requires a shared X310 and 1..4 hops/s")
        hop_rate = experimental_rate
    dwell, guard = 1 / hop_rate, 0.1
    second_phase = max(0.3, dwell / 2)
    if not math.isfinite(first_phase) or not 0.15 <= first_phase < second_phase - 0.05:
        raise ValueError("first phase must leave time for acquisition and the second packet")
    if experimental_rate is not None:
        # Queue before the post-tune wait expires. Production admission still
        # determines when each packet can actually be modulated and transmitted.
        first_phase = second_phase = guard
    if type(seconds) is not int or seconds < 6 or len(serials) != 2:
        raise ValueError("need at least six seconds and two serial placeholders")
    slots = math.ceil(seconds * hop_rate)
    seconds = slots * dwell

    root = Path(__file__).resolve().parents[1]
    apps, threads, failures = [], [], []
    shared = None
    stop = threading.Event()
    pauses = [threading.Event(), threading.Event()]
    tunes = [[], []]
    submissions = [[], []]
    received = [[], []]
    first_blocks = [[], []]
    awaiting_block = [False, False]
    awaiting_valid = [False, False]
    valid_blocks = [[], []]
    stages = [{"stop/drain": [], "RF control": [], "restart command": []} for _ in range(2)]

    def worker(index, app):
        try:
            while not stop.is_set():
                if pauses[index].is_set():
                    stop.wait(0.001)
                else:
                    app.step()
        except BaseException as exc:
            failures.append(repr(exc))
            stop.set()

    def wait_until(target):
        while time.time() < target:
            if failures:
                raise RuntimeError(failures)
            stop.wait(min(0.01, target - time.time()))

    def send(index, payload, target):
        wait_until(target)
        apps[index].endpoint.inject(payload)

    configs = []
    for i, serial in enumerate(serials):
        suffix = "x310-hopping" if hop_rate > 1 else "hopping"
        cfg = load_config(root / f"configs/v1_1/peer-{'ab'[i]}-{suffix}.yaml")
        configs.append(
            replace(
                cfg,
                operation="bidirectional",
                sdr=replace(
                    cfg.sdr,
                    device={"type": "x300", "addr": shared_address}
                    if shared_address
                    else {"type": "b200", "serial": serial},
                    channel=i if shared_address else 0,
                    tx_gain=0 if shared_address else 20,
                    rx_gain=0 if shared_address else 40,
                ),
            )
        )
    try:
        if shared_address:
            shared = SharedRadios(configs)
        for i, cfg in enumerate(configs):
            app = RadioApp(
                cfg, endpoint=MemoryEndpoint(), sdr=None if shared is None else shared.radios[i]
            )
            apps.append(app)
            if experimental_rate is not None:
                # Deliberately isolated benchmark override: do not weaken profile
                # validation or save this unqualified schedule as a normal config.
                app.config = replace(
                    app.config,
                    frequency=replace(app.config.frequency, interval_seconds=dwell),
                )
                app.scheduler = FrequencyScheduler(app.config.frequency, clock=app.wall_clock)
            raw = app.sdr.radio
            stopped_at = [None]
            original_stop, original_start = raw._stop_receive, raw._start_receive

            def stop_rx(original=original_stop, i=i, stopped_at=stopped_at):
                before = time.monotonic()
                original()
                stopped_at[0] = time.monotonic()
                stages[i]["stop/drain"].append((time.time(), (stopped_at[0] - before) * 1000))

            def start_rx(*args, original=original_start, i=i, stopped_at=stopped_at, **kwargs):
                before = time.monotonic()
                if stopped_at[0] is not None:
                    stages[i]["RF control"].append((time.time(), (before - stopped_at[0]) * 1000))
                original(*args, **kwargs)
                stages[i]["restart command"].append(
                    (time.time(), (time.monotonic() - before) * 1000)
                )

            raw._stop_receive, raw._start_receive = stop_rx, start_rx
            original_feed = app.rx_modem.feed_timed

            def feed(samples, timestamp, original=original_feed, i=i):
                if awaiting_valid[i] and len(samples):
                    valid_blocks[i].append((time.time(), timestamp))
                    awaiting_valid[i] = False
                return original(samples, timestamp)

            app.rx_modem.feed_timed = feed
            original_tune = app.sdr.tune
            original_tx = app.sdr.transmit_with_lead
            original_send = app.endpoint.send
            original_receive = app.sdr.receive_timed

            def tune(freq, original=original_tune, i=i):
                start = time.time()
                original(freq)
                tunes[i].append((start, time.time(), freq))
                awaiting_block[i] = True
                awaiting_valid[i] = True

            def transmit(samples, lead, original=original_tx, i=i, app=app):
                start = time.time()
                device_now = app.sdr.hardware_time()
                read_finished = time.time()
                target = original(samples, lead)
                target_wall = start + (target - device_now) / 1e9
                submissions[i].append(
                    (start, target_wall, len(samples), app.current_frequency, read_finished - start)
                )
                return target

            def receive(*args, original=original_receive, i=i, **kwargs):
                block = original(*args, **kwargs)
                if awaiting_block[i] and len(block.samples):
                    first_blocks[i].append((time.time(), block.time_ns))
                    awaiting_block[i] = False
                return block

            app.sdr.receive_timed = receive

            def deliver(payload, original=original_send, i=i):
                received[i].append((time.time(), payload))
                original(payload)

            app.sdr.tune, app.sdr.transmit_with_lead, app.endpoint.send = tune, transmit, deliver
            thread = threading.Thread(target=worker, args=(i, app), daemon=True)
            threads.append(thread)
            thread.start()
        start = math.ceil((time.time() + 2) / dwell) * dwell
        print(
            f"{'EXPERIMENTAL' if experimental_rate is not None else 'QUALIFICATION'}: "
            f"{hop_rate:g} hops/s, dwell={dwell * 1000:.3f} ms, "
            f"guard={guard * 1000:.0f} ms; {slots} steady packets each direction; "
            f"payload={1024 if experimental_rate is not None else 256} bytes",
            flush=True,
        )
        wait_until(start - 0.1)
        baseline = [a.statistics.snapshot() for a in apps]
        expected = [[], []]
        # One exact packet each direction on every dwell; experiments stress the
        # maximum payload and queue it before acquisition has necessarily finished.
        for slot in range(slots):
            for i, phase in ((0, first_phase), (1, second_phase)):
                payload = bytes([i]) + slot.to_bytes(4, "big") + bytes(range(251))
                if experimental_rate is not None:
                    payload += bytes(1024 - len(payload))
                expected[1 - i].append(payload)
                send(i, payload, start + slot * dwell + phase)
        wait_until(start + seconds - 0.05)
        steady = [a.statistics.snapshot() for a in apps]
        for i in range(2):
            counters = {
                k: steady[i][k] - baseline[i][k]
                for k in (
                    *ERRORS,
                    "hop_burst_rejections",
                    "hop_slots_missed",
                    "hop_tune_deadlines_missed",
                )
                if steady[i][k] != baseline[i][k]
            }
            usable = [t % dwell * 1000 for t, _ in valid_blocks[i] if start <= t < start + seconds]
            print(
                f"channel {i}: steady RX={len(apps[i].endpoint.inbound)}/{slots}; "
                f"counters={counters}; decoder-ready max={max(usable, default=math.nan):.3f} ms",
                flush=True,
            )
        for i in range(2):
            assert list(apps[i].endpoint.inbound) == expected[i], (
                i,
                len(apps[i].endpoint.inbound),
                len(expected[i]),
            )
            for timestamp, payload in received[i]:
                if payload in expected[i]:
                    slot = int.from_bytes(payload[1:5], "big")
                    assert start + slot * dwell <= timestamp < start + (slot + 1) * dwell, (
                        "packet delivered outside its intended dwell",
                        i,
                        slot,
                    )
        # One-minute payload boundaries, both directions, and deliberately too-late bursts.
        boundary_slots = math.ceil((60 if seconds >= 300 else 6) * hop_rate)
        for slot in range(boundary_slots):
            size = (1, 64, 1024)[slot % 3]
            for i, phase in ((0, first_phase), (1, second_phase)):
                payload = bytes([(2 * slot + i) % 256]) * size
                expected[1 - i].append(payload)
                send(i, payload, start + seconds + slot * dwell + phase)
            # Only 20 ms remain before the trailing guard: neither maximum burst fits.
            for i in range(2):
                send(i, b"late" * 256, start + seconds + slot * dwell + dwell - guard - 0.02)
        end = start + seconds + boundary_slots * dwell
        wait_until(end - 0.02)
        for i in range(2):
            assert list(apps[i].endpoint.inbound) == expected[i], (
                "boundary delivery mismatch",
                i,
                len(apps[i].endpoint.inbound),
                len(expected[i]),
                apps[i].statistics.snapshot()["hop_burst_rejections"],
            )
        assert all(a.statistics.snapshot()["hop_burst_rejections"] == boundary_slots for a in apps)
        # Hold peer B on an unrelated channel for 3 dwells. No physical reconnection needed.
        pauses[1].set()
        time.sleep(0.05)
        apps[1].decoder_worker.retune(lambda: apps[1].sdr.tune(920e6), 0.1)
        for slot in range(3):
            send(0, b"expected loss", end + slot * dwell + first_phase)
        wait_until(end + 3 * dwell + 0.02)
        pauses[1].clear()
        for slot in range(3):
            for i, phase in ((0, first_phase), (1, second_phase)):
                payload = b"recovered" + bytes([i, slot])
                expected[1 - i].append(payload)
                # Resumption starts 20 ms into the dwell. Allow the interrupted
                # receiver its full tune/settle time before either peer sends.
                recovery_phase = 0.16 if experimental_rate is not None else phase
                send(i, payload, end + (3 + slot) * dwell + recovery_phase)
        wait_until(end + 6 * dwell)
        assert not failures, failures
        for i, app in enumerate(apps):
            assert list(app.endpoint.inbound) == expected[i], "interruption recovery mismatch"
            stats = app.statistics.snapshot()
            errors = {k: stats[k] - baseline[i][k] for k in ERRORS if stats[k] != baseline[i][k]}
            assert not errors, errors
            assert stats["acknowledgements_sent"] == stats["retries"] == 0
            assert steady[i]["hop_slots_missed"] == baseline[i]["hop_slots_missed"]
            assert (
                steady[i]["hop_tune_deadlines_missed"] == baseline[i]["hop_tune_deadlines_missed"]
            )
            measured = [(b - a) * 1000 for a, b, _ in tunes[i] if start <= a < start + seconds]
            phases = [(b % dwell) * 1000 for a, b, _ in tunes[i] if start <= a < start + seconds]
            tx = [x for x in submissions[i] if start <= x[0] < end + 6 * dwell]
            for _, target, samples, freq, uncertainty in tx:
                assert 0.1 <= target % dwell
                assert target % dwell + uncertainty + (samples + 64) / 2e6 + 0.001 <= dwell - guard
                assert freq == app.scheduler.state(target).frequency_hz
            block_phases = [
                t % dwell * 1000 for t, _ in first_blocks[i] if start <= t < start + seconds
            ]
            valid_phases = [
                t % dwell * 1000 for t, _ in valid_blocks[i] if start <= t < start + seconds
            ]

            def summary(values):
                assert values, "missing timing observations"
                return (
                    f"min/p95/max/std={min(values):.3f}/"
                    f"{np.percentile(values, 95):.3f}/{max(values):.3f}/{np.std(values):.3f} ms"
                )

            for name, observations in stages[i].items():
                print(
                    f"channel {i} {name}: {summary([v for t, v in observations if start <= t < start + seconds])}",
                    flush=True,
                )
            print(f"channel {i} restart completion phase: {summary(phases)}", flush=True)
            print(f"channel {i} first buffered RX phase: {summary(block_phases)}", flush=True)
            print(f"channel {i} first decoder-usable RX phase: {summary(valid_phases)}", flush=True)
            delivery_phases = [
                t % dwell * 1000 for t, _ in received[i] if start <= t < start + seconds
            ]
            print(f"channel {i} steady delivery phase: {summary(delivery_phases)}", flush=True)
            label = f"{shared_address} channel {i}" if shared_address else serials[i]
            print(
                f"{label}: {hop_rate} hops/s; {slots}/{slots} sustained RX packets, "
                f"{len(expected[i])}/{len(expected[i])} total exact; "
                f"tune ms min/p95/max={min(measured):.3f}/{np.percentile(measured, 95):.3f}/{max(measured):.3f}; "
                f"tune std={np.std(measured):.3f} ms; "
                f"TX clock mapping max bracket={max(x[4] for x in tx) * 1000:.3f} ms; "
                f"latest restart phase={max(phases):.3f} ms; "
                f"first buffered RX block phase min/max={min(block_phases):.3f}/{max(block_phases):.3f} ms; "
                f"first successful TX target phase={min(x[1] % dwell for x in tx) * 1000:.3f} ms; "
                f"first delivery phase={min(t % dwell for t, p in received[i] if start <= t < start + seconds) * 1000:.3f} ms; "
                f"stream errors=0; missed steady hops/deadlines=0; "
                f"boundary rejects={stats['hop_burst_rejections']}; "
                f"recovery missed slots={stats['hop_slots_missed']}",
                flush=True,
            )
        print(
            "DELIVERY PASS: same-host UTC hopping; off-channel interruption recovered; no ACK/SYNC/retry traffic.",
            flush=True,
        )
    finally:
        stop.set()
        for thread in threads:
            thread.join(3)
        for app in apps:
            app.close()
        if shared is not None:
            shared.close()
        assert all(not t.is_alive() for t in threads), "application thread did not stop"
        assert all(a.statistics.snapshot()["shutdown_errors"] == 0 for a in apps), "shutdown error"
    print("PASS: delivery, boundaries, recovery and shutdown.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-address", help="one X310 address, using channels 0 and 1")
    parser.add_argument("--hop-rate", type=int, choices=(1, 2), default=1)
    parser.add_argument(
        "--experimental-rate",
        type=float,
        help="shared X310 only: test 1..4 hops/s with 1024-byte early-queued packets; unqualified",
    )
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--serials", nargs=2, default=["3457464", "3457480"])
    parser.add_argument(
        "--first-phase",
        type=float,
        default=0.2,
        help="first directional packet phase in seconds within each dwell",
    )
    args = parser.parse_args()
    if args.seconds < 6:
        parser.error("seconds must be at least 6")
    if (
        not math.isfinite(args.first_phase)
        or not 0.15 <= args.first_phase < max(0.3, 0.5 / args.hop_rate) - 0.05
    ):
        parser.error("first phase must leave acquisition time and at least 50 ms before peer B")
    logging.basicConfig(level=logging.ERROR)
    if args.hop_rate == 2 and not args.shared_address:
        parser.error("2 hops/s requires --shared-address")
    run(
        args.seconds,
        args.serials,
        args.first_phase,
        args.shared_address,
        args.hop_rate,
        experimental_rate=args.experimental_rate,
    )


if __name__ == "__main__":
    main()
