"""Keyboard-driven project editing, hardware checks and owned radio lifecycle controls.

Startup discovery and explicit hardware checks run in cancellable child processes. Project persistence
uses validated, atomic node/profile saves. Widgets delegate parsing and
radio validation to the configuration services.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Header, Input, Select, Static

from packet_radio.configuration.catalog import FIELDS, available_choices, visible_fields
from packet_radio.configuration.draft import RadioDraft
from packet_radio.configuration.forms import FormSession, format_value, lookup, parse_value
from packet_radio.configuration.preflight import (
    HardwareJob,
    signature,
    snapshot,
    validate_shared_pair,
)
from packet_radio.configuration.storage import (
    check_sources,
    export_peer,
    import_legacy,
    save_project,
)
from packet_radio.runtime.controller import RadioController
from packet_radio.tui.dialogs import DiscardDialog, PathDialog, PeerDialog
from packet_radio.tui.hardware import DeploymentDialog, HardwareDialog
from packet_radio.tui.run import RunDetails, SaveBeforeRunDialog, StartDialog, describe
from packet_radio.tui.widgets import Entries, FieldRow, field_id


class RadioEditor(App):
    TITLE = "Packet Radio · Configuration"
    COMMAND_PALETTE_BINDING = "ctrl+shift+p"
    BINDINGS = [
        ("ctrl+q", "request_quit", "Quit"),
        ("ctrl+o", "open_project", "Open"),
        ("ctrl+n", "new_project", "New"),
        ("ctrl+s", "save", "Save"),
        ("f4", "save_as", "Save As"),
        ("f6", "export_peer", "Export peer"),
        ("f7", "import_legacy", "Import v1"),
        ("f5", "validate", "Validate"),
        ("f8", "first_error", "Go to error"),
        ("f2", "advanced", "Advanced"),
        ("f9", "refresh_devices", "Refresh devices"),
        ("ctrl+d", "detect_setup", "Detect setup"),
        ("f10", "check_hardware", "Check hardware"),
        ("ctrl+p", "check_pair", "Check X310 pair"),
        ("f11", "cancel_hardware", "Cancel check"),
        ("f3", "hardware_details", "Hardware details"),
        ("ctrl+r", "start_radio", "Start radio"),
        ("ctrl+shift+r", "start_pair", "Start X310 pair"),
        Binding("ctrl+t", "stop_radio", "Stop radio", priority=True),
        Binding("ctrl+c", "stop_radio", "Stop radio", priority=True),
        ("f12", "run_details", "Run details"),
        ("ctrl+b", "packet_builder", "Packet builder"),
    ]
    CSS = """
    Screen { background: $surface; }
    #toolbar { height: 3; }
    #run-status { height: auto; max-height: 3; padding: 0 1; }
    #run-toolbar { height: 3; }
    #run-toolbar Button { min-width: 10; width: auto; }
    #run-dialog { width: 95%; height: 90%; padding: 1 2; border: round $accent; }
    #run-dialog Horizontal { height: 3; }
    #hardware-status { height: auto; max-height: 3; padding: 0 1; }
    #hardware-dialog { width: 95%; height: 90%; padding: 1 2; border: round $accent; }
    #hardware-dialog Horizontal { height: 3; }
    #sections { width: 1fr; }
    #advanced { width: auto; }
    #form { padding: 0 1; }
    .section-title { text-style: bold; color: $accent; margin-top: 1; height: 2; }
    .field-row { height: auto; margin-bottom: 1; }
    .compact-field { margin-bottom: 0; }
    .compact-value-row { height: auto; }
    .compact-label { width: 45%; height: auto; color: $text-muted; }
    .compact-value { width: 1fr; height: auto; }
    .field-label { text-style: bold; height: auto; }
    .field-help { color: $text-muted; height: auto; }
    .field-error { color: $error; height: auto; }
    .entries { height: auto; }
    .entry, .table-actions { height: 3; }
    .entry Input { width: 1fr; min-width: 5; }
    .row-button { width: 4; min-width: 4; padding: 0; }
    #summary { height: auto; max-height: 5; padding: 0 1; background: $panel; }
    #error-link { height: 3; width: 100%; }
    #banner { height: auto; max-height: 4; padding: 0 1; color: $text-muted; }
    ModalScreen { align: center middle; background: $background 80%; }
    #dialog { width: 90%; max-width: 70; height: auto; padding: 1 2; border: round $accent; }
    #dialog Horizontal { height: 3; }
    #peer-dialog { width: 90%; max-width: 80; height: 90%; padding: 1 2; border: round $accent; }
    #peer-dialog Horizontal { height: 3; }
    """

    def __init__(self, session=None, *, auto_detect=False, node_role=None):
        super().__init__()
        self.session = session or FormSession.new()
        self.auto_detect = auto_detect
        self.node_role = node_role
        self.setup_snapshot = None
        self.advanced = False
        self.pending = {}
        self.table_buffers = {}
        self.replacing_project = False
        self.errors = {}
        self.ready = False
        self.rendered_selection = None
        self.controller = RadioController()
        self.run_notice = ""
        self.packet_builder = None
        self.packet_template = None
        self.packet_template_path = None
        self.packet_template_bytes = None
        self.hardware_job = HardwareJob()
        self.hardware_task = None
        self.hardware_result = None
        self.hardware_signature = None
        self.hardware_peer = None
        self.hardware_status = (
            "Hardware unverified · F9 refresh · F10 check · Ctrl+P check X310 pair · F3 details"
        )

    @property
    def main_screen(self):
        """Keep form updates on the base screen while a dialog is active."""
        return self.screen_stack[0]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Offline draft · Enter applies a field · Modulation/format changes use preset waveform defaults. Ctrl+S saves · Ctrl+R starts saved settings · Ctrl+T stops.",
            id="banner",
            markup=False,
        )
        with Horizontal(id="toolbar"):
            yield Select(
                [("Project", "Project")], value="Project", allow_blank=False, id="sections"
            )
            yield Checkbox("Advanced", id="advanced")
        yield Static(self.hardware_status, id="hardware-status", markup=False)
        yield Static("", id="deployment-status", markup=False)
        yield Static(
            "Stopped · Ctrl+R start · Ctrl+T stop · F12 details", id="run-status", markup=False
        )
        with Horizontal(id="run-toolbar"):
            yield Button("Start", id="start-radio")
            yield Button("Start pair", id="start-pair")
            yield Button("Stop", id="stop-radio", disabled=True)
            yield Button("Run details", id="run-details-button")
            yield Button("Packets", id="packet-builder-button")
        yield VerticalScroll(id="form")
        yield Static("", id="summary", markup=False)
        yield Button("Go to first error (F8)", id="error-link")
        yield Footer()

    async def on_mount(self):
        await self.render_form()
        self.set_interval(0.25, self.update_run_status)
        if self.auto_detect:
            self.begin_setup_detection()

    async def render_form(self, focus_key=None):
        self.ready = False
        for table in () if self.replacing_project else self.main_screen.query(Entries):
            if table.dirty:
                self.table_buffers[table.key] = table.read_rows()
        self.replacing_project = False
        fields = visible_fields(self.session.selection, advanced=self.advanced)
        # Re-evaluate on every selection change; never mutate the field catalog.
        fields = tuple(
            replace(field, access="read_only")
            if field.access == "editable"
            and field.editor == "select"
            and len(available_choices(field.key, self.session.selection)) == 1
            else field
            for field in fields
        )
        keys = {f.key for f in fields}
        applicable = {f.key for f in visible_fields(self.session.selection, advanced=True)}
        self.pending = {k: v for k, v in self.pending.items() if k in applicable}
        self.errors = {k: v for k, v in self.errors.items() if k in applicable}
        self.table_buffers = {k: v for k, v in self.table_buffers.items() if k in applicable}
        form = self.main_screen.query_one("#form", VerticalScroll)
        await form.remove_children()
        sections = list(dict.fromkeys(f.section for f in fields))
        selector = self.main_screen.query_one("#sections", Select)
        current = selector.value
        with selector.prevent(Select.Changed):
            selector.set_options([(s, s) for s in sections])
            selector.value = current if current in sections else sections[0]
        for section in sections:
            await form.mount(
                Static(
                    section,
                    id="section-" + section.replace(" ", "-").replace("/", "-"),
                    classes="section-title",
                )
            )
            section_fields = sorted(
                (f for f in fields if f.section == section),
                key=lambda f: f.access != "editable",
            )
            for field in section_fields:
                hints = [] if field.help.startswith("Configure ") else [field.help]
                if field.access == "editable":
                    bounds = (
                        f"Min {format_value(field.key, field.minimum)}"
                        if field.minimum is not None
                        else ""
                    )
                    if field.maximum is not None:
                        bounds += f" Max {format_value(field.key, field.maximum)}"
                    if field.multiple_of:
                        bounds += f" · Multiple of {field.multiple_of}"
                    source = (
                        "override"
                        if field.key.startswith("profile.")
                        and lookup(self.session.origins, field.key[8:]) is not None
                        else "preset / node setting"
                    )
                    hints += [bounds]
                    if field.constraint_hint not in (
                        "Use the existing configuration/processing validator",
                        "Supported modem registry entry",
                    ):
                        hints.append(field.constraint_hint)
                    hints.append(f"Source: {source} · Requires restart")
                row = FieldRow(field, self.session, " · ".join(h for h in hints if h))
                await form.mount(row)
                if field.key in self.table_buffers:
                    table = row.query_one(Entries)
                    table.rows = copy.deepcopy(self.table_buffers[field.key])
                    await table.recompose()
                if field.key in self.pending and isinstance(
                    row.query_one("#" + field_id(field.key)), Input
                ):
                    row.query_one(Input).value = self.pending[field.key]
        self.rendered_selection = self.session.selection
        self.ready = True
        self.show_report()
        if focus_key and focus_key in keys:
            control = self.main_screen.query_one("#" + field_id(focus_key))
            control.focus()
            control.scroll_visible()

    def key_for(self, widget):
        return next((k for k in FIELDS if field_id(k) == widget.id), None)

    def on_input_changed(self, event: Input.Changed):
        if not self.ready or not event.input.is_attached:
            return
        key = self.key_for(event.input)
        if key and event.value != format_value(key, self.session.value(key)):
            self.pending[key] = event.value
            self.update_hardware_status(stale=True)
            self.sub_title = "Unapplied edits"
            self.main_screen.query_one("#summary", Static).update(
                "Unapplied edits · Press Enter or F5 to validate."
            )
        elif key:
            if key in self.pending or key in self.errors:
                self.pending.pop(key, None)
                self.errors.pop(key, None)
                self.show_report()
        elif any(isinstance(parent, Entries) and parent.dirty for parent in event.input.ancestors):
            self.update_hardware_status(stale=True)
            self.sub_title = "Unapplied edits"
            self.main_screen.query_one("#summary", Static).update(
                "Unapplied table edits · Apply rows or press F5."
            )

    async def on_input_submitted(self, event: Input.Submitted):
        key = self.key_for(event.input)
        if key:
            await self.apply_text(key, event.value)

    async def apply_text(self, key, text):
        try:
            if key == "node.profile":
                if text == self.session.value(key):
                    self.pending.pop(key, None)
                    self.errors.pop(key, None)
                    self.show_report()
                    return
                source = RadioDraft(
                    self.session.draft.node,
                    self.session.draft.profile,
                    self.session.draft.node_path,
                )
                source.source_versions = self.session.draft.source_versions.copy()
                source.read_only_source = self.session.draft.read_only_source
                source.select_profile(text)
                self.session = FormSession(source)
                self.session.replaced_profile = True
                changed = True
            else:
                changed = self.session.change(key, parse_value(key, text))
            self.pending.pop(key, None)
            self.errors.pop(key, None)
            if changed:
                await self.render_form(key)
            else:
                self.show_report()
        except (ValueError, OSError) as exc:
            self.errors[key] = str(exc)
            self.show_report()

    async def on_select_changed(self, event: Select.Changed):
        if not self.ready or not event.select.is_attached or event.value is Select.BLANK:
            return
        if event.select.id == "sections":
            self.main_screen.query_one(
                "#section-" + str(event.value).replace(" ", "-").replace("/", "-")
            ).scroll_visible(top=True)
            for row in self.main_screen.query(FieldRow):
                if row.field.section == event.value and row.field.access == "editable":
                    control = row.query_one("#" + field_id(row.field.key))
                    if isinstance(control, Entries):
                        control.query_one("Button").focus()
                    else:
                        control.focus()
                    row.scroll_visible(top=True, animate=False)
                    break
            return
        key = self.key_for(event.select)
        if key and event.value != self.session.value(key):
            await self.apply_choice(key, event.value)

    async def on_checkbox_changed(self, event: Checkbox.Changed):
        if not self.ready or not event.checkbox.is_attached:
            return
        if event.checkbox.id == "advanced":
            self.advanced = event.value
            await self.render_form()
            return
        key = self.key_for(event.checkbox)
        if key and event.value != self.session.value(key):
            await self.apply_choice(key, event.value)

    async def apply_choice(self, key, value):
        try:
            self.session.change(key, value)
            # A capability change discards only controls no longer applicable;
            # preset waveform replacements also discard old waveform input buffers.
            if key in ("profile.waveform.modulation", "profile.packet.format"):
                self.pending = {
                    k: v for k, v in self.pending.items() if not k.startswith("profile.")
                }
                self.errors = {k: v for k, v in self.errors.items() if not k.startswith("profile.")}
            await self.render_form(key)
        except (ValueError, OSError) as exc:
            self.errors[key] = str(exc)
            self.show_report()

    async def on_entries_edited(self, event: Entries.Edited):
        await self.apply_table(event.control)

    async def apply_table(self, control):
        key = control.key
        try:
            rows = control.read_rows()
            value: Any
            if key.endswith("frequencies_hz"):
                value = [
                    parse_value("profile.frequency.center_frequency_hz", row[0]) for row in rows
                ]
            elif key.endswith("public_key_files"):
                value = {}
                for node, path in rows:
                    node_id = parse_value("node.node_id", node)
                    if not path.strip():
                        raise ValueError("Key path cannot be empty.")
                    value.setdefault(node_id, []).append(path)
            else:
                value = {}
                for name, item in rows:
                    if not name.strip() or name in value:
                        raise ValueError("Argument names must be nonempty and unique.")
                    value[name] = item
            self.session.change(key, value)
            control.initial_rows = copy.deepcopy(rows)
            self.table_buffers.pop(key, None)
            self.errors.pop(key, None)
        except ValueError as exc:
            self.errors[key] = str(exc)
        self.show_report()

    def show_report(self):
        self.update_run_status()
        self.update_hardware_status()
        report = self.session.report
        issues = dict(self.errors)
        for issue in report.issues:
            for key in issue.fields:
                issues.setdefault(key, issue.message)
        for row in self.main_screen.query(FieldRow):
            if row.field.key in {
                "profile.waveform.bit_rate_bps",
                "profile.waveform.sample_rate_hz",
                "profile.waveform.bandwidth_hz",
                "profile.waveform.frequency_deviation_hz",
                "derived.hop_rate",
            }:
                limits = self.session.draft.limits(row.field.key, report=report)
                parts = []
                for value, inclusive, symbol in (
                    (limits.minimum, limits.minimum_inclusive, ">"),
                    (limits.maximum, limits.maximum_inclusive, "<"),
                ):
                    if value is not None:
                        parts.append(
                            symbol
                            + ("=" if inclusive else "")
                            + " "
                            + format_value(row.field.key, value)
                        )
                hint = (
                    "Software bounds: " + ", ".join(parts)
                    if parts
                    else "Dependent bounds unavailable until valid."
                )
                row.query_one("#help-" + field_id(row.field.key), Static).update(
                    row.hint + " · " + hint
                )
            error = row.query_one("#error-" + field_id(row.field.key), Static)
            error.update("Error: " + issues[row.field.key] if row.field.key in issues else "")
            error.display = row.field.key in issues
            if row.field.access != "editable" and (
                row.field.key.startswith("derived.") or row.field.key == "profile.qualification"
            ):
                row.query_one("#" + field_id(row.field.key), Static).update(
                    format_value(row.field.key, self.session.value(row.field.key))
                    or "Unavailable until valid"
                )
        messages = list(self.errors.values()) + [i.message for i in report.issues]
        text = (
            "Error: " + " | ".join(messages)
            if messages
            else "Unapplied edits · Press Enter or F5."
            if self.pending
            or self.table_buffers
            or any(table.dirty for table in self.main_screen.query(Entries))
            else "Software valid · See hardware status above"
        )
        if (
            report.derived
            and not messages
            and not self.pending
            and not self.table_buffers
            and not any(table.dirty for table in self.main_screen.query(Entries))
        ):
            text += (
                f" · Maximum payload {report.derived.effective_payload_bytes} bytes"
                f" · Maximum packet {report.derived.maximum_airtime_seconds * 1000:.3f} ms"
            )
        self.main_screen.query_one("#summary", Static).update(text)
        self.main_screen.query_one("#error-link", Button).display = bool(messages)
        self.sub_title = "Unsaved edits" if self.session.unsaved or self.pending else "Offline"

    async def action_validate(self):
        for key, text in list(self.pending.items()):
            if FIELDS[key].visible(self.session.selection, advanced=True):
                await self.apply_text(key, text)
        for table in list(self.main_screen.query(Entries)):
            if table.dirty or table.key in self.errors or table.key in self.table_buffers:
                await self.apply_table(table)
        self.session.report = self.session.draft.validate()
        self.show_report()

    async def action_first_error(self):
        candidates = list(self.errors) + [
            key for issue in self.session.report.issues for key in issue.fields
        ]
        for key in candidates:
            if key not in FIELDS:
                continue
            if FIELDS[key].advanced and not self.advanced:
                self.advanced = True
                self.main_screen.query_one("#advanced", Checkbox).value = True
                await self.render_form()
            controls = self.main_screen.query("#" + field_id(key))
            if controls:
                controls.first().focus()
                controls.first().scroll_visible()
                return
        self.main_screen.query_one("#summary").scroll_visible()

    async def action_advanced(self):
        self.main_screen.query_one("#advanced", Checkbox).toggle()

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "packet-builder-button":
            await self.action_packet_builder()
        elif event.button.id == "error-link":
            await self.action_first_error()
        elif event.button.id == "start-radio":
            await self.action_start_radio()
        elif event.button.id == "start-pair":
            await self.action_start_pair()
        elif event.button.id == "stop-radio":
            await self.action_stop_radio()
        elif event.button.id == "run-details-button":
            self.action_run_details()

    def guarded(self, callback):
        if (
            self.session.unsaved
            or self.pending
            or self.table_buffers
            or any(table.dirty for table in self.main_screen.query(Entries))
        ):
            self.push_screen(DiscardDialog(), lambda discard: callback() if discard else None)
        else:
            callback()

    def action_request_quit(self):
        if self.packet_builder is not None and self.packet_builder.unsaved:
            self.push_screen(
                DiscardDialog(), lambda discard: self.guarded(self.exit) if discard else None
            )
        else:
            self.guarded(self.exit)

    def action_new_project(self):
        self.guarded(self.new_project)

    def new_project(self):
        self.session = FormSession.new()
        self.replacing_project = True
        self.pending.clear()
        self.table_buffers.clear()
        self.errors.clear()
        self.call_later(self.render_form)

    def action_open_project(self):
        self.guarded(lambda: self.push_screen(PathDialog(), self.open_project))

    def open_project(self, path):
        if not path:
            return
        try:
            self.session = FormSession(RadioDraft.load(path))
            self.replacing_project = True
            self.pending.clear()
            self.table_buffers.clear()
            self.errors.clear()
            self.call_later(self.render_form)
        except (OSError, ValueError) as exc:
            self.main_screen.query_one("#summary", Static).update(
                "Cannot open project: " + str(exc)
            )

    async def valid_for_write(self):
        await self.action_validate()
        if self.errors or self.pending or not self.session.report.valid:
            await self.action_first_error()
            return False
        return True

    async def action_save(self):
        if not await self.valid_for_write():
            return
        from packet_radio.configuration.forms import template_root

        draft = self.session.draft
        if (
            draft.read_only_source
            or draft.node_path not in draft.source_versions
            or draft.node_path.is_relative_to(template_root())
        ):
            await self.action_save_as()
        else:
            await self.save_to(str(draft.node_path))

    async def action_save_as(self):
        if await self.valid_for_write():
            self.push_screen(
                PathDialog(
                    "Save node as · YAML formatting/comments are normalized. Existing unrelated files are never overwritten.",
                    "Save",
                    str(Path.cwd() / "radio-node.yaml"),
                ),
                self.save_to,
            )

    async def save_to(self, path):
        if not path or not await self.valid_for_write():
            return
        try:
            saved = save_project(self.session.draft, path)
            self.session = FormSession(saved)
            self.replacing_project = True
            self.pending.clear()
            self.table_buffers.clear()
            self.errors.clear()
            await self.render_form()
            self.main_screen.query_one("#summary", Static).update(
                f"Saved: {saved.node_path} · Software valid · See hardware status above"
            )
        except (OSError, ValueError) as exc:
            self.main_screen.query_one("#summary", Static).update("Save failed: " + str(exc))

    async def action_export_peer(self):
        if await self.valid_for_write():
            self.push_screen(PeerDialog(self.session.report.config), self.export_to)

    async def export_to(self, result):
        if result is None or not await self.valid_for_write():
            return
        try:
            path, options = result
            exported = export_peer(self.session.draft, path, options)
            self.main_screen.query_one("#summary", Static).update(
                f"Peer exported: {exported.node_path} · Check device, key and data paths on its host before running."
            )
        except (OSError, ValueError) as exc:
            self.main_screen.query_one("#summary", Static).update("Export failed: " + str(exc))

    def action_import_legacy(self):
        self.guarded(
            lambda: self.push_screen(
                PathDialog(
                    "Import schema-1 as a new schema-2 draft · Original stays read-only", "Import"
                ),
                self.open_legacy,
            )
        )

    def open_legacy(self, path):
        if not path:
            return
        try:
            self.session = FormSession(import_legacy(path))
            self.replacing_project = True
            self.pending.clear()
            self.table_buffers.clear()
            self.errors.clear()
            self.call_later(self.render_form)
        except (OSError, ValueError) as exc:
            self.main_screen.query_one("#summary", Static).update("Import failed: " + str(exc))

    def update_hardware_status(self, stale=False):
        if not self.screen_stack or not self.main_screen.query("#hardware-status"):
            return
        if self.hardware_signature is not None:
            current = signature(self.session.draft)
            stale = stale or bool(self.pending or self.table_buffers or self.errors)
            stale = stale or any(table.dirty for table in self.main_screen.query(Entries))
            stale = stale or current != self.hardware_signature
            if self.hardware_peer is not None:
                try:
                    check_sources(self.hardware_peer)
                except (ValueError, OSError):
                    stale = True
            if stale:
                self.hardware_status = (
                    "Hardware result stale · Settings changed; run Check Hardware again."
                )
            elif self.hardware_result and "nodes" in self.hardware_result:
                self.hardware_status = (
                    "Hardware compatible at check time · RF performance unmeasured · F3 details"
                )
                if self.hardware_result["warnings"]:
                    self.hardware_status += " · Device adjustments reported"
        self.main_screen.query_one("#hardware-status", Static).update(self.hardware_status)

    def start_hardware_job(self, request, checked_signature=None, peer=None):
        if self.controller.active:
            self.hardware_signature = None
            self.hardware_status = "Stop the owned radio before discovering or probing hardware."
            self.update_hardware_status()
            return
        if self.hardware_task is not None and not self.hardware_task.done():
            return
        self.hardware_signature = None
        self.hardware_result = None
        self.hardware_peer = peer
        self.hardware_status = (
            "Checking hardware… F11 cancels. Do not probe a device used by another radio."
        )
        self.update_hardware_status()
        self.hardware_task = asyncio.create_task(self.run_hardware_job(request, checked_signature))

    async def run_hardware_job(self, request, checked_signature):
        try:
            self.hardware_result = await self.hardware_job.run(request)
            self.hardware_signature = checked_signature
            self.hardware_status = (
                f"Discovery complete: {len(self.hardware_result['devices'])} device(s) · F3 to select/view"
                if "devices" in self.hardware_result
                else "Hardware compatible at check time · F3 details"
            )
            if request.get("setup"):
                await self.show_detected_setup(self.hardware_result.get("devices", []))
        except asyncio.CancelledError:
            self.hardware_status = "Hardware check cancelled · Hardware unverified"
        except (ValueError, OSError, RuntimeError) as exc:
            self.hardware_result = {"error": str(exc)}
            self.hardware_status = "Hardware check failed: " + str(exc)
        self.update_hardware_status()

    def action_refresh_devices(self):
        self.start_hardware_job({"action": "discover"})

    def action_detect_setup(self):
        self.guarded(self.begin_setup_detection)

    def begin_setup_detection(self):
        self.setup_snapshot = snapshot(self.session.draft)
        self.start_hardware_job({"action": "discover", "setup": True})

    def setup_unchanged(self):
        return (
            self.setup_snapshot == snapshot(self.session.draft)
            and not self.pending
            and not self.table_buffers
            and not self.controller.active
            and not any(t.dirty for t in self.main_screen.query(Entries))
        )

    async def show_detected_setup(self, devices):
        from packet_radio.configuration.deployment import available_deployments

        if not self.setup_unchanged():
            raise ValueError(
                "Draft changed during discovery. Use Detect setup again; edits were preserved."
            )
        choices = available_deployments(devices)
        if not choices:
            raise ValueError(
                "No identified B205 or X310 found. Connect a radio and use Ctrl+D; offline editing remains available."
            )
        if len(choices) == 1 and (choices[0].mode == "shared-x310" or self.node_role):
            await self.use_detected_setup((choices[0], self.node_role))
        else:
            self.push_screen(DeploymentDialog(choices, self.node_role), self.use_detected_setup)

    async def use_detected_setup(self, choice):
        if choice is None:
            return
        if not self.setup_unchanged():
            self.hardware_status = (
                "Setup was not applied because the draft changed; use Ctrl+D again."
            )
            self.update_hardware_status()
            return
        from packet_radio.configuration.deployment import new_deployment

        selected, role = choice
        try:
            self.session = FormSession(new_deployment(selected, role))
        except ValueError as exc:
            self.hardware_status = str(exc)
            self.update_hardware_status()
            return
        self.replacing_project = True
        self.pending.clear()
        self.errors.clear()
        self.table_buffers.clear()
        self.hardware_status = (
            f"Detected {selected.model} · {selected.mode} · Save this draft before starting"
        )
        await self.render_form()

    async def action_check_hardware(self):
        if await self.valid_for_write():
            self.start_hardware_job(
                {"action": "probe", "drafts": [snapshot(self.session.draft)]},
                signature(self.session.draft),
            )

    def action_check_pair(self):
        self.push_screen(
            PathDialog("Select the other schema-2 node for a shared X310 preflight", "Check pair"),
            self.check_pair,
        )

    async def check_pair(self, path):
        if not path or not await self.valid_for_write():
            return
        try:
            peer = RadioDraft.load(path)
            report = peer.validate()
            if not report.valid:
                raise ValueError(report.issues[0].message)
            assert self.session.report.config is not None and report.config is not None
            validate_shared_pair([self.session.report.config, report.config])
            self.start_hardware_job(
                {"action": "probe", "drafts": [snapshot(self.session.draft), snapshot(peer)]},
                signature(self.session.draft),
                peer,
            )
        except (ValueError, OSError) as exc:
            self.hardware_signature = None
            self.hardware_result = {"error": str(exc)}
            self.hardware_status = "Pair check failed: " + str(exc)
            self.update_hardware_status()

    async def action_cancel_hardware(self):
        if self.hardware_task is not None and not self.hardware_task.done():
            self.hardware_task.cancel()
            await self.hardware_task

    async def on_unmount(self):
        await self.action_cancel_hardware()
        await self.controller.stop()

    def action_hardware_details(self):
        self.update_hardware_status()
        if self.hardware_result is not None:
            self.push_screen(
                HardwareDialog(self.hardware_result, self.hardware_status), self.select_hardware
            )

    async def select_hardware(self, device):
        if device is None:
            return
        arguments = self.session.draft.node["hardware"]["device"]
        for key in ("type", "addr", "serial", "name"):
            arguments.pop(key, None)
        arguments.update({k: device[k] for k in ("type", "addr", "serial") if device.get(k)})
        if arguments.get("type") not in ("b200", "x300"):
            self.hardware_status = "This editor supports B205 (b200) and X310 (x300) devices."
            self.update_hardware_status()
            return
        if arguments.get("type") == "x300" and arguments.get("addr"):
            arguments.pop("serial", None)
        self.session.draft.set("node.hardware.device", arguments)
        self.session.report = self.session.draft.validate()
        for table in self.main_screen.query(Entries):
            if table.key == "node.hardware.device":
                table.initial_rows = table.read_rows()
        for key in list(self.pending):
            if key.startswith("node.hardware.device"):
                self.pending.pop(key)
        for key in list(self.errors):
            if key.startswith("node.hardware.device"):
                self.errors.pop(key)
        self.table_buffers.pop("node.hardware.device", None)
        await self.render_form()

    def update_run_status(self):
        if not self.ready or not self.screen_stack or not self.main_screen.query("#run-status"):
            return
        c = self.controller
        config = self.session.report.config
        if config is not None:
            device_type = config.sdr.device.get("type")
            mode = (
                "X310 · Start runs both local simple-frame channels"
                if device_type == "x300" and config.packet_format == "simple-frame-v1"
                else "B205 · Start runs this computer's node; the other computer communicates over RF"
                if device_type == "b200"
                else "Manual setup · Use Start pair for a saved X310 peer"
            )
            self.main_screen.query_one("#deployment-status", Static).update(mode)
        if not c.active and self.hardware_status.startswith("Radio owns the device"):
            self.hardware_status = (
                "Hardware unverified after radio shutdown · F9 refresh · F10 check"
            )
            self.update_hardware_status()
        summary = f"{c.state} · {self.run_notice or c.error}"
        if c.active and c.running_configs:
            summary += " · " + describe(c.running_configs).replace("\n", " · ")
            summary += " · Draft edits require stop/restart"
        self.main_screen.query_one("#run-status", Static).update(summary)
        invalid = not self.session.report.valid or bool(
            self.pending or self.errors or self.table_buffers
        )
        invalid = invalid or any(t.dirty for t in self.main_screen.query(Entries))
        busy = c.active or (self.hardware_task is not None and not self.hardware_task.done())
        for identifier in ("start-radio", "start-pair"):
            self.main_screen.query_one("#" + identifier, Button).disabled = busy or invalid
        self.main_screen.query_one("#stop-radio", Button).disabled = not c.active

    async def ready_to_start(self):
        if self.controller.active:
            self.run_notice = "Stop the current radio before starting another."
            self.update_run_status()
            return False
        if self.hardware_task is not None and not self.hardware_task.done():
            self.run_notice = "Cancel or finish the hardware check before starting."
            self.update_run_status()
            return False
        if not await self.valid_for_write():
            return False
        if self.session.unsaved:
            self.push_screen(SaveBeforeRunDialog(), self.save_before_run)
            return False
        return True

    async def save_before_run(self, save):
        if save:
            await self.action_save()
            self.run_notice = "Save the draft, then choose Start again."
            self.update_run_status()

    async def action_start_radio(self):
        if await self.ready_to_start():
            config = self.session.report.config
            auto_pair = (
                config is not None
                and config.sdr.device.get("type") == "x300"
                and config.packet_format == "simple-frame-v1"
            )
            self.offer_start(auto_pair=auto_pair)

    async def action_start_pair(self):
        if await self.ready_to_start():
            self.push_screen(
                PathDialog("Choose the saved peer node for shared X310 operation", "Continue"),
                lambda path: self.offer_start(path) if path else None,
            )

    def offer_start(self, peer_path=None, *, auto_pair=False):
        # A cancelled pair dialog must not accidentally launch a single node.
        try:
            check_sources(self.session.draft)
            drafts = [self.session.draft]
            if peer_path:
                drafts.append(RadioDraft.load(peer_path))
            if auto_pair:
                from packet_radio.configuration.deployment import shared_peer

                drafts.append(
                    shared_peer(
                        self.session.draft,
                        self.session.draft.node_path.with_name("generated-peer.yaml"),
                    )
                )
            configs = []
            for draft in drafts:
                report = draft.validate()
                if not report.valid:
                    raise ValueError(report.issues[0].message)
                configs.append(report.config)
            if len(drafts) == 2:
                validate_shared_pair(configs)
            self.push_screen(
                StartDialog(configs),
                lambda opt_in: self.launch_radio(
                    drafts[:1] if auto_pair else drafts, opt_in, auto_pair=auto_pair
                ),
            )
        except (ValueError, OSError) as exc:
            self.run_notice = "Cannot start: " + str(exc)
            self.update_run_status()

    def launch_radio(self, drafts, opt_in, *, auto_pair=False):
        if opt_in is None:
            return
        if self.hardware_task is not None and not self.hardware_task.done():
            self.run_notice = "Finish the hardware check before starting."
            return
        try:
            self.controller.start(
                drafts, allow_unauthenticated=opt_in, **({"shared_pair": True} if auto_pair else {})
            )
            self.run_notice = ""
            self.hardware_signature = None
            self.hardware_result = None
            self.hardware_status = "Radio owns the device · Hardware probing disabled until stopped"
            self.update_hardware_status()
        except ValueError as exc:
            self.run_notice = str(exc)
        self.update_run_status()

    async def action_stop_radio(self):
        self.run_notice = ""
        await self.controller.stop()
        self.update_run_status()

    def action_run_details(self):
        self.push_screen(RunDetails(self.controller), self.stop_from_details)

    async def stop_from_details(self, stop):
        if stop:
            await self.action_stop_radio()

    async def action_packet_builder(self):
        from packet_radio.tui.packets import PacketBuilder

        if self.screen is self.packet_builder:
            return
        if await self.valid_for_write():
            if self.packet_builder is None:
                self.packet_builder = PacketBuilder(self)
                self.install_screen(self.packet_builder, "packet-builder")
            self.push_screen("packet-builder")
