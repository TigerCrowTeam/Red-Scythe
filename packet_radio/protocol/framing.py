"""Define and serialize version-5 DATA, ACK and SYNC radio packets.

RadioPacket encodes headers, payloads, optional Ed25519 signatures and CRCs,
and decodes them with format, integrity and authentication checks. PacketKind
identifies each message type; dedicated exceptions distinguish protocol-version
and authentication failures. has_valid_crc provides a quick integrity check
for modem candidate selection, without authenticating the candidate.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

MAGIC = b"TR"
PROTOCOL_VERSION = 5
BROADCAST_ID = 0
_HEADER = struct.Struct("!2sBBBBQIH")
# magic, version, kind, source, destination, uint64 session, uint32 sequence, length
_CRC = struct.Struct("!I")
ACK_REFERENCE = struct.Struct("!QI")  # acknowledged DATA session and sequence
AUTH_TAG_SIZE = 64
MAX_PAYLOAD = 4096


class ProtocolVersionError(ValueError):
    """Raised when a frame uses an unsupported over-air protocol version."""

    def __init__(self, received: int):
        self.received = received
        self.supported = PROTOCOL_VERSION
        super().__init__(
            f"incompatible protocol version {received}; supported version is {self.supported}"
        )


class AuthenticationError(ValueError):
    """Raised when a frame has no valid Ed25519 signature."""


class PacketKind(IntEnum):
    DATA = 0
    ACK = 1
    SYNC = 2


@dataclass(frozen=True)
class RadioPacket:
    source: int
    destination: int
    sequence: int
    payload: bytes
    kind: PacketKind = PacketKind.DATA
    session_id: int = 0

    def encoded_size(self, authenticated: bool = False) -> int:
        """Return framed bytes including signature and CRC, before FEC."""
        return (
            _HEADER.size + len(self.payload) + _CRC.size + (AUTH_TAG_SIZE if authenticated else 0)
        )

    def encode(self, signing_key: Ed25519PrivateKey | None = None) -> bytes:
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError("payload too large")
        for name, value in (("source", self.source), ("destination", self.destination)):
            if not 0 <= value <= 255:
                raise ValueError(f"{name} must fit in one byte")
        if not 0 <= self.sequence <= 0xFFFFFFFF:
            raise ValueError("sequence must fit in an unsigned 32-bit integer")
        if not 0 <= self.session_id <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("session_id must fit in an unsigned 64-bit integer")
        header = _HEADER.pack(
            MAGIC,
            PROTOCOL_VERSION,
            int(self.kind),
            self.source,
            self.destination,
            self.session_id,
            self.sequence,
            len(self.payload),
        )
        body = header + self.payload
        tag = signing_key.sign(body) if signing_key is not None else b""
        protected = body + tag
        return protected + _CRC.pack(zlib.crc32(protected) & 0xFFFFFFFF)

    @classmethod
    def decode(
        cls,
        data: bytes,
        verification_keys: dict[int, tuple[Ed25519PublicKey, ...]] | None = None,
    ) -> "RadioPacket":
        if len(data) < _HEADER.size + _CRC.size:
            raise ValueError("truncated packet")
        magic, version, kind, source, destination, session_id, sequence, length = (
            _HEADER.unpack_from(data)
        )
        if magic != MAGIC:
            raise ValueError("invalid packet header")
        if version != PROTOCOL_VERSION:
            raise ProtocolVersionError(version)
        if length > MAX_PAYLOAD:
            raise ValueError("invalid packet header")
        unauthenticated_size = _HEADER.size + length + _CRC.size
        expected = unauthenticated_size + (AUTH_TAG_SIZE if verification_keys is not None else 0)
        if len(data) != expected:
            if verification_keys is not None and len(data) == unauthenticated_size:
                raise AuthenticationError("missing packet signature")
            raise ValueError("packet length mismatch")
        actual_crc = _CRC.unpack_from(data, expected - _CRC.size)[0]
        if zlib.crc32(data[: -_CRC.size]) & 0xFFFFFFFF != actual_crc:
            raise ValueError("CRC mismatch")
        payload_end = _HEADER.size + length
        if verification_keys is not None:
            received_tag = data[payload_end : payload_end + AUTH_TAG_SIZE]
            public_keys = verification_keys.get(source)
            if not public_keys:
                raise AuthenticationError(f"no trusted public key for source node {source}")
            if isinstance(public_keys, Ed25519PublicKey):
                public_keys = (public_keys,)
            for public_key in public_keys:
                try:
                    public_key.verify(received_tag, data[:payload_end])
                    break
                except InvalidSignature:
                    continue
            else:
                raise AuthenticationError("packet signature verification failed")
        try:
            packet_kind = PacketKind(kind)
        except ValueError as exc:
            raise ValueError("unknown packet kind") from exc
        return cls(
            source,
            destination,
            sequence,
            data[_HEADER.size : payload_end],
            packet_kind,
            session_id,
        )


def has_valid_crc(data: bytes) -> bool:
    """Cheap candidate selection only; callers must still decode/authenticate packets."""
    return (
        len(data) >= _HEADER.size + _CRC.size
        and (zlib.crc32(data[: -_CRC.size]) & 0xFFFFFFFF)
        == _CRC.unpack_from(data, len(data) - _CRC.size)[0]
    )
