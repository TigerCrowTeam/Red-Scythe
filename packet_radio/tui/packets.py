"""Terminal packet builder: typed fields, framing, previews, templates and UDP tools.

Drafts remain in memory across screen closes. Radio framing is applied explicitly
and transactionally; packet actions always validate against frozen running settings.
"""

from __future__ import annotations

import asyncio
import copy
from collections import deque
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, TextArea

from packet_radio.configuration.catalog import FIELDS
from packet_radio.configuration.forms import parse_value
from packet_radio.packets.template import KINDS, PacketTemplate, load_template, save_template
from packet_radio.packets.transport import receive, submit
from packet_radio.tui.dialogs import DiscardDialog, PathDialog


class PacketField(Vertical):
    def __init__(self, field):
        super().__init__(classes="packet-field")
        self.field = field

    def compose(self):
        f = self.field
        with Horizontal():
            yield Input(f["name"], placeholder="Field name", classes="packet-name")
            yield Select(
                [(k, k) for k in KINDS], value=f["kind"], allow_blank=False, classes="packet-kind"
            )
            yield Button("↑", classes="packet-up")
            yield Button("↓", classes="packet-down")
            yield Button("Remove", classes="packet-remove")
        with Horizontal():
            yield Input(str(f["width"]), placeholder="Width in bytes", classes="packet-width")
            yield Select(
                [("Big endian", "big"), ("Little endian", "little")],
                value=f["order"],
                allow_blank=False,
                classes="packet-order",
            )
            yield Input(
                f["value"],
                placeholder="Value (automatic for payload/length/CRC)",
                classes="packet-value",
            )

    def on_mount(self):
        self.update_controls()

    def update_controls(self):
        kind = self.query_one(".packet-kind", Select).value
        self.query_one(".packet-value", Input).disabled = kind in (
            "payload",
            "length",
            "crc16",
            "crc32",
        )
        self.query_one(".packet-width", Input).disabled = kind == "payload"
        self.query_one(".packet-order", Select).disabled = kind in ("payload", "hex", "text")

    def on_select_changed(self, event):
        if "packet-kind" in event.select.classes:
            width = self.query_one(".packet-width", Input)
            if event.value in ("payload", "crc16", "crc32"):
                width.value = {"payload": "0", "crc16": "2", "crc32": "4"}[event.value]
            elif width.value == "0":
                width.value = "1"
            self.update_controls()

    def read(self):
        return dict(
            name=self.query_one(".packet-name", Input).value,
            kind=str(self.query_one(".packet-kind", Select).value),
            width=int(self.query_one(".packet-width", Input).value),
            order=str(self.query_one(".packet-order", Select).value),
            value=self.query_one(".packet-value", Input).value,
        )


