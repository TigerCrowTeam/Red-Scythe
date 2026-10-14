import struct

import pytest

from packet_radio.config import LinkConfig
from packet_radio.protocol.framing import PROTOCOL_VERSION, PacketKind, RadioPacket
from packet_radio.protocol.reliability import ReliableLink
from packet_radio.statistics import LinkStatistics
from tests.integration.test_end_to_end import network


class Clock:
    now = 10.0

    def __call__(self):
        return self.now


def test_statistics_snapshot_contains_counters_gauges_and_uptime():
    clock = Clock()
    statistics = LinkStatistics(clock)
    statistics.increment("packets_received", 2)
    statistics.increment("payload_packets_received", 3)
    statistics.set_gauge("active_frequency_hz", 915e6)
    clock.now = 12.5
    snapshot = statistics.snapshot()
    assert snapshot["packets_received"] == 2
    assert snapshot["payload_packets_received"] == 3
    assert snapshot["active_frequency_hz"] == 915e6
    assert snapshot["queue_capacity"] == 0
    assert snapshot["uptime_seconds"] == 2.5


def test_acknowledgement_counter_and_pending_gauge():
    statistics = LinkStatistics()
    link = ReliableLink(2, LinkConfig(), statistics=statistics)
    packet = RadioPacket(2, 1, 7, b"unicast")
    link.enqueue(packet)
    queued = link.next_packet(True, True)
    link.transmitted(queued)
    assert statistics.snapshot()["awaiting_acknowledgements"] == 1
    assert link.acknowledge(RadioPacket(1, 2, 7, struct.pack("!QI", 0, 7), PacketKind.ACK))
    snapshot = statistics.snapshot()
    assert snapshot["acknowledgements_received"] == 1
    assert snapshot["awaiting_acknowledgements"] == 0

    ack = RadioPacket(2, 1, 8, struct.pack("!QI", 0, 8), PacketKind.ACK)
    link.enqueue(ack)
    sent_ack = link.next_packet(True, True)
    link.transmitted(sent_ack)
    assert statistics.snapshot()["acknowledgements_sent"] == 1


def test_each_transmission_attempt_charges_encoded_bytes_samples_and_airtime():
    statistics = LinkStatistics()
    link = ReliableLink(
        2,
        LinkConfig(),
        statistics=statistics,
        encoded_size=lambda _packet: 100,
    )
    link.enqueue(RadioPacket(2, 1, 7, b"retry"))
    queued = link.next_packet(True, True)
    link.transmitted(queued, encoded_bytes=100, samples=3200, airtime_seconds=0.0032)
    link.transmitted(queued, encoded_bytes=100, samples=3200, airtime_seconds=0.0032)
    snapshot = statistics.snapshot()
    assert snapshot["bytes_transmitted"] == 200
    assert snapshot["samples_transmitted"] == 6400
    assert snapshot["airtime_seconds_transmitted"] == pytest.approx(0.0064)


def test_end_to_end_statistics_track_packets_acknowledgements_and_frequency():
    radio_network = network()
    try:
        frame = b"\xfdstatistics-frame"
        radio_network.inject("b", frame)
        radio_network.step(30)
        follower = radio_network.nodes["b"].app.statistics.snapshot()
        leader = radio_network.nodes["a"].app.statistics.snapshot()
        assert follower["payload_packets_received"] == 1
        assert follower["queue_capacity"] == radio_network.nodes["b"].app.config.link.queue_size
        assert follower["packets_transmitted"] >= 1
        assert follower["samples_transmitted"] > 0
        assert follower["airtime_seconds_transmitted"] > 0
        assert follower["bytes_transmitted"] > len(frame)
        assert leader["packets_received"] >= 1
        assert leader["payload_packets_delivered"] >= 1
        assert leader["active_frequency_hz"] in (905e6, 915e6, 925e6)
        assert isinstance(leader["estimated_signal_power"], float)
    finally:
        radio_network.close()


def test_crc_failure_is_counted():
    radio_network = network()
    try:
        leader = radio_network.nodes["a"]
        wire = bytearray(RadioPacket(2, 1, 99, b"damaged").encode())
        wire[-1] ^= 1
        leader.radio.incoming.append(leader.app.modem.modulate(bytes(wire)))
        leader.app.step()
        snapshot = leader.app.statistics.snapshot()
        assert snapshot["invalid_packets"] == 1
        assert snapshot["crc_failures"] == 1
    finally:
        radio_network.close()


