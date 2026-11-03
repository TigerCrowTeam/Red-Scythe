"""Select a local SDR deployment and prepare ordinary validated radio drafts.

Discovery identifies local hardware, not a remote peer. One B205 runs one node;
an X310 can run complementary simple-frame nodes on its two channels. No streams
are opened here and no saved project is overwritten or silently retimed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.forms import FormSession
from packet_radio.configuration.preflight import validate_shared_pair
from packet_radio.configuration.storage import PeerOptions, peer_draft


@dataclass(frozen=True)
class Deployment:
    mode: str
    model: str
    arguments: dict[str, str]


def deployment(device: dict) -> Deployment:
    """Require a discovered model and stable selector; type alone is not a model."""
    product = str(device.get("product", "")).upper().replace("-", "")
    if device.get("type") == "b200" and product in ("B205", "B205MINI", "B205MINII"):
        if not device.get("serial"):
            raise ValueError(
                "The discovered B205 has no serial; select it explicitly in a project."
            )
        return Deployment(
            "remote-b205", str(device["product"]), {"type": "b200", "serial": str(device["serial"])}
        )
    if device.get("type") == "x300" and product == "X310":
        key = "addr" if device.get("addr") else "serial"
        if not device.get(key):
            raise ValueError("The discovered X310 has no address or serial.")
        return Deployment("shared-x310", "X310", {"type": "x300", key: str(device[key])})
    raise ValueError("Automatic setup supports identified B205/B205mini and X310 radios only.")


def available_deployments(devices: list[dict], *, serial: str | None = None) -> list[Deployment]:
    """List supported physical boards once, even when multiple interfaces advertise them."""
    selected = []
    seen = set()
    for device in devices:
        if serial is not None and device.get("serial") != serial:
            continue
        try:
            value = deployment(device)
        except ValueError:
            continue
        identity = (value.mode, device.get("serial") or device.get("addr"))
        if identity not in seen:
            selected.append(value)
            seen.add(identity)
    return selected


def select_deployment(devices: list[dict], *, serial: str | None = None) -> Deployment:
    """Select only a unique supported board, never the first of several radios."""
    selected = available_deployments(devices, serial=serial)
    if not selected:
        raise ValueError(
            "No supported SDR found. Check USB/Ethernet, power, UHD and the selected serial."
        )
    if len(selected) != 1:
        raise ValueError(
            "Multiple SDRs found. Select a device explicitly; automatic setup will not guess."
        )
    return selected[0]


def bind_device(source: RadioDraft, selected: Deployment) -> RadioDraft:
    """Keep the waveform and role, replacing local selectors and selecting channel 0 for USB."""
    node = source.node
    node.setdefault("hardware", {})["device"] = selected.arguments.copy()
    if selected.mode == "remote-b205":
        node["hardware"]["channel"] = 0
    result = RadioDraft(node, source.profile, source.node_path)
    result.source_versions = source.source_versions.copy()
    result.read_only_source = source.read_only_source
    report = result.validate()
    if not report.valid:
        raise ValueError("Settings do not fit the detected SDR: " + report.issues[0].message)
    assert report.config is not None
    if selected.mode == "remote-b205" and len(report.config.frequency.frequencies_hz) > 1:
        raise ValueError(
            "Automatic two-computer setup currently requires a fixed frequency; independent-host hopping needs a separate synchronization setup."
        )
    return result


def new_deployment(selected: Deployment, role: str | None = None) -> RadioDraft:
    """Create a fixed-frequency bidirectional draft; remote peers need distinct A/B roles."""
    if selected.mode == "remote-b205" and role not in ("a", "b"):
        raise ValueError("Choose computer A on one host and computer B on the other.")
    if role is not None and role not in ("a", "b"):
        raise ValueError("Computer role must be a or b.")
    source = FormSession.new().draft
    node = source.node
    node.update(
        node_id=2 if role == "b" else 1,
        peer_id=1 if role == "b" else 2,
        timing_role="follower" if role == "b" else "leader",
        operation="bidirectional",
    )
    # These sockets connect local applications to the radio, never the computers to each other.
    node["endpoint"].update(bind_host="127.0.0.1", destination_host="127.0.0.1")
    node["status"]["http_enabled"] = True
    return bind_device(RadioDraft(node, source.profile, source.node_path), selected)


def shared_peer(source: RadioDraft, destination: str | Path) -> RadioDraft:
    """Derive an ephemeral second simple-frame channel; explicit pairs retain full control."""
    report = source.validate()
    if not report.valid:
        raise ValueError(report.issues[0].message)
    cfg = report.config
    assert cfg is not None
    if cfg.packet_format != "simple-frame-v1" or cfg.sdr.device.get("type") != "x300":
        raise ValueError(
            "Automatic X310 pairing requires simple-frame-v1; use an explicit peer for generic-v5."
        )
    if cfg.sdr.channel not in (0, 1):
        raise ValueError("Automatic X310 pairing requires channel 0 or 1.")
    identifier = cfg.sdr.device.get("addr")
    if not identifier:
        raise ValueError("Automatic X310 pairing requires a discovered or configured address.")
    used = {cfg.endpoint.bind_port, cfg.endpoint.destination_port, cfg.status.http_port}
    ports = []
    for start in (50002, 50003, 8766):
        port = start
        while port in used:
            port = port % 65535 + 1
        used.add(port)
        ports.append(port)
    result = peer_draft(
        source,
        destination,
        PeerOptions("x300", identifier, 1 - cfg.sdr.channel, ports[0], ports[1], ports[2]),
    )
    # Preserve transport arguments, not just the address used by the export helper.
    result.set("node.hardware.device", cfg.sdr.device.copy())
    peer = result.validate()
    if not peer.valid:
        raise ValueError(peer.issues[0].message)
    assert peer.config is not None
    validate_shared_pair([cfg, peer.config])
    return result
