"""Compose schema-2 host settings with versioned, reusable radio definitions.

Profile normalization freezes defaults, rejects ambiguous/unknown fields and
separates wire behavior from device/host settings. Overrides replace known leaf
values only. The resolved definition travels with RadioConfig and capture metadata;
no profile files are consulted when replaying a capture.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, fields, replace
from pathlib import Path

import yaml

from packet_radio.config import (
    AuthenticationConfig,
    EndpointConfig,
    FrequencyConfig,
    LinkConfig,
    ModemProfile,
    RadioConfig,
    RecordingConfig,
    SDRConfig,
    StatusConfig,
    _section,
)
from packet_radio.hop_budget import MIN_AUTO_GUARD_SECONDS
from packet_radio.modems.registry import samples_per_symbol
from packet_radio.protocol.simple import FrameSettings
from packet_radio.protocol.simple_link import SimpleLinkSettings, SimpleProfile


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate and merge keys rather than letting YAML hide an override."""

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in result:
                    raise ValueError(f"duplicate YAML key: {key}")
                result[key] = self.construct_object(value_node, deep=deep)
            except TypeError as exc:
                raise ValueError("YAML keys must be scalar") from exc
        return result


def read_yaml(path: Path) -> dict:
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a mapping")
    return raw


def mapping(raw, allowed, required=(), label="settings") -> dict:
    if not isinstance(raw, dict) or any(not isinstance(k, str) for k in raw):
        raise ValueError(f"{label} must be a mapping with string keys")
    unknown, missing = set(raw) - set(allowed), set(required) - set(raw)
    if unknown or missing:
        raise ValueError(f"{label}: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}")
    return dict(raw)


HOST_LINK = {
    f.name
    for f in fields(LinkConfig)
    if (
        f.name.startswith("hardware_")
        and f.name not in ("hardware_clock_sync", "hardware_slot_scheduling")
    )
    or f.name
    in (
        "queue_size",
        "control_queue_size",
        "queue_airtime_seconds",
        "queue_max_age_seconds",
        "tdd_receive_delay_compensation_seconds",
    )
}
PORTABLE_LINK = {f.name for f in fields(LinkConfig)} - HOST_LINK
MODEM_FIELDS = {f.name for f in fields(ModemProfile)} - {
    "decoder_worker_enabled",
    "decoder_output_batches",
}
PROTECTION_FIELDS = {"scheme", "interleaver_depth", "rs_parity_bytes", "bit_repetitions"}
FRAME_FIELDS = {f.name for f in fields(FrameSettings)} - {
    "max_payload_bytes",
    "fec",
    "interleaver_depth",
    "rs_parity_bytes",
}
META = {"profile_version", "id", "revision", "reference", "qualification", "limitations"}
SECTIONS = {"waveform", "frequency", "packet", "protection", "link"}

# Parser-owned field inventories also let offline editors detect schema drift.
NODE_FIELDS = {
    "schema_version",
    "profile",
    "overrides",
    "node_id",
    "peer_id",
    "timing_role",
    "operation",
    "input_kind",
    "hardware",
    "runtime",
    "endpoint",
    "authentication",
    "recording",
    "status",
    "log_level",
}
HARDWARE_FIELDS = {
    "device",
    "tx_gain",
    "rx_gain",
    "tx_antenna",
    "rx_antenna",
    "channel",
    "open_retry_attempts",
    "open_retry_seconds",
}
FIXED_FREQUENCY_FIELDS = {"mode", "center_frequency_hz"}
HOPPING_FREQUENCY_FIELDS = {
    "mode",
    "frequencies_hz",
    "dwell_seconds",
    "guard_seconds",
    "epoch",
    "synchronization",
    "sequence_mode",
    "repeated_frequencies",
}
PACKET_FIELDS = {"format", "max_payload_bytes", "frame", "payload_sizing"}


