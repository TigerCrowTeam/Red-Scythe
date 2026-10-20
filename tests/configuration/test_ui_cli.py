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
