"""Manage bounded packet queues and acknowledgement-based delivery.

QueuedPacket records transmission attempts, timing and airtime for one packet.
ReliableLink selects control traffic, new data and retries; tracks outstanding
ACKs; applies backoff and retry limits; and suppresses duplicate delivery.
The application performs the actual RF transmission and reports its outcome.
"""

from __future__ import annotations

import random
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from packet_radio.config import LinkConfig
from packet_radio.protocol.framing import ACK_REFERENCE, BROADCAST_ID, PacketKind, RadioPacket
from packet_radio.protocol.interfaces import QueuedPacket
from packet_radio.statistics import LinkStatistics


class ReliableLink:
    """FIFO data with one in-flight packet per peer and bounded control traffic.

    queue_size bounds all admitted data, including in-flight packets. Control has
    separate capacity so an ACK cannot evict application data or deadlock behind it.
    """

    def __init__(
        self,
        node_id: int,
        config: LinkConfig,
        clock=time.monotonic,
        statistics: LinkStatistics | None = None,
        session_id: int = 0,
        encoded_size: Callable[[RadioPacket], int] | None = None,
        airtime: Callable[[RadioPacket], float] | None = None,
    ):
        if config.queue_airtime_seconds is not None and airtime is None:
            raise ValueError("airtime estimator required for queue airtime limit")
        self.airtime = airtime or (lambda packet: 0.0)
        self.node_id = node_id
        self.config = config
        self.clock = clock
        self.statistics = statistics or LinkStatistics(clock)
        self.session_id = session_id
        self.encoded_size = encoded_size or (lambda packet: len(packet.encode()))
        self.queue: deque[QueuedPacket] = deque()
        self.control_queue: deque[QueuedPacket] = deque()
        self.awaiting_ack: dict[tuple[int, int], QueuedPacket] = {}
        self.seen: OrderedDict[tuple[int, int, int], float] = OrderedDict()

    def enqueue(self, packet: RadioPacket) -> bool:
        self._expire_data(self.clock())
        airtime_seconds = self.airtime(packet)
        control = packet.kind in (PacketKind.ACK, PacketKind.SYNC)
        target = self.control_queue if control else self.queue
        if control:
            # A retransmission can request the same ACK while it is still queued.
            if any(q.packet == packet for q in target):
                return True
            full = len(target) >= self.config.control_queue_size
        else:
            full = len(self.queue) + len(self.awaiting_ack) >= self.config.queue_size
            if self.config.queue_airtime_seconds is not None:
                full |= self.queued_airtime() + airtime_seconds > self.config.queue_airtime_seconds
        if full:
            self.statistics.increment("control_queue_drops" if control else "queue_drops")
            return False
        now = self.clock()
        target.append(
            QueuedPacket(packet, next_attempt=now, enqueued_at=now, airtime_seconds=airtime_seconds)
        )
        self.statistics.increment("packets_enqueued")
        self._update_gauges()
        return True

    def next_packet(self, channel_clear: bool, tx_allowed: bool) -> QueuedPacket | None:
        now = self.clock()
        self._expire_data(now)
        self._expire_seen(now)
        for key, queued in list(self.awaiting_ack.items()):
            if now >= queued.next_attempt and queued.attempts > self.config.max_retries:
                del self.awaiting_ack[key]
                self.statistics.increment("retry_exhausted")
        self._update_gauges()
        if not channel_clear or not tx_allowed:
            return None
        if self.control_queue and self.control_queue[0].next_attempt <= now:
            result = self.control_queue.popleft()
            self._update_gauges()
            return result
        for queued in self.awaiting_ack.values():
            if queued.next_attempt <= now:
                return queued
        if not self.queue or self.queue[0].next_attempt > now:
            return None
        packet = self.queue[0].packet
        if any(destination == packet.destination for destination, _ in self.awaiting_ack):
            return None
        result = self.queue.popleft()
        self._update_gauges()
        return result

    def back_off(self) -> None:
        delay = self.clock() + random.uniform(
            self.config.backoff_min_seconds, self.config.backoff_max_seconds
        )
        if self.control_queue:
            self.control_queue[0].next_attempt = delay
        elif self.awaiting_ack:
            for queued in self.awaiting_ack.values():
                queued.next_attempt = max(queued.next_attempt, delay)
        elif self.queue:
            self.queue[0].next_attempt = delay

    def requeue(self, queued: QueuedPacket) -> None:
        if any(value is queued for value in self.awaiting_ack.values()):
            return
        target = self.control_queue if queued.packet.kind != PacketKind.DATA else self.queue
        if not any(value is queued for value in target):
            target.appendleft(queued)
        self._update_gauges()

    def transmitted(
        self,
        queued: QueuedPacket,
        *,
        encoded_bytes: int | None = None,
        samples: int = 0,
        airtime_seconds: float = 0.0,
    ) -> None:
        if queued.packet.kind == PacketKind.DATA and queued.attempts:
            self.statistics.increment("retries")
        queued.attempts += 1
        if self.statistics.timing.enabled:
            queued.last_transmitted_at = self.clock()
        packet = queued.packet
        self.statistics.increment("packets_transmitted")
        self.statistics.increment(
            "bytes_transmitted",
            self.encoded_size(packet) if encoded_bytes is None else encoded_bytes,
        )
        self.statistics.increment("samples_transmitted", samples)
        self.statistics.increment("airtime_seconds_transmitted", airtime_seconds)
        if packet.kind == PacketKind.ACK:
            self.statistics.increment("acknowledgements_sent")
        if packet.kind == PacketKind.DATA and packet.destination != BROADCAST_ID:
            queued.next_attempt = self.clock() + self.config.ack_timeout_seconds
            self.awaiting_ack[(packet.destination, packet.sequence)] = queued
        self._update_gauges()

    def acknowledge(self, packet: RadioPacket) -> bool:
        if packet.kind != PacketKind.ACK or packet.destination != self.node_id:
            return False
        if len(packet.payload) != ACK_REFERENCE.size:
            return False
        self._expire_data(self.clock())
        session, sequence = ACK_REFERENCE.unpack(packet.payload)
        key = (packet.source, sequence)
        queued = self.awaiting_ack.get(key)
        if queued is None or queued.packet.session_id != session:
            return False
        if self.statistics.timing.enabled:
            self.statistics.timing.observe(
                "enqueue_to_ack_ms", (self.clock() - queued.enqueued_at) * 1000
            )
            if queued.last_transmitted_at is not None:
                self.statistics.timing.observe(
                    "tx_completion_to_ack_ms", (self.clock() - queued.last_transmitted_at) * 1000
                )
        del self.awaiting_ack[key]
        self.statistics.increment("acknowledgements_received")
        self._update_gauges()
        return True

    def ack_for(self, packet: RadioPacket) -> RadioPacket | None:
        if packet.kind != PacketKind.DATA or packet.destination != self.node_id:
            return None
        return RadioPacket(
            self.node_id,
            packet.source,
            0,
            ACK_REFERENCE.pack(packet.session_id, packet.sequence),
            PacketKind.ACK,
            self.session_id,
        )

    def is_duplicate(self, packet: RadioPacket) -> bool:
        if packet.kind != PacketKind.DATA:
            return False
        now = self.clock()
        self._expire_seen(now)
        key = (packet.source, packet.session_id, packet.sequence)
        duplicate = key in self.seen
        self.seen[key] = now
        self.seen.move_to_end(key)
        if duplicate:
            self.statistics.increment("duplicates_suppressed")
        return duplicate

    def _expire_seen(self, now: float) -> None:
        cutoff = now - self.config.duplicate_window_seconds
        while self.seen and next(iter(self.seen.values())) < cutoff:
            self.seen.popitem(last=False)

    def queued_airtime(self) -> float:
        return sum(q.airtime_seconds for q in self.queue) + sum(
            q.airtime_seconds for q in self.awaiting_ack.values()
        )

    def _expire_data(self, now: float) -> None:
        limit = self.config.queue_max_age_seconds
        if limit is None:
            return
        expired = [q for q in self.queue if now - q.enqueued_at >= limit]
        self.queue = deque(q for q in self.queue if now - q.enqueued_at < limit)
        for key, queued in list(self.awaiting_ack.items()):
            if now - queued.enqueued_at >= limit:
                expired.append(self.awaiting_ack.pop(key))
        if expired:
            self.statistics.increment("queue_expired", len(expired))
            self._update_gauges()

    def _update_gauges(self) -> None:
        pending = [*self.queue, *self.awaiting_ack.values(), *self.control_queue]
        now = self.clock()
        oldest = min((q.enqueued_at for q in pending), default=now)
        self.statistics.set_gauge("oldest_pending_packet_age_ms", max(0, (now - oldest) * 1000))
        self.statistics.set_gauge("queue_airtime_seconds", self.queued_airtime())
        self.statistics.set_gauge("queue_depth", len(self.queue) + len(self.awaiting_ack))
        self.statistics.set_gauge("control_queue_depth", len(self.control_queue))
        self.statistics.set_gauge("awaiting_acknowledgements", len(self.awaiting_ack))
