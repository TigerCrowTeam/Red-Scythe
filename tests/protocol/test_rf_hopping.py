"""RF hopping acquires across clock offsets and never trusts stale/mismatched SYNC."""

import math
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packet_radio.clock_sync import HardwareClockSynchronizer
from packet_radio.config import load_config, validate_network
from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.forms import FormSession
from packet_radio.protocol.rf_hopping import RFHopSync
from packet_radio.scheduler import FrequencyScheduler
from packet_radio.statistics import LinkStatistics


def follower():
    cfg = load_config("configs/v1_1/peer-b-b205-rf-hopping.yaml")
    now = [100.2]
    # This receiver's device clock is 120 seconds ahead; wall clock is irrelevant.
    app = SimpleNamespace(
        config=cfg,
        monotonic_clock=lambda: now[0],
        sdr=SimpleNamespace(
            hardware_time=lambda: round((now[0] + 120) * 1e9), tune=Mock(), flush_receive=Mock()
        ),
        scheduler=FrequencyScheduler(cfg.frequency),
        hardware_clock_sync=HardwareClockSynchronizer(),
        statistics=LinkStatistics(lambda: now[0], cfg.frequency.sync_loss_seconds),
        decoder_worker=None,
        rx_modem=Mock(),
        recovering=False,
    )
    sync = RFHopSync(cfg)
    sync.activate(app)
    return sync, app, now


def beacon(sync, leader_seconds):
    ns = round(leader_seconds * 1e9)
    return struct.pack("!dQ", leader_seconds, ns) + sync.fingerprint


def test_acquisition_tracks_hardware_time_not_host_epoch_and_recovers():
    sync, app, now = follower()
    assert app.recovering and not sync.fresh(app)
    assert app.current_frequency == app.config.frequency.frequencies_hz[0]
    sync.receive(app, beacon(sync, 100.1), round(220.1e9))
    assert not app.recovering and sync.fresh(app)
    assert abs(sync.clock(app) - 100.2) < 1e-6
    assert app.scheduler.state().frequency_hz == 915e6
    now[0] = 101.2
    assert app.scheduler.state().frequency_hz == 916e6
    # Payload/peer liveness must not extend the lifetime of a clock observation.
    now[0] = 117
    app.last_rx = 117
    sync.recover(app)
    assert app.recovering and not sync.fresh(app)
    assert app.statistics.snapshot()["synchronization_losses"] == 1
    sync.receive(app, beacon(sync, 116.9), round(236.9e9))
    assert not app.recovering and sync.fresh(app)
    assert app.statistics.snapshot()["reacquisitions"] == 1


@pytest.mark.parametrize("kind", ["profile", "truncated", "no_timestamp", "stale", "future", "nan"])
def test_invalid_beacons_cannot_release_follower_transmission(kind):
    sync, app, now = follower()
    data = beacon(sync, 100.1)
    arrival = round(220.1e9)
    if kind == "profile":
        data = data[:-1] + bytes([data[-1] ^ 1])
    if kind == "truncated":
        data = data[:16]
    if kind == "no_timestamp":
        arrival = None
    if kind == "stale":
        arrival = round(100e9)
    if kind == "future":
        arrival = round(221e9)
    if kind == "nan":
        data = struct.pack("!dQ", math.nan, round(100.1e9)) + sync.fingerprint
    sync.receive(app, data, arrival)
    assert app.recovering and not sync.fresh(app)
    assert app.statistics.snapshot()["sync_freshness_rejections"] == 1


