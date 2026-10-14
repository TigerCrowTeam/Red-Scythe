"""Coordinate one radio node from local payload input to RF delivery.

RadioApp owns the endpoint, modem, SDR, reliable packet queues and optional
workers, recording and status services. Its step method receives and validates
packets, polls local payloads, schedules transmissions and maintains frequency
and clock synchronization. run drives this loop; stop and close handle shutdown.
The module-level run function connects operating-system signals to the app.
"""

from __future__ import annotations

import logging
import math
import signal
import struct
import threading
import time

import numpy as np

from packet_radio import reception, transmission
from packet_radio.clock_sync import HardwareClockSynchronizer
from packet_radio.config import RadioConfig, configuration_snapshot, validate_config
from packet_radio.endpoint import PayloadEndpoint, UDPEndpoint
from packet_radio.hardware.uhd import BufferedRadio, UHDRadio
from packet_radio.modems.decoder import DecoderWorker
from packet_radio.modems.registry import make_modem
from packet_radio.protocol.authentication import (
    load_private_key,
    load_public_keys,
    public_key_fingerprint,
)
from packet_radio.protocol.framing import (
    BROADCAST_ID,
    PacketKind,
    RadioPacket,
    has_valid_crc,
)
from packet_radio.protocol.reliability import QueuedPacket, ReliableLink
from packet_radio.protocol.replay_protection import ReplayProtector
from packet_radio.recording import CaptureRecorder
from packet_radio.scheduler import FrequencyScheduler
from packet_radio.statistics import LinkStatistics
from packet_radio.status import StatusService

LOG = logging.getLogger(__name__)


