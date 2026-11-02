"""Run ACK-free simple frames with explicit direction, input and timing policies.

SimpleProfile connects the configurable frame codec to RadioApp. SimpleLink
owns a bounded FIFO with no retry state; SimpleBehavior delivers opaque payloads
without generic-v5 control traffic, identities or authentication. Preframed input
is validated before admission and retains its exact length/payload/CRC bytes.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, cast

from packet_radio.modems.burst import BurstProcessing
from packet_radio.protocol.interfaces import QueuedPacket
from packet_radio.protocol.registry import ProcessingProfile
from packet_radio.protocol.simple import FrameSettings, SimpleFrameCodec, make_frame_modem

if TYPE_CHECKING:
    from packet_radio.app import RadioApp
    from packet_radio.config import RadioConfig, SDRConfig


@dataclass(frozen=True)
class SimpleLinkSettings:
    operation: Literal["transmit-only", "receive-only", "bidirectional"] = "bidirectional"
    input_kind: Literal["payload", "frame"] = "payload"
    minimum_gap_seconds: float = 0.002
    minimum_interval_seconds: float = 0.0
    turnaround_seconds: float = 0.002

    def __post_init__(self):
        if self.operation not in ("transmit-only", "receive-only", "bidirectional"):
            raise ValueError("unsupported operation")
        if self.input_kind not in ("payload", "frame"):
            raise ValueError("input_kind must be payload or frame")
        for name in ("minimum_gap_seconds", "minimum_interval_seconds", "turnaround_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ValueError(f"{name} must be numeric")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be nonnegative and finite")
        if self.minimum_gap_seconds < 0.002:
            raise ValueError("simple profile requires at least 2 ms between bursts")


@dataclass(frozen=True)
class EncodedFrame:
    """Validated body bytes, excluding acquisition prefix and FEC."""

    wire: bytes


class SimpleInputCodec(SimpleFrameCodec):
    def encode(self, packet, signing_key=None) -> bytes:
        if isinstance(packet, EncodedFrame):
            if signing_key is not None:
                raise ValueError("simple frames do not support signatures")
            self.decode(packet.wire)
            return packet.wire
        return super().encode(packet, signing_key)

    def encoded_size(self, packet, *, authenticated=False) -> int:
        if isinstance(packet, EncodedFrame):
            return len(self.encode(packet, True if authenticated else None))
        return super().encoded_size(packet, authenticated=authenticated)


class SimpleLink:
    """FIFO counts the selected packet against capacity until completion or drop."""

    def __init__(self, app: RadioApp, settings: SimpleLinkSettings):
        self.app = app
        self.settings = settings
        self.queue: deque[QueuedPacket] = deque()
        self.active: QueuedPacket | None = None
        self.next_allowed = 0.0
        self.started_at = 0.0

    def _update(self):
        pending = list(self.queue) + ([self.active] if self.active is not None else [])
        now = self.app.monotonic_clock()
        oldest = min((q.enqueued_at for q in pending), default=now)
        self.app.statistics.set_gauge("oldest_pending_packet_age_ms", max(0, (now - oldest) * 1000))
        self.app.statistics.set_gauge("queue_depth", len(pending))
        self.app.statistics.set_gauge(
            "queue_airtime_seconds", sum(q.airtime_seconds for q in pending)
        )

    def _expire_data(self):
        limit = self.app.config.link.queue_max_age_seconds
        if limit is not None:
            now = self.app.monotonic_clock()
            before = len(self.queue)
            self.queue = deque(q for q in self.queue if now - q.enqueued_at < limit)
            expired = before - len(self.queue)
            if expired:
                self.app.statistics.increment("queue_expired", expired)
        self._update()

    def enqueue(self, packet) -> bool:
        self._expire_data()
        airtime = self.app._packet_airtime(packet)
        pending = list(self.queue) + ([self.active] if self.active is not None else [])
        limit = self.app.config.link.queue_airtime_seconds
        if len(pending) >= self.app.config.link.queue_size or (
            limit is not None and sum(q.airtime_seconds for q in pending) + airtime > limit
        ):
            self.app.statistics.increment("queue_drops")
            return False
        self.queue.append(
            QueuedPacket(packet, enqueued_at=self.app.monotonic_clock(), airtime_seconds=airtime)
        )
        self.app.statistics.increment("packets_enqueued")
        self._update()
        return True

    def next_packet(self, channel_clear: bool, tx_allowed: bool) -> QueuedPacket | None:
        self._expire_data()
        if not tx_allowed or not channel_clear or self.app.monotonic_clock() < self.next_allowed:
            return None
        if self.active is not None or not self.queue:
            return None
        self.active = self.queue.popleft()
        self.started_at = self.app.monotonic_clock()
        self._update()
        return self.active

    def requeue(self, queued: QueuedPacket) -> None:
        # Admission can be deferred before an SDR submission, never after a failure.
        if queued is self.active:
            self.active = None
            self.queue.appendleft(queued)
        self._update()

    def finish(self, queued: QueuedPacket) -> None:
        lead = (
            self.app.config.link.hardware_timed_tx_lead_seconds
            if self.app.config.link.hardware_timed_io
            else 0.0
        )
        end = max(self.app.monotonic_clock(), self.started_at + lead + queued.airtime_seconds)
        self.next_allowed = max(
            end + self.settings.minimum_gap_seconds,
            end + self.settings.minimum_interval_seconds,
        )
        self.active = None
        self._update()

    def transmitted(self, queued, *, encoded_bytes=None, samples=0, airtime_seconds=0.0):
        stats = self.app.statistics
        stats.increment("packets_transmitted")
        stats.increment(
            "bytes_transmitted",
            self.app._on_air_size(queued.packet) if encoded_bytes is None else encoded_bytes,
        )
        stats.increment("samples_transmitted", samples)
        stats.increment("airtime_seconds_transmitted", airtime_seconds)
        self.finish(queued)


class SimpleBehavior:
    requires_peer = False

    def __init__(self, settings: SimpleLinkSettings):
        self.settings = settings
        self.operation: str = settings.operation
        self.input_kind: str = settings.input_kind
        self.receive_enabled = settings.operation != "transmit-only"

    def initialize(self, app: RadioApp) -> None:
        # No replay state, generic session identifiers or SYNC freshness state.
        pass

    def make_link(self, app: RadioApp) -> SimpleLink:
        return SimpleLink(app, self.settings)

    def input_limit(self, app: RadioApp) -> int:
        maximum = app.config.endpoint.max_payload_bytes
        return app.codec.payload_size(maximum) if self.input_kind == "frame" else maximum

    def queue(self, app: RadioApp, payload: bytes, destination: int, kind=None) -> bool:
        if self.operation == "receive-only" or kind is not None:
            app.statistics.increment("mode_rejections")
            return False
        packet: bytes | EncodedFrame = payload
        try:
            if self.input_kind == "frame":
                decoded = app.codec.decode(payload)
                if len(decoded) > app.config.endpoint.max_payload_bytes:
                    raise ValueError("payload exceeds endpoint limit")
                packet = EncodedFrame(bytes(payload))
            else:
                app.codec.encode(packet)
        except ValueError:
            app.statistics.increment("invalid_payloads")
            return False
        return cast(SimpleLink, app.link).enqueue(packet)

    def receive(self, app: RadioApp, decoded: list[tuple[bytes, int | None]]) -> bool:
        if not self.receive_enabled:
            return False
        received = False
        for wire, _ in decoded:
            frame_started = app.statistics.timing.start()
            try:
                payload = app.codec.decode(wire)
                if len(payload) > app.config.endpoint.max_payload_bytes:
                    raise ValueError("payload exceeds endpoint limit")
            except ValueError as exc:
                if app.recorder is not None:
                    app.recorder.record_frame(wire, str(exc))
                app.statistics.increment("invalid_packets")
                counter = app.codec.error_counter(exc)
                if counter is not None:
                    app.statistics.increment(counter)
                continue
            if app.recorder is not None:
                app.recorder.record_frame(wire)
            received = True
            app.last_rx = app.monotonic_clock()
            app.statistics.mark_activity("frame")
            app.statistics.increment("packets_received")
            app.statistics.increment("bytes_received", app.modem.frame_size(len(wire)))
            try:
                app.endpoint.send(payload)
                app.statistics.timing.finish("frame_handling_to_delivery_ms", frame_started)
            except OSError:
                app.statistics.increment("endpoint_output_errors")
            else:
                app.statistics.increment("payload_packets_delivered")
                app.statistics.increment("payload_bytes_delivered", len(payload))
        return received

    def prepare(self, app: RadioApp, queued: QueuedPacket, target_ns: int | None) -> int | None:
        return target_ns

    def cacheable(self, queued: QueuedPacket) -> bool:
        return True

    def channel_clear(self, app: RadioApp) -> bool:
        return True  # No carrier-sense backoff or implicit collision avoidance.

    def transmit_allowed(self, app: RadioApp) -> bool:
        if self.operation == "receive-only":
            return False
        link = cast(SimpleLink, app.link)
        return app.monotonic_clock() >= max(
            link.next_allowed,
            app.last_rx + self.settings.turnaround_seconds if self.receive_enabled else 0.0,
        )

    def transmit_failed(self, app: RadioApp, queued: QueuedPacket) -> None:
        app.statistics.increment("transmit_drops")
        app._prepared_burst = None
        cast(SimpleLink, app.link).finish(queued)

    def tick(self, app: RadioApp) -> None:
        pass

    def recover(self, app: RadioApp) -> None:
        pass

    def sync_is_fresh(self, app: RadioApp, leader_time: float, received_at: float) -> bool:
        return False  # This format has no synchronization packets.


class SimpleProfile(ProcessingProfile):
    """Explicit development selection; schema-1 YAML remains generic-v5 only."""

    name = "simple-frame-v1"
    frame: FrameSettings
    link: SimpleLinkSettings

    def __init__(
        self,
        frame: FrameSettings = FrameSettings(),
        link: SimpleLinkSettings = SimpleLinkSettings(),
    ):
        super().__init__(SimpleInputCodec(frame), SimpleBehavior(link))
        object.__setattr__(self, "frame", frame)
        object.__setattr__(self, "link", link)

    def tx_idle_samples(self, config: SDRConfig) -> int:
        # 32 us of zero-valued interburst idle at the frozen 2 Msps reference rate.
        return max(1, math.ceil(config.sample_rate * 32e-6))

    def describe(self) -> dict:
        frame = asdict(self.frame)
        frame["preamble"] = self.frame.preamble.hex()
        frame["sync"] = self.frame.sync.hex()
        return {"format": self.name, "frame": frame, "link": asdict(self.link)}

    def validate(self, config: RadioConfig) -> None:
        if config.sdr.rrc_rolloff and config.sdr.modulation not in ("bpsk", "qpsk"):
            raise ValueError("RRC shaping is supported only for BPSK/QPSK")
        if config.sdr.modulation != "gmsk" and (
            config.sdr.gaussian_bt is not None or config.sdr.gaussian_span_symbols is not None
        ):
            raise ValueError("Gaussian settings are supported only for GMSK")
        if config.sdr.bit_repetitions != 1:
            raise ValueError("simple profile requires bit_repetitions=1")
        if config.sdr.modulation != "2fsk" and self.frame.bit_order != "big":
            raise ValueError("little bit order is currently supported only for 2FSK")
        hopping = (
            config.schema_version == 2
            and config.emulation is not None
            and config.emulation["frequency"]["mode"] == "hopping"
        )
        if not hopping and (
            len(config.frequency.frequencies_hz) != 1 or config.frequency.guard_seconds
        ):
            raise ValueError("simple profile requires fixed frequency without hop guards")
        if hopping and config.recording.directory is not None and config.recording.iq:
            raise ValueError(
                "hopping IQ recording requires discontinuity metadata; use frame recording"
            )
        if config.link.hardware_clock_sync or config.link.hardware_slot_scheduling:
            raise ValueError("simple profile does not use peer clock synchronization")
        if config.link.tdd_period_seconds:
            raise ValueError("simple profile uses continuous receive and unscheduled TX, not TDD")
        if (
            config.authentication.private_key_file is not None
            or config.authentication.public_key_files
            or config.authentication.replay_state_file is not None
        ):
            raise ValueError("simple profile does not support authentication or replay state")
        if config.recording.directory is not None and config.schema_version != 2:
            raise ValueError("simple capture requires a resolved schema-2 profile")
        if (
            config.sdr.fec_interleaver_depth != self.frame.interleaver_depth
            or config.sdr.rs_parity_bytes != self.frame.rs_parity_bytes
        ):
            raise ValueError("SDR protection parameters must agree with frame settings")
        if self.tx_idle_samples(config.sdr) > 4096:
            raise ValueError("simple profile TX idle flush exceeds UHD limit")
        self.frame.check_size(config.endpoint.max_payload_bytes)

    def burst(self, config: SDRConfig) -> BurstProcessing:
        return self.make_modem(config).processing

    def make_modem(self, config: SDRConfig, *, clock=time.monotonic):
        if config.modulation != "2fsk":
            from packet_radio.modems.registry import make_modem
            from packet_radio.protocol.simple import FrameProtection, SimpleEnvelope

            protection = FrameProtection(self.frame)
            modem = make_modem(
                config,
                processing=BurstProcessing(protection, SimpleEnvelope(self.frame, protection)),
                candidate_validator=self.codec.candidate_valid,
            )
            modem.max_frame = self.codec.payload_size(
                self.frame.fixed_payload_bytes or self.frame.max_payload_bytes
            )
            from packet_radio.modems.fsk import FSK2Modem

            if isinstance(modem, FSK2Modem):
                modem.discard_invalid_candidates = True
            return modem
        return make_frame_modem(
            self.frame,
            sample_rate=config.sample_rate,
            bit_rate=config.bit_rate_bps,
            deviation=(
                config.frequency_deviation_hz
                if config.frequency_deviation_hz is not None
                else config.bit_rate_bps / 2
            ),
            clock=clock,
        )
