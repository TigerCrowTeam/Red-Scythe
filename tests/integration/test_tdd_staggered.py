from types import SimpleNamespace

import pytest

from packet_radio.app import RadioApp
from packet_radio.config import LinkConfig


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def app(role: str, clock: Clock):
    radio = RadioApp.__new__(RadioApp)
    radio.config = SimpleNamespace(
        timing_role=role,
        link=LinkConfig(
            tdd_period_seconds=0.1,
            tdd_guard_seconds=0.02,
            tdd_inter_burst_seconds=0.02,
            tdd_leader_bursts_per_slot=1,
            tdd_follower_bursts_per_slot=1,
            tdd_staggered_windows=True,
        ),
        sdr=SimpleNamespace(sample_rate=2_000_000),
    )
    radio.wall_clock = clock
    radio.monotonic_clock = clock
    radio.scheduler = SimpleNamespace(clock_offset=0.0)
    radio._last_tx_end = float("-inf")
    radio._tdd_slot_index = -1
    radio._tdd_bursts_sent = 0
    return radio


def test_staggered_windows_preserve_guard_at_both_direction_changes():
    clock = Clock()
    leader = app("leader", clock)
    follower = app("follower", clock)

    clock.now = 0.01
    assert leader._tdd_transmit_allowed()
    assert not follower._tdd_transmit_allowed()
    clock.now = 0.04
    assert not leader._tdd_transmit_allowed()
    assert not follower._tdd_transmit_allowed()
    clock.now = 0.06
    assert not leader._tdd_transmit_allowed()
    assert follower._tdd_transmit_allowed()
    clock.now = 0.09
    assert not leader._tdd_transmit_allowed()
    assert not follower._tdd_transmit_allowed()


def test_staggered_burst_must_fit_before_guard():
    clock = Clock()
    leader = app("leader", clock)
    # 8 ms airtime plus the 1 ms SDR drain margin.
    samples = 16_000
    clock.now = 0.02
    assert leader._tdd_burst_fits(samples)
    clock.now = 0.022
    assert not leader._tdd_burst_fits(samples)


def test_receive_delay_compensation_cancels_beacon_processing_offset():
    clock = Clock()
    follower = app("follower", clock)
    follower.config.link = LinkConfig(
        tdd_period_seconds=0.1,
        tdd_guard_seconds=0.02,
        tdd_staggered_windows=True,
        tdd_receive_delay_compensation_seconds=0.02,
    )
    follower.scheduler.clock_offset = -0.02
    clock.now = 0.06

    assert follower._tdd_time() == clock.now
    assert follower._tdd_transmit_allowed()


def test_hardware_scheduler_returns_fixed_opportunity_center():
    clock = Clock()
    leader = app("leader", clock)
    leader.config.link = LinkConfig(
        tdd_period_seconds=0.1,
        tdd_guard_seconds=0.01,
        tdd_leader_bursts_per_slot=3,
        hardware_timed_io=True,
        hardware_slot_scheduling=True,
        hardware_timed_tx_lead_seconds=0.003,
        hardware_slot_lookahead_seconds=0.012,
    )
    leader.sdr = SimpleNamespace(hardware_time=lambda: 1_002_000_000)
    leader.hardware_clock_sync = SimpleNamespace(ready=False)

    target = leader._next_hardware_tdd_opportunity()

    assert target == (1_006_666_666, 1_006_666_666, 0, 30)


def test_hardware_scheduler_waits_outside_lookahead():
    clock = Clock()
    leader = app("leader", clock)
    leader.config.link = LinkConfig(
        tdd_period_seconds=0.1,
        tdd_guard_seconds=0.01,
        tdd_leader_bursts_per_slot=3,
        hardware_timed_io=True,
        hardware_slot_scheduling=True,
        hardware_timed_tx_lead_seconds=0.003,
        hardware_slot_lookahead_seconds=0.005,
    )
    leader.sdr = SimpleNamespace(hardware_time=lambda: 1_010_000_000)
    leader.hardware_clock_sync = SimpleNamespace(ready=False)

    assert leader._next_hardware_tdd_opportunity() is None


