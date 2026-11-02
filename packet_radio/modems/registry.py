"""Select a supported waveform and construct it from radio settings.

Waveform entries describe modulation properties and constructors. waveform
resolves a modulation name, samples_per_symbol derives valid symbol timing
from the requested rates, and make_modem applies the SDR and error-correction
settings to a BPSK, QPSK, 2FSK, MSK or GMSK modem.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from packet_radio.modems import BPSKModem, GMSKModem, Modem, MSKModem, QPSKModem, StreamingModem
from packet_radio.modems.burst import (
    QPSK_TRAINING,
    TRAINING,
    BurstEnvelope,
    BurstProcessing,
    PacketProtection,
)
from packet_radio.modems.fsk import FSK2Modem

if TYPE_CHECKING:
    from packet_radio.config import SDRConfig


@dataclass(frozen=True)
class Waveform:
    build: Callable[["SDRConfig", BurstProcessing], Modem]
    bits_per_symbol: int
    training: bytes
    burst_format: str


WAVEFORMS = {
    "bpsk": Waveform(lambda c, p: _make_psk(c, BPSKModem, p), 1, TRAINING, "bpsk-burst-v1"),
    "2fsk": Waveform(lambda c, p: _make_fsk(c, p), 1, TRAINING, "2fsk-burst-v1"),
    "msk": Waveform(lambda c, p: _make_msk(c, p), 1, TRAINING, "msk-burst-v1"),
    "gmsk": Waveform(lambda c, p: _make_gmsk(c, p), 1, QPSK_TRAINING, "gmsk-burst-v1"),
    "qpsk": Waveform(lambda c, p: _make_psk(c, QPSKModem, p), 2, QPSK_TRAINING, "qpsk-burst-v1"),
}


def waveform(name: str) -> Waveform:
    try:
        return WAVEFORMS[name]
    except KeyError as exc:
        raise ValueError(
            f"unsupported modulation {name!r}; available: {', '.join(WAVEFORMS)}"
        ) from exc


def samples_per_symbol(name: str, sample_rate: float, bit_rate_bps: float) -> int:
    import math

    if (
        not math.isfinite(sample_rate)
        or not math.isfinite(bit_rate_bps)
        or min(sample_rate, bit_rate_bps) <= 0
    ):
        raise ValueError("sample rate and bit rate must be positive and finite")
    value = sample_rate * waveform(name).bits_per_symbol / bit_rate_bps
    if not math.isfinite(value):
        raise ValueError("rate ratio is not finite")
    nearest = round(value)
    if nearest < 2 or not math.isclose(value, nearest, rel_tol=0, abs_tol=1e-9):
        raise ValueError(
            "bit rate requires an integral samples_per_symbol >= 2; resampling is unsupported"
        )
    return nearest


def make_modem(
    config: "SDRConfig",
    *,
    candidate_validator: Callable[[bytes], bool] | None = None,
    processing: BurstProcessing | None = None,
) -> "Modem":
    if config.modulation != "gmsk" and (
        config.gaussian_bt is not None or config.gaussian_span_symbols is not None
    ):
        raise ValueError("Gaussian settings are only supported for GMSK")
    definition = waveform(config.modulation)
    if definition.bits_per_symbol > 1 and config.bit_repetitions != 1:
        raise ValueError("QPSK requires bit_repetitions=1")
    if processing is None:
        processing = BurstProcessing(
            PacketProtection(config.fec_interleaver_depth, config.rs_parity_bytes),
            BurstEnvelope(definition.training),
        )
    supported = getattr(processing.envelope, "supported_modulations", tuple(WAVEFORMS))
    if config.modulation not in supported:
        raise ValueError("selected envelope is unsupported for this modulation")
    if getattr(processing.envelope, "bit_order", "big") != "big" and config.modulation != "2fsk":
        raise ValueError("configurable bit order is currently supported only for 2FSK")
    modem = definition.build(config, processing)
    modem.candidate_validator = candidate_validator
    return modem


def _make_psk(
    config: "SDRConfig", kind: type[StreamingModem], processing: BurstProcessing
) -> Modem:
    return kind(
        config.samples_per_symbol,
        processing=processing,
        sample_rate=config.sample_rate,
        interleaver_depth=config.fec_interleaver_depth,
        bit_repetitions=config.bit_repetitions,
        rs_parity_bytes=config.rs_parity_bytes,
        rrc_rolloff=config.rrc_rolloff,
        rrc_span_symbols=config.rrc_span_symbols,
    )


def _make_fsk(config: "SDRConfig", processing: BurstProcessing) -> Modem:
    if config.frequency_deviation_hz is None:
        raise ValueError("2FSK requires frequency_deviation_hz")
    return FSK2Modem(
        config.samples_per_symbol,
        processing=processing,
        frequency_deviation_hz=config.frequency_deviation_hz,
        sample_rate=config.sample_rate,
        interleaver_depth=config.fec_interleaver_depth,
        bit_repetitions=config.bit_repetitions,
        rs_parity_bytes=config.rs_parity_bytes,
        rrc_rolloff=config.rrc_rolloff,
    )


def _make_msk(config: "SDRConfig", processing: BurstProcessing) -> Modem:
    if config.frequency_deviation_hz is not None:
        raise ValueError("MSK derives frequency deviation from bit rate; omit the override")
    return MSKModem(
        config.samples_per_symbol,
        processing=processing,
        sample_rate=config.sample_rate,
        interleaver_depth=config.fec_interleaver_depth,
        bit_repetitions=config.bit_repetitions,
        rs_parity_bytes=config.rs_parity_bytes,
        rrc_rolloff=config.rrc_rolloff,
    )


def _make_gmsk(config: "SDRConfig", processing: BurstProcessing) -> Modem:
    if config.frequency_deviation_hz is not None:
        raise ValueError("GMSK derives frequency deviation from bit rate; omit the override")
    if config.gaussian_bt is None or config.gaussian_span_symbols is None:
        raise ValueError("GMSK requires gaussian_bt and gaussian_span_symbols")
    return GMSKModem(
        config.samples_per_symbol,
        processing=processing,
        sample_rate=config.sample_rate,
        gaussian_bt=config.gaussian_bt,
        gaussian_span_symbols=config.gaussian_span_symbols,
        interleaver_depth=config.fec_interleaver_depth,
        bit_repetitions=config.bit_repetitions,
        rs_parity_bytes=config.rs_parity_bytes,
        rrc_rolloff=config.rrc_rolloff,
    )
