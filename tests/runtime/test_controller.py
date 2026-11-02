"""Real child processes exercise owned startup, shutdown, snapshots and failures."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.forms import FormSession
from packet_radio.configuration.storage import save_project
from packet_radio.runtime.controller import RadioController

FAKE = """
import argparse, os, signal, time
p=argparse.ArgumentParser()
p.add_argument('--run-id'); p.add_argument('--config'); p.add_argument('--ready-fd',type=int)
p.add_argument('--peer-config'); p.add_argument('--allow-unauthenticated',action='store_true')
a=p.parse_args()
MODE = %r
print('SNAPSHOT='+a.config,flush=True)
if MODE == 'fail':
    raise RuntimeError('SDR disconnected')
signal.signal(signal.SIGTERM, signal.SIG_IGN if MODE == 'stubborn' else lambda *args: exit(0))
if MODE != 'hang':
    os.write(a.ready_fd,b'R')
os.close(a.ready_fd) if MODE != 'hang' else None
if MODE == 'crash':
    time.sleep(.1)
    raise RuntimeError('device unplugged')
while True: time.sleep(.02)
"""


def project(tmp_path, mode="2fsk"):
    form = FormSession.new()
    if mode != "2fsk":
        form.change("profile.waveform.modulation", mode)
        form.change("profile.packet.format", "generic-v5")
    return save_project(form.draft, tmp_path / "node.yaml")


def controller(mode="normal", **kwargs):
    return RadioController(command=[sys.executable, "-u", "-c", FAKE % mode], **kwargs)


async def wait_state(c, state):
    async def wait():
        while c.state != state:
            if not c.active:
                raise AssertionError((c.state, c.error, list(c.logs)))
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), 5)


def test_start_stop_snapshot_isolation_and_restart(tmp_path):
    async def exercise():
        source = project(tmp_path)
        c = controller()
        c.start([source])
        await wait_state(c, "Running")
        child = c.process

        # Readiness uses a separate pipe and can arrive before stdout is drained.
        async def wait_for_log():
            while not any(line.startswith("SNAPSHOT=") for line in c.logs):
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_log(), 5)
        snapshot = Path(
            next(line.split("=", 1)[1] for line in c.logs if line.startswith("SNAPSHOT="))
        )
        assert snapshot.exists() and snapshot != source.node_path
        source.set("profile.frequency.center_frequency_hz", 916e6)
        updated = save_project(source, source.node_path)
        assert RadioDraft.load(snapshot).validate().config.center_frequency_hz == 915e6
        with pytest.raises(ValueError, match="already"):
            c.start([updated])
        await c.stop()
        assert c.state == "Stopped" and child.returncode == 0
        assert not snapshot.exists() and c.process is None
        c.start([updated])
        await wait_state(c, "Running")
        assert c.running_configs[0].center_frequency_hz == 916e6
        await c.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["fail", "crash", "hang"])
def test_failure_startup_timeout_and_disconnection_are_reaped(tmp_path, mode):
    async def exercise():
        c = controller(mode, startup_timeout=0.3, stop_timeout=0.2)
        c.start([project(tmp_path)])
        await asyncio.wait_for(c.task, 5)
        assert c.state == "Failed" and c.error and c.process is None
        if mode == "hang":
            assert "timed out" in c.error
        else:
            assert any("disconnected" in line or "unplugged" in line for line in c.logs)

    asyncio.run(exercise())


def test_escalation_and_immediate_or_startup_stop(tmp_path):
    async def exercise():
        source = project(tmp_path)
        c = controller("stubborn", stop_timeout=0.1)
        c.start([source])
        await wait_state(c, "Running")
        child = c.process
        await asyncio.gather(c.stop(), c.stop())
        assert child.returncode == -signal.SIGKILL
        assert c.state == "Stopped" and any("killed" in line for line in c.logs)
        c.start([source])
        await c.stop()
        assert c.state == "Stopped" and not c.active
        c = controller("hang", startup_timeout=60)
        c.start([source])
        await wait_state(c, "Starting")
        await c.stop()
        assert c.process is None and c.state == "Stopped"

    asyncio.run(exercise())


def test_unsaved_external_edits_and_auth_gate_never_spawn(tmp_path):
    async def exercise():
        source = project(tmp_path, "bpsk")
        c = controller()
        c.start([source])
        await c.task
        assert c.state == "Failed" and "opt-in" in c.error
        c.start([source], allow_unauthenticated=True)
        await wait_state(c, "Running")
        await c.stop()
        source.set("node.log_level", "DEBUG")
        c.start([source], allow_unauthenticated=True)
        await c.task
        assert "Save and revalidate" in c.error
        source.node_path.write_text(source.node_path.read_text() + "\n# external edit\n")
        c.start([source], allow_unauthenticated=True)
        await c.task
        assert "Source changed" in c.error
        assert c.process is None

    asyncio.run(exercise())


def test_cancel_during_spawn_reaps_child(tmp_path, monkeypatch):
    async def exercise():
        original = asyncio.create_subprocess_exec
        children = []

        async def delayed(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            await asyncio.sleep(0.1)
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
        c = controller()
        c.start([project(tmp_path)])
        while not children:
            await asyncio.sleep(0.01)
        await c.stop()
        assert children[0].returncode is not None and c.process is None

    asyncio.run(exercise())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux kernel parent-death signal")
def test_abnormal_parent_exit_kills_radio_child(tmp_path):
    """SIGKILL a real owner; verify its guarded child cannot continue running."""
    import subprocess
    import time

    child_code = "from packet_radio.runtime.child import protect_parent; import os,time,sys; protect_parent(int(sys.argv[1])); print(os.getpid(),flush=True); time.sleep(60)"
    parent_code = "import os,subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',sys.argv[1],str(os.getpid())]); time.sleep(60)"
    parent = subprocess.Popen(
        [sys.executable, "-u", "-c", parent_code, child_code], stdout=subprocess.PIPE, text=True
    )
    child = None
    try:
        import select

        assert select.select([parent.stdout], [], [], 5)[0]
        child = int(parent.stdout.readline())
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            stat = Path(f"/proc/{child}/stat")
            if not stat.exists() or stat.read_text().split()[2] == "Z":
                break
            time.sleep(0.02)
        else:
            pytest.fail("Radio child survived abnormal owner death")
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        if child is not None:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        parent.stdout.close()


def test_stop_does_not_signal_unrelated_process(tmp_path):
    async def exercise():
        unrelated = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(60)"
        )
        try:
            c = controller()
            c.start([project(tmp_path)])
            await wait_state(c, "Running")
            await c.stop()
            assert unrelated.returncode is None
        finally:
            unrelated.terminate()
            await unrelated.wait()

    asyncio.run(exercise())


def test_snapshot_creation_failure_is_reported(tmp_path, monkeypatch):
    async def exercise():
        source = project(tmp_path)

        def fail(*args, **kwargs):
            raise OSError("temporary directory unavailable")

        monkeypatch.setattr("packet_radio.runtime.controller.tempfile.TemporaryDirectory", fail)
        c = controller()
        c.start([source])
        await c.task
        assert c.state == "Failed" and "temporary directory" in c.error and c.process is None

    asyncio.run(exercise())


def test_stop_during_readiness_does_not_break_child_pipe(tmp_path):
    code = """
