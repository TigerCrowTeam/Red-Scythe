"""Headless keyboard, conditional form, error recovery and small-screen tests."""

import asyncio

import pytest

pytest.importorskip("textual")
from textual.widgets import Input

from packet_radio.tui.app import DiscardDialog, RadioEditor
from packet_radio.tui.widgets import Entries, field_id


def control(app, key):
    return app.query_one("#" + field_id(key))


async def settle(pilot):
    """Wait for async form mounting, not just the first visible new control."""
    for _ in range(100):
        await pilot.pause()
        if pilot.app.ready:
            return
    raise AssertionError("form did not finish mounting")


def test_auto_payload_controls_recalculate_from_hop_rate():
    from pathlib import Path

    from packet_radio.configuration.draft import RadioDraft
    from packet_radio.configuration.forms import FormSession

    async def exercise():
        path = Path(__file__).resolve().parents[2] / "configs/v1_1/peer-a-x310-hopping.yaml"
        source = RadioDraft.load(path)
        source.set("node.runtime.hardware_timed_tx_lead_seconds", 0.008)
        app = RadioEditor(FormSession(source))
        async with app.run_test(size=(100, 40)) as pilot:
            await settle(pilot)
            control(app, "profile.packet.payload_sizing").value = "automatic"
            await settle(pilot)
            for key, value in [
                ("profile.frequency.guard_seconds", "1 ms"),
                ("derived.hop_rate", "28"),
            ]:
                widget = control(app, key)
                widget.focus()
                widget.value = value
                await pilot.press("enter")
                await settle(pilot)
            assert app.session.report.valid, app.session.report.issues
            assert app.session.report.config.endpoint.max_payload_bytes == 75
            assert "75" in str(control(app, "derived.effective_payload_bytes").render())
            assert "75 bytes" in str(app.query_one("#summary").render())
            rate = control(app, "derived.hop_rate")
            rate.focus()
            rate.value = "30"
            await pilot.press("enter")
            await settle(pilot)
            assert not app.session.report.valid
            assert "must not exceed 29 hops/s" in str(app.session.report.issues)
            rate.value = "27"
            await pilot.press("enter")
            await settle(pilot)
            assert not app.errors
            assert app.session.report.config.endpoint.max_payload_bytes == 116
            assert app.session.value("profile.packet.max_payload_bytes") == 1024

    asyncio.run(exercise())


def test_keyboard_unit_edit_validation_error_focus_and_resize():
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot)
            rate = control(app, "profile.waveform.bit_rate_bps")
            rate.focus()
            rate.scroll_visible(animate=False)
            rate.value = "125 kbit/s"
            await pilot.press("enter")
            await settle(pilot)
            assert app.session.report.config.sdr.bit_rate_bps == 125000
            rate.value = "nan"
            await pilot.press("enter")
            await settle(pilot)
            assert "profile.waveform.bit_rate_bps" in app.errors
            await pilot.press("f8")
            assert control(app, "profile.waveform.bit_rate_bps").has_focus
            rate.value = "250 kbit/s"
            await pilot.press("f5")
            await settle(pilot)
            assert not app.errors and app.session.report.valid
            await pilot.resize_terminal(48, 16)
            await pilot.press("tab", "shift+tab")
            assert app.focused is not None
            await pilot.resize_terminal(100, 32)
            assert app.session.report.valid

    asyncio.run(exercise())


def test_modulation_select_events_and_advanced_visibility():
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot)
            for mode in ("bpsk", "msk", "gmsk", "qpsk", "2fsk"):
                control(app, "profile.waveform.modulation").value = mode
                await settle(pilot)
                assert app.session.selection.modulation == mode
                assert bool(app.query("#" + field_id("profile.waveform.gaussian_bt"))) == (
                    mode == "gmsk"
                )
                assert bool(
                    app.query("#" + field_id("profile.waveform.frequency_deviation_hz"))
                ) == (mode == "2fsk")
                assert app.session.report.valid
            await pilot.press("f2")
            await settle(pilot)
            assert app.advanced
            assert not app.query("#" + field_id("profile.waveform.gaussian_bt"))
            control(app, "profile.packet.format").value = "simple-frame-v1"
            await settle(pilot)
            control(app, "node.operation").value = "receive-only"
            await settle(pilot)
            assert not app.query("#" + field_id("node.hardware.tx_gain"))

    asyncio.run(exercise())


def test_hop_table_add_remove_reorder_and_hex_error():
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(90, 30)) as pilot:
            await settle(pilot)
            await app.apply_choice("profile.frequency.mode", "hopping")
            table = control(app, "profile.frequency.frequencies_hz")
            assert isinstance(table, Entries)
            original = table.read_rows()
            table.query_one("#down-0").press()
            await settle(pilot)
            assert table.read_rows()[1] == original[0]
            await app.apply_table(table)
            assert app.session.report.valid
            table.query_one("#add-row").press()
            await settle(pilot)
            assert len(table.rows) == len(original) + 1
            await app.apply_table(table)
            assert "profile.frequency.frequencies_hz" in app.errors
            table.query_one(f"#remove-{len(original)}").press()
            await settle(pilot)
            await app.apply_table(table)
            assert not app.errors
            await app.apply_text("profile.packet.frame.sync", "abc")
            assert "profile.packet.frame.sync" in app.errors
            await app.apply_text("profile.packet.frame.sync", "00")
            await pilot.press("f8")
            await settle(pilot)
            assert control(app, "profile.packet.frame.sync").has_focus
            await app.apply_text("profile.packet.frame.sync", "a7 c3 5a 9e")
            assert not app.errors and app.session.report.valid

    asyncio.run(exercise())


