"""Discover UHD devices and probe settings without creating or starting streams.

Invoked by the editor in a disposable subprocess. Opening/configuring an SDR can
change its tuning and gains; never probe a device used by another running radio.
No device clock is reset and no transmit or receive stream is created.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

from packet_radio.configuration.preflight import RESULT_PREFIX, resolve, validate_shared_pair
from packet_radio.hardware.uhd import load_uhd, make_uhd_device, validate_device_capabilities


def discover(api, arguments: dict) -> list[dict]:
    return [dict(address.to_dict()) for address in api.find(api.types.DeviceAddr(arguments))]


def limits(value) -> dict:
    return {
        "minimum": float(value.start()),
        "maximum": float(value.stop()),
        "step": float(value.step()),
    }


def describe(device) -> dict:
    result: dict[str, Any] = {"motherboards": int(device.get_num_mboards()), "channels": {}}
    for direction in ("rx", "tx"):
        channels = []
        for channel in range(getattr(device, f"get_{direction}_num_channels")()):
            channels.append(
                {
                    "channel": channel,
                    "identity": dict(getattr(device, f"get_usrp_{direction}_info")(channel)),
                    "antennas": list(getattr(device, f"get_{direction}_antennas")(channel)),
                    "ranges": {
                        label: limits(getattr(device, f"get_{direction}_{getter}")(channel))
                        for label, getter in (
                            ("rate", "rates"),
                            ("frequency", "freq_range"),
                            ("bandwidth", "bandwidth_range"),
                            ("gain", "gain_range"),
                        )
                    },
                }
            )
        result["channels"][direction] = channels
    return result


def probe(api, configs) -> dict:
    if len(configs) == 2:
        validate_shared_pair(configs)
    device = make_uhd_device(api, configs[0].sdr.device)
    try:
        capabilities = describe(device)
        if len(configs) == 2 and capabilities["motherboards"] != 1:
            raise ValueError("Paired X310 operation requires exactly one motherboard.")
        for config in configs:
            validate_device_capabilities(device, config.sdr, config.frequency.frequencies_hz)
        nodes = []
        warnings = []
        for config in configs:
            sdr = config.sdr
            channel = sdr.channel
            requested: dict[str, Any] = {}
            actual: dict[str, Any] = {}
            for direction, gain, antenna in (
                ("rx", sdr.rx_gain, sdr.rx_antenna),
                ("tx", sdr.tx_gain, sdr.tx_antenna),
            ):
                for name, value in (
                    ("rate", sdr.sample_rate),
                    ("bandwidth", sdr.bandwidth),
                    ("gain", gain),
                ):
                    requested[f"{direction}_{name}"] = value
                    target = value
                    if name == "bandwidth":
                        band = getattr(device, f"get_{direction}_bandwidth_range")(channel)
                        if band.start() == band.stop():
                            target = float(band.start())
                    getattr(device, f"set_{direction}_{name}")(target, channel)
                requested[f"{direction}_antenna"] = antenna
                if antenna is not None:
                    getattr(device, f"set_{direction}_antenna")(antenna, channel)
            # Probe each hop, not merely the first, without creating any streams.
            tuned = []
            for frequency in config.frequency.frequencies_hz:
                entry = {"requested_hz": frequency}
                for direction in ("rx", "tx"):
                    getattr(device, f"set_{direction}_freq")(
                        api.types.TuneRequest(frequency), channel
                    )
                    value = float(getattr(device, f"get_{direction}_freq")(channel))
                    entry[direction + "_hz"] = value
                    if not math.isclose(value, frequency, rel_tol=0, abs_tol=1):
                        raise ValueError(
                            f"UHD {direction} frequency coerced: {frequency} -> {value}"
                        )
                tuned.append(entry)
            nodes.append(
                {
                    "node_id": config.node_id,
                    "channel": channel,
                    "requested": requested,
                    "actual": actual,
                    "frequencies": tuned,
                }
            )
        # Read all channels after all setters: shared clocks can coerce another channel.
        for config, node in zip(configs, nodes):
            for direction in ("rx", "tx"):
                for name in ("rate", "bandwidth", "gain", "antenna"):
                    key = f"{direction}_{name}"
                    value = getattr(device, f"get_{direction}_{name}")(config.sdr.channel)
                    value = str(value) if name == "antenna" else float(value)
                    node["actual"][key] = value
                    wanted = node["requested"][key]
                    if name == "rate" and not math.isclose(float(value), wanted, rel_tol=1e-6):
                        raise ValueError(
                            f"UHD {direction} sample rate coerced: {wanted} -> {value}"
                        )
                    if name == "antenna" and wanted is not None and wanted != value:
                        raise ValueError(f"UHD {direction} antenna differs from requested {wanted}")
                    if name in ("gain", "bandwidth") and not math.isclose(
                        float(value), wanted, rel_tol=1e-6, abs_tol=0.01
                    ):
                        warnings.append(
                            f"Node {config.node_id} {key}: requested {wanted:g}, actual {value:g}"
                        )
        return {"ok": True, "capabilities": capabilities, "nodes": nodes, "warnings": warnings}
    finally:
        del device


def main():
    try:
        request = json.load(sys.stdin)
        api = load_uhd()
        if request["action"] == "discover":
            result = {"ok": True, "devices": discover(api, request.get("arguments", {}))}
        elif request["action"] == "probe":
            configs = [resolve(value) for value in request["drafts"]]
            if len(configs) not in (1, 2):
                raise ValueError("Check one node or a shared X310 pair.")
            result = probe(api, configs)
        else:
            raise ValueError("Unknown hardware action")
    except Exception as exc:
        result = {
            "ok": False,
            "error": f"{exc}\nCheck UHD Python bindings, device power/address and connection. "
            "If busy, stop the application that owns the SDR before retrying.",
        }
    print(RESULT_PREFIX + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
