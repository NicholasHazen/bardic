"""One-time, versioned data migrations of an existing library, run at server start.

Each migration is recorded in ``schema_migrations`` (one row per migration ID)
with its per-book counts, and is skipped once recorded as ``completed`` (the
Classic data drop runs again only if one of its tables reappears). A run
that cannot finish records ``failed`` with the reason, changes nothing it cannot
redo, and is retried at the next start.

``classic_removal_v1`` is stage 4 of the Classic removal (docs/CLASSIC-REMOVAL.md).
The removed phase ("Classic") engine left three kinds of data:

* the ``analysis_units`` and ``analysis_checkpoints`` tables, which are dropped;
* ``character_observations`` rows, which are deleted (the table stays: series
  memory's switchable history writes it, and a missing table would be recreated
  and re-seeded at startup);
* ``character_references`` rows without a ``projection`` field, which stay. They
  are the current projection of a book with no accepted pipeline evidence.

Order, per the stage 4 data rules:

1. Retain. For every book with Classic data (archived books included), run
   :meth:`bardic.artifacts.ArtifactRepository.backfill` and then this module's
   legacy reader, which copies each ``analysis_units`` row, each checkpoint unit,
   the whole checkpoint and each observation into immutable artifacts
   (``analysis_input``/``analysis_output``, ``analysis_checkpoint``,
   ``character_observation``). Every legacy row must then have its artifact. One
   short transaction per book. If any book fails, nothing is deleted or dropped.
2. Delete each book's observation rows, one short transaction per book, after
   checking again that each has its ``character_observation`` artifact.
3. In one transaction: check every remaining unit and checkpoint once more, drop
   the two tables and record the migration as completed.

Retaining is idempotent (artifacts are content-addressed; a row whose content an
artifact already holds is skipped), so an interrupted run redoes only what it
had not finished. A unit whose key already has a different current result is
retained beside it, unselected: two disagreeing copies are both kept, and the
current selection does not change. ``analysis_attempts``, ``pipeline_*`` tables and
``book_preprocessing`` are never changed; artifacts are only appended.

Classic-written reference rows keep counting in series context while the
observation retained with them exists: :class:`bardic.series._SourceCheck` now
checks the retained ``character_observation`` artifact instead of the deleted
row. Nothing is synced or re-attributed here; no provenance is fabricated.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import sys

CLASSIC_REMOVAL = 'classic_removal_v1'
# Dropped by CLASSIC_REMOVAL. Nothing else in the application reads or creates them.
LEGACY_TABLES = ('analysis_units', 'analysis_checkpoints')
OBSERVATIONS = 'character_observations'
COUNTED = ('analysis_units', 'analysis_checkpoints', 'checkpoint_units', OBSERVATIONS)


def _now():
    return datetime.now(timezone.utc).isoformat()


def initialize_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
        id TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL)''')


def migration(store, identifier):
    """The recorded row of a migration as ``{id, status, updated_at, **body}``, or None."""
    with store.lock, store.connect() as conn:
        row = conn.execute('SELECT status,updated_at,body FROM schema_migrations WHERE id=?', (identifier,)).fetchone()
    return {'id': identifier, 'status': row[0], 'updated_at': row[1], **json.loads(row[2])} if row else None


def _logger():
    """This module's logger. The server's uvicorn logging configures only its own loggers,
    so a one-time handler makes these lines reach the service log."""
    logger = logging.getLogger(__name__)
    if not logger.hasHandlers():
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter('%(levelname)s:     %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def run_startup_migrations(store):
    """Run every pending migration. Called once by the Runtime, holding the instance lock.

    A migration that fails unexpectedly is logged and retried at the next start. The
    server still starts: each migration deletes only data it has verified is retained.
    """
    try:
        remove_classic_data(store)
    except Exception:
        _logger().exception('Classic removal postponed by an unexpected error; nothing unretained was deleted. '
                            'It runs again at the next start.')


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _record(conn, status, body):
    conn.execute('''INSERT INTO schema_migrations(id,status,updated_at,body) VALUES (?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,body=excluded.body''',
                 (CLASSIC_REMOVAL, status, _now(), json.dumps(body, ensure_ascii=False, sort_keys=True)))


def _census(conn, tables):
    """{book_id: counts} of the Classic data per book, including rows of books that no longer exist."""
    books = {book_id: {'book': True} for (book_id,) in conn.execute('SELECT id FROM books ORDER BY rowid')}

    def add(book_id, field, count):
        books.setdefault(book_id, {'book': False})[field] = count

    if 'analysis_units' in tables:
        for book_id, count in conn.execute('SELECT book_id,COUNT(*) FROM analysis_units GROUP BY book_id'):
            add(book_id, 'analysis_units', count)
    if 'analysis_checkpoints' in tables:
        for book_id, body in conn.execute('SELECT book_id,body FROM analysis_checkpoints'):
            add(book_id, 'analysis_checkpoints', 1)
            try:
                units = len(_checkpoint_units(json.loads(body)))
            except ValueError:  # Unreadable: its book's retention fails and is reported there.
                units = 0
            add(book_id, 'checkpoint_units', units)
    if OBSERVATIONS in tables:
        for book_id, count in conn.execute(f'SELECT book_id,COUNT(*) FROM {OBSERVATIONS} GROUP BY book_id'):
            add(book_id, OBSERVATIONS, count)
    for counts in books.values():
        for field in COUNTED:
            counts.setdefault(field, 0)
    return books


def _empty_counts(conn, book_id):
    exists = conn.execute('SELECT 1 FROM books WHERE id=?', (book_id,)).fetchone() is not None
    return {'book': exists, **{field: 0 for field in COUNTED}}


def _has_classic_data(counts):
    return bool(counts['analysis_units'] or counts['analysis_checkpoints'] or counts[OBSERVATIONS])


def _checkpoint_units(checkpoint):
    units = checkpoint.get('units') if isinstance(checkpoint, dict) else None
    return units if isinstance(units, dict) else {}


def _checkpoint_unit_items(checkpoint):
    """(unit key, unit) of each checkpoint unit, with the checkpoint's producer as the default.

    A unit that is not an object is kept only inside the retained whole checkpoint.
    """
    for key, value in _checkpoint_units(checkpoint).items():
        if isinstance(value, dict):
            unit = deepcopy(value)
            unit.setdefault('provider', checkpoint.get('provider'))
            unit.setdefault('model', checkpoint.get('model'))
            yield unit.get('unit_key') or key, unit


def _unit_payload(unit):
    return {k: deepcopy(v) for k, v in unit.items() if k not in {'input_recipe', 'dependency_artifact_ids'}}


def _retained_version(conn, book_id, kind, key, payload):
    """The ID of a retained version of this book/kind/key with this content (timestamps aside), or None."""
    from .artifacts import _content

    wanted = _content(payload)
    for identifier, stored in conn.execute('SELECT id,payload FROM artifact_versions WHERE book_id=? AND kind=? AND logical_key=?',
                                           (book_id, kind, key)):
        if _content(json.loads(stored)) == wanted:
            return identifier
    return None


# --- the legacy reader: exists only for this migration, inert once the tables are gone --------------

def retain_legacy(conn, book_id, tables):
    """Copy a book's Classic rows into immutable artifacts, inside the caller's transaction.

    Moved here from ``ArtifactRepository.backfill`` in stage 4, unchanged except for
    two additions: the whole checkpoint is retained too (``analysis_checkpoint``, so
    its progress and working copy are not lost with the table), and rows of a book
    that no longer exists are retained without source dependencies. It reads only
    tables that exist, so after the drop it retains nothing. Returns the number of
    rows it read.
    """
    from .artifacts import _source_dependency, capture_observation, output_head, record

    row = conn.execute('SELECT body FROM books WHERE id=?', (book_id,)).fetchone()
    book = json.loads(row[0]) if row else {}
    chapters = {c['id']: c for c in book.get('chapters', []) if isinstance(c.get('text'), str)}
    read = 0

    def save_unit(key, unit, source=None):
        payload = _unit_payload(unit)
        if _retained_version(conn, book_id, 'analysis_output', key, payload):
            return
        # A different current result under the same key (two copies of one unit that disagree)
        # stays current; this content is retained beside it, unselected.
        select = not output_head(conn, book_id, 'analysis_output', key)
        dependencies = _source_dependency(conn, book_id, unit, chapters, source)
        recipe = unit.get('input_recipe')
        if isinstance(recipe, dict):
            input_id = _retained_version(conn, book_id, 'analysis_input', key, recipe) or record(
                conn, book_id, 'analysis_input', key, recipe, label='Saved analysis input', stage=unit.get('stage', ''),
                provider=unit.get('provider'), model=unit.get('model'), dependencies=dependencies,
                legacy_provenance=True, select=not output_head(conn, book_id, 'analysis_input', key))
            dependencies.append(input_id)
        record(conn, book_id, 'analysis_output', key, payload, label='Saved analysis output', stage=unit.get('stage', ''),
               provider=unit.get('provider'), model=unit.get('model'), dependencies=dependencies, legacy_provenance=True,
               select=select)

    if 'analysis_units' in tables:
        for key, source, body in conn.execute('SELECT unit_key,source_hash,body FROM analysis_units WHERE book_id=? ORDER BY rowid',
                                              (book_id,)).fetchall():
            save_unit(key, json.loads(body), source)
            read += 1
    if 'analysis_checkpoints' in tables:
        row = conn.execute('SELECT body FROM analysis_checkpoints WHERE book_id=?', (book_id,)).fetchone()
        if row:
            checkpoint = json.loads(row[0])
            read += 1
            working = checkpoint.get('working_book') if isinstance(checkpoint.get('working_book'), dict) else {}
            baseline = {c.get('id'): c.get('text') for c in working.get('chapters', []) if isinstance(c, dict)}
            for key, unit in _checkpoint_unit_items(checkpoint):
                chapter_id = unit.get('chapter_id')
                same_source = chapter_id in chapters and baseline.get(chapter_id) == chapters[chapter_id]['text']
                save_unit(key, unit, None if same_source else 'unverified-legacy-source')
            # The rest of the checkpoint (status, per-chapter progress, working copy, references)
            # is retained as it was stored. Its inputs are unknown, so it has no dependencies.
            if not _retained_version(conn, book_id, 'analysis_checkpoint', 'book', checkpoint):
                record(conn, book_id, 'analysis_checkpoint', 'book', checkpoint, label='Saved Classic analysis checkpoint',
                       stage='', provider=checkpoint.get('provider'), model=checkpoint.get('model'), legacy_provenance=True)
    if OBSERVATIONS in tables:
        for identifier, body in conn.execute(f'SELECT id,body FROM {OBSERVATIONS} WHERE book_id=? ORDER BY rowid',
                                             (book_id,)).fetchall():
            read += 1
            observation = json.loads(body)
            if not _observation_retained(conn, book_id, identifier, observation):
                capture_observation(conn, book_id, observation, chapters)
    return read


def unretained(conn, book_id, tables):
    """{table: count} of a book's Classic rows whose content no retained artifact holds (empty when all do)."""
    missing = {}
    if 'analysis_units' in tables:
        for key, body in conn.execute('SELECT unit_key,body FROM analysis_units WHERE book_id=?', (book_id,)).fetchall():
            if not _retained_version(conn, book_id, 'analysis_output', key, _unit_payload(json.loads(body))):
                missing['analysis_units'] = missing.get('analysis_units', 0) + 1
    if 'analysis_checkpoints' in tables:
        row = conn.execute('SELECT body FROM analysis_checkpoints WHERE book_id=?', (book_id,)).fetchone()
        if row:
            checkpoint = json.loads(row[0])
            lost = sum(1 for key, unit in _checkpoint_unit_items(checkpoint)
                       if not _retained_version(conn, book_id, 'analysis_output', key, _unit_payload(unit)))
            if not _retained_version(conn, book_id, 'analysis_checkpoint', 'book', checkpoint) or lost:
                missing['analysis_checkpoints'] = 1 + lost
    if OBSERVATIONS in tables:
        for identifier, body in conn.execute(f'SELECT id,body FROM {OBSERVATIONS} WHERE book_id=?', (book_id,)).fetchall():
            if not _observation_retained(conn, book_id, identifier, json.loads(body)):
                missing[OBSERVATIONS] = missing.get(OBSERVATIONS, 0) + 1
    return missing


