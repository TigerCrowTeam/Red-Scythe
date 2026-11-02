"""Validate radio profiles before starting the application.

main checks individual or paired configurations and reports waveform rates and
airtime. validate_hardware optionally opens the configured SDR, reads its actual
settings and closes it after inspection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from packet_radio.config import configuration_snapshot, load_config, validate_network
from packet_radio.hardware.uhd import UHDRadio
from packet_radio.protocol.registry import resolve_processing


def validate_hardware(config) -> str:
    radio = UHDRadio(
        config.sdr,
        config.frequency.frequencies_hz,
        tx_idle_samples=resolve_processing(config).tx_idle_samples(config.sdr),
    )
    try:
        return str(radio.actual_settings)
    finally:
        radio.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate packet-radio configuration")
    parser.add_argument("configs", nargs="+", help="one config, or every network config")
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="also check one config against its auto-detected/configured SDR",
    )
    parser.add_argument(
        "--resolved", action="store_true", help="print resolved settings as JSON without key paths"
    )
    args = parser.parse_args()
    try:
        if args.resolved and args.hardware:
            raise ValueError("--resolved and --hardware must be used separately")
        loaded = {str(Path(path)): load_config(path) for path in args.configs}
        if len(loaded) > 1:
            validate_network(loaded)
        if args.hardware:
            if len(loaded) != 1:
                raise ValueError("--hardware accepts exactly one node configuration")
            details = validate_hardware(next(iter(loaded.values())))
            print(f"hardware valid: {details}")
        if args.resolved:
            from packet_radio.profiles import profile_fingerprint

            print(
                json.dumps(
                    {
                        path: {
                            "configuration": configuration_snapshot(config),
                            "processing": resolve_processing(config).describe(),
                            "profile_sha256": profile_fingerprint(config.emulation)
                            if config.emulation
                            else None,
                        }
                        for path, config in loaded.items()
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        for path, config in loaded.items():
            processing = resolve_processing(config)
            size = processing.codec.payload_size(
                config.endpoint.max_payload_bytes,
                authenticated=config.authentication.private_key_file is not None,
            )
            airtime_ms = (
                processing.make_modem(config.sdr).sample_count(size) / config.sdr.sample_rate * 1000
            )
            print(f"valid: {path} ({config.timing_role} node {config.node_id})")
            frequency_text = (
                f"center={config.center_frequency_hz:g} Hz"
                if len(config.frequency.frequencies_hz) == 1
                else f"frequencies={list(config.frequency.frequencies_hz)} Hz, "
                f"hop_rate={1 / config.frequency.interval_seconds:g}/s"
            )
            print(
                f"  {config.sdr.modulation}: {frequency_text}, "
                f"bit_rate={config.sdr.bit_rate_bps:g} bps, "
                f"symbol_rate={config.sdr.symbol_rate_baud:g} baud, "
                f"samples/symbol={config.sdr.samples_per_symbol}, "
                f"max_payload={config.endpoint.max_payload_bytes} bytes, max_burst={airtime_ms:.3f} ms"
            )
            if config.sdr.frequency_deviation_hz is not None:
                print(f"  frequency deviation: +/-{config.sdr.frequency_deviation_hz:g} Hz")
            elif config.sdr.modulation in ("msk", "gmsk"):
                print(
                    f"  derived frequency deviation: +/-{config.sdr.bit_rate_bps / 4:g} Hz (h=0.5)"
                )
            if config.sdr.modulation == "gmsk":
                print(
                    f"  Gaussian BT={config.sdr.gaussian_bt}, span={config.sdr.gaussian_span_symbols} symbols"
                )
        if len(loaded) > 1:
            print(f"network valid: {len(loaded)} nodes")
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"packet-radio-validate: {exc}\n")


if __name__ == "__main__":
    main()
