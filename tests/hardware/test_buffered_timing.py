"""Timed operations through the real receive-buffer worker, with a fake device."""

import threading

import numpy as np
import pytest

from packet_radio.hardware.uhd import BufferedRadio


class TimedDevice:
    def __init__(self, concurrent):
        self.concurrent_rx_tx = concurrent
        self.calls = []
        self.received = threading.Event()
        self.closed = False
        self.failure = None

    def receive(self, count, timeout_us):
        self.received.set()
        threading.Event().wait(0.001)
        return np.ones(4, dtype=np.complex64)

    def hardware_time(self):
        return 1_000_000_000

    def operation(self, *args):
        self.calls.append(args)
        if self.failure:
            raise self.failure

    def set_hardware_time(self, target):
        self.operation("clock", target)

    def transmit_at(self, samples, target):
        self.operation("tx", samples.copy(), target)

    def transmit_with_lead(self, samples, lead):
        target = self.hardware_time() + lead
        self.transmit_at(samples, target)
        return target

    def schedule_receive(self, target, count):
        self.operation("rx", target, count)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("concurrent", [False, True])
@pytest.mark.parametrize("operation", ["clock", "tx", "lead", "rx"])
@pytest.mark.parametrize("fail", [False, True])
def test_timed_operations_preserve_arguments_and_resume_receive(concurrent, operation, fail):
    device = TimedDevice(concurrent)
    radio = BufferedRadio(device)
    samples = np.array([1 + 2j, 3 - 4j], dtype=np.complex64)
    target = 1_010_000_000
    try:
        assert device.received.wait(1)
        device.failure = OSError("timed command failed") if fail else None

        def command():
            if operation == "clock":
                return radio.set_hardware_time(target)
            if operation == "tx":
                return radio.transmit_at(samples, target)
            if operation == "lead":
                return radio.transmit_with_lead(samples, 10_000_000)
            return radio.schedule_receive(target, 128)

        if fail:
            with pytest.raises(OSError, match="timed command failed"):
                command()
        else:
            result = command()
            if operation == "lead":
                assert result == target
        assert not radio._paused.is_set()
        call = device.calls[-1]
        if operation in ("tx", "lead"):
            assert call[0] == "tx" and call[2] == target
            np.testing.assert_array_equal(call[1], samples)
        else:
            assert call == (("clock", target) if operation == "clock" else ("rx", target, 128))
        device.received.clear()
        assert device.received.wait(1), "receive worker did not resume"
        assert radio.hardware_time() == 1_000_000_000
    finally:
        radio.close()
    assert device.closed and not radio._thread.is_alive()


def test_receive_worker_failure_reaches_consumer_and_device_is_closed():
    device = TimedDevice(False)

    def failed_receive(*args):
        raise OSError("USB disconnected")

    device.receive = failed_receive
    radio = BufferedRadio(device)
    try:
        radio._thread.join(1)
        assert not radio._thread.is_alive()
        with pytest.raises(RuntimeError) as error:
            radio.receive_timed()
        assert isinstance(error.value.__cause__, OSError)
        assert str(error.value.__cause__) == "USB disconnected"
    finally:
        radio.close()
    assert device.closed
