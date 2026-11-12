"""Export portable A/B simple-frame projects with one shared radio profile.

Each node retains separate hardware and endpoint settings. Export requires a new
folder, preserves source files, and copies no keys, recordings or replay state.
The resulting ordinary YAML projects run independently without SSH or a LAN.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from packet_radio.config import validate_network
from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.storage import (
    PeerOptions,
    SaveConflict,
    check_sources,
    peer_draft,
    save_project,
)


def export_two_host(source: RadioDraft, destination: str | Path, peer_serial: str) -> Path:
    """Write complementary B205 nodes and one immutable shared waveform definition."""
    check_sources(source)
    report = source.validate()
    if not report.valid:
        raise ValueError(report.issues[0].message)
    cfg = report.config
    assert cfg is not None
    if cfg.sdr.device.get("type") != "b200" or not cfg.sdr.device.get("serial"):
        raise ValueError(
            "Set the local B205 serial in Setup before exporting a two-computer project."
        )
    if cfg.sdr.channel != 0:
        raise ValueError("A B205 two-computer project must use channel 0.")
    if cfg.packet_format != "simple-frame-v1" or len(cfg.frequency.frequencies_hz) != 1:
        raise ValueError(
            "Two-computer project export currently requires fixed-frequency simple frames."
        )
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise SaveConflict(
            "Project export requires a new folder; existing projects are not overwritten."
        )
    target = target.resolve()
    role = "a" if cfg.timing_role == "leader" else "b"
    other = "b" if role == "a" else "a"
    peer = peer_draft(
        source,
        target / f"computer-{other}.yaml",
        PeerOptions(
            "b200",
            peer_serial.strip(),
            0,
            cfg.endpoint.bind_port,
            cfg.endpoint.destination_port,
            cfg.status.http_port,
        ),
        separate_host=True,
    )
    # This portable bundle contains no host-specific authentication/recording paths.
    node = source.node
    node.pop("overrides", None)
    assert cfg.emulation is not None
    local = RadioDraft(node, cfg.emulation, target / f"computer-{role}.yaml")
    local.source_versions = source.source_versions.copy()
    for draft in (local, peer):
        draft.set("node.authentication", {})
        draft.set("node.recording", {})
        draft.set("node.endpoint.bind_host", "127.0.0.1")
        draft.set("node.endpoint.destination_host", "127.0.0.1")
        draft.set("node.status.http_host", "127.0.0.1")
        draft.set("node.status.http_enabled", True)
    configs = {}
    for name, draft in ((role, local), (other, peer)):
        resolved = draft.validate()
        if not resolved.valid or resolved.config is None:
            raise ValueError("The exported node settings are invalid.")
        configs[name] = resolved.config
    validate_network(configs)
    # Exclusively claim the folder; a failed export removes only this newly created tree.
    target.mkdir(parents=True, exist_ok=False)
    try:
        save_project(local, local.node_path)
        save_project(peer, peer.node_path)
    except BaseException:
        shutil.rmtree(target)
        raise
    return target
