"""Hardware workers leave editing responsive and never validate a stale snapshot."""

import asyncio

import pytest

pytest.importorskip("textual")
from textual.widgets import Input

from packet_radio.tui.app import RadioEditor
from packet_radio.tui.hardware import HardwareDialog
from packet_radio.tui.widgets import field_id


class FakeJob:
    def __init__(self, result=None, error=None):
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.result = result or {
            "ok": True,
            "nodes": [],
            "warnings": [],
            "capabilities": {"motherboards": 1, "channels": {}},
        }
        self.error = error
        self.cancelled = False

    async def run(self, request):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error:
            raise RuntimeError(self.error)
        return self.result


async def settle(pilot):
    for _ in range(100):
        await pilot.pause()
        if pilot.app.ready:
            return
    raise AssertionError("form did not mount")


def test_edit_during_probe_marks_result_stale_and_cancel_is_responsive():
    async def exercise():
        app = RadioEditor()
        job = app.hardware_job = FakeJob()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("f10")
            await job.started.wait()
            await app.apply_text("profile.frequency.center_frequency_hz", "916 MHz")
            job.release.set()
            await app.hardware_task
            assert "stale" in app.hardware_status
            await pilot.press("f3")
            assert isinstance(app.screen, HardwareDialog)
            await pilot.press("escape")
            job = app.hardware_job = FakeJob()
            await pilot.press("f10")
            await job.started.wait()
            await pilot.press("f11")
            assert job.cancelled and "cancelled" in app.hardware_status

    asyncio.run(exercise())


def test_failure_keeps_editor_usable_and_pending_input_blocks_probe():
    async def exercise():
        app = RadioEditor()
        job = app.hardware_job = FakeJob(error="Device busy; stop its owner")
        job.release.set()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("f10")
            await app.hardware_task
            assert "Device busy" in app.hardware_status
            await pilot.press("f3")
            assert isinstance(app.screen, HardwareDialog)
            await pilot.press("escape")
            field = app.query_one("#" + field_id("profile.waveform.bit_rate_bps"), Input)
            field.value = "nan"
            await pilot.pause()
            job = app.hardware_job = FakeJob()
            await pilot.press("f10")
            assert not job.started.is_set()
            assert app.errors

    asyncio.run(exercise())


def test_discovery_selection_and_quit_cancel():
    async def exercise():
        app = RadioEditor()
        job = app.hardware_job = FakeJob(
            result={
                "ok": True,
                "devices": [{"type": "x300", "addr": "192.168.10.2", "serial": "3267DE0"}],
            }
        )
        job.release.set()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("f9")
            await app.hardware_task
            await pilot.press("f3")
            app.screen.query_one("#hardware-select").press()
            await settle(pilot)
            assert app.session.report.config.sdr.device == {"type": "x300", "addr": "192.168.10.2"}
            job = app.hardware_job = FakeJob()
            await pilot.press("f10")
            await job.started.wait()
        assert job.cancelled

    asyncio.run(exercise())


def test_pair_selection_validates_ownership_before_probe(tmp_path):
    from packet_radio.configuration.forms import FormSession
    from packet_radio.configuration.storage import PeerOptions, export_peer, save_project
    from packet_radio.tui.dialogs import PathDialog

    async def exercise():
        form = FormSession.new()
        form.change("node.hardware.device.type", "x300")
        form.change("node.hardware.device.addr", "192.168.10.2")
        peer = export_peer(
            form.draft,
            tmp_path / "peer.yaml",
            PeerOptions("x300", "192.168.10.2", 1, 50002, 50003, 8766),
        )
        app = RadioEditor(form)
        job = app.hardware_job = FakeJob()
        job.release.set()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("ctrl+p")
            assert isinstance(app.screen, PathDialog)
            app.screen.query_one(Input).value = str(peer.node_path)
            await pilot.press("enter")
            await asyncio.wait_for(job.started.wait(), 3)
            await app.hardware_task
            assert app.hardware_peer.node_path == peer.node_path
            peer.set("node.hardware.channel", 0)
            save_project(peer, peer.node_path)
            app.update_hardware_status()
            assert "stale" in app.hardware_status
            job = app.hardware_job = FakeJob()
            await app.check_pair(str(peer.node_path))
            assert "distinct channels" in app.hardware_status
            assert not job.started.is_set()

    asyncio.run(exercise())


