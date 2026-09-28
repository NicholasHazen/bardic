"""Write or check the published contract.

    uv run --frozen python -m bardic.apispec           # regenerate contract/
    uv run --frozen python -m bardic.apispec --check   # exit 1 if contract/ is stale

The newest entry in contract/CHANGELOG.md records the SHA-256 of the
openapi.json it describes. Generation fills that line in for a new entry, and
refuses when the contract changed after its version was recorded: every
change to openapi.json needs a new version and a changelog entry.
"""
from __future__ import annotations

import hashlib
import re
import sys
import tempfile
from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[2] / 'contract'
HEADING = re.compile(r'^## (\d+\.\d+\.\d+) — \d{4}-\d{2}-\d{2}\n(?:<!-- contract-sha256: ([0-9a-f]{64}) -->\n)?', re.M)


class ContractError(Exception):
    pass


def generate() -> dict[str, str]:
    """The contract files, keyed by name, generated from the current code."""
    from ..app import create_app
    from .reference import render
    from .spec import VERSION, dumps

    with tempfile.TemporaryDirectory() as data:
        # Building the schema does not start the runtime or touch the directory.
        schema = create_app(Path(data)).openapi()
    openapi = dumps(schema)
    files = {'openapi.json': openapi, 'API-REFERENCE.md': render(schema)}
    changelog = CONTRACT / 'CHANGELOG.md'
    if changelog.is_file():
        files['CHANGELOG.md'] = record_version(changelog.read_text(encoding='utf-8'), VERSION, openapi)
    return files


def record_version(changelog: str, version: str, openapi: str) -> str:
    """The changelog with the newest entry's contract hash filled in, or a ContractError."""
    digest = hashlib.sha256(openapi.encode('utf-8')).hexdigest()
    newest = HEADING.search(changelog)
    if newest is None or newest.group(1) != version:
        raise ContractError(f'contract/CHANGELOG.md must start (below the rules) with "## {version} — YYYY-MM-DD", '
                            f'the version in bardic/apispec/spec.py.')
    recorded = newest.group(2)
    if recorded == digest:
        return changelog
    if recorded is not None:
        raise ContractError(f'contract/openapi.json changed after version {version} was recorded. Bump VERSION in '
                            f'bardic/apispec/spec.py, add a "## <version> — <date>" entry to contract/CHANGELOG.md that '
                            f'describes the change (mark BREAKING if it is), then regenerate. See docs/API-WORKFLOW.md.')
    line = f'<!-- contract-sha256: {digest} -->\n'
    return changelog[:newest.end()] + line + changelog[newest.end():]


def stale(files: dict[str, str]) -> list[str]:
    return [name for name, text in files.items()
            if not (CONTRACT / name).is_file() or (CONTRACT / name).read_text(encoding='utf-8') != text]


def main(argv: list[str]) -> int:
    try:
        files = generate()
    except ContractError as error:
        print(error)
        return 1
    changed = stale(files)
    if '--check' in argv:
        if changed:
            print('Stale contract files: ' + ', '.join(f'contract/{name}' for name in changed))
            print('Regenerate with: uv run --frozen python -m bardic.apispec  (see docs/API-WORKFLOW.md)')
            return 1
        print('contract/ is current.')
        return 0
    CONTRACT.mkdir(exist_ok=True)
    for name in changed:
        (CONTRACT / name).write_text(files[name], encoding='utf-8')
    print('Updated: ' + (', '.join(f'contract/{name}' for name in changed) or 'nothing'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
