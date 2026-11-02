"""Encode configurable byte-aligned frames and build the reference 2FSK modem.

FrameSettings validates supported framing/protection choices. SimpleFrameCodec
encodes length, opaque payload and CRC; SimpleEnvelope supplies acquisition bytes
and maps payload length to protected size. make_frame_modem connects these pieces
for the reference 2FSK path; SimpleProfile supplies the same frame processing
to other registered modems without changing generic-v5 configuration.
"""

from __future__ import annotations

import binascii
import math
import time
import zlib
from dataclasses import dataclass
from typing import Callable, Literal

from packet_radio.modems.burst import BurstProcessing, PacketProtection, TransformChain
from packet_radio.modems.fec import FECDecodeResult
from packet_radio.modems.fsk import FSK2Modem


@dataclass(frozen=True)
class CRCDefinition:
    width: int
    polynomial: int
    initial: int
    reflect_input: bool
    reflect_output: bool
    final_xor: int


CRC_DEFINITIONS = {
    "crc16-ccitt-false": CRCDefinition(16, 0x1021, 0xFFFF, False, False, 0),
    "crc32-iso-hdlc": CRCDefinition(32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF),
    "none": CRCDefinition(0, 0, 0, False, False, 0),
}


@dataclass(frozen=True)
class FrameSettings:
    preamble: bytes = b"\x55"
    preamble_repetitions: int = 32
    sync: bytes = bytes.fromhex("a7c35a9e")
    bit_order: Literal["big", "little"] = "big"
    length_bytes: int = 2
    length_byte_order: Literal["big", "little"] = "big"
    fixed_payload_bytes: int | None = None
    max_payload_bytes: int = 1024
    crc: Literal["crc16-ccitt-false", "crc32-iso-hdlc", "none"] = "crc16-ccitt-false"
    crc_byte_order: Literal["big", "little"] = "big"
    fec: Literal["none", "extended-hamming-8-4"] = "none"
    interleaver_depth: int = 16
    rs_parity_bytes: int = 0
    whitening: Literal["none"] = "none"
    differential: bool = False

    def __post_init__(self):
        for name in (
            "preamble_repetitions",
            "length_bytes",
            "max_payload_bytes",
            "interleaver_depth",
            "rs_parity_bytes",
        ):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        if type(self.preamble) is not bytes or not 1 <= len(self.preamble) <= 64:
            raise ValueError("preamble must contain 1..64 bytes")
        if not 1 <= self.preamble_repetitions <= 256 or len(self.training) > 256:
            raise ValueError("expanded preamble must contain 1..256 bytes")
        if type(self.sync) is not bytes or not 2 <= len(self.sync) <= 16:
            raise ValueError("sync must contain 2..16 bytes")
        prefix = self.training + self.sync
        if all(byte == 0 for byte in prefix) or all(byte == 255 for byte in prefix):
            raise ValueError("acquisition prefix must contain both bit values")
        if (
            self.bit_order not in ("big", "little")
            or self.length_byte_order not in ("big", "little")
            or self.crc_byte_order not in ("big", "little")
        ):
            raise ValueError("bit and byte orders must be big or little")
        if self.length_bytes not in (0, 1, 2, 4):
            raise ValueError("length_bytes must be 0, 1, 2 or 4; non-byte fields are unsupported")
        if not 1 <= self.max_payload_bytes <= 1024:
            raise ValueError("max_payload_bytes must be 1..1024")
        if self.fixed_payload_bytes is not None:
            if (
                type(self.fixed_payload_bytes) is not int
                or not 1 <= self.fixed_payload_bytes <= self.max_payload_bytes
                or self.length_bytes != 0
            ):
                raise ValueError(
                    "fixed payload requires length_bytes=0 and a size within the limit"
                )
        elif not self.length_bytes or self.max_payload_bytes >= 1 << (8 * self.length_bytes):
            raise ValueError("variable payload limit must fit the nonzero length field")
        if not isinstance(self.crc, str) or self.crc not in CRC_DEFINITIONS:
            raise ValueError("unsupported CRC")
        if self.fec not in ("none", "extended-hamming-8-4"):
            raise ValueError("unsupported FEC")
        if (
            not 1 <= self.interleaver_depth <= 256
            or not 0 <= self.rs_parity_bytes <= 254
            or self.rs_parity_bytes % 2
        ):
            raise ValueError("invalid interleaver or RS parity")
        if self.fec == "none" and (self.rs_parity_bytes or self.interleaver_depth != 16):
            raise ValueError("no-FEC requires RS=0 and the unused default interleaver depth")
        if self.whitening != "none" or self.differential is not False:
            raise ValueError("whitening and differential encoding are not supported")

    @property
    def training(self) -> bytes:
        return self.preamble * self.preamble_repetitions

    @property
    def crc_bytes(self) -> int:
        return CRC_DEFINITIONS[self.crc].width // 8

    def check_size(self, size: int) -> None:
        if type(size) is not int or not 1 <= size <= self.max_payload_bytes:
            raise ValueError("payload length outside configured limits")
        if self.fixed_payload_bytes is not None and size != self.fixed_payload_bytes:
            raise ValueError("payload length does not match fixed size")

    def payload_length(self, field: bytes) -> int:
        if len(field) != self.length_bytes:
            raise ValueError("invalid length field size")
        size = (
            self.fixed_payload_bytes
            if self.fixed_payload_bytes is not None
            else int.from_bytes(field, self.length_byte_order)
        )
        self.check_size(size)
        return size


