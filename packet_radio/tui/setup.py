"""Operating mode, local gain presets and portable two-computer export dialogs."""

from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from packet_radio.configuration.bundle import export_two_host
from packet_radio.configuration.deployment import GAIN_PRESETS, SETUP_MODES, configure_setup


class SetupDialog(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, draft):
        super().__init__()
        self.draft = draft
        self.config = draft.validate().config
        self.mode = "shared-x310" if self.config.sdr.device.get("type") == "x300" else "remote-b205"

    def compose(self):
        cfg = self.config
        with VerticalScroll(id="peer-dialog"):
            yield Static("Operating setup · Changes apply to the draft; Save before Start.")
            yield Static("Operating mode")
            yield Select(
                [(label, key) for key, label in SETUP_MODES.items()],
                value=self.mode,
                allow_blank=False,
                id="setup-mode",
            )
            yield Static("Local B205 serial / shared X310 address")
            yield Input(
                cfg.sdr.device.get("serial" if self.mode == "remote-b205" else "addr", ""),
                id="setup-identifier",
            )
            yield Static(
                "Computer role (X310: primary local node). A sets node 1 / peer 2; B reverses them."
            )
            yield Select(
                [("Computer A", "a"), ("Computer B", "b")],
                value="a" if cfg.timing_role == "leader" else "b",
                allow_blank=False,
                id="setup-role",
            )
            yield Static("Gain preset")
            yield Select(self.presets(), value="keep", allow_blank=False, id="setup-preset")
            yield Static(
                "The B205 bench preset requires a 30 dB attenuator in each crossed TX/RX → RX2 path. "
                "Keep current gains preserves custom values. Waveform and framing are retained. "
                "B205 remote mode supports fixed frequency or generic-v5 RF leader hopping.",
                markup=False,
            )
            yield Static("", id="setup-error", markup=False)
            with Horizontal():
                yield Button("Apply setup", id="setup-apply", variant="primary")
                yield Button("Cancel", id="setup-cancel")

    def presets(self):
        return [
            (label, key)
            for key, label in GAIN_PRESETS.items()
            if key != "b205-bench" or self.mode == "remote-b205"
        ]

    def on_select_changed(self, event):
        if event.select.id == "setup-mode" and event.value != self.mode:
            self.mode = event.value
            self.query_one("#setup-identifier", Input).value = ""
            selector = self.query_one("#setup-preset", Select)
            selector.set_options(self.presets())
            selector.value = "keep"

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "setup-cancel":
            self.dismiss(None)
            return
        try:
            result = configure_setup(
                self.draft,
                mode=self.mode,
                role=self.query_one("#setup-role", Select).value,
                identifier=self.query_one("#setup-identifier", Input).value,
                preset=self.query_one("#setup-preset", Select).value,
            )
            self.dismiss(result)
        except ValueError as exc:
            self.query_one("#setup-error", Static).update(str(exc))


class BundleDialog(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, draft):
        super().__init__()
        self.draft = draft

    def compose(self):
        with VerticalScroll(id="peer-dialog"):
            yield Static("Export two-computer project")
            yield Static("New folder")
            yield Input(placeholder="/home/user/radio-project", id="bundle-path")
            yield Static("Other computer's B205 serial")
            yield Input(id="bundle-serial")
            yield Static(
                "Creates computer-a.yaml, computer-b.yaml and one shared profiles/ file. "
                "Copy the whole folder to the other computer and open its node. "
                "Each node has independent hardware/gain/port settings; the peer initially inherits your gains. "
                "Application/status addresses become localhost. Recordings and authentication paths "
                "are omitted. Only fixed-frequency simple-frame B205 projects are supported. "
                "No SSH or remote IP is needed to run the radios.",
                markup=False,
            )
            yield Static("", id="bundle-error", markup=False)
            with Horizontal():
                yield Button("Export project", id="bundle-export", variant="primary")
                yield Button("Cancel", id="bundle-cancel")

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "bundle-cancel":
            self.dismiss(None)
            return
        try:
            path = self.query_one("#bundle-path", Input).value.strip()
            if not path:
                raise ValueError("Choose a new project folder.")
            exported = export_two_host(
                self.draft, path, self.query_one("#bundle-serial", Input).value
            )
            self.dismiss(exported)
        except (ValueError, OSError) as exc:
            self.query_one("#bundle-error", Static).update(str(exc))
