"""Auto setup stays offline until Start and preserves edits during discovery."""

import asyncio

import pytest

pytest.importorskip("textual")
from textual.widgets import Select

from packet_radio.configuration.forms import FormSession
from packet_radio.configuration.storage import save_project
from packet_radio.tui.app import RadioEditor
from packet_radio.tui.hardware import DeploymentDialog
from packet_radio.tui.run import StartDialog
from tests.configuration.test_deployment import B205, X310
from tests.tui.test_hardware import FakeJob, settle


def editor(devices, role=None):
    app = RadioEditor(auto_detect=True, node_role=role)
    app.hardware_job = FakeJob(result={"ok": True, "devices": devices})
    app.hardware_job.release.set()
    return app


def test_b205_discovery_requires_role_and_creates_one_bidirectional_node():
    async def exercise():
        app = editor([B205])
        async with app.run_test() as pilot:
            await settle(pilot)
            await app.hardware_task
            assert isinstance(app.screen, DeploymentDialog)
            app.screen.query_one("#deployment-use").press()
            await pilot.pause()
            assert isinstance(app.screen, DeploymentDialog)
            app.screen.query_one("#deployment-role", Select).value = "b"
            app.screen.query_one("#deployment-use").press()
            await pilot.pause()
            await settle(pilot)
            cfg = app.session.report.config
            assert cfg.node_id == 2 and cfg.operation == "bidirectional"
            assert cfg.sdr.device["serial"] == "USB-A" and cfg.sdr.channel == 0
            assert not app.controller.active

    asyncio.run(exercise())


def test_x310_auto_setup_start_previews_two_local_channels(tmp_path):
    async def exercise():
        app = editor([X310])
        async with app.run_test() as pilot:
            await settle(pilot)
            await app.hardware_task
            assert app.session.report.config.sdr.device["type"] == "x300"
            app.session = FormSession(save_project(app.session.draft, tmp_path / "x310.yaml"))
            await app.render_form()
            await app.action_start_radio()
            assert isinstance(app.screen, StartDialog)
            assert [c.sdr.channel for c in app.screen.configs] == [0, 1]
            assert not app.controller.active
            await pilot.press("escape")
            assert not app.controller.active

    asyncio.run(exercise())


def test_delayed_discovery_preserves_user_edits():
    async def exercise():
        app = editor([B205], role="a")
        app.hardware_job.release.clear()
        async with app.run_test() as pilot:
            await settle(pilot)
            await app.hardware_job.started.wait()
            await app.apply_text("profile.frequency.center_frequency_hz", "916 MHz")
            app.hardware_job.release.set()
            await app.hardware_task
            assert app.session.report.config.center_frequency_hz == 916e6
            assert "Draft changed" in app.hardware_status
            assert "serial" not in app.session.report.config.sdr.device

    asyncio.run(exercise())


def test_no_radio_leaves_offline_editor_usable():
    async def exercise():
        app = editor([])
        async with app.run_test() as pilot:
            await settle(pilot)
            await app.hardware_task
            assert "No identified" in app.hardware_status
            await app.apply_text("profile.frequency.center_frequency_hz", "916 MHz")
            assert app.session.report.config.center_frequency_hz == 916e6
            assert not app.controller.active

    asyncio.run(exercise())


def test_multiple_devices_wait_for_selection_even_with_role():
    async def exercise():
        app = editor([X310, B205], role="a")
        async with app.run_test() as pilot:
            await settle(pilot)
            await app.hardware_task
            assert isinstance(app.screen, DeploymentDialog)
            assert "serial" not in app.session.report.config.sdr.device
            app.screen.query_one("#deployment-device", Select).value = 1
            app.screen.query_one("#deployment-use").press()
            await pilot.pause()
            await settle(pilot)
            assert app.session.report.config.sdr.device == {"type": "b200", "serial": "USB-A"}
            assert not app.controller.active

    asyncio.run(exercise())