class SimpleFrameCodec:
    """Length/payload/CRC codec; CRC covers transmitted length bytes plus payload."""

    def __init__(self, settings: FrameSettings = FrameSettings()):
        self.settings = settings

    def _crc(self, data: bytes) -> bytes:
        value = (
            binascii.crc_hqx(data, 0xFFFF)
            if self.settings.crc == "crc16-ccitt-false"
            else zlib.crc32(data) & 0xFFFFFFFF
            if self.settings.crc == "crc32-iso-hdlc"
            else 0
        )
        return value.to_bytes(self.settings.crc_bytes, self.settings.crc_byte_order)

    def encode(self, packet: bytes, signing_key=None) -> bytes:
        if signing_key is not None:
            raise ValueError("simple frames do not support signatures")
        self.settings.check_size(len(packet))
        prefix = (
            len(packet).to_bytes(self.settings.length_bytes, self.settings.length_byte_order)
            if self.settings.length_bytes
            else b""
        )
        body = prefix + packet
        return body + self._crc(body)

    def decode(self, wire: bytes, verification_keys=None) -> bytes:
        if verification_keys is not None:
            raise ValueError("simple frames do not support authentication")
        cfg = self.settings
        size = cfg.payload_length(wire[: cfg.length_bytes])
        if len(wire) != cfg.length_bytes + size + cfg.crc_bytes:
            raise ValueError("frame length mismatch")
        body = wire[: cfg.length_bytes + size]
        if wire[len(body) :] != self._crc(body):
            raise ValueError("CRC mismatch")
        return body[cfg.length_bytes :]

    def encoded_size(self, packet: bytes, *, authenticated: bool = False) -> int:
        return self.payload_size(len(packet), authenticated=authenticated)

    def payload_size(self, size: int, *, authenticated: bool = False) -> int:
        if authenticated:
            raise ValueError("simple frames do not support signatures")
        self.settings.check_size(size)
        return self.settings.length_bytes + size + self.settings.crc_bytes

    def candidate_valid(self, wire: bytes) -> bool:
        try:
            self.decode(wire)
            return True
        except ValueError:
            return False

    def error_counter(self, error: ValueError) -> str | None:
        return "crc_failures" if "CRC" in str(error) else None