# The fields that locate an observation and its reading; its ID is a hash of them (bardic.series).
_OBSERVATION_CORE = ('book_id', 'character_id', 'chapter_id', 'segment_id', 'start', 'end', 'source_hash', 'quote', 'kind',
                     'description', 'direction', 'provider', 'model', 'confidence')


def _observation_retained(conn, book_id, identifier, observation):
    """True when the current ``character_observation`` artifact for this ID carries the row's reading.

    A profile request may have retained the same observation with extra provenance fields;
    the fields the ID hashes must match.
    """
    row = conn.execute("""SELECT v.payload FROM artifact_heads h JOIN artifact_versions v ON v.id=h.artifact_id
        WHERE h.book_id=? AND h.kind='character_observation' AND h.logical_key=?""", (book_id, identifier)).fetchone()
    if not row:
        return False
    retained = json.loads(row[0])
    return isinstance(retained, dict) and all(retained.get(f) == observation.get(f) for f in _OBSERVATION_CORE)


class MigrationError(RuntimeError):
    pass


def _classic_references(conn):
    """{book_id: [rows, rows still counted in series context]} of reference rows the Classic engine wrote."""
    from .series import CONTEXT_KINDS, _SourceCheck, observation_of, reference_is_valid, source_hash

    result = {}
    sources = _SourceCheck(conn)
    books = {}
    for book_id, body in conn.execute("""SELECT book_id,body FROM character_references
            WHERE json_extract(body,'$.projection') IS NULL ORDER BY book_id,rowid""").fetchall():
        ref = json.loads(body)
        if book_id not in books:
            row = conn.execute('SELECT body FROM books WHERE id=?', (book_id,)).fetchone()
            books[book_id] = {c['id']: c for c in json.loads(row[0]).get('chapters', [])} if row else {}
        counts = result.setdefault(book_id, [0, 0])
        counts[0] += 1
        chapters = books[book_id]
        if (ref.get('kind') in CONTEXT_KINDS and reference_is_valid(ref, chapters, {ref.get('character_id')})
                and sources.current(book_id, ref, observation_of(book_id, ref, source_hash(chapters[ref['chapter_id']]['text'])))):
            counts[1] += 1
    return result


