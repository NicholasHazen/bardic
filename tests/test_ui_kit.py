from pathlib import Path
import shutil
import subprocess

import pytest


def test_ui_kit_behavior():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for standalone JavaScript checks')
    result = subprocess.run([node, '--test', str(Path(__file__).with_name('ui_kit_test.js'))], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
