"""Offline series preview, dispatch, polling and selection-race checks."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_series_processing_ui_behaviors():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for standalone JavaScript behavior checks')
    result = subprocess.run([node, str(Path(__file__).with_name('series_processing_ui_test.js'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
