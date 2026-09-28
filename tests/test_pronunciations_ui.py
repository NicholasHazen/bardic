"""Exercise the pronunciation panel's browser behavior without provider calls."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_pronunciation_panel_behaviors():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for standalone JavaScript behavior checks")
    script = Path(__file__).with_name("pronunciations_ui_test.js")
    result = subprocess.run([node, "--test", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
