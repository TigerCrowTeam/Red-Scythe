"""Bound test-only B205 hop schedules without changing normal profile/GUI limits.

The owned benchmark child first opens a normally validated radio, then applies
an explicit in-memory timing experiment. Status reports its effective schedule;
the immutable runtime identity still identifies the validated launch file.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

from packet_radio.config import configuration_snapshot
from packet_radio.protocol.registry import resolve_processing
from packet_radio.protocol.rf_hopping import RFHopSync, enabled
from packet_radio.scheduler import FrequencyScheduler


def experiment_config(config, experiment):
    """Check finite bounds and whole DATA/ACK/SYNC airtime before any device is opened."""
    if not experiment:
        return config
    if set(experiment) != {"hop_rate", "guard", "sync_lead", "payload"}:
        raise ValueError("experiment requires hop_rate, guard, sync_lead and payload")
    if not enabled(config) or config.sdr.device.get("type") != "b200" or config.sdr.channel != 0:
        raise ValueError("hop experiments require the generic-v5 RF-synchronized B205 profile")
    if config.recording.directory is not None:
        raise ValueError("disable recording for unqualified timing experiments")
    for name in ("hop_rate", "guard", "sync_lead"):
        value = experiment[name]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("experimental timing values must be finite numbers")
    rate, guard, lead, size = (experiment[k] for k in ("hop_rate", "guard", "sync_lead", "payload"))
    if not 1 <= rate <= 30 or not 0.001 <= guard <= 0.1 or not 0.005 <= lead <= 0.05:
        raise ValueError("experiment bounds: 1..30 hops/s, 1..100 ms guards, 5..50 ms SYNC lead")
    if type(size) is not int or not 13 <= size <= config.endpoint.max_payload_bytes:
        raise ValueError("experimental payload must be 13 bytes through the profile maximum")
    processing = resolve_processing(config)
    modem = processing.make_modem(config.sdr)

    def airtime(length):
        wire = processing.codec.payload_size(length, authenticated=True)
        return (
            modem.sample_count(wire) + processing.tx_idle_samples(config.sdr)
        ) / config.sdr.sample_rate

    required = (
        max(
            airtime(size) + config.link.hardware_timed_tx_lead_seconds,
            airtime(12) + config.link.hardware_timed_tx_lead_seconds,
            airtime(32) + lead,
        )
        + 2 * guard
        + 0.001
    )
    if required >= 1 / rate:
        raise ValueError("experimental dwell cannot fit complete DATA/ACK/SYNC bursts and guards")
    definition = copy.deepcopy(config.emulation)
    definition["frequency"].update(dwell_seconds=1 / rate, guard_seconds=guard)
    definition["packet"]["max_payload_bytes"] = size
    definition["qualification"] = "unqualified"
    return replace(
        config,
        emulation=definition,
        frequency=replace(config.frequency, interval_seconds=1 / rate, guard_seconds=guard),
        endpoint=replace(config.endpoint, max_payload_bytes=size),
        link=replace(config.link, hardware_sync_tx_lead_seconds=lead),
    )


def apply_experiment(app, experiment):
    """Apply a checked schedule and report it explicitly while preserving launch ownership."""
    if not experiment:
        return
    config = experiment_config(app.config, experiment)
    prior = app.statistics.snapshot()
    app.config = config
    app.scheduler = FrequencyScheduler(config.frequency, clock=app.wall_clock)
    app.behavior.rf_sync = RFHopSync(config)
    app._prepared_burst = None
    app._hop_slot = None
    app.behavior.rf_sync.activate(app)
    snapshot = configuration_snapshot(config)
    snapshot["packet_format"] = app.processing.name
    snapshot["processing"] = app.processing.describe()
    snapshot["benchmark_experiment"] = dict(experiment)
    app.statistics.set_radio_info(snapshot, prior["hardware"])
    app.statistics.set_runtime_identity(prior["runtime"])
