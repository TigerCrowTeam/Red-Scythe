"""Ensure the optional editor CLI stays usable and explains missing dependencies."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ui_help_needs_no_optional_framework():
    result = subprocess.run(
        [sys.executable, "-m", "packet_radio.cli.ui", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert "schema-2" in result.stdout


def test_ui_missing_dependency_has_actionable_error():
    code = """
import sys
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == "textual":
            raise ModuleNotFoundError("test blocked optional UI", name="textual")
sys.meta_path.insert(0, Block())
sys.argv = ["packet-radio-ui"]
from packet_radio.cli.ui import main
main()
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 1
    assert "packet-radio[ui]" in result.stderr
    assert "Traceback" not in result.stderr


def test_ui_new_project_detection_and_explicit_offline_mode(monkeypatch):
    from unittest.mock import Mock

    from packet_radio.cli import ui
    from packet_radio.tui import app

    editor = Mock()
    monkeypatch.setattr(app, "RadioEditor", editor)
    monkeypatch.setattr(sys, "argv", ["packet-radio-ui", "--node-role", "b"])
    ui.main()
    assert editor.call_args.kwargs == {"auto_detect": True, "node_role": "b"}
    monkeypatch.setattr(sys, "argv", ["packet-radio-ui", "--offline"])
    ui.main()
    assert editor.call_args.kwargs == {"auto_detect": False, "node_role": None}
    monkeypatch.setattr(sys, "argv", ["packet-radio-ui", "configs/v1_1/peer-a-x310-simple.yaml"])
    ui.main()
    assert not editor.call_args.kwargs["auto_detect"]
