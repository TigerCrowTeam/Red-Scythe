"""Check optional UI isolation and the installed editor workflow without UHD or RF.

Run with an installed wheel's interpreter outside the source checkout. A minimal
fake child exercises readiness and owned cleanup; it never opens an SDR or socket.
The same command verifies the actionable error when Textual is not installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import packet_radio

FAKE_RADIO = """
import argparse, os, signal, time
from packet_radio.config import load_config
parser = argparse.ArgumentParser()
parser.add_argument('--config'); parser.add_argument('--ready-fd', type=int)
parser.add_argument('--run-id')
args = parser.parse_args()
config = load_config(args.config)
assert config.sdr.modulation == '2fsk'
assert config.center_frequency_hz == 916e6
signal.signal(signal.SIGTERM, lambda *args: exit(0))
os.write(args.ready_fd, b'R'); os.close(args.ready_fd)
while True: time.sleep(.02)
"""


async def check_editor(directory):
    from textual.widgets import Input

    from packet_radio.config import load_config, validate_network
    from packet_radio.runtime.controller import RadioController
    from packet_radio.tui.app import RadioEditor
    from packet_radio.tui.dialogs import DiscardDialog, PathDialog, PeerDialog
    from packet_radio.tui.run import RunDetails, StartDialog
    from packet_radio.tui.widgets import field_id

    async def settle(pilot):
        for _ in range(100):
            await pilot.pause()
            if pilot.app.ready:
                return
        raise AssertionError("Editor form did not finish mounting")

    target, peer = directory / "node.yaml", directory / "peer.yaml"
    app = RadioEditor()
    app.controller = RadioController(command=[sys.executable, "-u", "-c", FAKE_RADIO])
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        # Actual widgets exercise event routing and modulation-dependent rebuilding.
        app.query_one("#" + field_id("profile.waveform.modulation")).value = "gmsk"
        await settle(pilot)
        assert app.query("#" + field_id("profile.waveform.gaussian_bt"))
        app.query_one("#" + field_id("profile.waveform.modulation")).value = "2fsk"
        await settle(pilot)
        app.query_one("#" + field_id("profile.packet.format")).value = "simple-frame-v1"
        await settle(pilot)
        frequency = app.query_one("#" + field_id("profile.frequency.center_frequency_hz"), Input)
        frequency.value = "916 MHz"
        await pilot.press("f5")
        await settle(pilot)
        assert app.session.report.valid and not app.errors
        await pilot.press("ctrl+n")
        assert isinstance(app.screen, DiscardDialog)
        await pilot.press("escape")
        assert app.session.report.config.center_frequency_hz == 916e6
        await pilot.press("ctrl+s")
        assert isinstance(app.screen, PathDialog)
        app.screen.query_one(Input).value = str(target)
        await pilot.press("enter")
        await settle(pilot)
        assert not app.session.unsaved and load_config(target) == app.session.report.config
        await pilot.press("ctrl+o")
        assert isinstance(app.screen, PathDialog)
        app.screen.query_one(Input).value = str(target)
        await pilot.press("enter")
        await settle(pilot)
        assert app.session.report.config.center_frequency_hz == 916e6
        await pilot.press("f6")
        assert isinstance(app.screen, PeerDialog)
        app.screen.query_one("#peer-path", Input).value = str(peer)
        app.screen.query_one("#peer-identifier", Input).value = "192.168.10.2"
        app.screen.query_one("#peer-export").press()
        await settle(pilot)
        validate_network({"local": load_config(target), "peer": load_config(peer)})
        await pilot.press("ctrl+b")
        from packet_radio.tui.packets import PacketBuilder

        assert isinstance(app.screen, PacketBuilder)
        packet_path = directory / "packet.yaml"
        await app.screen.save_to(packet_path)
        assert packet_path.is_file()
        await app.screen.open_template(packet_path)
        assert app.screen.read_template().encode()[0] == b"Hello radio"
        await pilot.press("escape")
        await pilot.press("ctrl+r")
        assert isinstance(app.screen, StartDialog)
        app.screen.query_one("#run-start").press()
        await pilot.pause()

        async def ready():
            while app.controller.state != "Running":
                assert app.controller.state != "Failed", app.controller.error
                await asyncio.sleep(0.01)

        await asyncio.wait_for(ready(), 5)
        child = app.controller.process
        await pilot.press("f12")
        assert isinstance(app.screen, RunDetails)
        await pilot.press("ctrl+t")
        assert app.controller.state == "Stopped" and child.returncode == 0
        await pilot.press("escape", "ctrl+q")
    assert not app.controller.active and app.controller.process is None
    assert "uhd" not in sys.modules
    print(
        "Installed UI: edit/switch/validate/save/reopen/export/packet-builder/start/details/stop/quit passed; no UHD access."
    )


def main():
    assert Path(packet_radio.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), (
        "Run the installed wheel outside the source checkout"
    )
    command = str(Path(sys.executable).parent / "packet-radio-ui")
    subprocess.run([command, "--help"], check=True, stdout=subprocess.DEVNULL)
    if importlib.util.find_spec("textual") is None:
        result = subprocess.run([command], text=True, capture_output=True, timeout=10)
        assert result.returncode == 1 and "packet-radio[ui]" in result.stderr
        assert "Traceback" not in result.stderr
        print(
            "Installed core: UI extra is optional and its missing-dependency message is actionable."
        )
        return
    with tempfile.TemporaryDirectory(prefix="packet-radio-ui-install-") as directory:
        asyncio.run(check_editor(Path(directory)))


if __name__ == "__main__":
    main()
