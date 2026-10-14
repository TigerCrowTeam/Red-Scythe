"""Verify an installed release, bundled resources and signed simulated packet delivery.

Run with the installed environment's Python outside the source checkout. This
checks console scripts and every paired profile without loading UHD or sending RF.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import replace
from importlib.metadata import distribution
from pathlib import Path

import packet_radio
from packet_radio.cli.conformance import validate_fixture
from packet_radio.cli.key_cli import generate_key_pair
from packet_radio.config import load_config, validate_network
from packet_radio.simulation import SimulatedNetwork


def main() -> None:
    package = distribution("packet-radio")
    assert package.version == packet_radio.__version__ == "1.0.0"
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
    assert set(validate_fixture()) == {"data", "ack", "sync"}
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
    print("Installed release checks passed; no SDR access performed.")


if __name__ == "__main__":
    main()
