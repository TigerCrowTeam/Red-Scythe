"""Load, represent and validate the radio's startup configuration.

Configuration dataclasses group endpoint, RF, modem, packet, timing, security,
recording and status settings. load_config parses the versioned YAML profile
and derives internal settings. validate_config checks one node, while
validate_network checks that peer configurations can operate together.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import yaml

from packet_radio.modems.registry import samples_per_symbol, waveform


@dataclass(frozen=True)
class EndpointConfig:
    bind_host: str = "127.0.0.1"
    bind_port: int = 50000
    destination_host: str = "127.0.0.1"
    destination_port: int = 50001
    max_payload_bytes: int = 1024
    drain_max_packets: int = 32


@dataclass(frozen=True)
class SDRConfig:
    device: dict[str, str] = field(default_factory=dict)
    sample_rate: float = 1_000_000
    bandwidth: float = 250_000
    tx_gain: float = 30
    rx_gain: float = 30
    tx_antenna: str | None = None
    rx_antenna: str | None = None
    channel: int = 0
    samples_per_symbol: int = 8
    fec_interleaver_depth: int = 16
    bit_repetitions: int = 1
    rs_parity_bytes: int = 0
    rrc_rolloff: float = 0.0
    rrc_span_symbols: int = 8
    modulation: str = "bpsk"
    frequency_deviation_hz: float | None = None
    gaussian_bt: float | None = None
    gaussian_span_symbols: int | None = None
    decoder_worker_enabled: bool = False
    decoder_output_batches: int = 1024
    open_retry_attempts: int = 5
    open_retry_seconds: float = 1.0

    @property
    def bit_rate_bps(self) -> float:
        return (
            self.sample_rate * waveform(self.modulation).bits_per_symbol / self.samples_per_symbol
        )

    @property
    def symbol_rate_baud(self) -> float:
        return self.sample_rate / self.samples_per_symbol


@dataclass(frozen=True)
class RFProfile:
    center_frequency_hz: float
    sample_rate_hz: float
    bandwidth_hz: float
    tx_gain_db: float
    rx_gain_db: float
    tx_antenna: str
    rx_antenna: str
    device: dict[str, str] = field(default_factory=dict)
    channel: int = 0
    open_retry_attempts: int = 5
    open_retry_seconds: float = 1.0


@dataclass(frozen=True)
class ModemProfile:
    modulation: str
    bit_rate_bps: float
    frequency_deviation_hz: float | None = None
    gaussian_bt: float | None = None
    gaussian_span_symbols: int | None = None
    rrc_rolloff: float = 0.0
    rrc_span_symbols: int = 8
    decoder_worker_enabled: bool = False
    decoder_output_batches: int = 1024


@dataclass(frozen=True)
class ErrorCorrectionProfile:
    scheme: Literal["extended-hamming-8-4"] = "extended-hamming-8-4"
    interleaver_depth: int = 16
    bit_repetitions: int = 1
    rs_parity_bytes: int = 0


@dataclass(frozen=True)
class PacketProfile:
    format: Literal["generic-v5"] = "generic-v5"
    max_payload_bytes: int = 1024


@dataclass(frozen=True)
class FrequencyConfig:
    frequencies_hz: tuple[float, ...]
    epoch: float
    interval_seconds: float = 300
    guard_seconds: float = 0.25
    reacquire_dwell_seconds: float = 1.0
    sync_loss_seconds: float = 15.0
    sequence_mode: Literal["cycle", "ping_pong"] = "cycle"


@dataclass(frozen=True)
class LinkConfig:
    ack_timeout_seconds: float = 0.6
    max_retries: int = 4
    duplicate_window_seconds: float = 30.0
    channel_busy_threshold: float = 0.02
    backoff_min_seconds: float = 0.01
    backoff_max_seconds: float = 0.08
    queue_size: int = 256
    control_queue_size: int = 32
    queue_airtime_seconds: float | None = None
    tdd_period_seconds: float = 0.0
    tdd_guard_seconds: float = 0.0
    tdd_inter_burst_seconds: float | None = None
    tdd_leader_bursts_per_slot: int = 1
    tdd_follower_bursts_per_slot: int = 2
    tdd_staggered_windows: bool = False
    tdd_receive_delay_compensation_seconds: float = 0.0
    hardware_timed_io: bool = False
    hardware_timed_tx_lead_seconds: float = 0.01
    hardware_sync_tx_lead_seconds: float = 0.01
    hardware_clock_sync: bool = False
    hardware_epoch_lead_seconds: float = 2.0
    hardware_slot_scheduling: bool = False
    hardware_slot_lookahead_seconds: float = 0.012
    hardware_slot_submission_margin_seconds: float = 0.0


@dataclass(frozen=True)
class StatusConfig:
    output: Literal["off", "text", "json"] = "text"
    interval_seconds: float = 10.0
    http_enabled: bool = True
    http_host: str = "127.0.0.1"
    http_port: int = 8765


@dataclass(frozen=True)
class RecordingConfig:
    directory: str | None = None
    iq: bool = True
    frames: bool = True
    max_file_bytes: int = 256 * 1024 * 1024
    max_sessions: int = 10
    min_free_bytes: int = 128 * 1024 * 1024


@dataclass(frozen=True)
class AuthenticationConfig:
    private_key_file: str | None = None
    public_key_files: dict[int, tuple[str, ...]] = field(default_factory=dict)
    replay_window: int = 128
    sync_max_age_seconds: float = 10.0
    sync_max_future_seconds: float = 10.0
    replay_state_file: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "public_key_files",
            {
                node: (paths,) if isinstance(paths, str) else tuple(paths)
                for node, paths in self.public_key_files.items()
            },
        )


@dataclass(frozen=True)
class RadioConfig:
    node_id: int
    peer_id: int
    timing_role: Literal["leader", "follower"]
    sdr: SDRConfig
    frequency: FrequencyConfig
    endpoint: EndpointConfig = field(default_factory=EndpointConfig)
    log_level: str = "INFO"
    link: LinkConfig = field(default_factory=LinkConfig)
    status: StatusConfig = field(default_factory=StatusConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    authentication: AuthenticationConfig = field(default_factory=AuthenticationConfig)
    schema_version: int = 1
    packet_format: Literal["generic-v5"] = "generic-v5"

    @property
    def center_frequency_hz(self) -> float:
        if len(self.frequency.frequencies_hz) != 1:
            raise ValueError("center_frequency_hz is defined only for a fixed-frequency profile")
        return self.frequency.frequencies_hz[0]


def _matches(value: Any, annotation: Any) -> bool:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        return any(_matches(value, item) for item in args)
    if origin is Literal:
        return value in args
    if origin is dict:
        return isinstance(value, dict) and all(
            _matches(k, args[0]) and _matches(v, args[1]) for k, v in value.items()
        )
    if origin is tuple:
        return isinstance(value, tuple) and all(_matches(v, args[0]) for v in value)
    if annotation is float:
        return type(value) in (int, float) and math.isfinite(value)
    if annotation in (int, bool, str, type(None)):
        return type(value) is annotation
    return isinstance(value, annotation)


def _section(cls, raw):
    if not isinstance(raw, dict):
        raise ValueError(f"{cls.__name__} must be a mapping")
    unknown = set(raw) - {item.name for item in fields(cls)}
    if unknown:
        raise ValueError(f"unknown {cls.__name__} settings: {sorted(unknown)}")
    try:
        result = cls(**raw)
    except (TypeError, AttributeError) as exc:
        raise ValueError(f"invalid {cls.__name__}: {exc}") from exc
    _check_types(result)
    return result


def _check_types(value):
    for name, annotation in get_type_hints(type(value)).items():
        if not _matches(getattr(value, name), annotation):
            raise ValueError(f"{type(value).__name__}.{name} has invalid type or non-finite value")


def load_config(path: str | Path) -> RadioConfig:
    with Path(path).open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a mapping")
    raw = dict(raw)
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("schema_version: 1 is required; see docs/CONFIGURATION.md for migration")
    allowed = {
        "schema_version",
        "node_id",
        "peer_id",
        "timing_role",
        "log_level",
        "radio",
        "modem",
        "error_correction",
        "packet",
        "endpoint",
        "link",
        "status",
        "recording",
        "authentication",
    }
    if set(raw) - allowed:
        raise ValueError(f"unknown profile sections: {sorted(set(raw) - allowed)}")
    radio = _section(RFProfile, raw.pop("radio", {}))
    modem = _section(ModemProfile, raw.pop("modem", {}))
    fec = _section(ErrorCorrectionProfile, raw.pop("error_correction", {}))
    packet = _section(PacketProfile, raw.pop("packet", {}))
    raw["sdr"] = SDRConfig(
        device=radio.device,
        sample_rate=radio.sample_rate_hz,
        bandwidth=radio.bandwidth_hz,
        tx_gain=radio.tx_gain_db,
        rx_gain=radio.rx_gain_db,
        tx_antenna=radio.tx_antenna,
        rx_antenna=radio.rx_antenna,
        channel=radio.channel,
        open_retry_attempts=radio.open_retry_attempts,
        open_retry_seconds=radio.open_retry_seconds,
        modulation=modem.modulation,
        frequency_deviation_hz=modem.frequency_deviation_hz,
        gaussian_bt=modem.gaussian_bt,
        gaussian_span_symbols=modem.gaussian_span_symbols,
        samples_per_symbol=samples_per_symbol(
            modem.modulation, radio.sample_rate_hz, modem.bit_rate_bps
        ),
        rrc_rolloff=modem.rrc_rolloff,
        rrc_span_symbols=modem.rrc_span_symbols,
        decoder_worker_enabled=modem.decoder_worker_enabled,
        decoder_output_batches=modem.decoder_output_batches,
        fec_interleaver_depth=fec.interleaver_depth,
        bit_repetitions=fec.bit_repetitions,
        rs_parity_bytes=fec.rs_parity_bytes,
    )
    raw["frequency"] = FrequencyConfig((radio.center_frequency_hz,), epoch=0, guard_seconds=0)
    raw["packet_format"] = packet.format
    endpoint = raw.get("endpoint", {})
    if not isinstance(endpoint, dict) or "max_payload_bytes" in endpoint:
        raise ValueError(
            "endpoint must be a mapping; payload limit belongs in packet.max_payload_bytes"
        )
    raw["endpoint"] = dict(endpoint, max_payload_bytes=packet.max_payload_bytes)
    for name, cls in (
        ("endpoint", EndpointConfig),
        ("link", LinkConfig),
        ("status", StatusConfig),
        ("recording", RecordingConfig),
        ("authentication", AuthenticationConfig),
    ):
        raw[name] = _section(cls, raw.get(name, {}))
    config = _section(RadioConfig, raw)
    validate_config(config)
    return config


def validate_config(config: RadioConfig) -> None:
    """Validate a peer without opening sockets, keys, or SDR devices."""
    _check_types(config)
    if config.schema_version != 1:
        raise ValueError("unsupported configuration schema_version")
    for section in (
        config.sdr,
        config.frequency,
        config.endpoint,
        config.link,
        config.status,
        config.recording,
        config.authentication,
    ):
        _check_types(section)
    if not 1 <= config.node_id <= 255 or not 1 <= config.peer_id <= 255:
        raise ValueError("node_id and peer_id must be 1..255; 0 is reserved for broadcast")
    if config.node_id == config.peer_id:
        raise ValueError("node_id and peer_id must differ")
    endpoint = config.endpoint
    if not endpoint.bind_host.strip() or not endpoint.destination_host.strip():
        raise ValueError("endpoint hosts must not be empty")
    if not 1 <= endpoint.bind_port <= 65535 or not 1 <= endpoint.destination_port <= 65535:
        raise ValueError("endpoint ports must be 1..65535")
    if (endpoint.bind_host, endpoint.bind_port) == (
        endpoint.destination_host,
        endpoint.destination_port,
    ):
        raise ValueError("endpoint input and output must differ")
    if not 1 <= endpoint.max_payload_bytes <= 1024 or not 1 <= endpoint.drain_max_packets <= 256:
        raise ValueError("invalid endpoint payload or drain limit")
    sdr = config.sdr
    if "driver" in sdr.device:
        raise ValueError("sdr.device requires UHD arguments such as type: b200")
    if sdr.sample_rate <= 0 or sdr.bandwidth <= 0 or sdr.bandwidth > sdr.sample_rate:
        raise ValueError("bandwidth cannot exceed sample_rate; both must be positive")
    if sdr.channel < 0 or sdr.samples_per_symbol < 2 or sdr.bit_repetitions < 1:
        raise ValueError("invalid SDR channel, samples_per_symbol, or bit_repetitions")
    if (
        sdr.fec_interleaver_depth < 1
        or not 0 <= sdr.rs_parity_bytes <= 254
        or sdr.rs_parity_bytes % 2
    ):
        raise ValueError("invalid FEC parameters")
    if sdr.modulation == "qpsk" and sdr.bit_repetitions != 1:
        raise ValueError("QPSK requires bit_repetitions=1")
    if sdr.modulation == "2fsk":
        if sdr.frequency_deviation_hz is None:
            raise ValueError("2FSK requires frequency_deviation_hz")
        if sdr.bandwidth < 2 * sdr.frequency_deviation_hz + 2 * sdr.bit_rate_bps:
            raise ValueError("2FSK bandwidth must cover 2*deviation + 2*bit_rate")
    elif sdr.modulation in ("msk", "gmsk"):
        if sdr.frequency_deviation_hz is not None:
            raise ValueError("MSK/GMSK derive frequency deviation from bit rate; omit the override")
        if sdr.bandwidth < 2.5 * sdr.bit_rate_bps:
            raise ValueError("MSK/GMSK bandwidth must cover 2.5*bit_rate")
    elif sdr.frequency_deviation_hz is not None:
        raise ValueError("frequency_deviation_hz is only supported for 2FSK")
    if not 0 <= sdr.rrc_rolloff <= 1 or sdr.rrc_span_symbols < 2 or sdr.rrc_span_symbols % 2:
        raise ValueError("invalid RRC parameters")
    if not 16 <= sdr.decoder_output_batches <= 65536:
        raise ValueError("invalid decoder_output_batches")
    if not 1 <= sdr.open_retry_attempts <= 60 or not 0 <= sdr.open_retry_seconds <= 30:
        raise ValueError("invalid SDR open retry settings")
    freq = config.frequency
    if not freq.frequencies_hz or any(v <= 0 for v in freq.frequencies_hz):
        raise ValueError("frequencies must be positive")
    if len(set(freq.frequencies_hz)) != len(freq.frequencies_hz):
        raise ValueError("frequency list contains duplicates")
    if freq.interval_seconds <= 0 or not 0 <= freq.guard_seconds < freq.interval_seconds / 2:
        raise ValueError("invalid frequency guard interval")
    if freq.reacquire_dwell_seconds <= 0 or freq.sync_loss_seconds <= 0:
        raise ValueError("invalid synchronization recovery settings")
    link = config.link
    if link.ack_timeout_seconds <= 0 or link.max_retries < 0 or link.duplicate_window_seconds <= 0:
        raise ValueError("invalid ACK, retry, or duplicate window settings")
    if link.queue_airtime_seconds is not None and link.queue_airtime_seconds <= 0:
        raise ValueError("queue_airtime_seconds must be positive")
    if link.queue_size < 1 or link.control_queue_size < 1:
        raise ValueError("queue capacities must be positive")
    if (
        link.channel_busy_threshold < 0
        or not 0 <= link.backoff_min_seconds <= link.backoff_max_seconds
    ):
        raise ValueError("invalid channel threshold or backoff range")
    if link.tdd_period_seconds < 0 or link.tdd_guard_seconds < 0:
        raise ValueError("TDD durations cannot be negative")
    if link.tdd_period_seconds == 0 and link.tdd_guard_seconds:
        raise ValueError("TDD guard requires a period")
    factor = 1 if link.tdd_staggered_windows else 2
    if link.tdd_period_seconds and link.tdd_guard_seconds * factor >= link.tdd_period_seconds / 2:
        raise ValueError("TDD guard leaves no transmit window")
    if min(link.tdd_leader_bursts_per_slot, link.tdd_follower_bursts_per_slot) < 1:
        raise ValueError("TDD bursts_per_slot must be positive")
    if link.tdd_inter_burst_seconds is not None and link.tdd_inter_burst_seconds < 0:
        raise ValueError("tdd_inter_burst_seconds cannot be negative")
    for name in ("hardware_timed_tx_lead_seconds", "hardware_sync_tx_lead_seconds"):
        if not 0.002 <= getattr(link, name) <= 0.1:
            raise ValueError(f"invalid {name}")
    if not 0.5 <= link.hardware_epoch_lead_seconds <= 10:
        raise ValueError("invalid hardware epoch lead")
    if not 0.003 <= link.hardware_slot_lookahead_seconds <= 0.05:
        raise ValueError("invalid hardware slot lookahead")
    if (
        not 0 <= link.hardware_slot_submission_margin_seconds <= 0.1
        or not 0 <= link.tdd_receive_delay_compensation_seconds <= 0.1
    ):
        raise ValueError("invalid timing margin or compensation")
    if (link.hardware_clock_sync or link.hardware_slot_scheduling) and not link.hardware_timed_io:
        raise ValueError("hardware synchronization/scheduling requires timed I/O")
    if link.hardware_slot_scheduling and (
        not link.hardware_clock_sync or link.tdd_period_seconds <= 0
    ):
        raise ValueError("hardware slot scheduling requires clock sync and TDD")
    if not 1 <= config.status.http_port <= 65535 or config.status.interval_seconds <= 0:
        raise ValueError("invalid status port or interval")
    if not config.status.http_host.strip():
        raise ValueError("status host cannot be empty")
    if config.log_level.upper() not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise ValueError("invalid log level")
    recording = config.recording
    if (
        recording.max_file_bytes < 16384
        or recording.max_sessions < 1
        or recording.min_free_bytes < 0
    ):
        raise ValueError("invalid recording limits")
    if recording.directory is not None and (
        not recording.directory.strip() or not (recording.iq or recording.frames)
    ):
        raise ValueError("invalid recording directory or sources")
    auth = config.authentication
    if auth.private_key_file is None and auth.public_key_files:
        raise ValueError("public keys require a private key")
    if auth.private_key_file is not None:
        if not auth.private_key_file.strip() or set(auth.public_key_files) != {
            config.node_id,
            config.peer_id,
        }:
            raise ValueError("authentication keys must include exactly the local node and peer")
        if any(
            not paths or any(not path.strip() for path in paths)
            for paths in auth.public_key_files.values()
        ):
            raise ValueError("authentication key paths cannot be empty")
    if auth.replay_window < 1 or min(auth.sync_max_age_seconds, auth.sync_max_future_seconds) <= 0:
        raise ValueError("invalid authentication replay or freshness window")
    if auth.replay_state_file is not None and not auth.replay_state_file.strip():
        raise ValueError("replay state path cannot be empty")

    from packet_radio.modems.registry import make_modem
    from packet_radio.protocol.framing import RadioPacket

    modem = make_modem(sdr)
    largest = RadioPacket(config.node_id, config.peer_id, 0, bytes(endpoint.max_payload_bytes))
    wire_size = largest.encoded_size(authenticated=auth.private_key_file is not None)
    maximum_airtime = modem.sample_count(wire_size) / sdr.sample_rate
    if link.queue_airtime_seconds is not None and maximum_airtime > link.queue_airtime_seconds:
        raise ValueError("queue airtime limit cannot admit the maximum payload")
    if link.tdd_period_seconds:
        usable = link.tdd_period_seconds / 2 - factor * link.tdd_guard_seconds
        lead = link.hardware_timed_tx_lead_seconds if link.hardware_timed_io else 0
        if maximum_airtime + lead + 0.001 > usable:
            raise ValueError("largest encoded packet cannot fit the configured TDD window")


def validate_network(configs: dict[str, RadioConfig]) -> None:
    if len(configs) != 2:
        raise ValueError("network must contain exactly two peers")
    for config in configs.values():
        validate_config(config)
    first, second = configs.values()
    if first.node_id == second.node_id:
        raise ValueError("duplicate radio node IDs")
    if first.peer_id != second.node_id or second.peer_id != first.node_id:
        raise ValueError("peer IDs must point to each other")
    if {first.timing_role, second.timing_role} != {"leader", "follower"}:
        raise ValueError("network requires one timing leader and one follower")
    if first.frequency != second.frequency:
        raise ValueError("frequency schedule differs between peers")
    for name in (
        "sample_rate",
        "bandwidth",
        "samples_per_symbol",
        "modulation",
        "frequency_deviation_hz",
        "gaussian_bt",
        "gaussian_span_symbols",
        "bit_repetitions",
        "fec_interleaver_depth",
        "rs_parity_bytes",
        "rrc_rolloff",
        "rrc_span_symbols",
    ):
        if getattr(first.sdr, name) != getattr(second.sdr, name):
            raise ValueError(f"waveform {name} differs between peers")
    for item in fields(LinkConfig):
        if item.name.startswith(("tdd_", "hardware_")) and getattr(
            first.link, item.name
        ) != getattr(second.link, item.name):
            raise ValueError(f"timing {item.name} differs between peers")
    if first.endpoint.max_payload_bytes != second.endpoint.max_payload_bytes:
        raise ValueError("payload limits differ between peers")
    if bool(first.authentication.private_key_file) != bool(second.authentication.private_key_file):
        raise ValueError("authentication enablement differs between peers")


def configuration_snapshot(config: RadioConfig) -> dict[str, Any]:
    """Describe effective settings while redacting authentication file paths."""
    result = asdict(config)
    auth = result["authentication"]
    for name in ("private_key_file", "replay_state_file"):
        if auth[name] is not None:
            auth[name] = "<configured>"
    auth["public_key_files"] = {
        str(node): ["<configured>"] * len(paths)
        for node, paths in config.authentication.public_key_files.items()
    }
    return json.loads(json.dumps(result))
