"""User-facing template dialogs, invalid framing and preservation after save failures."""

import asyncio

import pytest

pytest.importorskip("textual")
from textual.widgets import Button, Input, Select, TextArea

from packet_radio.packets.template import load_template
from packet_radio.tui.app import RadioEditor
from packet_radio.tui.dialogs import DiscardDialog, PathDialog
from packet_radio.tui.packets import PacketField
from tests.tui.test_editor import settle
from tests.tui.test_packet_io import message, until


def test_save_dialog_cancel_overwrite_conflict_and_save_as_recovery(tmp_path):
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+b", "ctrl+s")
            b = app.packet_builder
            assert isinstance(app.screen, PathDialog)
            await pilot.press("escape")
            assert b.path is None and not list(tmp_path.iterdir())
            await pilot.press("ctrl+s")
            first = tmp_path / "first.yaml"
            app.screen.query_one(Input).value = str(first)
            await pilot.press("enter")
            await until(first.exists)
            assert load_template(first).payload == "Hello radio"
            b.query_one("#packet-payload", TextArea).load_text("updated")
            await pilot.press("ctrl+s")
            assert load_template(first).payload == "updated"
            first.write_text("external change\n")
            b.query_one("#packet-payload", TextArea).load_text("keep my draft")
            b.query_one("#packet-save", Button).press()
            await until(lambda: "Cannot save" in message(b))
            assert first.read_text() == "external change\n"
            assert b.unsaved and b.read_template().payload == "keep my draft"
            b.query_one("#packet-save-as", Button).press()
            await until(lambda: isinstance(app.screen, PathDialog))
            second = tmp_path / "recovered.yaml"
            app.screen.query_one(Input).value = str(second)
            await pilot.press("enter")
            await until(second.exists)
            assert load_template(second).payload == "keep my draft"
            assert not b.unsaved and first.read_text() == "external change\n"

    asyncio.run(exercise())


@pytest.mark.parametrize("bad_file", ["missing", "malformed", "wrong-format"])
def test_open_failure_preserves_current_packet(tmp_path, bad_file):
    from packet_radio.configuration.forms import FormSession
    from packet_radio.packets.template import PacketTemplate, save_template

    path = tmp_path / "bad.yaml"
    if bad_file == "malformed":
        path.write_text("[unclosed")
    elif bad_file == "wrong-format":
        form = FormSession.new()
        form.change("profile.waveform.modulation", "qpsk")
        form.change("profile.packet.format", "generic-v5")
        save_template(PacketTemplate.new(form.report.config), path)

    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+b")
            b = app.screen
            original = b.read_template().document()
            b.query_one("#packet-open", Button).press()
            await until(lambda: isinstance(app.screen, PathDialog))
            app.screen.query_one(Input).value = str(path)
            await pilot.press("enter")
            await until(lambda: "Cannot open" in message(b))
            assert b.read_template().document() == original
            assert b.path is None

    asyncio.run(exercise())


def test_save_permission_failure_retains_packet_and_destination(tmp_path, monkeypatch):
    from packet_radio.tui import packets

    original_save = packets.save_template

    def denied(*args, **kwargs):
        raise PermissionError("read-only destination")

    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+b")
            b = app.screen
            b.query_one("#packet-payload", TextArea).load_text("retain this")
            await pilot.press("ctrl+s")
            target = tmp_path / "packet.yaml"
            app.screen.query_one(Input).value = str(target)
            monkeypatch.setattr(packets, "save_template", denied)
            await pilot.press("enter")
            await until(lambda: "read-only destination" in message(b))
            assert not target.exists() and b.unsaved and b.path is None
            assert b.read_template().payload == "retain this"
            monkeypatch.setattr(packets, "save_template", original_save)
            await pilot.press("ctrl+s")
            app.screen.query_one(Input).value = str(target)
            await pilot.press("enter")
            await until(target.exists)
            assert load_template(target).payload == "retain this"

    asyncio.run(exercise())


def test_invalid_fixed_framing_is_atomic_then_valid_apply_changes_limit():
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+b")
            b = app.screen
            original = app.session.report.config
            b.query_one("#packet-frame-length_bytes", Select).value = 0
            b.query_one("#packet-frame-fixed_payload_bytes", Input).value = "4"
            b.query_one("#packet-apply", Button).press()
            await until(lambda: "payload" in message(b))
            assert app.session.report.config == original
            b.query_one("#packet-payload", TextArea).load_text("four")
            b.query_one("#packet-apply", Button).press()
            await until(lambda: "Framing applied" in message(b))
            assert app.session.report.config.endpoint.max_payload_bytes == 4
            assert app.session.report.config.emulation["packet"]["frame"]["length_bytes"] == 0

    asyncio.run(exercise())


def test_removed_payload_blocks_save_and_new_requires_discard():
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+b")
            b = app.screen
            b.query_one("#packet-add", Button).press()
            await until(lambda: len(b.query(PacketField)) == 2)
            payload = list(b.query(PacketField))[1]
            payload.query_one(".packet-up", Button).press()
            await pilot.pause()
            assert b.read_template().fields[0]["kind"] == "payload"
            payload.query_one(".packet-remove", Button).press()
            await until(lambda: len(b.query(PacketField)) == 1)
            await pilot.press("ctrl+s")
            assert "Cannot save" in message(b) and app.screen is b
            b.query_one("#packet-new", Button).press()
            await until(lambda: isinstance(app.screen, DiscardDialog))
            await pilot.press("escape")
            assert b.read_template().fields[0]["kind"] == "uint"
            b.query_one("#packet-new", Button).press()
            await until(lambda: isinstance(app.screen, DiscardDialog))
            app.screen.query_one("#discard", Button).press()
            await until(lambda: app.screen is b and len(b.query(PacketField)) == 1)
            await pilot.pause()
            assert b.read_template().encode()[0] == b"Hello radio"
            assert not b.unsaved

    asyncio.run(exercise())
