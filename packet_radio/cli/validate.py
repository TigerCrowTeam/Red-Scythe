"""Validate radio profiles before starting the application.

main checks individual or paired configurations and reports waveform rates and
airtime. validate_hardware optionally opens the configured SDR, reads its actual
settings and closes it after inspection.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from packet_radio.config import load_config, validate_network
from packet_radio.hardware.uhd import UHDRadio
from packet_radio.modems.registry import make_modem
from packet_radio.protocol.framing import RadioPacket


def validate_hardware(config) -> str:
    radio = UHDRadio(config.sdr, config.frequency.frequencies_hz)
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
    args = parser.parse_args()
    try:
        loaded = {str(Path(path)): load_config(path) for path in args.configs}
        if len(loaded) > 1:
            validate_network(loaded)
        if args.hardware:
            if len(loaded) != 1:
                raise ValueError("--hardware accepts exactly one node configuration")
            details = validate_hardware(next(iter(loaded.values())))
            print(f"hardware valid: {details}")
        for path, config in loaded.items():
            size = RadioPacket(
                config.node_id, config.peer_id, 0, bytes(config.endpoint.max_payload_bytes)
            ).encoded_size(authenticated=config.authentication.private_key_file is not None)
            airtime_ms = make_modem(config.sdr).sample_count(size) / config.sdr.sample_rate * 1000
            print(f"valid: {path} ({config.timing_role} node {config.node_id})")
            print(
                f"  {config.sdr.modulation}: center={config.center_frequency_hz:g} Hz, "
                f"bit_rate={config.sdr.bit_rate_bps:g} bps, "
                f"symbol_rate={config.sdr.symbol_rate_baud:g} baud, "
                f"samples/symbol={config.sdr.samples_per_symbol}, max_burst={airtime_ms:.3f} ms"
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
