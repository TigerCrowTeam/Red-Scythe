"""Readable hardware capability reports and explicit discovered-device selection."""

from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static


def report_text(result):
    if "error" in result:
        return result["error"]
    if "devices" in result:
        if not result["devices"]:
            return (
                "No devices found. Check SDR power, cabling, network address and UHD installation."
            )
        return "\n".join(
            " · ".join(f"{k}: {v}" for k, v in item.items()) for item in result["devices"]
        )
    lines = [
        "Device compatibility checked · RF performance unmeasured",
        "No streams started. Settings below belong to the checked snapshot.",
    ]
    lines.append("Ranges are envelopes; discrete sample rates require actual readback checks.")
    caps = result["capabilities"]
    lines.append(f"Motherboards: {caps['motherboards']}")
    for direction, channels in caps["channels"].items():
        for channel in channels:
            lines.append(
                f"\n{direction.upper()} channel {channel['channel']} · {channel['identity']}"
            )
            lines.append("Antennas: " + ", ".join(channel["antennas"]))
            for name, values in channel["ranges"].items():
                unit = "dB" if name == "gain" else "samples/s" if name == "rate" else "Hz"
                lines.append(
                    f"  {name}: {values['minimum']:g}–{values['maximum']:g} {unit}; step {values['step']:g}"
                )
    for node in result["nodes"]:
        lines.append(f"\nNode {node['node_id']} · Channel {node['channel']} · Requested → actual")
        for key, wanted in node["requested"].items():
            lines.append(
                f"  {key}: {'automatic' if wanted is None else wanted} → {node['actual'][key]}"
            )
        for frequency in node["frequencies"]:
            lines.append(
                f"  frequency Hz: {frequency['requested_hz']:g} → RX {frequency['rx_hz']:g}, TX {frequency['tx_hz']:g}"
            )
    lines.extend("Adjustment: " + warning for warning in result["warnings"])
    return "\n".join(lines)


class HardwareDialog(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", "Close")]

    def __init__(self, result, status):
        super().__init__()
        self.result, self.status = result, status

    def compose(self):
        with VerticalScroll(id="hardware-dialog"):
            yield Static(self.status, markup=False)
            yield Static(report_text(self.result), markup=False)
            devices = self.result.get("devices", [])
            if devices:
                yield Select(
                    [
                        (
                            f"{d.get('product', d.get('type', 'USRP'))} · {d.get('serial', '')} · {d.get('addr', '')}",
                            i,
                        )
                        for i, d in enumerate(devices)
                    ],
                    allow_blank=False,
                    id="hardware-device",
                )
                yield Button("Use selected device", id="hardware-select", variant="primary")
            with Horizontal():
                yield Button("Close", id="hardware-close")

    def on_button_pressed(self, event):
        if event.button.id == "hardware-select":
            index = self.query_one(Select).value
            self.dismiss(self.result["devices"][index])
        else:
            self.dismiss(None)


class DeploymentDialog(ModalScreen):
    """Choose a local board and the role that discovery cannot infer remotely."""

    BINDINGS = [("escape", "dismiss(None)", "Keep editing")]

    def __init__(self, devices, role=None):
        super().__init__()
        self.devices = devices
        self.role = role

    def compose(self):
        with VerticalScroll(id="hardware-dialog"):
            yield Static("Detected SDR setup", markup=False)
            yield Select(
                [(f"{d.model} · {d.arguments} · {d.mode}", i) for i, d in enumerate(self.devices)],
                value=0,
                allow_blank=False,
                id="deployment-device",
            )
            yield Static(
                "B205: one node on this computer; choose A on one computer and B on the other. "
                "X310: both simple-frame nodes on this computer. "
                "This creates a new fixed-frequency draft; it does not transmit.",
                markup=False,
            )
            yield Select(
                [("Computer A", "a"), ("Computer B", "b")],
                value=self.role or Select.NULL,
                prompt="Choose this computer's role",
                id="deployment-role",
            )
            yield Static("", id="deployment-error", markup=False)
            with Horizontal():
                yield Button("Use detected setup", id="deployment-use", variant="primary")
                yield Button("Keep editing", id="deployment-cancel")

    def on_button_pressed(self, event):
        if event.button.id != "deployment-use":
            self.dismiss(None)
            return
        selected = self.devices[self.query_one("#deployment-device", Select).value]
        role = self.query_one("#deployment-role", Select).value
        if selected.mode == "remote-b205" and role is Select.NULL:
            self.query_one("#deployment-error", Static).update("Choose A or B for this computer.")
            return
        self.dismiss((selected, None if role is Select.NULL else role))
