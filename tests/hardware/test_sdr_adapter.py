import sys
import time
from dataclasses import replace
from enum import IntEnum
from types import SimpleNamespace

import numpy as np
import pytest

from packet_radio.config import SDRConfig
from packet_radio.hardware.uhd import (
    BufferedRadio,
    UHDRadio,
    make_uhd_device,
    validate_device_capabilities,
)


class RXCode(IntEnum):
    none = 0
    timeout = 1
    late = 2
    broken_chain = 4
    overflow = 8
    alignment = 12
    bad_packet = 15


class TXCode(IntEnum):
    burst_ack = 1
    underflow = 2
    seq_error = 4
    time_error = 8
    underflow_in_packet = 16
    seq_error_in_packet = 32
    user_payload = 64


class TimeSpec:
    def __init__(self, seconds=0, fraction=0):
        self.ns = int(seconds) * 1_000_000_000 + round(fraction * 1e9)

    def to_ticks(self, rate):
        assert rate == 1e9
        return self.ns


class Metadata:
    def __init__(self):
        self.start_of_burst = False
        self.end_of_burst = False
        self.has_time_spec = False
        self.time_spec = TimeSpec()
        self.error_code = RXCode.none

    def strerror(self):
        return str(self.error_code)


class Limits:
    def __init__(self, low, high):
        self.low, self.high = low, high

    def start(self):
        return self.low

    def stop(self):
        return self.high


class FakeRX:
    def __init__(self):
        self.commands = []
        self.code = RXCode.none
        self.count = 3
        self.timestamp = 4_200_000_000
        self.stopped = False

    def issue_stream_cmd(self, command):
        self.commands.append(command)
        self.stopped = command.mode == "stop_cont"

    def recv(self, buffer, metadata, timeout):
        assert buffer.dtype == np.complex64 and buffer.ndim == 2
        metadata.error_code = self.code
        metadata.has_time_spec = True
        metadata.time_spec = TimeSpec(4, 0.2)
        count = 0 if self.stopped else self.count
        buffer[0, :count] = np.arange(count)
        return count


class FakeTX:
    def __init__(self):
        self.sent = []
        self.results = []
        self.events = []
        self.completion = TXCode.burst_ack
        self.completed_eobs = []

    def get_max_num_samps(self):
        return 3

    def send(self, samples, metadata, timeout):
        self.sent.append(
            (
                samples.copy(),
                metadata.start_of_burst,
                metadata.end_of_burst,
                metadata.has_time_spec,
                metadata.time_spec.to_ticks(1e9),
            )
        )
        accepted = (self.results.pop(0) if self.results else samples.size) if samples.size else 0
        # Match UHD fragmentation: EOB is emitted only when the final
        # fragment of this request is accepted, never on a partial return.
        if metadata.end_of_burst and accepted == samples.size:
            self.events.append(self.completion)
            self.completed_eobs.append(len(self.sent) - 1)
        return accepted

    def recv_async_msg(self, metadata, timeout):
        if not self.events:
            return False
        metadata.event_code = self.events.pop(0)
        return True


class FakeDevice:
    def __init__(self):
        self.rx = FakeRX()
        self.tx = FakeTX()
        self.values = {}
        self.calls = []
        self.coerced_rate = None
        self.time = TimeSpec(1)

    def __getattr__(self, name):
        # Mirror UHD's direction-specific methods, ranges and readbacks.
        parts = name.split("_", 2)
        if len(parts) != 3 or parts[1] not in ("rx", "tx"):
            raise AttributeError(name)
        action, direction, setting = parts
        key = (direction, setting)
        if action == "set":

            def setter(value, channel):
                self.calls.append((name, value, channel))
                self.values[key] = value

            return setter
        if setting == "num_channels":
            return lambda: 1
        ranges = {
            "rates": (100_000, 10e6),
            "bandwidth_range": (200_000, 5e6),
            "freq_range": (70e6, 6e9),
            "gain_range": (0, 70),
        }
        if setting in ranges:
            return lambda channel: Limits(*ranges[setting])
        if setting == "antennas":
            return lambda channel: ["TX/RX", "RX2"] if direction == "rx" else ["TX/RX"]
        if setting == "antenna":
            return lambda channel: self.values.get(key, "TX/RX")
        if setting == "rate" and self.coerced_rate:
            return lambda channel: self.coerced_rate
        return lambda channel: self.values[key]

    def get_usrp_rx_info(self, channel):
        return {"mboard_id": "B205mini", "mboard_serial": "fake"}

    def get_rx_stream(self, args):
        assert args.cpu == "fc32" and args.wire == "sc16" and args.channels == [0]
        return self.rx

    def get_tx_stream(self, args):
        return self.tx

    def get_time_now(self):
        return self.time

    def set_time_now(self, value):
        self.time = value


