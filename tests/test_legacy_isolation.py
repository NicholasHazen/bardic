"""The legacy phase ("Classic") analysis engine is gone, and nothing depends on it.

Classic removal, stage 3 (docs/CLASSIC-REMOVAL.md) deleted the engine modules,
their routes and their UI. Stage 4 drops the data they left behind, after
``ArtifactRepository.backfill`` has retained it; until then an existing
library's ``analysis_units`` rows must stay readable by that backfill. Run:

    uv run --frozen pytest -q tests/test_legacy_isolation.py
"""
import ast
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from bardic.store import Store

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'bardic'

# The deleted engine and the storage module only it used.
LEGACY = {'bardic.progressive', 'bardic.staged_analysis', 'bardic.legacy_phase'}
# The deleted book routes (the service path /v1/analyze of self-hosted servers is unrelated).
REMOVED_ROUTES = {('POST', '/api/books/{book_id}/analyze'), ('GET', '/api/books/{book_id}/preprocessing'),
                  ('POST', '/api/books/{book_id}/analysis-plan'), ('GET', '/api/books/{book_id}/analysis')}


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


def test_the_legacy_modules_are_deleted():
    for name in LEGACY:
        assert importlib.util.find_spec(name) is None, f'{name} still exists'
    assert not (PACKAGE / 'static' / 'production.js').exists() and not (PACKAGE / 'static' / 'production.css').exists()


def test_no_module_imports_the_legacy_engine():
    importers = {module_name(path): sorted(imported_modules(path) & LEGACY) for path in sorted(PACKAGE.rglob('*.py'))}
    assert not {name: uses for name, uses in importers.items() if uses}


def test_loading_the_app_and_pipeline_does_not_load_the_legacy_engine():
    code = ('import json, sys\n'
            'import bardic.app, bardic.pipeline.api, bardic.pipeline.runner, bardic.pipeline.projection, bardic.pipeline.prompts\n'
            'import bardic.pipeline.steps, bardic.pipeline_view, bardic.series_processing, bardic.processing, bardic.preprocessing\n'
            'bardic.pipeline.default_registry()\n'
            'print(json.dumps(sorted(m for m in sys.modules if m.startswith("bardic."))))\n')
    loaded = json.loads(subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True, capture_output=True, text=True).stdout)
    assert 'bardic.pipeline.prompts' in loaded
    assert not LEGACY & set(loaded)


def test_the_classic_routes_are_not_served(tmp_path):
    from bardic.app import create_app
    served = {(method, route.path) for route in create_app(tmp_path).routes for method in getattr(route, 'methods', None) or ()}
    assert not REMOVED_ROUTES & served
    assert ('GET', '/api/books/{book_id}/pipeline') in served  # the Details explorer stays


def test_the_browser_calls_no_classic_route():
    pattern = re.compile(r'/(?:analyze|preprocessing|analysis-plan)\b|/analysis[`\'"]')
    offenders = {path.name: pattern.findall(path.read_text()) for path in (PACKAGE / 'static').glob('*.*')
                 if path.suffix in {'.js', '.html'}}
    assert not {name: found for name, found in offenders.items() if found}


UNITS_DDL = ('CREATE TABLE analysis_units (book_id TEXT, unit_key TEXT, stage TEXT, source_hash TEXT, '
             'body TEXT NOT NULL, PRIMARY KEY(book_id,unit_key))')


def tables(store):
    with store.connect() as conn:
        return {name: sql for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}


def test_a_new_library_never_creates_the_legacy_unit_table(tmp_path):
    from bardic.processing import ProcessingStore
    store = Store(tmp_path)
    ProcessingStore(store)
    created = tables(store)
    assert {'analysis_attempts', 'book_preprocessing', 'pipeline_events'} <= created.keys()
    assert 'analysis_units' not in created


def test_an_existing_library_keeps_its_legacy_units_until_backfill_retains_them(tmp_path):
    """Stage 4 runs the backfill before dropping the table; the rows must survive until then."""
    from bardic.artifacts import ArtifactRepository
    from bardic.importer import parse_book
    from bardic.processing import ProcessingStore, source_hash
    store = Store(tmp_path)
    book = parse_book('story.txt', b'Chapter 1\n\nMara said, "Wait."')
    store.save_book(book)
    chapter = book['chapters'][0]
    unit = {'unit_key': 'discovery:old', 'stage': 'discovery', 'chapter_id': chapter['id'], 'start': 0,
            'end': len(chapter['text']), 'provider': 'anthropic', 'model': 'old-model', 'result': {'characters': []}}
    # A library written by an earlier version: the table and a unit already exist.
    with sqlite3.connect(tmp_path / 'library.sqlite3') as conn:
        conn.execute(UNITS_DDL)
        conn.execute('CREATE INDEX analysis_units_stage ON analysis_units(book_id,stage,source_hash)')
        conn.execute('INSERT INTO analysis_units VALUES (?,?,?,?,?)',
                     (book['id'], 'discovery:old', 'discovery', source_hash(book), json.dumps(unit)))
    store = Store(tmp_path)
    ProcessingStore(store)
    repository = ArtifactRepository(store)
    repository.backfill(book['id'])
    retained = repository.get(book['id'], repository.output_head(book['id'], 'analysis_output', 'discovery:old'))
    assert retained['legacy_provenance'] is True and retained['provider'] == 'anthropic'
    assert tables(store)['analysis_units'] == UNITS_DDL
    with store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM analysis_units').fetchone()[0] == 1
