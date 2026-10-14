"""Exchange opaque payload bytes with local applications.

PayloadEndpoint defines receive, send and close operations. UDPEndpoint uses a
nonblocking UDP socket, preserving datagram boundaries and leaving payload
validation to RadioApp. MemoryEndpoint provides the same interface through
queues for simulation and tests.
"""

from __future__ import annotations

import socket
from collections import deque
from typing import Protocol

from packet_radio.config import EndpointConfig


class PayloadEndpoint(Protocol):
    """Opaque datagrams; None means no packet, while b'' is an invalid packet."""

    def receive(self) -> bytes | None: ...
    def send(self, payload: bytes) -> None: ...
    def close(self) -> None: ...


class UDPEndpoint:
    """Nonblocking IPv4 UDP input and output, with no payload interpretation."""

    def __init__(self, config: EndpointConfig):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.destination = (
                socket.gethostbyname(config.destination_host),
                config.destination_port,
            )
            self.socket.bind((config.bind_host, config.bind_port))
            self.socket.setblocking(False)
        except BaseException:
            self.socket.close()
            raise

    def receive(self) -> bytes | None:
        try:
            # Read an entire IPv4 datagram so the app can reject oversize input
            # instead of forwarding a silently truncated prefix.
            return self.socket.recvfrom(65535)[0]
        except BlockingIOError:
            return None

    def send(self, payload: bytes) -> None:
        self.socket.sendto(payload, self.destination)

    def close(self) -> None:
        self.socket.close()


class MemoryEndpoint:
    """Deterministic packet endpoint for simulation; no sockets or external modules."""

    def __init__(self):
        self.outbound: deque[bytes] = deque()
        self.inbound: deque[bytes] = deque()
        self.closed = False

    def inject(self, payload: bytes) -> None:
        self.outbound.append(bytes(payload))

    def receive(self) -> bytes | None:
        return self.outbound.popleft() if self.outbound else None

    def send(self, payload: bytes) -> None:
        self.inbound.append(bytes(payload))

    def close(self) -> None:
        self.closed = True
