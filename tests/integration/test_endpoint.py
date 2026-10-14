import select
import socket
from dataclasses import replace

from packet_radio.endpoint import UDPEndpoint
from packet_radio.protocol.framing import RadioPacket
from tests.integration.test_end_to_end import network


def test_real_udp_input_and_output_through_simulated_radio():
    net = network()
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as output,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender,
    ):
        output.bind(("127.0.0.1", 0))
        endpoint = UDPEndpoint(
            replace(
                net.nodes["a"].app.config.endpoint,
                bind_port=0,
                destination_port=output.getsockname()[1],
            )
        )
        net.nodes["a"].app.endpoint.close()
        net.nodes["a"].app.endpoint = endpoint
        try:
            packets = [b"", b"x" * 60000, bytes(range(256)), b"\x00\xffend"]
            for packet in packets:
                sender.sendto(packet, endpoint.socket.getsockname())
            assert select.select([endpoint.socket], [], [], 1)[0]
            net.step(200)
            assert net.received("b") == packets[2:]
            assert net.nodes["a"].app.statistics.snapshot()["invalid_payloads"] == 2
            net.inject("b", b"return\x00\xff")
            net.step(100)
            assert select.select([output], [], [], 1)[0]
            assert output.recvfrom(65535)[0] == b"return\x00\xff"
        finally:
            net.close()


def test_unrelated_peers_and_destinations_are_not_delivered_or_acked():
    net = network()
    try:
        receiver = net.nodes["a"]
        receiver.app.next_sync_at = float("inf")
        receiver.app.link.control_queue.clear()
        for packet in (
            RadioPacket(3, 1, 99, b"wrong peer"),
            RadioPacket(2, 3, 100, b"wrong destination"),
        ):
            receiver.radio.incoming.append(receiver.app.modem.modulate(packet.encode()))
        for _ in range(10):
            receiver.app._receive_radio()
        assert not net.received("a")
        assert not receiver.app.link.control_queue
        stats = receiver.app.statistics.snapshot()
        assert stats["unexpected_peer_packets"] == 1
        assert stats["wrong_destination_packets"] == 1
    finally:
        net.close()


def test_endpoint_output_failure_is_counted_without_stopping_radio():
    net = network()

    class FailedOutput:
        def receive(self):
            return None

        def send(self, payload):
            raise OSError("local receiver unavailable")

        def close(self):
            pass

    try:
        net.nodes["a"].app.endpoint = FailedOutput()
        net.inject("b", b"payload")
        net.step(100)
        stats = net.nodes["a"].app.statistics.snapshot()
        assert stats["endpoint_output_errors"] == 1
        assert stats["payload_packets_delivered"] == 0
    finally:
        net.close()


def test_payload_cli_sends_exact_binary_datagram(capsys):
    from packet_radio.cli.payload_cli import main

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        main(["send", "--port", str(receiver.getsockname()[1]), "--hex", "0001ff"])
        assert select.select([receiver], [], [], 1)[0]
        assert receiver.recvfrom(65535)[0] == b"\x00\x01\xff"
    assert "submitted_bytes" in capsys.readouterr().out


def test_generic_core_imports_without_application_protocol_dependency():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
class RejectRemovedDependency:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == 'pymavlink':
            raise RuntimeError('removed dependency imported')
sys.meta_path.insert(0, RejectRemovedDependency())
from packet_radio.app import RadioApp
from tests.integration.test_end_to_end import network
net = network()
try:
    net.inject('a', b'opaque')
    net.step(100)
    assert net.received('b') == [b'opaque']
finally:
    net.close()
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