class RadioApp:
    def __init__(
        self,
        config: RadioConfig,
        *,
        sdr=None,
        endpoint: PayloadEndpoint | None = None,
        wall_clock=time.time,
        monotonic_clock=time.monotonic,
    ):
        self.status_service: StatusService | None = None
        self.recorder: CaptureRecorder | None = None
        self.decoder_worker: DecoderWorker | None = None
        try:
            self._initialize(
                config,
                sdr=sdr,
                endpoint=endpoint,
                wall_clock=wall_clock,
                monotonic_clock=monotonic_clock,
            )
            self.statistics.set_gauge("radio_ready", True)
        except BaseException:
            self.close()
            raise

    def _initialize(
        self,
        config: RadioConfig,
        *,
        sdr=None,
        endpoint: PayloadEndpoint | None = None,
        wall_clock=time.time,
        monotonic_clock=time.monotonic,
    ):
        validate_config(config)
        self.config = config
        self.signing_key = (
            load_private_key(config.authentication.private_key_file)
            if config.authentication.private_key_file is not None
            else None
        )
        self.verification_keys = (
            load_public_keys(config.authentication.public_key_files)
            if self.signing_key is not None
            else None
        )
        if self.signing_key is None:
            LOG.warning("radio packet authentication is disabled")
        else:
            assert self.verification_keys is not None
            for node_id, keys in self.verification_keys.items():
                LOG.info(
                    "trusted node %d identity fingerprints: %s",
                    node_id,
                    ", ".join(public_key_fingerprint(key) for key in keys),
                )
        self.session_id = transmission.new_session_id() or 1
        self.replay_protector = ReplayProtector(
            config.authentication.replay_window,
            config.authentication.replay_state_file,
        )
        self.last_sync_leader_time: float | None = None
        self.last_sync_received_at: float | None = None
        self.wall_clock = wall_clock
        self.monotonic_clock = monotonic_clock
        self.scheduler = FrequencyScheduler(config.frequency, clock=wall_clock)
        self.modem = make_modem(config.sdr, candidate_validator=has_valid_crc)
        self.rx_modem = (
            make_modem(config.sdr, candidate_validator=has_valid_crc)
            if config.sdr.decoder_worker_enabled
            else self.modem
        )
        self.sdr = sdr
        if self.sdr is None:
            self.sdr = BufferedRadio(UHDRadio(config.sdr, config.frequency.frequencies_hz))
        self.hardware_clock_sync = HardwareClockSynchronizer()
        if config.link.hardware_clock_sync:
            self._initialize_hardware_epoch()
        self.endpoint = endpoint if endpoint is not None else UDPEndpoint(config.endpoint)
        self.statistics = LinkStatistics(monotonic_clock, config.frequency.sync_loss_seconds)
        backend = getattr(self.sdr, "radio", self.sdr)
        actual = getattr(backend, "actual_settings", {})
        self.statistics.set_radio_info(
            configuration_snapshot(config),
            {"actual_settings": actual if isinstance(actual, dict) else {}},
        )
        self.statistics.set_gauge("queue_capacity", config.link.queue_size)
        self.statistics.set_gauge("modulation", config.sdr.modulation)
        self.statistics.set_gauge("configured_bit_rate_bps", config.sdr.bit_rate_bps)
        self.statistics.set_gauge(
            "configured_center_frequency_hz", config.frequency.frequencies_hz[0]
        )
        self.statistics.set_gauge("sample_rate_hz", config.sdr.sample_rate)
        self.link = ReliableLink(
            config.node_id,
            config.link,
            clock=monotonic_clock,
            statistics=self.statistics,
            session_id=self.session_id,
            encoded_size=self._on_air_size,
            airtime=self._packet_airtime,
        )
        self._prepared_burst: tuple[QueuedPacket, bytes, np.ndarray] | None = None
        self.sequence = 0
        self.running = threading.Event()
        self.running.set()
        self.last_rx = self.monotonic_clock()
        self.current_frequency: float | None = None
        self.channel_power = 0.0
        self._receive_diagnostics: dict[str, int] = {}
        self.recovering = False
        self.scan_frequencies: list[float] = []
        self.scan_index = 0
        self.next_scan_at = 0.0
        self.next_sync_at = 0.0
        # Monotonic end time of the most recent RF burst.  TDD admission uses
        # this to enforce a quiet guard before another burst or retry.
        self._last_tx_end = 0.0
        self._tdd_slot_index: int | None = None
        self._tdd_bursts_sent = 0
        self._last_tdd_opportunity_id: int | None = None
        if config.recording.directory is not None:
            try:
                self.recorder = CaptureRecorder(
                    config.recording.directory,
                    sample_rate=config.sdr.sample_rate,
                    center_frequency_hz=config.frequency.frequencies_hz[0],
                    samples_per_symbol=config.sdr.samples_per_symbol,
                    rrc_rolloff=config.sdr.rrc_rolloff,
                    frequency_deviation_hz=config.sdr.frequency_deviation_hz,
                    gaussian_bt=config.sdr.gaussian_bt,
                    gaussian_span_symbols=config.sdr.gaussian_span_symbols,
                    rrc_span_symbols=config.sdr.rrc_span_symbols,
                    modulation=config.sdr.modulation,
                    interleaver_depth=config.sdr.fec_interleaver_depth,
                    bit_repetitions=config.sdr.bit_repetitions,
                    rs_parity_bytes=config.sdr.rs_parity_bytes,
                    record_iq=config.recording.iq,
                    record_frames=config.recording.frames,
                    authenticated=self.signing_key is not None,
                    max_file_bytes=config.recording.max_file_bytes,
                    max_sessions=config.recording.max_sessions,
                    min_free_bytes=config.recording.min_free_bytes,
                    create_session=True,
                    on_disable=lambda _reason: self.statistics.increment("recording_failures"),
                )
                LOG.info("recording capture session in %s", self.recorder.directory)
            except (OSError, ValueError) as exc:
                LOG.error("capture recording unavailable; continuing without it: %s", exc)
                self.statistics.increment("recording_failures")
        if config.sdr.decoder_worker_enabled:
            self.decoder_worker = DecoderWorker(
                self.sdr,
                self.rx_modem,
                sample_rate=config.sdr.sample_rate,
                output_batches=config.sdr.decoder_output_batches,
                record_iq=(self.recorder.record_iq if self.recorder is not None else None),
            )

    def _queue(
        self, payload: bytes, destination: int, *, kind: PacketKind = PacketKind.DATA
    ) -> bool:
        packet = RadioPacket(
            self.config.node_id, destination, self.sequence, payload, kind, self.session_id
        )
        return self.link.enqueue(packet)

    def _transmit_one(self) -> None:
        return transmission.transmit_one(self)

    def _tdd_transmit_allowed(self) -> bool:
        return transmission.tdd_transmit_allowed(self)

    def _tdd_burst_fits(
        self,
        sample_count: int,
        timed_lead_seconds: float | None = None,
        target_leader_ns: int | None = None,
    ) -> bool:
        return transmission.tdd_burst_fits(self, sample_count, timed_lead_seconds, target_leader_ns)

    def _next_hardware_tdd_opportunity(self) -> tuple[int, int, int, int] | None:
        return transmission.next_hardware_tdd_opportunity(self)

    def _tdd_time(self) -> float:
        return transmission.tdd_time(self)

    def _initialize_hardware_epoch(self) -> None:
        """Set device time to a shared future UTC whole-second epoch."""
        target_ns = int(
            math.ceil(self.wall_clock() + self.config.link.hardware_epoch_lead_seconds) * 1e9
        )
        while True:
            remaining = target_ns / 1e9 - self.wall_clock()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.05))
        self.sdr.set_hardware_time(target_ns)
        measured = self.sdr.hardware_time()
        LOG.info(
            "hardware clock epoch set to %d ns (readback error %+d ns)",
            target_ns,
            measured - target_ns,
        )

    def _retune(self) -> None:
        if self.recovering:
            return
        state = self.scheduler.state()
        wanted = state.frequency_hz
        if wanted != self.current_frequency:
            previous = self.current_frequency
            self.sdr.tune(wanted)
            flush = getattr(self.sdr, "flush_receive", None)
            if flush is not None:
                flush()
            self.current_frequency = wanted
            self.statistics.set_gauge("active_frequency_hz", wanted)
            if previous is not None:
                self.statistics.increment("frequency_hops")
                self._last_tdd_opportunity_id = None
                LOG.info("frequency hop %.3f MHz -> %.3f MHz", previous / 1e6, wanted / 1e6)

    def _packet_airtime(self, packet: RadioPacket) -> float:
        size = packet.encoded_size(authenticated=self.signing_key is not None)
        return self.modem.sample_count(size) / self.config.sdr.sample_rate

    def _on_air_size(self, packet: RadioPacket) -> int:
        framed = packet.encoded_size(authenticated=self.signing_key is not None)
        return self.modem.frame_size(framed)

    def _recover_if_needed(self) -> None:
        if self.config.timing_role == "leader":
            return
        now = self.monotonic_clock()
        if not self.recovering:
            if now - self.last_rx < self.config.frequency.sync_loss_seconds:
                return
            LOG.warning("radio synchronization lost; beginning channel scan")
            self.statistics.increment("synchronization_losses")
            self.statistics.set_gauge("synchronized", False)
            self.recovering = True
            self.scan_frequencies = self.scheduler.scan_order()
            self.scan_index = 0
            self.next_scan_at = 0.0
        if now >= self.next_scan_at:
            frequency = self.scan_frequencies[self.scan_index]
            self.scan_index = (self.scan_index + 1) % len(self.scan_frequencies)
            self.sdr.tune(frequency)
            self.current_frequency = frequency
            self.statistics.set_gauge("active_frequency_hz", frequency)
            self.next_scan_at = now + self.config.frequency.reacquire_dwell_seconds

    def _receive_radio(self) -> bool:
        return reception.receive_radio(self)

    def _sync_is_fresh(self, leader_time: float, received_at: float) -> bool:
        return reception.sync_is_fresh(self, leader_time, received_at)

    def _queue_clock_sync(self) -> None:
        now = self.monotonic_clock()
        if self.config.timing_role == "leader" and now >= self.next_sync_at:
            self._queue(struct.pack("!d", self.wall_clock()), BROADCAST_ID, kind=PacketKind.SYNC)
            self.next_sync_at = now + 2.0

    def _queue_local_payload(self, payload: bytes) -> bool:
        self.statistics.increment("payload_packets_received")
        self.statistics.increment("payload_bytes_received", len(payload))
        if not 1 <= len(payload) <= self.config.endpoint.max_payload_bytes:
            self.statistics.increment("invalid_payloads")
            LOG.warning(
                "rejected local payload length %d (allowed 1..%d)",
                len(payload),
                self.config.endpoint.max_payload_bytes,
            )
            return False
        return self._queue(payload, self.config.peer_id)

    def step(self) -> None:
        """Run one application cycle, with bounded nonblocking endpoint polling."""
        self._retune()
        self._receive_radio()
        for _ in range(self.config.endpoint.drain_max_packets):
            try:
                payload = self.endpoint.receive()
            except OSError as exc:
                self.statistics.increment("endpoint_input_errors")
                LOG.warning("local payload input failed: %s", exc)
                break
            if payload is None:
                break
            self._queue_local_payload(payload)
        self._queue_clock_sync()
        self._transmit_one()
        self._recover_if_needed()
        state = self.scheduler.state()
        self.statistics.set_gauge("frequency_slot", state.slot)
        self.statistics.set_gauge("transmit_allowed", state.transmit_allowed)
        if self.status_service is not None:
            self.status_service.maybe_report()

    def run(self) -> None:
        LOG.info("starting %s node %d", self.config.timing_role, self.config.node_id)
        try:
            self.status_service = StatusService(
                self.statistics,
                self.config.status,
                self.config.timing_role,
                self.config.node_id,
                self.monotonic_clock,
            )
            self.status_service.start()
            while self.running.is_set():
                self.step()
        finally:
            self.close()
            LOG.info("stopped %s node %d", self.config.timing_role, self.config.node_id)

    def close(self) -> None:
        statistics = getattr(self, "statistics", None)
        if statistics is not None:
            statistics.set_gauge("radio_ready", False)
        for name in (
            "status_service",
            "decoder_worker",
            "endpoint",
            "sdr",
            "recorder",
        ):
            resource = getattr(self, name, None)
            if resource is not None:
                setattr(self, name, None)
                try:
                    resource.close()
                except Exception:
                    if statistics is not None:
                        statistics.increment("shutdown_errors")
                    LOG.exception("failed to close %s", name)

    def stop(self, *_args: object) -> None:
        self.running.clear()


def run(config: RadioConfig) -> None:
    app = RadioApp(config)
    signal.signal(signal.SIGINT, app.stop)
    signal.signal(signal.SIGTERM, app.stop)
    app.run()
