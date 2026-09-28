from pathlib import Path
import shutil
import subprocess

import pytest


def test_resource_ui_behavior():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for standalone JavaScript checks')
    result = subprocess.run([node, str(Path(__file__).with_name('resources_ui_test.js'))], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
