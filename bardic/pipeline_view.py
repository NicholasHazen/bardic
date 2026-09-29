"""Read-only views and portable exports of durable pipeline knowledge.

Nothing here creates domain records (artifacts, decisions, ledger rows, jobs)
or changes a book. Legacy data is retained as artifacts once, at startup
(:func:`bardic.artifacts.backfill_library`), not by these views. The only
writes are disposable derived caches (the census cache).
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import zipfile

from . import wire
from .artifacts import ArtifactRepository
from .processing import ProcessingStore
from .series import SeriesRepository
from .store import now, public_job


# The public fields of a stored analysis attempt, shared by the inspector and the export.
# Stored rows also hold process IDs and anything older versions saved. The price rates stay public:
# they explain `charged_estimate_usd`.
ATTEMPT_FIELDS = ('id', 'book_id', 'run_id', 'stage', 'unit_key', 'chapter_id', 'provider', 'model', 'status', 'created_at',
                  'completed_at', 'http_status', 'input_tokens', 'output_tokens', 'cached_input_tokens',
                  'cache_write_input_tokens', 'reserved_input_tokens', 'reserved_output_tokens', 'charged_estimate_usd',
                  'cost_basis', 'input_rate', 'output_rate', 'price_as_of', 'price_source', 'elapsed_seconds',
                  'input_artifact_id')


def prepare(store, book_id):
    """The artifact repository. Read-only: legacy retention happens at startup."""
    return ArtifactRepository(store)


def attempt_validation(conn, book_id):
    """Output validation per attempt ID, from all retained events (not a bounded preview)."""
    validation = {}
    for (body,) in conn.execute('SELECT body FROM pipeline_events WHERE book_id=? ORDER BY rowid', (book_id,)):
        event = json.loads(body)
        if event.get('attempt_id') and event['event'] in {'accepted', 'validation_rejected'}:
            validation[event['attempt_id']] = 'accepted' if event['event'] == 'accepted' else 'rejected'
    return validation


def public_attempt(attempt, validation, active_runs):
    """One attempt through the allowlist, with its validation state.

    A ``reserved`` attempt whose run is not active has an unknown outcome
    (``interrupted_unknown``); ``active_runs`` is the set of queued or running job IDs.
    """
    item = {k: attempt[k] for k in ATTEMPT_FIELDS if k in attempt}
    item['validation_state'] = validation.get(attempt.get('id'), 'unknown')
    if item.get('status') == 'reserved' and item.get('run_id') not in active_runs:
        item['status'] = 'interrupted_unknown'
    return item


def story_map(store, book):
    repository = prepare(store, book['id'])
    cast = {c['id'] for c in book['characters']}
    passages = defaultdict(list)
    for passage in book['segments']:
        passages[passage['chapter_id']].append(passage)
    nodes, edges, chapters = [], [], []
    def node_id(kind, identifier):
        return book['id'] + ':' + kind + ':' + identifier
    book_node = node_id('book', book['id'])
    nodes.append({'id': book_node, 'type': 'book', 'book_id': book['id']})
    for index, chapter in enumerate(book['chapters']):
        chapter_node = node_id('chapter', chapter['id'])
        source_id = repository.output_head(book['id'], 'source', chapter['id'])
        nodes.append({'id': chapter_node, 'type': 'chapter', 'chapter_id': chapter['id'], 'source_artifact_id': source_id})
        edges.append({'from': book_node, 'to': chapter_node, 'type': 'contains', 'order': index})
        scenes = []
        for scene in book.get('scenes', []):
            if scene.get('chapter_id') != chapter['id']:
                continue
            members = [p for p in passages[chapter['id']] if p.get('scene_id') == scene['id']]
            spans = [p for p in members if type(p.get('start')) is int and type(p.get('end')) is int]
            # Only cast members: a stale speaker ID no longer in the cast names no character.
            speakers = sorted({p['speaker_id'] for p in members if p.get('kind') == 'dialogue'
                               and p.get('speaker_id') in cast - {'narrator', 'unassigned'}})
            scenes.append({'id': scene['id'], 'title': scene.get('title', ''),
                           'start': min((p['start'] for p in spans), default=None),
                           'end': max((p['end'] for p in spans), default=None),
                           'passage_ids': [p['id'] for p in members], 'character_ids': speakers})
            scene_node = node_id('scene', scene['id'])
            nodes.append({'id': scene_node, 'type': 'scene', 'scene_id': scene['id']})
            edges.append({'from': chapter_node, 'to': scene_node, 'type': 'contains', 'order': len(scenes) - 1})
        chapters.append({'id': chapter['id'], 'title': chapter['title'], 'kind': chapter.get('kind', 'section'),
                         'start': 0, 'end': len(chapter['text']), 'source_artifact_id': source_id,
                         'logical_sections': chapter.get('logical_sections', []), 'scenes': scenes})
        for index, passage in enumerate(passages[chapter['id']]):
            start, end = passage.get('start'), passage.get('end')
            anchored = (type(start) is int and type(end) is int and 0 <= start < end <= len(chapter['text'])
                        and chapter['text'][start:end] == passage.get('text'))
            passage_node = node_id('passage', passage['id'])
            nodes.append({'id': passage_node, 'type': 'passage', 'passage_id': passage['id'],
                          'chapter_id': chapter['id'], 'source_anchor':
                          {'artifact_id': source_id, 'start': start, 'end': end} if anchored else None})
            scene_node = node_id('scene', passage.get('scene_id', ''))
            parent = scene_node if any(s['id'] == passage.get('scene_id') for s in scenes) else chapter_node
            edges.append({'from': parent, 'to': passage_node, 'type': 'contains', 'order': index})
            if index:
                edges.append({'from': node_id('passage', passages[chapter['id']][index - 1]['id']), 'to': passage_node, 'type': 'next'})
            # Every edge ends at a node: an attribution to a speaker no longer in the cast is omitted.
            if passage.get('kind') == 'dialogue' and passage.get('speaker_id') in cast - {'unassigned'}:
                edges.append({'from': passage_node, 'to': node_id('character', passage['speaker_id']),
                              'type': 'attributed_speaker', 'confidence': passage.get('confidence')})
    for character in book['characters']:
        nodes.append({'id': node_id('character', character['id']), 'type': 'character', 'character_id': character['id'], 'name': character['name']})
    references = store.character_references(book['id'])
    counts = Counter(ref.get('kind', 'unknown') for ref in references)
    return {'schema_version': 1, 'book_id': book['id'], 'chapters': chapters,
            'characters': [{'id': c['id'], 'name': c['name']} for c in book['characters']],
            'nodes': nodes, 'edges': edges, 'references': references,
            'reference_counts': {kind: counts[kind] for kind in ('mention', 'dialogue', 'profile_evidence')},
            'note': 'Scene characters are attributed speakers, not verified physical presence. Mentions and profile evidence remain separate source references. Scene boundaries may be local drafts. Offsets are Python Unicode character offsets into the identified chapter source.'}


UNIT_LABELS = {'book': 'book result', 'chapter': 'sections', 'character': 'profiles'}


def pipeline(store, book, valid_audio, registry):
    """The Details explorer: step-pipeline stage cards plus the shared request history.

    Step cards count accepted, out-of-date and candidate results from the step
    pipeline's own tables, the same numbers the Analyze tab shows. Artifact
    counts are retained versions per stage, including older history.
    """
    from .pipeline.api import step_states
    from .pipeline.repository import ACTIVE, PipelineRepository
    repository = prepare(store, book['id'])
    processing = ProcessingStore(store)
    counts = repository.counts(book['id'])
    jobs = store.jobs(book['id'])
    rendering = next((j for j in jobs if j['status'] in ACTIVE and j['kind'] == 'render'), None)
    stages = []

    def stage(identifier, label, completed, total, unit, dependencies, note, status=None, stale=None, candidates=None):
        state = status or ('complete' if total and completed >= total else 'partial' if completed else 'pending')
        stages.append({'id': identifier, 'label': label, 'status': state, 'completed': completed, 'total': total,
                       'unit_label': unit, 'dependencies': dependencies, 'artifact_count': counts['stages'].get(identifier, 0),
                       'stale_count': stale, 'candidate_count': candidates, 'note': note})

    stage('import', 'Source import', len(book['chapters']), len(book['chapters']), 'sections', [],
          'Original source text and stable source locations are retained.')
    member = SeriesRepository(store).membership(book['id'])
    stage('series', 'Series memory', None, None, 'identities', ['import'],
          'Confirmed links to earlier volumes supply their accepted evidence as optional profile context. Each book retains its own evidence.',
          status='available' if member else 'not_started')
    for item in step_states(store, PipelineRepository(store), registry, book):
        step = registry.get(item['id'])
        latest = item['latest'] or {}
        stale = len(item['stale_scopes'])
        status = latest.get('status') if latest.get('status') in ACTIVE else 'stale' if stale else None
        unit = 'eligible sections' if step.chapter_scoped else UNIT_LABELS.get(step.scope, 'results')
        waiting = item['pending_versions']
        note = step.summary + (f" {waiting} {'versions wait' if waiting != 1 else 'version waits'} for review." if waiting else '')
        stage(step.id, step.label, item['accepted_scopes'], item['total_scopes'], unit, list(step.inputs) or ['import'],
              note, status=status, stale=stale, candidates=waiting)
    cast = [c for c in book['characters'] if c['id'] != 'unassigned']
    stage('voices', 'Voice assignments', sum(bool(c.get('voices') or c.get('voice') or c.get('system_voice')) for c in cast), len(cast),
          'voices', ['profiles'], 'Saved choices may be defaults. Review them for the selected narration provider.')
    audio = sum(valid_audio(book, s) for s in book['segments'])
    stage('narration', 'Audio takes', audio, len(book['segments']), 'passages', ['directing', 'voices'],
          'Counts selected takes valid for current performance settings. Earlier takes remain stored.',
          status=rendering['status'] if rendering else None)
    stage('alignment', 'Word alignment', 0, None, 'words', ['narration'],
          'Not implemented. Current read-along timing follows passage boundaries.', status='planned')
    stage('export', 'Reusable analysis export', None, None, 'bundles', ['import'],
          'Download source, graph, profiles, observations, version history and provenance without generating audio. '
          'Audio files use the separate audiobook export.', status='ready')
    events = [wire.complete(dict(event), wire.EVENT_FIELDS) for event in processing.events(book['id'], 100)]
    # Look up validation for displayed attempts across all history, not just the
    # event preview. HTTP success alone never means an output passed validation.
    attempts = processing.attempts(book['id'])[-100:]
    with store.lock, store.connect() as conn:
        validation = attempt_validation(conn, book['id'])
    active_runs = {j['id'] for j in store.jobs(book['id'], limit=None, active=True)}
    # The inspector sends every allowlisted field (null when the stored attempt lacks it); the export bundle keeps
    # its fields as stored, so its content is unchanged.
    public_attempts = [wire.complete(public_attempt(attempt, validation, active_runs), ATTEMPT_FIELDS) for attempt in attempts]
    return {'schema_version': 1, 'book_id': book['id'], 'stages': stages,
            'jobs': [public_job(j) for j in jobs],
            'usage': processing.usage(book['id']), 'attempts': public_attempts, 'events': events,
            'capabilities': {'word_alignment': False}, 'artifact_kinds': counts['kinds'], 'artifact_counts': counts,
            'notes': ['Step counts are accepted results of the analysis pipeline.',
                      'Historical records imported from older versions may lack exact request provenance. Lost versions cannot be reconstructed.',
                      'These are stage-specific counts, not a single percentage for an unknown amount of future work.',
                      'Dependency cards describe the pipeline. The artifact browser records actual retained input dependencies.']}


def write_analysis_export(store, book_id, path):
    """Portable JSON with full transitive artifact dependencies, no audio prerequisite. Writes only ``path``."""
    repository = prepare(store, book_id)
    active_runs = {j['id'] for j in store.jobs(book_id, limit=None, active=True)}
    with store.lock, store.connect() as conn:
        book = store.book(book_id)
        graph = story_map(store, book)
        series = SeriesRepository(store)
        processing = ProcessingStore(store)
        validation = attempt_validation(conn, book_id)
        rows = conn.execute('''WITH RECURSIVE included(id) AS (
            SELECT id FROM artifact_versions WHERE book_id=? UNION
            SELECT d.dependency_id FROM artifact_dependencies d JOIN included i ON d.artifact_id=i.id)
            SELECT v.book_id,v.id FROM artifact_versions v JOIN included i ON i.id=v.id
            ORDER BY v.created_at,v.id''', (book_id,)).fetchall()
        artifacts = [repository.get(owner, identifier) for owner, identifier in rows]
        member = series.membership(book_id)
        identity = {'membership': member, 'links': series.links_for_book(book_id),
                    'series_characters': series.list_characters(member['series_id']) if member else []}
        # The format identifier predates the product rename and is kept. Version 2: analysis-attempts.json
        # holds allowlisted attempts (the inspector's PipelineAttempt shape), not raw stored rows.
        manifest = {'schema_version': 2, 'format': 'spintails-analysis', 'exported_at': now(), 'book_id': book_id,
                    'artifact_count': len(artifacts), 'external_book_dependencies': sorted({owner for owner, _ in rows if owner != book_id}),
                    'audio_files_included': False, 'source_text_included': True, 'word_alignment': False,
                    'coordinate_system': 'Python Unicode character offsets, end exclusive, chapter-local',
                    'notes': ['All retained versions for this book and their transitive input artifacts are included.',
                              'Current reader projections can be rebuilt or consumed independently of the synthesis provider.',
                              'Legacy provenance is explicitly marked; missing historic prompts or outputs are not reconstructed.',
                              'Audio asset IDs identify separately stored files. This bundle does not include audio binaries.']}
        files = {'manifest.json': manifest, 'book.json': book, 'story-map.json': graph, 'series.json': identity,
                 'observations.json': series.observations(book_id), 'references.json': store.character_references(book_id),
                 'analysis-attempts.json': [public_attempt(attempt, validation, active_runs)
                                            for attempt in processing.attempts(book_id)]}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, name in [('resource_operations', 'resource-operations.json'), ('listening_sessions', 'listening-sessions.json'),
                            ('listening_takes', 'listening-takes.json'), ('listening_chunks', 'listening-chunks.json')]:
            if table in tables:
                files[name] = [json.loads(row[0]) for row in conn.execute(f'SELECT body FROM {table} WHERE book_id=? ORDER BY rowid', (book_id,))]
        event_rows = conn.execute('SELECT body FROM pipeline_events WHERE book_id=? ORDER BY rowid', (book_id,)).fetchall()
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, value in files.items():
                archive.writestr(name, json.dumps(value, ensure_ascii=False, indent=2))
            archive.writestr('artifacts.jsonl', ''.join(json.dumps(a, ensure_ascii=False) + '\n' for a in artifacts))
            archive.writestr('pipeline-events.jsonl', ''.join(row[0] + '\n' for row in event_rows))
            archive.writestr('README.txt', 'Bardic reusable analysis bundle\n\nStart with manifest.json. book.json is the current reader projection, including chapter text and passage IDs. story-map.json contains typed nodes, edges and source references. artifacts.jsonl preserves immutable versions, current selection and dependency IDs; legacy_provenance means original production inputs may be incomplete. Dependencies from other books are included transitively. Voice assignments are separate from profiles. Audio binaries and credentials are not included. Local draft scene boundaries, attributed speakers and mentions do not establish physical scene presence.\n')
    return manifest
