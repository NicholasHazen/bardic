"""Read-only pipeline browsing, provenance rendering and asynchronous isolation."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_pipeline_ui_behaviors():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for standalone JavaScript behavior checks")
    script = Path(__file__).with_name("pipeline_ui_test.js")
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
