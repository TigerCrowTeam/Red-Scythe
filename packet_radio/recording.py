"""Record bounded captures and provide helpers for offline replay.

CaptureRecorder writes IQ samples, decoded frames and a settings manifest,
rotates capture files and manages storage limits. load_manifest reads capture
settings; replay_iq reconstructs the configured modem to decode stored samples;
replay_frames reads stored wire packets. describe_frames turns decoded packet
results into metadata for the replay command.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import secrets
import shutil
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Callable

import numpy as np

from packet_radio.config import SDRConfig, _check_types, config_from_snapshot
from packet_radio.modems.fec import INTERLEAVER_DEPTH
from packet_radio.modems.registry import samples_per_symbol as derive_sps
from packet_radio.modems.registry import waveform
from packet_radio.protocol.framing import PROTOCOL_VERSION
from packet_radio.protocol.registry import ProcessingProfile, processing_profile, resolve_processing

CAPTURE_FORMAT_VERSION = 3
IQ_DTYPE = np.dtype("<c8")
LOG = logging.getLogger(__name__)


class CaptureRecorder:
    """Append IQ samples and demodulated radio frames to a capture directory."""

    def __init__(
        self,
        directory: str | Path,
        *,
        sample_rate: float,
        resolved_configuration: dict | None = None,
        center_frequency_hz: float | None = None,
        samples_per_symbol: int,
        rrc_rolloff: float = 0.0,
        rrc_span_symbols: int = 8,
        modulation: str = "bpsk",
        frequency_deviation_hz: float | None = None,
        gaussian_bt: float | None = None,
        gaussian_span_symbols: int | None = None,
        interleaver_depth: int = INTERLEAVER_DEPTH,
        bit_repetitions: int = 1,
        rs_parity_bytes: int = 0,
        record_iq: bool = True,
        record_frames: bool = True,
        authenticated: bool = False,
        max_file_bytes: int = 256 * 1024 * 1024,
        max_sessions: int = 10,
        min_free_bytes: int = 128 * 1024 * 1024,
        create_session: bool = False,
        on_disable: Callable[[str], None] | None = None,
    ):
        if max_file_bytes < 16 * 1024 or max_sessions < 1 or min_free_bytes < 0:
            raise ValueError("invalid capture size, retention, or free-space limit")
        definition = waveform(modulation)
        if type(samples_per_symbol) is not int or samples_per_symbol < 2:
            raise ValueError("invalid samples_per_symbol")
        bit_rate_bps = sample_rate * definition.bits_per_symbol / samples_per_symbol
        derive_sps(modulation, sample_rate, bit_rate_bps)
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        if create_session:
            self._prune_sessions(root, max_sessions - 1)
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            root = root / f"capture-{stamp}-{os.getpid()}-{secrets.token_hex(3)}"
        self.directory = root
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = max_file_bytes
        self.min_free_bytes = min_free_bytes
        self.on_disable = on_disable
        self.disabled_reason: str | None = None
        self.record_iq_enabled = record_iq
        self.record_frames_enabled = record_frames
        self._started_ns = time.time_ns()
        manifest = {
            "capture_format_version": 2,
            "protocol_version": PROTOCOL_VERSION,
            "configuration_schema_version": 1,
            "packet_format": "generic-v5",
            "burst_format": definition.burst_format,
            "center_frequency_hz": center_frequency_hz,
            "bit_rate_bps": bit_rate_bps,
            "symbol_rate_baud": sample_rate / samples_per_symbol,
            "fec_scheme": "extended-hamming-8-4",
            "interleaver_depth": interleaver_depth,
            "bit_repetitions": bit_repetitions,
            "rs_parity_bytes": rs_parity_bytes,
            "authenticated": authenticated,
            "sample_rate": sample_rate,
            "samples_per_symbol": samples_per_symbol,
            "rrc_rolloff": rrc_rolloff,
            "rrc_span_symbols": rrc_span_symbols,
            "modulation": modulation,
            "frequency_deviation_hz": frequency_deviation_hz,
            "iq_dtype": IQ_DTYPE.str,
        }
        if modulation == "gmsk" or gaussian_bt is not None or gaussian_span_symbols is not None:
            manifest.update(gaussian_bt=gaussian_bt, gaussian_span_symbols=gaussian_span_symbols)
        if resolved_configuration is not None:
            from packet_radio.profiles import profile_fingerprint

            resolved = config_from_snapshot(resolved_configuration)
            selected = resolve_processing(resolved)
            manifest.update(
                capture_format_version=CAPTURE_FORMAT_VERSION,
                resolved_configuration=resolved_configuration,
                configuration_sha256=profile_fingerprint(resolved_configuration),
                processing=selected.describe(),
                tx_idle_samples=selected.tx_idle_samples(resolved.sdr),
                configuration_schema_version=resolved.schema_version,
                packet_format=selected.name,
                protocol_version=PROTOCOL_VERSION if selected.name == "generic-v5" else None,
                burst_format=definition.burst_format
                if selected.name == "generic-v5"
                else "simple-frame-v1",
                fec_scheme=(
                    resolved.emulation["protection"]["scheme"]
                    if resolved.emulation
                    else "extended-hamming-8-4"
                ),
            )
        _manifest_modem(manifest)
        if create_session:
            manifest["session_started_unix_ns"] = self._started_ns
        manifest_path = self.directory / "manifest.json"
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing != manifest:
                raise ValueError("capture directory has an incompatible manifest")
        else:
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        self._iq_index = 0
        self._frame_index = 0
        self._iq = self._open_iq() if record_iq else None
        self._frames = self._open_frames() if record_frames else None

    @staticmethod
    def _prune_sessions(root: Path, keep: int) -> None:
        sessions = sorted(
            (
                path
                for path in root.glob("capture-*")
                if path.is_dir() and (path / "manifest.json").is_file()
            ),
            key=lambda path: (path / "manifest.json").stat().st_mtime_ns,
        )
        for path in sessions[: max(0, len(sessions) - keep)]:
            shutil.rmtree(path)

    def _open_iq(self):
        path = self.directory / f"iq-{self._iq_index:05d}.c64"
        return path.open("ab")

    def _open_frames(self):
        path = self.directory / f"frames-{self._frame_index:05d}.jsonl"
        return path.open("a", encoding="utf-8")

    def _space_available(self, amount: int) -> bool:
        return shutil.disk_usage(self.directory).free - amount >= self.min_free_bytes

    def _disable(self, reason: str) -> None:
        if self.disabled_reason is not None:
            return
        self.disabled_reason = reason
        LOG.error("capture recording disabled: %s", reason)
        self.close()
        if self.on_disable is not None:
            self.on_disable(reason)

    def record_iq(self, samples: np.ndarray) -> None:
        if self._iq is None or len(samples) == 0:
            return
        data = memoryview(np.asarray(samples, dtype=IQ_DTYPE).tobytes())
        try:
            if not self._space_available(len(data)):
                self._disable("minimum free-space threshold reached")
                return
            while data:
                available = self.max_file_bytes - self._iq.tell()
                if available < IQ_DTYPE.itemsize:
                    self._iq.close()
                    self._iq_index += 1
                    self._iq = self._open_iq()
                    available = self.max_file_bytes
                chunk_size = min(len(data), available - available % IQ_DTYPE.itemsize)
                self._iq.write(data[:chunk_size])
                data = data[chunk_size:]
            self._iq.flush()
        except OSError as exc:
            self._disable(str(exc))

    def record_frame(self, wire: bytes, error: str | None = None) -> None:
        if self._frames is None:
            return
        entry = {
            "elapsed_ns": time.time_ns() - self._started_ns,
            "wire_base64": base64.b64encode(wire).decode("ascii"),
            "valid": error is None,
        }
        if error is not None:
            entry["error"] = error
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        try:
            encoded_size = len(line.encode("utf-8"))
            if not self._space_available(encoded_size):
                self._disable("minimum free-space threshold reached")
                return
            if self._frames.tell() and self._frames.tell() + encoded_size > self.max_file_bytes:
                self._frames.close()
                self._frame_index += 1
                self._frames = self._open_frames()
            self._frames.write(line)
            self._frames.flush()
        except OSError as exc:
            self._disable(str(exc))

    def close(self) -> None:
        if self._iq is not None:
            self._iq.close()
            self._iq = None
        if self._frames is not None:
            self._frames.close()
            self._frames = None


def load_manifest(directory: str | Path) -> dict[str, Any]:
    path = Path(directory) / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read capture manifest: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("capture_format_version")) is not int
        or manifest["capture_format_version"] not in (1, 2, CAPTURE_FORMAT_VERSION)
    ):
        raise ValueError("unsupported capture format version")
    if (
        manifest["capture_format_version"] != 3
        and manifest.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise ValueError("unsupported capture protocol version")
    _manifest_modem(manifest)
    return manifest


def _manifest_modem(manifest: dict[str, Any]):
    try:
        if manifest.get("capture_format_version") == 3:
            config = _resolved_manifest_config(manifest)
            return resolve_processing(config).make_modem(config.sdr, clock=lambda: 0.0)
        if manifest.get("iq_dtype") != IQ_DTYPE.str:
            raise ValueError("unsupported capture IQ dtype")
        modulation = manifest.get("modulation", "bpsk")
        definition = waveform(modulation)
        rate = manifest["sample_rate"]
        sps = manifest["samples_per_symbol"]
        if type(sps) is not int or sps < 2 or type(rate) not in (int, float):
            raise ValueError("invalid capture sample rate/symbol rate")
        bit_rate = rate * definition.bits_per_symbol / sps
        if manifest["capture_format_version"] == 2:
            if (
                type(manifest.get("configuration_schema_version")) is not int
                or manifest["configuration_schema_version"] != 1
            ):
                raise ValueError("unsupported capture configuration schema")
            frequency = manifest.get("center_frequency_hz")
            if frequency is not None and (
                type(frequency) not in (int, float)
                or not math.isfinite(frequency)
                or frequency <= 0
            ):
                raise ValueError("invalid capture center frequency")
            if (
                manifest.get("packet_format") != "generic-v5"
                or manifest.get("burst_format") != definition.burst_format
            ):
                raise ValueError("unsupported packet or burst format")
            if (
                manifest.get("bit_rate_bps") != bit_rate
                or manifest.get("symbol_rate_baud") != rate / sps
            ):
                raise ValueError("capture rate fields disagree")
        if derive_sps(modulation, rate, bit_rate) != sps:
            raise ValueError("capture timing differs")
        if manifest.get("fec_scheme") != "extended-hamming-8-4":
            raise ValueError("unsupported capture error correction")
        config = SDRConfig(
            modulation=modulation,
            frequency_deviation_hz=manifest.get("frequency_deviation_hz"),
            gaussian_bt=manifest.get("gaussian_bt"),
            gaussian_span_symbols=manifest.get("gaussian_span_symbols"),
            sample_rate=rate,
            samples_per_symbol=sps,
            rrc_rolloff=manifest.get("rrc_rolloff", 0.0),
            rrc_span_symbols=manifest.get("rrc_span_symbols", 8),
            fec_interleaver_depth=manifest.get("interleaver_depth", INTERLEAVER_DEPTH),
            bit_repetitions=manifest.get("bit_repetitions", 1),
            rs_parity_bytes=manifest.get("rs_parity_bytes", 0),
        )
        _check_types(config)
        return processing_profile(manifest.get("packet_format", "generic-v5")).make_modem(config)
    except (KeyError, TypeError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid capture waveform: {exc}") from exc


def replay_iq(directory: str | Path, chunk_samples: int = 4096) -> Iterator[bytes]:
    if type(chunk_samples) is not int or chunk_samples <= 0:
        raise ValueError("chunk_samples must be a positive integer")
    manifest = load_manifest(directory)
    modem = _manifest_modem(manifest)
    try:
        paths = sorted(Path(directory).glob("iq-*.c64"))
        legacy = Path(directory) / "iq.c64"
        if legacy.exists():
            paths.insert(0, legacy)
        for path in paths:
            with path.open("rb") as stream:
                while True:
                    samples = np.fromfile(stream, dtype=IQ_DTYPE, count=chunk_samples)
                    if len(samples) == 0:
                        break
                    yield from modem.feed(samples)
    except OSError as exc:
        raise ValueError(f"cannot replay IQ capture: {exc}") from exc


def replay_frames(directory: str | Path) -> Iterator[bytes]:
    load_manifest(directory)
    try:
        paths = sorted(Path(directory).glob("frames-*.jsonl"))
        legacy = Path(directory) / "frames.jsonl"
        if legacy.exists():
            paths.insert(0, legacy)
        for path in paths:
            with path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    try:
                        entry = json.loads(line)
                        yield base64.b64decode(entry["wire_base64"], validate=True)
                    except (json.JSONDecodeError, KeyError, ValueError) as exc:
                        raise ValueError(
                            f"invalid frame record in {path.name} line {line_number}"
                        ) from exc
    except OSError as exc:
        raise ValueError(f"cannot replay frame capture: {exc}") from exc


def describe_frames(
    wires: Iterable[bytes],
    verification_keys: dict[int, Any] | None = None,
    *,
    processing: ProcessingProfile | None = None,
) -> Iterator[dict[str, Any]]:
    selected = processing or processing_profile("generic-v5")
    for index, wire in enumerate(wires):
        try:
            packet = selected.codec.decode(wire, verification_keys)
        except ValueError as exc:
            yield {"index": index, "valid": False, "error": str(exc)}
            continue
        if selected.name == "simple-frame-v1":
            yield {
                "index": index,
                "valid": True,
                "packet_format": selected.name,
                "payload_base64": base64.b64encode(packet).decode("ascii"),
            }
            continue
        yield {
            "index": index,
            "valid": True,
            "kind": packet.kind.name,
            "source": packet.source,
            "destination": packet.destination,
            "sequence": packet.sequence,
            "session_id": packet.session_id,
            "payload_base64": base64.b64encode(packet.payload).decode("ascii"),
        }


def _resolved_manifest_config(manifest: dict):
    """Reject conflicting metadata rather than replaying with guessed defaults."""
    from packet_radio.profiles import profile_fingerprint

    raw = manifest.get("resolved_configuration")
    if not isinstance(raw, dict):
        raise ValueError("capture requires resolved_configuration")
    config = config_from_snapshot(raw)
    processing = resolve_processing(config)
    if manifest.get("configuration_sha256") != profile_fingerprint(raw):
        raise ValueError("capture resolved configuration checksum differs")
    sdr = config.sdr
    expected = {
        "iq_dtype": IQ_DTYPE.str,
        "processing": processing.describe(),
        "tx_idle_samples": processing.tx_idle_samples(sdr),
        "configuration_schema_version": config.schema_version,
        "packet_format": config.packet_format,
        "protocol_version": PROTOCOL_VERSION if config.packet_format == "generic-v5" else None,
        "burst_format": waveform(sdr.modulation).burst_format
        if config.packet_format == "generic-v5"
        else "simple-frame-v1",
        "authenticated": config.authentication.private_key_file is not None,
        "center_frequency_hz": config.frequency.frequencies_hz[0],
        "sample_rate": sdr.sample_rate,
        "samples_per_symbol": sdr.samples_per_symbol,
        "bit_rate_bps": sdr.bit_rate_bps,
        "symbol_rate_baud": sdr.symbol_rate_baud,
        "modulation": sdr.modulation,
        "frequency_deviation_hz": sdr.frequency_deviation_hz,
        "rrc_rolloff": sdr.rrc_rolloff,
        "rrc_span_symbols": sdr.rrc_span_symbols,
        "interleaver_depth": sdr.fec_interleaver_depth,
        "bit_repetitions": sdr.bit_repetitions,
        "rs_parity_bytes": sdr.rs_parity_bytes,
        "fec_scheme": config.emulation["protection"]["scheme"]
        if config.emulation
        else "extended-hamming-8-4",
    }
    if sdr.modulation == "gmsk":
        expected.update(
            gaussian_bt=sdr.gaussian_bt, gaussian_span_symbols=sdr.gaussian_span_symbols
        )
    for key, value in expected.items():
        if (
            key not in manifest
            or manifest[key] != value
            or (type(value) in (bool, int) and type(manifest[key]) is not type(value))
        ):
            raise ValueError(f"capture {key} disagrees with resolved configuration")
    return config


def manifest_processing(manifest: dict) -> ProcessingProfile:
    if manifest.get("capture_format_version") == 3:
        return resolve_processing(_resolved_manifest_config(manifest))
    return processing_profile("generic-v5")
