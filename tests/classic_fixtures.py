"""Libraries that the removed Classic ("phase") engine wrote to, for tests.

``classic_references`` is what a library keeps after the Classic data drop
(``bardic.migrations``): the character reference rows that engine wrote
(replacing the book's rows, as its checkpoint did), with each valid row's
observation retained as a ``character_observation`` artifact and no row left in
``character_observations``.

``add_classic_tables`` gives a library the tables and rows of the version before
the drop, for tests of the migration itself. Synthetic data only.
"""
import json

from bardic.series import retain_observations

UNITS_DDL = ('CREATE TABLE IF NOT EXISTS analysis_units (book_id TEXT, unit_key TEXT, stage TEXT, source_hash TEXT, '
             'body TEXT NOT NULL, PRIMARY KEY(book_id,unit_key))')
UNITS_INDEX = 'CREATE INDEX IF NOT EXISTS analysis_units_stage ON analysis_units(book_id,stage,source_hash)'
CHECKPOINTS_DDL = 'CREATE TABLE IF NOT EXISTS analysis_checkpoints (book_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL)'


def write_references(conn, book_id, references):
    """Replace a book's reference rows, as the Classic checkpoint writer did."""
    conn.execute('DELETE FROM character_references WHERE book_id=?', (book_id,))
    conn.executemany('INSERT INTO character_references (book_id,id,character_id,chapter_id,segment_id,body) VALUES (?,?,?,?,?,?)',
                     [(book_id, ref['id'], ref['character_id'], ref['chapter_id'], ref.get('segment_id'),
                       json.dumps(ref, ensure_ascii=False)) for ref in references])


def classic_references(store, book_id, references):
    """Reference rows the Classic engine wrote, as they are after the Classic data drop."""
    with store.lock, store.connect() as conn:
        write_references(conn, book_id, references)
        # The engine retained each valid row as an observation; the drop kept it as an artifact only.
        retain_observations(conn, book_id, references)
        conn.execute('DELETE FROM character_observations WHERE book_id=?', (book_id,))


def add_classic_tables(conn, *, units=(), checkpoints=(), observations=()):
    """The Classic tables of a library from before the drop, with rows.

    ``units`` are ``(book_id, unit_key, stage, source_hash, unit)``, ``checkpoints``
    ``(book_id, fingerprint, checkpoint)`` and ``observations`` observation records
    (``bardic.series.observation_of`` plus ``recorded_at``), written without artifacts
    as a version before artifact records left them.
    """
    conn.execute(UNITS_DDL)
    conn.execute(UNITS_INDEX)
    conn.execute(CHECKPOINTS_DDL)
    conn.executemany('INSERT INTO analysis_units VALUES (?,?,?,?,?)',
                     [(book_id, key, stage, source, json.dumps(unit)) for book_id, key, stage, source, unit in units])
    conn.executemany('INSERT INTO analysis_checkpoints VALUES (?,?,?)',
                     [(book_id, fingerprint, json.dumps(body)) for book_id, fingerprint, body in checkpoints])
    conn.executemany('INSERT INTO character_observations (id,book_id,character_id,chapter_id,source_hash,body) VALUES (?,?,?,?,?,?)',
                     [(o['id'], o['book_id'], o['character_id'], o['chapter_id'], o['source_hash'], json.dumps(o))
                      for o in observations])
