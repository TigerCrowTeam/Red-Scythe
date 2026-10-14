from packet_radio.simulation import SimulatedChannel, SimulatedNetwork
from tests.integration.test_end_to_end import node


def two_node_network(*, channel=None, offsets=None, cycle_seconds=0.02):
    network = SimulatedNetwork(
        {
            "a": node("leader", 1),
            "b": node("follower", 2),
        },
        channel=channel,
        clock_offsets=offsets,
        cycle_seconds=cycle_seconds,
    )
    network.step(4)
    return network


def test_fec_recovers_short_corruption_burst():
    channel = SimulatedChannel(corruption_rate=1.0, slotted=True, seed=10)
    network = two_node_network(channel=channel)
    try:
        frame = b"\xfdthis-frame-can-be-recovered"
        network.inject("b", frame)
        network.step(20)
        assert frame in network.received("a")
        assert channel.stats["corrupted"] > 0
        assert network.nodes["a"].app.statistics.snapshot()["fec_corrected_bits"] > 0
    finally:
        network.close()


def test_uncorrectable_corruption_is_not_delivered_to_payload():
    channel = SimulatedChannel(
        corruption_rate=1.0,
        corruption_burst_samples=128,
        slotted=True,
        seed=10,
    )
    network = two_node_network(channel=channel)
    try:
        frame = b"\xfdthis-frame-must-fail-fec-or-radio-crc"
        network.inject("b", frame)
        network.step(20)
        assert frame not in network.received("a")
        assert channel.stats["corrupted"] > 0
        assert network.nodes["a"].app.statistics.snapshot()["fec_uncorrectable_frames"] > 0
    finally:
        network.close()


def test_dropped_ack_causes_retry_without_duplicate_payload_delivery():
    network = two_node_network(cycle_seconds=0.02)
    try:
        leader = network.nodes["a"]
        leader.app.next_sync_at = float("inf")
        leader.app.link.queue.clear()
        leader.radio.drop_next()  # selectively lose the next ACK transmission
        frame = b"\xfdreliable-follower-frame"
        before = network.channel.stats["transmissions"]
        network.inject("b", frame)
        network.step(20)
        assert network.received("a").count(frame) == 1
        assert network.channel.stats["transmissions"] - before >= 3  # data, ACK, retry
        assert not network.nodes["b"].app.link.awaiting_ack
        assert network.nodes["b"].app.statistics.snapshot()["retries"] >= 1
        assert network.nodes["a"].app.statistics.snapshot()["duplicates_suppressed"] >= 1
    finally:
        network.close()


def test_temporary_disconnect_enters_scan_and_reacquires_leader():
    network = two_node_network(cycle_seconds=0.05)
    try:
        network.disconnect("b")
        network.step(120)  # six seconds, beyond the configured sync-loss timeout
        assert network.nodes["b"].app.recovering
        network.reconnect("b")
        network.step(160)  # scan until a periodic leader clock beacon is heard
        follower = network.nodes["b"].app
        assert not follower.recovering
        assert follower.statistics.snapshot()["reacquisitions"] >= 1
        assert follower.current_frequency == network.nodes["a"].app.current_frequency
        frame = b"\xfdafter-reconnect"
        network.inject("b", frame)
        network.step(20)
        assert network.received("a").count(frame) == 1
    finally:
        network.close()


def test_clock_skew_misses_channel_then_leader_beacon_corrects_schedule():
    # A five-minute slow clock selects the previous hop in the frequency list.
    network = two_node_network(offsets={"b": -300.0}, cycle_seconds=0.05)
    try:
        leader = network.nodes["a"].app
        follower = network.nodes["b"].app
        assert leader.current_frequency != follower.current_frequency
        network.step(320)
        assert not follower.recovering
        assert abs(follower.scheduler.clock_offset - 300.0) < 0.1
        assert follower.current_frequency == leader.current_frequency
    finally:
        network.close()


def test_disconnected_node_independently_catches_missed_frequency_boundary():
    network = two_node_network(cycle_seconds=0.01)
    try:
        network.disconnect("b")
        previous = network.nodes["a"].app.current_frequency
        network.advance_time(301)
        network.step(2)
        assert network.nodes["a"].app.current_frequency != previous
        network.reconnect("b")
        network.step(2)
        assert network.nodes["b"].app.current_frequency == network.nodes["a"].app.current_frequency
    finally:
        network.close()