def test_capability_report_shows_both_directions_coercions_and_literal_device_text():
    from textual.widgets import Button, Static

    result = {
        "ok": True,
        "capabilities": {
            "motherboards": 1,
            "channels": {
                direction: [
                    {
                        "channel": 1,
                        "identity": "UBX [red]literal[/red]",
                        "antennas": ["TX/RX", "RX2"] if direction == "rx" else ["TX/RX"],
                        "ranges": {
                            "frequency": {"minimum": 1e7, "maximum": 6e9, "step": 1},
                            "rate": {"minimum": 1e6, "maximum": 2e8, "step": 0},
                            "gain": {"minimum": 0, "maximum": 31.5, "step": 0.5},
                        },
                    }
                ]
                for direction in ("tx", "rx")
            },
        },
        "nodes": [
            {
                "node_id": 2,
                "channel": 1,
                "requested": {"tx_rate": 2e6, "rx_antenna": None, "rx_bandwidth": 1e6},
                "actual": {"tx_rate": 2e6, "rx_antenna": "RX2", "rx_bandwidth": 160e6},
                "frequencies": [{"requested_hz": 915e6, "rx_hz": 915e6, "tx_hz": 915e6}],
            }
        ],
        "warnings": ["RX bandwidth adjusted from 1000000 to 160000000 Hz"],
    }

    async def exercise():
        app = RadioEditor()
        job = app.hardware_job = FakeJob(result=result)
        job.release.set()
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(pilot)
            await pilot.press("f10")
            await app.hardware_task
            await pilot.press("f3")
            dialog = app.screen
            controls = list(dialog.query(Static))
            report = "\n".join(str(control.render()) for control in controls)
            for expected in (
                "TX channel 1",
                "RX channel 1",
                "UBX [red]literal[/red]",
                "Antennas: TX/RX, RX2",
                "gain: 0–31.5 dB; step 0.5",
                "rate: 1e+06–2e+08 samples/s",
                "frequency: 1e+07–6e+09 Hz",
                "Node 2",
                "rx_antenna: automatic → RX2",
                "rx_bandwidth: 1000000.0 → 160000000.0",
                "frequency Hz: 9.15e+08 → RX 9.15e+08, TX 9.15e+08",
                "Adjustment: RX bandwidth adjusted",
                "RF performance unmeasured",
            ):
                assert expected in report
            dialog.query_one("#hardware-close", Button).press()
            await pilot.pause()
            assert app.screen is app.main_screen
            assert not app.controller.active

    asyncio.run(exercise())


def test_empty_discovery_explains_recovery_without_changing_device():
    from textual.widgets import Button, Static

    async def exercise():
        app = RadioEditor()
        before = app.session.draft.node
        app.hardware_job = FakeJob(result={"devices": []})
        app.hardware_job.release.set()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("f9")
            await app.hardware_task
            await pilot.press("f3")
            text = "\n".join(str(s.render()) for s in app.screen.query(Static))
            assert "No devices found" in text and "power, cabling" in text
            assert not app.screen.query("#hardware-select")
            app.screen.query_one("#hardware-close", Button).press()
            await pilot.pause()
            assert app.session.draft.node == before

    asyncio.run(exercise())


def test_discovery_selects_requested_b205_from_multiple_devices():
    from textual.widgets import Button, Select

    async def exercise():
        app = RadioEditor()
        app.hardware_job = FakeJob(
            result={
                "devices": [
                    {"type": "b200", "serial": "FIRST", "product": "B205"},
                    {"type": "b200", "serial": "SECOND", "product": "B205"},
                ]
            }
        )
        app.hardware_job.release.set()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("f9")
            await app.hardware_task
            await pilot.press("f3")
            app.screen.query_one("#hardware-device", Select).value = 1
            app.screen.query_one("#hardware-select", Button).press()
            await settle(pilot)
            assert app.session.report.config.sdr.device == {"type": "b200", "serial": "SECOND"}

    asyncio.run(exercise())
