"""Launch frozen saved projects, supervise readiness and stop only owned children.

The radio uses the existing CLI and authentication rules. Temporary snapshots
preserve key, recording and replay-state referents; no keys or state are copied.
A private inherited pipe signals actual startup, independently of HTTP ports.
Bounded logs are in memory only. Linux child protection handles abnormal UI exit.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
from collections import deque
from pathlib import Path
from uuid import uuid4

from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.preflight import validate_shared_pair
from packet_radio.configuration.storage import check_sources, save_project
from packet_radio.runtime.deadline import wait_with_timeout
from packet_radio.runtime.monitor import StatusMonitor


class RadioController:
    def __init__(self, *, startup_timeout=30.0, stop_timeout=5.0, command=None):
        self.startup_timeout = startup_timeout
        self.stop_timeout = stop_timeout
        self.command = command
        self.state = "Stopped"
        self.error = ""
        self.logs: deque[str] = deque(maxlen=200)
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.running_configs = []
        self.sources: list[str] = []
        self._stopping = False
        self.run_id = ""
        self.monitor: StatusMonitor | None = None

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def start(self, drafts: list[RadioDraft], *, allow_unauthenticated=False):
        if self.active:
            raise ValueError("A radio is already starting, running or stopping. Stop it first.")
        self.state = "Validating"
        self.error = ""
        self.logs.clear()
        self.run_id = uuid4().hex
        self.monitor = None
        self.running_configs = []
        self.sources = []
        self._stopping = False
        # Own copies of both the staged values and the exact source-file digests.
        owned = []
        for draft in drafts:
            copy = RadioDraft(draft.node, draft.profile, draft.node_path)
            copy.source_versions = draft.source_versions.copy()
            copy.read_only_source = draft.read_only_source
            owned.append(copy)
        self.task = asyncio.create_task(self._supervise(owned, allow_unauthenticated))

    async def _supervise(self, drafts, allow_unauthenticated):
        try:
            await self._run(drafts, allow_unauthenticated)
        except Exception as exc:
            self.error = f"Radio lifecycle failed: {exc}"
            self.state = "Failed"

    async def _read_logs(self, stream):
        # Chunk reads also bound output with no newlines; never wait for readline limits.
        while data := await stream.read(4096):
            self.logs.extend(data.decode(errors="replace").splitlines())

    def _prepare(self, drafts, directory, allow_unauthenticated):
        if len(drafts) not in (1, 2):
            raise ValueError("Start one saved node or a shared X310 pair.")
        configs = []
        for draft in drafts:
            if draft.read_only_source or draft.node_path not in draft.source_versions:
                raise ValueError("Save the project before starting the radio.")
            check_sources(draft)
            staged = draft.validate()
            saved = RadioDraft.load(draft.node_path).validate()
            if not staged.valid or not saved.valid or staged.config != saved.config:
                raise ValueError("Save and revalidate the edited project before starting.")
            config = staged.config
            assert config is not None
            if (
                config.packet_format == "generic-v5"
                and config.authentication.private_key_file is None
                and not allow_unauthenticated
            ):
                raise ValueError(
                    "Authentication requires keys or an explicit isolated-bench unauthenticated opt-in."
                )
            configs.append(config)
        if len(configs) == 2:
            validate_shared_pair(configs)
        paths = []
        for index, draft in enumerate(drafts):
            frozen = save_project(draft, Path(directory) / f"node-{index}.yaml")
            paths.append(str(frozen.node_path))
        for draft in drafts:
            check_sources(draft)
        self.running_configs = configs
        self.sources = [str(d.node_path) for d in drafts]
        return paths

    async def _run(self, drafts, allow_unauthenticated):
        readers = []
        monitoring = None
        read_fd = write_fd = None
        transport = None
        cancelled = False
        failure = ""
        with tempfile.TemporaryDirectory(prefix="packet-radio-run-") as directory:
            try:
                paths = self._prepare(drafts, directory, allow_unauthenticated)
                read_fd, write_fd = os.pipe()
                reader = asyncio.StreamReader()
                protocol = asyncio.StreamReaderProtocol(reader)
                read_file = os.fdopen(read_fd, "rb", buffering=0)
                read_fd = None
                transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                    lambda: protocol, read_file
                )
                command = self.command or [
                    sys.executable,
                    "-m",
                    "packet_radio.runtime.child",
                    str(os.getpid()),
                ]
                arguments = [
                    "--config",
                    paths[0],
                    "--ready-fd",
                    str(write_fd),
                    "--run-id",
                    self.run_id,
                ]
                if len(paths) == 2:
                    arguments += ["--peer-config", paths[1]]
                if allow_unauthenticated:
                    arguments.append("--allow-unauthenticated")
                self.state = "Starting"
                spawn = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        *command,
                        *arguments,
                        pass_fds=(write_fd,),
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                )
                try:
                    self.process = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    self.process = await spawn
                    raise
                os.close(write_fd)
                write_fd = None
                readers.append(asyncio.create_task(self._read_logs(self.process.stdout)))
                try:
                    ready = await wait_with_timeout(reader.read(1), self.startup_timeout)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(
                        f"Radio startup timed out after {self.startup_timeout:g}s."
                    ) from exc
                if ready != b"R" or self.process.returncode is not None:
                    raise RuntimeError("Radio exited before completing startup.")
                self.monitor = StatusMonitor(self.running_configs, self.run_id, self.process.pid)
                monitoring = asyncio.create_task(self.monitor.run())
                self.state = "Running"
                code = await self.process.wait()
                raise RuntimeError(f"Radio exited unexpectedly (exit code {code}).")
            except asyncio.CancelledError:
                cancelled = True
            except (OSError, ValueError, RuntimeError) as exc:
                failure = str(exc)
            finally:
                self.state = "Stopping"
                if monitoring is not None:
                    monitoring.cancel()
                    await asyncio.gather(monitoring, return_exceptions=True)
                if write_fd is not None:
                    os.close(write_fd)
                if read_fd is not None:
                    os.close(read_fd)
                if self.process is not None and self.process.returncode is None:
                    try:
                        self.process.send_signal(signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        await wait_with_timeout(self.process.wait(), self.stop_timeout)
                    except asyncio.TimeoutError:
                        self.logs.append("Graceful stop timed out; killed the owned radio process.")
                        try:
                            self.process.kill()
                        except ProcessLookupError:
                            pass
                        await self.process.wait()
                if (
                    self.process is not None
                    and cancelled
                    and self.process.returncode not in (0, -signal.SIGTERM, -signal.SIGKILL)
                ):
                    failure = f"Radio shutdown failed (exit code {self.process.returncode}); inspect run logs."
                if transport is not None:
                    transport.close()
                await asyncio.gather(*readers, return_exceptions=True)
                self.process = None
        self.error = failure
        self.state = "Stopped" if cancelled and not failure else "Failed"
        self._stopping = False

    async def stop(self):
        if self.active:
            assert self.task is not None
            if not self._stopping:
                self._stopping = True
                self.state = "Stopping"
                self.task.cancel()
            try:
                await asyncio.shield(self.task)
            except asyncio.CancelledError:
                if self.task.cancelled() and self.process is None:
                    self.state = "Stopped"
                    self._stopping = False
                else:
                    raise
        elif self.state != "Failed":
            self.state = "Stopped"
