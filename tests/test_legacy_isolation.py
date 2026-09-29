"""The legacy phase ("Classic") analysis engine is gone, and nothing depends on it.

Classic removal, stage 3 (docs/CLASSIC-REMOVAL.md) deleted the engine modules,
their routes and their UI. Stage 4 dropped the data they left behind, with a
one-time startup migration (``bardic.migrations``) that retains it as artifacts
first; that migration is the only code that still names the Classic tables.
Its behavior is tested in tests/test_classic_data_drop.py. Run:

    uv run --frozen pytest -q tests/test_legacy_isolation.py
"""
import ast
import importlib.util
import json
from pathlib import Path
import re
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


def tables(store):
    with store.connect() as conn:
        return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_a_new_library_never_creates_the_legacy_tables(tmp_path):
    from bardic.app import create_app
    from bardic.processing import ProcessingStore
    from fastapi.testclient import TestClient
    store = Store(tmp_path)
    ProcessingStore(store)
    created = tables(store)
    assert {'analysis_attempts', 'book_preprocessing', 'pipeline_events', 'character_references'} <= created
    assert not {'analysis_units', 'analysis_checkpoints'} & created
    with TestClient(create_app(tmp_path)):
        pass
    assert not {'analysis_units', 'analysis_checkpoints'} & tables(Store(tmp_path))


# Names of the removed Classic storage (not the `analysis_checkpoint` artifact kind that retains it).
# Only the one-time migration that retains and drops that data may name them.
CLASSIC_STORAGE = re.compile(r'analysis_units|analysis_checkpoints\b|\.analysis_checkpoint\(|save_analysis_checkpoint|commit_analysis|'
                             r'delete_analysis_checkpoint|analysis_status|transform_checkpoint_structure|PIPELINE_VERSION')
MIGRATION = PACKAGE / 'migrations.py'


def test_only_the_migration_names_the_classic_storage():
    offenders = {str(path.relative_to(ROOT)): sorted(set(CLASSIC_STORAGE.findall(path.read_text())))
                 for path in sorted(PACKAGE.rglob('*.py')) if path != MIGRATION}
    assert not {name: found for name, found in offenders.items() if found}
    assert CLASSIC_STORAGE.search(MIGRATION.read_text())


def test_the_classic_store_api_is_gone():
    from bardic import analysis_common, structure
    for name in ('analysis_checkpoint', 'save_analysis_checkpoint', '_save_analysis_checkpoint', 'commit_analysis',
                 'delete_analysis_checkpoint', 'analysis_status'):
        assert not hasattr(Store, name), name
    assert not hasattr(analysis_common, 'fingerprint') and not hasattr(analysis_common, 'PIPELINE_VERSION')
    assert not hasattr(structure, 'transform_checkpoint_structure')
