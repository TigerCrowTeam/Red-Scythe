"""Packet editing, preview, persistence and live-tool lifecycle without SDR hardware."""

import asyncio

import pytest

pytest.importorskip("textual")
from textual.widgets import Button, Input, Static, TextArea

from packet_radio.tui.app import RadioEditor
from packet_radio.tui.packets import PacketBuilder
from tests.tui.test_editor import settle


def test_builder_preview_edit_save_reopen_and_apply(tmp_path):
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(pilot)
            await pilot.press("ctrl+b")
            await pilot.pause()
            builder = app.screen
            assert isinstance(builder, PacketBuilder)
            assert "Invalid" not in str(builder.query_one("#packet-preview", Static).render())
            builder.query_one("#packet-payload", TextArea).load_text("changed")
            builder.query_one("#packet-frame-sync", Input).value = "d391d391"
            await pilot.pause()
            before = app.session.report.config
            template = builder.read_template()
            assert template.encode()[0] == b"changed"
            candidate = builder.candidate(template)
            assert (
                before.emulation["packet"]["frame"]["sync"]
                != candidate.report.config.emulation["packet"]["frame"]["sync"]
            )
            builder.query_one("#packet-apply", Button).press()
            await pilot.pause()
            assert app.session.report.config.emulation["packet"]["frame"]["sync"] == "d391d391"
            path = tmp_path / "packet.yaml"
            await builder.save_to(path)
            assert path.exists(), str(builder.query_one("#packet-message", Static).render())
            builder.query_one("#packet-payload", TextArea).load_text("other")
            await builder.open_template(path)
            assert builder.read_template().payload == "changed"
            builder.capture_received(b"received data")
            await builder.edit_received()
            assert builder.read_template().encode()[0] == b"received data"
            assert builder.read_template().payload_encoding == "hex"
            builder.query_one("#packet-frame-sync", Input).value = "invalid"
            await pilot.pause()
            assert "Invalid packet" in str(builder.query_one("#packet-preview", Static).render())
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert app.screen is builder
            assert builder.query_one("#packet-frame-sync", Input).value == "invalid"
            await pilot.resize_terminal(60, 24)
            await pilot.press("escape")

    asyncio.run(exercise())


def test_reordering_generic_new_and_unsaved_guard():
    from textual.widgets import Select

    from packet_radio.tui.dialogs import DiscardDialog
    from packet_radio.tui.packets import PacketField

    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(pilot)
            await app.action_packet_builder()
            await pilot.pause()
            b = app.screen
            b.query_one("#packet-add", Button).press()
            await pilot.pause()
            rows = list(b.query(PacketField))
            assert len(rows) == 2
            rows[0].query_one(".packet-name", Input).value = "trailer"
            rows[0].query_one(".packet-kind", Select).value = "crc16"
            rows[0].query_one(".packet-width", Input).value = "2"
            rows[0].query_one(".packet-down", Button).press()
            await pilot.pause()
            assert b.read_template().fields[-1]["kind"] == "crc16"
            b.query_one("#packet-open", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, DiscardDialog)
            await pilot.press("escape")
            await b.action_close()
            await app.apply_choice("profile.waveform.modulation", "qpsk")
            await app.action_packet_builder()
            await pilot.pause()
            await b.new_template()
            assert b.read_template().packet_format == "generic-v5"
            assert b.read_template().frame is None
            assert b.query_one("#packet-apply", Button).disabled
            assert "generic-v5 protocol header" in str(
                b.query_one("#packet-preview", Static).render()
            )
            b.query_one("#packet-payload", TextArea).load_text("keep me")
            await pilot.pause()
            await b.action_close()
            app.action_request_quit()
            await pilot.pause()
            assert isinstance(app.screen, DiscardDialog)
            await pilot.press("escape")

    asyncio.run(exercise())