def test_open_failure_retains_project_and_quit_protects_edits():
    async def exercise():
        app = RadioEditor()
        async with app.run_test() as pilot:
            await settle(pilot)
            original = app.session
            app.open_project("missing-node.yaml")
            assert app.session is original
            await app.apply_text("node.node_id", "3")
            app.action_request_quit()
            await settle(pilot)
            assert isinstance(app.screen, DiscardDialog)
            await pilot.press("escape")
            await settle(pilot)
            assert not isinstance(app.screen, DiscardDialog)

    asyncio.run(exercise())


def test_section_navigation_and_modulation_with_keyboard():
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(60, 20)) as pilot:
            await settle(pilot)
            app.query_one("#sections").value = "Radio"
            await settle(pilot)
            assert control(app, "profile.waveform.modulation").has_focus
            await pilot.press("enter", "down", "enter")
            await settle(pilot)
            assert app.session.selection.modulation == "msk"
            assert app.session.report.valid

    asyncio.run(exercise())


def test_unapplied_table_and_advanced_values_survive_rebuild():
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot)
            await app.apply_choice("profile.frequency.mode", "hopping")
            table = control(app, "profile.frequency.frequencies_hz")
            table.query_one("#cell-0-0", Input).value = "not a frequency"
            await settle(pilot)
            await pilot.press("f2")
            await settle(pilot)
            table = control(app, "profile.frequency.frequencies_hz")
            assert table.query_one("#cell-0-0", Input).value == "not a frequency"
            depth = control(app, "node.runtime.queue_size")
            depth.value = "999"
            await settle(pilot)
            await pilot.press("f2")
            await settle(pilot)
            assert app.pending["node.runtime.queue_size"] == "999"
            await app.action_validate()
            assert app.session.draft.node["runtime"]["queue_size"] == 999
            assert "profile.frequency.frequencies_hz" in app.errors

    asyncio.run(exercise())


def test_framing_fec_crc_and_rrc_selectors_filter_controls():
    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot)
            await app.apply_text("node.profile", "radio-profile.yaml")
            assert not app.errors
            control(app, "derived.length_mode").value = "fixed"
            await settle(pilot)
            assert app.query("#" + field_id("profile.packet.frame.fixed_payload_bytes"))
            assert not app.query("#" + field_id("profile.packet.frame.length_bytes"))
            control(app, "derived.length_mode").value = "variable"
            await settle(pilot)
            assert app.session.report.valid
            control(app, "profile.packet.frame.crc").value = "none"
            await settle(pilot)
            assert not app.query("#" + field_id("profile.packet.frame.crc_byte_order"))
            await pilot.press("f2")
            await settle(pilot)
            control(app, "profile.protection.scheme").value = "extended-hamming-8-4"
            await settle(pilot)
            assert app.query("#" + field_id("profile.protection.rs_parity_bytes"))
            control(app, "profile.protection.scheme").value = "none"
            await settle(pilot)
            assert not app.query("#" + field_id("profile.protection.rs_parity_bytes"))
            control(app, "profile.waveform.modulation").value = "bpsk"
            await settle(pilot)
            control(app, "derived.pulse_shaping").value = "rrc"
            await settle(pilot)
            assert app.query("#" + field_id("profile.waveform.rrc_span_symbols"))
            assert app.session.report.valid

    asyncio.run(exercise())


def test_compact_values_restore_controls_when_choices_become_available():
    from textual.widgets import Select, Static

    from packet_radio.tui.widgets import FieldRow

    async def exercise():
        app = RadioEditor()
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot)
            control(app, "profile.waveform.modulation").value = "bpsk"
            await settle(pilot)
            control(app, "profile.packet.format").value = "generic-v5"
            await settle(pilot)
            for key in ("profile.frequency.mode",):
                assert isinstance(control(app, key), Static)
                row = app.query_one("#row-" + field_id(key), FieldRow)
                assert row.has_class("compact-field")
                assert not row.query(".field-help")
            await pilot.resize_terminal(48, 16)
            await settle(pilot)
            for row in app.query(".compact-field"):
                assert not row.query(".field-help")
                assert row.query_one(".compact-label").size.width > 0
                assert row.query_one(".compact-value").size.width > 0
            control(app, "profile.waveform.modulation").value = "2fsk"
            await settle(pilot)
            assert isinstance(control(app, "profile.packet.format"), Select)
            control(app, "profile.packet.format").value = "simple-frame-v1"
            await settle(pilot)
            for key in ("profile.packet.format", "profile.frequency.mode"):
                assert isinstance(control(app, key), Select)
                row = app.query_one("#row-" + field_id(key), FieldRow)
                assert not row.has_class("compact-field")
                assert row.query(".field-help")
            assert app.session.report.valid

    asyncio.run(exercise())


def test_x310_hop_span_error_blocks_save_and_recovers():
    from packet_radio.configuration.forms import FormSession
    from tests.configuration.test_auto_payload import draft

    async def exercise():
        app = RadioEditor(FormSession(draft()))
        async with app.run_test(size=(100, 40)) as pilot:
            await settle(pilot)
            key = "profile.frequency.frequencies_hz"
            table = control(app, key)
            table.query_one("#cell-0-0", Input).value = "750 MHz"
            await app.apply_table(table)
            assert not app.session.report.valid
            assert "hop span plus signal bandwidth" in str(app.session.report.issues)
            assert not await app.valid_for_write()
            assert app.query_one("#start-radio").disabled
            table.query_one("#cell-0-0", Input).value = "914 MHz"
            await app.apply_table(table)
            assert app.session.report.valid
            assert await app.valid_for_write()

    asyncio.run(exercise())
