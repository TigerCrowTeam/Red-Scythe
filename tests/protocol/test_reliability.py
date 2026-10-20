import struct

from packet_radio.config import LinkConfig
from packet_radio.protocol.framing import PacketKind, RadioPacket
from packet_radio.protocol.reliability import ReliableLink


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def test_unicast_retries_until_acknowledged():
    clock = Clock()
    link = ReliableLink(2, LinkConfig(ack_timeout_seconds=1, max_retries=2), clock)
    packet = RadioPacket(2, 1, 7, b"payload")
    link.enqueue(packet)
    queued = link.next_packet(True, True)
    assert queued is not None
    link.transmitted(queued)
    clock.now = 1.1
    assert link.next_packet(True, True).packet == packet
    ack = RadioPacket(1, 2, 7, struct.pack("!QI", 0, 7), PacketKind.ACK)
    assert link.acknowledge(ack)


def test_duplicate_data_is_suppressed_but_can_be_acked_again():
    link = ReliableLink(1, LinkConfig())
    packet = RadioPacket(2, 1, 9, b"data")
    assert not link.is_duplicate(packet)
    assert link.is_duplicate(packet)
    assert link.ack_for(packet).kind == PacketKind.ACK


def test_guard_interval_holds_queue():
    link = ReliableLink(1, LinkConfig())
    link.enqueue(RadioPacket(1, 2, 1, b"held"))
    assert link.next_packet(True, False) is None
    assert link.next_packet(True, True) is not None


def test_late_ack_removes_already_queued_retry():
    clock = Clock()
    link = ReliableLink(2, LinkConfig(ack_timeout_seconds=1), clock)
    packet = RadioPacket(2, 1, 8, b"data")
    link.enqueue(packet)
    sent = link.next_packet(True, True)
    link.transmitted(sent)
    clock.now = 1.1
    assert link.next_packet(False, True) is None  # retry is queued while channel is busy
    assert link.acknowledge(RadioPacket(1, 2, 8, struct.pack("!QI", 0, 8), PacketKind.ACK))
    assert not link.queue


def test_new_unicast_waits_for_inflight_packet_ack():
    link = ReliableLink(2, LinkConfig())
    first = RadioPacket(2, 1, 8, b"first")
    second = RadioPacket(2, 1, 9, b"second")
    link.enqueue(first)
    link.enqueue(second)

    sent = link.next_packet(True, True)
    assert sent.packet == first
    link.transmitted(sent)
    assert link.next_packet(True, True) is None

    assert link.acknowledge(RadioPacket(1, 2, 8, struct.pack("!QI", 0, 8), PacketKind.ACK))
    assert link.next_packet(True, True).packet == second


def test_ack_bypasses_inflight_unicast_wait():
    link = ReliableLink(2, LinkConfig())
    link.enqueue(RadioPacket(2, 1, 8, b"outbound"))
    sent = link.next_packet(True, True)
    link.transmitted(sent)

    acknowledgement = RadioPacket(2, 1, 42, struct.pack("!QI", 0, 42), PacketKind.ACK)
    link.enqueue(acknowledgement)
    assert link.next_packet(True, True).packet == acknowledgement


def test_sequences_above_255_remain_distinct_in_duplicate_window():
    link = ReliableLink(1, LinkConfig(duplicate_window_seconds=30))
    low = RadioPacket(2, 1, 1, b"old")
    high = RadioPacket(2, 1, 257, b"new")
    assert not link.is_duplicate(low)
    assert not link.is_duplicate(high)
    assert link.is_duplicate(low)
    assert link.is_duplicate(high)


def test_data_queue_rejects_newest_and_counts_inflight_capacity():
    link = ReliableLink(1, LinkConfig(queue_size=2))
    assert link.enqueue(RadioPacket(1, 2, 1, b"first"))
    assert link.enqueue(RadioPacket(1, 2, 2, b"second"))
    sent = link.next_packet(True, True)
    link.transmitted(sent)
    assert not link.enqueue(RadioPacket(1, 2, 3, b"rejected"))
    assert link.statistics.snapshot()["queue_depth"] == 2
    assert link.statistics.snapshot()["queue_drops"] == 1
    assert link.acknowledge(RadioPacket(2, 1, 1, struct.pack("!QI", 0, 1), PacketKind.ACK))
    assert link.enqueue(RadioPacket(1, 2, 4, b"fourth"))
    assert [q.packet.payload for q in link.queue] == [b"second", b"fourth"]


