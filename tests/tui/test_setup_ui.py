"""Operating setup and portable export are explicit, saved and RF-independent."""

import asyncio
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")
from textual.widgets import Input, Select, Static

from packet_radio.config import load_config
from packet_radio.configuration.forms import FormSession
from packet_radio.configuration.storage import save_project
from packet_radio.runtime.monitor import NodeStatus
from packet_radio.tui.app import RadioEditor
from packet_radio.tui.setup import SetupDialog
from packet_radio.tui.telemetry import connection_summary
from tests.configuration.test_bundle import source
from tests.tui.test_hardware import settle


def test_setup_preset_role_and_save_gate(tmp_path):
    async def exercise():
        saved = save_project(source(preset="conservative"), tmp_path / "node.yaml")
        app = RadioEditor(FormSession(saved))
        async with app.run_test(size=(110, 45)) as pilot:
            await settle(pilot)
            await app.action_configure_setup()
            await pilot.pause()
            assert isinstance(app.screen, SetupDialog)
            app.screen.query_one("#setup-preset", Select).value = "b205-bench"
            app.screen.query_one("#setup-role", Select).value = "b"
            app.screen.query_one("#setup-apply").press()
            await pilot.pause()
            await settle(pilot)
            cfg = app.session.report.config
            assert (cfg.node_id, cfg.peer_id) == (2, 1)
            assert (cfg.sdr.tx_gain, cfg.sdr.rx_gain) == (20, 40)
            assert app.session.unsaved and not app.controller.active
            assert load_config(saved.node_path).sdr.tx_gain == 0
            await app.save_to(str(saved.node_path))
            assert not app.session.unsaved
            assert load_config(saved.node_path).sdr.tx_gain == 20

    asyncio.run(exercise())


def test_switch_mode_clears_identifier_and_removes_b205_preset():
    async def exercise():
        app = RadioEditor(FormSession(source()))
        async with app.run_test() as pilot:
            await settle(pilot)
            await app.action_configure_setup()
            await pilot.pause()
            app.screen.query_one("#setup-preset", Select).value = "b205-bench"
            app.screen.query_one("#setup-mode", Select).value = "shared-x310"
            await pilot.pause()
            assert app.screen.query_one("#setup-identifier", Input).value == ""
            assert app.screen.query_one("#setup-preset", Select).value == "keep"
            app.screen.query_one("#setup-apply").press()
            await pilot.pause()
            assert isinstance(app.screen, SetupDialog)
            app.screen.query_one("#setup-identifier", Input).value = "192.168.10.2"
            app.screen.query_one("#setup-apply").press()
            await pilot.pause()
            await settle(pilot)
            assert app.session.report.config.sdr.device == {"type": "x300", "addr": "192.168.10.2"}
            assert app.session.unsaved and not app.controller.active

    asyncio.run(exercise())


def test_bundle_export_from_gui_and_existing_folder_error(tmp_path):
    async def exercise():
        app = RadioEditor(FormSession(source()))
        async with app.run_test() as pilot:
            await settle(pilot)
            await app.action_export_bundle()
            await pilot.pause()
            app.screen.query_one("#bundle-path", Input).value = str(tmp_path)
            app.screen.query_one("#bundle-serial", Input).value = "REMOTE"
            app.screen.query_one("#bundle-export").press()
            await pilot.pause()
            assert "new folder" in str(app.screen.query_one("#bundle-error", Static).render())
            app.screen.query_one("#bundle-path", Input).value = str(tmp_path / "pair")
            app.screen.query_one("#bundle-export").press()
            await pilot.pause()
            assert load_config(tmp_path / "pair/computer-b.yaml").sdr.device["serial"] == "REMOTE"
            assert not app.controller.active

    asyncio.run(exercise())


def test_peer_indicator_never_uses_stale_or_stopped_activity():
    node = NodeStatus(
        1,
        0,
        True,
        snapshot={"radio_ready": True, "peer_active": True},
        received_at=time.monotonic(),
        error="",
    )
    controller = SimpleNamespace(
        active=True, state="Running", monitor=SimpleNamespace(nodes=[node])
    )
    assert "recent RF packets received" in connection_summary(controller)
    node.snapshot["peer_active"] = False
    assert "peer may be idle" in connection_summary(controller)
    node.snapshot["peer_active"] = True
    node.received_at -= 10
    assert "unknown" in connection_summary(controller)
    controller.active = False
    assert "not monitored" in connection_summary(controller)