@pytest.fixture
def uhd(monkeypatch):
    device = FakeDevice()
    api = SimpleNamespace(
        usrp=SimpleNamespace(
            MultiUSRP=lambda selector: device,
            StreamArgs=lambda cpu, wire: SimpleNamespace(cpu=cpu, wire=wire),
        ),
        types=SimpleNamespace(
            DeviceAddr=lambda args: SimpleNamespace(
                to_string=lambda: ",".join(f"{k}={v}" for k, v in args.items())
            ),
            TimeSpec=TimeSpec,
            TuneRequest=lambda freq: freq,
            RXMetadata=Metadata,
            TXMetadata=Metadata,
            TXAsyncMetadata=Metadata,
            RXMetadataErrorCode=RXCode,
            TXMetadataEventCode=TXCode,
            StreamMode=SimpleNamespace(
                start_cont="start_cont", stop_cont="stop_cont", num_done="num_done"
            ),
            StreamCMD=lambda mode: SimpleNamespace(mode=mode),
        ),
    )
    monkeypatch.setitem(sys.modules, "uhd", api)
    return device, api


def test_configuration_readbacks_tuning_and_release(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig(rx_antenna="RX2", tx_antenna="TX/RX"), (915e6,))
    assert radio.actual_settings["rx_rate"] == 1e6
    assert radio.actual_settings["rx_antenna"] == "RX2"
    radio.tune(925e6)
    assert radio.actual_settings["tx_frequency"] == 925e6
    radio.close()
    radio.close()
    assert device.rx.stopped
    assert radio.device is radio.rx_stream is radio.tx_stream is None


@pytest.mark.parametrize(
    "change, frequencies, message",
    [
        ({"channel": 2}, (915e6,), "channel"),
        ({"tx_gain": 100}, (915e6,), "gain"),
        ({}, (8e9,), "frequency"),
        ({"sample_rate": 20e6}, (915e6,), "sample rate"),
        ({"bandwidth": 100}, (915e6,), "bandwidth"),
        ({"tx_antenna": "RX2"}, (915e6,), "antenna"),
    ],
)
def test_capabilities_reject_unsupported_settings(uhd, change, frequencies, message):
    with pytest.raises(ValueError, match=message):
        validate_device_capabilities(uhd[0], replace(SDRConfig(), **change), frequencies)


def test_coerced_sample_rate_rejected_before_streaming(uhd):
    uhd[0].coerced_rate = 2e6
    with pytest.raises(ValueError, match="coerced"):
        UHDRadio(SDRConfig())
    assert not uhd[0].rx.commands


def test_open_retries_and_direct_device_selection(uhd, monkeypatch):
    device, api = uhd
    selectors = []

    def make(selector):
        selectors.append(selector)
        if len(selectors) < 3:
            raise RuntimeError("busy")
        return device

    api.usrp.MultiUSRP = make
    monkeypatch.setattr(time, "sleep", lambda _: None)
    assert make_uhd_device(api, {"type": "b200", "serial": "123"}, attempts=3) is device
    assert selectors == ["type=b200,serial=123"] * 3
    with pytest.raises(ValueError, match="driver selector"):
        make_uhd_device(api, {"driver": "uhd"})


