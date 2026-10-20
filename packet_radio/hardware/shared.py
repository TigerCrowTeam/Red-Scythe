"""Manage two application endpoints on separate channels of one USRP.

SharedRadios validates peer settings and owns the board and buffered channel
streams. run_pair drives both applications in one process, stops the peer on
failure, and releases streams before releasing their shared device.
"""

from __future__ import annotations

import signal
import threading
from contextlib import ExitStack

from packet_radio.config import RadioConfig, validate_network
from packet_radio.hardware.uhd import BufferedRadio, UHDDeviceSession
from packet_radio.protocol.registry import resolve_processing


class SharedRadios:
    def __init__(self, configs: list[RadioConfig]):
        if len(configs) != 2:
            raise ValueError("shared radio operation requires two configurations")
        validate_network({str(i): c for i, c in enumerate(configs)})
        a, b = configs
        if a.sdr.device != b.sdr.device or a.sdr.channel == b.sdr.channel:
            raise ValueError("shared radios require the same device and distinct channels")
        if a.sdr.device.get("type") != "x300":
            raise ValueError("shared radio operation currently requires UHD type x300")
        self.stack = ExitStack()
        self.radios: list[BufferedRadio] = []
        try:
            session = UHDDeviceSession(a.sdr)
            self.stack.callback(session.close)
            for config in configs:
                raw = session.open(
                    config.sdr,
                    config.frequency.frequencies_hz,
                    tx_idle_samples=resolve_processing(config).tx_idle_samples(config.sdr),
                )
                try:
                    radio = BufferedRadio(raw)
                except BaseException:
                    raw.close()
                    raise
                self.stack.callback(radio.close)
                self.radios.append(radio)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.stack.close()


def run_pair(configs: list[RadioConfig], *, timing_diagnostics: bool = False) -> None:
    from packet_radio.app import RadioApp

    a, b = configs
    if (a.endpoint.bind_host, a.endpoint.bind_port) == (b.endpoint.bind_host, b.endpoint.bind_port):
        raise ValueError("shared peers need distinct UDP bind addresses")
    if a.status.http_enabled and b.status.http_enabled and a.status.http_port == b.status.http_port:
        raise ValueError("shared peers need distinct status HTTP ports")
    apps: list[RadioApp] = []
    errors: list[BaseException] = []
    threads: list[threading.Thread] = []
    stopped = threading.Event()

    def stop(*_args: object) -> None:
        stopped.set()
        for app in apps:
            app.stop()

    def worker(app: RadioApp) -> None:
        try:
            app.run()
        except BaseException as exc:
            errors.append(exc)
        finally:
            stop()

    with ExitStack() as stack:
        radios = SharedRadios(configs)
        stack.callback(radios.close)
        for config, radio in zip(configs, radios.radios):
            app = RadioApp(config, sdr=radio, timing_diagnostics=timing_diagnostics)
            apps.append(app)
            stack.callback(app.close)
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous = signal.signal(sig, stop)
            stack.callback(signal.signal, sig, previous)
        try:
            for app in apps:
                if stopped.is_set():
                    break
                thread = threading.Thread(target=worker, args=(app,), name="packet-radio-peer")
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            stop()
            for thread in threads:
                thread.join()
        if errors:
            raise RuntimeError("shared radio peer failed") from errors[0]
        if any(app.statistics.snapshot()["shutdown_errors"] for app in apps):
            raise RuntimeError("shared radio shutdown failed")
