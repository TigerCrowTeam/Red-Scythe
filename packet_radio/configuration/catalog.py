"""Describe schema-2 parameters and context-dependent editor visibility.

FieldSpec inventories persisted fields, display-only values and deferred controls.
Selection describes form choices, not a validated radio configuration. visible_fields
filters Basic/Advanced forms without importing Textual or UHD. Defaults/constraints
are presentation hints: load_config and the processing validators remain authoritative.
This module never edits a document, starts a radio or validates RF settings.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from typing import Any, Literal, get_args, get_origin, get_type_hints

from packet_radio.config import (
    AuthenticationConfig,
    EndpointConfig,
    LinkConfig,
    ModemProfile,
    RecordingConfig,
    SDRConfig,
    StatusConfig,
)
from packet_radio.protocol.simple import FrameSettings
from packet_radio.protocol.simple_link import SimpleLinkSettings

MODULATIONS = ("bpsk", "2fsk", "msk", "gmsk", "qpsk")
FORMATS = ("generic-v5", "simple-frame-v1")


def compatible_formats(modulation: str) -> tuple[str, ...]:
    """Return currently implemented formats, not proposed future capabilities."""
    if modulation not in MODULATIONS:
        raise ValueError(f"unsupported modulation: {modulation}")
    return FORMATS if modulation == "2fsk" else ("generic-v5",)


@dataclass(frozen=True)
class Selection:
    """Valid form selectors; partial numeric/text draft values belong elsewhere."""

    modulation: str = "bpsk"
    packet_format: str = "generic-v5"
    frequency_mode: str = "fixed"
    operation: str = "bidirectional"
    device_type: str = "x300"
    rrc: bool = False
    fec: str = "extended-hamming-8-4"
    fixed_length: bool = False
    crc: str = "crc16-ccitt-false"
    timed_io: bool = True
    clock_sync: bool = False
    tdd: bool = False
    slot_scheduling: bool = False
    decoder_worker: bool = True
    recording: bool = False
    http_status: bool = True
    status_output: str = "text"

    def __post_init__(self) -> None:
        if self.packet_format not in compatible_formats(self.modulation):
            raise ValueError("packet format is incompatible with modulation")
        for name, choices in (
            ("frequency_mode", ("fixed", "hopping")),
            ("operation", ("bidirectional", "transmit-only", "receive-only")),
            ("device_type", ("auto", "b200", "x300")),
            ("fec", ("none", "extended-hamming-8-4")),
            ("crc", ("none", "crc16-ccitt-false", "crc32-iso-hdlc")),
            ("status_output", ("off", "text", "json")),
        ):
            if getattr(self, name) not in choices:
                raise ValueError(f"unsupported {name}")
        if self.packet_format == "generic-v5" and (
            self.frequency_mode != "fixed"
            or self.operation != "bidirectional"
            or self.fec != "extended-hamming-8-4"
        ):
            raise ValueError("generic-v5 requires fixed frequency, bidirectional operation and FEC")
        if self.rrc and self.modulation not in ("bpsk", "qpsk"):
            raise ValueError("RRC is only supported for BPSK/QPSK")

    @property
    def tags(self) -> frozenset[str]:
        tags = {self.modulation, self.frequency_mode, self.device_type}
        if self.device_type == "auto":
            tags.update(("b200", "x300"))
        tags.add("generic" if self.packet_format == "generic-v5" else "simple")
        if self.modulation in ("bpsk", "qpsk"):
            tags.add("psk")
        if self.modulation in ("msk", "gmsk"):
            tags.add("minimum_shift")
        if self.operation != "receive-only":
            tags.add("tx")
        if self.operation != "transmit-only":
            tags.add("rx")
        tags.add("fixed_length" if self.fixed_length else "variable_length")
        for name, enabled in (
            ("rrc", self.rrc),
            ("fec", self.fec != "none"),
            ("crc", self.crc != "none"),
            ("timed", self.timed_io),
            ("clock_sync", self.clock_sync),
            ("tdd", self.tdd),
            ("slots", self.slot_scheduling),
            ("decoder", self.decoder_worker),
            ("recording", self.recording),
            ("http", self.http_status),
            ("status_output", self.status_output != "off"),
        ):
            if enabled:
                tags.add(name)
        return frozenset(tags)


@dataclass(frozen=True)
class FieldSpec:
    """A stable namespaced source path plus widget-independent presentation metadata."""

    key: str
    section: str
    label: str
    editor: str
    help: str
    unit: str = ""
    default_hint: str = "Selected preset; required when no default exists"
    constraint_hint: str = "Use the existing configuration/processing validator"
    minimum: float | None = None
    maximum: float | None = None
    multiple_of: int | None = None
    choices: tuple = ()
    requires: frozenset[str] = frozenset()
    advanced: bool = False
    access: Literal["editable", "read_only", "deferred"] = "editable"
    reason: str = ""

    @property
    def persistence(self) -> str:
        if self.key.startswith("profile."):
            return "profile definition or supported node override"
        if self.key.startswith("node."):
            return "node YAML"
        return "not persisted"

    @property
    def validation_path(self) -> str:
        return self.key.partition(".")[2]

    @property
    def restart_required(self) -> bool:
        return self.access == "editable"

    def visible(self, selection: Selection, *, advanced: bool = False) -> bool:
        return (
            self.access != "deferred"
            and (advanced or not self.advanced)
            and self.requires <= selection.tags
        )


def _field(key: str, section: str, *, model=None, member=None, **kwargs) -> FieldSpec:
    name = member or key.rsplit(".", 1)[-1]
    label = name.replace("_", " ").capitalize()
    base: dict[str, Any] = dict(label=label, editor="text", help=f"Configure {label.lower()}.")
    # Inclusive schema limits for widget hints. These do not replace validation:
    # waveform, timing and UHD limits depend on the whole draft and selected device.
    if key in NUMERIC_LIMITS:
        minimum, maximum, multiple_of = NUMERIC_LIMITS[key]
        base.update(minimum=minimum, maximum=maximum, multiple_of=multiple_of)
    if model is not None:
        item = next(f for f in fields(model) if f.name == name)
        annotation = get_type_hints(model)[name]
        choices = get_args(annotation) if get_origin(annotation) is Literal else ()
        base["choices"] = choices
        default = item.default
        if default is not MISSING:
            base["default_hint"] = repr(default)
        elif item.default_factory is not MISSING:
            base["default_hint"] = repr(item.default_factory())
        base["editor"] = (
            "select"
            if choices
            else "toggle"
            if annotation is bool
            else "integer"
            if annotation is int
            else "number"
            if annotation is float
            else "text"
        )
    if name.endswith("_seconds"):
        base["unit"] = "s"
    elif name.endswith("_hz"):
        base["unit"] = "Hz"
    elif name.endswith("_bps"):
        base["unit"] = "bit/s"
    base.update(kwargs)
    if "requires" in base:
        base["requires"] = frozenset(base["requires"])
    return FieldSpec(key=key, section=section, **base)


NUMERIC_LIMITS: dict[str, tuple[float | None, float | None, int | None]] = {
    "node.node_id": (1, 255, None),
    "node.peer_id": (1, 255, None),
    "node.endpoint.bind_port": (1, 65535, None),
    "node.endpoint.destination_port": (1, 65535, None),
    "node.endpoint.drain_max_packets": (1, 256, None),
    "profile.packet.max_payload_bytes": (1, 1024, None),
    "profile.packet.frame.preamble_repetitions": (1, 256, None),
    "profile.protection.interleaver_depth": (1, 256, None),
    "profile.protection.rs_parity_bytes": (0, 254, 2),
    "profile.protection.bit_repetitions": (1, None, None),
    "profile.waveform.rrc_rolloff": (0, 1, None),
    "profile.waveform.rrc_span_symbols": (2, None, 2),
    "node.hardware.channel": (0, None, None),
    "node.hardware.open_retry_attempts": (1, 60, None),
    "node.hardware.open_retry_seconds": (0, 30, None),
    "node.runtime.decoder_output_batches": (16, 65536, None),
}


def _build_catalog() -> tuple[FieldSpec, ...]:
    result: list[FieldSpec] = []
    conditions: tuple[str, ...]

    def add(key, section, **kwargs):
        result.append(_field(key, section, **kwargs))

    for key, editor, hint in (
        ("schema_version", "integer", "2"),
        ("profile", "path", "Choose a reusable definition"),
        ("node_id", "integer", "Required; 1..255"),
        ("peer_id", "integer", "Required; 1..255"),
        ("timing_role", "select", "Required; leader or follower"),
        ("operation", "select", "bidirectional"),
        ("input_kind", "select", "payload"),
        ("log_level", "select", "INFO"),
    ):
        add(
            f"node.{key}",
            "Project",
            editor=editor,
            default_hint=hint,
            access="read_only" if key == "schema_version" else "editable",
            reason="Schema-2 editor" if key == "schema_version" else "",
            advanced=key == "log_level",
            requires=("simple", "tx")
            if key == "input_kind"
            else ("simple",)
            if key == "operation"
            else (),
            choices={
                "timing_role": ("leader", "follower"),
                "operation": ("bidirectional", "transmit-only", "receive-only"),
                "input_kind": ("payload", "frame"),
                "log_level": ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
            }.get(key, ()),
        )
    add(
        "node.overrides",
        "Project",
        access="read_only",
        editor="summary",
        reason="Edited through individual profile controls, not an unchecked raw mapping",
        help="Explicit node overrides; resolved values retain their preset/override source.",
    )
    for name in ("profile_version", "id", "revision", "reference", "qualification", "limitations"):
        add(
            f"profile.{name}",
            "Project",
            access="read_only",
            editor="summary",
            reason="Profile provenance is managed by clone/save services, not a qualification toggle",
            help="Source profile provenance; changing effective settings creates a custom profile.",
        )

    for name in (
        "modulation",
        "bit_rate_bps",
        "frequency_deviation_hz",
        "gaussian_bt",
        "gaussian_span_symbols",
        "rrc_rolloff",
        "rrc_span_symbols",
    ):
        rules = {
            "frequency_deviation_hz": ("2fsk",),
            "gaussian_bt": ("gmsk",),
            "gaussian_span_symbols": ("gmsk",),
            "rrc_rolloff": ("psk", "rrc"),
            "rrc_span_symbols": ("psk", "rrc"),
        }
        hints = {
            "frequency_deviation_hz": "Positive tone offset; < sample rate / 4",
            "gaussian_bt": "0.3 or 0.5",
            "gaussian_span_symbols": "4 or 6 symbols",
            "rrc_rolloff": "0..1; zero selects rectangular pulses",
            "rrc_span_symbols": "Even integer >=2",
            "bit_rate_bps": "Positive; integral supported samples/symbol",
        }
        add(
            f"profile.waveform.{name}",
            "Radio",
            model=ModemProfile,
            requires=rules.get(name, ()),
            editor="select"
            if name in ("modulation", "gaussian_bt", "gaussian_span_symbols")
            else "integer"
            if name == "rrc_span_symbols"
            else "number",
            choices={
                "modulation": MODULATIONS,
                "gaussian_bt": (0.3, 0.5),
                "gaussian_span_symbols": (4, 6),
            }.get(name, ()),
            constraint_hint=hints.get(name, "Supported modem registry entry"),
            default_hint="Required for this modulation; use the selected preset"
            if name in ("frequency_deviation_hz", "gaussian_bt", "gaussian_span_symbols")
            else "Selected preset; backend default applies when optional",
        )
    for name, unit in (("sample_rate_hz", "sample/s"), ("bandwidth_hz", "Hz")):
        add(
            f"profile.waveform.{name}",
            "Radio",
            editor="number",
            unit=unit,
            constraint_hint="Positive; bandwidth <= sample rate and covers selected waveform",
        )
    add(
        "derived.pulse_shaping",
        "Radio",
        editor="select",
        requires=("psk",),
        choices=("rectangular", "rrc"),
        help="Presentation selector: maps to zero/nonzero RRC rolloff; no new YAML key.",
    )
    for name, unit, conditions, explanation in (
        ("symbol_rate_baud", "baud", (), "Bit rate divided by bits per symbol"),
        ("samples_per_symbol", "samples/symbol", (), "Sample rate divided by symbol rate"),
        (
            "maximum_airtime",
            "s",
            (),
            "Calculated by the selected codec and modem, including protection",
        ),
        ("frequency_deviation_hz", "Hz", ("minimum_shift",), "MSK/GMSK deviation is bit rate / 4"),
        ("hop_rate", "hops/s", ("hopping",), "Reciprocal of dwell; use one canonical dwell value"),
    ):
        add(
            f"derived.{name}",
            "Frequency" if name == "hop_rate" else "Radio",
            editor="number",
            unit=unit,
            requires=conditions,
            access="editable" if name == "hop_rate" else "read_only",
            reason=explanation,
            help=explanation,
            default_hint="Computed from the selected configuration",
        )

    frequency = {
        "mode": ("select", (), ("fixed", "hopping")),
        "center_frequency_hz": ("number", ("fixed",), ()),
        "frequencies_hz": ("frequency_table", ("hopping",), ()),
        "dwell_seconds": ("number", ("hopping",), ()),
        "guard_seconds": ("number", ("hopping",), ()),
        "epoch": ("utc_timestamp", ("hopping",), ()),
        "synchronization": ("summary", ("hopping",), ("external-utc",)),
        "sequence_mode": ("select", ("hopping",), ("cycle", "ping_pong")),
        "repeated_frequencies": ("summary", ("hopping",), ("reject",)),
    }
    for name, (editor, conditions, choices) in frequency.items():
        fixed = name in ("synchronization", "repeated_frequencies", "dwell_seconds")
        add(
            f"profile.frequency.{name}",
            "Frequency",
            editor=editor,
            requires=conditions,
            choices=choices,
            access="read_only" if fixed else "editable",
            reason="Canonical dwell is edited through hop rate"
            if name == "dwell_seconds"
            else "Only the existing UTC/no-duplicates contract is supported"
            if fixed
            else "",
            help="Ordered frequencies and UTC schedule; validated by normalize_frequency.",
            constraint_hint="Hopping: 2..256 unique frequencies; dwell >=0.5 s, guard >=0.1 s; 2*guard < dwell; device limits also apply",
        )

    add(
        "profile.packet.format",
        "Framing",
        editor="select",
        choices=FORMATS,
        help="Select a compatible definition; format cannot change through a leaf override.",
    )
    add(
        "profile.packet.max_payload_bytes",
        "Framing",
        editor="integer",
        unit="bytes",
        constraint_hint="1..1024; must fit the selected length field",
    )
    for name in (
        "preamble",
        "preamble_repetitions",
        "sync",
        "bit_order",
        "length_bytes",
        "length_byte_order",
        "fixed_payload_bytes",
        "crc",
        "crc_byte_order",
        "whitening",
        "differential",
    ):
        requires = ["simple"]
        if name in ("length_bytes", "length_byte_order"):
            requires.append("variable_length")
        if name == "fixed_payload_bytes":
            requires.append("fixed_length")
        if name == "crc_byte_order":
            requires.append("crc")
        kwargs = dict(editor="hex") if name in ("preamble", "sync") else {}
        if name == "fixed_payload_bytes":
            kwargs["editor"] = "integer"
        add(
            f"profile.packet.frame.{name}",
            "Framing",
            model=FrameSettings,
            requires=requires,
            access="deferred" if name in ("whitening", "differential") else "editable",
            reason="Only none/False supported by the backend; preserve canonical defaults"
            if name in ("whitening", "differential")
            else "",
            **kwargs,
        )
    add(
        "derived.length_mode",
        "Framing",
        editor="select",
        requires=("simple",),
        choices=("variable", "fixed"),
        help="Maps to length_bytes and fixed_payload_bytes; not an additional YAML field.",
    )
    for name, member in (
        ("scheme", "fec"),
        ("interleaver_depth", "interleaver_depth"),
        ("rs_parity_bytes", "rs_parity_bytes"),
        ("bit_repetitions", None),
    ):
        add(
            f"profile.protection.{name}",
            "Protection",
            model=FrameSettings if member else SDRConfig,
            member=member,
            requires=("bpsk", "generic")
            if name == "bit_repetitions"
            else ("fec",)
            if name != "scheme"
            else (),
            advanced=name != "scheme",
        )

    portable = (
        "ack_timeout_seconds max_retries duplicate_window_seconds channel_busy_threshold "
        "backoff_min_seconds backoff_max_seconds tdd_period_seconds tdd_guard_seconds "
        "tdd_inter_burst_seconds tdd_leader_bursts_per_slot tdd_follower_bursts_per_slot "
        "tdd_staggered_windows hardware_clock_sync hardware_slot_scheduling"
    ).split()
    add(
        "profile.link.mode",
        "Link",
        access="read_only",
        editor="summary",
        reason="Reliable for generic-v5, ACK-free for simple-frame-v1; selected by definition",
    )
    for name in portable:
        requires = ["generic"]
        if name.startswith("tdd_") and name != "tdd_period_seconds":
            requires.append("tdd")
        if name.startswith("hardware_"):
            requires.append("timed")
        add(
            f"profile.link.{name}",
            "Link",
            model=LinkConfig,
            requires=requires,
            advanced=True,
            **({"editor": "number"} if name == "tdd_inter_burst_seconds" else {}),
        )
    for name in ("minimum_gap_seconds", "minimum_interval_seconds", "turnaround_seconds"):
        add(
            f"profile.link.{name}",
            "Link",
            model=SimpleLinkSettings,
            requires=("simple", "tx", "rx") if name == "turnaround_seconds" else ("simple", "tx"),
            advanced=True,
        )

    for name in (
        "device",
        "tx_gain",
        "rx_gain",
        "tx_antenna",
        "rx_antenna",
        "channel",
        "open_retry_attempts",
        "open_retry_seconds",
    ):
        add(
            f"node.hardware.{name}",
            "Hardware",
            model=SDRConfig,
            requires=("tx",)
            if name.startswith("tx_")
            else ("rx",)
            if name.startswith("rx_")
            else (),
            advanced=name.startswith("open_") or name == "device",
            editor="device_arguments"
            if name == "device"
            else "integer"
            if name in ("channel", "open_retry_attempts")
            else "antenna"
            if name.endswith("antenna")
            else "number",
            unit="dB" if name.endswith("gain") else "s" if name.endswith("seconds") else "",
            help="Additional UHD string arguments exclude type/serial/addr, edited separately."
            if name == "device"
            else "Device-specific setting; validate against probed capabilities.",
        )
    for name, conditions in (("type", ()), ("serial", ("b200",)), ("addr", ("x300",))):
        add(
            f"node.hardware.device.{name}",
            "Hardware",
            requires=conditions,
            editor="select" if name == "type" else "text",
            choices=("auto", "b200", "x300") if name == "type" else (),
            help="Select the UHD device; allow manual entry while hardware is disconnected.",
        )
    host = (
        "queue_size control_queue_size queue_airtime_seconds queue_max_age_seconds "
        "tdd_receive_delay_compensation_seconds hardware_timed_io hardware_timed_tx_lead_seconds "
        "hardware_sync_tx_lead_seconds hardware_min_tx_lead_seconds hardware_tx_batch_packets "
        "hardware_epoch_lead_seconds hardware_slot_lookahead_seconds hardware_slot_submission_margin_seconds"
    ).split()
    # Explicit inventory: backend additions must be reviewed rather than silently exposed.
    for name in host:
        requires = ["tx"]
        if name == "control_queue_size":
            requires.append("generic")
        if name.startswith("hardware_") and name not in (
            "hardware_timed_io",
            "hardware_tx_batch_packets",
        ):
            requires.append("timed")
        if name in ("hardware_sync_tx_lead_seconds", "hardware_epoch_lead_seconds"):
            requires.extend(("generic", "clock_sync"))
        if name in ("hardware_slot_lookahead_seconds", "hardware_slot_submission_margin_seconds"):
            requires.extend(("generic", "slots"))
        if name == "tdd_receive_delay_compensation_seconds":
            requires.extend(("generic", "tdd"))
        add(
            f"node.runtime.{name}",
            "Advanced",
            model=LinkConfig,
            requires=requires,
            advanced=True,
            editor="number"
            if name in ("queue_airtime_seconds", "queue_max_age_seconds")
            else "toggle"
            if name == "hardware_timed_io"
            else "integer"
            if name.endswith(("size", "packets"))
            else "number",
        )
    for name in ("decoder_worker_enabled", "decoder_output_batches"):
        add(
            f"node.runtime.{name}",
            "Advanced",
            model=SDRConfig,
            advanced=True,
            requires=("rx", "decoder") if name.endswith("batches") else ("rx",),
        )

    groups = (
        (
            "endpoint",
            EndpointConfig,
            "Local I/O",
            "bind_host bind_port destination_host destination_port drain_max_packets",
        ),
        (
            "authentication",
            AuthenticationConfig,
            "Authentication",
            "private_key_file public_key_files replay_window sync_max_age_seconds sync_max_future_seconds replay_state_file",
        ),
        (
            "status",
            StatusConfig,
            "Status",
            "output interval_seconds http_enabled http_host http_port",
        ),
        (
            "recording",
            RecordingConfig,
            "Recording",
            "directory iq frames max_file_bytes max_sessions min_free_bytes",
        ),
    )
    for group, model, section, names in groups:
        for name in names.split():
            requires = []
            if group == "endpoint":
                requires.append("rx" if name.startswith("destination_") else "tx")
            if group == "authentication":
                requires.append("generic")
            if group == "status" and name in ("http_host", "http_port"):
                requires.append("http")
            if group == "status" and name == "interval_seconds":
                requires.append("status_output")
            if group == "recording" and name != "directory":
                requires.append("recording")
            opts = {}
            if name.endswith(("_file", "directory")):
                opts["editor"] = "path"
            if name == "public_key_files":
                opts["editor"] = "key_paths"
            add(
                f"node.{group}.{name}",
                section,
                model=model,
                requires=requires,
                advanced=group in ("status", "recording")
                or name
                in (
                    "drain_max_packets",
                    "replay_window",
                    "sync_max_age_seconds",
                    "sync_max_future_seconds",
                ),
                unit="bytes" if name.endswith("bytes") else "s" if name.endswith("seconds") else "",
                **opts,
            )
    return tuple(result)


CATALOG = _build_catalog()
FIELDS = {field.key: field for field in CATALOG}


def visible_fields(selection: Selection, *, advanced: bool = False) -> tuple[FieldSpec, ...]:
    """Filter the form; Advanced never bypasses capability/dependency rules."""
    return tuple(field for field in CATALOG if field.visible(selection, advanced=advanced))


def available_choices(key: str, selection: Selection) -> tuple:
    """Narrow selector choices for the current implemented backend capabilities."""
    field = FIELDS[key]
    if not field.visible(selection, advanced=True):
        return ()
    if key == "profile.packet.format":
        return compatible_formats(selection.modulation)
    if key == "profile.frequency.mode":
        return ("fixed", "hopping") if selection.packet_format == "simple-frame-v1" else ("fixed",)
    if key == "profile.protection.scheme" and selection.packet_format == "generic-v5":
        return ("extended-hamming-8-4",)
    return field.choices
