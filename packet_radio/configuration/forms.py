"""Translate form values, select explicit presets and maintain applicable draft fields.

This adapter contains no terminal widgets. It keeps incomplete text in the UI,
converts display units without rounding integer inputs, and resets newly hidden
controls to valid defaults when a capability selector changes.
"""

from __future__ import annotations

import copy
import re
import sysconfig
from dataclasses import asdict
from decimal import Decimal, DecimalException, localcontext
from pathlib import Path

from packet_radio.config import (
    AuthenticationConfig,
    EndpointConfig,
    LinkConfig,
    RecordingConfig,
    SDRConfig,
    StatusConfig,
)
from packet_radio.configuration.catalog import FIELDS, Selection, visible_fields
from packet_radio.configuration.draft import RadioDraft
from packet_radio.profiles import HARDWARE_FIELDS, HOST_LINK, normalize_definition, read_yaml


def template_root() -> Path:
    for root in (
        Path(__file__).resolve().parents[2] / "configs",
        Path(sysconfig.get_path("data")) / "share/packet-radio/configs",
    ):
        if (root / "profiles/simple-2fsk-v1.yaml").is_file():
            return root
    raise ValueError(
        "Radio templates are missing; reinstall packet-radio with its configuration data."
    )


def preset(modulation="2fsk", packet_format="simple-frame-v1") -> dict:
    name = (
        "simple-2fsk-v1"
        if packet_format == "simple-frame-v1"
        else f"generic-{modulation}-unslotted-v5"
    )
    definition = read_yaml(template_root() / "profiles" / f"{name}.yaml")
    if packet_format == "simple-frame-v1" and modulation != "2fsk":
        from packet_radio.modems.registry import waveform

        definition["waveform"] = read_yaml(
            template_root() / "profiles" / f"generic-{modulation}-unslotted-v5.yaml"
        )["waveform"]
        definition["id"] = f"simple-{modulation}-v1"
        definition["qualification"] = "unqualified"
        definition["reference"] = "docs/CONFIGURATION.md: simple-frame modem contract"
        definition["limitations"] = [
            "Project-defined frame; no commercial-radio interoperability claim.",
            "Calculated payload capacity is not hardware qualification; test the exact settings.",
        ]
        definition["waveform"]["bit_rate_bps"] = 250000
        definition["packet"]["frame"]["preamble"] = waveform(modulation).training.hex()
        definition["packet"]["frame"]["preamble_repetitions"] = 1
    return normalize_definition(definition)


def lookup(document, path, default=None):
    for part in path.split("."):
        if not isinstance(document, dict) or part not in document:
            return default
        document = document[part]
    return copy.deepcopy(document)


def display_unit(key):
    field = FIELDS[key]
    if field.unit == "Hz":
        return "MHz", Decimal("1000000")
    if field.unit == "sample/s":
        return "Msps", Decimal("1000000")
    if field.unit == "bit/s":
        return "kbit/s", Decimal("1000")
    if field.unit == "s" and not key.endswith("epoch"):
        return "ms", Decimal("0.001")
    return field.unit, Decimal(1)


def format_value(key, value):
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        unit, factor = display_unit(key)
        number = Decimal(str(value)) / factor
        return f"{number.normalize():f}" + (f" {unit}" if unit else "")
    return str(value)


