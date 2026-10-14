"""Protect packet bytes and wrap them in a modulation-independent burst.

PacketProtection applies and reverses Hamming coding, interleaving and optional
Reed-Solomon coding. BurstEnvelope adds training bytes, a synchronization word
and a protected-payload length, and calculates the resulting envelope size.
Waveform classes convert this byte envelope into IQ samples.
"""

from dataclasses import dataclass

from reedsolo import RSCodec

from packet_radio.modems.fec import FECDecodeResult, decode, encode, encoded_size

SYNC = bytes.fromhex("55555555d391")
TRAINING = b"\x55" * 32
QPSK_TRAINING = bytes.fromhex("d37a49c5a612f08e5b31e4972cd068bf91e6235dac7840f7b2c9540e6fa1833d")


class PacketProtection:
    def __init__(self, interleaver_depth: int = 16, rs_parity_bytes: int = 0):
        if interleaver_depth < 1 or not 0 <= rs_parity_bytes <= 254 or rs_parity_bytes % 2:
            raise ValueError("invalid error-correction settings")
        self.depth = interleaver_depth
        self.parity = rs_parity_bytes
        self.rs = RSCodec(rs_parity_bytes) if rs_parity_bytes else None

    def encoded_size(self, size: int) -> int:
        count = encoded_size(size, self.depth)
        if self.parity:
            block = 255 - self.parity
            count += ((count + block - 1) // block) * self.parity
        return count

    def encode(self, packet: bytes) -> bytes:
        protected = encode(packet, self.depth)
        return bytes(self.rs.encode(protected)) if self.rs else protected

    def decode(self, protected: bytes) -> FECDecodeResult:
        if self.rs:
            protected = bytes(self.rs.decode(protected)[0])
        return decode(protected, self.depth)


@dataclass(frozen=True)
class BurstEnvelope:
    training: bytes = TRAINING
    sync: bytes = SYNC

    def encoded_size(self, protected_size: int) -> int:
        if not 0 <= protected_size <= 65535:
            raise ValueError("protected burst length exceeds uint16")
        return len(self.training) + len(self.sync) + 2 + protected_size

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
