"""Calculate a hop's payload capacity using encoded samples and a timing reserve.

The reserve describes the measured host tuning and delivery path, not an absolute
hardware speed. Payload ceilings remain bounded by the frame format. No hardware
is opened and no data is fragmented or truncated by these calculations.
"""

from __future__ import annotations

import math

from packet_radio.protocol.registry import resolve_processing

MAX_HOP_RATE_HZ = 29.0
DSP_TRANSITION_SECONDS = 0.012
MIN_AUTO_GUARD_SECONDS = 0.001
HOST_MARGIN_SECONDS = 0.006
PREPARATION_SECONDS = 0.003


def automatic(config) -> bool:
    return (
        bool(config.emulation) and config.emulation["packet"].get("payload_sizing") == "automatic"
    )


def transition_allowance(config) -> float:
    """Allow both peers to finish tuning before their settling guard begins."""
    return DSP_TRANSITION_SECONDS if config.sdr.device.get("type") == "x300" else 0.04


def timing_reserve(config) -> float:
    """Reserve retuning, both guards, TX lead/idle/drain, preparation and end margin."""
    processing = resolve_processing(config)
    lead = config.link.hardware_timed_tx_lead_seconds if config.link.hardware_timed_io else 0
    return (
        transition_allowance(config)
        + 2 * config.frequency.guard_seconds
        + lead
        + processing.tx_idle_samples(config.sdr) / config.sdr.sample_rate
        + 0.001
        + (PREPARATION_SECONDS if config.sdr.device.get("type") == "x300" else 0.010)
        + HOST_MARGIN_SECONDS
    )


def payload_airtime(config, payload_bytes: int) -> float:
    processing = resolve_processing(config)
    wire = processing.codec.payload_size(
        payload_bytes, authenticated=config.authentication.private_key_file is not None
    )
    return processing.make_modem(config.sdr).sample_count(wire) / config.sdr.sample_rate


def maximum_hop_rate(config) -> float:
    """Return the smaller of the product cap and the minimum-packet timing bound."""
    # Round inward so the displayed inclusive bound survives reciprocal/subtraction
    # rounding when validation turns that rate back into available airtime.
    estimate = math.nextafter(1 / (timing_reserve(config) + payload_airtime(config, 1)), 0)
    return min(MAX_HOP_RATE_HZ, estimate)


def payload_limit(config) -> int:
    """Find the largest legal payload fitting a full dwell, up to the user's ceiling."""
    if config.frequency.interval_seconds < 1 / MAX_HOP_RATE_HZ:
        raise ValueError(f"automatic payload: hop rate must not exceed {MAX_HOP_RATE_HZ:g} hops/s")
    ceiling = config.emulation["packet"]["max_payload_bytes"]
    available = config.frequency.interval_seconds - timing_reserve(config)
    processing = resolve_processing(config)
    modem = processing.make_modem(config.sdr)

    def fits(size):
        wire = processing.codec.payload_size(
            size, authenticated=config.authentication.private_key_file is not None
        )
        return modem.sample_count(wire) / config.sdr.sample_rate <= available

    if not fits(1):
        raise ValueError(
            "automatic payload: hop dwell cannot fit even one payload byte with timing reserves"
        )
    low, high = 1, ceiling
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low
