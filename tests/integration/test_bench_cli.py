"""Exercise the maintained two-host checker over real local UDP without SDRs."""

import asyncio
import socket
from dataclasses import replace

import pytest

from packet_radio.config import load_config
from packet_radio.runtime.bench import payload, traffic, workload


@pytest.mark.parametrize("modes", [("send", "receive"), ("duplex", "duplex")])
def test_checker_verifies_binary_payloads_without_shared_wall_clock(modes):
    async def exercise():
        sockets = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2)]
        try:
            for sock in sockets:
                sock.bind(("127.0.0.1", 0))
            configs = [load_config(f"configs/v1_1/peer-{r}-b205-two-host.yaml") for r in "ab"]
            configs = [
                replace(c, endpoint=replace(c.endpoint, bind_port=sockets[1 - i].getsockname()[1]))
                for i, c in enumerate(configs)
            ]
            reports = await asyncio.gather(
                *(
                    traffic(
                        sock, c, run_id="test", duration=1, rate=5, size=256, mode=mode, grace=0.2
                    )
                    for sock, c, mode in zip(sockets, configs, modes)
                )
            )
            assert all(r["passed"] for r in reports)
            assert reports[1]["received"] == 5
            assert reports[0]["received"] == (5 if modes[0] == "duplex" else 0)
        finally:
            for sock in sockets:
                sock.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "duration,rate,size,mode",
    [
        (0, 1, 256, "duplex"),
        (1, float("nan"), 256, "duplex"),
        (1, 1, 12, "duplex"),
        (1, 1, 256, "invalid"),
    ],
)
def test_bad_workloads_are_rejected(duration, rate, size, mode):
    with pytest.raises(ValueError):
        workload(duration, rate, size, mode)


def test_payload_contains_run_source_sequence_and_all_bytes():
    original = payload("run1", 1, 5, 1024)
    assert len(original) == 1024 and b"\x00\xff" not in original[:13]
    assert original != payload("run2", 1, 5, 1024)
    assert original != payload("run1", 2, 5, 1024)
    assert original != payload("run1", 1, 6, 1024)


def test_controller_disconnect_stops_traffic_immediately():
    async def exercise():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            cfg = load_config("configs/v1_1/peer-a-b205-two-host.yaml")
            with pytest.raises(ConnectionError, match="disconnected"):
                await traffic(
                    sock,
                    cfg,
                    run_id="disconnected",
                    duration=1,
                    rate=1,
                    size=256,
                    mode="duplex",
                    connected=lambda: False,
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("acknowledged", [True, False])
def test_worker_shutdown_requires_acknowledgement(acknowledged):
    import json
    from unittest.mock import AsyncMock, Mock

    from packet_radio.cli.bench_cli import stop

    async def exercise():
        process = Mock(returncode=None)
        process.stdin.drain = AsyncMock()
        process.stdout.readline = AsyncMock(
            return_value=(
                json.dumps({"stopped": "Stopped", "error": None}).encode() + b"\n"
                if acknowledged
                else b""
            )
        )

        async def wait():
            process.returncode = 0
            return 0

        process.wait = wait
        assert await stop(process) is acknowledged
        if not acknowledged:
            process.terminate.assert_called_once()
        else:
            process.terminate.assert_not_called()

    asyncio.run(exercise())


@pytest.mark.parametrize("modulation", ["bpsk", "2fsk", "msk", "gmsk", "qpsk"])
def test_modulation_comparison_preserves_source_rates_and_bandwidth(modulation):
    from packet_radio.protocol.registry import resolve_processing
    from packet_radio.runtime.bench import prepare_session

    session = prepare_session("configs/v1_1/peer-a-b205-rf-hopping.yaml", modulation)
    assert session.report.valid
    config = session.report.config
    assert config.sdr.bit_rate_bps == 250000
    assert config.sdr.sample_rate == 2000000
    assert config.sdr.bandwidth == 1000000
    assert config.sdr.modulation == modulation
    assert config.sdr.samples_per_symbol == (16 if modulation == "qpsk" else 8)
    processing = resolve_processing(config)
    modem = processing.make_modem(config.sdr)
    wire_size = processing.codec.payload_size(1024, authenticated=True)
    assert modem.frame_size(wire_size) == 2280
    assert modem.sample_count(wire_size) / config.sdr.sample_rate == pytest.approx(
        0.07296, abs=0.00002
    )


def test_failed_phase_stops_before_backlog_can_contaminate_next_phase(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from packet_radio.cli import bench_cli

    async def exercise():
        configurations = {"emulation": {"frequency": {"mode": "fixed"}}}
        ready = [
            dict(node=1, peer=2, serial="a", bit_rate=250000, configuration=configurations),
            dict(node=2, peer=1, serial="b", bit_rate=250000, configuration=configurations),
        ]
        results = [
            dict(passed=False, received=0, expected=0, errors={}),
            dict(passed=False, received=0, expected=2, errors={}),
        ]
        read = AsyncMock(side_effect=ready + results)
        send = AsyncMock()
        stop = AsyncMock(return_value=True)
        monkeypatch.setattr(bench_cli, "read", read)
        monkeypatch.setattr(bench_cli, "send", send)
        monkeypatch.setattr(bench_cli, "stop", stop)
        monkeypatch.setattr(bench_cli.asyncio, "create_subprocess_exec", AsyncMock())
        args = SimpleNamespace(
            modulations=["2fsk"],
            experiment=None,
            allow_unauthenticated=False,
            peer_python="/python",
            peer_config="/node.yaml",
            peer_clock_offset=0,
            config=tmp_path / "node.yaml",
            peer_ssh="host",
            recovery=False,
            duration=1,
            rate=2,
            payload=128,
            summary=None,
        )
        assert not await bench_cli.pair(args)
        assert send.await_count == 2
        assert stop.await_count == 2

    asyncio.run(exercise())


def test_matching_modulation_keeps_custom_shaping_and_protection(tmp_path):
    from packet_radio.configuration.storage import save_project
    from packet_radio.runtime.bench import prepare_session

    session = prepare_session("configs/v1_1/peer-a-b205-rf-hopping.yaml", "qpsk")
    session.change("profile.waveform.rrc_rolloff", 0.35)
    saved = save_project(session.draft, tmp_path / "qpsk.yaml")
    actual = prepare_session(saved.node_path, "qpsk")
    assert actual.report.valid
    assert actual.report.config.sdr.rrc_rolloff == 0.35
    assert actual.report.config.sdr.bit_rate_bps == 250000


def test_phase_baseline_waits_for_a_new_poll_instead_of_reusing_startup_status():
    import time
    from types import SimpleNamespace

    from packet_radio.runtime.bench import fresh_snapshot

    async def exercise():
        node = SimpleNamespace(
            state="Live", received_at=time.monotonic(), snapshot={"rx_buffer_drops": 0}
        )
        controller = SimpleNamespace(monitor=SimpleNamespace(nodes=[node]))
        pending = asyncio.create_task(fresh_snapshot(controller))
        await asyncio.sleep(0.01)
        assert not pending.done()
        node.snapshot = {"rx_buffer_drops": 840}
        node.received_at = time.monotonic()
        result = await pending
        assert result == {"rx_buffer_drops": 840}
        node.snapshot["rx_buffer_drops"] = 841
        assert result["rx_buffer_drops"] == 840
        # A cached Live status cannot conceal a stopped poller.
        with pytest.raises(RuntimeError, match="fresh"):
            await fresh_snapshot(controller, timeout=0.01)

    asyncio.run(exercise())