class FrameProtection:
    """Protect payload/CRC while leaving the payload length available for acquisition."""

    def __init__(self, settings: FrameSettings):
        self.settings = settings
        self.inner = (
            TransformChain(())
            if settings.fec == "none"
            else PacketProtection(settings.interleaver_depth, settings.rs_parity_bytes)
        )

    def encoded_size(self, size: int) -> int:
        self.settings.check_size(size - self.settings.length_bytes - self.settings.crc_bytes)
        return self.settings.length_bytes + self.inner.encoded_size(
            size - self.settings.length_bytes
        )

    def encode(self, packet: bytes) -> bytes:
        n = self.settings.length_bytes
        # Validate the full pre-protection frame before generating samples.
        SimpleFrameCodec(self.settings).decode(packet)
        return packet[:n] + self.inner.encode(packet[n:])

    def decode(self, protected: bytes) -> FECDecodeResult:
        n = self.settings.length_bytes
        size = self.settings.payload_length(protected[:n])
        if len(protected[n:]) != self.inner.encoded_size(size + self.settings.crc_bytes):
            raise ValueError("protected frame length mismatch")
        result = self.inner.decode(protected[n:])
        return FECDecodeResult(protected[:n] + result.payload, result.corrected_bits)


@dataclass(frozen=True)
class SimpleEnvelope:
    settings: FrameSettings
    protection: FrameProtection

    @property
    def training(self) -> bytes:
        return self.settings.training

    @property
    def sync(self) -> bytes:
        return self.settings.sync

    @property
    def bit_order(self) -> Literal["big", "little"]:
        return self.settings.bit_order

    @property
    def length_bytes(self) -> int:
        return self.settings.length_bytes

    def body_size(self, length: bytes) -> int:
        size = self.settings.payload_length(length)
        return self.protection.inner.encoded_size(size + self.settings.crc_bytes)

    def restore(self, length: bytes, body: bytes) -> bytes:
        return length + body

    def encoded_size(self, protected_size: int) -> int:
        return len(self.training) + len(self.sync) + protected_size

    def encode(self, protected: bytes) -> bytes:
        if len(protected) != self.length_bytes + self.body_size(protected[: self.length_bytes]):
            raise ValueError("protected burst length mismatch")
        return self.training + self.sync + protected

    def decode(self, wire: bytes) -> bytes:
        prefix = self.training + self.sync
        if not wire.startswith(prefix):
            raise ValueError("invalid burst preamble or sync")
        protected = wire[len(prefix) :]
        if len(protected) != self.length_bytes + self.body_size(protected[: self.length_bytes]):
            raise ValueError("burst length mismatch")
        return protected


class FrameFSKModem(FSK2Modem):
    """Byte-framed 2FSK with bounded stalled-frame lifetime and strict CRC rejection."""

    def __init__(self, *args, clock: Callable[[], float] = time.monotonic, **kwargs):
        super().__init__(*args, **kwargs)
        self._clock = clock
        self._last_input: float | None = None
        self.discard_invalid_candidates = True

    def reset(self) -> None:
        super().reset()
        self._last_input = None

    def feed_timed(self, samples, time_ns):
        now = self._clock()
        if self._last_input is not None and now - self._last_input >= 0.1:
            self.reset()
        if len(samples):
            self._last_input = now
        return super().feed_timed(samples, time_ns)


def make_frame_modem(
    settings: FrameSettings = FrameSettings(),
    *,
    sample_rate: float = 2_000_000,
    bit_rate: float = 250_000,
    deviation: float = 125_000,
    clock: Callable[[], float] = time.monotonic,
) -> FrameFSKModem:
    """Build an offline/raw modem; application modes and YAML selection remain separate."""
    if any(not math.isfinite(v) or v <= 0 for v in (sample_rate, bit_rate, deviation)):
        raise ValueError("rates and deviation must be positive and finite")
    sps = sample_rate / bit_rate
    if not math.isfinite(sps) or not sps.is_integer() or sps < 4:
        raise ValueError("2FSK requires integral samples per bit >=4")
    codec = SimpleFrameCodec(settings)
    protection = FrameProtection(settings)
    processing = BurstProcessing(protection, SimpleEnvelope(settings, protection))
    modem = FrameFSKModem(
        int(sps),
        sample_rate=sample_rate,
        frequency_deviation_hz=deviation,
        max_frame=codec.payload_size(settings.fixed_payload_bytes or settings.max_payload_bytes),
        processing=processing,
        clock=clock,
    )
    modem.candidate_validator = codec.candidate_valid
    return modem