def parse_value(key, text):
    field = FIELDS[key]
    if field.editor in ("integer", "number", "utc_timestamp"):
        if not text.strip() and key in {
            "node.runtime.queue_airtime_seconds",
            "node.runtime.queue_max_age_seconds",
            "profile.link.tdd_inter_burst_seconds",
        }:
            return None
        match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*?)\s*", text)
        if not match:
            raise ValueError("Enter a finite number, with an optional unit.")
        canonical = field.unit
        units = {"": Decimal(1), canonical: Decimal(1)}
        if canonical == "Hz":
            units.update(Hz=Decimal(1), kHz=Decimal(1000), MHz=Decimal(1000000))
        elif canonical == "sample/s":
            units.update(sps=Decimal(1), ksps=Decimal(1000), Msps=Decimal(1000000))
        elif canonical == "bit/s":
            units.update(bps=Decimal(1), **{"kbit/s": Decimal(1000), "Mbit/s": Decimal(1000000)})
        elif canonical == "s":
            units.update(s=Decimal(1), ms=Decimal("0.001"))
        if match[2] not in units:
            raise ValueError(f"Use units: {', '.join(u for u in units if u) or 'unitless'}.")
        try:
            with localcontext() as context:
                context.prec = max(50, len(match[1]) + 20)
                value = Decimal(match[1]) * units[match[2]]
            if (
                not value.is_finite()
                or not float(value) < float("inf")
                or not float(value) > -float("inf")
            ):
                raise ValueError("Enter a finite number.")
            if float(value) == 0 and Decimal(match[1]) != 0:
                raise ValueError("Value is too small to represent; it will not be rounded to zero.")
            if field.editor == "integer" and value != value.to_integral_value():
                raise ValueError("Enter a whole number; values are not rounded.")
        except (DecimalException, OverflowError) as exc:
            raise ValueError("Enter a finite number.") from exc
        if field.minimum is not None and value < Decimal(str(field.minimum)):
            raise ValueError(f"Minimum: {format_value(key, field.minimum)}.")
        if field.maximum is not None and value > Decimal(str(field.maximum)):
            raise ValueError(f"Maximum: {format_value(key, field.maximum)}.")
        if field.multiple_of and int(value) % field.multiple_of:
            raise ValueError(f"Must be a multiple of {field.multiple_of}.")
        return int(value) if field.editor == "integer" else float(value)
    if field.editor == "hex":
        try:
            return bytes.fromhex(text).hex()
        except ValueError as exc:
            raise ValueError("Enter complete hexadecimal bytes, e.g. a7 c3 5a 9e.") from exc
    if field.editor in ("path", "antenna") and not text.strip():
        return None
    return text


