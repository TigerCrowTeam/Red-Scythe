"""Validate packet templates, encode named fields, inspect bytes and save YAML safely.

Layouts describe the opaque application payload, not generic-v5 protocol headers.
Length fields count the entire application packet; layout CRCs cover preceding bytes.
Simple-frame acquisition, length and CRC remain owned by the existing radio codec.
"""

from __future__ import annotations

import binascii
import copy
import os
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from packet_radio.protocol.registry import resolve_processing
from packet_radio.protocol.simple import SimpleFrameCodec

KINDS = ("uint", "int", "hex", "text", "payload", "length", "crc16", "crc32")


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}.")
    return value


@dataclass(frozen=True)
class Segment:
    name: str
    kind: str
    offset: int
    data: bytes
    value: str


@dataclass
class PacketTemplate:
    name: str
    packet_format: str
    frame: dict | None
    fields: list[dict]
    payload_encoding: str = "text"
    payload: str = "Hello radio"

    @classmethod
    def new(cls, config):
        return cls(
            "Untitled",
            config.packet_format,
            copy.deepcopy((config.emulation or {}).get("packet", {}).get("frame")),
            [{"name": "payload", "kind": "payload", "width": 0, "order": "big", "value": ""}],
        )

    def document(self):
        return dict(
            version=1,
            name=self.name,
            packet_format=self.packet_format,
            frame=self.frame,
            fields=self.fields,
            payload_encoding=self.payload_encoding,
            payload=self.payload,
        )

    @classmethod
    def from_document(cls, raw):
        keys = {
            "version",
            "name",
            "packet_format",
            "frame",
            "fields",
            "payload_encoding",
            "payload",
        }
        if (
            not isinstance(raw, dict)
            or set(raw) != keys
            or type(raw["version"]) is not int
            or raw["version"] != 1
        ):
            raise ValueError("Expected a version-1 packet template with its documented fields.")
        result = cls(**{k: v for k, v in raw.items() if k != "version"})
        result.encode()
        return result

    def _layout(self, payload_size):
        if not isinstance(self.name, str) or not 1 <= len(self.name) <= 100:
            raise ValueError("Template name must contain 1..100 characters.")
        if self.packet_format not in ("simple-frame-v1", "generic-v5"):
            raise ValueError("Unsupported packet format.")
        if (self.packet_format == "generic-v5" and self.frame is not None) or (
            self.packet_format == "simple-frame-v1" and not isinstance(self.frame, dict)
        ):
            raise ValueError("Only simple-frame templates contain editable framing.")
        if not isinstance(self.fields, list) or not 1 <= len(self.fields) <= 64:
            raise ValueError("Use 1..64 layout fields.")
        names = set()
        layout = []
        for f in self.fields:
            if not isinstance(f, dict) or set(f) != {"name", "kind", "width", "order", "value"}:
                raise ValueError("Each field needs name, kind, width, order and value.")
            name, kind = f["name"], f["kind"]
            if not isinstance(name, str) or not name.strip() or len(name) > 64 or name in names:
                raise ValueError("Field names must be nonempty, unique and at most 64 characters.")
            names.add(name)
            if (
                kind not in KINDS
                or f["order"] not in ("big", "little")
                or not isinstance(f["value"], str)
            ):
                raise ValueError(f"Invalid type, byte order or value for {name}.")
            width = integer(f["width"], 0, 1024, "Field width")
            if kind == "payload":
                if width != 0:
                    raise ValueError("The payload field uses width 0 (automatic).")
                width = payload_size
            elif not width:
                raise ValueError("Non-payload fields require a positive byte width.")
            if kind in ("uint", "int", "length") and width not in (1, 2, 4, 8):
                raise ValueError("Integer/length fields use 1, 2, 4 or 8 bytes.")
            if kind in ("crc16", "crc32") and width != (2 if kind == "crc16" else 4):
                raise ValueError("CRC16 uses 2 bytes; CRC32 uses 4 bytes.")
            layout.append((f, width))
        if sum(f["kind"] == "payload" for f in self.fields) != 1:
            raise ValueError("The layout must contain exactly one automatic payload field.")
        return layout

    def payload_bytes(self):
        if not isinstance(self.payload, str) or len(self.payload) > 4096:
            raise ValueError("Payload input is limited to 4096 characters.")
        if self.payload_encoding not in ("text", "hex"):
            raise ValueError("Payload encoding must be text or hex.")
        try:
            return (
                self.payload.encode("utf-8")
                if self.payload_encoding == "text"
                else bytes.fromhex(self.payload)
            )
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid UTF-8 text or hexadecimal payload.") from exc

    def encode(self):
        payload = self.payload_bytes()
        layout = self._layout(len(payload))
        total = sum(width for _, width in layout)
        integer(total, 1, 1024, "Application packet size")
        data = bytearray()
        segments = []
        for f, width in layout:
            kind = f["kind"]
            try:
                if kind == "payload":
                    part = payload
                elif kind in ("uint", "int", "length", "crc16", "crc32"):
                    value = (
                        total
                        if kind == "length"
                        else binascii.crc_hqx(data, 0xFFFF)
                        if kind == "crc16"
                        else zlib.crc32(data) & 0xFFFFFFFF
                        if kind == "crc32"
                        else int(
                            f["value"], 16 if f["value"].lower().startswith(("0x", "-0x")) else 10
                        )
                    )
                    part = value.to_bytes(width, f["order"], signed=kind == "int")
                else:
                    part = (
                        bytes.fromhex(f["value"]) if kind == "hex" else f["value"].encode("utf-8")
                    )
                if len(part) != width:
                    raise ValueError(
                        "Value must exactly fill its byte width (no implicit padding)."
                    )
            except (ValueError, OverflowError, UnicodeError) as exc:
                raise ValueError(f"{f['name']}: {exc}") from exc
            segments.append(Segment(f["name"], kind, len(data), part, f["value"]))
            data.extend(part)
        return bytes(data), segments

    def inspect(self, data: bytes):
        # Validate the schema independently of received field values.
        self.encode()
        fixed = sum(f["width"] for f in self.fields)
        if not fixed <= len(data) <= 1024 or not data:
            raise ValueError("Received packet does not fit this layout.")
        segments = []
        offset = 0
        for f, width in self._layout(len(data) - fixed):
            part = data[offset : offset + width]
            kind = f["kind"]
            if kind in ("uint", "int", "length", "crc16", "crc32"):
                value = int.from_bytes(part, f["order"], signed=kind == "int")
                expected = (
                    len(data)
                    if kind == "length"
                    else binascii.crc_hqx(data[:offset], 0xFFFF)
                    if kind == "crc16"
                    else zlib.crc32(data[:offset]) & 0xFFFFFFFF
                    if kind == "crc32"
                    else None
                )
                if expected is not None and value != expected:
                    raise ValueError(f"{f['name']}: calculated {kind} mismatch.")
                display = str(value)
            else:
                display = (
                    part.decode("utf-8", errors="replace")
                    if kind == "text" or (kind == "payload" and self.payload_encoding == "text")
                    else part.hex(" ")
                )
            segments.append(Segment(f["name"], kind, offset, part, display))
            offset += width
        return segments

    def with_packet(self, data):
        """Copy received field values into an editable template, preserving exact bytes."""
        segments = self.inspect(data)
        result = copy.deepcopy(self)
        for field, segment in zip(result.fields, segments):
            kind = field["kind"]
            if kind == "payload":
                result.payload_encoding, result.payload = "hex", segment.data.hex(" ")
            elif kind in ("uint", "int"):
                field["value"] = segment.value
            elif kind in ("hex", "text"):
                field["kind"], field["value"] = "hex", segment.data.hex(" ")
        if result.encode()[0] != data:
            raise ValueError("Received bytes cannot be represented by this template.")
        return result

    def validate_radio(self, config):
        data, segments = self.encode()
        if config.packet_format != self.packet_format:
            raise ValueError("Template format differs from the selected radio.")
        if self.frame != (config.emulation or {}).get("packet", {}).get("frame"):
            raise ValueError(
                "Template framing differs from the radio. Apply, save and restart both peers."
            )
        if len(data) > config.endpoint.max_payload_bytes:
            raise ValueError("Application packet exceeds the radio's payload limit.")
        processing = resolve_processing(config)
        if self.frame is not None:
            processing.codec.encode(data)
        return data, segments

    def preview(self, config):
        data, segments = self.validate_radio(config)
        lines = [f"Application packet: {len(data)} bytes"]
        lines.extend(
            f"{s.offset:04x}  {s.name} ({s.kind}, {len(s.data)} B): {s.data.hex(' ')}"
            for s in segments
        )
        if self.frame is not None:
            codec = resolve_processing(config).codec
            assert isinstance(codec, SimpleFrameCodec)
            settings = codec.settings
            body = codec.encode(data)
            lines += [
                "Simple frame before FEC / bit-order mapping:",
                f"Preamble: {settings.training.hex(' ')}",
                f"Sync: {settings.sync.hex(' ')}",
                f"Length: {body[: settings.length_bytes].hex(' ')}",
                f"Application: {data.hex(' ')}",
                f"CRC: {body[-settings.crc_bytes :].hex(' ') if settings.crc_bytes else '(none)'}",
            ]
        else:
            lines.append(
                "generic-v5 protocol header, sequence, authentication and CRC are generated by the radio at transmission."
            )
        return "\n".join(lines)


def load_template(path):
    with Path(path).open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Template file exceeds 64 KiB.")
    try:
        return PacketTemplate.from_document(yaml.safe_load(data))
    except (yaml.YAMLError, TypeError, KeyError) as exc:
        raise ValueError(f"Invalid packet template: {exc}") from exc


def save_template(template, path, *, expected=None):
    template.encode()
    path = Path(path).expanduser().resolve()
    content = yaml.safe_dump(template.document(), sort_keys=False).encode()
    if len(content) > 65536:
        raise ValueError("Template file exceeds 64 KiB.")
    # Refuse unrelated or externally changed files. New saves use an exclusive link.
    if path.exists() and (expected is None or path.read_bytes() != expected):
        raise ValueError("File exists or changed externally; choose a new template path.")
    fd, temporary = tempfile.mkstemp(prefix=".packet-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if expected is None:
            os.link(temporary, path)
        else:
            if not path.exists() or path.read_bytes() != expected:
                raise ValueError("Template changed externally; reopen or save to a new path.")
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return content