def test_20_hz_scheduler_targets_two_bursts_inside_leader_window():
    clock = Clock()
    leader = app("leader", clock)
    leader.config.link = LinkConfig(
        tdd_period_seconds=0.05,
        tdd_guard_seconds=0.005,
        tdd_leader_bursts_per_slot=2,
        hardware_timed_io=True,
        hardware_slot_scheduling=True,
        hardware_timed_tx_lead_seconds=0.003,
        hardware_slot_lookahead_seconds=0.008,
    )
    leader.sdr = SimpleNamespace(hardware_time=lambda: 1_001_000_000)
    leader.hardware_clock_sync = SimpleNamespace(ready=False)

    target = leader._next_hardware_tdd_opportunity()

    assert target == (1_005_000_000, 1_005_000_000, 0, 40)


@pytest.mark.parametrize("elapsed_ns, sent", [(1_000_000, True), (20_000_000, False)])
def test_timed_beacon_uses_remaining_lead_and_rejects_expired_target(elapsed_ns, sent):
    from packet_radio.config import FrequencyConfig, RadioConfig, SDRConfig
    from packet_radio.endpoint import MemoryEndpoint

    class TimedRadio:
        calls = []
        now = 1_000_000_000

        def hardware_time(self):
            self.now += elapsed_ns
            return self.now

        def transmit_at(self, samples, target):
            assert target > self.now
            self.calls.append(target)

        def close(self):
            pass

    radio = TimedRadio()
    config = RadioConfig(
        node_id=1,
        peer_id=2,
        timing_role="leader",
        sdr=SDRConfig(),
        frequency=FrequencyConfig((915e6,), epoch=0, guard_seconds=0),
        link=LinkConfig(hardware_timed_io=True),
    )
    application = RadioApp(config, sdr=radio, endpoint=MemoryEndpoint())
    try:
        application._queue_clock_sync()
        application._transmit_one()
        assert len(radio.calls) == int(sent)
        assert application.statistics.snapshot()["tdd_opportunities_missed"] == int(not sent)
    finally:
        application.close()


def test_prepared_maximum_packet_survives_encoding_overrun_into_next_window(monkeypatch):
    from packet_radio.config import FrequencyConfig, RadioConfig, SDRConfig
    from packet_radio.endpoint import MemoryEndpoint
    from packet_radio.modems import BPSKModem
    from packet_radio.protocol.framing import RadioPacket

    clock = Clock()
    clock.now = 0.003
    bursts = []
    radio = SimpleNamespace(transmit=bursts.append, close=lambda: None)
    config = RadioConfig(
        node_id=1,
        peer_id=2,
        timing_role="leader",
        sdr=SDRConfig(sample_rate=2_000_000, samples_per_symbol=8),
        frequency=FrequencyConfig((915e6,), epoch=0, guard_seconds=0),
        link=LinkConfig(tdd_period_seconds=0.16, tdd_guard_seconds=0.002),
    )
    application = RadioApp(
        config, sdr=radio, endpoint=MemoryEndpoint(), wall_clock=clock, monotonic_clock=clock
    )
    modulate = application.modem.modulate
    encodes = []

    def expensive_modulation(wire):
        encodes.append(wire)
        clock.now += 0.012
        return modulate(wire)

    monkeypatch.setattr(application.modem, "modulate", expensive_modulation)
    payload = bytes(range(256)) * 4
    try:
        assert application._queue_local_payload(payload)
        application._transmit_one()
        assert not bursts
        assert application._prepared_burst is not None
        assert len(application.link.queue) == 1
        clock.now = 0.163
        application._transmit_one()
        assert len(encodes) == len(bursts) == 1
        assert application._prepared_burst is None
        wire = list(BPSKModem(8, sample_rate=2_000_000).feed(bursts[0]))[0]
        assert RadioPacket.decode(wire).payload == payload
    finally:
        application.close()
