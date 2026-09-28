"""Exercise the real main-player functions in an offline media harness."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_main_player_simple_listening_behaviors():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for standalone JavaScript behavior checks')
    result = subprocess.run([node, str(Path(__file__).with_name('listen_player_test.js'))],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
