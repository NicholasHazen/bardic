"""Immutable, content-addressed history with replaceable current projections.

Artifact dependencies are verified artifact IDs, never inferred model provenance.
Book JSON remains the reader's projection; these records preserve reusable history.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from .errors import NotFound


SCHEMA_VERSION = 1
_TIMESTAMPS = {'created_at', 'updated_at', 'recorded_at', 'checked_at', 'completed_at', 'exported_at'}
_PROJECTION_KINDS = {'source', 'structure', 'scene_map', 'character_profile', 'voice_assignment', 'audio_take'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _content(value):
    """Ignore operational timestamps, but retain story/time-of-day fields."""
    if isinstance(value, dict):
        return {key: _content(item) for key, item in value.items() if key not in _TIMESTAMPS}
    if isinstance(value, (list, tuple)):
        return [_content(item) for item in value]
    return value


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def initialize_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS artifact_versions (
        id TEXT PRIMARY KEY, book_id TEXT NOT NULL, kind TEXT NOT NULL,
        logical_key TEXT NOT NULL, label TEXT NOT NULL, stage TEXT NOT NULL,
        provider TEXT, model TEXT, schema_version INTEGER NOT NULL,
        legacy_provenance INTEGER NOT NULL, payload TEXT NOT NULL,
        payload_bytes INTEGER NOT NULL, dependencies TEXT NOT NULL,
        created_at TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS artifact_versions_book ON artifact_versions(book_id,kind,stage,created_at)')
    conn.execute('''CREATE TABLE IF NOT EXISTS artifact_heads (
        book_id TEXT NOT NULL, kind TEXT NOT NULL, logical_key TEXT NOT NULL,
        artifact_id TEXT NOT NULL REFERENCES artifact_versions(id), updated_at TEXT NOT NULL,
        PRIMARY KEY(book_id,kind,logical_key))''')
    conn.execute('CREATE INDEX IF NOT EXISTS artifact_heads_version ON artifact_heads(artifact_id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS artifact_dependencies (
        artifact_id TEXT NOT NULL REFERENCES artifact_versions(id),
        dependency_id TEXT NOT NULL REFERENCES artifact_versions(id),
        PRIMARY KEY(artifact_id,dependency_id))''')
    conn.execute('CREATE INDEX IF NOT EXISTS artifact_dependencies_input ON artifact_dependencies(dependency_id)')
    # Immutability is an invariant even if a later caller bypasses record().
    for table in ('artifact_versions', 'artifact_dependencies'):
        for operation in ('UPDATE', 'DELETE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_immutable_{operation.lower()}
                BEFORE {operation} ON {table} BEGIN
                SELECT RAISE(ABORT, 'Artifact history is immutable'); END''')
    # SQLite REPLACE can bypass delete triggers when recursive_triggers is off.
    conn.execute('''CREATE TRIGGER IF NOT EXISTS artifact_versions_immutable_replace
        BEFORE INSERT ON artifact_versions WHEN EXISTS(SELECT 1 FROM artifact_versions WHERE id=NEW.id)
        BEGIN SELECT RAISE(ABORT, 'Artifact history is immutable'); END''')


def output_head(conn, book_id, kind, logical_key):
    row = conn.execute('SELECT artifact_id FROM artifact_heads WHERE book_id=? AND kind=? AND logical_key=?',
                       (book_id, kind, logical_key)).fetchone()
    return row[0] if row else None


def record(conn, book_id, kind, logical_key, payload, *, label='', stage='', provider=None,
           model=None, dependencies=(), legacy_provenance=False, select=True):
    """Append a content version and select it, within the caller's transaction.

    ``select=False`` retains a candidate without making it current; the caller
    selects it later with :func:`select_head` (e.g. after user acceptance).
    """
    if any(not isinstance(value, str) or not value for value in (book_id, kind, logical_key)):
        raise ValueError('Artifact book, kind and logical key must be nonempty strings.')
    if not isinstance(label, str) or not isinstance(stage, str):
        raise ValueError('Artifact label and stage must be strings.')
    if provider is not None and not isinstance(provider, str) or model is not None and not isinstance(model, str):
        raise ValueError('Artifact producer metadata must be strings or null.')
    dependencies = tuple(dependencies)
    if any(not isinstance(item, str) or not item for item in dependencies):
        raise ValueError('Artifact dependencies must be existing artifact IDs.')
    dependencies = sorted(set(dependencies))
    for identifier in dependencies:
        if not conn.execute('SELECT 1 FROM artifact_versions WHERE id=?', (identifier,)).fetchone():
            raise ValueError('Artifact dependency does not exist.')
    serialized = _json(payload)
    identity = {'schema_version': SCHEMA_VERSION, 'book_id': book_id, 'kind': kind, 'logical_key': logical_key,
                'stage': stage, 'provider': provider, 'model': model, 'legacy_provenance': bool(legacy_provenance),
                'payload': _content(payload), 'dependencies': dependencies}
    identifier = 'artifact_' + _hash(identity)
    timestamp = _now()
    if not conn.execute('SELECT 1 FROM artifact_versions WHERE id=?', (identifier,)).fetchone():
        conn.execute('''INSERT INTO artifact_versions
            (id,book_id,kind,logical_key,label,stage,provider,model,schema_version,legacy_provenance,
             payload,payload_bytes,dependencies,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier, book_id, kind, logical_key, label, stage, provider, model, SCHEMA_VERSION,
             int(bool(legacy_provenance)), serialized, len(serialized.encode('utf-8')), _json(dependencies), timestamp))
    conn.executemany('INSERT OR IGNORE INTO artifact_dependencies(artifact_id,dependency_id) VALUES (?,?)',
                     [(identifier, dependency) for dependency in dependencies])
    if select:
        select_head(conn, book_id, kind, logical_key, identifier, timestamp)
    return identifier


def select_head(conn, book_id, kind, logical_key, identifier, timestamp=None):
    """Make an existing version of this book/kind/scope the current selection."""
    row = conn.execute('SELECT book_id,kind,logical_key FROM artifact_versions WHERE id=?', (identifier,)).fetchone()
    if row != (book_id, kind, logical_key):
        raise ValueError('Only a retained version of the same artifact scope can be selected.')
    conn.execute('''INSERT INTO artifact_heads(book_id,kind,logical_key,artifact_id,updated_at) VALUES (?,?,?,?,?)
        ON CONFLICT(book_id,kind,logical_key) DO UPDATE SET artifact_id=excluded.artifact_id,updated_at=excluded.updated_at''',
        (book_id, kind, logical_key, identifier, timestamp or _now()))


def _pick(item, fields):
    return {field: deepcopy(item[field]) for field in fields if field in item}


def _source_dependency(conn, book_id, unit, chapters, expected_source=None):
    """Resolve only a verified current source span, never an unverified old hash."""
    chapter = chapters.get(unit.get('chapter_id'))
    start, end = unit.get('start'), unit.get('end')
    if not chapter or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(chapter.get('text', '')):
        return []
    if expected_source is not None:
        from .processing import source_hash
        if source_hash({'chapters': list(chapters.values())}) != expected_source:
            return []
    identifier = output_head(conn, book_id, 'source', chapter['id'])
    if not identifier:
        return []
    row = conn.execute('SELECT payload FROM artifact_versions WHERE id=?', (identifier,)).fetchone()
    return [identifier] if json.loads(row[0]).get('text') == chapter['text'] else []


def _profile_dependency(conn, book_id, character):
    if character.get('edited') or not character.get('profile_input_key'):
        return []
    identifier = output_head(conn, book_id, 'analysis_output', character['profile_input_key'])
    if not identifier:
        return []
    row = conn.execute('SELECT payload FROM artifact_versions WHERE id=?', (identifier,)).fetchone()
    payload = json.loads(row[0])
    candidates = payload.get('result', {}).get('characters', []) if isinstance(payload, dict) else []
    if any(isinstance(c, dict) and all(c.get(field) == character.get(field) for field in ('name', 'description', 'direction'))
           and set(c.get('aliases', [])) <= set(character.get('aliases', [])) for c in candidates):
        return [identifier]
    return []


def capture_take(conn, book, segment, audio, *, legacy_provenance=False):
    """Snapshot one take without recapturing every passage for each audio write."""
    book_id, segment_id = book['id'], segment['id']
    if not isinstance(audio, dict):
        conn.execute('DELETE FROM artifact_heads WHERE book_id=? AND kind=? AND logical_key=?',
                     (book_id, 'audio_take', segment_id))
        return None
    chapter = next((c for c in book.get('chapters', []) if c.get('id') == segment.get('chapter_id')), None)
    dependencies = []
    if chapter and isinstance(chapter.get('text'), str):
        dependencies = _source_dependency(conn, book_id, segment, {chapter['id']: chapter})
        if dependencies and 'text' in segment and chapter['text'][segment['start']:segment['end']] != segment['text']:
            dependencies = []
    return record(conn, book_id, 'audio_take', segment_id,
                  {'segment_id': segment_id, 'chapter_id': segment.get('chapter_id'), 'audio': deepcopy(audio)},
                  label='Passage audio', stage='narration', provider=audio.get('provider'), model=audio.get('model'),
                  dependencies=dependencies, legacy_provenance=legacy_provenance)


def capture_observation(conn, book_id, observation, chapters, *, legacy_provenance=True):
    chapter = chapters.get(observation.get('chapter_id'))
    start, end = observation.get('start'), observation.get('end')
    valid = (chapter and isinstance(chapter.get('text'), str)
             and type(start) is int and type(end) is int and 0 <= start < end <= len(chapter['text'])
             and chapter['text'][start:end] == observation.get('quote')
             and observation.get('source_hash') == hashlib.sha256(chapter['text'].encode('utf-8')).hexdigest())
    dependencies = _source_dependency(conn, book_id, observation, chapters) if valid else []
    return record(conn, book_id, 'character_observation', observation['id'], observation,
                  label='Character observation', stage='discovery', provider=observation.get('provider'),
                  model=observation.get('model'), dependencies=dependencies, legacy_provenance=legacy_provenance)


def capture_series(conn, book_id, *, legacy_provenance=False, only_missing=False):
    """Retain explicit membership and identity links, including their removal."""
    previous = output_head(conn, book_id, 'series_context', 'book') if only_missing else None
    if previous:
        return previous
    member = conn.execute('''SELECT sb.series_id,s.name,sb.position FROM series_books sb
        JOIN series s ON s.id=sb.series_id WHERE sb.book_id=?''', (book_id,)).fetchone()
    links = [dict(zip(('character_id', 'series_character_id', 'name'), row))
             for row in conn.execute('''SELECT l.character_id,l.series_character_id,c.name
                 FROM series_character_links l JOIN series_characters c ON c.id=l.series_character_id
                 WHERE l.book_id=? ORDER BY l.character_id''', (book_id,))]
    payload = {'book_id': book_id, 'membership': dict(zip(('series_id', 'series_name', 'position'), member)) if member else None,
               'links': links}
    return record(conn, book_id, 'series_context', 'book', payload, label='Series membership and character links',
                  stage='series', legacy_provenance=legacy_provenance)


def capture_book(conn, book, *, legacy_provenance=False, only_missing=False):
    """Snapshot portable projections without changing any source or object ID.

    Only current heads for disappeared projection scopes are removed. Immutable
    versions and edges always remain. Minimal legacy book shapes are accepted.
    """
    book_id = book['id']
    captured = {kind: {} for kind in _PROJECTION_KINDS}

    def save(kind, key, payload, **metadata):
        previous = output_head(conn, book_id, kind, key) if only_missing else None
        identifier = previous or record(conn, book_id, kind, key, payload,
                                        legacy_provenance=legacy_provenance, **metadata)
        captured[kind][key] = identifier
        return identifier

    chapters = {c['id']: c for c in book.get('chapters', []) if isinstance(c, dict) and isinstance(c.get('id'), str)}
    segments = [s for s in book.get('segments', []) if isinstance(s, dict) and isinstance(s.get('id'), str)]
    for chapter_id, chapter in chapters.items():
        if isinstance(chapter.get('text'), str):
            save('source', chapter_id, {'chapter_id': chapter_id, 'text': chapter['text'],
                                       'text_sha256': hashlib.sha256(chapter['text'].encode('utf-8')).hexdigest()},
                 label=chapter.get('title', chapter_id), stage='import')
    structure = {'book': _pick(book, ('id', 'title', 'author', 'source_name')),
                 'structure_version': book.get('structure_version'),
                 'chapters': [_pick(c, ('id', 'index', 'title', 'kind', 'source_href', 'title_source', 'narrative_order', 'logical_sections'))
                              for c in chapters.values()]}
    save('structure', 'book', structure, label=book.get('title', 'Book structure'), stage='structure',
         dependencies=captured['source'].values())
    for chapter_id, chapter in chapters.items():
        source_id = captured['source'].get(chapter_id)
        passages = []
        for segment in segments:
            if segment.get('chapter_id') != chapter_id:
                continue
            passage = _pick(segment, ('id', 'chapter_id', 'scene_id', 'start', 'end', 'kind', 'speaker_id', 'confidence',
                                      'direction', 'cues', 'evidence', 'analysis_provider', 'analysis_model', 'edited'))
            start, end = segment.get('start'), segment.get('end')
            valid = (source_id is not None and type(start) is int and type(end) is int
                     and 0 <= start < end <= len(chapter['text'])
                     and ('text' not in segment or chapter['text'][start:end] == segment['text']))
            passage['source_anchor'] = {'artifact_id': source_id, 'start': start, 'end': end} if valid else None
            passages.append(passage)
        scenes = [_pick(scene, ('id', 'chapter_id', 'title', 'summary', 'tone', 'direction', 'segment_ids', 'character_ids', 'edited'))
                  for scene in book.get('scenes', []) if isinstance(scene, dict) and scene.get('chapter_id') == chapter_id]
        save('scene_map', chapter_id, {'chapter_id': chapter_id, 'source_artifact_id': source_id,
                                     'scenes': scenes, 'passages': passages},
             label=chapter.get('title', chapter_id), stage='directing', dependencies=[source_id] if source_id else [])
    for character in book.get('characters', []):
        if not isinstance(character, dict) or not isinstance(character.get('id'), str):
            continue
        character_id = character['id']
        profile = _pick(character, ('id', 'name', 'aliases', 'description', 'direction', 'evidence', 'edited',
                                    'profile_refined', 'profile_model', 'profile_provider', 'profile_priority',
                                    'profile_input_key', 'profile_provisional', 'profile_state'))
        save('character_profile', character_id, profile, label=character.get('name', character_id), stage='profiles',
             dependencies=_profile_dependency(conn, book_id, character))
        save('voice_assignment', character_id, _pick(character, ('id', 'voices', 'voice', 'system_voice')),
             label=character.get('name', character_id), stage='voices')
    for segment in segments:
        if not isinstance(segment.get('audio'), dict):
            continue
        # This is the currently selected metadata, not a claim that today's
        # mutable cast/directions were the inputs used by an earlier renderer.
        previous = output_head(conn, book_id, 'audio_take', segment['id']) if only_missing else None
        captured['audio_take'][segment['id']] = previous or capture_take(
            conn, book, segment, segment['audio'], legacy_provenance=legacy_provenance)
    for kind, scopes in captured.items():
        for (logical_key,) in conn.execute('SELECT logical_key FROM artifact_heads WHERE book_id=? AND kind=?', (book_id, kind)).fetchall():
            if logical_key not in scopes:
                conn.execute('DELETE FROM artifact_heads WHERE book_id=? AND kind=? AND logical_key=?', (book_id, kind, logical_key))
    return captured


_METADATA = ('id', 'book_id', 'kind', 'logical_key', 'label', 'stage', 'provider', 'model',
             'schema_version', 'legacy_provenance', 'payload_bytes', 'dependencies', 'created_at')
_SELECT = ','.join('v.' + field for field in _METADATA)


def _metadata(row):
    item = dict(zip(_METADATA, row))
    item['legacy_provenance'] = bool(item['legacy_provenance'])
    item['dependencies'] = json.loads(item['dependencies'])
    item['is_current'] = bool(row[len(_METADATA)])
    return item


class ArtifactRepository:
    def __init__(self, store):
        self.store = store
        with store.lock, store.connect() as conn:
            initialize_schema(conn)

    record = staticmethod(record)
    capture_book = staticmethod(capture_book)

    def output_head(self, book_id, kind, logical_key):
        with self.store.lock, self.store.connect() as conn:
            return output_head(conn, book_id, kind, logical_key)

    def list(self, book_id, kind=None, stage=None, limit=30, offset=0, current=None):
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError('Choose an artifact page size of 1–200 and a nonnegative offset.')
        if current is not None and type(current) is not bool:
            raise ValueError('Current must be true, false or null.')
        filters, args = ['v.book_id=?'], [book_id]
        for column, value in (('kind', kind), ('stage', stage)):
            if value is not None:
                filters.append(f'v.{column}=?')
                args.append(value)
        if current is not None:
            filters.append('h.artifact_id IS ' + ('NOT NULL' if current else 'NULL'))
        query = ''' FROM artifact_versions v LEFT JOIN artifact_heads h
            ON h.book_id=v.book_id AND h.kind=v.kind AND h.logical_key=v.logical_key AND h.artifact_id=v.id
            WHERE ''' + ' AND '.join(filters)
        with self.store.lock, self.store.connect() as conn:
            total = conn.execute('SELECT COUNT(*)' + query, args).fetchone()[0]
            rows = conn.execute('SELECT ' + _SELECT + ',h.artifact_id IS NOT NULL' + query +
                                ' ORDER BY v.created_at DESC,v.rowid DESC LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        return {'items': [_metadata(row) for row in rows], 'total': total, 'offset': offset, 'limit': limit}

    def get(self, book_id, identifier):
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT ' + _SELECT + ''',h.artifact_id IS NOT NULL,v.payload
                FROM artifact_versions v LEFT JOIN artifact_heads h ON h.book_id=v.book_id AND h.kind=v.kind
                AND h.logical_key=v.logical_key AND h.artifact_id=v.id WHERE v.book_id=? AND v.id=?''',
                               (book_id, identifier)).fetchone()
            dependency_links = [dict(zip(('id', 'book_id'), item)) for item in conn.execute('''
                SELECT dependency.id,dependency.book_id FROM artifact_dependencies d
                JOIN artifact_versions dependency ON dependency.id=d.dependency_id
                JOIN artifact_versions owner ON owner.id=d.artifact_id
                WHERE owner.book_id=? AND owner.id=? ORDER BY dependency.id''', (book_id, identifier))] if row else []
        if row is None:
            raise NotFound('artifact_not_found', 'Artifact not found')
        return {**_metadata(row), 'dependency_links': dependency_links, 'payload': json.loads(row[-1])}

    def counts(self, book_id):
        with self.store.lock, self.store.connect() as conn:
            kinds = [row[0] for row in conn.execute('SELECT DISTINCT kind FROM artifact_versions WHERE book_id=? ORDER BY kind', (book_id,))]
            stages = dict(conn.execute('SELECT stage,COUNT(*) FROM artifact_versions WHERE book_id=? GROUP BY stage', (book_id,)))
            current = conn.execute('SELECT COUNT(*) FROM artifact_heads WHERE book_id=?', (book_id,)).fetchone()[0]
        return {'kinds': kinds, 'stages': stages, 'total': sum(stages.values()), 'current': current}

    def backfill(self, book_id):
        """Retain currently available legacy data without inventing lost inputs."""
        with self.store.lock, self.store.connect() as conn:
            row = conn.execute('SELECT body FROM books WHERE id=?', (book_id,)).fetchone()
            if not row:
                raise NotFound('book_not_found', 'Book not found')
            book = json.loads(row[0])
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            takes = {row[0]: json.loads(row[1]) for row in conn.execute('SELECT segment_id,body FROM takes WHERE book_id=?', (book_id,))} if 'takes' in tables else {}
            for segment in book.get('segments', []):
                if 'takes' in tables:
                    segment['audio'] = takes.get(segment['id'])
            before = conn.execute('SELECT COUNT(*) FROM artifact_versions WHERE book_id=?', (book_id,)).fetchone()[0]
            capture_book(conn, book, legacy_provenance=True, only_missing=True)
            chapters = {c['id']: c for c in book.get('chapters', []) if isinstance(c.get('text'), str)}

            def save_unit(key, unit, source=None):
                if output_head(conn, book_id, 'analysis_output', key):
                    return
                dependencies = _source_dependency(conn, book_id, unit, chapters, source)
                recipe = unit.get('input_recipe')
                if isinstance(recipe, dict):
                    input_id = output_head(conn, book_id, 'analysis_input', key) or record(
                        conn, book_id, 'analysis_input', key, recipe, label='Saved analysis input', stage=unit.get('stage', ''),
                        provider=unit.get('provider'), model=unit.get('model'), dependencies=dependencies,
                        legacy_provenance=True)
                    dependencies.append(input_id)
                payload = {k: deepcopy(v) for k, v in unit.items() if k not in {'input_recipe', 'dependency_artifact_ids'}}
                record(conn, book_id, 'analysis_output', key, payload, label='Saved analysis output', stage=unit.get('stage', ''),
                       provider=unit.get('provider'), model=unit.get('model'), dependencies=dependencies, legacy_provenance=True)

            if 'analysis_units' in tables:
                for key, source, body in conn.execute('SELECT unit_key,source_hash,body FROM analysis_units WHERE book_id=? ORDER BY rowid', (book_id,)).fetchall():
                    save_unit(key, json.loads(body), source)
            if 'analysis_checkpoints' in tables:
                row = conn.execute('SELECT body FROM analysis_checkpoints WHERE book_id=?', (book_id,)).fetchone()
                checkpoint = json.loads(row[0]) if row else {}
                baseline = {c['id']: c.get('text') for c in checkpoint.get('working_book', {}).get('chapters', [])}
                for key, value in checkpoint.get('units', {}).items():
                    unit = deepcopy(value)
                    unit.setdefault('provider', checkpoint.get('provider'))
                    unit.setdefault('model', checkpoint.get('model'))
                    chapter_id = unit.get('chapter_id')
                    same_source = chapter_id in chapters and baseline.get(chapter_id) == chapters[chapter_id]['text']
                    save_unit(unit.get('unit_key') or key, unit, None if same_source else 'unverified-legacy-source')
            if 'character_observations' in tables:
                for identifier, body in conn.execute('SELECT id,body FROM character_observations WHERE book_id=? ORDER BY rowid', (book_id,)).fetchall():
                    if output_head(conn, book_id, 'character_observation', identifier):
                        continue
                    observation = json.loads(body)
                    capture_observation(conn, book_id, observation, chapters)
            if 'book_preprocessing' in tables:
                row = conn.execute('SELECT fingerprint,body FROM book_preprocessing WHERE book_id=?', (book_id,)).fetchone()
                if row and not output_head(conn, book_id, 'census', 'book'):
                    record(conn, book_id, 'census', 'book', json.loads(row[1]), label='Saved local census',
                           stage='census', provider='local', legacy_provenance=True)
            if {'series_books', 'series_character_links'} <= tables:
                capture_series(conn, book_id, legacy_provenance=True, only_missing=True)
            after = conn.execute('SELECT COUNT(*) FROM artifact_versions WHERE book_id=?', (book_id,)).fetchone()[0]
        return {'book_id': book_id, 'added': after - before, **self.counts(book_id)}