def test_partial_transfers_preserve_all_samples_and_single_burst(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    device.tx.results = [2, 1, 2]
    radio.transmit(np.arange(5, dtype=np.complex64))
    sent = device.tx.sent
    assert [len(row[0][0]) for row in sent] == [3, 3, 2]
    assert [row[1] for row in sent] == [True, False, False]
    assert [row[2] for row in sent] == [False, True, True]
    accepted = np.concatenate([sent[i][0][0, :n] for i, n in enumerate([2, 1, 2])])
    assert np.array_equal(accepted, np.arange(5))
    assert radio.rx_active
    radio.close()


@pytest.mark.parametrize(
    "event, counter",
    [
        (TXCode.underflow, "tx_underflows"),
        (TXCode.underflow_in_packet, "tx_underflows"),
        (TXCode.time_error, "timed_transmit_late"),
        (TXCode.seq_error, "tx_stream_errors"),
    ],
)
def test_async_errors_are_reported_and_next_burst_recovers(uhd, event, counter):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    device.tx.completion = event
    with pytest.raises(OSError, match="asynchronous"):
        radio.transmit(np.ones(5))
    assert radio.receive_diagnostics()[counter] == 1
    device.tx.completion = TXCode.burst_ack
    radio.transmit(np.ones(5))
    radio.close()


def test_send_timeout_closes_burst(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    device.tx.results = [0]
    with pytest.raises(OSError, match="incomplete"):
        radio.transmit(np.ones(5))
    assert device.tx.sent[-1][2]
    assert radio.receive_diagnostics()["tx_timeouts"] == 1
    radio.close()


def test_timed_transmission_marks_only_first_transfer(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    target = radio.transmit_with_lead(np.ones(5), 10_000_000)
    assert target == 1_010_000_000
    assert [row[3] for row in device.tx.sent] == [True, False]
    assert device.tx.sent[0][4] == target
    assert radio.receive_diagnostics()["timed_transmissions"] == 1
    with pytest.raises(OSError, match="late"):
        radio.transmit_at(np.ones(5), 1)
    radio.close()


def test_receive_timestamp_and_buffer_ownership(uhd):
    radio = UHDRadio(SDRConfig())
    block = radio.receive_timed(8, 1234)
    assert np.array_equal(block.samples, np.arange(3))
    assert block.time_ns == 4_200_000_000
    next_block = radio.receive_timed(8)
    next_block.samples[:] = 20
    assert np.array_equal(block.samples, np.arange(3))
    radio.close()


@pytest.mark.parametrize(
    "code,counter", [(RXCode.timeout, "rx_timeouts"), (RXCode.overflow, "rx_overflows")]
)
def test_recoverable_receive_errors(uhd, code, counter):
    radio = UHDRadio(SDRConfig())
    uhd[0].rx.code = code
    assert radio.receive().size == 0
    assert radio.receive_diagnostics()[counter] == 1
    uhd[0].rx.code = RXCode.none
    assert radio.receive().size == 3
    radio.close()


@pytest.mark.parametrize(
    "code", [RXCode.late, RXCode.broken_chain, RXCode.alignment, RXCode.bad_packet]
)
def test_fatal_receive_errors_are_surfaced(uhd, code):
    radio = UHDRadio(SDRConfig())
    uhd[0].rx.code = code
    with pytest.raises(OSError, match="RX error"):
        radio.receive()
    assert radio.receive_diagnostics()["rx_stream_errors"] == 1
    radio.close()


def test_clock_reset_and_timed_receive_preserve_nanoseconds(uhd):
    radio = UHDRadio(SDRConfig())
    epoch = 1_700_000_000_123_456_789
    radio.set_hardware_time(epoch)
    assert radio.hardware_time() == epoch
    radio.schedule_receive(epoch + 10_000_000, 1000)
    command = uhd[0].rx.commands[-1]
    assert command.mode == "num_done" and not command.stream_now
    assert command.num_samps == 1000
    assert command.time_spec.to_ticks(1e9) == epoch + 10_000_000
    radio.close()


class SourceRadio:
    def __init__(self):
        self.next_sample = 0
        self.transmitted = []
        self.tuned = []
        self.closed = False

    def receive(self, count=4096, timeout_us=50_000):
        time.sleep(0.001)
        self.next_sample += 1
        return np.full(4, self.next_sample, np.complex64)

    def transmit(self, samples):
        self.transmitted.append(samples.copy())

    def tune(self, frequency_hz):
        self.tuned.append(frequency_hz)

    def close(self):
        self.closed = True


def test_buffered_radio_drains_in_background_and_serializes_transmit():
    source = SourceRadio()
    radio = BufferedRadio(source, queue_blocks=8, read_timeout_us=1_000)
    try:
        samples = radio.receive(timeout_us=100_000)
        assert samples.size == 4
        radio.transmit(np.ones(3, np.complex64))
        radio.tune(915e6)
        assert len(source.transmitted) == 1
        assert source.tuned == [915e6]
    finally:
        radio.close()
    assert source.closed


def test_buffered_radio_counts_dropped_blocks_when_consumer_stalls():
    source = SourceRadio()
    radio = BufferedRadio(source, queue_blocks=1, read_timeout_us=1_000)
    try:
        time.sleep(0.02)
        assert radio.receive_diagnostics()["rx_buffer_drops"] > 0
        assert radio.receive().size == 4
    finally:
        radio.close()


def test_buffered_uhd_keeps_receiving_during_transmit():
    import threading

    class DuplexSource(SourceRadio):
        concurrent_rx_tx = True

        def transmit(self, samples):
            before = self.next_sample
            deadline = time.monotonic() + 1
            while self.next_sample == before and time.monotonic() < deadline:
                threading.Event().wait(0.001)
            assert self.next_sample > before, "RX was blocked by TX"

    source = DuplexSource()
    radio = BufferedRadio(source)
    try:
        radio.transmit(np.ones(5))
    finally:
        radio.close()


def test_burst_completion_timeout_is_bounded(uhd, monkeypatch):
    radio = UHDRadio(SDRConfig())
    device, _ = uhd
    device.tx.recv_async_msg = lambda metadata, timeout: False
    ticks = iter([0, 0.1, 0.2, 2])
    monkeypatch.setattr("packet_radio.hardware.uhd.time.monotonic", lambda: next(ticks, 2))
    with pytest.raises(OSError, match="completion timed out"):
        radio.transmit(np.ones(2))
    assert radio.receive_diagnostics()["tx_timeouts"] == 1
    radio.close()


def test_tune_stops_old_frequency_samples_then_restarts(uhd):
    radio = UHDRadio(SDRConfig())
    radio.tune(915e6)
    assert [c.mode for c in uhd[0].rx.commands] == ["start_cont", "stop_cont", "start_cont"]
    radio.close()


def test_uhd_clock_read_does_not_wait_for_receive_lock():
    source = SourceRadio()
    source.concurrent_rx_tx = True
    source.hardware_time = lambda: 123456
    radio = BufferedRadio(source)
    try:
        # The lock is held by the caller: acquiring it again would deadlock.
        # A worker with a deadline makes a regression fail without hanging pytest.
        import threading

        result = []
        with radio._io_lock:
            reader = threading.Thread(target=lambda: result.append(radio.hardware_time()))
            reader.start()
            reader.join(0.2)
            finished = not reader.is_alive()
        reader.join(1)
        assert finished
        assert result == [123456]
    finally:
        radio.close()


def test_optional_idle_flush_preserves_waveform_then_sends_zeros_before_eob(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig(), tx_idle_samples=64)
    samples = np.array([1 + 1j, -1j, 1, -1, 1j], np.complex64)
    try:
        radio.transmit(samples)
        actual = np.concatenate([row[0][0] for row in device.tx.sent])
        np.testing.assert_array_equal(actual, np.concatenate((samples, np.zeros(64))))
        assert sum(row[1] for row in device.tx.sent) == 1
        assert sum(row[2] for row in device.tx.sent) == 1
        assert radio.actual_settings["tx_idle_samples"] == 64
        with pytest.raises(ValueError):
            radio.transmit(np.empty(0))
    finally:
        radio.close()


@pytest.mark.parametrize("count", [-1, 4097, True, 1.5])
def test_invalid_idle_flush_rejected_before_uhd(count, monkeypatch):
    def unexpected():
        raise AssertionError("UHD must not be loaded")

    monkeypatch.setattr("packet_radio.hardware.uhd.load_uhd", unexpected)
    with pytest.raises(ValueError):
        UHDRadio(SDRConfig(), tx_idle_samples=count)


@pytest.fixture
def dual_uhd(uhd):
    device, api = uhd
    opened = []
    resets = []
    streams = [FakeRX(), FakeRX()]
    transmitters = [FakeTX(), FakeTX()]
    api.usrp.MultiUSRP = lambda selector: opened.append(selector) or device
    device.get_num_mboards = lambda: 1
    device.get_rx_num_channels = lambda: 2
    device.get_tx_num_channels = lambda: 2
    device.get_rx_stream = lambda args: streams[args.channels[0]]
    device.get_tx_stream = lambda args: transmitters[args.channels[0]]
    device.set_time_now = lambda value: resets.append(value)
    return device, opened, resets, streams


def test_shared_channels_have_one_owner_clock_and_independent_lifetime(dual_uhd):
    from packet_radio.hardware.uhd import UHDDeviceSession

    device, opened, resets, streams = dual_uhd
    config = SDRConfig(device={"type": "x300"})
    owner = UHDDeviceSession(config)
    a = owner.open(config, (915e6,))
    b = owner.open(replace(config, channel=1), (915e6,))
    assert len(opened) == len(resets) == 1
    assert a.device is b.device is device
    assert a._control_lock is b._control_lock
    with pytest.raises(ValueError, match="already owned"):
        owner.open(config)
    with pytest.raises(RuntimeError, match="cannot be reset"):
        b.set_hardware_time(0)
    with pytest.raises(RuntimeError, match="close all"):
        owner.close()
    a.close()
    a.close()
    assert streams[0].stopped and not streams[1].stopped
    assert len(b.receive()) == 3
    b.transmit(np.ones(4, np.complex64))
    b.close()
    owner.close()
    owner.close()
    with pytest.raises(RuntimeError, match="closed"):
        owner.open(config)


def test_shared_open_failure_releases_channel_and_checks_selector(dual_uhd):
    from packet_radio.hardware.uhd import UHDDeviceSession

    config = SDRConfig(device={"type": "x300"})
    owner = UHDDeviceSession(config)
    with pytest.raises(ValueError, match="same UHD device"):
        owner.open(replace(config, device={"type": "b200"}))
    with pytest.raises(ValueError, match="frequency"):
        owner.open(config, (1.0,))
    assert not owner.channels
    a = owner.open(config)
    a.close()
    owner.close()


def test_fixed_analog_bandwidth_records_actual_and_requested(uhd):
    device, _ = uhd
    for direction in ("rx", "tx"):
        setattr(device, f"get_{direction}_bandwidth_range", lambda channel: Limits(160e6, 160e6))
    radio = UHDRadio(SDRConfig(bandwidth=1e6), (915e6,))
    for direction in ("rx", "tx"):
        assert radio.actual_settings[f"{direction}_bandwidth"] == 160e6
        assert radio.actual_settings[f"{direction}_requested_bandwidth"] == 1e6
    radio.close()
    with pytest.raises(ValueError, match="bandwidth"):
        UHDRadio(SDRConfig(bandwidth=200e6))


def test_app_keeps_shared_device_epoch():
    from packet_radio.app import RadioApp

    app = SimpleNamespace(
        sdr=SimpleNamespace(radio=SimpleNamespace(hardware_epoch_initialized=True))
    )
    RadioApp._initialize_hardware_epoch(app)


def test_shared_session_rejects_multiple_motherboards(dual_uhd):
    from packet_radio.hardware.uhd import UHDDeviceSession

    device, _, resets, _ = dual_uhd
    device.get_num_mboards = lambda: 2
    with pytest.raises(ValueError, match="exactly one motherboard"):
        UHDDeviceSession(SDRConfig())
    assert not resets


def test_timing_diagnostics_preserve_partial_sends_and_separate_late_sources(uhd):
    from packet_radio.diagnostics import TimingDiagnostics

    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.timing = TimingDiagnostics(True, clock=lambda: 900_000.0)
    try:
        device.tx.results = [2, 1, 2]
        radio.transmit_with_lead(np.arange(5), 10_000_000)
        timing = radio.timing.snapshot()
        assert timing["uhd_send_calls_per_attempt"]["mean"] == 3
        assert timing["uhd_send_requested_samples"]["mean"] == pytest.approx(8 / 3)
        assert timing["uhd_send_accepted_samples"]["mean"] == pytest.approx(5 / 3)
        assert timing["first_send_hardware_lead_ms"]["mean"] == 10
        assert timing["uhd_completion_wait_ms"]["count"] == 1
        with pytest.raises(OSError, match="late"):
            radio.transmit_at(np.ones(5), 1)
        assert radio.receive_diagnostics()["host_tx_late_rejections"] == 1
        assert radio.receive_diagnostics()["uhd_tx_late_events"] == 0
        device.tx.completion = TXCode.time_error
        with pytest.raises(OSError, match="asynchronous"):
            radio.transmit(np.ones(5))
        counters = radio.receive_diagnostics()
        assert counters["host_tx_late_rejections"] == 1
        assert counters["uhd_tx_late_events"] == 1
        assert counters["timed_transmit_late"] == 2
    finally:
        radio.close()


@pytest.mark.parametrize("size", [1, 48, 49, 200_000])
def test_batched_send_preserves_samples_idle_and_metadata(uhd, size):
    device, _ = uhd
    radio = UHDRadio(SDRConfig(), tx_idle_samples=7)
    radio.tx_batch_samples = 16 * radio.tx_mtu
    # Strided input also exercises ownership and contiguous conversion.
    source = np.arange(size * 2, dtype=np.complex64)[::2]
    expected = np.concatenate((source.copy(), np.zeros(7, np.complex64)))
    original_send = device.tx.send
    timeouts = []

    def send(samples, metadata, timeout):
        timeouts.append(timeout)
        result = original_send(samples, metadata, timeout)
        source[:] = -999  # Caller mutation must not alter subsequent batches.
        return result

    device.tx.send = send
    try:
        target = radio.transmit_with_lead(source, 10_000_000)
        rows = device.tx.sent
        np.testing.assert_array_equal(np.concatenate([r[0][0] for r in rows]), expected)
        assert len(rows) == (len(expected) + 47) // 48
        assert all(r[0].size <= 48 for r in rows)
        assert [r[1] for r in rows] == [True] + [False] * (len(rows) - 1)
        assert [r[3] for r in rows] == [True] + [False] * (len(rows) - 1)
        assert rows[0][4] == target
        assert sum(r[2] for r in rows) == 1 and rows[-1][2]
        if size >= 48:
            assert timeouts[0] < 0.1  # Per-fragment allowance, not per-batch.
    finally:
        radio.close()


def test_batched_partial_send_rechecks_deadline_and_closes_burst(uhd, monkeypatch):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.tx_batch_samples = 16 * radio.tx_mtu
    now = [0.0]
    monkeypatch.setattr("packet_radio.hardware.uhd.time.monotonic", lambda: now[0])
    original_send = device.tx.send

    def send(samples, metadata, timeout):
        result = original_send(samples, metadata, timeout)
        now[0] += 2.0
        return result

    device.tx.results = [1]
    device.tx.send = send
    try:
        with pytest.raises(OSError, match="deadline"):
            radio.transmit(np.ones(100))
        assert len(device.tx.sent) == 2
        assert device.tx.sent[-1][2]
        assert radio.receive_diagnostics()["tx_timeouts"] == 1
    finally:
        radio.close()


@pytest.mark.parametrize("accepted", [-1, 49])
def test_invalid_batched_acceptance_closes_burst_and_reports_error(uhd, accepted):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.tx_batch_samples = 16 * radio.tx_mtu
    device.tx.results = [accepted]
    try:
        with pytest.raises(OSError, match="incomplete"):
            radio.transmit(np.ones(100))
        assert device.tx.sent[-1][2]
        assert radio.receive_diagnostics()["tx_stream_errors"] == 1
    finally:
        radio.close()


def test_batched_partial_transfers_across_boundaries_do_not_skip_samples(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.tx_batch_samples = 16 * radio.tx_mtu
    device.tx.results = [48, 5, 47]
    try:
        radio.transmit(np.arange(100, dtype=np.complex64))
        rows = device.tx.sent
        assert [r[0].size for r in rows] == [48, 48, 47]
        actual = np.concatenate([r[0][0, :n] for r, n in zip(rows, [48, 5, 47])])
        np.testing.assert_array_equal(actual, np.arange(100))
        assert [r[1] for r in rows] == [True, False, False]
        assert rows[-1][2]
    finally:
        radio.close()


def test_batched_send_exception_still_ends_burst(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.tx_batch_samples = 16 * radio.tx_mtu
    original_send = device.tx.send

    def send(samples, metadata, timeout):
        if samples.size:
            raise OSError("transport disconnected")
        return original_send(samples, metadata, timeout)

    device.tx.send = send
    try:
        with pytest.raises(OSError, match="transport disconnected"):
            radio.transmit(np.ones(100))
        assert len(device.tx.sent) == 1 and device.tx.sent[0][2]
    finally:
        radio.close()


def test_relative_tx_target_is_chosen_after_stale_event_drain_with_one_time_read(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    original = device.tx.recv_async_msg
    reads = []

    def receive(metadata, timeout):
        device.time = TimeSpec(2)
        return original(metadata, timeout)

    def read_time():
        reads.append(True)
        return device.time

    device.tx.recv_async_msg = receive
    device.get_time_now = read_time
    try:
        target = radio.transmit_with_lead(np.ones(5), 10_000_000)
        assert target == 2_010_000_000
        assert device.tx.sent[0][4] == target
        assert len(reads) == 1
    finally:
        radio.close()


def test_absolute_target_expiring_during_preparation_never_reaches_streamer(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())

    def receive(metadata, timeout):
        device.time = TimeSpec(2)
        return False

    device.tx.recv_async_msg = receive
    try:
        with pytest.raises(OSError, match="late"):
            radio.transmit_at(np.ones(5), 1_010_000_000)
        assert device.tx.sent == []
        assert radio.receive_diagnostics()["host_tx_late_rejections"] == 1
    finally:
        radio.close()


def test_fragment_error_train_is_drained_once_and_does_not_poison_next_burst(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    device.tx.events = [TXCode.time_error] * 46 + [TXCode.burst_ack]
    try:
        with pytest.raises(OSError, match="asynchronous"):
            radio.transmit(np.ones(5))
        assert radio.receive_diagnostics()["uhd_tx_late_events"] == 46
        assert device.tx.sent == []
        assert device.tx.events == []
        radio.transmit(np.ones(5))
        assert device.tx.sent[-1][2]
    finally:
        radio.close()


def test_failed_attempt_diagnostics_correlate_lead_with_uhd_error(uhd):
    from packet_radio.diagnostics import TimingDiagnostics

    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.timing = TimingDiagnostics(True)
    device.tx.completion = TXCode.time_error
    try:
        with pytest.raises(OSError, match="asynchronous"):
            radio.transmit_with_lead(np.ones(5), 10_000_000)
        metrics = radio.timing.snapshot()
        assert metrics["uhd_late_attempt_first_send_lead_ms"]["mean"] == 10
        assert metrics["uhd_late_attempt_ms"]["count"] == 1
    finally:
        radio.close()


def test_insufficient_lead_defers_without_submitting_or_counting_uhd_late(uhd):
    from packet_radio.hardware.uhd import TransmitDeadlineError

    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.minimum_tx_lead_ns = 2_000_000
    try:
        with pytest.raises(TransmitDeadlineError, match="insufficient"):
            radio.transmit_at(np.ones(5), 1_001_000_000)
        assert device.tx.sent == []
        counts = radio.receive_diagnostics()
        assert counts["tx_lead_rejections"] == 1
        assert counts["timed_transmit_late"] == 0
        assert counts["uhd_tx_late_events"] == 0
        radio.transmit_at(np.ones(5), 1_003_000_000)
        assert device.tx.sent[0][4] == 1_003_000_000
    finally:
        radio.close()


def test_unbounded_stale_error_source_is_bounded_before_any_new_iq(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    calls = []

    def receive(metadata, timeout):
        calls.append(timeout)
        metadata.event_code = TXCode.time_error
        return True

    device.tx.recv_async_msg = receive
    try:
        with pytest.raises(OSError, match="queue did not drain"):
            radio.transmit(np.ones(5))
        assert len(calls) == 256
        assert radio.receive_diagnostics()["uhd_tx_late_events"] == 256
        assert device.tx.sent == []
    finally:
        radio.close()


def test_buffered_receive_measures_host_queue_age_without_changing_rf_timestamp():
    import queue

    from packet_radio.diagnostics import TimingDiagnostics
    from packet_radio.hardware.uhd import SampleBlock

    radio = BufferedRadio.__new__(BufferedRadio)
    radio._error = None
    radio._queue = queue.Queue()
    radio.timing = TimingDiagnostics(True, clock=lambda: 10.025)
    block = SampleBlock(np.ones(4, np.complex64), 987654321, 10.0)
    radio._queue.put(block)
    assert radio.receive_timed() is block
    stats = radio.timing.snapshot()
    assert stats["rx_buffer_queue_ms"]["mean"] == pytest.approx(25)
    assert stats["rx_buffer_queue_blocks"]["max"] == 0
    # Unknown host age must not be inferred from the unrelated RF timestamp.
    radio._queue.put(SampleBlock(np.ones(4, np.complex64), 123))
    radio.receive_timed()
    assert radio.timing.snapshot()["rx_buffer_queue_ms"]["count"] == 1


@pytest.mark.parametrize("packets", [1, 16, 256])
def test_runtime_tx_batch_setting_preserves_fragmented_data_and_metadata(uhd, packets):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    try:
        radio.set_tx_batch_packets(packets)
        assert radio.tx_batch_samples == packets * radio.tx_mtu
        assert radio.actual_settings["tx_batch_packets"] == packets
        assert radio.actual_settings["tx_batch_samples"] == packets * radio.tx_mtu
        samples = np.arange(101, dtype=np.complex64)
        radio.transmit_with_lead(samples, 10_000_000)
        chunks = [entry[0] for entry in device.tx.sent if entry[0].size]
        np.testing.assert_array_equal(np.concatenate(chunks, axis=1).ravel(), samples)
        assert device.tx.sent[0][1:4] == (True, len(chunks) == 1, True)
        assert all(not entry[1] and not entry[3] for entry in device.tx.sent[1:])
        assert device.tx.sent[-1][0].size > 0
        assert device.tx.sent[-1][2] is True
    finally:
        radio.close()


@pytest.mark.parametrize("packets", [0, 257, True, 1.5])
def test_invalid_runtime_tx_batch_setting_leaves_stream_unchanged(uhd, packets):
    radio = UHDRadio(SDRConfig())
    try:
        original = radio.tx_batch_samples
        with pytest.raises(ValueError):
            radio.set_tx_batch_packets(packets)
        assert radio.tx_batch_samples == original
    finally:
        radio.close()


def test_partial_final_batch_emits_eob_only_after_all_samples(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.set_tx_batch_packets(16)
    device.tx.results = [48, 6, 6]
    try:
        radio.transmit_with_lead(np.arange(60), 10_000_000)
        rows = device.tx.sent
        assert [r[0].size for r in rows] == [48, 12, 6]
        assert [r[2] for r in rows] == [False, True, True]
        assert device.tx.completed_eobs == [2]
        accepted = np.concatenate([r[0][0, :n] for r, n in zip(rows, [48, 6, 6])])
        np.testing.assert_array_equal(accepted, np.arange(60))
        assert [r[3] for r in rows] == [True, False, False]
    finally:
        radio.close()


def test_final_batch_timeout_uses_untimed_empty_cleanup(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.set_tx_batch_packets(16)
    device.tx.results = [48, 0]
    try:
        with pytest.raises(OSError, match="incomplete"):
            radio.transmit_with_lead(np.arange(60), 10_000_000)
        assert [r[0].size for r in device.tx.sent] == [48, 12, 0]
        assert device.tx.completed_eobs == [2]
        assert device.tx.sent[-1][1:4] == (False, True, False)
    finally:
        radio.close()


def test_final_iq_is_already_ended_before_python_resumes(uhd):
    device, _ = uhd
    radio = UHDRadio(SDRConfig())
    radio.set_tx_batch_packets(16)
    original = device.tx.send
    accepted_total = 0

    def send(samples, metadata, timeout):
        nonlocal accepted_total
        accepted = original(samples, metadata, timeout)
        accepted_total += accepted
        # Model hardware draining the complete 60-sample waveform while
        # the calling Python thread is unable to resume after send().
        if accepted_total == 60 and not device.tx.completed_eobs:
            device.tx.events.append(TXCode.underflow)
        return accepted

    device.tx.send = send
    try:
        radio.transmit_with_lead(np.arange(60), 10_000_000)
        assert device.tx.completed_eobs == [1]
        assert len(device.tx.sent) == 2
        assert radio.receive_diagnostics()["tx_underflows"] == 0
    finally:
        radio.close()
