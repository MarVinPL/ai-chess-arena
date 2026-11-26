"""Execute the actual tournament card renderer and event handlers in Node."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_tournament_control_async():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    source = (ROOT / "web/js/arena.js").read_text()
    script = (ROOT / "tests/tournament_control_ui.cjs").read_text()
    result = subprocess.run([node, "-e", script], input=source, text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
