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
        if metadata.end_of_burst:
            self.events.append(self.completion)
            return 0
        return self.results.pop(0) if self.results else samples.size

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
    assert [len(row[0][0]) for row in sent] == [3, 3, 2, 0]
    assert [row[1] for row in sent] == [True, False, False, False]
    assert [row[2] for row in sent] == [False, False, False, True]
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
    assert [row[3] for row in device.tx.sent] == [True, False, False]
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
