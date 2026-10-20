"""Save validated projects atomically, preserve path meaning and export explicit peers.

Profiles are immutable content-named YAML files; the node is the single commit
point. Optimistic source digests and a cooperative writer lock detect conflicting
edits. No key material, replay-state files, recordings or hardware are accessed.
"""

from __future__ import annotations

import copy
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from packet_radio.config import load_config, validate_network
from packet_radio.configuration.draft import RadioDraft
from packet_radio.profiles import (
    HARDWARE_FIELDS,
    HOST_LINK,
    MODEM_FIELDS,
    PORTABLE_LINK,
    normalize_definition,
    profile_fingerprint,
)


class SaveConflict(ValueError):
    """The current source or destination cannot safely be overwritten."""


def check_sources(draft: RadioDraft) -> None:
    for path, expected in draft.source_versions.items():
        try:
            unchanged = RadioDraft.version(path) == expected
        except OSError:
            unchanged = False
        if not unchanged:
            raise SaveConflict(f"Source changed or disappeared: {path}. Reopen it before saving.")


def _local(value, base: Path):
    return str((base / value).resolve()) if value is not None else None


def rebase_paths(node: dict, old_base: Path, new_base: Path) -> dict:
    """Retain the same referents; never relocate key, recording or replay-state data."""
    node = copy.deepcopy(node)

    def relative(value):
        return os.path.relpath(_local(value, old_base), new_base) if value is not None else None

    auth = node.get("authentication", {})
    for key in ("private_key_file", "replay_state_file"):
        if key in auth:
            auth[key] = relative(auth[key])
    if "public_key_files" in auth:
        auth["public_key_files"] = {
            key: [relative(p) for p in ([paths] if isinstance(paths, str) else paths)]
            for key, paths in auth["public_key_files"].items()
        }
    recording = node.get("recording", {})
    if "directory" in recording:
        recording["directory"] = relative(recording["directory"])
    return node


def _bytes(value: dict) -> bytes:
    return yaml.safe_dump(value, sort_keys=True, allow_unicode=True).encode("utf-8")


def _sync_directory(directory: Path):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(path: Path, data: bytes, *, replace=False, precommit=None):
    """Stage/fsync in the destination filesystem, then publish without partial writes."""
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if precommit is not None:
            precommit()
        if replace:
            os.replace(staged, path)
        else:
            # Atomic create-if-absent, unlike a check followed by os.replace.
            os.link(staged, path)
        _sync_directory(path.parent)
    finally:
        staged.unlink(missing_ok=True)


