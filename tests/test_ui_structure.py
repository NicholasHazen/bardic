"""Book lifecycle strip and primary-surface copy lint (Node tests; see the .js files)."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('script', ['lifecycle_test.js', 'copy_lint_test.js'])
def test_ui_structure(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for standalone JavaScript checks')
    result = subprocess.run([node, '--test', str(Path(__file__).with_name(script))], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
