"""The step pipeline and shared modules do not depend on the legacy phase engine.

Classic removal, stage 1 (docs/CLASSIC-REMOVAL.md): after this, stage 3 can
delete the legacy modules by editing only the ALLOWED importers below. Run:

    uv run --frozen pytest -q tests/test_legacy_isolation.py
"""
import ast
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from bardic.store import Store

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'bardic'

# The legacy phase ("Classic") engine and the storage only it uses.
LEGACY = {'bardic.progressive', 'bardic.staged_analysis', 'bardic.legacy_phase'}
# The only live modules that may still reference it, and why. Stage 3 removes
# each reference, then this set and LEGACY become empty.
ALLOWED = {
    'bardic.app': 'POST /analyze, GET /preprocessing and POST /analysis-plan endpoints',
    'bardic.analysis': 'analyze_book() dispatch to the phase and chapter engines when given a store',
    'bardic.pipeline_view': 'the legacy GET /pipeline readout (pipeline())',
}


def module_name(path):
    parts = path.relative_to(ROOT).with_suffix('').parts
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)


def imported_modules(path):
    """Every bardic module a file imports, including imports inside functions."""
    name = module_name(path)
    package = name if path.name == '__init__.py' else name.rsplit('.', 1)[0]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split('.')
                base = base[:len(base) - node.level + 1]
                target = '.'.join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ''
            found.add(target)
            # "from . import progressive" names a module, not an attribute.
            found.update(f'{target}.{alias.name}' for alias in node.names)
    return {m for m in found if m == 'bardic' or m.startswith('bardic.')}


def legacy_importers():
    result = {}
    for path in sorted(PACKAGE.rglob('*.py')):
        name = module_name(path)
        if name in LEGACY:
            continue
        uses = imported_modules(path) & LEGACY
        if uses:
            result[name] = sorted(uses)
    return result


def test_only_listed_modules_reference_the_legacy_engine():
    importers = legacy_importers()
    unexpected = {name: uses for name, uses in importers.items() if name not in ALLOWED}
    assert not unexpected, f'Live code must not import the legacy phase engine: {unexpected}'


def test_the_step_pipeline_and_shared_infrastructure_import_nothing_legacy():
    importers = legacy_importers()
    shared = ('bardic.pipeline', 'bardic.processing', 'bardic.preprocessing', 'bardic.analysis_common', 'bardic.artifacts',
              'bardic.series', 'bardic.series_processing', 'bardic.resources', 'bardic.search', 'bardic.performances')
    assert not [name for name in importers if any(name == s or name.startswith(s + '.') for s in shared)]


def test_loading_the_app_and_pipeline_does_not_load_the_legacy_engine():
    code = ('import json, sys\n'
            'import bardic.app, bardic.pipeline.api, bardic.pipeline.runner, bardic.pipeline.projection, bardic.pipeline.prompts\n'
            'import bardic.pipeline.steps, bardic.pipeline_view, bardic.series_processing, bardic.processing, bardic.preprocessing\n'
            'bardic.pipeline.default_registry()\n'
            'print(json.dumps(sorted(m for m in sys.modules if m.startswith("bardic."))))\n')
    loaded = json.loads(subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True, capture_output=True, text=True).stdout)
    assert 'bardic.pipeline.prompts' in loaded
    assert not LEGACY & set(loaded)


UNITS_DDL = ('CREATE TABLE analysis_units (book_id TEXT, unit_key TEXT, stage TEXT, source_hash TEXT, '
             'body TEXT NOT NULL, PRIMARY KEY(book_id,unit_key))')


def tables(store):
    with store.connect() as conn:
        return {name: sql for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}


def test_shared_request_records_no_longer_create_the_legacy_unit_table(tmp_path):
    from bardic.processing import ProcessingStore
    store = Store(tmp_path)
    ProcessingStore(store)
    created = tables(store)
    assert {'analysis_attempts', 'book_preprocessing', 'pipeline_events'} <= created.keys()
    assert 'analysis_units' not in created


def test_an_existing_library_keeps_its_legacy_units_readable_and_unchanged(tmp_path):
    from bardic.legacy_phase import LegacyProcessingStore
    from bardic.processing import ProcessingStore
    Store(tmp_path)
    # A library written by an earlier version: the table and a unit already exist.
    with sqlite3.connect(tmp_path / 'library.sqlite3') as conn:
        conn.execute(UNITS_DDL)
        conn.execute('CREATE INDEX analysis_units_stage ON analysis_units(book_id,stage,source_hash)')
        conn.execute('INSERT INTO analysis_units VALUES (?,?,?,?,?)',
                     ('book', 'discovery:old', 'discovery', 'source', json.dumps({'unit_key': 'discovery:old'})))
    store = Store(tmp_path)
    ProcessingStore(store)
    assert tables(store)['analysis_units'] == UNITS_DDL
    assert LegacyProcessingStore(store).units('book', 'discovery', 'source') == [{'unit_key': 'discovery:old'}]
    assert tables(store)['analysis_units'] == UNITS_DDL


def test_the_legacy_store_creates_the_same_unit_table_lazily(tmp_path):
    from bardic.legacy_phase import LegacyProcessingStore
    store = Store(tmp_path)
    LegacyProcessingStore(store)
    assert tables(store)['analysis_units'] == UNITS_DDL
