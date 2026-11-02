"""Accessible field rows and ordered table controls for the offline radio editor."""

from __future__ import annotations

import copy

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Checkbox, Input, Select, Static

from packet_radio.configuration.catalog import FieldSpec, available_choices
from packet_radio.configuration.forms import format_value


def field_id(key):
    return "field-" + key.replace(".", "-").replace("_", "-")


class Entries(Vertical):
    """Editable rows: ordered frequencies, UHD name/value pairs or node/key paths."""

    class Edited(Message):
        def __init__(self, control):
            self.control = control
            super().__init__()

    def __init__(self, key, value):
        super().__init__(id=field_id(key), classes="entries")
        self.key = key
        self.rows = []
        if key.endswith("frequencies_hz"):
            self.rows = [[format_value("profile.frequency.center_frequency_hz", v)] for v in value]
        elif key.endswith("public_key_files"):
            self.rows = [
                [str(k), p]
                for k, paths in value.items()
                for p in ([paths] if isinstance(paths, str) else paths)
            ]
        else:
            self.rows = [[k, v] for k, v in value.items() if k not in {"type", "serial", "addr"}]
        self.columns = 1 if key.endswith("frequencies_hz") else 2
        self.initial_rows = copy.deepcopy(self.rows)
        self.rebuilding = False

    @property
    def dirty(self):
        return self.read_rows() != self.initial_rows

    def compose(self) -> ComposeResult:
        for index, row in enumerate(self.rows):
            with Horizontal(classes="entry"):
                for column, value in enumerate(row):
                    yield Input(
                        str(value),
                        id=f"cell-{index}-{column}",
                        placeholder="MHz / Hz"
                        if self.columns == 1
                        else "Node ID / name"
                        if column == 0
                        else "Path / value",
                    )
                yield Button("↑", id=f"up-{index}", classes="row-button", tooltip="Move up")
                yield Button("↓", id=f"down-{index}", classes="row-button", tooltip="Move down")
                yield Button("×", id=f"remove-{index}", classes="row-button", tooltip="Remove row")
        with Horizontal(classes="table-actions"):
            yield Button("Add row", id="add-row")
            yield Button("Apply rows", id="apply-rows", variant="primary")

    async def recompose(self):
        # Timers and input events may inspect rows while Textual replaces children.
        self.rebuilding = True
        try:
            await super().recompose()
        finally:
            self.rebuilding = False

    def read_rows(self):
        if self.rebuilding:
            return copy.deepcopy(self.rows)
        cells = {cell.id: cell.value for cell in self.query(Input)}
        return [
            [cells.get(f"cell-{i}-{j}", self.rows[i][j]) for j in range(self.columns)]
            for i in range(len(self.rows))
        ]

    async def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        name = event.button.id
        self.rows = self.read_rows()
        if name == "apply-rows":
            self.post_message(self.Edited(self))
            return
        if name == "add-row":
            self.rows.append([""] * self.columns)
        else:
            action, raw = name.split("-")
            index = int(raw)
            if action == "remove":
                self.rows.pop(index)
            else:
                other = index + (-1 if action == "up" else 1)
                if 0 <= other < len(self.rows):
                    self.rows[index], self.rows[other] = self.rows[other], self.rows[index]
        await self.recompose()
        self.query_one("#add-row", Button).focus()


class FieldRow(Vertical):
    def __init__(self, field: FieldSpec, session, hint=""):
        super().__init__(
            id="row-" + field_id(field.key),
            classes="field-row" + (" compact-field" if field.access != "editable" else ""),
        )
        self.field = field
        self.session = session
        self.hint = hint

    def compose(self) -> ComposeResult:
        field, session = self.field, self.session
        value = session.value(field.key)
        label = field.label + (f" ({field.unit})" if field.unit else "")
        ident = field_id(field.key)
        if field.access != "editable":
            with Horizontal(classes="compact-value-row"):
                yield Static(label + ":", classes="compact-label", markup=False)
                yield Static(
                    format_value(field.key, value) or "Unavailable until valid",
                    id=ident,
                    classes="compact-value",
                    markup=False,
                )
            yield Static("", id="error-" + ident, classes="field-error", markup=False)
            return
        yield Static(label, classes="field-label", markup=False)
        if field.editor in ("frequency_table", "device_arguments", "key_paths"):
            yield Entries(field.key, value or {})
        elif field.editor == "toggle":
            yield Checkbox("Enabled", bool(value), id=ident)
        elif field.editor == "select":
            choices = available_choices(field.key, session.selection)
            yield Select(
                [
                    (str(v).upper() if field.key == "profile.waveform.modulation" else str(v), v)
                    for v in choices
                ],
                value=value,
                allow_blank=False,
                id=ident,
            )
        else:
            yield Input(format_value(field.key, value), id=ident)
        yield Static(self.hint, id="help-" + ident, classes="field-help", markup=False)
        yield Static("", id="error-" + ident, classes="field-error", markup=False)