def test_profile_pair_and_gui_select_rf_mode_only_for_generic_b205():
    a = load_config("configs/v1_1/peer-a-b205-rf-hopping.yaml")
    b = load_config("configs/v1_1/peer-b-b205-rf-hopping.yaml")
    validate_network({"a": a, "b": b})
    session = FormSession(RadioDraft.load("configs/v1_1/peer-a-generic.yaml"))
    session.change("profile.frequency.mode", "hopping")
    assert session.report.valid
    assert session.report.config.emulation["frequency"]["synchronization"] == "rf-leader"
    session.change("profile.frequency.mode", "fixed")
    assert session.report.valid


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
@pytest.mark.parametrize("experimental", [False, True])
def test_real_generic_codec_and_modem_acquire_with_independent_device_epochs(
    modulation, experimental
):
    import numpy as np

    from packet_radio.app import RadioApp
    from packet_radio.endpoint import MemoryEndpoint
    from packet_radio.hardware.uhd import SampleBlock

    now = [100.2]

    class Radio:
        hardware_epoch_initialized = True
        actual_settings = {}

        def __init__(self, offset):
            self.offset = offset
            self.sent = []

        def hardware_time(self):
            return round((now[0] + self.offset) * 1e9)

        def tune(self, frequency):
            self.frequency = frequency

        def receive_timed(self, **kwargs):
            return SampleBlock(np.empty(0, np.complex64), self.hardware_time())

        def transmit_at(self, samples, target):
            self.sent.append((samples.copy(), target))

        def close(self):
            pass

    apps = []
    try:
        for role, offset in (("a", 0), ("b", 120)):
            draft = RadioDraft.load(f"configs/v1_1/peer-{role}-b205-rf-hopping.yaml")
            session = FormSession(draft)
            session.change("profile.waveform.modulation", modulation)
            draft = session.draft
            draft.set("node.authentication", {})
            draft.set("node.runtime.decoder_worker_enabled", False)
            draft.set("node.status.http_enabled", False)
            cfg = draft.validate().config
            app = RadioApp(
                cfg,
                sdr=Radio(offset),
                endpoint=MemoryEndpoint(),
                wall_clock=lambda: now[0] - 3600,
                monotonic_clock=lambda: now[0],
            )
            apps.append(app)
        if experimental:
            from packet_radio.runtime.bench_experiment import apply_experiment

            now[0] = 100.05
            for app in apps:
                prior_identity = app.statistics.snapshot()["runtime"]
                experiment = dict(hop_rate=4, guard=0.025, sync_lead=0.01, payload=128)
                apply_experiment(app, experiment)
                snapshot = app.statistics.snapshot()
                assert snapshot["runtime"] == prior_identity
                assert snapshot["configuration"]["benchmark_experiment"] == experiment
                assert snapshot["configuration"]["frequency"]["interval_seconds"] == 0.25
        leader, follow = apps
        leader._retune()
        now[0] = 100.1 if experimental else 100.35
        leader.step()
        assert len(leader.sdr.sent) == 1
        samples, target = leader.sdr.sent[0]
        arrival = target + 120_000_000_000
        now[0] = target / 1e9 + len(samples) / leader.config.sdr.sample_rate + 0.01
        decoded = follow.rx_modem.feed_timed(samples, arrival)
        assert len(decoded) == 1
        follow.behavior.receive(follow, decoded)
        assert not follow.recovering
        assert abs(follow.scheduler.clock() - leader.scheduler.clock()) < 1e-5
        follow._retune()
        assert follow.current_frequency == leader.current_frequency
    finally:
        for app in apps:
            app.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile.frequency.epoch", 1),
        ("profile.link.hardware_clock_sync", False),
        ("profile.link.hardware_slot_scheduling", True),
        ("node.runtime.hardware_timed_io", False),
        ("node.hardware.type", "x300"),
    ],
)
def test_rf_hopping_rejects_incompatible_timing_and_hardware(field, value):
    draft = RadioDraft.load("configs/v1_1/peer-a-b205-rf-hopping.yaml")
    draft.set(field, value)
    assert not draft.validate().valid


def test_fast_hopping_bounds_beacons_and_still_visits_rendezvous_channel():
    from dataclasses import replace

    from packet_radio.protocol.generic import GenericV5Behavior

    sync, app, now = follower()
    app.config = replace(
        app.config,
        timing_role="leader",
        frequency=replace(app.config.frequency, interval_seconds=0.125),
    )
    app.link = SimpleNamespace(control_queue=[])
    app._queue = Mock()
    app.wall_clock = lambda: now[0]
    app.next_sync_at = 0
    app.current_frequency = 916e6
    behavior = GenericV5Behavior()
    behavior.rf_sync = sync
    behavior.tick(app)
    assert app._queue.call_count == 1
    assert app.next_sync_at == pytest.approx(now[0] + 0.5)
    # A regular timer firing off the rendezvous channel must not strand the follower.
    now[0] += 0.125
    app.current_frequency = 915e6
    behavior.tick(app)
    assert app._queue.call_count == 2
    for _ in range(3):
        now[0] += 0.125
        behavior.tick(app)
    assert app._queue.call_count == 2
    now[0] += 0.125
    behavior.tick(app)
    assert app._queue.call_count == 3