def normalize_frequency(raw, *, automatic_payload=False) -> dict:
    """Normalize UTC hopping; automatic capacity is checked after host settings resolve."""
    if not isinstance(raw, dict):
        raise ValueError("frequency must be a mapping")
    if raw.get("mode") == "fixed":
        result = mapping(raw, FIXED_FREQUENCY_FIELDS, FIXED_FREQUENCY_FIELDS, "frequency")
        values = [result["center_frequency_hz"]]
    elif raw.get("mode") == "hopping":
        keys = HOPPING_FREQUENCY_FIELDS
        result = mapping(raw, keys, keys, "frequency")
        values = result["frequencies_hz"]
        if not isinstance(values, list) or not 2 <= len(values) <= 256:
            raise ValueError("hop table must contain 2..256 frequencies")
        for key in ("dwell_seconds", "guard_seconds", "epoch"):
            value = result[key]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
        if result["epoch"] < 0:
            raise ValueError("hop epoch must be a nonnegative POSIX timestamp")
        if automatic_payload:
            if result["dwell_seconds"] <= 0 or result["guard_seconds"] < MIN_AUTO_GUARD_SECONDS:
                raise ValueError(
                    f"automatic hopping requires positive dwell and guard >= {MIN_AUTO_GUARD_SECONDS:g} s"
                )
        elif result["dwell_seconds"] < 0.5 or result["guard_seconds"] < 0.1:
            raise ValueError("host hopping requires dwell >= 0.5 s and guard >= 0.1 s")
        if 2 * result["guard_seconds"] >= result["dwell_seconds"]:
            raise ValueError("hop guards must leave a usable dwell")
        if result["synchronization"] != "external-utc":
            raise ValueError("hopping requires externally synchronized UTC clocks")
        if result["sequence_mode"] not in ("cycle", "ping_pong"):
            raise ValueError("unsupported hop sequence")
        if result["repeated_frequencies"] != "reject":
            raise ValueError("repeated hop frequencies are not supported")
    else:
        raise ValueError("unsupported frequency mode")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("frequencies must be positive finite numbers")
    if len(set(values)) != len(values):
        raise ValueError("frequency list contains duplicates")
    return result


def frequency_config(definition: dict) -> FrequencyConfig:
    if definition["mode"] == "fixed":
        return FrequencyConfig((definition["center_frequency_hz"],), 0, guard_seconds=0)
    return FrequencyConfig(
        tuple(definition["frequencies_hz"]),
        definition["epoch"],
        interval_seconds=definition["dwell_seconds"],
        guard_seconds=definition["guard_seconds"],
        sequence_mode=definition["sequence_mode"],
    )


