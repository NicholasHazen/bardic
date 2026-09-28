"""Clients can generate strict, usable TypeScript types from the contract.

Needs Node and the pinned dev tools (`npm ci`); skipped otherwise, like the
other Node-dependent wrappers. Run directly with `npm run contract:codegen`.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is not installed')
@pytest.mark.skipif(not (ROOT / 'node_modules' / 'openapi-typescript').is_dir(),
                    reason='dev tools not installed; run `npm ci`')
def test_generated_typescript_compiles_strictly():
    result = subprocess.run(['node', str(ROOT / 'tools' / 'contract-codegen-check.mjs')],
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