def test_control_has_separate_bounded_capacity_without_data_eviction():
    link = ReliableLink(1, LinkConfig(queue_size=1, control_queue_size=1))
    assert link.enqueue(RadioPacket(1, 2, 1, b"data"))
    ack = RadioPacket(1, 2, 2, struct.pack("!QI", 0, 2), PacketKind.ACK)
    assert link.enqueue(ack)
    assert link.enqueue(ack)  # same ACK is already pending
    assert not link.enqueue(RadioPacket(1, 2, 3, struct.pack("!QI", 0, 3), PacketKind.ACK))
    assert link.statistics.snapshot()["control_queue_drops"] == 1
    assert link.next_packet(True, True).packet == ack
    assert link.next_packet(True, True).packet.payload == b"data"


def test_repeated_payload_is_not_coalesced_or_expired():
    clock = Clock()
    link = ReliableLink(1, LinkConfig(queue_size=3), clock)
    for seq in range(3):
        assert link.enqueue(RadioPacket(1, 0, seq, b"identical"))
    clock.now = 1000
    assert [link.next_packet(True, True).packet.sequence for _ in range(3)] == [0, 1, 2]


def test_retries_exhaust_and_next_packet_can_progress():
    clock = Clock()
    link = ReliableLink(1, LinkConfig(ack_timeout_seconds=1, max_retries=1), clock)
    link.enqueue(RadioPacket(1, 2, 1, b"lost"))
    link.enqueue(RadioPacket(1, 2, 2, b"next"))
    for _ in range(2):
        queued = link.next_packet(True, True)
        assert queued.packet.sequence == 1
        link.transmitted(queued)
        clock.now += 1.1
    assert link.next_packet(True, True).packet.sequence == 2
    assert link.statistics.snapshot()["retry_exhausted"] == 1


def test_duplicate_cache_distinguishes_boot_sessions():
    link = ReliableLink(1, LinkConfig())
    old = RadioPacket(2, 1, 1, b"old", session_id=1)
    new = RadioPacket(2, 1, 1, b"new", session_id=2)
    assert not link.is_duplicate(old)
    assert not link.is_duplicate(new)
    assert link.is_duplicate(old)


def test_deferred_retry_is_not_inserted_twice_or_allowed_to_expand_queue():
    clock = Clock()
    link = ReliableLink(1, LinkConfig(queue_size=1), clock)
    link.enqueue(RadioPacket(1, 2, 1, b"packet"))
    sent = link.next_packet(True, True)
    link.transmitted(sent)
    clock.now = 1
    retry = link.next_packet(True, True)
    link.requeue(retry)
    link.requeue(retry)
    assert not link.queue
    assert len(link.awaiting_ack) == 1
    assert not link.enqueue(RadioPacket(1, 2, 2, b"new"))


def test_ack_from_previous_local_session_cannot_clear_new_data():
    link = ReliableLink(1, LinkConfig(), session_id=200)
    link.enqueue(RadioPacket(1, 2, 7, b"new session", session_id=200))
    link.transmitted(link.next_packet(True, True))
    stale = RadioPacket(2, 1, 99, struct.pack("!QI", 100, 7), PacketKind.ACK, 300)
    assert not link.acknowledge(stale)
    assert len(link.awaiting_ack) == 1
    valid = RadioPacket(2, 1, 100, struct.pack("!QI", 200, 7), PacketKind.ACK, 300)
    assert link.acknowledge(valid)


def test_airtime_budget_includes_pending_ack_and_releases_on_ack():
    link = ReliableLink(
        1, LinkConfig(queue_size=10, queue_airtime_seconds=0.1), airtime=lambda packet: 0.06
    )
    assert link.enqueue(RadioPacket(1, 2, 1, b"first"))
    queued = link.next_packet(True, True)
    link.transmitted(queued)
    assert not link.enqueue(RadioPacket(1, 2, 2, b"over budget"))
    assert link.queued_airtime() == 0.06
    assert link.acknowledge(RadioPacket(2, 1, 0, struct.pack("!QI", 0, 1), PacketKind.ACK))
    assert link.queued_airtime() == 0
    assert link.enqueue(RadioPacket(1, 2, 2, b"fits now"))
    # Control packets have their own bounded queue and never evict admitted data.
    assert link.enqueue(RadioPacket(1, 2, 3, struct.pack("!QI", 0, 7), PacketKind.ACK))
    assert link.queued_airtime() == 0.06