def normalize_definition(raw) -> dict:
    raw = mapping(raw, META | SECTIONS, META | SECTIONS, "radio profile")
    if type(raw["profile_version"]) is not int or raw["profile_version"] != 1:
        raise ValueError("unsupported radio profile_version")
    if not isinstance(raw["id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", raw["id"]):
        raise ValueError("invalid profile id")
    if type(raw["revision"]) is not int or raw["revision"] < 1:
        raise ValueError("profile revision must be a positive integer")
    if not isinstance(raw["reference"], str) or not raw["reference"].strip():
        raise ValueError("profile reference must be explicit")
    if raw["qualification"] not in (
        "unqualified",
        "software-verified",
        "short-conducted",
        "qualified",
    ):
        raise ValueError("unsupported qualification label")
    if (
        not isinstance(raw["limitations"], list)
        or not raw["limitations"]
        or any(not isinstance(s, str) or not s.strip() for s in raw["limitations"])
    ):
        raise ValueError("profile limitations must be a nonempty list of strings")
    wf = mapping(
        raw["waveform"],
        MODEM_FIELDS | {"sample_rate_hz", "bandwidth_hz"},
        {"modulation", "bit_rate_bps", "sample_rate_hz", "bandwidth_hz"},
        "waveform",
    )
    rate, bandwidth = wf.pop("sample_rate_hz"), wf.pop("bandwidth_hz")
    for value in (rate, bandwidth):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("waveform sample rate/bandwidth must be positive finite numbers")
    modem = _section(ModemProfile, wf)
    wf = {k: v for k, v in asdict(modem).items() if k in MODEM_FIELDS}
    wf.update(sample_rate_hz=rate, bandwidth_hz=bandwidth)
    if not isinstance(raw.get("packet"), dict):
        raise ValueError("packet must be a mapping")
    sizing = raw["packet"].get("payload_sizing", "manual")
    if sizing not in ("manual", "automatic"):
        raise ValueError("payload_sizing must be manual or automatic")
    freq = normalize_frequency(raw["frequency"], automatic_payload=sizing == "automatic")
    packet = mapping(
        raw["packet"],
        PACKET_FIELDS,
        {"format", "max_payload_bytes"},
        "packet",
    )
    maximum = packet["max_payload_bytes"]
    if sizing == "automatic" and (
        freq["mode"] != "hopping"
        or packet["format"] != "simple-frame-v1"
        or not isinstance(packet.get("frame", {}), dict)
        or packet.get("frame", {}).get("fixed_payload_bytes") is not None
    ):
        raise ValueError(
            "automatic payload sizing requires hopping and variable-length simple frames"
        )
    if type(maximum) is not int or not 1 <= maximum <= 1024:
        raise ValueError("max_payload_bytes must be an integer in 1..1024")
    protection = mapping(raw["protection"], PROTECTION_FIELDS, {"scheme"}, "protection")
    protection = {"interleaver_depth": 16, "rs_parity_bytes": 0, "bit_repetitions": 1, **protection}
    for key in ("interleaver_depth", "rs_parity_bytes", "bit_repetitions"):
        if type(protection[key]) is not int:
            raise ValueError(f"protection {key} must be an integer")
    if (
        not 1 <= protection["interleaver_depth"] <= 256
        or not 0 <= protection["rs_parity_bytes"] <= 254
        or protection["rs_parity_bytes"] % 2
        or protection["bit_repetitions"] < 1
    ):
        raise ValueError("invalid protection parameters")
    link = mapping(
        raw["link"],
        {"mode"}
        | PORTABLE_LINK
        | {"minimum_gap_seconds", "minimum_interval_seconds", "turnaround_seconds"},
        {"mode"},
        "link",
    )
    if packet["format"] == "simple-frame-v1":
        if link.pop("mode") != "ack-free":
            raise ValueError("simple-frame-v1 requires ack-free link")
        opts = _section(SimpleLinkSettings, link)
        frame = mapping(packet.get("frame", {}), FRAME_FIELDS, label="frame")
        for key in ("preamble", "sync"):
            if key in frame:
                if not isinstance(frame[key], str):
                    raise ValueError(f"{key} must be hex text")
                try:
                    frame[key] = bytes.fromhex(frame[key])
                except ValueError as exc:
                    raise ValueError(f"invalid {key} hex") from exc
        cfg = _section(
            FrameSettings,
            dict(
                frame,
                max_payload_bytes=packet["max_payload_bytes"],
                fec=protection["scheme"],
                interleaver_depth=protection["interleaver_depth"],
                rs_parity_bytes=protection["rs_parity_bytes"],
            ),
        )
        if type(protection["bit_repetitions"]) is not int or protection["bit_repetitions"] != 1:
            raise ValueError("simple frames require bit_repetitions=1")
        normalized = asdict(cfg)
        normalized["preamble"] = cfg.preamble.hex()
        normalized["sync"] = cfg.sync.hex()
        packet["frame"] = {k: v for k, v in normalized.items() if k in FRAME_FIELDS}
        link = {
            "mode": "ack-free",
            **{k: v for k, v in asdict(opts).items() if k not in ("operation", "input_kind")},
        }
    elif packet["format"] == "generic-v5":
        if "frame" in packet:
            raise ValueError("generic-v5 framing is fixed")
        if link.pop("mode") != "reliable":
            raise ValueError("generic-v5 requires reliable link")
        mapping(link, PORTABLE_LINK, label="reliable link")
        opts = _section(LinkConfig, link)
        link = {"mode": "reliable", **{k: v for k, v in asdict(opts).items() if k in PORTABLE_LINK}}
        if protection["scheme"] != "extended-hamming-8-4":
            raise ValueError("generic-v5 requires extended-hamming-8-4")
    else:
        raise ValueError("unsupported packet format")
    if freq["mode"] == "hopping" and packet["format"] != "simple-frame-v1":
        raise ValueError("schema-2 hopping currently requires simple-frame-v1")
    return {
        **{k: copy.deepcopy(raw[k]) for k in META},
        "waveform": wf,
        "frequency": freq,
        "packet": packet,
        "protection": protection,
        "link": link,
    }


def merge_overrides(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    if isinstance(overrides, dict) and isinstance(overrides.get("packet"), dict):
        if "payload_sizing" in overrides["packet"]:
            result["packet"].setdefault("payload_sizing", "manual")

    def merge(target, changes):
        mapping(changes, target, label="overrides")
        for key, value in changes.items():
            if isinstance(target[key], dict):
                merge(target[key], value)
            elif isinstance(value, dict):
                raise ValueError("override must replace a leaf value")
            else:
                target[key] = copy.deepcopy(value)

    mapping(overrides, SECTIONS, label="overrides")
    for key, value in overrides.items():
        mapping(value, result[key], label=f"overrides.{key}")
    if "format" in overrides.get("packet", {}) or "mode" in overrides.get("link", {}):
        raise ValueError("select another profile to change packet format or link mode")
    merge(result, overrides)
    if result != base:
        result["id"] += "-custom"
        result["qualification"] = "unqualified"
    return normalize_definition(result)


def processing_for_definition(definition: dict, operation="bidirectional", input_kind="payload"):
    from packet_radio.protocol.registry import GenericV5Profile

    d = normalize_definition(definition)
    if d["packet"]["format"] == "generic-v5":
        if operation != "bidirectional" or input_kind != "payload":
            raise ValueError("generic-v5 requires bidirectional payload operation")
        return GenericV5Profile()
    frame = dict(d["packet"]["frame"])
    for key in ("preamble", "sync"):
        frame[key] = bytes.fromhex(frame[key])
    p = d["protection"]
    settings = _section(
        FrameSettings,
        dict(
            frame,
            max_payload_bytes=d["packet"]["max_payload_bytes"],
            fec=p["scheme"],
            interleaver_depth=p["interleaver_depth"],
            rs_parity_bytes=p["rs_parity_bytes"],
        ),
    )
    return SimpleProfile(
        settings,
        _section(
            SimpleLinkSettings,
            dict(
                {k: v for k, v in d["link"].items() if k != "mode"},
                operation=operation,
                input_kind=input_kind,
            ),
        ),
    )


def definition_fields(d: dict) -> dict:
    """Internal settings owned by the portable profile, excluding host fields."""
    w, p = d["waveform"], d["protection"]
    return dict(
        sample_rate=w["sample_rate_hz"],
        bandwidth=w["bandwidth_hz"],
        samples_per_symbol=samples_per_symbol(
            w["modulation"], w["sample_rate_hz"], w["bit_rate_bps"]
        ),
        **{k: v for k, v in w.items() if k in MODEM_FIELDS - {"bit_rate_bps"}},
        fec_interleaver_depth=p["interleaver_depth"],
        rs_parity_bytes=p["rs_parity_bytes"],
        bit_repetitions=p["bit_repetitions"],
    )


def validate_resolved(config: RadioConfig) -> None:
    d = normalize_definition(config.emulation)
    if d != config.emulation:
        raise ValueError("resolved profile must contain all normalized fields")
    from packet_radio.hop_budget import automatic, payload_limit

    expected_payload = (
        payload_limit(config) if automatic(config) else d["packet"]["max_payload_bytes"]
    )
    if (
        d["packet"]["format"] != config.packet_format
        or expected_payload != config.endpoint.max_payload_bytes
    ):
        raise ValueError("resolved packet settings disagree")
    if config.frequency != frequency_config(d["frequency"]):
        raise ValueError("resolved frequency settings disagree")
    for key, value in definition_fields(d).items():
        if getattr(config.sdr, key) != value:
            raise ValueError(f"resolved waveform {key} disagrees")
    expected = d["link"] if d["link"]["mode"] == "reliable" else asdict(LinkConfig(max_retries=0))
    for key in PORTABLE_LINK:
        if getattr(config.link, key) != expected[key]:
            raise ValueError(f"resolved link {key} disagrees")


def load_v2(raw: dict, path: Path, *, definition: dict | None = None) -> RadioConfig:
    """Resolve a node, optionally using an in-memory profile instead of reading it."""
    raw = mapping(
        raw,
        NODE_FIELDS,
        {"schema_version", "profile", "node_id", "peer_id", "timing_role", "hardware"},
        "node",
    )
    if not isinstance(raw["profile"], str) or not raw["profile"].strip():
        raise ValueError("profile must be a file path")
    definition = normalize_definition(
        read_yaml(path.parent / raw["profile"]) if definition is None else definition
    )
    definition = merge_overrides(definition, raw.get("overrides", {}))
    hardware = mapping(
        raw["hardware"],
        HARDWARE_FIELDS,
        {"device", "tx_gain", "rx_gain", "tx_antenna", "rx_antenna"},
        "hardware",
    )
    runtime = mapping(
        raw.get("runtime", {}),
        HOST_LINK | {"decoder_worker_enabled", "decoder_output_batches"},
        label="runtime",
    )
    sdr = _section(
        SDRConfig,
        {
            **definition_fields(definition),
            **hardware,
            **{k: v for k, v in runtime.items() if k.startswith("decoder_")},
        },
    )
    portable = (
        {k: v for k, v in definition["link"].items() if k != "mode"}
        if definition["link"]["mode"] == "reliable"
        else {"max_retries": 0}
    )
    link = _section(
        LinkConfig, {**portable, **{k: v for k, v in runtime.items() if k in HOST_LINK}}
    )
    endpoint = mapping(
        raw.get("endpoint", {}),
        {f.name for f in fields(EndpointConfig)} - {"max_payload_bytes"},
        label="endpoint",
    )
    endpoint = _section(
        EndpointConfig, dict(endpoint, max_payload_bytes=definition["packet"]["max_payload_bytes"])
    )
    auth = (
        dict(raw.get("authentication", {}))
        if isinstance(raw.get("authentication", {}), dict)
        else raw["authentication"]
    )
    auth = _section(AuthenticationConfig, auth)

    def local(value):
        return str((path.parent / value).resolve()) if value is not None else None

    auth = AuthenticationConfig(
        local(auth.private_key_file),
        {node: tuple(local(p) for p in values) for node, values in auth.public_key_files.items()},
        auth.replay_window,
        auth.sync_max_age_seconds,
        auth.sync_max_future_seconds,
        local(auth.replay_state_file),
    )
    recording = _section(RecordingConfig, raw.get("recording", {}))
    recording = _section(
        RecordingConfig, {**asdict(recording), "directory": local(recording.directory)}
    )
    config = _section(
        RadioConfig,
        dict(
            node_id=raw["node_id"],
            peer_id=raw["peer_id"],
            timing_role=raw["timing_role"],
            sdr=sdr,
            frequency=frequency_config(definition["frequency"]),
            endpoint=endpoint,
            link=link,
            authentication=auth,
            recording=recording,
            status=_section(StatusConfig, raw.get("status", {})),
            log_level=raw.get("log_level", "INFO"),
            schema_version=2,
            packet_format=definition["packet"]["format"],
            emulation=definition,
            operation=raw.get("operation", "bidirectional"),
            input_kind=raw.get("input_kind", "payload"),
        ),
    )
    from packet_radio.hop_budget import automatic, payload_limit

    if automatic(config):
        config = replace(
            config, endpoint=replace(config.endpoint, max_payload_bytes=payload_limit(config))
        )
    return config


def profile_fingerprint(definition: dict) -> str:
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
