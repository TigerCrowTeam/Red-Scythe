"""Ownership, startup failures and shutdown of application resources."""

import signal
from dataclasses import replace
from unittest.mock import Mock

import pytest

from packet_radio import app as application
from packet_radio.app import RadioApp
from packet_radio.endpoint import MemoryEndpoint
from packet_radio.simulation import SimulatedChannel
from tests.integration.test_end_to_end import node


@pytest.fixture
def app():
    config = node("leader", 1)
    radio = SimulatedChannel().radio()
    result = RadioApp(config, sdr=radio, endpoint=MemoryEndpoint())
    try:
        yield result
    finally:
        result.close()


@pytest.mark.parametrize("fail_step", [False, True])
def test_run_releases_resources_on_stop_and_failure(app, monkeypatch, fail_step):
    status = Mock()
    monkeypatch.setattr(application, "StatusService", Mock(return_value=status))
    endpoint, radio = app.endpoint, app.sdr
    endpoint.close = Mock(wraps=endpoint.close)
    radio.close = Mock(wraps=radio.close)

    def step():
        if fail_step:
            raise OSError("receive failed")
        app.stop()

    monkeypatch.setattr(app, "step", step)
    if fail_step:
        with pytest.raises(OSError, match="receive failed"):
            app.run()
    else:
        app.run()
        assert not app.running.is_set()
    status.start.assert_called_once()
    status.close.assert_called_once()
    endpoint.close.assert_called_once()
    radio.close.assert_called_once()
    app.close()
    endpoint.close.assert_called_once()
    radio.close.assert_called_once()


def test_cleanup_continues_after_resource_close_fails(app, caplog):
    failing = Mock()
    failing.close.side_effect = OSError("cleanup failed")
    remaining = Mock()
    app.status_service = failing
    app.recorder = remaining
    app.close()
    remaining.close.assert_called_once()
    assert "failed to close status_service" in caplog.text
    assert app.statistics.snapshot()["shutdown_errors"] == 1
    assert not app.statistics.snapshot()["radio_ready"]
    assert app.sdr is None and app.endpoint is None


def test_endpoint_startup_failure_closes_acquired_radio(monkeypatch):
    radio = Mock()
    monkeypatch.setattr(application, "UDPEndpoint", Mock(side_effect=OSError("port occupied")))
    with pytest.raises(OSError, match="port occupied"):
        RadioApp(node("leader", 1), sdr=radio)
    radio.close.assert_called_once()


def test_decoder_startup_failure_closes_endpoint_and_radio(monkeypatch):
    radio, endpoint = Mock(), Mock()
    config = node("leader", 1)
    config = replace(config, sdr=replace(config.sdr, decoder_worker_enabled=True))
    monkeypatch.setattr(application, "DecoderWorker", Mock(side_effect=RuntimeError("worker")))
    with pytest.raises(RuntimeError, match="worker"):
        RadioApp(config, sdr=radio, endpoint=endpoint)
    radio.close.assert_called_once()
    endpoint.close.assert_called_once()


def test_recording_failure_does_not_disable_radio(monkeypatch):
    config = node("leader", 1)
    config = replace(config, recording=replace(config.recording, directory="unused"))
    monkeypatch.setattr(application, "CaptureRecorder", Mock(side_effect=OSError("disk full")))
    app = RadioApp(config, sdr=SimulatedChannel().radio(), endpoint=MemoryEndpoint())
    try:
        assert app.recorder is None
        assert app.statistics.snapshot()["recording_failures"] == 1
        assert app._queue_local_payload(b"still running")
    finally:
        app.close()


def test_endpoint_input_failure_is_counted_and_loop_continues(app, monkeypatch):
    monkeypatch.setattr(app.endpoint, "receive", Mock(side_effect=OSError("local input")))
    transmit = Mock()
    monkeypatch.setattr(app, "_transmit_one", transmit)
    app.step()
    transmit.assert_called_once()
    assert app.statistics.snapshot()["endpoint_input_errors"] == 1


def test_hardware_epoch_waits_for_target_and_sets_nanoseconds(app, monkeypatch):
    now = [100.1]
    app.wall_clock = lambda: now[0]
    app.config = replace(app.config, link=replace(app.config.link, hardware_epoch_lead_seconds=0.2))
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(application.time, "sleep", sleep)
    app.sdr.set_hardware_time = Mock()
    app.sdr.hardware_time = Mock(return_value=101_000_000_001)
    app._initialize_hardware_epoch()
    assert sleeps and all(0 < duration <= 0.05 for duration in sleeps)
    app.sdr.set_hardware_time.assert_called_once_with(101_000_000_000)


@pytest.mark.parametrize("timing", [False, True])
def test_runner_installs_both_shutdown_handlers(monkeypatch, timing):
    instance = Mock()
    factory = Mock(return_value=instance)
    handler = Mock()
    monkeypatch.setattr(application, "RadioApp", factory)
    monkeypatch.setattr(application.signal, "signal", handler)
    config = node("leader", 1)
    application.run(config, timing_diagnostics=timing)
    factory.assert_called_once_with(config, timing_diagnostics=timing)
    assert handler.call_args_list[0].args == (signal.SIGINT, instance.stop)
    assert handler.call_args_list[1].args == (signal.SIGTERM, instance.stop)
    instance.run.assert_called_once()


def test_runtime_batch_setting_reaches_backend_before_application_runs():
    config = node("leader", 1)
    config = replace(config, link=replace(config.link, hardware_tx_batch_packets=16))
    radio = SimulatedChannel().radio()
    radio.set_tx_batch_packets = Mock()
    result = RadioApp(config, sdr=radio, endpoint=MemoryEndpoint())
    try:
        radio.set_tx_batch_packets.assert_called_once_with(16)
        assert result.statistics.snapshot()["packets_transmitted"] == 0
    finally:
        result.close()