class FormSession:
    """A working copy of a valid project, with effective defaults for every control."""

    def __init__(self, source: RadioDraft):
        report = source.validate()
        if not report.valid:
            raise ValueError(report.issues[0].message)
        config = report.config
        assert config is not None and config.emulation is not None
        node = source.node
        self.origins = source.node.get("overrides", {})
        node.pop("overrides", None)
        sdr = asdict(SDRConfig())
        for name, defaults in (
            ("hardware", {k: sdr[k] for k in HARDWARE_FIELDS}),
            (
                "runtime",
                {
                    **{k: asdict(LinkConfig())[k] for k in HOST_LINK},
                    "decoder_worker_enabled": sdr["decoder_worker_enabled"],
                    "decoder_output_batches": sdr["decoder_output_batches"],
                },
            ),
            (
                "endpoint",
                {k: v for k, v in asdict(EndpointConfig()).items() if k != "max_payload_bytes"},
            ),
            ("authentication", asdict(AuthenticationConfig())),
            ("status", asdict(StatusConfig())),
            ("recording", asdict(RecordingConfig())),
        ):
            node[name] = {**defaults, **node.get(name, {})}
        for name, value in (
            ("operation", "bidirectional"),
            ("input_kind", "payload"),
            ("log_level", "INFO"),
        ):
            node.setdefault(name, value)
        node["log_level"] = node["log_level"].upper()
        self.draft = RadioDraft(node, config.emulation, source.node_path)
        self.draft.source_versions = source.source_versions.copy()
        self.draft.read_only_source = source.read_only_source
        self.replaced_profile = False
        self.defaults = {"node": copy.deepcopy(node), "profile": copy.deepcopy(config.emulation)}
        self.report = self.draft.validate()
        # Reject configurations outside this editor's supported selector set with
        # a normal open error, before constructing widgets with invalid choices.
        _ = self.selection

    @property
    def unsaved(self):
        return (
            self.draft.node_path not in self.draft.source_versions
            or self.draft.read_only_source
            or self.replaced_profile
            or self.draft.dirty
        )

    @classmethod
    def new(cls):
        node = read_yaml(template_root() / "v1_1/peer-a-simple.yaml")
        node["profile"] = "radio-profile.yaml"
        node["operation"] = "bidirectional"
        return cls(RadioDraft(node, preset(), Path.cwd() / "radio-node.yaml"))

    @property
    def selection(self):
        n, p = self.draft.node, self.draft.profile
        w, f, link = p["waveform"], p["packet"].get("frame", {}), p["link"]
        return Selection(
            modulation=w["modulation"],
            packet_format=p["packet"]["format"],
            frequency_mode=p["frequency"]["mode"],
            operation=n["operation"],
            device_type=n["hardware"]["device"].get("type", "auto"),
            rrc=bool(w["rrc_rolloff"]),
            fec=p["protection"]["scheme"],
            fixed_length=f.get("fixed_payload_bytes") is not None,
            crc=f.get("crc", "none"),
            timed_io=n["runtime"]["hardware_timed_io"],
            clock_sync=link.get("hardware_clock_sync", False),
            tdd=bool(link.get("tdd_period_seconds", 0)),
            slot_scheduling=link.get("hardware_slot_scheduling", False),
            decoder_worker=n["runtime"]["decoder_worker_enabled"],
            recording=n["recording"]["directory"] is not None,
            http_status=n["status"]["http_enabled"],
            status_output=n["status"]["output"],
        )

    def value(self, key):
        if key == "profile.packet.payload_sizing":
            return self.draft.profile["packet"].get("payload_sizing", "manual")
        if key == "node.hardware.device.type":
            return self.draft.node["hardware"]["device"].get("type", "auto")
        if key == "node.overrides":
            return self.origins
        if key in ("profile.id", "profile.qualification") and self.report.config is not None:
            emulation = self.report.config.emulation
            if emulation is not None:
                return emulation[key.split(".")[1]]
        if key == "derived.pulse_shaping":
            return "rrc" if self.selection.rrc else "rectangular"
        if key == "derived.length_mode":
            return "fixed" if self.selection.fixed_length else "variable"
        if key == "derived.hop_rate":
            return 1 / self.draft.profile["frequency"]["dwell_seconds"]
        if key.startswith("derived."):
            attr = {"maximum_airtime": "maximum_airtime_seconds"}.get(key[8:], key[8:])
            return getattr(self.report.derived, attr, None)
        return lookup({"node": self.draft.node, "profile": self.draft.profile}, key)

    def change(self, key, value):
        before = self.selection
        if key in ("profile.waveform.modulation", "profile.packet.format"):
            mode = value if key.endswith("modulation") else before.modulation
            fmt = value if key.endswith("format") else before.packet_format
            chosen = preset(mode, fmt)
            self.draft.set("profile.waveform", chosen["waveform"])
            self.draft.set("profile.protection", chosen["protection"])
            if fmt == "simple-frame-v1" and mode != before.modulation:
                # Use a waveform-appropriate acquisition word; retain packet and hop settings.
                for field in ("preamble", "preamble_repetitions", "bit_order"):
                    self.draft.set(
                        f"profile.packet.frame.{field}", chosen["packet"]["frame"][field]
                    )
            if fmt != before.packet_format:
                for section in ("packet", "link", "frequency"):
                    self.draft.set(f"profile.{section}", chosen[section])
                self.draft.set("node.operation", "bidirectional")
                self.draft.set("node.input_kind", "payload")
                if fmt == "simple-frame-v1":
                    self.draft.set("node.authentication", asdict(AuthenticationConfig()))
        elif key == "profile.frequency.mode":
            if value == "hopping":
                chosen = normalize_definition(
                    read_yaml(template_root() / "profiles/simple-2fsk-hopping-v1.yaml")
                )
                self.draft.set("profile.frequency", chosen["frequency"])
            else:
                current = self.draft.profile["frequency"]
                if self.draft.profile["packet"].get("payload_sizing") == "automatic":
                    self.draft.set("profile.packet.payload_sizing", "manual")
                self.draft.set(
                    "profile.frequency",
                    {"mode": "fixed", "center_frequency_hz": current["frequencies_hz"][0]},
                )
        elif key == "derived.pulse_shaping":
            self.draft.set("profile.waveform.rrc_rolloff", 0.35 if value == "rrc" else 0.0)
            self.draft.set("profile.waveform.rrc_span_symbols", 8)
        elif key == "derived.length_mode":
            if (
                value == "fixed"
                and self.draft.profile["packet"].get("payload_sizing") == "automatic"
            ):
                self.draft.set("profile.packet.payload_sizing", "manual")
            self.draft.set("profile.packet.frame.length_bytes", 0 if value == "fixed" else 2)
            self.draft.set(
                "profile.packet.frame.fixed_payload_bytes",
                self.value("profile.packet.max_payload_bytes") if value == "fixed" else None,
            )
        elif key == "derived.hop_rate":
            if value <= 0:
                raise ValueError("Hop rate must be positive.")
            self.draft.set("profile.frequency.dwell_seconds", 1 / value)
        elif key == "node.hardware.device":
            if set(value) & {"type", "serial", "addr"}:
                raise ValueError("Use the dedicated type, serial and address fields.")
            current = self.draft.node["hardware"]["device"]
            self.draft.set(
                key,
                {**{k: v for k, v in current.items() if k in {"type", "serial", "addr"}}, **value},
            )
        else:
            self.draft.set(key, value)
        if key == "node.hardware.device.type":
            device = self.draft.node["hardware"]["device"]
            if value == "auto":
                device.pop("type", None)
            else:
                device.pop("serial" if value == "x300" else "addr", None)
            self.draft.set("node.hardware.device", device)
        if key in ("node.hardware.device.serial", "node.hardware.device.addr") and not value:
            device = self.draft.node["hardware"]["device"]
            device.pop(key.rsplit(".", 1)[1], None)
            self.draft.set("node.hardware.device", device)
        after = self.selection
        # Clear controls that have become irrelevant, including temporarily bad input.
        hidden = {f.key for f in visible_fields(before, advanced=True)} - {
            f.key for f in visible_fields(after, advanced=True)
        }
        for name in hidden:
            if (
                FIELDS[name].access != "editable"
                or name.startswith("derived.")
                or name.startswith("node.hardware.device.")
            ):
                continue
            default = lookup(self.defaults, name)
            if key in ("profile.waveform.modulation", "profile.packet.format") and name.startswith(
                "profile.waveform."
            ):
                continue
            if name.startswith("node.authentication."):
                default = asdict(AuthenticationConfig())[name.rsplit(".", 1)[1]]
            if name.startswith("profile."):
                default = lookup(preset(after.modulation, after.packet_format), name[8:])
                section = name.split(".")[1]
                current = self.draft.profile.get(section, {})
                if name.split(".")[2] not in current:
                    continue
                # Use canonical defaults for transforms, not the previous preset's values.
                if name.endswith("bit_repetitions"):
                    default = 1
                if name.endswith("payload_sizing"):
                    default = "manual"
                if name.endswith("rrc_rolloff"):
                    default = 0.0
                if name.endswith("fixed_payload_bytes"):
                    default = None
                if name.endswith("length_bytes") and after.fixed_length:
                    default = 0
                if name.endswith("tdd_guard_seconds") and not after.tdd:
                    default = 0.0
                if (
                    name.endswith(("hardware_clock_sync", "hardware_slot_scheduling"))
                    and not after.timed_io
                ):
                    default = False
            self.draft.set(name, default)
        self.report = self.draft.validate()
        return before != self.selection
