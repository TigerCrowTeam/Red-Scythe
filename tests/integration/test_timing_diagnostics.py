"""Check bounded diagnostics and delivery with instrumentation enabled."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from packet_radio.diagnostics import TimingDiagnostics
from packet_radio.statistics import LinkStatistics
from tests.integration.test_end_to_end import network


def test_disabled_diagnostics_never_read_clock_or_retain_observations():
    def forbidden():
        raise AssertionError("disabled diagnostics read clock")

    timing = TimingDiagnostics(clock=forbidden)
    timing.finish("work_ms", timing.start())
    timing.observe("samples", 3)
    assert timing.snapshot() == {}
    assert "timing_diagnostics" not in LinkStatistics().snapshot()


def test_summaries_are_thread_safe_and_rolling_storage_is_bounded():
    timing = TimingDiagnostics(True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: timing.observe("work_ms", 2), range(1000)))
    metric = timing.snapshot()["work_ms"]
    assert metric == dict(count=1000, mean=2, min=2, max=2, recent_count=256, recent_p95=2)
    for value in range(256):
        timing.observe("work_ms", value)
    metric = timing.snapshot()["work_ms"]
    assert metric["count"] == 1256
    assert metric["recent_p95"] == 243
    assert metric["min"] == 0
    assert metric["max"] == 255


def test_durations_and_signed_hardware_lead_are_distinct():
    now = [10.0]
    timing = TimingDiagnostics(True, lambda: now[0])
    started = timing.start()
    now[0] += 0.125
    timing.finish("encode_ms", started)
    timing.observe("first_send_hardware_lead_ms", -2)
    assert timing.snapshot()["encode_ms"]["mean"] == 125
    assert timing.snapshot()["first_send_hardware_lead_ms"]["min"] == -2
    with pytest.raises(ValueError, match="finite"):
        timing.observe("invalid", float("nan"))


@pytest.mark.parametrize("enabled", [False, True])
def test_diagnostics_preserve_simulated_bidirectional_delivery(enabled):
    net = network()
    try:
        for peer in net.nodes.values():
            peer.app.statistics.timing.enabled = enabled
        payloads = [b"first", bytes(range(64)), b"last"]
        for payload in payloads:
            net.inject("a", payload)
            net.inject("b", payload[::-1])
        net.step(200)
        assert net.received("b") == payloads
        assert net.received("a") == [p[::-1] for p in payloads]
        for peer in net.nodes.values():
            snapshot = peer.app.statistics.snapshot()
            if enabled:
                timing = snapshot["timing_diagnostics"]
                for name in (
                    "packet_selection_ms",
                    "packet_encode_ms",
                    "packet_modulate_ms",
                    "rx_decode_ms",
                    "enqueue_to_ack_ms",
                    "frame_handling_to_delivery_ms",
                ):
                    assert timing[name]["count"] > 0
                assert snapshot["oldest_pending_packet_age_ms"] == 0
            else:
                assert "timing_diagnostics" not in snapshot
    finally:
        net.close()


def test_work_separates_thread_cpu_from_elapsed_time_and_records_exceptions(monkeypatch):
    ticks = iter([10.0, 10.020])
    cpu = iter([2.0, 2.003])
    monkeypatch.setattr("packet_radio.diagnostics.time.thread_time", lambda: next(cpu))
    timing = TimingDiagnostics(True, clock=lambda: next(ticks))
    with pytest.raises(RuntimeError):
        with timing.work("test"):
            raise RuntimeError("expected")
    assert timing.snapshot()["test_wall_ms"]["mean"] == pytest.approx(20)
    assert timing.snapshot()["test_cpu_ms"]["mean"] == pytest.approx(3)


def test_lock_timing_releases_on_exception_and_preserves_reentrancy():
    import threading

    ticks = iter([1.0, 1.002, 1.007])
    timing = TimingDiagnostics(True, clock=lambda: next(ticks))
    lock = threading.RLock()
    with pytest.raises(RuntimeError):
        with timing.lock("control", lock):
            with lock:
                raise RuntimeError("expected")
    assert timing.snapshot()["control_wait_ms"]["mean"] == pytest.approx(2)
    assert timing.snapshot()["control_hold_ms"]["mean"] == pytest.approx(5)
    # A different thread must be able to acquire it after the exception.
    with ThreadPoolExecutor(max_workers=1) as pool:

        def acquire():
            assert lock.acquire(timeout=0.1)
            lock.release()

        pool.submit(acquire).result()


def test_disabled_work_and_lock_instrumentation_do_not_read_clocks(monkeypatch):
    import threading

    def forbidden():
        raise AssertionError("disabled instrumentation read a clock")

    monkeypatch.setattr("packet_radio.diagnostics.time.thread_time", forbidden)
    timing = TimingDiagnostics(False, clock=forbidden)
    with timing.work("work"), timing.lock("lock", threading.Lock()):
        pass
    assert timing.snapshot() == {}
