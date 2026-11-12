"""Start the radio from a YAML profile and command-line overrides.

main loads the configuration, enforces format-specific authentication rules,
applies status options, configures logging and calls app.run. Startup and runtime
errors are reported as command-line failures.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import replace
from typing import Any

from packet_radio.app import run
from packet_radio.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="General-purpose SDR packet radio")
    parser.add_argument("--config", required=True, help="path to YAML configuration")
    parser.add_argument("--peer-config", help="second channel profile on the same X310")
    parser.add_argument(
        "--auto-device",
        action="store_true",
        help="discover a local B205 or X310 and choose single-node or shared-pair operation (schema 2)",
    )
    parser.add_argument(
        "--sdr-serial", help="choose this serial when automatic discovery finds several radios"
    )
    parser.add_argument(
        "--status",
        choices=("off", "text", "json"),
        help="override periodic status output format",
    )
    parser.add_argument("--status-interval", type=float, help="override status interval in seconds")
    parser.add_argument("--status-http-port", type=int, help="override local status HTTP port")
    parser.add_argument(
        "--no-status-http", action="store_true", help="disable the status HTTP server"
    )
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="allow insecure operation without authentication (isolated bench use only)",
    )
    parser.add_argument(
        "--timing-diagnostics",
        action="store_true",
        help="include bounded timing summaries in status snapshots",
    )
    parser.add_argument("--ready-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.sdr_serial and not args.auto_device:
        parser.error("--sdr-serial requires --auto-device")
    timing_options: dict[str, Any] = {"timing_diagnostics": True} if args.timing_diagnostics else {}
    if args.run_id is not None:
        timing_options["run_id"] = args.run_id
    if args.ready_fd is not None:

        def ready():
            os.write(args.ready_fd, b"R")
            os.close(args.ready_fd)

        timing_options["on_ready"] = ready
    try:
        config = load_config(args.config)
        peer = None
        if args.auto_device:
            from packet_radio.configuration.deployment import (
                bind_device,
                select_deployment,
                shared_peer,
            )
            from packet_radio.configuration.draft import RadioDraft
            from packet_radio.configuration.preflight import HardwareJob

            discovered = asyncio.run(HardwareJob().run({"action": "discover"}))
            selected = select_deployment(discovered["devices"], serial=args.sdr_serial)
            local = bind_device(RadioDraft.load(args.config), selected)
            resolved = local.validate().config
            assert resolved is not None
            config = resolved
            if selected.mode == "shared-x310":
                other = (
                    bind_device(RadioDraft.load(args.peer_config), selected)
                    if args.peer_config
                    else shared_peer(local, local.node_path.with_name("generated-peer.yaml"))
                )
                peer = other.validate().config
                assert peer is not None
            elif args.peer_config:
                raise ValueError(
                    "B205 two-computer mode starts only the local node; run the remote profile on the other computer."
                )
            print(
                f"Detected {selected.model}: {selected.mode}; device={selected.arguments}",
                flush=True,
            )
        if (
            config.packet_format == "generic-v5"
            and config.authentication.private_key_file is None
            and not args.allow_unauthenticated
        ):
            raise ValueError(
                "authentication.private_key_file is required; use --allow-unauthenticated only on an isolated bench"
            )
        status = config.status
        if args.status is not None:
            status = replace(status, output=args.status)
        if args.status_interval is not None:
            if args.status_interval <= 0:
                raise ValueError("--status-interval must be positive")
            status = replace(status, interval_seconds=args.status_interval)
        if args.status_http_port is not None:
            if not 1 <= args.status_http_port <= 65535:
                raise ValueError("--status-http-port must be between 1 and 65535")
            status = replace(status, http_port=args.status_http_port)
        if args.no_status_http:
            status = replace(status, http_enabled=False)
        config = replace(config, status=status)
        logging.basicConfig(
            level=getattr(logging, config.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        if args.peer_config is None and peer is None:
            run(config, **timing_options)
        else:
            from packet_radio.hardware.shared import run_pair

            peer = peer or load_config(args.peer_config)
            if (
                peer.packet_format == "generic-v5"
                and peer.authentication.private_key_file is None
                and not args.allow_unauthenticated
            ):
                raise ValueError("peer authentication requires keys or --allow-unauthenticated")
            peer_status = replace(
                peer.status,
                output=peer.status.output if args.status is None else args.status,
                interval_seconds=(
                    peer.status.interval_seconds
                    if args.status_interval is None
                    else args.status_interval
                ),
                http_enabled=False if args.no_status_http else peer.status.http_enabled,
            )
            run_pair([config, replace(peer, status=peer_status)], **timing_options)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"packet-radio: {exc}\n")


if __name__ == "__main__":
    main()
