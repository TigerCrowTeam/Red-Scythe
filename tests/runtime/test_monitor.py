"""Exercise status identity, stale data, bounded HTTP reads and cancellation offline."""

import asyncio
import json
import time
from dataclasses import replace

import pytest

from packet_radio.runtime.identity import config_identity, runtime_identity
from packet_radio.runtime.monitor import MAX_BODY, StatusMonitor, fetch_status
from packet_radio.tui.telemetry import render_node
from tests.runtime.test_controller import project


def test_config_identity_and_frozen_paths(tmp_path):
    from packet_radio.configuration.storage import save_project

    draft = project(tmp_path)
    config = draft.validate().config
    frozen = save_project(draft, tmp_path / "frozen" / "node.yaml").validate().config
    assert config_identity(config) == config_identity(frozen)
    assert config_identity(config) != config_identity(replace(config, node_id=19))
    assert runtime_identity(config)["run_id"] != runtime_identity(config)["run_id"]


def test_poll_identity_counters_staleness_and_disabled(tmp_path, monkeypatch):
    async def exercise():
        config = project(tmp_path).validate().config
        monitor = StatusMonitor(
            [config, replace(config, status=replace(config.status, http_enabled=False))],
            "owned",
            123,
        )
        identity = {**runtime_identity(config, "owned"), "pid": 123}
        response = {
            "runtime": identity,
            "uptime_seconds": 1,
            "payload_packets_delivered": 3,
            "receiver_acquired": True,
        }

        async def fetch(*args):
            return response.copy()

        monkeypatch.setattr("packet_radio.runtime.monitor.fetch_status", fetch)
        await monitor.poll(0)
        node = monitor.nodes[0]
        assert node.state == "Live"
        assert "packets delivered=3" in render_node(node)
        assert "FEC failed frames=N/A" in render_node(node)
        response.update(uptime_seconds=2, payload_packets_delivered=4)
        await monitor.poll(0)
        assert node.snapshot["payload_packets_delivered"] == 4
        await monitor.poll(0)
        assert node.state == "Stale" and "Non-advancing" in node.error
        assert "current RF acquisition and delivery are unknown" in render_node(node)
        response["runtime"] = {**identity, "run_id": "previous-run"}
        response["uptime_seconds"] = 3
        await monitor.poll(0)
        assert "mismatch" in node.error
        assert node.snapshot["uptime_seconds"] == 2
        for key, wrong in [
            ("config_sha256", "other"),
            ("pid", 456),
            ("node_id", 77),
            ("channel", 8),
        ]:
            response["runtime"] = {**identity, key: wrong}
            await monitor.poll(0)
            assert "mismatch" in node.error
        response["runtime"] = identity
        await monitor.poll(0)
        assert node.state == "Live"
        node.received_at = time.monotonic() - 3
        assert node.state == "Stale"
        await monitor.poll(1)
        assert monitor.nodes[1].state == "Disabled"
        assert "Enable HTTP" in monitor.nodes[1].error
        node.stopped = True
        assert node.state == "Stopped (historical)"

    asyncio.run(exercise())


@pytest.mark.parametrize("body", [b"[]", b"{bad", b"null"])
def test_http_invalid_json(body):
    async def exercise():
        async def serve(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(f"HTTP/1.0 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        async with server:
            with pytest.raises(ValueError):
                await fetch_status("127.0.0.1", server.sockets[0].getsockname()[1])

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "header",
    [f"200 OK\r\nContent-Length: {MAX_BODY + 1}", "503 Failed\r\nContent-Length: 2", "200 OK"],
)
def test_http_bad_headers(header):
    async def exercise():
        async def serve(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(f"HTTP/1.0 {header}\r\n\r\n".encode())
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        async with server:
            with pytest.raises(ValueError):
                await fetch_status("127.0.0.1", server.sockets[0].getsockname()[1])

    asyncio.run(exercise())


def test_two_nodes_real_http_and_hung_poll_cancellation(tmp_path):
    async def exercise():
        config = project(tmp_path).validate().config
        requests = []
        connections = set()

        async def serve(reader, writer):
            connections.add(writer)
            try:
                requests.append(await reader.readuntil(b"\r\n\r\n"))
                body = json.dumps(
                    {
                        "runtime": {**runtime_identity(config, "run"), "pid": 123},
                        "uptime_seconds": time.monotonic(),
                    }
                ).encode()
                writer.write(
                    f"HTTP/1.0 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
                )
                await writer.drain()
            finally:
                writer.close()
                connections.discard(writer)

        async def hang(reader, writer):
            connections.add(writer)
            try:
                await reader.read()  # Closes when the bounded request is cancelled.
            finally:
                writer.close()
                connections.discard(writer)

        good = await asyncio.start_server(serve, "127.0.0.1", 0)
        bad = await asyncio.start_server(hang, "127.0.0.1", 0)
        async with good, bad:
            config = replace(
                config, status=replace(config.status, http_port=good.sockets[0].getsockname()[1])
            )
            peer = replace(
                config,
                node_id=2,
                status=replace(config.status, http_port=bad.sockets[0].getsockname()[1]),
            )
            monitor = StatusMonitor([config, peer], "run", 123, timeout=0.1)
            task = asyncio.create_task(monitor.run())
            await asyncio.sleep(0.25)
            assert monitor.nodes[0].state == "Live"
            assert monitor.nodes[1].state == "Unavailable"
            assert "timed out" in monitor.nodes[1].error
            await asyncio.sleep(0.4)
            assert len(requests) == 2  # At most 2 Hz; no busy-loop retry.
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.3)
            await asyncio.sleep(0.02)
            assert not connections
            assert all(n.stopped for n in monitor.nodes)

    asyncio.run(exercise())


def test_cancel_racing_failed_fetch_stops_monitor(tmp_path, monkeypatch):
    async def exercise():
        config = project(tmp_path).validate().config
        monitor = StatusMonitor([config], "run", 123)

        async def fails(*args):
            asyncio.get_running_loop().call_soon(owner.cancel)
            raise OSError("connection refused at cancellation")

        monkeypatch.setattr("packet_radio.runtime.monitor.fetch_status", fails)
        owner = asyncio.create_task(monitor.run())
        try:
            done, _ = await asyncio.wait({owner}, timeout=0.5)
            assert done and owner.cancelled(), "Stop was swallowed by a completed status request"
            assert monitor.nodes[0].stopped
        finally:
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("hopping", [False, True])
def test_hop_admission_diagnostics_only_for_hopping(hopping):
    from packet_radio.runtime.monitor import NodeStatus

    snapshot = {
        "runtime": {"config_sha256": "checked"},
        "configuration": {"frequency": {"frequencies_hz": [914e6, 915e6] if hopping else [915e6]}},
        "hop_burst_rejections": 10,
        "transmit_drops": 10,
        "hop_slots_missed": 0,
    }
    node = NodeStatus(1, 0, True, snapshot=snapshot, received_at=time.monotonic(), error="")
    text = render_node(node)
    assert ("Hopping:" in text) == hopping
    assert ("schedule payload input within the hop windows" in text) == hopping
    if hopping:
        assert "burst admission rejections=10" in text
        assert "TX drops=10" in text
        assert "missed slots=0" in text
