"""Storage and readers used only by the legacy phase ("Classic") analysis engine.

The phase engine (``bardic/progressive.py``, ``bardic/staged_analysis.py``)
caches validated requests in the ``analysis_units`` table and reports
discovery coverage from those units and the old ``analysis_checkpoints``
record. The step pipeline has its own caches and never reads either.

Nothing outside the phase engine, its endpoints and the legacy ``/pipeline``
readout may import this module (tests/test_legacy_isolation.py). It is
deleted with the engine; the tables are dropped separately, after a backup
(docs/CLASSIC-REMOVAL.md, stages 3 and 4).
"""
from __future__ import annotations

from collections import defaultdict
import json

from . import analysis as a
from .preprocessing import census, eligible_chapters
from .processing import ProcessingStore, source_hash


def ensure_units_table(conn):
    """Create the unit cache if absent. Existing libraries already have it; the schema is unchanged."""
    conn.execute('CREATE TABLE IF NOT EXISTS analysis_units (book_id TEXT, unit_key TEXT, stage TEXT, source_hash TEXT, body TEXT NOT NULL, PRIMARY KEY(book_id,unit_key))')
    conn.execute('CREATE INDEX IF NOT EXISTS analysis_units_stage ON analysis_units(book_id,stage,source_hash)')


class LegacyProcessingStore(ProcessingStore):
    """The shared request records plus the phase engine's ``analysis_units`` cache."""

    def __init__(self, store):
        super().__init__(store)
        with store.lock, store.connect() as conn:
            ensure_units_table(conn)

    def unit(self, book_id, key):
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT body FROM analysis_units WHERE book_id=? AND unit_key=?', (book_id, key)).fetchone()
        return json.loads(row[0]) if row else None

    def save_unit(self, book_id, key, stage, source, value):
        from .artifacts import record
        with self.store.lock, self.store.connect() as conn:
            dependencies = value.get('dependency_artifact_ids', [])
            recipe = value.get('input_recipe')
            if recipe:
                recipe_id = record(conn, book_id, 'analysis_input', key, recipe, label=f'{stage} request recipe', stage=stage,
                                   provider=value.get('provider'), model=value.get('model'), dependencies=dependencies)
                dependencies = [recipe_id]
            payload = {k: v for k, v in value.items() if k not in {'input_recipe', 'dependency_artifact_ids'}}
            artifact_id = record(conn, book_id, 'analysis_output', key, payload, label=f'{stage} accepted result', stage=stage,
                                 provider=value.get('provider'), model=value.get('model'), dependencies=dependencies,
                                 legacy_provenance=not bool(recipe))
            conn.execute('INSERT OR REPLACE INTO analysis_units VALUES (?,?,?,?,?)', (book_id, key, stage, source, json.dumps(value, ensure_ascii=False)))
        return artifact_id

    def units(self, book_id, stage, source):
        with self.store.lock, self.store.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute('SELECT body FROM analysis_units WHERE book_id=? AND stage=? AND source_hash=? ORDER BY rowid', (book_id, stage, source))]


def coverage(book, store):
    repository = LegacyProcessingStore(store)
    local = census(book, store)
    units = repository.units(book['id'], 'discovery', source_hash(book))
    checkpoint_status = store.analysis_status(book['id']) or {}
    checkpoint = store.analysis_checkpoint(book['id'], checkpoint_status.get('fingerprint')) or {}
    # Include pre-upgrade validated chapter discovery without claiming unscanned text.
    rows = ({r['id']: r for r in checkpoint.get('chapters', [])}
            if 'phase' not in checkpoint and checkpoint.get('provider') in a.PROVIDER_LABELS else {})
    baseline = {c['id']: c['text'] for c in checkpoint.get('working_book', {}).get('chapters', [])}
    chapter_map = {c['id']: c for c in book['chapters']}
    intervals = defaultdict(list)
    for unit in units:
        chapter = chapter_map.get(unit.get('chapter_id'))
        start, end = unit.get('start'), unit.get('end')
        if (chapter is not None and unit.get('provider') in a.PROVIDER_LABELS
                and type(start) is int and type(end) is int and 0 <= start < end <= len(chapter['text'])):
            intervals[chapter['id']].append((start, end))
    complete = []
    for chapter in eligible_chapters(book):
        parts = sorted(intervals[chapter['id']])
        merged = []
        for start, end in parts:
            if merged and not chapter['text'][merged[-1][1]:start].strip():
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        is_complete = bool(merged and len(merged) == 1 and not chapter['text'][:merged[0][0]].strip() and not chapter['text'][merged[0][1]:].strip())
        legacy_complete = (rows.get(chapter['id'], {}).get('discovery_complete') is True
                           and baseline.get(chapter['id']) == chapter['text'])
        if is_complete or legacy_complete:
            complete.append(chapter['id'])
    all_discovered = bool(local['eligible_chapters']) and len(complete) == local['eligible_chapters']
    return {'local': local, 'semantic_chapters_complete': len(complete), 'semantic_chapter_ids': complete,
            'eligible_chapters': local['eligible_chapters'], 'whole_book_discovered': all_discovered,
            'profiles_provisional': not all_discovered,
            'usage': repository.usage(book['id']),
            'note': 'Full discovery coverage is required for comprehensive profiles; it does not guarantee that all identities or traits are correct.'}
