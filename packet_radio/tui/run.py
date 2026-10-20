"""Explicit launch choices, authentication opt-in and literal owned-session details."""

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Static

from packet_radio.tui.telemetry import render_monitor


def describe(configs):
    return "\n".join(
        f"Node {c.node_id}: {c.operation}, {c.sdr.modulation.upper()}, "
        f"{c.sdr.bit_rate_bps:g} bit/s, channel {c.sdr.channel}, "
        f"frequencies {', '.join(f'{f:g}' for f in c.frequency.frequencies_hz)} Hz"
        for c in configs
    )


class SaveBeforeRunDialog(ModalScreen):
    BINDINGS = [("escape", "dismiss(False)", "Cancel")]

    def compose(self):
        with Vertical(id="dialog"):
            yield Static(
                "Save and validate this draft before starting. After saving, choose Start again.",
                markup=False,
            )
            with Horizontal():
                yield Button("Save", id="run-save", variant="primary")
                yield Button("Cancel", id="run-cancel")

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "run-save")


class StartDialog(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, configs):
        super().__init__()
        self.configs = configs
        self.needs_opt_in = any(
            c.packet_format == "generic-v5" and c.authentication.private_key_file is None
            for c in configs
        )

    def compose(self):
        with VerticalScroll(id="run-dialog"):
            yield Static("Start saved radio configuration", markup=False)
            yield Static(describe(self.configs), markup=False)
            yield Static(
                "Starting can emit control traffic as well as payloads in transmit-capable modes. Stop other applications using this SDR. Edits require stop and restart.",
                markup=False,
            )
            if self.needs_opt_in:
                yield Checkbox(
                    "Allow unauthenticated operation on an isolated bench", id="run-unauthenticated"
                )
            elif any(c.authentication.private_key_file is None for c in self.configs):
                yield Static(
                    "This custom packet format is unauthenticated; the existing CLI allows it without the generic-v5 bench override.",
                    markup=False,
                )
            with Horizontal():
                yield Button(
                    "Start radio", id="run-start", variant="primary", disabled=self.needs_opt_in
                )
                yield Button("Cancel", id="run-cancel")

    def on_checkbox_changed(self, event):
        self.query_one("#run-start", Button).disabled = not event.value

    def on_button_pressed(self, event):
        if event.button.id == "run-start":
            self.dismiss(self.needs_opt_in and self.query_one(Checkbox).value)
        else:
            self.dismiss(None)


class RunDetails(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Close")]

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def compose(self):
        with VerticalScroll(id="run-dialog"):
            yield Static("", id="run-details", markup=False)
            with Horizontal():
                yield Button("Stop owned radio", id="run-stop", variant="error")
                yield Button("Close", id="run-close")

    def refresh_details(self):
        c = self.controller
        text = f"{c.state} · Startup readiness is not RF acquisition or payload delivery.\n"
        text += f"Run ID: {c.run_id or 'not launched'}\n"
        text += render_monitor(c) + "\n\n"
        text += "Last launched snapshot (unchanged by draft edits):\n"
        text += describe(c.running_configs) + "\n" + "\n".join(c.sources)
        text += "\n" + c.error + "\nRecent logs (last 200 chunks/lines):\n" + "\n".join(c.logs)
        self.query_one("#run-details", Static).update(text)
        self.query_one("#run-stop", Button).disabled = not c.active

    def on_mount(self):
        self.refresh_details()
        self.set_interval(0.5, self.refresh_details)

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "run-stop")
