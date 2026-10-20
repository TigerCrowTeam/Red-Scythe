import time
from collections import deque

import numpy as np

from packet_radio.hardware.uhd import SampleBlock
from packet_radio.modems.decoder import DecoderWorker


class Radio:
    def __init__(self, blocks):
        self.blocks = deque(blocks)

    def receive_timed(self, timeout_us=5_000):
        if self.blocks:
            return self.blocks.popleft()
        time.sleep(timeout_us / 1_000_000)
        return SampleBlock(np.empty(0, dtype=np.complex64))


class Modem:
    fec_corrected_bits = 0
    fec_uncorrectable_frames = 0
    last_fec_failure_score = None
    last_fec_failure_corrected_score = None

    def feed_timed(self, samples, time_ns):
        return [(bytes([len(samples)]), time_ns)]


def wait_for_batch(worker):
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        batch = worker.receive(timeout=0.01)
        if batch is not None:
            return batch
    raise AssertionError("decoder worker did not produce a batch")


def test_decoder_worker_preserves_timestamps_and_reports_work():
    worker = DecoderWorker(
        Radio([SampleBlock(np.ones(4, np.complex64), 1_000)]),
        Modem(),
        sample_rate=1_000_000,
    )
    try:
        batch = wait_for_batch(worker)
        diagnostics = worker.diagnostics()
    finally:
        worker.close()

    assert batch.frames == [(b"\x04", 1_000)]
    assert diagnostics["decoder_iq_blocks"] == 1
    assert diagnostics["decoder_iq_samples"] == 4
    assert diagnostics["decoder_frames"] == 1


def test_decoder_worker_counts_hardware_timestamp_discontinuity():
    worker = DecoderWorker(
        Radio(
            [
                SampleBlock(np.ones(4, np.complex64), 1_000),
                SampleBlock(np.ones(4, np.complex64), 20_000),
            ]
        ),
        Modem(),
        sample_rate=1_000_000,
    )
    try:
        wait_for_batch(worker)
        wait_for_batch(worker)
        diagnostics = worker.diagnostics()
    finally:
        worker.close()

    assert diagnostics["decoder_timestamp_discontinuities"] == 1


def test_retune_discards_old_output_resets_modem_and_resumes_after_guard():
    from unittest.mock import Mock

    radio = Radio([SampleBlock(np.ones(4, np.complex64), 1000)])
    modem = Modem()
    modem.reset = Mock()
    worker = DecoderWorker(radio, modem, sample_rate=1e6)
    try:
        deadline = time.monotonic() + 1
        while worker.diagnostics()["decoder_queue_depth"] == 0:
            assert time.monotonic() < deadline
            time.sleep(0.001)
        action = Mock()
        worker.retune(action, 0.05)
        action.assert_called_once()
        modem.reset.assert_called_once()
        assert worker.receive() is None
        radio.blocks.append(SampleBlock(np.ones(5, np.complex64), 10000))
        deadline = time.monotonic() + 1
        while radio.blocks:
            assert time.monotonic() < deadline
            time.sleep(0.001)
        assert worker.receive() is None
        time.sleep(0.06)
        radio.blocks.append(SampleBlock(np.ones(6, np.complex64), 20000))
        assert wait_for_batch(worker).frames == [(b"\x06", 20000)]
        assert worker.diagnostics()["decoder_timestamp_discontinuities"] == 0
    finally:
        worker.close()


def test_retune_waits_for_inflight_decode_before_flushing():
    import threading

    entered, release, tuned = threading.Event(), threading.Event(), threading.Event()

    class BlockingModem(Modem):
        def feed_timed(self, samples, time_ns):
            entered.set()
            assert release.wait(1)
            return super().feed_timed(samples, time_ns)

        def reset(self):
            pass

    worker = DecoderWorker(
        Radio([SampleBlock(np.ones(4, np.complex64), 1000)]), BlockingModem(), sample_rate=1e6
    )
    thread = threading.Thread(target=lambda: worker.retune(tuned.set, 0))
    try:
        assert entered.wait(1)
        thread.start()
        assert not tuned.wait(0.02)
        release.set()
        thread.join(1)
        assert not thread.is_alive()
        assert tuned.is_set()
        assert worker.receive() is None
    finally:
        release.set()
        worker.close()


def test_frame_recognition_uses_host_clock_without_replacing_rf_timestamp():
    from packet_radio.diagnostics import TimingDiagnostics

    timing = TimingDiagnostics(True, clock=lambda: 123.0)
    worker = DecoderWorker(
        Radio([SampleBlock(np.ones(4, np.complex64), 1_000)]),
        Modem(),
        sample_rate=1_000_000,
        timing=timing,
    )
    try:
        batch = wait_for_batch(worker)
        assert batch.recognized_at_monotonic == 123.0
        assert batch.frames == [(b"\x04", 1_000)]
        assert timing.snapshot()["rx_decode_ms"]["count"] == 1
    finally:
        worker.close()


def test_incremental_processing_window_matches_exact_sort_through_eviction():
    worker = DecoderWorker(Radio([]), Modem(), sample_rate=1_000_000)
    worker.close()
    history = deque(maxlen=2048)
    # Duplicates, an expiring extreme and multiple complete window turnovers.
    for i in range(5000):
        value = 9999.0 if i == 0 else float((i * 7) % 31)
        worker._record_processing_ms(value)
        history.append(value)
        if i % 317 == 0 or i == 4999:
            expected = sorted(history)
            stats = worker.diagnostics()
            assert stats["decoder_processing_p95_ms"] == expected[int(0.95 * (len(expected) - 1))]
            assert stats["decoder_processing_max_ms"] == expected[-1]
            assert worker._processing_sorted == expected
            assert len(worker._processing_sorted) <= 2048
    assert worker.diagnostics()["decoder_processing_max_ms"] == 30
