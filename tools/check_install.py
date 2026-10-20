"""Verify an installed release, bundled resources and signed simulated packet delivery.

Run with the installed environment's Python outside the source checkout. This
checks console scripts and every paired profile without loading UHD or sending RF.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from importlib.metadata import distribution
from pathlib import Path

import packet_radio
from packet_radio.cli.conformance import validate_fixture
from packet_radio.cli.key_cli import generate_key_pair
from packet_radio.config import (
    AuthenticationConfig,
    configuration_snapshot,
    load_config,
    validate_network,
)
from packet_radio.protocol.framing import RadioPacket
from packet_radio.protocol.registry import resolve_processing
from packet_radio.recording import CaptureRecorder, replay_frames, replay_iq
from packet_radio.simulation import SimulatedNetwork


def check_capture(config, directory: Path, *, hopping: bool = False) -> None:
    """Replay self-contained captures through installed APIs and both CLI sources."""
    config = replace(config, authentication=AuthenticationConfig())
    processing = resolve_processing(config)
    payload = b"installed capture\x00\xff"
    packet = RadioPacket(1, 2, 17, payload) if config.packet_format == "generic-v5" else payload
    wire = processing.codec.encode(packet)
    sdr = config.sdr
    capture = CaptureRecorder(
        directory,
        resolved_configuration=configuration_snapshot(config),
        sample_rate=sdr.sample_rate,
        center_frequency_hz=config.frequency.frequencies_hz[0],
        samples_per_symbol=sdr.samples_per_symbol,
        modulation=sdr.modulation,
        frequency_deviation_hz=sdr.frequency_deviation_hz,
        gaussian_bt=sdr.gaussian_bt,
        gaussian_span_symbols=sdr.gaussian_span_symbols,
        rrc_rolloff=sdr.rrc_rolloff,
        rrc_span_symbols=sdr.rrc_span_symbols,
        interleaver_depth=sdr.fec_interleaver_depth,
        bit_repetitions=sdr.bit_repetitions,
        rs_parity_bytes=sdr.rs_parity_bytes,
        authenticated=False,
        record_iq=not hopping,
    )
    try:
        capture.record_frame(wire)
        if not hopping:
            capture.record_iq(processing.make_modem(sdr).modulate(wire))
    finally:
        capture.close()
    assert list(replay_frames(directory)) == [wire]
    if not hopping:
        assert list(replay_iq(directory, 257)) == [wire]
    for source in ("frames",) if hopping else ("frames", "iq"):
        result = subprocess.run(
            [
                str(Path(sys.executable).parent / "packet-radio-replay"),
                str(directory),
                "--source",
                source,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        records = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(records) == 1 and records[0]["valid"], records
        assert base64.b64decode(records[0]["payload_base64"]) == payload
    print(f"{config.packet_format}: installed capture/replay passed ({directory.name})")


def main() -> None:
    package = distribution("packet-radio")
    assert package.version == packet_radio.__version__ == "1.1.0"
    assert Path(packet_radio.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), (
        "check must import the installed wheel, not the source checkout"
    )
    root = Path(sys.prefix) / "share/packet-radio"
    for name in ("README.md", "MODULATION_RESULTS.md", "docs/PROGRAM_GUIDE.md"):
        assert (root / name).is_file(), name
    for entry in package.entry_points:
        if entry.group == "console_scripts":
            subprocess.run(
                [str(Path(sys.executable).parent / entry.name), "--help"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
    subprocess.run([sys.executable, str(root / "tools/check_ui_install.py")], check=True)
    assert set(validate_fixture()) == {"data", "ack", "sync"}
    subprocess.run([sys.executable, str(root / "tools/fidelity_check.py")], check=True)
    with tempfile.TemporaryDirectory(prefix="packet-radio-install-") as temporary:
        keys = {node: generate_key_pair(node, temporary, "release") for node in (1, 2)}
        for modulation in ("bpsk", "2fsk", "msk", "gmsk", "qpsk"):
            for kind in ("examples", "bench"):
                configs = {
                    peer: load_config(root / f"configs/{kind}/peer-{peer}-{modulation}.yaml")
                    for peer in ("a", "b")
                }
                validate_network(configs)
                if kind == "bench":
                    continue
                configs = {
                    peer: replace(
                        config,
                        status=replace(config.status, output="off", http_enabled=False),
                        authentication=replace(
                            config.authentication,
                            private_key_file=str(keys[config.node_id][0]),
                            public_key_files={
                                node: (str(paths[1]),) for node, paths in keys.items()
                            },
                            replay_state_file=str(Path(temporary) / f"{modulation}-{peer}.json"),
                        ),
                    )
                    for peer, config in configs.items()
                }
                network = SimulatedNetwork(configs)
                try:
                    network.step(4)
                    forward, reverse = bytes(range(256)), bytes(reversed(range(256)))
                    network.inject("a", forward)
                    network.inject("b", reverse)
                    network.step(250)
                    assert network.received("b") == [forward], modulation
                    assert network.received("a") == [reverse], modulation
                finally:
                    network.close()
                assert all(
                    node.app.statistics.snapshot()["shutdown_errors"] == 0
                    for node in network.nodes.values()
                )
            print(f"{modulation}: installed profiles and signed binary delivery passed")
        for kind in ("generic", "simple", "hopping", "simple-100k", "simple-500k"):
            subprocess.run(
                [
                    str(Path(sys.executable).parent / "packet-radio-validate"),
                    str(root / f"configs/v1_1/peer-a-{kind}.yaml"),
                    str(root / f"configs/v1_1/peer-b-{kind}.yaml"),
                    "--resolved",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            configs = {
                peer: load_config(root / f"configs/v1_1/peer-{peer}-{kind}.yaml")
                for peer in ("a", "b")
            }
            validate_network(configs)
            configs = {
                peer: replace(
                    config,
                    sdr=replace(config.sdr, decoder_worker_enabled=False),
                    link=replace(config.link, hardware_timed_io=False),
                    authentication=(
                        AuthenticationConfig()
                        if kind != "generic"
                        else replace(
                            config.authentication,
                            private_key_file=str(keys[config.node_id][0]),
                            public_key_files={
                                node: (str(paths[1]),) for node, paths in keys.items()
                            },
                            replay_state_file=None,
                        )
                    ),
                )
                for peer, config in configs.items()
            }
            check_capture(configs["a"], Path(temporary) / kind, hopping=kind == "hopping")
            network = SimulatedNetwork(configs)
            try:
                payload = bytes(range(256)) * 4
                network.step(4)
                network.inject("a", payload)
                network.step(250)
                assert network.received("b") == [payload], kind
                if kind != "generic":
                    assert network.nodes["b"].app.statistics.snapshot()["samples_transmitted"] == 0
            finally:
                network.close()
            print(f"schema 2 {kind}: installed reusable profile and payload delivery passed")
    print("Installed release checks passed; no SDR access performed.")


if __name__ == "__main__":
    main()
