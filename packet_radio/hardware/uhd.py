"""Connect the application to Ettus USRP hardware through UHD.

Device helpers load UHD, construct a device and validate its capabilities.
UHDRadio configures RF settings, streams complex IQ samples, performs immediate
or timed operations, and reports stream diagnostics. SampleBlock carries IQ
with an optional hardware timestamp. BufferedRadio drains reception on a worker
thread and coordinates device operations with its bounded sample queue.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from packet_radio.config import SDRConfig

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SampleBlock:
    samples: np.ndarray
    time_ns: int | None = None


def load_uhd() -> Any:
    try:
        import uhd
    except ImportError as exc:
        raise RuntimeError(
            "Ettus UHD Python bindings are required; see docs/UHD_SETUP.md. "
            "Use the Python interpreter matching your UHD installation."
        ) from exc
    return uhd


def make_uhd_device(
    api: Any, arguments: dict[str, str], *, attempts: int = 1, retry_seconds: float = 0.0
) -> Any:
    if "driver" in arguments:
        raise ValueError("sdr.device uses UHD arguments (type: b200), not a driver selector")
    selector = api.types.DeviceAddr(arguments).to_string()
    for attempt in range(1, attempts + 1):
        try:
            return api.usrp.MultiUSRP(selector)
        except RuntimeError as exc:
            if attempt == attempts:
                raise
            LOG.warning("UHD open %d/%d failed: %s", attempt, attempts, exc)
            time.sleep(retry_seconds)
    raise ValueError("attempts must be positive")


def validate_device_capabilities(
    device: Any, config: SDRConfig, frequencies_hz: Sequence[float]
) -> None:
    """Validate UHD ranges before changing hardware settings."""
    for direction, gain, antenna in (
        ("rx", config.rx_gain, config.rx_antenna),
        ("tx", config.tx_gain, config.tx_antenna),
    ):
        label = direction.upper()
        if not 0 <= config.channel < getattr(device, f"get_{direction}_num_channels")():
            raise ValueError(f"UHD {label} channel {config.channel} is unavailable")
        for name, getter, values in (
            ("sample rate", "rates", (config.sample_rate,)),
            ("bandwidth", "bandwidth_range", (config.bandwidth,)),
            ("frequency", "freq_range", frequencies_hz),
            ("gain", "gain_range", (gain,)),
        ):
            limits = getattr(device, f"get_{direction}_{getter}")(config.channel)
            for value in values:
                if not limits.start() <= value <= limits.stop():
                    raise ValueError(f"UHD {label} does not support configured {name} {value}")
        if antenna is not None and antenna not in getattr(device, f"get_{direction}_antennas")(
            config.channel
        ):
            raise ValueError(f"UHD {label} does not provide configured antenna {antenna}")


class Radio(Protocol):
    def tune(self, frequency_hz: float) -> None: ...
    def transmit(self, samples: np.ndarray) -> None: ...
    def transmit_at(self, samples: np.ndarray, time_ns: int) -> None: ...
    def transmit_with_lead(self, samples: np.ndarray, lead_ns: int) -> int: ...
    def hardware_time(self) -> int: ...
    def set_hardware_time(self, time_ns: int) -> None: ...
    def receive_timed(self, count: int = 4096, timeout_us: int = 50_000) -> SampleBlock: ...
    def receive(self, count: int = 4096, timeout_us: int = 50_000) -> np.ndarray: ...
    def schedule_receive(self, time_ns: int, count: int) -> None: ...
    def close(self) -> None: ...


class UHDRadio:
    """Single-channel UHD streaming. BufferedRadio serializes stream ownership.

    RX remains enabled during bursts so USB turnaround cannot lose the peer's ACK.
    The application still schedules half-duplex packets. No buffer passed to UHD
    is concurrently accessed by the decoder.
    """

    concurrent_rx_tx = True

    COUNTERS = (
        "rx_overflows",
        "rx_timeouts",
        "rx_stream_errors",
        "timed_transmissions",
        "timed_transmit_late",
        "timed_receive_windows",
        "tx_underflows",
        "tx_stream_errors",
        "tx_timeouts",
    )

    def __init__(self, config: SDRConfig, frequencies_hz: Sequence[float] = ()):
        self.api = load_uhd()
        self.config = config
        self.device: Any = None
        self.rx_stream: Any = None
        self.tx_stream: Any = None
        self.rx_active = False
        self._control_lock = threading.RLock()
        self._counters = dict.fromkeys(self.COUNTERS, 0)
        self.actual_settings: dict[str, Any] = {}
        try:
            self.device = make_uhd_device(
                self.api,
                config.device,
                attempts=config.open_retry_attempts,
                retry_seconds=config.open_retry_seconds,
            )
            validate_device_capabilities(self.device, config, frequencies_hz)
            self.actual_settings["device"] = self.device.get_usrp_rx_info(config.channel)
            for direction, gain, antenna in (
                ("rx", config.rx_gain, config.rx_antenna),
                ("tx", config.tx_gain, config.tx_antenna),
            ):
                for name, requested in (
                    ("rate", config.sample_rate),
                    ("bandwidth", config.bandwidth),
                    ("gain", gain),
                ):
                    getattr(self.device, f"set_{direction}_{name}")(requested, config.channel)
                    actual = float(getattr(self.device, f"get_{direction}_{name}")(config.channel))
                    self.actual_settings[f"{direction}_{name}"] = actual
                    if name == "rate" and not math.isclose(actual, requested, rel_tol=1e-6):
                        raise ValueError(
                            f"UHD {direction} sample rate coerced: {requested} -> {actual}"
                        )
                    if not math.isclose(actual, requested, rel_tol=1e-6, abs_tol=0.01):
                        LOG.warning(
                            "UHD %s %s adjusted: %s -> %s", direction, name, requested, actual
                        )
                if antenna is not None:
                    getattr(self.device, f"set_{direction}_antenna")(antenna, config.channel)
                actual_antenna = getattr(self.device, f"get_{direction}_antenna")(config.channel)
                self.actual_settings[f"{direction}_antenna"] = actual_antenna
                if antenna is not None and actual_antenna != antenna:
                    raise ValueError(f"UHD {direction} antenna differs from requested {antenna}")
            # RX and TX share a B2xx master clock. Recheck both after both setters.
            for direction in ("rx", "tx"):
                actual = float(getattr(self.device, f"get_{direction}_rate")(config.channel))
                self.actual_settings[f"{direction}_rate"] = actual
                if not math.isclose(actual, config.sample_rate, rel_tol=1e-6):
                    raise ValueError(f"UHD {direction} sample rate coerced: {actual}")
            if frequencies_hz:
                self.tune(frequencies_hz[0])
            stream_args = self.api.usrp.StreamArgs("fc32", "sc16")
            stream_args.channels = [config.channel]
            self.rx_stream = self.device.get_rx_stream(stream_args)
            self.tx_stream = self.device.get_tx_stream(stream_args)
            self.tx_mtu = int(self.tx_stream.get_max_num_samps())
            if self.tx_mtu <= 0:
                raise RuntimeError("UHD returned an invalid TX buffer size")
            # Initialize UHD's lazy NumPy bridge on this thread. Concurrent first
            # send/recv calls can deadlock in extension/import initialization.
            # No stream is active yet and this zero-timeout read cannot transmit.
            self.rx_stream.recv(np.empty((1, 4096), np.complex64), self.api.types.RXMetadata(), 0.0)
            self._start_receive()
            LOG.info("UHD actual settings: %s", self.actual_settings)
        except BaseException:
            self.close()
            raise

    def _time_spec(self, nanoseconds: int) -> Any:
        seconds, remainder = divmod(int(nanoseconds), 1_000_000_000)
        return self.api.types.TimeSpec(seconds, remainder / 1e9)

    def hardware_time(self) -> int:
        with self._control_lock:
            return int(self.device.get_time_now().to_ticks(1e9))

    def set_hardware_time(self, time_ns: int) -> None:
        with self._control_lock:
            # Stop old-epoch samples before resetting the clock.
            self._stop_receive()
            self.device.set_time_now(self._time_spec(time_ns))
            self._start_receive()

    def tune(self, frequency_hz: float) -> None:
        with self._control_lock:
            validate_device_capabilities(self.device, self.config, (frequency_hz,))
            was_active = self.rx_active
            if was_active:
                self._stop_receive()
            try:
                for direction in ("rx", "tx"):
                    getattr(self.device, f"set_{direction}_freq")(
                        self.api.types.TuneRequest(frequency_hz), self.config.channel
                    )
                    actual = float(
                        getattr(self.device, f"get_{direction}_freq")(self.config.channel)
                    )
                    self.actual_settings[f"{direction}_frequency"] = actual
                    if not math.isclose(actual, frequency_hz, rel_tol=0, abs_tol=1):
                        raise ValueError(
                            f"UHD {direction} frequency coerced: {frequency_hz} -> {actual}"
                        )
                LOG.info("tuned to %.3f MHz", frequency_hz / 1e6)
            finally:
                if was_active:
                    self._start_receive()

    def transmit(self, samples: np.ndarray) -> None:
        self._transmit(samples, None)

    def transmit_at(self, samples: np.ndarray, time_ns: int) -> None:
        if time_ns <= self.hardware_time():
            self._counters["timed_transmit_late"] += 1
            raise OSError("timed transmit is late")
        self._transmit(samples, int(time_ns))

    def transmit_with_lead(self, samples: np.ndarray, lead_ns: int) -> int:
        target = self.hardware_time() + int(lead_ns)
        self.transmit_at(samples, target)
        return target

    def _end_burst(self) -> None:
        metadata = self.api.types.TXMetadata()
        metadata.end_of_burst = True
        self.tx_stream.send(np.empty((1, 0), np.complex64), metadata, 0.1)

    def _transmit(self, samples: np.ndarray, time_ns: int | None) -> None:
        # Copy even contiguous inputs: UHD releases the GIL while accessing memory.
        samples = np.array(samples, dtype=np.complex64, copy=True)
        if samples.ndim != 1 or not samples.size or not np.isfinite(samples).all():
            raise ValueError("TX requires a nonempty finite one-dimensional sample array")
        async_metadata = self.api.types.TXAsyncMetadata()
        # Old asynchronous reports cannot acknowledge this burst.
        for _ in range(64):
            if not self.tx_stream.recv_async_msg(async_metadata, 0.0):
                break
            self._check_tx_event(async_metadata.event_code)
        else:
            raise OSError("UHD async event queue did not drain")
        lead = 0 if time_ns is None else max(0, (time_ns - self.hardware_time()) / 1e9)
        deadline = time.monotonic() + lead + len(samples) / self.config.sample_rate + 1.0
        metadata = self.api.types.TXMetadata()
        metadata.start_of_burst = True
        metadata.has_time_spec = time_ns is not None
        if time_ns is not None:
            metadata.time_spec = self._time_spec(time_ns)
        ended = False
        try:
            position = 0
            while position < len(samples):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._counters["tx_timeouts"] += 1
                    raise OSError("UHD TX send deadline expired")
                chunk = samples[position : position + self.tx_mtu].reshape(1, -1)
                sent = int(self.tx_stream.send(chunk, metadata, min(remaining, 0.1)))
                if not 0 < sent <= chunk.size:
                    self._counters["tx_timeouts" if sent == 0 else "tx_stream_errors"] += 1
                    raise OSError(f"UHD TX incomplete transfer: {sent}/{chunk.size}")
                position += sent
                metadata.start_of_burst = False
                metadata.has_time_spec = False
            # A separate zero-sample EOB avoids ending a partially accepted final chunk.
            self._end_burst()
            ended = True
            while time.monotonic() < deadline:
                if self.tx_stream.recv_async_msg(
                    async_metadata, min(0.1, max(0, deadline - time.monotonic()))
                ):
                    self._check_tx_event(async_metadata.event_code)
                    if async_metadata.event_code == self.api.types.TXMetadataEventCode.burst_ack:
                        if time_ns is not None:
                            self._counters["timed_transmissions"] += 1
                        return
            self._counters["tx_timeouts"] += 1
            raise OSError("UHD TX burst completion timed out")
        finally:
            if not ended:
                self._end_burst()

    def _check_tx_event(self, event: Any) -> None:
        codes = self.api.types.TXMetadataEventCode
        if event == codes.burst_ack:
            return
        if event in (codes.underflow, codes.underflow_in_packet):
            counter = "tx_underflows"
        elif event == codes.time_error:
            counter = "timed_transmit_late"
        else:
            counter = "tx_stream_errors"
        self._counters[counter] += 1
        raise OSError(f"UHD TX asynchronous error: {event}")

    def _stop_receive(self) -> None:
        if self.rx_active:
            self.rx_stream.issue_stream_cmd(
                self.api.types.StreamCMD(self.api.types.StreamMode.stop_cont)
            )
            self.rx_active = False
            # Drain old epoch/frequency samples; bound work even if hardware misbehaves.
            metadata = self.api.types.RXMetadata()
            buffer = np.empty((1, 4096), np.complex64)
            for _ in range(256):
                received = self.rx_stream.recv(buffer, metadata, 0.01)
                codes = self.api.types.RXMetadataErrorCode
                if metadata.error_code == codes.overflow:
                    self._counters["rx_overflows"] += 1
                elif metadata.error_code not in (codes.none, codes.timeout):
                    self._counters["rx_stream_errors"] += 1
                if received == 0:
                    return
            raise OSError("UHD RX did not stop within the drain limit")

    def _start_receive(self, time_ns: int | None = None, count: int = 0) -> None:
        mode = self.api.types.StreamMode.num_done if count else self.api.types.StreamMode.start_cont
        command = self.api.types.StreamCMD(mode)
        command.stream_now = time_ns is None
        command.num_samps = count
        if time_ns is not None:
            command.time_spec = self._time_spec(time_ns)
        self.rx_stream.issue_stream_cmd(command)
        self.rx_active = True

    def schedule_receive(self, time_ns: int, count: int) -> None:
        if count < 0:
            raise ValueError("receive count cannot be negative")
        if time_ns <= self.hardware_time():
            raise OSError("timed receive activation is late")
        self._stop_receive()
        self._start_receive(time_ns, count)
        self._counters["timed_receive_windows"] += 1

    def receive(self, count: int = 4096, timeout_us: int = 50_000) -> np.ndarray:
        return self.receive_timed(count, timeout_us).samples

    def receive_timed(self, count: int = 4096, timeout_us: int = 50_000) -> SampleBlock:
        if count <= 0 or timeout_us < 0:
            raise ValueError("invalid receive buffer size or timeout")
        buffer = np.empty((1, count), np.complex64)
        metadata = self.api.types.RXMetadata()
        received = int(self.rx_stream.recv(buffer, metadata, timeout_us / 1e6))
        codes = self.api.types.RXMetadataErrorCode
        if metadata.error_code != codes.none:
            if metadata.error_code in (codes.timeout, codes.overflow):
                counter = "rx_timeouts" if metadata.error_code == codes.timeout else "rx_overflows"
                self._counters[counter] += 1
                return SampleBlock(buffer[0, :0])
            self._counters["rx_stream_errors"] += 1
            raise OSError(f"UHD RX error: {metadata.strerror()}")
        if not 0 <= received <= count:
            self._counters["rx_stream_errors"] += 1
            raise OSError(f"UHD RX invalid sample count: {received}")
        timestamp = int(metadata.time_spec.to_ticks(1e9)) if metadata.has_time_spec else None
        return SampleBlock(buffer[0, :received], timestamp)

    def receive_diagnostics(self) -> dict[str, int]:
        return dict(self._counters)

    def close(self) -> None:
        try:
            self._stop_receive()
        finally:
            # UHD streamers and MultiUSRP release their resources through reference ownership.
            self.rx_stream = None
            self.tx_stream = None
            self.device = None


class BufferedRadio:
    """Continuously drain a radio into a bounded queue on a dedicated thread."""

    def __init__(
        self,
        radio: Radio,
        *,
        block_size: int = 4096,
        queue_blocks: int = 256,
        read_timeout_us: int = 5_000,
    ):
        if block_size <= 0 or queue_blocks <= 0 or read_timeout_us <= 0:
            raise ValueError("buffer dimensions and read timeout must be positive")
        self.radio = radio
        self.block_size = block_size
        self.read_timeout_us = read_timeout_us
        self._queue: queue.Queue[SampleBlock] = queue.Queue(queue_blocks)
        self._io_lock = threading.Lock()
        self._paused = threading.Event()
        self._running = threading.Event()
        self._running.set()
        self._error: BaseException | None = None
        self.rx_buffer_drops = 0
        self.rx_buffer_flushes = 0
        self._thread = threading.Thread(
            target=self._drain,
            name="packet-radio-rx",
            daemon=True,
        )
        self._thread.start()

    def _drain(self) -> None:
        try:
            while self._running.is_set():
                if self._paused.is_set():
                    time.sleep(0.0005)
                    continue
                with self._io_lock:
                    timed_receive = getattr(self.radio, "receive_timed", None)
                    if timed_receive is None:
                        samples = self.radio.receive(self.block_size, self.read_timeout_us)
                        received = SampleBlock(samples)
                    else:
                        received = timed_receive(self.block_size, self.read_timeout_us)
                    if not len(received.samples):
                        continue
                    block = SampleBlock(
                        np.asarray(received.samples, dtype=np.complex64).copy(), received.time_ns
                    )
                    try:
                        self._queue.put_nowait(block)
                    except queue.Full:
                        try:
                            self._queue.get_nowait()
                        except queue.Empty:
                            pass
                        self.rx_buffer_drops += 1
                        self._queue.put_nowait(block)
        except BaseException as exc:  # surfaced to the application thread
            self._error = exc
            self._running.clear()

    def _discard_buffered(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
                self.rx_buffer_flushes += 1
            except queue.Empty:
                return

    def tune(self, frequency_hz: float) -> None:
        self._paused.set()
        try:
            with self._io_lock:
                self._discard_buffered()
                self.radio.tune(frequency_hz)
        finally:
            self._paused.clear()

    def flush_receive(self) -> None:
        with self._io_lock:
            self._discard_buffered()

    def transmit(self, samples: np.ndarray) -> None:
        if getattr(self.radio, "concurrent_rx_tx", False):
            # UHD has independent RX/TX streamers; keep draining throughout TX.
            self.radio.transmit(samples)
            return
        self._paused.set()
        try:
            with self._io_lock:
                self._discard_buffered()
                self.radio.transmit(samples)
                self._discard_buffered()
        finally:
            self._paused.clear()

    def hardware_time(self) -> int:
        if getattr(self.radio, "concurrent_rx_tx", False):
            # UHD control access is independently serialized by its adapter.
            return self.radio.hardware_time()
        with self._io_lock:
            return self.radio.hardware_time()

    def set_hardware_time(self, time_ns: int) -> None:
        self._paused.set()
        try:
            with self._io_lock:
                self._discard_buffered()
                self.radio.set_hardware_time(time_ns)
        finally:
            self._paused.clear()

    def transmit_at(self, samples: np.ndarray, time_ns: int) -> None:
        if getattr(self.radio, "concurrent_rx_tx", False):
            # UHD has independent RX/TX streamers; keep draining throughout TX.
            self.radio.transmit_at(samples, time_ns)
            return
        self._paused.set()
        try:
            with self._io_lock:
                self._discard_buffered()
                self.radio.transmit_at(samples, time_ns)
                self._discard_buffered()
        finally:
            self._paused.clear()

    def transmit_with_lead(self, samples: np.ndarray, lead_ns: int) -> int:
        """Choose the target only after gaining exclusive SDR access."""
        if getattr(self.radio, "concurrent_rx_tx", False):
            return self.radio.transmit_with_lead(samples, lead_ns)
        self._paused.set()
        try:
            with self._io_lock:
                self._discard_buffered()
                target_ns = self.radio.hardware_time() + int(lead_ns)
                self.radio.transmit_at(samples, target_ns)
                self._discard_buffered()
                return target_ns
        finally:
            self._paused.clear()

    def schedule_receive(self, time_ns: int, count: int) -> None:
        self._paused.set()
        try:
            with self._io_lock:
                self._discard_buffered()
                self.radio.schedule_receive(time_ns, count)
        finally:
            self._paused.clear()

    def receive(self, count: int = 4096, timeout_us: int = 50_000) -> np.ndarray:
        return self.receive_timed(count, timeout_us).samples

    def receive_timed(self, count: int = 4096, timeout_us: int = 50_000) -> SampleBlock:
        if self._error is not None:
            raise RuntimeError("SDR receive thread failed") from self._error
        try:
            return self._queue.get(timeout=min(timeout_us / 1_000_000, 0.01))
        except queue.Empty:
            return SampleBlock(np.empty(0, dtype=np.complex64))

    def receive_diagnostics(self) -> dict[str, int]:
        diagnostics = {
            "rx_buffer_drops": self.rx_buffer_drops,
            "rx_buffer_flushes": self.rx_buffer_flushes,
        }
        source = getattr(self.radio, "receive_diagnostics", None)
        if source is not None:
            diagnostics.update(source())
        return diagnostics

    def close(self) -> None:
        self._running.clear()
        self._thread.join(timeout=max(1.0, self.read_timeout_us / 500_000))
        if self._thread.is_alive():
            raise RuntimeError("SDR receive thread did not stop; device remains owned by worker")
        self.radio.close()
