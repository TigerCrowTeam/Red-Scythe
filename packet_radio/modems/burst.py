"""Protect packet bytes and wrap them in a modulation-independent burst.

PacketProtection applies and reverses Hamming coding, interleaving and optional
Reed-Solomon coding. BurstEnvelope adds training bytes, a synchronization word
and a protected-payload length, and calculates the resulting envelope size.
Waveform classes convert this byte envelope into IQ samples.
"""

from dataclasses import dataclass
from typing import Protocol

from reedsolo import RSCodec

from packet_radio.modems.fec import FECDecodeResult, decode, encode, encoded_size

SYNC = bytes.fromhex("55555555d391")
TRAINING = b"\x55" * 32
QPSK_TRAINING = bytes.fromhex("d37a49c5a612f08e5b31e4972cd068bf91e6235dac7840f7b2c9540e6fa1833d")


class ByteTransform(Protocol):
    """A reversible byte transform with exact size and correction accounting."""

    def encoded_size(self, size: int) -> int: ...
    def encode(self, packet: bytes) -> bytes: ...
    def decode(self, protected: bytes) -> FECDecodeResult: ...


class Envelope(Protocol):
    """Acquisition prefix and length parsing shared by TX and streaming RX."""

    @property
    def training(self) -> bytes: ...
    @property
    def sync(self) -> bytes: ...
    @property
    def length_bytes(self) -> int: ...
    def body_size(self, length: bytes) -> int: ...
    def restore(self, length: bytes, body: bytes) -> bytes: ...
    def encoded_size(self, protected_size: int) -> int: ...
    def encode(self, protected: bytes) -> bytes: ...
    def decode(self, wire: bytes) -> bytes: ...


@dataclass(frozen=True)
class HammingInterleaver:
    depth: int = 16

    def encoded_size(self, size: int) -> int:
        return encoded_size(size, self.depth)

    def encode(self, packet: bytes) -> bytes:
        return encode(packet, self.depth)

    def decode(self, protected: bytes) -> FECDecodeResult:
        return decode(protected, self.depth)


class ReedSolomonTransform:
    def __init__(self, parity: int):
        if not 0 < parity <= 254 or parity % 2:
            raise ValueError("invalid Reed-Solomon parity")
        self.parity = parity
        self.rs = RSCodec(parity)

    def encoded_size(self, size: int) -> int:
        block = 255 - self.parity
        return size + ((size + block - 1) // block) * self.parity

    def encode(self, packet: bytes) -> bytes:
        return bytes(self.rs.encode(packet))

    def decode(self, protected: bytes) -> FECDecodeResult:
        # Preserve the existing metric: Hamming corrected bits only. RS reports
        # corrected symbols and must not silently inflate a bit counter.
        return FECDecodeResult(bytes(self.rs.decode(protected)[0]), 0)


@dataclass(frozen=True)
class TransformChain:
    """Apply transforms in listed TX order and reverse that order on RX."""

    transforms: tuple[ByteTransform, ...]

    def encoded_size(self, size: int) -> int:
        for transform in self.transforms:
            size = transform.encoded_size(size)
        return size

    def encode(self, packet: bytes) -> bytes:
        for transform in self.transforms:
            packet = transform.encode(packet)
        return packet

    def decode(self, protected: bytes) -> FECDecodeResult:
        corrected = 0
        for transform in reversed(self.transforms):
            result = transform.decode(protected)
            protected = result.payload
            corrected += result.corrected_bits
        return FECDecodeResult(protected, corrected)


class PacketProtection(TransformChain):
    """The frozen generic-v5 chain: Hamming/interleaver, then optional RS."""

    def __init__(self, interleaver_depth: int = 16, rs_parity_bytes: int = 0):
        if interleaver_depth < 1 or not 0 <= rs_parity_bytes <= 254 or rs_parity_bytes % 2:
            raise ValueError("invalid error-correction settings")
        transforms: tuple[ByteTransform, ...] = (HammingInterleaver(interleaver_depth),)
        if rs_parity_bytes:
            transforms += (ReedSolomonTransform(rs_parity_bytes),)
        super().__init__(transforms)


@dataclass(frozen=True)
class BurstEnvelope:
    training: bytes = TRAINING
    sync: bytes = SYNC

    @property
    def length_bytes(self) -> int:
        return 2

    def body_size(self, length: bytes) -> int:
        if len(length) != self.length_bytes:
            raise ValueError("invalid burst length field")
        return int.from_bytes(length, "big")

    def restore(self, length: bytes, body: bytes) -> bytes:
        return body

    def encoded_size(self, protected_size: int) -> int:
        if not 0 <= protected_size <= 65535:
            raise ValueError("protected burst length exceeds uint16")
        return len(self.training) + len(self.sync) + self.length_bytes + protected_size

    def encode(self, protected: bytes) -> bytes:
        self.encoded_size(len(protected))
        return self.training + self.sync + len(protected).to_bytes(2, "big") + protected

    def decode(self, wire: bytes) -> bytes:
        prefix = self.training + self.sync
        if not wire.startswith(prefix) or len(wire) < len(prefix) + 2:
            raise ValueError("invalid burst preamble or length")
        length = int.from_bytes(wire[len(prefix) : len(prefix) + 2], "big")
        if len(wire) != self.encoded_size(length):
            raise ValueError("burst length mismatch")
        return wire[len(prefix) + 2 :]


@dataclass(frozen=True)
class BurstProcessing:
    """Selected protection and envelope, supplied before modem acquisition setup."""

    protection: ByteTransform
    envelope: Envelope

    def encode(self, packet: bytes) -> bytes:
        return self.envelope.encode(self.protection.encode(packet))

    def encoded_size(self, size: int) -> int:
        return self.envelope.encoded_size(self.protection.encoded_size(size))

    def decode_body(self, length: bytes, body: bytes) -> FECDecodeResult:
        return self.protection.decode(self.envelope.restore(length, body))