def remove_classic_data(store):
    """Stage 4 of the Classic removal (see the module notes). Returns the recorded body, or None when already done."""
    from .artifacts import ArtifactRepository

    log = _logger()
    with store.lock, store.connect() as conn:
        row = conn.execute('SELECT status FROM schema_migrations WHERE id=?', (CLASSIC_REMOVAL,)).fetchone()
        tables = _tables(conn)
        # Done once; a Classic table that reappeared (copied in by hand) is migrated again.
        if row and row[0] == 'completed' and not set(LEGACY_TABLES) & tables:
            return None
        census = _census(conn, tables)
    present = [name for name in LEGACY_TABLES if name in tables]
    pending = [book_id for book_id, counts in census.items() if _has_classic_data(counts)]
    totals = {field: sum(c[field] for c in census.values()) for field in COUNTED}
    log.info('Classic removal (%s): %d book(s) in the library; legacy rows: analysis_units %d, analysis_checkpoints %d '
             '(%d checkpoint units), character_observations %d, in %d book(s). Tables present: %s.',
             CLASSIC_REMOVAL, sum(1 for c in census.values() if c['book']), totals['analysis_units'],
             totals['analysis_checkpoints'], totals['checkpoint_units'], totals[OBSERVATIONS], len(pending),
             ', '.join(present) or 'none')

    # 1. Retain every book's Classic data as artifacts, and verify it, before deleting anything.
    repository = ArtifactRepository(store)
    failures = {}
    for book_id in pending:
        try:
            with store.lock:
                before = repository.counts(book_id)['total']
                if census[book_id]['book']:
                    repository.backfill(book_id)
                with store.connect() as conn:
                    retain_legacy(conn, book_id, tables)
                    missing = unretained(conn, book_id, tables)
                    if missing:
                        raise MigrationError(f'rows without a retained artifact: {missing}')
                census[book_id]['artifacts_added'] = repository.counts(book_id)['total'] - before
            log.info('Classic removal: book %s%s: retained %d analysis unit(s), %d checkpoint(s) with %d unit(s) and '
                     '%d observation(s) as artifacts (%d new artifact version(s)).', book_id,
                     '' if census[book_id]['book'] else ' (no longer in the library)', census[book_id]['analysis_units'],
                     census[book_id]['analysis_checkpoints'], census[book_id]['checkpoint_units'],
                     census[book_id][OBSERVATIONS], census[book_id]['artifacts_added'])
        except Exception as exc:  # Retried at the next start; nothing is deleted meanwhile.
            log.exception('Classic removal: could not retain the legacy data of book %s', book_id)
            failures[book_id] = f'{type(exc).__name__}: {exc}'
    if failures:
        with store.lock, store.connect() as conn:
            _record(conn, 'failed', {'books': census, 'failures': failures})
        log.error('Classic removal postponed: %d book(s) could not be retained (%s). Nothing was deleted or dropped; '
                  'the migration runs again at the next start.', len(failures), ', '.join(sorted(failures)))
        return {'status': 'failed', 'books': census, 'failures': failures}

    try:
        body = _delete_and_drop(store, census, pending, present, totals)
    except Exception as exc:  # Each delete and the drop are checked first: nothing unretained is lost.
        log.exception('Classic removal: stopped before completing')
        failures = {'': f'{type(exc).__name__}: {exc}'}
        with store.lock, store.connect() as conn:
            _record(conn, 'failed', {'books': census, 'failures': failures})
        log.error('Classic removal postponed: the legacy tables were not dropped; the migration runs again at the next start.')
        return {'status': 'failed', 'books': census, 'failures': failures}
    log.info('Classic removal: done. Dropped tables: %s. Deleted %d character_observations row(s) (the table is kept; '
             'their history is in character_observation artifacts). Kept %d Classic-written character_references '
             'row(s), %d of them in series context. Recorded as %s in schema_migrations.',
             ', '.join(present) or 'none (already absent)', body['deleted_observations'], body['classic_references'],
             body['classic_references_in_series_context'], CLASSIC_REMOVAL)
    return {'status': 'completed', **body}


