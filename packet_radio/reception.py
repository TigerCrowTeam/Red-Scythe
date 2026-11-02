"""Process incoming samples and deliver accepted radio packets.

receive_radio consumes decoder output or IQ from the owning RadioApp, collects
stream and modem diagnostics, and passes decoded bytes to the selected link
behavior. Packet validation, ACKs, replay and delivery belong to that behavior;
RadioApp retains ownership of resources and shared link state.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from packet_radio.app import RadioApp
LOG = logging.getLogger(__name__)


def receive_radio(app: RadioApp) -> bool:
    diagnostics = getattr(app.sdr, "receive_diagnostics", None)
    if diagnostics is not None:
        for name, value in diagnostics().items():
            previous = app._receive_diagnostics.get(name, 0)
            if value > previous:
                app.statistics.increment(name, value - previous)
            app._receive_diagnostics[name] = value
    if not app.behavior.receive_enabled:
        # Drain the backend's RX buffer without decoding or delivering in TX-only mode.
        app.sdr.receive()
        return False
    if app.decoder_worker is not None:
        with app.statistics.timing.work("decoder_status"):
            worker_diagnostics = app.decoder_worker.diagnostics()
        app.channel_power = float(worker_diagnostics.pop("decoder_channel_power"))
        for name, value in worker_diagnostics.items():
            if name in {
                "decoder_queue_depth",
                "decoder_realtime_utilization",
                "decoder_processing_p95_ms",
                "decoder_processing_max_ms",
            }:
                app.statistics.set_gauge(name, value)
                continue
            previous = app._receive_diagnostics.get(name, 0)
            if value > previous:
                app.statistics.increment(name, value - previous)
            app._receive_diagnostics[name] = int(value)
        batches = []
        timeout = 0.002
        if len(app.config.frequency.frequencies_hz) > 1:
            # Do not sleep through the next hop boundary waiting for decoded data.
            timeout = min(timeout, max(0.0, app.scheduler.state().seconds_to_change - 0.0005))
        first = app.decoder_worker.receive(timeout=timeout)
        if first is not None:
            batches.append(first)
            for _ in range(63):
                batch = app.decoder_worker.receive()
                if batch is None:
                    break
                batches.append(batch)
        for batch in batches:
            app.statistics.timing.finish("recognized_batch_queue_ms", batch.recognized_at_monotonic)
        decoded = [frame for batch in batches for frame in batch.frames]
        corrected = sum(batch.fec_corrected_bits for batch in batches)
        uncorrectable = sum(batch.fec_uncorrectable_frames for batch in batches)
        failure_score = next(
            (batch.failure_score for batch in reversed(batches) if batch.failure_score is not None),
            None,
        )
        failure_corrected_score = next(
            (
                batch.failure_corrected_score
                for batch in reversed(batches)
                if batch.failure_corrected_score is not None
            ),
            None,
        )
    else:
        timed_receive = getattr(app.sdr, "receive_timed", None)
        if timed_receive is None:
            samples = app.sdr.receive()
            sample_time_ns = None
        else:
            block = timed_receive()
            samples = block.samples
            sample_time_ns = block.time_ns
        if (
            len(app.config.frequency.frequencies_hz) > 1
            and app.monotonic_clock() < app._hop_ready_at
        ):
            return False
        if app.recorder is not None:
            app.recorder.record_iq(samples)
        app.channel_power = float(np.mean(np.abs(samples) ** 2)) if len(samples) else 0.0
        corrected_before = app.rx_modem.fec_corrected_bits
        uncorrectable_before = app.rx_modem.fec_uncorrectable_frames
        feed_timed = getattr(app.rx_modem, "feed_timed", None)
        decode_started = app.statistics.timing.start()
        if feed_timed is None:
            decoded = [(wire, None) for wire in app.rx_modem.feed(samples)]
        else:
            decoded = feed_timed(samples, sample_time_ns)
        app.statistics.timing.finish("rx_decode_ms", decode_started)
        corrected = app.rx_modem.fec_corrected_bits - corrected_before
        uncorrectable = app.rx_modem.fec_uncorrectable_frames - uncorrectable_before
        failure_score = app.rx_modem.last_fec_failure_score
        failure_corrected_score = app.rx_modem.last_fec_failure_corrected_score
    channel_power = getattr(app.sdr, "channel_power", None)
    if channel_power is not None:
        app.channel_power = max(app.channel_power, float(channel_power()))
    app.statistics.set_gauge("estimated_signal_power", app.channel_power)
    if corrected:
        app.statistics.increment("fec_corrected_bits", corrected)
    if uncorrectable:
        app.statistics.increment("fec_uncorrectable_frames", uncorrectable)
        app.statistics.increment("invalid_packets", uncorrectable)
        app.statistics.set_gauge("last_fec_failure_score", failure_score)
        app.statistics.set_gauge(
            "last_fec_failure_corrected_score",
            failure_corrected_score,
        )
    rejected = getattr(app.rx_modem, "candidate_rejections", 0)
    previous = app._receive_diagnostics.get("candidate_rejections", 0)
    if rejected > previous:
        app.statistics.increment("candidate_rejections", rejected - previous)
        app.statistics.increment("invalid_packets", rejected - previous)
    app._receive_diagnostics["candidate_rejections"] = rejected
    return app.behavior.receive(app, decoded)
