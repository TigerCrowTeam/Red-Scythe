"""Probe lifecycle, timeout/reaping, and shared-channel ownership checks without UHD."""

import asyncio
import json
import sys
from dataclasses import replace

import pytest

from packet_radio.configuration.forms import FormSession
from packet_radio.configuration.preflight import RESULT_PREFIX, HardwareJob, validate_shared_pair


def pair():
    a = FormSession.new().report.config
    a = replace(a, sdr=replace(a.sdr, device={"type": "x300", "addr": "192.168.10.2"}))
    b = replace(
        a,
        node_id=a.peer_id,
        peer_id=a.node_id,
        timing_role="follower",
        sdr=replace(a.sdr, channel=1),
        endpoint=replace(a.endpoint, bind_port=50002, destination_port=50003),
        status=replace(a.status, http_port=8766),
    )
    return a, b


def test_pair_ownership_and_ports():
    a, b = pair()
    validate_shared_pair([a, b])
    for bad, message in [
        (replace(b, sdr=replace(b.sdr, channel=0)), "distinct channels"),
        (replace(b, sdr=replace(b.sdr, device={"type": "x300", "addr": "other"})), "identical"),
        (replace(b, endpoint=a.endpoint), "distinct payload"),
        (
            replace(b, status=replace(b.status, http_enabled=True, http_port=a.status.http_port)),
            "status HTTP",
        ),
    ]:
        with pytest.raises(ValueError, match=message):
            validate_shared_pair([replace(a, status=replace(a.status, http_enabled=True)), bad])


def test_worker_logs_do_not_corrupt_result_and_failures_are_actionable():
    async def exercise():
        for result in ({"ok": True, "devices": []}, {"ok": False, "error": "UHD unavailable"}):
            command = [
                sys.executable,
                "-c",
                f"print('UHD log'); print({(RESULT_PREFIX + json.dumps(result))!r})",
            ]
            job = HardwareJob(command=command)
            if result["ok"]:
                assert await job.run({}) == result
            else:
                with pytest.raises(RuntimeError, match="UHD unavailable"):
                    await job.run({})
            assert not job.running and job.process is None
        job = HardwareJob(command=[sys.executable, "-c", "raise RuntimeError('broken bindings')"])
        with pytest.raises(RuntimeError, match="without a result"):
            await job.run({})

    asyncio.run(exercise())


def test_timeout_cancel_duplicate_and_reaping():
    async def exercise():
        command = [sys.executable, "-c", "import time; time.sleep(60)"]
        job = HardwareJob(timeout=0.1, command=command)
        with pytest.raises(RuntimeError, match="timed out"):
            await job.run({})
        assert not job.running and job.process is None
        job.timeout = 30
        task = asyncio.create_task(job.run({}))
        while job.process is None:
            await asyncio.sleep(0.01)
        child = job.process
        with pytest.raises(ValueError, match="already running"):
            await job.run({})
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.returncode is not None and job.process is None and not job.running

    asyncio.run(exercise())


def test_cancel_during_spawn_reaps_child(monkeypatch):
    async def exercise():
        original = asyncio.create_subprocess_exec
        children = []

        async def delayed_spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            await asyncio.sleep(0.1)
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
        job = HardwareJob(command=[sys.executable, "-c", "import time; time.sleep(60)"])
        task = asyncio.create_task(job.run({}))
        while not children:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
        assert not job.running

    asyncio.run(exercise())