def test_retry_counter_counts_transmissions_not_missed_window_selections():
    clock = Clock()
    link = ReliableLink(1, LinkConfig(ack_timeout_seconds=1), clock)
    link.enqueue(RadioPacket(1, 2, 9, b"held retry"))
    queued = link.next_packet(True, True)
    link.transmitted(queued)
    clock.now = 2
    for _ in range(10):
        assert link.next_packet(True, True) is queued
        link.requeue(queued)  # eligible, but burst did not fit its transmit window
    assert link.statistics.snapshot()["retries"] == 0
    link.transmitted(queued)
    assert link.statistics.snapshot()["retries"] == 1


def test_ack_timing_includes_queue_wait_and_tracks_latest_retry_completion():
    from packet_radio.statistics import LinkStatistics

    clock = Clock()
    stats = LinkStatistics(clock, timing_diagnostics=True)
    link = ReliableLink(2, LinkConfig(ack_timeout_seconds=1), clock, stats)
    packet = RadioPacket(2, 1, 7, b"payload")
    link.enqueue(packet)
    clock.now = 2
    queued = link.next_packet(True, True)
    link.transmitted(queued)
    clock.now = 3.5
    assert link.next_packet(True, True) is queued
    link.transmitted(queued)
    clock.now = 4
    ack = RadioPacket(1, 2, 7, struct.pack("!QI", 0, 7), PacketKind.ACK)
    assert link.acknowledge(ack)
    assert not link.acknowledge(ack)
    timing = stats.snapshot()["timing_diagnostics"]
    assert timing["enqueue_to_ack_ms"]["mean"] == 4000
    assert timing["tx_completion_to_ack_ms"]["mean"] == 500
    assert timing["enqueue_to_ack_ms"]["count"] == 1
    assert stats.snapshot()["oldest_pending_packet_age_ms"] == 0


def test_empty_queue_age_is_zero_even_when_host_clock_advances():
    from itertools import count

    from packet_radio.statistics import LinkStatistics

    ticks = count(10.0, 0.001)

    def clock():
        return next(ticks)

    stats = LinkStatistics(clock, timing_diagnostics=True)
    link = ReliableLink(2, LinkConfig(), clock, stats)
    link._update_gauges()
    assert stats.snapshot()["oldest_pending_packet_age_ms"] == 0


def test_queue_age_expiry_runs_while_blocked_and_preserves_control():
    clock = Clock()
    link = ReliableLink(1, LinkConfig(queue_size=1, queue_max_age_seconds=1), clock)
    assert link.enqueue(RadioPacket(1, 2, 1, b"old"))
    control = RadioPacket(1, 2, 2, b"sync", PacketKind.SYNC)
    assert link.enqueue(control)
    clock.now = 1
    assert link.next_packet(False, False) is None
    assert not link.queue
    assert link.control_queue[0].packet == control
    assert link.statistics.snapshot()["queue_expired"] == 1
    assert link.enqueue(RadioPacket(1, 2, 3, b"fresh"))
    assert link.statistics.snapshot()["queue_drops"] == 0


def test_expired_inflight_data_cannot_retry_or_be_acknowledged():
    clock = Clock()
    link = ReliableLink(2, LinkConfig(queue_max_age_seconds=1), clock)
    packet = RadioPacket(2, 1, 7, b"payload")
    link.enqueue(packet)
    queued = link.next_packet(True, True)
    link.transmitted(queued)
    clock.now = 1
    ack = RadioPacket(1, 2, 7, struct.pack("!QI", 0, 7), PacketKind.ACK)
    assert not link.acknowledge(ack)
    assert not link.awaiting_ack
    assert link.next_packet(True, True) is None
    snapshot = link.statistics.snapshot()
    assert snapshot["queue_expired"] == 1
    assert snapshot["retry_exhausted"] == 0
    assert snapshot["queue_depth"] == 0


def test_default_preserves_data_and_reports_age_without_timing_diagnostics():
    clock = Clock()
    link = ReliableLink(1, LinkConfig(), clock)
    link.enqueue(RadioPacket(1, 2, 1, b"held"))
    clock.now = 100
    assert link.next_packet(False, False) is None
    snapshot = link.statistics.snapshot()
    assert snapshot["oldest_pending_packet_age_ms"] == 100_000
    assert "timing_diagnostics" not in snapshot
    assert snapshot["queue_expired"] == 0
    assert link.next_packet(True, True).packet.payload == b"held"
