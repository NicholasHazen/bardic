"""Durable pipeline state: runs, candidate versions, accepted heads, decisions.

Storage map
-----------
* A **version** is an immutable ``step_output`` artifact, one per step and
  scope (``logical_key = '<step>:<scope>'``). Candidates are recorded with
  ``select=False``; the artifact head for that key is the **accepted** version.
* ``pipeline_decisions`` is an append-only log of accept/reject actions, with
  who or what made them (user, auto policy, baseline or external capture).
* ``pipeline_step_runs`` groups the versions produced by one execution of one
  step (the unit the UI calls a version of the step), with its configuration,
  the accepted inputs it read, and progress.
* ``pipeline_runs`` is one orchestrated request (one or several steps).
* ``pipeline_units`` caches validated unit results by exact request identity so
  resumed or repeated work is not paid for twice.
"""
from __future__ import annotations

import json
from uuid import uuid4

from ..artifacts import output_head, record, select_head
from ..store import now

KIND = 'step_output'
PAYLOAD_SCHEMA = 1
ORIGINS = ('run', 'baseline', 'external')
MODES = ('user', 'auto', 'baseline', 'external')
ACTIVE = ('queued', 'running')


def version_key(step_id, scope):
    return f'{step_id}:{scope}'


def initialize_schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS pipeline_runs (id TEXT PRIMARY KEY, book_id TEXT NOT NULL, body TEXT NOT NULL)')
    conn.execute('CREATE INDEX IF NOT EXISTS pipeline_runs_book ON pipeline_runs(book_id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS pipeline_step_runs (id TEXT PRIMARY KEY, book_id TEXT NOT NULL,
        run_id TEXT, step_id TEXT NOT NULL, body TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS pipeline_step_runs_book ON pipeline_step_runs(book_id,step_id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS pipeline_decisions (id TEXT PRIMARY KEY, book_id TEXT NOT NULL,
        step_id TEXT NOT NULL, body TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS pipeline_decisions_book ON pipeline_decisions(book_id,step_id)')
    for operation in ('UPDATE', 'DELETE'):
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS pipeline_decisions_immutable_{operation.lower()}
            BEFORE {operation} ON pipeline_decisions BEGIN
            SELECT RAISE(ABORT, 'Pipeline decisions are append-only'); END''')
    conn.execute('''CREATE TABLE IF NOT EXISTS pipeline_units (book_id TEXT NOT NULL, unit_key TEXT NOT NULL,
        step_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(book_id,unit_key))''')
    conn.execute('CREATE TABLE IF NOT EXISTS pipeline_state (book_id TEXT PRIMARY KEY, body TEXT NOT NULL)')


class PipelineRepository:
    def __init__(self, store):
        self.store = store
        with store.lock, store.connect() as conn:
            initialize_schema(conn)

    def recover_interrupted(self):
        """At startup, no pipeline work is running: mark leftovers interrupted."""
        with self.store.lock, self.store.connect() as conn:
            for table in ('pipeline_runs', 'pipeline_step_runs'):
                for identifier, body in conn.execute(f'SELECT id,body FROM {table}').fetchall():
                    value = json.loads(body)
                    if value.get('status') in ACTIVE:
                        value.update(status='interrupted', updated_at=now(),
                                     error='Server restarted. Run the step again; validated units are reused.')
                        conn.execute(f'UPDATE {table} SET body=? WHERE id=?', (json.dumps(value, ensure_ascii=False), identifier))

    # --- runs -----------------------------------------------------------------------
    def create_run(self, book_id, **fields):
        value = {'id': uuid4().hex, 'book_id': book_id, 'status': 'queued', 'created_at': now(),
                 'updated_at': now(), 'step_run_ids': [], 'error': None, **fields}
        with self.store.lock, self.store.connect() as conn:
            conn.execute('INSERT INTO pipeline_runs VALUES (?,?,?)', (value['id'], book_id, json.dumps(value, ensure_ascii=False)))
        return value

    def update_run(self, run_id, **fields):
        return self._update('pipeline_runs', run_id, fields)

    def run(self, run_id):
        return self._get('pipeline_runs', run_id)

    def runs(self, book_id, limit=20):
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute('SELECT body FROM pipeline_runs WHERE book_id=? ORDER BY rowid DESC LIMIT ?', (book_id, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    # --- step runs ------------------------------------------------------------------
    def create_step_run(self, book_id, step, *, run_id=None, origin='run', provider=None, model=None,
                        status='queued', inputs=None, chapter_ids=None, conn=None, **fields):
        if origin not in ORIGINS:
            raise ValueError('Unknown step version origin.')
        value = {'id': uuid4().hex, 'book_id': book_id, 'run_id': run_id, 'step_id': step.id,
                 'step_version': step.version, 'origin': origin, 'provider': provider, 'model': model,
                 'status': status, 'inputs': inputs or {}, 'chapter_ids': sorted(chapter_ids) if chapter_ids else None,
                 'scopes': {}, 'unchanged_scopes': [], 'units': {'total': 0, 'done': 0, 'cached': 0, 'failed': 0},
                 'conflicts': [], 'error': None, 'created_at': now(), 'updated_at': now(), 'completed_at': None,
                 **fields}
        row = (value['id'], book_id, run_id, step.id, json.dumps(value, ensure_ascii=False))
        if conn is not None:
            conn.execute('INSERT INTO pipeline_step_runs VALUES (?,?,?,?,?)', row)
            return value
        with self.store.lock, self.store.connect() as own:
            own.execute('INSERT INTO pipeline_step_runs VALUES (?,?,?,?,?)', row)
        return value

    def update_step_run(self, step_run_id, **fields):
        return self._update('pipeline_step_runs', step_run_id, fields)

    def step_run(self, book_id, step_run_id):
        value = self._get('pipeline_step_runs', step_run_id)
        if value['book_id'] != book_id:
            raise KeyError('Step version not found')
        return value

    def step_runs(self, book_id, step_id=None, limit=50):
        query, args = 'SELECT body FROM pipeline_step_runs WHERE book_id=?', [book_id]
        if step_id:
            query += ' AND step_id=?'
            args.append(step_id)
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute(query + ' ORDER BY rowid DESC LIMIT ?', [*args, limit]).fetchall()
        return [json.loads(row[0]) for row in rows]

    # --- unit cache -------------------------------------------------------------------
    def unit(self, book_id, unit_key):
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT body FROM pipeline_units WHERE book_id=? AND unit_key=?', (book_id, unit_key)).fetchone()
        return json.loads(row[0]) if row else None

    def save_unit(self, book_id, step_id, unit_key, value, *, dependencies=()):
        """Cache a validated result and retain it as an immutable analysis output."""
        with self.store.lock, self.store.connect() as conn:
            artifact_id = record(conn, book_id, 'analysis_output', unit_key, value, label=f'{step_id} validated unit',
                                 stage=step_id, provider=value.get('provider'), model=value.get('model'),
                                 dependencies=dependencies)
            conn.execute('INSERT OR REPLACE INTO pipeline_units VALUES (?,?,?,?)',
                         (book_id, unit_key, step_id, json.dumps(value, ensure_ascii=False)))
        return artifact_id

    def forget_unit(self, book_id, unit_key):
        """Drop a cache row that failed revalidation; its artifact history remains."""
        with self.store.lock, self.store.connect() as conn:
            conn.execute('DELETE FROM pipeline_units WHERE book_id=? AND unit_key=?', (book_id, unit_key))

    # --- versions, heads and decisions ------------------------------------------------------
    def record_version(self, conn, book_id, step, scope, result, *, origin, provider=None, model=None,
                       inputs=None, dependencies=()):
        """Retain a candidate for one scope without selecting it."""
        payload = {'schema_version': PAYLOAD_SCHEMA, 'step_id': step.id, 'step_version': step.version,
                   'scope': scope, 'origin': origin, 'inputs': inputs or {}, 'result': result}
        return record(conn, book_id, KIND, version_key(step.id, scope), payload, label=f'{step.label} · {scope}',
                      stage=step.id, provider=provider, model=model, dependencies=dependencies,
                      legacy_provenance=origin != 'run', select=False)

    def heads(self, conn, book_id, step_id):
        prefix = step_id + ':'
        rows = conn.execute('SELECT logical_key,artifact_id FROM artifact_heads WHERE book_id=? AND kind=? AND substr(logical_key,1,?)=?',
                            (book_id, KIND, len(prefix), prefix)).fetchall()
        return {key[len(prefix):]: artifact_id for key, artifact_id in rows}

    def payloads(self, conn, identifiers):
        identifiers = list(dict.fromkeys(identifiers))
        result = {}
        for start in range(0, len(identifiers), 500):
            chunk = identifiers[start:start + 500]
            marks = ','.join('?' * len(chunk))
            for identifier, body in conn.execute(f'SELECT id,payload FROM artifact_versions WHERE kind=? AND id IN ({marks})', [KIND, *chunk]):
                result[identifier] = json.loads(body)
        return result

    def accepted(self, conn, book_id, step_id):
        """{scope: result} and {scope: artifact_id} for the accepted versions."""
        heads = self.heads(conn, book_id, step_id)
        payloads = self.payloads(conn, heads.values())
        return {scope: payloads[identifier]['result'] for scope, identifier in heads.items()}, heads

    def decide(self, conn, book_id, step_id, action, versions, *, mode, step_run_id=None, note=None):
        """Append a decision; accepting selects each scope's version as its head."""
        if action not in {'accept', 'reject'} or mode not in MODES:
            raise ValueError('Unknown pipeline decision.')
        timestamp = now()
        if action == 'accept':
            for scope, identifier in versions.items():
                select_head(conn, book_id, KIND, version_key(step_id, scope), identifier, timestamp)
        value = {'id': uuid4().hex, 'book_id': book_id, 'step_id': step_id, 'action': action, 'mode': mode,
                 'step_run_id': step_run_id, 'versions': dict(versions), 'note': note, 'created_at': timestamp}
        conn.execute('INSERT INTO pipeline_decisions VALUES (?,?,?,?)', (value['id'], book_id, step_id, json.dumps(value, ensure_ascii=False)))
        return value

    def decisions(self, book_id, step_id=None, limit=200):
        query, args = 'SELECT body FROM pipeline_decisions WHERE book_id=?', [book_id]
        if step_id:
            query += ' AND step_id=?'
            args.append(step_id)
        with self.store.lock, self.store.connect() as conn:
            rows = conn.execute(query + ' ORDER BY rowid DESC LIMIT ?', [*args, limit]).fetchall()
        return [json.loads(row[0]) for row in rows]

    def head(self, conn, book_id, step_id, scope):
        return output_head(conn, book_id, KIND, version_key(step_id, scope))

    # --- projection bookkeeping ---------------------------------------------------------------
    def state(self, conn, book_id):
        row = conn.execute('SELECT body FROM pipeline_state WHERE book_id=?', (book_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def set_state(self, conn, book_id, **fields):
        value = {**self.state(conn, book_id), **fields, 'updated_at': now()}
        conn.execute('INSERT OR REPLACE INTO pipeline_state VALUES (?,?)', (book_id, json.dumps(value, ensure_ascii=False)))
        return value

    # --- helpers --------------------------------------------------------------------------------
    def _get(self, table, identifier):
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute(f'SELECT body FROM {table} WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise KeyError('Pipeline record not found')
        return json.loads(row[0])

    def _update(self, table, identifier, fields):
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute(f'SELECT body FROM {table} WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise KeyError('Pipeline record not found')
            value = {**json.loads(row[0]), **fields, 'updated_at': now()}
            conn.execute(f'UPDATE {table} SET body=? WHERE id=?', (json.dumps(value, ensure_ascii=False), identifier))
        return value
