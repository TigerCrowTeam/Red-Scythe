"""Keyboard-driven save, reopen, conflict recovery and explicit peer export."""

import asyncio

import pytest

pytest.importorskip("textual")
from textual.widgets import Input, Static

from packet_radio.config import load_config, validate_network
from packet_radio.configuration.forms import FormSession
from packet_radio.tui.app import RadioEditor
from packet_radio.tui.dialogs import PathDialog, PeerDialog
from packet_radio.tui.widgets import field_id


async def settle(pilot):
    for _ in range(100):
        await pilot.pause()
        if pilot.app.ready:
            return
    raise AssertionError("form did not finish mounting")


def test_keyboard_save_reopen_and_external_edit_conflict(tmp_path):
    async def exercise():
        target = tmp_path / "project/node.yaml"
        app = RadioEditor()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert isinstance(app.screen, PathDialog)
            app.screen.query_one(Input).value = str(target)
            await pilot.press("enter")
            await settle(pilot)
            assert target.exists() and not app.session.unsaved
            expected = app.session.report.config
            app.open_project(str(target))
            await settle(pilot)
            assert app.session.report.config == expected == load_config(target)
            await app.apply_text("profile.frequency.center_frequency_hz", "916 MHz")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert load_config(target).center_frequency_hz == 916e6
            assert not app.session.unsaved
            target.write_bytes(target.read_bytes() + b"\n# external edit\n")
            external = target.read_bytes()
            await app.apply_text("profile.frequency.center_frequency_hz", "917 MHz")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert target.read_bytes() == external
            assert app.session.unsaved
            assert "Save failed" in str(app.query_one("#summary", Static).render())

    asyncio.run(exercise())


def test_invalid_pending_input_blocks_save(tmp_path):
    async def exercise():
        app = RadioEditor()
        async with app.run_test() as pilot:
            await settle(pilot)
            field = app.query_one("#" + field_id("profile.waveform.bit_rate_bps"), Input)
            field.value = "nan"
            await pilot.pause()
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert not isinstance(app.screen, PathDialog)
            assert app.errors
            await app.save_to(str(tmp_path / "invalid.yaml"))
            assert not (tmp_path / "invalid.yaml").exists()

    asyncio.run(exercise())


def test_peer_dialog_exports_complementary_configuration(tmp_path):
    async def exercise():
        app = RadioEditor(FormSession.new())
        async with app.run_test(size=(100, 45)) as pilot:
            await settle(pilot)
            local = app.session.report.config
            await pilot.press("f6")
            await settle(pilot)
            assert isinstance(app.screen, PeerDialog)
            app.screen.query_one("#peer-path", Input).value = str(tmp_path / "peer.yaml")
            app.screen.query_one("#peer-identifier", Input).value = "192.168.10.2"
            app.screen.query_one("#peer-export").press()
            await settle(pilot)
            peer = load_config(tmp_path / "peer.yaml")
            validate_network({"local": local, "peer": peer})
            assert peer.node_id == local.peer_id and peer.peer_id == local.node_id
            assert app.session.report.config == local
            assert app.session.unsaved

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "failure", [PermissionError("read-only destination"), OSError("disk full")]
)
def test_failed_save_preserves_draft_and_previous_file_then_recovers(
    tmp_path, monkeypatch, failure
):
    from packet_radio.configuration import storage
    from packet_radio.configuration.storage import save_project

    async def exercise():
        saved = save_project(FormSession.new().draft, tmp_path / "node.yaml")
        original = saved.node_path.read_bytes()
        app = RadioEditor(FormSession(saved))
        async with app.run_test() as pilot:
            await settle(pilot)
            await app.apply_text("profile.frequency.center_frequency_hz", "916 MHz")
            publish = storage._publish

            def fail(*args, **kwargs):
                raise failure

            monkeypatch.setattr(storage, "_publish", fail)
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert "Save failed" in str(app.query_one("#summary", Static).render())
            assert saved.node_path.read_bytes() == original
            assert app.session.unsaved
            assert app.session.report.config.center_frequency_hz == 916e6
            monkeypatch.setattr(storage, "_publish", publish)
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert not app.session.unsaved
            assert load_config(saved.node_path).center_frequency_hz == 916e6

    asyncio.run(exercise())


def test_missing_profile_open_preserves_current_project(tmp_path):
    from packet_radio.configuration.storage import save_project

    async def exercise():
        saved = save_project(FormSession.new().draft, tmp_path / "node.yaml")
        for path in saved.source_versions:
            if path != saved.node_path:
                path.unlink()
        app = RadioEditor()
        async with app.run_test() as pilot:
            await settle(pilot)
            original = app.session
            app.open_project(str(saved.node_path))
            await settle(pilot)
            assert app.session is original
            assert "Cannot open project" in str(app.query_one("#summary", Static).render())
            assert app.session.report.valid

    asyncio.run(exercise())


def test_profile_field_replacement_failure_preserves_old_settings(tmp_path):
    from packet_radio.configuration.forms import template_root
    from tests.runtime.test_controller import project

    async def exercise():
        app = RadioEditor(FormSession(project(tmp_path)))
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(pilot)
            before = app.session.report.config
            field = app.query_one("#" + field_id("node.profile"), Input)
            field.value = str(tmp_path / "missing-profile.yaml")
            field.focus()
            await pilot.press("enter")
            assert "node.profile" in app.errors
            assert app.session.report.config == before
            field.value = str(template_root() / "profiles/simple-2fsk-100k-v1.yaml")
            await pilot.press("enter")
            await settle(pilot)
            assert not app.errors and app.session.report.config.sdr.bit_rate_bps == 100000
            assert app.session.unsaved
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert not app.session.unsaved
            assert load_config(app.session.draft.node_path).sdr.bit_rate_bps == 100000

    asyncio.run(exercise())


def test_new_draft_discard_and_legacy_import_keep_original_file(tmp_path):
    from pathlib import Path

    from textual.widgets import Button

    from packet_radio.tui.dialogs import DiscardDialog, PathDialog
    from tests.runtime.test_controller import project

    legacy = Path("configs/examples/peer-a-bpsk.yaml").resolve()
    original = legacy.read_bytes()

    async def exercise():
        app = RadioEditor(FormSession(project(tmp_path)))
        async with app.run_test(size=(100, 35)) as pilot:
            await settle(pilot)
            await app.apply_text("profile.frequency.center_frequency_hz", "916 MHz")
            await pilot.press("ctrl+n")
            assert isinstance(app.screen, DiscardDialog)
            await pilot.press("escape")
            assert app.session.report.config.center_frequency_hz == 916e6
            await pilot.press("ctrl+n")
            app.screen.query_one("#discard", Button).press()
            await settle(pilot)
            assert app.session.report.config.center_frequency_hz == 915e6
            assert not app.pending and not app.errors
            await pilot.press("f7")
            assert isinstance(app.screen, DiscardDialog)
            app.screen.query_one("#discard", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, PathDialog)
            app.screen.query_one(Input).value = str(legacy)
            await pilot.press("enter")
            await settle(pilot)
            assert app.session.draft.read_only_source
            assert app.session.report.config.sdr.modulation == "bpsk"
            await pilot.press("ctrl+s")
            assert isinstance(app.screen, PathDialog)
            target = tmp_path / "converted.yaml"
            app.screen.query_one(Input).value = str(target)
            await pilot.press("enter")
            await settle(pilot)
            assert load_config(target).schema_version == 2
            assert legacy.read_bytes() == original

    asyncio.run(exercise())
