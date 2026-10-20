"""Keyboard launch/stop, save gate, opt-in and owned shutdown with real fake children."""

import asyncio

import pytest

pytest.importorskip("textual")
from textual.widgets import Checkbox

from packet_radio.configuration.forms import FormSession
from packet_radio.tui.app import RadioEditor
from packet_radio.tui.run import RunDetails, SaveBeforeRunDialog, StartDialog
from tests.runtime.test_controller import controller, project, wait_state


async def settle(pilot):
    for _ in range(100):
        await pilot.pause()
        if pilot.app.ready:
            return
    raise AssertionError("form did not mount")


def test_keyboard_start_stop_restart_and_probe_exclusion(tmp_path):
    async def exercise():
        app = RadioEditor(FormSession(project(tmp_path)))
        app.controller = controller()
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+r")
            assert isinstance(app.screen, StartDialog)
            app.screen.query_one("#run-start").press()
            await pilot.pause()
            await wait_state(app.controller, "Running")
            first = app.controller.process
            await pilot.press("f9")
            assert "Stop the owned radio" in app.hardware_status
            assert app.hardware_task is None
            await pilot.press("f12")
            assert isinstance(app.screen, RunDetails)
            await pilot.press("escape")
            await app.apply_text("profile.frequency.center_frequency_hz", "916 MHz")
            assert app.controller.running_configs[0].center_frequency_hz == 915e6
            await pilot.press("ctrl+c")
            assert app.controller.state == "Stopped" and first.returncode == 0
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("ctrl+r")
            app.screen.query_one("#run-start").press()
            await pilot.pause()
            await wait_state(app.controller, "Running")
            assert app.controller.running_configs[0].center_frequency_hz == 916e6
            second = app.controller.process
        assert second.returncode == 0 and not app.controller.active

    asyncio.run(exercise())


def test_new_draft_requires_save_and_pair_dialog_cancel_does_not_start():
    async def exercise():
        app = RadioEditor()
        app.controller = controller()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("ctrl+r")
            assert isinstance(app.screen, SaveBeforeRunDialog)
            await pilot.press("escape")
            assert not app.controller.active

    asyncio.run(exercise())


def test_explicit_bench_opt_in_and_quit_stops_owned_radio(tmp_path):
    async def exercise():
        app = RadioEditor(FormSession(project(tmp_path, "bpsk")))
        app.controller = controller()
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+shift+r")
            await pilot.press("escape")
            assert not isinstance(app.screen, StartDialog)
            assert not app.controller.active
            await pilot.press("ctrl+r")
            assert isinstance(app.screen, StartDialog)
            assert app.screen.query_one("#run-start").disabled
            app.screen.query_one(Checkbox).value = True
            await pilot.pause()
            app.screen.query_one("#run-start").press()
            await pilot.pause()
            await wait_state(app.controller, "Running")
            child = app.controller.process
            await pilot.press("ctrl+q")
        assert child.returncode == 0 and not app.controller.active

    asyncio.run(exercise())


def test_run_timer_can_inspect_table_while_rows_are_rebuilt(monkeypatch):
    from textual.containers import Vertical

    from packet_radio.tui.widgets import Entries, field_id

    async def exercise():
        app = RadioEditor()
        async with app.run_test() as pilot:
            await settle(pilot)
            await app.apply_choice("profile.frequency.mode", "hopping")
            table = app.query_one("#" + field_id("profile.frequency.frequencies_hz"), Entries)
            original = Vertical.recompose
            entered, release = asyncio.Event(), asyncio.Event()

            async def delayed(widget):
                entered.set()
                await release.wait()
                await original(widget)

            monkeypatch.setattr(Vertical, "recompose", delayed)
            table.rows = table.read_rows() + [["917 MHz"]]
            task = asyncio.create_task(table.recompose())
            await entered.wait()
            assert table.read_rows()[-1] == ["917 MHz"]
            app.update_run_status()
            assert table.dirty
            release.set()
            await task
            assert not table.rebuilding

    asyncio.run(exercise())


def test_live_details_literal_logs_and_responsive_stop_during_hung_poll(tmp_path, monkeypatch):
    from textual.widgets import Static

    async def exercise():
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def hang(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr("packet_radio.runtime.monitor.fetch_status", hang)
        app = RadioEditor(FormSession(project(tmp_path)))
        app.controller = controller()
        async with app.run_test(size=(110, 42)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+r")
            app.screen.query_one("#run-start").press()
            await pilot.pause()
            await wait_state(app.controller, "Running")
            await asyncio.wait_for(entered.wait(), 1)
            app.controller.logs.append("[bold red]literal device log[/bold red]")
            await pilot.press("f12")
            assert isinstance(app.screen, RunDetails)
            widget = app.screen.query_one("#run-details", Static)
            assert widget._render_markup is False
            text = str(widget.content)
            assert app.controller.run_id in text
            assert "RF acquired / peer active / payload delivered: unknown" in text
            assert "[bold red]literal device log[/bold red]" in text
            await asyncio.wait_for(pilot.press("ctrl+t"), 3)
            assert app.controller.state == "Stopped"
            assert cancelled.is_set()
            assert all(n.stopped for n in app.controller.monitor.nodes)

    asyncio.run(exercise())


def test_start_requests_during_probe_or_existing_run_do_not_spawn(tmp_path):
    from tests.tui.test_hardware import FakeJob

    async def exercise():
        app = RadioEditor(FormSession(project(tmp_path)))
        app.controller = controller()
        app.hardware_job = FakeJob()
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(pilot)
            await pilot.press("f10")
            await app.hardware_job.started.wait()
            await pilot.press("ctrl+r")
            assert "hardware check" in app.run_notice
            assert app.controller.process is None and app.screen is app.main_screen
            await pilot.press("f11", "ctrl+r")
            app.screen.query_one("#run-start").press()
            await pilot.pause()
            await wait_state(app.controller, "Running")
            child = app.controller.process
            await pilot.press("ctrl+r")
            assert "Stop the current radio" in app.run_notice
            assert app.controller.process is child
            app.main_screen.query_one("#run-details-button").press()
            await pilot.pause()
            assert isinstance(app.screen, RunDetails)
            app.screen.query_one("#run-stop").press()
            await pilot.pause()
            assert app.controller.state == "Stopped" and child.returncode == 0

    asyncio.run(exercise())


def test_save_before_start_button_and_missing_peer_recovery(tmp_path):
    from textual.widgets import Input

    from packet_radio.tui.dialogs import PathDialog

    async def exercise():
        app = RadioEditor()
        app.controller = controller()
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(pilot)
            app.main_screen.query_one("#start-radio").press()
            await pilot.pause()
            assert isinstance(app.screen, SaveBeforeRunDialog)
            app.screen.query_one("#run-save").press()
            await pilot.pause()
            assert isinstance(app.screen, PathDialog)
            app.screen.query_one(Input).value = str(tmp_path / "saved.yaml")
            await pilot.press("enter")
            await settle(pilot)
            assert not app.session.unsaved and not app.controller.active
            app.main_screen.query_one("#start-pair").press()
            await pilot.pause()
            assert isinstance(app.screen, PathDialog)
            app.screen.query_one(Input).value = str(tmp_path / "missing-peer.yaml")
            await pilot.press("enter")
            await pilot.pause()
            assert "Cannot start" in app.run_notice and not app.controller.active
            app.main_screen.query_one("#start-radio").press()
            await pilot.pause()
            assert isinstance(app.screen, StartDialog)
            await pilot.press("escape")
            assert not app.controller.active

    asyncio.run(exercise())
