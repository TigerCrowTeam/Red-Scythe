"""Drive packet-screen buttons through real loopback UDP and owned fake radio children.

Only the SDR process is replaced. Packet construction, endpoint selection, task
cancellation and error presentation use the production UI and UDP implementation.
"""

import asyncio
import errno
import socket
from contextlib import ExitStack, asynccontextmanager

import pytest

pytest.importorskip("textual")
from textual.widgets import Button, Input, Select, Static, TextArea

from packet_radio.configuration.forms import FormSession
from packet_radio.configuration.storage import PeerOptions, export_peer, save_project
from packet_radio.tui.app import RadioEditor
from packet_radio.tui.dialogs import DiscardDialog
from tests.runtime.test_controller import controller, wait_state
from tests.tui.test_editor import settle


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), 5)


async def datagram(udp):
    async def read():
        while True:
            try:
                return udp.recvfrom(2048)[0]
            except BlockingIOError:
                await asyncio.sleep(0.01)

    return await asyncio.wait_for(read(), 5)


def message(builder):
    return str(builder.query_one("#packet-message", Static).render())


def occupied(port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        try:
            udp.bind(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return True
            raise
    return False


@asynccontextmanager
async def running_builder(tmp_path):
    with ExitStack() as stack:
        sockets = []
        for _ in range(4):
            udp = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            udp.bind(("127.0.0.1", 0))
            udp.setblocking(False)
            sockets.append(udp)
        inputs, outputs = sockets[:2], sockets[2:]
        form = FormSession.new()
        for key, value in {
            "node.hardware.device.type": "x300",
            "node.hardware.device.addr": "192.168.10.2",
            "node.endpoint.bind_port": inputs[0].getsockname()[1],
            "node.endpoint.destination_port": outputs[0].getsockname()[1],
            "node.status.http_enabled": False,
        }.items():
            form.change(key, value)
        local = save_project(form.draft, tmp_path / "local.yaml")
        peer = export_peer(
            local,
            tmp_path / "peer.yaml",
            PeerOptions(
                "x300",
                "192.168.10.2",
                1,
                inputs[1].getsockname()[1],
                outputs[1].getsockname()[1],
                8766,
            ),
        )
        app = RadioEditor(FormSession(local))
        app.controller = controller()
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(pilot)
            app.controller.start([local, peer])
            await wait_state(app.controller, "Running")
            await pilot.press("ctrl+b")
            await pilot.pause()
            yield app, pilot, app.screen, inputs, outputs
        assert not app.controller.active and app.controller.process is None


async def listen(builder, reservation, node=0):
    port = reservation.getsockname()[1]
    reservation.close()
    builder.query_one("#packet-node", Select).value = node
    previous = builder.receive_task
    builder.query_one("#packet-listen", Button).press()
    await until(lambda: builder.receive_task is not None and builder.receive_task is not previous)
    await until(lambda: occupied(port) or builder.receive_task.done())
    assert not builder.receive_task.done(), message(builder)
    return port


@pytest.mark.parametrize("node", [0, 1])
def test_send_buttons_route_exact_bytes_and_repeat_freezes_packet(tmp_path, node):
    async def exercise():
        async with running_builder(tmp_path) as (_, pilot, b, inputs, _outputs):
            b.query_one("#packet-node", Select).value = node
            b.query_one("#packet-encoding", Select).value = "hex"
            b.query_one("#packet-payload", TextArea).load_text("00 ff 10")
            # An invalid repeat interval must not prevent a single send.
            b.query_one("#packet-interval", Input).value = "invalid"
            b.query_one("#packet-once", Button).press()
            assert await datagram(inputs[node]) == b"\x00\xff\x10"
            await until(lambda: b.send_task.done())
            assert "Submitted 1/1" in message(b)
            with pytest.raises(BlockingIOError):
                inputs[1 - node].recv(2048)
            b.query_one("#packet-count", Input).value = "3"
            b.query_one("#packet-interval", Input).value = "0.2"
            b.query_one("#packet-repeat", Button).press()
            assert await datagram(inputs[node]) == b"\x00\xff\x10"
            b.query_one("#packet-payload", TextArea).load_text("ab cd")
            assert await datagram(inputs[node]) == b"\x00\xff\x10"
            assert await datagram(inputs[node]) == b"\x00\xff\x10"
            await until(lambda: b.send_task.done())
            assert "Submitted 3/3" in message(b)
            await pilot.pause()
            with pytest.raises(BlockingIOError):
                inputs[node].recv(2048)

    asyncio.run(exercise())


def test_listener_inspects_received_bytes_and_edit_button_loads_them(tmp_path):
    async def exercise():
        async with running_builder(tmp_path) as (_, pilot, b, inputs, outputs):
            port = await listen(b, outputs[1], node=1)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"received\x00\xff", ("127.0.0.1", port))
            await until(lambda: b.last_received is not None)
            assert "10 bytes" in str(b.query_one("#packet-received", Static).render())
            assert "72 65 63 65 69 76 65 64 00 ff" in b.received[-1]
            b.query_one("#packet-edit-rx", Button).press()
            await until(lambda: b.receive_task.done())
            await pilot.pause()
            assert b.read_template().encode()[0] == b"received\x00\xff"
            assert b.read_template().payload_encoding == "hex"
            assert b.unsaved and not occupied(port)
            for udp in inputs:
                with pytest.raises(BlockingIOError):
                    udp.recv(2048)

    asyncio.run(exercise())


@pytest.mark.parametrize("stop", ["cancel", "back", "radio"])
def test_active_send_and_listener_are_cancelled_and_port_released(tmp_path, stop):
    async def exercise():
        async with running_builder(tmp_path) as (app, pilot, b, inputs, outputs):
            port = await listen(b, outputs[0])
            b.query_one("#packet-count", Input).value = "100"
            b.query_one("#packet-interval", Input).value = "10"
            b.query_one("#packet-repeat", Button).press()
            assert await datagram(inputs[0]) == b"Hello radio"
            send_task, receive_task = b.send_task, b.receive_task
            b.query_one("#packet-repeat", Button).press()
            await until(lambda: "Cancel the current send" in message(b))
            assert b.send_task is send_task and not send_task.done()
            b.query_one("#packet-listen", Button).press()
            await until(lambda: "Cancel the current listener" in message(b))
            assert b.receive_task is receive_task
            if stop == "radio":
                await pilot.press("ctrl+t")
                assert app.controller.state == "Stopped"
            elif stop == "back":
                b.query_one("#packet-close", Button).press()
                await until(lambda: app.screen is not b)
            else:
                b.query_one("#packet-cancel", Button).press()
            await until(lambda: send_task.done() and receive_task.done())
            assert not occupied(port)
            with pytest.raises(BlockingIOError):
                inputs[0].recv(2048)
            if stop != "radio":
                assert send_task.cancelled() and receive_task.cancelled()
                assert app.controller.active
            else:
                assert "stopped or changed" in message(b)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "problem, expected",
    [
        ("count", "Count must be 1..10000"),
        ("interval", "Interval must be 0.01..3600"),
        ("framing", "framing differs"),
        ("stopped", "Radio stopped or changed"),
    ],
)
def test_send_errors_are_visible_and_emit_no_datagram(tmp_path, problem, expected):
    async def exercise():
        async with running_builder(tmp_path) as (app, _pilot, b, inputs, _outputs):
            if problem == "count":
                b.query_one("#packet-count", Input).value = "0"
            elif problem == "interval":
                b.query_one("#packet-interval", Input).value = "nan"
            elif problem == "framing":
                b.query_one("#packet-frame-sync", Input).value = "deadbeef"
            else:
                await app.action_stop_radio()
            b.query_one("#packet-repeat", Button).press()
            await until(lambda: expected in message(b))
            assert b.send_task.done()
            for udp in inputs:
                with pytest.raises(BlockingIOError):
                    udp.recv(2048)

    asyncio.run(exercise())


def test_busy_listen_port_and_invalid_received_packet_preserve_editor(tmp_path):
    async def exercise():
        async with running_builder(tmp_path) as (_, pilot, b, _inputs, outputs):
            b.query_one("#packet-listen", Button).press()
            await until(lambda: "Packet I/O:" in message(b))
            assert b.receive_task.done()
            assert outputs[0].fileno() >= 0  # Existing owner is not closed or displaced.
            b.query_one("#packet-edit-rx", Button).press()
            await until(lambda: "Listen for a packet first" in message(b))
            port = await listen(b, outputs[0])
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(b"", ("127.0.0.1", port))
            await until(
                lambda: "Layout mismatch" in str(b.query_one("#packet-received", Static).render())
            )
            before = b.read_template().document()
            b.query_one("#packet-edit-rx", Button).press()
            await until(lambda: "Cannot edit received packet" in message(b))
            assert b.read_template().document() == before
            b.query_one("#packet-payload", TextArea).load_text("unsaved")
            await pilot.pause()
            b.query_one("#packet-edit-rx", Button).press()
            await until(lambda: isinstance(b.app.screen, DiscardDialog))
            await pilot.press("escape")
            assert b.read_template().payload == "unsaved"

    asyncio.run(exercise())