import os,sys,signal,time
fd=int(sys.argv[sys.argv.index('--ready-fd')+1])
stopping=False
def stop(*args):
    global stopping
    stopping=True
signal.signal(signal.SIGTERM,stop)
print('initializing',flush=True)
time.sleep(.2)
os.write(fd,b'R')
os.close(fd)
while not stopping: time.sleep(.01)
"""

    async def exercise():
        c = RadioController(command=[sys.executable, "-u", "-c", code])
        c.start([project(tmp_path)])
        while "initializing" not in c.logs:
            await asyncio.sleep(0.01)
        child = c.process
        await c.stop()
        assert child.returncode == 0 and c.state == "Stopped"

    asyncio.run(exercise())


def test_missing_key_fails_real_child_before_hardware_access(tmp_path):
    async def exercise():
        draft = project(tmp_path, "bpsk")
        missing = tmp_path / "missing-private.pem"
        draft.set("node.authentication.private_key_file", str(missing))
        draft.set(
            "node.authentication.public_key_files",
            {1: [str(tmp_path / "local.pem")], 2: [str(tmp_path / "peer.pem")]},
        )
        draft = save_project(draft, draft.node_path)
        c = RadioController()
        c.start([draft])
        await asyncio.wait_for(c.task, 10)
        assert c.state == "Failed" and c.process is None
        assert "missing-private.pem" in "\n".join(c.logs)
        assert not missing.exists()
        assert c.monitor is None

    asyncio.run(exercise())
