"""Adapt the frozen generic-v5 wire format to the packet-codec interface.

GenericV5Codec delegates signing, parsing and CRC checks to the existing codec,
including exact encoded-size accounting and diagnostic error classification.
"""

from packet_radio.protocol.framing import (
    AuthenticationError,
    ProtocolVersionError,
    RadioPacket,
    has_valid_crc,
)


class GenericV5Codec:
    def encode(self, packet: RadioPacket, signing_key=None) -> bytes:
        return packet.encode(signing_key)

    def decode(self, wire: bytes, verification_keys=None) -> RadioPacket:
        return RadioPacket.decode(wire, verification_keys)

    def encoded_size(self, packet: RadioPacket, *, authenticated: bool = False) -> int:
        return packet.encoded_size(authenticated)

    def payload_size(self, size: int, *, authenticated: bool = False) -> int:
        return self.encoded_size(RadioPacket(1, 2, 0, bytes(size)), authenticated=authenticated)

    def candidate_valid(self, wire: bytes) -> bool:
        return has_valid_crc(wire)

    def error_counter(self, error: ValueError) -> str | None:
        if isinstance(error, AuthenticationError):
            return "authentication_failures"
        if isinstance(error, ProtocolVersionError):
            return "protocol_version_mismatches"
        if "CRC" in str(error):
            return "crc_failures"
        return None
