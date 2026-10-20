"""Local UDP submission, frame adaptation, receive decoding and stop isolation."""

import asyncio
import socket
from dataclasses import replace
from types import SimpleNamespace

import pytest

from packet_radio.configuration.forms import FormSession
from packet_radio.packets.template import PacketTemplate
from packet_radio.packets.transport import receive, submit
from packet_radio.protocol.registry import resolve_processing


def owned(config):
    return SimpleNamespace(active=True, state="Running", run_id="first", running_configs=[config])


@pytest.mark.parametrize("preframed", [False, True])
def test_exact_repeated_submission_and_radio_restart(preframed):
    async def exercise():
        cfg = FormSession.new().report.config
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as output:
            output.bind(("127.0.0.1", 0))
            output.settimeout(1)
            cfg = replace(
                cfg,
                endpoint=replace(cfg.endpoint, bind_port=output.getsockname()[1]),
                input_kind="frame" if preframed else "payload",
            )
            c = owned(cfg)
            template = PacketTemplate.new(cfg)
            progress = []
            await submit(c, 0, template, 3, 0.05, progress.append)
            expected = template.encode()[0]
            if preframed:
                expected = resolve_processing(cfg).codec.encode(expected)
            assert [output.recv(2048) for _ in range(3)] == [expected] * 3
            assert progress == [1, 2, 3]
            progress.clear()
            task = asyncio.create_task(submit(c, 0, template, 10, 3600, progress.append))
            for _ in range(100):
                if progress:
                    break
                await asyncio.sleep(0.01)
            c.run_id = "replacement"
            with pytest.raises(ValueError, match="changed"):
                await asyncio.wait_for(task, 1)
            assert progress == [1]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "count, interval", [(0, 1), (10001, 1), (1, float("nan")), (1, 0), (2, 0.01)]
)
def test_invalid_sending_limits(count, interval):
    async def exercise():
        cfg = FormSession.new().report.config
        t = PacketTemplate.new(cfg)
        t.payload = "a" * 1024
        with pytest.raises(ValueError):
            await submit(owned(cfg), 0, t, count, interval, lambda n: None)

    asyncio.run(exercise())


def test_listener_decode_collision_and_cleanup():
    async def exercise():
        cfg = FormSession.new().report.config
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
            cfg = replace(cfg, endpoint=replace(cfg.endpoint, destination_port=port))
            with pytest.raises(OSError):
                await receive(owned(cfg), 0, PacketTemplate.new(cfg), lambda text: None)
        c = owned(cfg)
        t = PacketTemplate.new(cfg)
        seen = []
        task = asyncio.create_task(receive(c, 0, t, seen.append))
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                for _ in range(100):
                    sender.sendto(b"Hello receiver", ("127.0.0.1", port))
                    await asyncio.sleep(0.02)
                    if seen:
                        break
            assert "payload (payload): Hello receiver" in seen[-1]
            c.active = False
            with pytest.raises(ValueError, match="stopped"):
                await asyncio.wait_for(task, 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reopened:
            reopened.bind(("127.0.0.1", port))

    asyncio.run(exercise())


def test_no_submission_for_stopped_wrong_frame_or_direction():
    async def exercise():
        cfg = FormSession.new().report.config
        t = PacketTemplate.new(cfg)
        c = owned(cfg)
        c.active = False
        with pytest.raises(ValueError, match="stopped"):
            await submit(c, 0, t, 1, 0.1, lambda n: None)
        c.active = True
        c.running_configs = [replace(cfg, operation="receive-only")]
        with pytest.raises(ValueError, match="receive-only"):
            await submit(c, 0, t, 1, 0.1, lambda n: None)
        c.running_configs = [cfg]
        t.frame["sync"] = "deadbeef"
        with pytest.raises(ValueError, match="framing differs"):
            await submit(c, 0, t, 1, 0.1, lambda n: None)

    asyncio.run(exercise())
