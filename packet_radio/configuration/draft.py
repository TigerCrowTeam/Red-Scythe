"""Edit schema-2 source documents and validate snapshots without hardware or writes.

RadioDraft keeps node/profile source separate from normalized RadioConfig defaults.
Validation uses the same parser, modem and network checks as the CLI. Reports carry
field hints, effective settings and derived values; unknown error locations remain
form-level errors instead of guessing a field. No keys, sockets or SDRs are opened.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packet_radio.config import (
    RadioConfig,
    configuration_snapshot,
    validate_config,
    validate_network,
)
from packet_radio.configuration.catalog import FIELDS
from packet_radio.profiles import load_v2, profile_fingerprint, read_yaml
from packet_radio.protocol.registry import resolve_processing


@dataclass(frozen=True)
class ValidationIssue:
    message: str
    fields: tuple[str, ...] = ()
    scope: str = "configuration"


@dataclass(frozen=True)
class DerivedValues:
    bit_rate_bps: float
    symbol_rate_baud: float
    samples_per_symbol: int
    maximum_airtime_seconds: float
    frequency_deviation_hz: float | None
    hop_rate_hz: float | None
    profile_sha256: str


@dataclass(frozen=True)
class ValidationReport:
    """A snapshot result, never cached as proof that a subsequently edited draft is valid."""

    issues: tuple[ValidationIssue, ...] = ()
    config: RadioConfig | None = None
    derived: DerivedValues | None = None
    effective: dict | None = None
    hardware_verified: bool = False

    @property
    def valid(self) -> bool:
        return not self.issues and self.config is not None


@dataclass(frozen=True)
class InputLimits:
    """Software bounds in canonical units; None means unspecified, not unlimited hardware."""

    minimum: float | None = None
    maximum: float | None = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True
    multiple_of: int | None = None
    choices: tuple = ()


def _issue(exc: Exception, scope: str = "configuration") -> ValidationIssue:
    message = str(exc)
    # Coupled constraints intentionally highlight all participating controls.
    rules = (
        ("sync must contain", ("profile.packet.frame.sync",)),
        (
            "expanded preamble",
            ("profile.packet.frame.preamble", "profile.packet.frame.preamble_repetitions"),
        ),
        ("preamble must contain", ("profile.packet.frame.preamble",)),
        ("acquisition prefix", ("profile.packet.frame.preamble", "profile.packet.frame.sync")),
        (
            "fixed payload requires",
            ("profile.packet.frame.fixed_payload_bytes", "profile.packet.max_payload_bytes"),
        ),
        (
            "variable payload limit",
            ("profile.packet.frame.length_bytes", "profile.packet.max_payload_bytes"),
        ),
        (
            "samples_per_symbol",
            ("profile.waveform.bit_rate_bps", "profile.waveform.sample_rate_hz"),
        ),
        (
            "sample rate and bit rate",
            ("profile.waveform.bit_rate_bps", "profile.waveform.sample_rate_hz"),
        ),
        (
            "sample rate/bandwidth",
            ("profile.waveform.sample_rate_hz", "profile.waveform.bandwidth_hz"),
        ),
        (
            "bandwidth",
            (
                "profile.waveform.bandwidth_hz",
                "profile.waveform.sample_rate_hz",
                "profile.waveform.bit_rate_bps",
            ),
        ),
        (
            "hop dwell",
            (
                "profile.frequency.dwell_seconds",
                "profile.packet.max_payload_bytes",
                "profile.waveform.bit_rate_bps",
            ),
        ),
        ("hop guards", ("profile.frequency.guard_seconds", "profile.frequency.dwell_seconds")),
        ("frequency list contains duplicates", ("profile.frequency.frequencies_hz",)),
        (
            "TDD window",
            (
                "profile.link.tdd_period_seconds",
                "profile.link.tdd_guard_seconds",
                "profile.packet.max_payload_bytes",
            ),
        ),
        ("node_id and peer_id", ("node.node_id", "node.peer_id")),
    )
    for fragment, paths in rules:
        if fragment in message:
            return ValidationIssue(message, paths, scope)
    # Exact dataclass field/type messages have stable identifiers. Ambiguous names
    # and unmapped errors are left at form level; the original message is preserved.
    for prefix, section in (
        ("ModemProfile.", "profile.waveform."),
        ("EndpointConfig.", "node.endpoint."),
        ("StatusConfig.", "node.status."),
        ("AuthenticationConfig.", "node.authentication."),
        ("RecordingConfig.", "node.recording."),
    ):
        if message.startswith(prefix):
            key = section + message[len(prefix) :].split()[0]
            if key in FIELDS:
                return ValidationIssue(message, (key,), scope)
    return ValidationIssue(message, scope=scope)


class RadioDraft:
    """Owned source copies with explicit edits, dirty tracking and offline validation.

    Invalid/incomplete leaf values may be staged for typing. validate() is the gate
    before use. Changing profile files is explicit so an edit never reads arbitrary
    files implicitly. The storage service handles persistence; FormSession handles
    widget-level unit conversion.
    """

    def __init__(self, node: dict, profile: dict, node_path: str | Path):
        self.node_path = Path(node_path).resolve()
        self._node = copy.deepcopy(node)
        self._profile = copy.deepcopy(profile)
        self._original_node = copy.deepcopy(node)
        self._original_profile = copy.deepcopy(profile)
        self._profile_baseline = copy.deepcopy(profile)
        self.source_versions: dict[Path, str] = {}
        self.read_only_source = False

    @staticmethod
    def version(path: Path) -> str:
        """Digest exact source bytes, including comments, for overwrite checks."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def load(cls, path: str | Path) -> RadioDraft:
        path = Path(path).resolve()
        node_version = cls.version(path)
        node = read_yaml(path)
        if type(node.get("schema_version")) is not int or node["schema_version"] != 2:
            raise ValueError("draft editor requires schema_version: 2")
        reference = node.get("profile")
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("profile must be a file path")
        profile_path = (path.parent / reference).resolve()
        profile_version = cls.version(profile_path)
        result = cls(node, read_yaml(profile_path), path)
        result.source_versions = {path: node_version, profile_path: profile_version}
        if any(cls.version(p) != version for p, version in result.source_versions.items()):
            raise ValueError("Source changed while opening; reopen the project.")
        return result

    @property
    def node(self) -> dict:
        return copy.deepcopy(self._node)

    @property
    def profile(self) -> dict:
        return copy.deepcopy(self._profile)

    @property
    def dirty(self) -> bool:
        return self._node != self._original_node or self._profile != self._original_profile

    def reset(self) -> None:
        self._node = copy.deepcopy(self._original_node)
        self._profile = copy.deepcopy(self._original_profile)
        self._profile_baseline = copy.deepcopy(self._original_profile)

    def select_profile(self, reference: str) -> None:
        """Read a replacement preset atomically and discard old profile overrides."""
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("profile must be a file path")
        path = (self.node_path.parent / reference).resolve()
        version = self.version(path)
        profile = read_yaml(path)
        if self.version(path) != version:
            raise ValueError("Profile changed while opening; try again.")
        self._profile = profile
        self._profile_baseline = copy.deepcopy(profile)
        self._node["profile"] = reference
        self._node.pop("overrides", None)
        self.source_versions[path] = version

    def set(self, path: str, value: Any) -> None:
        """Set a source path, e.g. profile.waveform.bit_rate_bps or node.overrides.*."""
        if path == "node.profile":
            raise ValueError("use select_profile to change the referenced profile")
        target, parts = self._target(path)
        # Mutate a copy so an invalid intermediate path cannot partially change a draft.
        edited = copy.deepcopy(target)
        parent = edited
        for part in parts[:-1]:
            parent = parent.setdefault(part, {})
            if not isinstance(parent, dict):
                raise ValueError("source path crosses a non-mapping value")
        parent[parts[-1]] = copy.deepcopy(value)
        target.clear()
        target.update(edited)

    def delete(self, path: str) -> None:
        if path == "node.profile":
            raise ValueError("use select_profile to change the referenced profile")
        target, parts = self._target(path)
        parent = target
        for part in parts[:-1]:
            parent = parent[part]
        del parent[parts[-1]]

    def _target(self, path: str) -> tuple[dict, list[str]]:
        namespace, _, rest = path.partition(".")
        if namespace not in ("node", "profile") or not rest or any(not p for p in rest.split(".")):
            raise ValueError("source path must start with node. or profile. and name a field")
        return (self._node if namespace == "node" else self._profile), rest.split(".")

    def validate(self) -> ValidationReport:
        """Resolve and validate a fresh snapshot with the CLI's authoritative checks."""
        try:
            if (
                type(self._node.get("schema_version")) is not int
                or self._node["schema_version"] != 2
            ):
                raise ValueError("draft editor requires schema_version: 2")
            profile = copy.deepcopy(self._profile)
            if profile != self._profile_baseline:
                profile["qualification"] = "unqualified"
                if isinstance(profile.get("id"), str) and not profile["id"].endswith("-custom"):
                    profile["id"] = profile["id"][:121] + "-custom"
            config = load_v2(copy.deepcopy(self._node), self.node_path, definition=profile)
            validate_config(config)
            processing = resolve_processing(config)
            size = processing.codec.payload_size(
                config.endpoint.max_payload_bytes,
                authenticated=config.authentication.private_key_file is not None,
            )
            sdr = config.sdr
            assert config.emulation is not None
            derived = DerivedValues(
                sdr.bit_rate_bps,
                sdr.symbol_rate_baud,
                sdr.samples_per_symbol,
                processing.make_modem(sdr).sample_count(size) / sdr.sample_rate,
                sdr.bit_rate_bps / 4
                if sdr.modulation in ("msk", "gmsk")
                else sdr.frequency_deviation_hz,
                1 / config.frequency.interval_seconds
                if len(config.frequency.frequencies_hz) > 1
                else None,
                profile_fingerprint(config.emulation),
            )
            return ValidationReport(
                config=config, derived=derived, effective=configuration_snapshot(config)
            )
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return ValidationReport(issues=(_issue(exc),))

    def limits(self, key: str) -> InputLimits:
        """Resolve software bounds; invalid drafts retain static hints until corrected.

        No hardware range is fabricated offline. Discrete rate ratios, airtime and
        cross-field constraints still require validate(), even when a range fits.
        """
        field = FIELDS[key]
        bounds = InputLimits(
            field.minimum, field.maximum, multiple_of=field.multiple_of, choices=field.choices
        )
        report = self.validate()
        if not report.valid:
            return bounds
        assert report.config is not None
        config = report.config
        sdr = config.sdr
        minimum_sps = 4 if sdr.modulation in ("2fsk", "msk", "gmsk") else 2
        if key == "profile.waveform.sample_rate_hz":
            return InputLimits(max(sdr.bandwidth, minimum_sps * sdr.symbol_rate_baud))
        if key == "profile.frequency.center_frequency_hz":
            return InputLimits(0, minimum_inclusive=False)
        if key == "profile.waveform.bit_rate_bps":
            bits = 2 if sdr.modulation == "qpsk" else 1
            maximum = sdr.sample_rate * bits / minimum_sps
            if sdr.modulation in ("msk", "gmsk"):
                maximum = min(maximum, sdr.bandwidth / 2.5)
            elif sdr.modulation == "2fsk":
                assert sdr.frequency_deviation_hz is not None
                maximum = min(maximum, (sdr.bandwidth - 2 * sdr.frequency_deviation_hz) / 2)
            return InputLimits(0, maximum, minimum_inclusive=False)
        if key == "profile.waveform.bandwidth_hz":
            minimum = 0.0
            if sdr.modulation in ("msk", "gmsk"):
                minimum = 2.5 * sdr.bit_rate_bps
            elif sdr.modulation == "2fsk":
                assert sdr.frequency_deviation_hz is not None
                minimum = 2 * sdr.frequency_deviation_hz + 2 * sdr.bit_rate_bps
            return InputLimits(minimum, sdr.sample_rate, minimum_inclusive=minimum > 0)
        if key == "profile.waveform.frequency_deviation_hz" and sdr.modulation == "2fsk":
            bandwidth_limit = (sdr.bandwidth - 2 * sdr.bit_rate_bps) / 2
            sample_limit = sdr.sample_rate / 4
            return InputLimits(
                0, min(bandwidth_limit, sample_limit), False, bandwidth_limit < sample_limit
            )
        if key == "derived.hop_rate":
            maximum = 2.0 if sdr.device.get("type") == "x300" else 1.0
            if len(config.frequency.frequencies_hz) > 1:
                assert report.derived is not None
                lead = (
                    config.link.hardware_timed_tx_lead_seconds
                    if config.link.hardware_timed_io
                    else 0
                )
                idle = resolve_processing(config).tx_idle_samples(sdr) / sdr.sample_rate
                required_dwell = (
                    2 * config.frequency.guard_seconds
                    + report.derived.maximum_airtime_seconds
                    + lead
                    + idle
                    + 0.001
                )
                maximum = min(maximum, 1 / required_dwell)
            return InputLimits(0, maximum, False)
        return bounds


def validate_pair(first: RadioDraft, second: RadioDraft) -> ValidationReport:
    """Validate both snapshots and peer compatibility with the CLI network rules."""
    reports = (first.validate(), second.validate())
    issues = tuple(
        ValidationIssue(issue.message, issue.fields, scope)
        for report, scope in zip(reports, ("first", "second"))
        for issue in report.issues
    )
    if issues:
        return ValidationReport(issues=issues)
    assert reports[0].config is not None and reports[1].config is not None
    try:
        validate_network({"first": reports[0].config, "second": reports[1].config})
    except ValueError as exc:
        return ValidationReport(issues=(_issue(exc, "network"),))
    return reports[0]
