"""RF-only acquisition and hardware-clock tracking for generic-v5 B205 hopping.

The follower waits on the first table frequency until a matching leader beacon
arrives, then schedules against the leader's hardware clock. Losing beacons
returns it to that rendezvous frequency with payload transmission inhibited.
The existing generic signatures/replay checks run before this module sees SYNC.
"""

from __future__ import annotations

import math
import struct

from packet_radio.clock_sync import HardwareClockSynchronizer


def enabled(config) -> bool:
    return (
        bool(config.emulation)
        and config.emulation["frequency"].get("synchronization") == "rf-leader"
    )


class RFHopSync:
    def __init__(self, config):
        from packet_radio.profiles import profile_fingerprint

        self.fingerprint = bytes.fromhex(profile_fingerprint(config.emulation))[:16]
        self.last_beacon = None
        self.had_lock = False
        self.last_leader_time = None
        self.next_rendezvous_at = 0.0

    def clock(self, app):
        local = app.sdr.hardware_time()
        if app.config.timing_role == "leader" or self.last_beacon is None:
            return local / 1e9
        return app.hardware_clock_sync.local_to_leader(local) / 1e9

    def activate(self, app):
        app.scheduler.clock = lambda: self.clock(app)
        app.scheduler.clock_offset = 0
        if app.config.timing_role == "follower":
            self.park(app)
        else:
            app.statistics.set_gauge("synchronized", True)

    def park(self, app):
        app.recovering = True
        app.statistics.set_gauge("synchronized", False)
        self.last_beacon = None
        self.last_leader_time = None
        app.last_sync_leader_time = None
        app.last_sync_received_at = None
        app.hardware_clock_sync = HardwareClockSynchronizer()
        app._prepared_burst = None
        app._hop_slot = None
        frequency = app.config.frequency.frequencies_hz[0]

        def tune():
            app.sdr.tune(frequency)
            flush = getattr(app.sdr, "flush_receive", None)
            if flush:
                flush()

        if app.decoder_worker is not None:
            app.decoder_worker.retune(tune, app.config.frequency.guard_seconds)
        else:
            tune()
            app.rx_modem.reset()
        app.current_frequency = frequency
        app._hop_ready_at = app.monotonic_clock() + app.config.frequency.guard_seconds
        app.statistics.set_gauge("active_frequency_hz", frequency)

    def fresh(self, app):
        return app.config.timing_role == "leader" or (
            self.last_beacon is not None
            and app.monotonic_clock() - self.last_beacon < app.config.frequency.sync_loss_seconds
        )

    def recover(self, app):
        if not self.fresh(app) and not app.recovering:
            app.statistics.increment("synchronization_losses")
            self.park(app)

    def receive(self, app, payload, arrival_ns):
        if app.config.timing_role != "follower":
            return
        if len(payload) != 32 or arrival_ns is None or payload[16:] != self.fingerprint:
            app.statistics.increment("sync_freshness_rejections")
            return
        leader_seconds, leader_ns = struct.unpack("!dQ", payload[:16])
        now_ns = app.sdr.hardware_time()
        if (
            not math.isfinite(leader_seconds)
            or not leader_ns
            or abs(leader_seconds - leader_ns / 1e9) > 1e-6
            or not (
                0
                <= now_ns - arrival_ns
                < min(
                    app.config.frequency.sync_loss_seconds,
                    app.config.authentication.sync_max_age_seconds,
                )
                * 1e9
            )
        ):
            app.statistics.increment("sync_freshness_rejections")
            return
        if self.last_beacon is not None:
            expected = self.last_leader_time + app.monotonic_clock() - self.last_beacon
            delta = leader_seconds - expected
            if (
                not -app.config.authentication.sync_max_age_seconds
                <= delta
                <= app.config.authentication.sync_max_future_seconds
            ):
                app.statistics.increment("sync_freshness_rejections")
                return
        estimate = app.hardware_clock_sync.observe(leader_ns, arrival_ns)
        app.statistics.set_gauge("hardware_clock_offset_ns", estimate.offset_ns)
        app.statistics.set_gauge("hardware_clock_drift_ppm", estimate.drift_ppm)
        app.statistics.set_gauge("hardware_clock_observations", estimate.samples)
        if app.recovering and self.had_lock:
            app.statistics.increment("reacquisitions")
        self.last_beacon = app.monotonic_clock()
        self.last_leader_time = leader_seconds
        self.had_lock = True
        app.last_sync_received_at = self.last_beacon
        app.last_sync_leader_time = leader_seconds
        app.last_rx = self.last_beacon
        app.statistics.mark_activity("peer")
        app.recovering = False
        app.statistics.set_gauge("synchronized", True)
        app.scheduler.clock_offset = 0