class PacketBuilder(ModalScreen):
    BINDINGS = [("escape", "close", "Back"), ("ctrl+s", "save_template", "Save template")]
    DEFAULT_CSS = """
    #packet-builder { width: 96%; height: 96%; border: round $accent; padding: 1; }
    #packet-builder Horizontal { height: 3; }
    #packet-builder Button { min-width: 10; width: auto; }
    #packet-builder Input, #packet-builder Select { width: 1fr; }
    #packet-builder Static { height: auto; }
    #packet-fields, #packet-frame { height: auto; }
    .packet-field { height: 6; margin-bottom: 1; }
    #packet-payload { height: 6; }
    #packet-preview, #packet-received { height: auto; padding: 1; background: $panel; }
    #packet-message { height: auto; color: $accent; }
    .frame-label { width: 30; }
    """

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.template = copy.deepcopy(
            editor.packet_template or PacketTemplate.new(editor.session.report.config)
        )
        self.path = editor.packet_template_path
        self.expected = editor.packet_template_bytes
        self.send_task = None
        self.receive_task = None
        self.last_received = None
        self.received: deque[str] = deque(maxlen=20)
        self.loaded = False
        self.frame_keys = []
        self.baseline = copy.deepcopy(self.template.document())

    def compose(self) -> ComposeResult:
        with Vertical(id="packet-builder"):
            yield Static(
                "Packet Builder · Application fields are carried inside the radio payload",
                markup=False,
            )
            with Horizontal():
                yield Button("New", id="packet-new")
                yield Button("Open", id="packet-open")
                yield Button("Save", id="packet-save")
                yield Button("Save as", id="packet-save-as")
                yield Button("Back", id="packet-close")
            with VerticalScroll():
                yield Input(self.template.name, placeholder="Template name", id="packet-name")
                yield Static(
                    f"Radio format: {self.template.packet_format}. Simple framing changes require Save and restart on BOTH peers.",
                    id="packet-format",
                    markup=False,
                )
                yield Static(
                    "Fields appear in wire order. Width is bytes; payload uses 0. Integers: 1/2/4/8 bytes. Length counts the complete application packet. CRC16 (2 bytes, CCITT-FALSE) / CRC32 (4 bytes, ISO-HDLC) covers preceding application bytes.",
                    markup=False,
                )
                with Vertical(id="packet-fields"):
                    for field in self.template.fields:
                        yield PacketField(field)
                yield Button("Add header field", id="packet-add")
                yield Static("Payload content · UTF-8 text or hexadecimal bytes")
                yield Select(
                    [("UTF-8 text", "text"), ("Hexadecimal", "hex")],
                    value=self.template.payload_encoding,
                    allow_blank=False,
                    id="packet-encoding",
                )
                yield TextArea(self.template.payload, id="packet-payload")
                yield Static(
                    "Radio framing · generic-v5 headers are fixed. Simple-frame fields below use the existing codec."
                )
                yield Vertical(id="packet-frame")
                yield Button(
                    "Apply framing to draft",
                    id="packet-apply",
                    disabled=self.template.frame is None,
                )
                yield Static("", id="packet-preview", markup=False)
                yield Static(
                    "Live tools · Start a radio from the main screen first. Submitting UDP does not confirm RF delivery. Hop-boundary admission can drop packets."
                )
                yield Select(
                    [("Local node", 0), ("Peer node (shared pair)", 1)],
                    value=0,
                    allow_blank=False,
                    id="packet-node",
                )
                yield Static(
                    "Repeat count (1..10000) / interval in seconds (0.01..3600, also limited by airtime)"
                )
                with Horizontal():
                    yield Input("1", placeholder="Count (1..10000)", id="packet-count")
                    yield Input(
                        "0.2", placeholder="Interval seconds (0.01..3600)", id="packet-interval"
                    )
                with Horizontal():
                    yield Button("Send once", id="packet-once")
                    yield Button("Send repeat", id="packet-repeat")
                    yield Button("Listen", id="packet-listen")
                    yield Button("Cancel I/O", id="packet-cancel")
                yield Button("Edit last received packet", id="packet-edit-rx")
                yield Static(
                    "Last 20 received application packets; listener uses the selected node's UDP output port exclusively.",
                    id="packet-received",
                    markup=False,
                )
            yield Static("", id="packet-message", markup=False)

    async def on_mount(self):
        await self.render_frame()
        self.loaded = True
        self.refresh_preview()

    async def render_frame(self):
        area = self.query_one("#packet-frame", Vertical)
        await area.remove_children()
        self.frame_keys = []
        for key, value in (self.template.frame or {}).items():
            if key in ("whitening", "differential"):
                continue
            self.frame_keys.append(key)
            row = Horizontal()
            await area.mount(row)
            choices = FIELDS["profile.packet.frame." + key].choices
            if key == "length_bytes":
                choices = (0, 1, 2, 4)
            control = (
                Select(
                    [(str(choice), choice) for choice in choices],
                    value=value,
                    allow_blank=False,
                    id="packet-frame-" + key,
                )
                if choices
                else Input("" if value is None else str(value), id="packet-frame-" + key)
            )
            await row.mount(Static(key.replace("_", " "), classes="frame-label"), control)

    def read_template(self):
        frame = copy.deepcopy(self.template.frame)
        if frame is not None:
            for key in self.frame_keys:
                control = self.query_one("#packet-frame-" + key)
                if isinstance(control, Select):
                    frame[key] = control.value
                    continue
                text = control.value
                if key == "fixed_payload_bytes" and not text.strip():
                    frame[key] = None
                else:
                    frame[key] = parse_value("profile.packet.frame." + key, text)
        return PacketTemplate(
            self.query_one("#packet-name", Input).value,
            self.template.packet_format,
            frame,
            [f.read() for f in self.query(PacketField)],
            str(self.query_one("#packet-encoding", Select).value),
            self.query_one("#packet-payload", TextArea).text,
        )

    def candidate(self, template):
        session = copy.deepcopy(self.editor.session)
        if template.packet_format != session.selection.packet_format:
            raise ValueError("Select the template's radio format in the main editor first.")
        if template.frame is not None:
            session.draft.set("profile.packet.frame", template.frame)
            if template.frame.get("fixed_payload_bytes") is not None:
                session.draft.set(
                    "profile.packet.max_payload_bytes", template.frame["fixed_payload_bytes"]
                )
        session.report = session.draft.validate()
        if not session.report.valid:
            raise ValueError(session.report.issues[0].message)
        template.validate_radio(session.report.config)
        return session

    def message(self, text):
        self.query_one("#packet-message", Static).update(text)

    def refresh_preview(self):
        if not self.loaded:
            return
        try:
            template = self.read_template()
            session = self.candidate(template)
            self.query_one("#packet-preview", Static).update(
                template.preview(session.report.config)
            )
        except (ValueError, TypeError) as exc:
            self.query_one("#packet-preview", Static).update("Invalid packet: " + str(exc))

    def on_input_changed(self, event):
        self.refresh_preview()
        event.stop()

    def on_select_changed(self, event):
        self.refresh_preview()
        event.stop()

    def on_text_area_changed(self, event):
        self.refresh_preview()
        event.stop()

    async def on_button_pressed(self, event):
        event.stop()
        name = event.button.id
        if "packet-up" in event.button.classes or "packet-down" in event.button.classes:
            row = event.button.parent.parent
            rows = list(self.query(PacketField))
            index = rows.index(row)
            parent = self.query_one("#packet-fields")
            if "packet-up" in event.button.classes and index > 0:
                parent.move_child(row, before=rows[index - 1])
            elif "packet-down" in event.button.classes and index + 1 < len(rows):
                parent.move_child(row, after=rows[index + 1])
            self.refresh_preview()
            return
        if "packet-remove" in event.button.classes:
            await event.button.parent.parent.remove()
            self.refresh_preview()
            return
        try:
            if name == "packet-add":
                if len(self.query(PacketField)) >= 64:
                    raise ValueError("Maximum 64 fields.")
                rows = list(self.query(PacketField))
                payload = next(
                    (r for r in rows if r.query_one(".packet-kind", Select).value == "payload"),
                    None,
                )
                await self.query_one("#packet-fields").mount(
                    PacketField(
                        dict(
                            name=f"field_{len(rows)}", kind="uint", width=1, order="big", value="0"
                        )
                    ),
                    before=payload,
                )
                self.refresh_preview()
            elif name == "packet-close":
                await self.action_close()
            elif name == "packet-new":
                self.guard_replace(lambda: self.call_later(self.new_template))
            elif name == "packet-open":
                self.guard_replace(
                    lambda: self.app.push_screen(
                        PathDialog("Open packet template", "Open"), self.open_template
                    )
                )
            elif name in ("packet-save", "packet-save-as"):
                await self.action_save_template(save_as=name == "packet-save-as")
            elif name == "packet-apply":
                template = self.read_template()
                self.editor.session = self.candidate(template)
                await self.editor.render_form()
                self.message(
                    "Framing applied to draft. Save and restart both peers; existing runs are unchanged."
                )
            elif name in ("packet-once", "packet-repeat"):
                if self.send_task and not self.send_task.done():
                    raise ValueError("Cancel the current send before starting another.")
                template = self.read_template()
                count = (
                    1
                    if name == "packet-once"
                    else int(self.query_one("#packet-count", Input).value)
                )
                interval = (
                    0.2
                    if name == "packet-once"
                    else float(self.query_one("#packet-interval", Input).value)
                )
                node = int(self.query_one("#packet-node", Select).value)
                self.send_task = asyncio.create_task(
                    self.run_io(
                        submit(
                            self.editor.controller,
                            node,
                            template,
                            count,
                            interval,
                            lambda n: self.message(
                                f"Submitted {n}/{count} UDP packets; RF delivery not confirmed."
                            ),
                        )
                    )
                )
            elif name == "packet-listen":
                if self.receive_task and not self.receive_task.done():
                    raise ValueError("Cancel the current listener before selecting another.")
                template = self.read_template()
                node = int(self.query_one("#packet-node", Select).value)
                self.receive_task = asyncio.create_task(
                    self.run_io(
                        receive(
                            self.editor.controller,
                            node,
                            template,
                            self.show_received,
                            self.capture_received,
                        )
                    )
                )
                self.message("Opening selected node's UDP output listener…")
            elif name == "packet-edit-rx":
                if self.last_received is None:
                    raise ValueError("Listen for a packet first.")
                self.guard_replace(lambda: self.call_later(self.edit_received))
            elif name == "packet-cancel":
                await self.cancel_io()
                self.message(
                    "Packet I/O cancelled. Already submitted UDP packets may remain in the radio queue."
                )
        except (ValueError, OSError, TypeError) as exc:
            self.message(str(exc))

    async def run_io(self, work):
        try:
            await work
        except (ValueError, OSError) as exc:
            self.message("Packet I/O: " + str(exc))

    def show_received(self, text):
        self.received.append(text)
        self.query_one("#packet-received", Static).update("\n\n".join(self.received))

    async def cancel_io(self):
        tasks = [t for t in (self.send_task, self.receive_task) if t is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def on_unmount(self):
        await self.cancel_io()

    async def action_close(self):
        # Retain incomplete widgets by keeping this screen instance on the editor.
        await self.cancel_io()
        self.editor.packet_builder = self
        self.dismiss()

    async def action_save_template(self, save_as=False):
        try:
            template = self.read_template()
            self.candidate(template)
            if self.path is not None and not save_as:
                await self.save_to(str(self.path))
            else:
                self.app.push_screen(
                    PathDialog(
                        "Save packet template as YAML",
                        "Save",
                        str(Path.cwd() / "packet-template.yaml"),
                    ),
                    self.save_to,
                )
        except (ValueError, OSError, TypeError) as exc:
            self.message("Cannot save: " + str(exc))

    async def save_to(self, path):
        if not path:
            return
        try:
            template = self.read_template()
            self.candidate(template)
            target = Path(path).expanduser().resolve()
            expected = self.expected if target == self.path else None
            content = save_template(template, target, expected=expected)
            self.path, self.expected = target, content
            self.baseline = copy.deepcopy(template.document())
            self.editor.packet_template = copy.deepcopy(template)
            self.editor.packet_template_path = target
            self.editor.packet_template_bytes = content
            self.message(f"Saved packet template: {target}")
        except (ValueError, OSError, TypeError) as exc:
            self.message("Cannot save: " + str(exc))

    async def open_template(self, path):
        if not path:
            return
        try:
            template = load_template(path)
            self.candidate(template)
            await self.cancel_io()
            await self.replace_template(template)
            self.path = Path(path).expanduser().resolve()
            self.expected = self.path.read_bytes()
            self.message(f"Opened {self.path}; framing is not applied until you choose Apply.")
        except (ValueError, OSError, TypeError) as exc:
            self.message("Cannot open: " + str(exc))

    @property
    def unsaved(self):
        try:
            return self.read_template().document() != self.baseline
        except (ValueError, TypeError):
            return True

    def guard_replace(self, callback):
        if self.unsaved:
            self.app.push_screen(DiscardDialog(), lambda discard: callback() if discard else None)
        else:
            callback()

    async def new_template(self):
        await self.cancel_io()
        await self.replace_template(PacketTemplate.new(self.editor.session.report.config))
        self.path = self.expected = None
        self.message("New template from current radio draft.")

    async def replace_template(self, template):
        self.template = template
        self.baseline = copy.deepcopy(template.document())
        self.loaded = False
        area = self.query_one("#packet-fields")
        await area.remove_children()
        await area.mount(*(PacketField(f) for f in template.fields))
        self.query_one("#packet-name", Input).value = template.name
        self.query_one("#packet-encoding", Select).value = template.payload_encoding
        self.query_one("#packet-payload", TextArea).load_text(template.payload)
        self.query_one("#packet-format", Static).update(
            f"Radio format: {template.packet_format}. Simple framing changes require Save and restart on BOTH peers."
        )
        self.query_one("#packet-apply", Button).disabled = template.frame is None
        await self.render_frame()
        self.loaded = True
        self.refresh_preview()

    def capture_received(self, data):
        self.last_received = data[:1025]

    async def edit_received(self):
        try:
            template = self.read_template().with_packet(self.last_received)
            baseline = self.baseline
            await self.cancel_io()
            await self.replace_template(template)
            self.baseline = baseline
            self.message(
                "Received bytes copied into editable fields. Nothing has been transmitted."
            )
        except (ValueError, TypeError) as exc:
            self.message("Cannot edit received packet: " + str(exc))
