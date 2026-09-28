"""Analysis tab: settings never start work, runs need a confirmed plan, output is escaped."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_analysis_pipeline_ui_behaviors():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for standalone JavaScript behavior checks")
    script = Path(__file__).with_name("analysis_pipeline_ui_test.js")
    result = subprocess.run([node, "--test", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