def _delete_and_drop(store, census, pending, present, totals):
    """Steps 2 and 3 of :func:`remove_classic_data`, once every book's data is retained."""
    # 2. Delete the observation rows, book by book, each after its artifacts are checked again.
    deleted = 0
    for book_id in pending:
        if not census[book_id][OBSERVATIONS]:
            continue
        with store.lock, store.connect() as conn:
            missing = unretained(conn, book_id, {OBSERVATIONS})
            if missing:  # Retained in step 1 of this run; reaching this means something changed underneath.
                raise MigrationError(f'book {book_id} has observations without artifacts: {missing}')
            deleted += conn.execute(f'DELETE FROM {OBSERVATIONS} WHERE book_id=?', (book_id,)).rowcount

    # 3. Check the units and checkpoints once more, drop the tables and record, in one transaction.
    with store.lock, store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        tables = _tables(conn)
        for book_id in census:
            missing = unretained(conn, book_id, set(LEGACY_TABLES) & tables)
            if missing:
                raise MigrationError(f'book {book_id} has rows without artifacts: {missing}')
        for name in LEGACY_TABLES:
            conn.execute(f'DROP TABLE IF EXISTS {name}')
        references = _classic_references(conn)
        for book_id, (rows, in_context) in references.items():
            counts = census.setdefault(book_id, _empty_counts(conn, book_id))
            counts.update(classic_references=rows, classic_references_in_series_context=in_context)
        body = {'books': {book_id: counts for book_id, counts in census.items()
                          if _has_classic_data(counts) or counts.get('classic_references')},
                'book_count': sum(1 for c in census.values() if c['book']), 'totals': totals,
                'dropped_tables': present, 'deleted_observations': deleted,
                'classic_references': sum(r[0] for r in references.values()),
                'classic_references_in_series_context': sum(r[1] for r in references.values())}
        _record(conn, 'completed', body)
    return body
