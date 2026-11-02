"""Measure conducted simple-frame hopping on two USRPs or one shared X310.

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
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np

from packet_radio.app import RadioApp
from packet_radio.config import load_config, validate_network
from packet_radio.endpoint import MemoryEndpoint
from packet_radio.hardware.shared import SharedRadios
from packet_radio.modems.registry import WAVEFORMS
from packet_radio.profiles import load_v2, read_yaml
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


def experiment_timing(rate: float, guard: float, shared_address: str | None) -> tuple[float, float]:
    """Bound a test-only sweep and reject schedules that cannot fit a maximum burst.

    The frozen 250 kbit/s profile needs 34.048 ms on air, 10 ms TX lead,
    32 us transport idle and 1 ms drain margin. This necessary fit check does
    not predict retune/settling performance; conducted tests must establish it.
    Experimental recovery resumes 1 ms into a dwell and allows 40 ms for
    retuning/scheduling in addition to the selected settling guard.
    """
    if not shared_address or not math.isfinite(rate) or not 1 <= rate <= 8:
        raise ValueError("experimental rate requires a shared X310 and 1..8 hops/s")
    if not math.isfinite(guard) or not 0.025 <= guard <= 0.1:
        raise ValueError("experimental guard must be 0.025..0.1 seconds")
    dwell = 1 / rate
    if dwell <= 2 * guard + 0.045080:
        raise ValueError("experimental dwell cannot fit guards and the maximum packet")
    recovery_phase = guard + 0.04
    if recovery_phase + 0.012664 >= dwell - guard:
        raise ValueError("experimental dwell cannot fit the recovery probe")
    return dwell, recovery_phase


def run(
    seconds: int,
    serials: list[str],
    first_phase: float = 0.2,
    shared_address=None,
    hop_rate: int = 1,
    *,
    experimental_rate: float | None = None,
    experimental_guard: float | None = None,
    automatic_rate: float | None = None,
    automatic_guard: float = 0.03,
    automatic_lead: float = 0.01,
    modulation: str = "2fsk",
    payload_ceiling: int | None = None,
):
    if payload_ceiling is not None and (
        automatic_rate is None
        or type(payload_ceiling) is not int
        or not 5 <= payload_ceiling <= 1024
    ):
        raise ValueError("payload ceiling requires automatic rate and 5..1024 bytes")
    if modulation not in WAVEFORMS:
        raise ValueError("unsupported benchmark modulation")
    if modulation != "2fsk" and automatic_rate is None:
        raise ValueError("other modulations require --automatic-rate")
    if hop_rate not in (1, 2) or (hop_rate == 2 and not shared_address):
        raise ValueError("2 hops/s requires the shared X310")
    dwell, guard = 1 / hop_rate, 0.1
    recovery_phase = None
    if automatic_rate is not None:
        if experimental_rate is not None or experimental_guard is not None:
            raise ValueError("automatic rate cannot be combined with experimental overrides")
        if not shared_address or not math.isfinite(automatic_rate) or automatic_rate <= 0:
            raise ValueError("automatic rate requires a shared X310 and a positive finite rate")
        hop_rate = automatic_rate
        dwell, guard = 1 / hop_rate, automatic_guard
        recovery_phase = guard
    if experimental_guard is not None and experimental_rate is None:
        raise ValueError("experimental guard requires --experimental-rate")
    if experimental_rate is not None:
        guard = 0.1 if experimental_guard is None else experimental_guard
        dwell, recovery_phase = experiment_timing(experimental_rate, guard, shared_address)
        hop_rate = experimental_rate
    second_phase = max(0.3, dwell / 2)
    if not math.isfinite(first_phase) or not 0.15 <= first_phase < second_phase - 0.05:
        raise ValueError("first phase must leave time for acquisition and the second packet")
    if experimental_rate is not None or automatic_rate is not None:
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
    stages = [{"stop/drain": [], "frequency control": [], "restart command": []} for _ in range(2)]

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
        if automatic_rate is not None:
            path = root / f"configs/v1_1/peer-{'ab'[i]}-x310-hopping.yaml"
            node = read_yaml(path)
            definition = read_yaml(path.parent / node["profile"])
            from packet_radio.modems.registry import waveform

            definition["packet"]["frame"].update(
                preamble=waveform(modulation).training.hex(), preamble_repetitions=1
            )
            definition["waveform"]["modulation"] = modulation
            if modulation != "2fsk":
                definition["waveform"].pop("frequency_deviation_hz", None)
            if modulation == "gmsk":
                definition["waveform"].update(gaussian_bt=0.3, gaussian_span_symbols=4)
            definition["packet"]["payload_sizing"] = "automatic"
            if payload_ceiling is not None:
                definition["packet"]["max_payload_bytes"] = payload_ceiling
            node.setdefault("runtime", {})["hardware_timed_tx_lead_seconds"] = automatic_lead
            node["runtime"]["hardware_tx_batch_packets"] = 16
            definition["frequency"].update(dwell_seconds=dwell, guard_seconds=guard)
            definition["qualification"] = "unqualified"
            definition["id"] += "-automatic-custom"
            cfg = load_v2(node, path, definition=definition)
            if cfg.endpoint.max_payload_bytes < 5:
                raise ValueError(
                    "automatic benchmark needs at least 5 payload bytes for probe identities"
                )
            from packet_radio.hop_budget import HOST_MARGIN_SECONDS, payload_airtime

            if (
                recovery_phase
                + payload_airtime(cfg, 5)
                + automatic_lead
                + HOST_MARGIN_SECONDS
                + 0.001032
                >= dwell - guard
            ):
                raise ValueError("automatic benchmark recovery probe cannot fit the dwell")
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
    validate_network(dict(enumerate(configs)))
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
                    frequency=replace(
                        app.config.frequency, interval_seconds=dwell, guard_seconds=guard
                    ),
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
                    stages[i]["frequency control"].append(
                        (time.time(), (before - stopped_at[0]) * 1000)
                    )
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
            original_tx_at = app.sdr.transmit_at
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

            def transmit_at(samples, target, original=original_tx_at, i=i, app=app):
                start = time.time()
                device_now = app.sdr.hardware_time()
                read_finished = time.time()
                original(samples, target)
                submissions[i].append(
                    (
                        start,
                        start + (target - device_now) / 1e9,
                        len(samples),
                        app.current_frequency,
                        read_finished - start,
                    )
                )

            def receive(*args, original=original_receive, i=i, **kwargs):
                block = original(*args, **kwargs)
                if awaiting_block[i] and len(block.samples):
                    first_blocks[i].append((time.time(), block.time_ns))
                    awaiting_block[i] = False
                return block

            app.sdr.receive_timed = receive
            app.sdr.transmit_at = transmit_at

            def deliver(payload, original=original_send, i=i):
                received[i].append((time.time(), payload))
                original(payload)

            app.sdr.tune, app.sdr.transmit_with_lead, app.endpoint.send = tune, transmit, deliver
            thread = threading.Thread(target=worker, args=(i, app), daemon=True)
            threads.append(thread)
            thread.start()
        start = math.ceil((time.time() + 2) / dwell) * dwell
        print(
            f"{'AUTOMATIC' if automatic_rate is not None else 'EXPERIMENTAL' if experimental_rate is not None else 'QUALIFICATION'}: "
            f"{modulation.upper()} at {configs[0].sdr.bit_rate_bps:g} bit/s; "
            f"{hop_rate:g} hops/s, dwell={dwell * 1000:.3f} ms, "
            f"guard={guard * 1000:.0f} ms; {slots} steady packets each direction; "
            f"payload={configs[0].endpoint.max_payload_bytes if automatic_rate is not None else 1024 if experimental_rate is not None else 256} bytes",
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
                if automatic_rate is not None:
                    size = configs[i].endpoint.max_payload_bytes
                    payload = payload[:5] + (bytes(range(251)) * 5)[: size - 5]
                expected[1 - i].append(payload)
                send(i, payload, start + slot * dwell + phase)
        wait_until(start + seconds - 0.001)
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
            expected_set = set(expected[i])
            for timestamp, payload in received[i]:
                if payload in expected_set:
                    slot = int.from_bytes(payload[1:5], "big")
                    assert start + slot * dwell <= timestamp < start + (slot + 1) * dwell, (
                        "packet delivered outside its intended dwell",
                        i,
                        slot,
                    )
        # One-minute payload boundaries, both directions, and deliberately too-late bursts.
        boundary_slots = math.ceil((60 if seconds >= 300 else 6) * hop_rate)
        # Validation/printing after steady traffic takes host time. Start the
        # next phase in the future instead of injecting its first packet late.
        boundary_start = math.ceil((time.time() + 2 * dwell) / dwell) * dwell
        for slot in range(boundary_slots):
            size = min((1, 64, 1024)[slot % 3], configs[0].endpoint.max_payload_bytes)
            for i, phase in ((0, first_phase), (1, second_phase)):
                payload = bytes([(2 * slot + i) % 256]) * size
                expected[1 - i].append(payload)
                send(i, payload, boundary_start + slot * dwell + phase)
        end = boundary_start + boundary_slots * dwell
        wait_until(end - 0.001)
        for i in range(2):
            missing = Counter(expected[i]) - Counter(apps[i].endpoint.inbound)
            assert list(apps[i].endpoint.inbound) == expected[i], (
                "boundary delivery mismatch",
                i,
                len(apps[i].endpoint.inbound),
                len(expected[i]),
                apps[i].statistics.snapshot()["hop_burst_rejections"],
                [(len(payload), payload[:5].hex(), count) for payload, count in missing.items()],
            )
        assert all(a.statistics.snapshot()["hop_burst_rejections"] == 0 for a in apps)
        # Test late admission in separate dwells. Mixing an invalid second input
        # with an in-flight valid burst can defer endpoint polling to the next hop;
        # that input is then valid for the new slot, rather than a late RF attempt.
        late_start = math.ceil((time.time() + dwell) / dwell) * dwell
        late_slots = 12
        for slot in range(late_slots):
            # Leave less than one maximum burst's budget before the trailing guard.
            for i in range(2):
                remaining = 0.02
                if automatic_rate is not None:
                    from packet_radio.hop_budget import HOST_MARGIN_SECONDS, payload_airtime

                    # Leave 1 ms less than the complete admission budget. The
                    # input is still unambiguously late, but the application has
                    # enough time to poll it before the next hop at high rates.
                    remaining = (
                        payload_airtime(configs[i], configs[i].endpoint.max_payload_bytes)
                        + configs[i].link.hardware_timed_tx_lead_seconds
                        + HOST_MARGIN_SECONDS
                        + 0.000032
                    )
                send(
                    i,
                    b"L" * configs[i].endpoint.max_payload_bytes,
                    late_start + slot * dwell + dwell - guard - remaining,
                )
        wait_until(late_start + late_slots * dwell)
        assert all(list(a.endpoint.inbound) == expected[i] for i, a in enumerate(apps))
        assert all(a.statistics.snapshot()["hop_burst_rejections"] == late_slots for a in apps)
        # Hold peer B on an unrelated channel for 3 dwells. No physical reconnection needed.
        pauses[1].set()
        time.sleep(0.05)
        apps[1].decoder_worker.retune(lambda: apps[1].sdr.tune(920e6), guard)
        # Start the outage after setup, on a fresh slot. At high hop rates the
        # pause/tune preparation itself can span multiple dwells.
        end = math.ceil((time.time() + dwell) / dwell) * dwell
        for slot in range(3):
            send(
                0,
                b"expected loss"[: configs[0].endpoint.max_payload_bytes],
                end + slot * dwell + first_phase,
            )
        wait_until(end + 3 * dwell + (0.001 if recovery_phase is not None else 0.02))
        pauses[1].clear()
        for slot in range(3):
            for i, phase in ((0, first_phase), (1, second_phase)):
                payload = b"recovered" + bytes([i, slot])
                if automatic_rate is not None:
                    payload = b"r" + bytes([i, slot]) + b"ok"
                expected[1 - i].append(payload)
                # Queue early; production admission waits for local and peer
                # readiness. A fixed late injection would waste the short dwell.
                send(
                    i,
                    payload,
                    end
                    + (3 + slot) * dwell
                    + (phase if recovery_phase is None else recovery_phase),
                )
        wait_until(end + 6 * dwell)
        assert not failures, failures
        for i, app in enumerate(apps):
            assert list(app.endpoint.inbound) == expected[i], (
                "interruption recovery mismatch",
                i,
                len(app.endpoint.inbound),
                len(expected[i]),
                list(app.endpoint.inbound)[-6:],
                expected[i][-6:],
            )
            settings = app.sdr.radio.actual_settings
            if shared_address:
                assert settings["tuning_mode"] == "fixed-lo-dsp"
                for direction in ("rx", "tx"):
                    assert settings[f"{direction}_rf_tune_requests"] == 1
                    assert settings[f"{direction}_dsp_tune_requests"] > 1
                print(
                    f"channel {i} fixed LO: RX {settings['rx_lo_hz']:g} Hz, "
                    f"TX {settings['tx_lo_hz']:g} Hz; RF requests RX/TX=1/1; "
                    f"DSP requests RX/TX={settings['rx_dsp_tune_requests']}/"
                    f"{settings['tx_dsp_tune_requests']}",
                    flush=True,
                )
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
                assert guard <= target % dwell
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
                if not any(start <= t < start + seconds for t, _ in observations):
                    continue
                print(
                    f"channel {i} {name}: {summary([v for t, v in observations if start <= t < start + seconds])}",
                    flush=True,
                )
            print(f"channel {i} tune completion phase: {summary(phases)}", flush=True)
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
                f"latest tune completion phase={max(phases):.3f} ms; "
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
    parser.add_argument("--modulation", choices=tuple(WAVEFORMS), default="2fsk")
    parser.add_argument("--hop-rate", type=int, choices=(1, 2), default=1)
    parser.add_argument(
        "--automatic-rate",
        type=float,
        help="validate normal automatic payload sizing at this X310 rate, with 30 ms guards",
    )
    parser.add_argument(
        "--experimental-rate",
        type=float,
        help="shared X310 only: test 1..8 hops/s with 1024-byte early-queued packets; unqualified",
    )
    parser.add_argument(
        "--experimental-guard",
        type=float,
        help="test-only settling/trailing guard in seconds (0.025..0.1); requires experimental rate",
    )
    parser.add_argument("--automatic-guard", type=float, default=0.03)
    parser.add_argument("--automatic-lead", type=float, default=0.01)
    parser.add_argument(
        "--payload-ceiling",
        type=int,
        help="cap automatic payload bytes (5..1024) to measure smaller-packet hopping",
    )
    parser.add_argument(
        "--dsp-experiment",
        action="store_true",
        help="test-only automatic DSP sweep: bypass product rate cap; timing reserves still apply",
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
    if args.dsp_experiment:
        if not args.shared_address or args.automatic_rate is None:
            parser.error("DSP experiments require a shared X310 and automatic rate")
        import packet_radio.hop_budget as budget

        budget.MAX_HOP_RATE_HZ = 100.0
    run(
        args.seconds,
        args.serials,
        args.first_phase,
        args.shared_address,
        args.hop_rate,
        experimental_rate=args.experimental_rate,
        experimental_guard=args.experimental_guard,
        automatic_rate=args.automatic_rate,
        automatic_guard=args.automatic_guard,
        automatic_lead=args.automatic_lead,
        modulation=args.modulation,
        payload_ceiling=args.payload_ceiling,
    )


if __name__ == "__main__":
    main()
