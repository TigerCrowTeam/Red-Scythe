"""Path, discard and explicit peer-export dialogs for offline project persistence."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from packet_radio.configuration.forms import parse_value
from packet_radio.configuration.storage import PeerOptions


class PathDialog(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title="Open a schema-2 node configuration", action="Open", initial=""):
        super().__init__()
        self.prompt, self.confirm, self.initial = title, action, initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.prompt, markup=False)
            yield Input(self.initial, placeholder="/path/to/radio-node.yaml", id="open-path")
            with Horizontal():
                yield Button(self.confirm, id="open-confirm", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self):
        self.query_one(Input).focus()

    def on_input_submitted(self, event):
        self.dismiss(event.value)

    def on_button_pressed(self, event):
        self.dismiss(self.query_one(Input).value if event.button.id == "open-confirm" else None)


class DiscardDialog(ModalScreen):
    BINDINGS = [("escape", "dismiss(False)", "Keep editing")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(
                "Discard unsaved edits? Choose Keep editing to return and save.", markup=False
            )
            with Horizontal():
                yield Button("Keep editing", id="keep", variant="primary")
                yield Button("Discard", id="discard", variant="error")

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "discard")


class PeerDialog(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, config):
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        config = self.config
        used = {
            config.endpoint.bind_port,
            config.endpoint.destination_port,
            config.status.http_port,
        }

        def port(start):
            value = (start - 1) % 65535 + 1
            while value in used:
                value = value % 65535 + 1
            used.add(value)
            return str(value)

        with VerticalScroll(id="peer-dialog"):
            yield Static(
                "Export a complementary peer · Configure peer-local paths; key/state data are not copied.",
                markup=False,
            )
            yield Static("New node file")
            yield Input(placeholder="/path/to/peer-node.yaml", id="peer-path")
            yield Static("Peer SDR type")
            yield Select(
                [("B205", "b200"), ("X310", "x300")],
                value=config.sdr.device.get("type", "x300"),
                allow_blank=False,
                id="peer-type",
            )
            fields = [
                ("identifier", "Peer B205 serial / X310 address (required)", ""),
                (
                    "channel",
                    "Channel (distinct for a shared X310)",
                    str(
                        1 - config.sdr.channel
                        if config.sdr.channel in (0, 1) and config.sdr.device.get("type") == "x300"
                        else 0
                    ),
                ),
                ("bind_port", "Payload input UDP port", port(config.endpoint.bind_port + 2)),
                (
                    "destination_port",
                    "Payload output UDP port",
                    port(config.endpoint.destination_port + 2),
                ),
                ("http_port", "Status HTTP port", port(config.status.http_port + 1)),
                ("bind_host", "Payload input bind host", "127.0.0.1"),
                ("destination_host", "Payload output destination host", "127.0.0.1"),
                ("private_key_file", "Peer private key path (required if authenticated)", ""),
                (
                    "replay_state_file",
                    "Separate peer replay-state path (required if authenticated)",
                    "",
                ),
                ("recording_directory", "Peer recording directory (blank disables recording)", ""),
            ]
            for name, label, default in fields:
                yield Static(label, markup=False)
                yield Input(default, id="peer-" + name)
            yield Static("", id="peer-error", markup=False)
            with Horizontal():
                yield Button("Export", id="peer-export", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self):
        self.query_one("#peer-path", Input).focus()

    def on_button_pressed(self, event):
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        try:
            values = {
                name: self.query_one("#peer-" + name, Input).value
                for name in PeerOptions.__dataclass_fields__
                if name != "device_type"
            }
            for name, key in (
                ("channel", "node.hardware.channel"),
                ("bind_port", "node.endpoint.bind_port"),
                ("destination_port", "node.endpoint.destination_port"),
                ("http_port", "node.endpoint.bind_port"),
            ):
                values[name] = parse_value(key, values[name])
            for name in ("private_key_file", "replay_state_file", "recording_directory"):
                values[name] = values[name] or None
            values["device_type"] = self.query_one("#peer-type", Select).value
            path = self.query_one("#peer-path", Input).value
            if not path.strip():
                raise ValueError("Choose a new peer node file.")
            self.dismiss((path, PeerOptions(**values)))
        except ValueError as exc:
            self.query_one("#peer-error", Static).update("Error: " + str(exc))