def test_protocol_version_mismatch_is_counted():
    radio_network = network()
    try:
        leader = radio_network.nodes["a"]
        wire = bytearray(RadioPacket(2, 1, 99, b"future frame").encode())
        wire[2] = PROTOCOL_VERSION + 1
        leader.radio.incoming.append(leader.app.modem.modulate(bytes(wire)))
        leader.app.step()
        snapshot = leader.app.statistics.snapshot()
        assert snapshot["invalid_packets"] == 1
        assert snapshot["protocol_version_mismatches"] == 1
        assert snapshot["crc_failures"] == 0
    finally:
        radio_network.close()


def test_activity_and_delivered_rate_expire_without_status_polling_side_effects():
    clock = Clock()
    stats = LinkStatistics(clock, activity_timeout_seconds=10)
    assert not stats.snapshot()["receiver_acquired"]
    assert stats.snapshot()["last_peer_age_seconds"] is None
    clock.now += 1
    stats.mark_activity("frame")
    assert stats.snapshot()["receiver_acquired"]
    assert not stats.snapshot()["peer_active"]
    stats.mark_activity("peer")
    assert not stats.snapshot()["application_delivering"]
    stats.increment("payload_bytes_delivered", 100)
    assert stats.snapshot()["delivered_payload_bps"] == 800
    clock.now += 1
    assert stats.snapshot()["delivered_payload_bps"] == 400
    assert stats.snapshot() == stats.snapshot()
    clock.now += 5
    assert stats.snapshot()["delivered_payload_bps"] == 0
    assert stats.snapshot()["application_delivering"]
    clock.now += 5
    assert not stats.snapshot()["peer_active"]
    assert not stats.snapshot()["application_delivering"]
    assert stats.snapshot()["payload_bytes_delivered"] == 100
    with pytest.raises(ValueError):
        stats.mark_activity("invalid")


def test_application_status_describes_profile_and_real_delivery():
    net = network()
    try:
        stats = net.nodes["a"].app.statistics
        snapshot = stats.snapshot()
        assert snapshot["radio_ready"]
        assert snapshot["modulation"] == "bpsk"
        assert snapshot["configuration"]["node_id"] == 1
        assert not snapshot["application_delivering"]
        net.inject("b", b"status delivery")
        net.step(100)
        snapshot = stats.snapshot()
        assert snapshot["receiver_acquired"] and snapshot["peer_active"]
        assert snapshot["application_delivering"]
        assert snapshot["delivered_payload_bps"] > 0
        snapshot["configuration"]["node_id"] = 99
        assert stats.snapshot()["configuration"]["node_id"] == 1
    finally:
        net.close()
    assert not stats.snapshot()["radio_ready"]


@pytest.mark.parametrize("case", ["wrong_peer", "wrong_destination", "bad_crc", "delivery_error"])
def test_reception_does_not_confuse_acquisition_peer_and_delivery(monkeypatch, case):
    from packet_radio.app import RadioApp
    from packet_radio.endpoint import MemoryEndpoint
    from packet_radio.simulation import SimulatedChannel
    from tests.integration.test_end_to_end import node

    app = RadioApp(node("leader", 1), sdr=SimulatedChannel().radio(), endpoint=MemoryEndpoint())
    try:
        wire = RadioPacket(
            3 if case == "wrong_peer" else 2, 3 if case == "wrong_destination" else 1, 1, b"payload"
        ).encode()
        if case == "bad_crc":
            wire = wire[:-1] + bytes([wire[-1] ^ 1])
        monkeypatch.setattr(app.rx_modem, "feed_timed", lambda *args: [(wire, None)])
        if case == "delivery_error":

            def fail(*args):
                raise OSError("endpoint unavailable")

            monkeypatch.setattr(app.endpoint, "send", fail)
        app._receive_radio()
        snapshot = app.statistics.snapshot()
        assert snapshot["receiver_acquired"] == (case != "bad_crc")
        assert snapshot["peer_active"] == (case == "delivery_error")
        assert not snapshot["application_delivering"]
        assert snapshot["delivered_payload_bps"] == 0
    finally:
        app.close()
