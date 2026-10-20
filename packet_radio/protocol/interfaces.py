"""Define packet-codec and link-behavior boundaries for processing profiles.

Codecs own bytes and validation; behaviors own packet objects, queues and control
traffic. The common application handles samples, resources and scheduling. Packet
objects are deliberately opaque across this boundary: a future format need not
carry generic-v5 addresses, sequences or packet kinds.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from packet_radio.app import RadioApp


@dataclass
class QueuedPacket:
    packet: Any
    sequence_assigned: bool = False
    attempts: int = 0
    next_attempt: float = 0.0
    enqueued_at: float = 0.0
    airtime_seconds: float = 0.0
    last_transmitted_at: float | None = None


class PacketCodec(Protocol):
    def encode(self, packet: Any, signing_key: Any = None) -> bytes: ...
    def decode(self, wire: bytes, verification_keys: Any = None) -> Any: ...
    def encoded_size(self, packet: Any, *, authenticated: bool = False) -> int: ...
    def payload_size(self, size: int, *, authenticated: bool = False) -> int: ...
    def candidate_valid(self, wire: bytes) -> bool: ...
    def error_counter(self, error: ValueError) -> str | None: ...


class LinkQueue(Protocol):
    """Queue operations used by the common transmitter; packet semantics stay private."""

    queue: deque[QueuedPacket]

    def next_packet(self, channel_clear: bool, tx_allowed: bool) -> QueuedPacket | None: ...
    def requeue(self, queued: QueuedPacket) -> None: ...
    def transmitted(
        self,
        queued: QueuedPacket,
        *,
        encoded_bytes: int | None = None,
        samples: int = 0,
        airtime_seconds: float = 0.0,
    ) -> None: ...


class LinkBehavior(Protocol):
    receive_enabled: bool
    requires_peer: bool
    operation: str
    input_kind: str

    def input_limit(self, app: RadioApp) -> int: ...
    def initialize(self, app: RadioApp) -> None: ...
    def make_link(self, app: RadioApp) -> LinkQueue: ...
    def queue(self, app: RadioApp, payload: bytes, destination: int, kind: Any = None) -> bool: ...
    def receive(self, app: RadioApp, decoded: list[tuple[bytes, int | None]]) -> bool: ...
    def prepare(self, app: RadioApp, queued: QueuedPacket, target_ns: int | None) -> int | None: ...
    def cacheable(self, queued: QueuedPacket) -> bool: ...
    def channel_clear(self, app: RadioApp) -> bool: ...
    def transmit_allowed(self, app: RadioApp) -> bool: ...
    def transmit_failed(self, app: RadioApp, queued: QueuedPacket) -> None: ...
    def tick(self, app: RadioApp) -> None: ...
    def recover(self, app: RadioApp) -> None: ...
    def sync_is_fresh(self, app: RadioApp, leader_time: float, received_at: float) -> bool: ...