def save_project(draft: RadioDraft, destination: str | Path) -> RadioDraft:
    """Save or Save As, returning a clean reopened draft only after successful commit.

    Existing targets may be replaced only when they are this draft's tracked node.
    Reference templates and legacy sources always require Save As to a new path.
    """
    from packet_radio.configuration.forms import template_root

    target = Path(destination).expanduser().absolute()
    if target.is_symlink():
        raise SaveConflict("Choose a regular node file, not a symbolic link.")
    target = target.resolve()
    if target.is_relative_to(template_root().resolve()):
        raise SaveConflict("Reference templates are read-only. Use Save As outside configs.")
    if draft.read_only_source and target == draft.node_path:
        raise SaveConflict("Legacy source is read-only. Choose a new schema-2 node path.")
    check_sources(draft)
    expected = draft.source_versions.get(target) if target == draft.node_path else None
    if target.exists() and expected is None:
        raise SaveConflict(
            f"Destination already exists: {target}. Open it explicitly or choose a new name."
        )
    report = draft.validate()
    if not report.valid:
        raise ValueError(report.issues[0].message)
    assert report.config is not None and report.config.emulation is not None
    definition = report.config.emulation
    node = rebase_paths(draft.node, draft.node_path.parent, target.parent)
    node.pop("overrides", None)
    profile_path = target.parent / "profiles" / f"{profile_fingerprint(definition)}.yaml"
    node["profile"] = os.path.relpath(profile_path, target.parent)
    proposed = RadioDraft(node, definition, target)
    resolved = proposed.validate()
    if not resolved.valid or resolved.config != report.config:
        raise ValueError("Save would change effective settings or paths; no files were committed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".{target.name}.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise SaveConflict(
            f"Another save owns {lock}; if its process stopped, remove the stale lock explicitly."
        ) from exc
    try:
        os.close(lock_fd)
        check_sources(draft)
        if target.exists() and expected is None:
            raise SaveConflict(f"Destination appeared during save: {target}")
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        if profile_path.parent.is_symlink():
            raise SaveConflict("The project's profiles directory must not be a symbolic link.")
        data = _bytes(definition)
        try:
            _publish(profile_path, data)
        except FileExistsError:
            if profile_path.is_symlink() or profile_path.read_bytes() != data:
                raise SaveConflict(
                    f"Profile destination contains different data: {profile_path}"
                ) from None
        # Reject an external edit made while staging the profile, too.
        check_sources(draft)
        _publish(
            target,
            _bytes(node),
            replace=expected is not None,
            precommit=lambda: check_sources(draft),
        )
    finally:
        lock.unlink(missing_ok=True)
    return RadioDraft.load(target)


@dataclass(frozen=True)
class PeerOptions:
    """Explicit peer-local choices; authentication identities are never guessed/copied."""

    device_type: str
    identifier: str
    channel: int
    bind_port: int
    destination_port: int
    http_port: int
    private_key_file: str | None = None
    replay_state_file: str | None = None
    recording_directory: str | None = None
    bind_host: str = "127.0.0.1"
    destination_host: str = "127.0.0.1"


def peer_draft(source: RadioDraft, destination: str | Path, options: PeerOptions) -> RadioDraft:
    """Prepare a complementary peer and verify its pair before writing anything."""
    check_sources(source)
    report = source.validate()
    if not report.valid:
        raise ValueError(report.issues[0].message)
    config = report.config
    assert config is not None and config.emulation is not None
    if options.device_type not in ("b200", "x300") or not options.identifier.strip():
        raise ValueError(
            "Specify the peer B205 serial or X310 address; automatic selection is not exported."
        )
    target = Path(destination).expanduser().resolve()
    node = rebase_paths(source.node, source.node_path.parent, target.parent)
    node.pop("overrides", None)
    node["profile"] = "peer-profile.yaml"
    node["node_id"], node["peer_id"] = config.peer_id, config.node_id
    node["timing_role"] = "follower" if config.timing_role == "leader" else "leader"
    node["operation"] = {
        "transmit-only": "receive-only",
        "receive-only": "transmit-only",
        "bidirectional": "bidirectional",
    }[config.operation]
    node["hardware"]["device"] = {
        "type": options.device_type,
        "serial" if options.device_type == "b200" else "addr": options.identifier,
    }
    node["hardware"]["channel"] = options.channel
    original = config.sdr.device
    identity_key = "serial" if options.device_type == "b200" else "addr"
    if (
        original.get("type") == options.device_type
        and original.get(identity_key) == options.identifier
    ):
        if options.device_type != "x300" or config.sdr.channel == options.channel:
            raise ValueError(
                "Peer must use a different SDR, or a distinct channel on the shared X310."
            )
    ports = (options.bind_port, options.destination_port, options.http_port)
    local_ports = {
        config.endpoint.bind_port,
        config.endpoint.destination_port,
        config.status.http_port,
    }
    if len(set(ports)) != 3 or set(ports) & local_ports:
        raise ValueError(
            "Peer UDP/status ports must be distinct from each other and the local node."
        )
    node.setdefault("endpoint", {}).update(
        bind_host=options.bind_host,
        destination_host=options.destination_host,
        bind_port=options.bind_port,
        destination_port=options.destination_port,
    )
    node.setdefault("status", {})["http_port"] = options.http_port
    auth = node.setdefault("authentication", {})
    if config.authentication.private_key_file is not None:
        if not options.private_key_file or not options.replay_state_file:
            raise ValueError(
                "Authenticated export requires explicit peer private-key and replay-state paths."
            )
        private = _local(options.private_key_file, target.parent)
        replay = _local(options.replay_state_file, target.parent)
        if (
            private == config.authentication.private_key_file
            or replay == config.authentication.replay_state_file
        ):
            raise ValueError(
                "Peer private key and replay state must not reuse the local node's paths."
            )
        auth["private_key_file"] = options.private_key_file
        auth["replay_state_file"] = options.replay_state_file
    elif options.private_key_file or options.replay_state_file:
        raise ValueError(
            "Source is unauthenticated; configure authentication for both peers first."
        )
    else:
        auth["private_key_file"] = None
        auth["replay_state_file"] = None
        auth["public_key_files"] = {}
    node.setdefault("recording", {})["directory"] = options.recording_directory
    if (
        options.recording_directory
        and _local(options.recording_directory, target.parent) == config.recording.directory
    ):
        raise ValueError("Choose a separate peer recording directory.")
    result = RadioDraft(node, config.emulation, target)
    result.source_versions = source.source_versions.copy()
    # An export must never gain permission to overwrite its source node.
    result.read_only_source = target in source.source_versions
    peer = result.validate()
    if not peer.valid:
        raise ValueError(peer.issues[0].message)
    assert peer.config is not None
    validate_network({"local": config, "peer": peer.config})
    return result


def export_peer(source: RadioDraft, destination: str | Path, options: PeerOptions) -> RadioDraft:
    if Path(destination).expanduser().exists():
        raise SaveConflict("Peer export requires a new destination file.")
    return save_project(peer_draft(source, destination, options), destination)


def import_legacy(path: str | Path) -> RadioDraft:
    """Explicit schema-1 → schema-2 working copy. The original is always read-only.

    Schema-1 relative data paths are relative to the process working directory;
    resolve them there before placing the new node elsewhere.
    """
    path = Path(path).expanduser().resolve()
    version = RadioDraft.version(path)
    config = load_config(path)
    if (
        config.schema_version != 1
        or config.packet_format != "generic-v5"
        or len(config.frequency.frequencies_hz) != 1
    ):
        raise ValueError(
            "Only validated fixed-frequency schema-1 generic-v5 nodes can be converted; source unchanged."
        )
    sdr = asdict(config.sdr)
    definition = normalize_definition(
        {
            "profile_version": 1,
            "id": "imported-schema-1-custom",
            "revision": 1,
            "reference": "Explicit schema-1 import",
            "qualification": "unqualified",
            "limitations": ["Imported configuration; no hardware qualification is implied."],
            "waveform": {
                **{key: getattr(config.sdr, key) for key in MODEM_FIELDS},
                "sample_rate_hz": config.sdr.sample_rate,
                "bandwidth_hz": config.sdr.bandwidth,
            },
            "frequency": {"mode": "fixed", "center_frequency_hz": config.center_frequency_hz},
            "packet": {
                "format": "generic-v5",
                "max_payload_bytes": config.endpoint.max_payload_bytes,
            },
            "protection": {
                "scheme": "extended-hamming-8-4",
                "interleaver_depth": config.sdr.fec_interleaver_depth,
                "rs_parity_bytes": config.sdr.rs_parity_bytes,
                "bit_repetitions": config.sdr.bit_repetitions,
            },
            "link": {
                "mode": "reliable",
                **{key: getattr(config.link, key) for key in PORTABLE_LINK},
            },
        }
    )
    node = {
        "schema_version": 2,
        "profile": "imported-profile.yaml",
        "node_id": config.node_id,
        "peer_id": config.peer_id,
        "timing_role": config.timing_role,
        "log_level": config.log_level,
        "hardware": {key: sdr[key] for key in HARDWARE_FIELDS},
        "runtime": {
            **{key: getattr(config.link, key) for key in HOST_LINK},
            "decoder_worker_enabled": config.sdr.decoder_worker_enabled,
            "decoder_output_batches": config.sdr.decoder_output_batches,
        },
        "endpoint": {
            key: value
            for key, value in asdict(config.endpoint).items()
            if key != "max_payload_bytes"
        },
        "authentication": asdict(config.authentication),
        "recording": asdict(config.recording),
        "status": asdict(config.status),
    }
    node = rebase_paths(node, Path.cwd(), path.parent)
    result = RadioDraft(node, definition, path)
    result.source_versions = {path: version}
    result.read_only_source = True
    check_sources(result)
    report = result.validate()
    if not report.valid:
        raise ValueError(f"Unsupported conversion: {report.issues[0].message}; source unchanged.")
    return result
