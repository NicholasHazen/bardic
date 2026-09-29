"""Cast references as a projection of accepted step evidence.

Synthetic prose and fake provider responses only; no network access.
"""
import json
import sqlite3

import pytest

from bardic.importer import parse_book
from bardic.pipeline import default_registry, projection
from bardic.pipeline import evidence
from bardic.pipeline.repository import PipelineRepository
from bardic.series import source_hash
from bardic.store import Store
from classic_fixtures import classic_references, write_references
from test_analysis_pipeline import FakeProvider, STORY, client, import_book, latest, run  # noqa: F401  (fixture)

# Alternative evidence, each present in only one chapter's excerpt.
ALTERNATIVE = {'Mara': ['Stay close', 'Not yet'], 'Elio': ['We leave now']}


class EvidenceProvider(FakeProvider):
    """FakeProvider whose discovery quotations can be switched to other exact excerpts."""

    def __init__(self):
        super().__init__()
        self.alternative = False

    def __call__(self, _client, model, key, prompt, schema, cancelled):
        result = super().__call__(_client, model, key, prompt, schema, cancelled)
        if self.alternative and self.calls[-1] == 'discovery':
            excerpt = prompt.split('BOOK EXCERPT:\n', 1)[1]
            for item in result['characters']:
                item['evidence'] = [q for q in ALTERNATIVE[item['name']] if q in excerpt]
        return result


@pytest.fixture
def api(client, monkeypatch):
    client.provider = EvidenceProvider()
    monkeypatch.setattr('bardic.analysis._openai_request', client.provider)
    return client


def references(api, book_id, character_id=None):
    if character_id is None:
        return api.app.state.runtime.store.character_references(book_id)
    response = api.get(f'/api/books/{book_id}/characters/{character_id}/references')
    assert response.status_code == 200, response.text
    return response.json()


def book_of(api, book_id):
    return api.get(f'/api/books/{book_id}').json()


def cast(api, book_id):
    return {c['name']: c['id'] for c in book_of(api, book_id)['characters']}


def assert_exact(api, book_id, rows):
    chapters = {c['id']: c['text'] for c in book_of(api, book_id)['chapters']}
    for row in rows:
        assert chapters[row['chapter_id']][row['start']:row['end']] == row['quote'], row


def state(api, book_id):
    store = api.app.state.runtime.store
    with store.connect() as conn:
        return PipelineRepository(store).state(conn, book_id).get(evidence.STATE_KEY)


def heads(api, book_id, step_id):
    store = api.app.state.runtime.store
    with store.connect() as conn:
        return PipelineRepository(store).heads(conn, book_id, step_id)


def observations(api, book_id):
    store = api.app.state.runtime.store
    with store.connect() as conn:
        return [json.loads(body) for (body,) in conn.execute(
            'SELECT body FROM character_observations WHERE book_id=?', (book_id,))]


def test_accepted_steps_project_exact_evidence_to_the_cast_endpoint(api):
    book = import_book(api)
    assert run(api, book['id'], ['discovery', 'profiles', 'directing'])[0]['status'] == 'completed'
    names = cast(api, book['id'])
    mara = references(api, book['id'], names['Mara'])
    assert_exact(api, book['id'], mara)
    by_step = {}
    for row in mara:
        by_step.setdefault((row['kind'], row['step']), []).append(row)
    # Discovery: "Mara said" in each chapter's range, with that version's producer.
    found = by_step[('profile_evidence', 'discovery')]
    discovery_heads = heads(api, book['id'], 'discovery')
    assert sorted((r['chapter_id'], r['quote']) for r in found) == sorted((c['id'], 'Mara said') for c in book['chapters'])
    assert {r['version_id'] for r in found} == set(discovery_heads.values())
    assert {(r['provider'], r['model'], r['origin']) for r in found} == {('openai', 'gpt-6-luna', 'run')}
    assert all(r['profile_description'] == 'Mara draft.' for r in found)
    # Profiles: the quotation occurs in two observations; both locations are kept, marked ambiguous.
    profiled = by_step[('profile_evidence', 'profiles')]
    assert len(profiled) == 2 and {r['anchors'] for r in profiled} == {2}
    assert {r['version_id'] for r in profiled} == {heads(api, book['id'], 'profiles')[names['Mara']]}
    assert profiled[0]['profile_description'] == 'Refined Mara.'
    # Directing: every attributed line, anchored to its passage.
    spoken = by_step[('dialogue', 'directing')]
    passages = {s['id']: s for s in book_of(api, book['id'])['passages']}
    assert len(spoken) == 3 and all(passages[r['passage_id']]['text'] == r['quote'] for r in spoken)
    assert {(r['provider'], r['confidence'], r['origin']) for r in spoken} == {('openai', .9, 'run')}
    # Mentions are exact name matches, not presence.
    mentions = by_step[('mention', None)]
    assert {r['quote'] for r in mentions} == {'Mara'} and len(mentions) == 3
    # Deterministic content-hash IDs and the Cast filter by character.
    assert len({r['id'] for r in mara}) == len(mara)
    assert all(r['character_id'] == names['Mara'] for r in mara)
    assert state(api, book['id'])['counts'].get('invalid', 0) == 0
    # The book was analysed only through the pipeline: every row is a projection row.
    assert all(r['projection'] == evidence.PROJECTION_VERSION for r in references(api, book['id']))


def test_rollback_removes_rolled_back_evidence_and_set_aside_adds_nothing(api):
    book = import_book(api)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    run(api, book['id'], ['discovery', 'profiles', 'directing'])
    first = latest(api, book['id'], 'discovery')
    before = references(api, book['id'])
    api.provider.alternative = True
    job, _ = run(api, book['id'], ['discovery'], gates={'discovery': 'review'}, fresh=True)
    assert job['status'] == 'completed'
    candidate = latest(api, book['id'], 'discovery')
    assert candidate['state'] == 'candidate'
    assert references(api, book['id']) == before  # a candidate changes nothing

    # Set aside: still nothing.
    assert api.post(f"{base}/steps/discovery/versions/{candidate['id']}/reject", json={}).status_code == 200
    assert references(api, book['id']) == before

    # Accept: its quotations appear, the old ones leave.
    assert api.post(f"{base}/steps/discovery/versions/{candidate['id']}/accept", json={}).status_code == 200
    quotes = {r['quote'] for r in references(api, book['id']) if r['step'] == 'discovery'}
    assert quotes == {'Stay close', 'Not yet', 'We leave now'}
    assert_exact(api, book['id'], references(api, book['id']))

    # Rollback (accept the earlier version): the rolled-back quotations are gone.
    assert api.post(f"{base}/steps/discovery/versions/{first['id']}/accept", json={}).status_code == 200
    after = references(api, book['id'])
    assert {r['quote'] for r in after if r['step'] == 'discovery'} == {'Mara said', 'Elio said'}
    assert sorted(r['id'] for r in after) == sorted(r['id'] for r in before)


def test_directing_rollback_moves_dialogue_and_manual_speakers_win(api):
    book = import_book(api)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    run(api, book['id'], ['discovery', 'profiles', 'directing'])
    names = cast(api, book['id'])
    first = latest(api, book['id'], 'directing')
    api.provider.speaker = 'Elio'
    run(api, book['id'], ['directing'], gates={'directing': 'review'}, fresh=True)
    candidate = latest(api, book['id'], 'directing')
    api.post(f"{base}/steps/directing/versions/{candidate['id']}/accept", json={})
    spoken = [r for r in references(api, book['id']) if r['kind'] == 'dialogue']
    assert {r['character_id'] for r in spoken} == {names['Elio']} and len(spoken) == 3
    api.post(f"{base}/steps/directing/versions/{first['id']}/accept", json={})
    spoken = [r for r in references(api, book['id']) if r['kind'] == 'dialogue']
    assert {r['character_id'] for r in spoken} == {names['Mara']} and len(spoken) == 3

    # A manual speaker choice is what the book shows, so it is what Cast lists.
    line = next(s for s in book_of(api, book['id'])['passages'] if s['kind'] == 'dialogue')
    assert api.patch(f"/api/books/{book['id']}/passages/{line['id']}", json={'speaker_id': names['Elio']}).status_code == 200
    # The references route computes the projection from the current book, so it shows the edit at once
    # without recording anything; the stored rows follow at the next write.
    stored = references(api, book['id'])
    row = next(r for r in references(api, book['id'], names['Elio']) if r['passage_id'] == line['id'] and r['kind'] == 'dialogue')
    assert (row['character_id'], row['provider'], row['origin'], row['step']) == (names['Elio'], 'reviewed', 'manual', None)
    assert references(api, book['id']) == stored
    assert api.post(f"{base}/plan", json={'steps': ['census']}).status_code == 200
    row = next(r for r in references(api, book['id']) if r['segment_id'] == line['id'] and r['kind'] == 'dialogue')
    assert row['character_id'] == names['Elio'] and row['provider'] == 'reviewed'


def test_rebuild_is_idempotent_and_writes_no_observations(api):
    book = import_book(api)
    run(api, book['id'], ['discovery', 'profiles', 'directing'])
    rows = references(api, book['id'])
    recorded = state(api, book['id'])
    # Series context reads the projection itself; artifacts keep the history (RETAIN_OBSERVATIONS is off).
    assert evidence.RETAIN_OBSERVATIONS is False
    assert observations(api, book['id']) == []
    retained = []
    for _ in range(2):
        api.get(f"/api/books/{book['id']}/analysis-pipeline")
    assert state(api, book['id'])['rebuilt_at'] == recorded['rebuilt_at']  # skipped: nothing changed
    store = api.app.state.runtime.store
    with store.lock, store.connect() as conn:
        forced = evidence.refresh(PipelineRepository(store), conn, store.book(book['id']), force=True)
    assert forced['rows'] == recorded['rows']
    assert references(api, book['id']) == rows
    assert len(observations(api, book['id'])) == len(retained)


def test_classic_rows_written_over_accepted_evidence_are_replaced_by_the_next_sync(api):
    book = import_book(api)
    run(api, book['id'], ['discovery', 'profiles', 'directing'])
    rows = references(api, book['id'])
    chapter = book['chapters'][0]
    start = chapter['text'].index('Mara lit')
    legacy = {'id': 'legacy-1', 'character_id': cast(api, book['id'])['Mara'], 'chapter_id': chapter['id'],
              'segment_id': None, 'start': start, 'end': start + 8, 'quote': 'Mara lit', 'kind': 'profile_evidence',
              'confidence': None, 'provider': 'openai', 'model': 'older', 'profile_description': 'Legacy.'}
    store = api.app.state.runtime.store
    # Rows the removed Classic engine wrote over the projection (kept by the Classic data drop).
    classic_references(store, book['id'], [legacy])
    assert references(api, book['id']) == [legacy]
    # Reads show the rebuilt projection at once and record nothing.
    mara = cast(api, book['id'])['Mara']
    # The route presents stored rows on the wire, where a stored segment_id is a passage_id.
    on_wire = [{('passage_id' if key == 'segment_id' else key): value for key, value in r.items()} for r in rows]
    assert references(api, book['id'], mara) == [r for r in on_wire if r['character_id'] == mara]
    api.get(f"/api/books/{book['id']}/analysis-pipeline")
    assert references(api, book['id']) == [legacy]
    # The next write syncs. Discovery has an accepted version, so legacy discovery evidence is not carried.
    assert api.post(f"/api/books/{book['id']}/analysis-pipeline/plan", json={'steps': ['census']}).status_code == 200
    assert references(api, book['id']) == rows
    # It remains in retained history: its observation artifact, not the (empty) observation table.
    assert observations(api, book['id']) == []
    with store.connect() as conn:
        retained = [json.loads(p) for (p,) in conn.execute(
            "SELECT payload FROM artifact_versions WHERE book_id=? AND kind='character_observation'", (book['id'],))]
    assert any(o['quote'] == 'Mara lit' for o in retained)


# --- direct projector tests --------------------------------------------------------------------

@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path)
    book = parse_book('story.txt', STORY.encode())
    book['id'] = 'book'
    book['characters'].append({'id': 'character_mara', 'name': 'Mara', 'aliases': [], 'description': 'Kind.',
                               'direction': 'Soft.', 'evidence': []})
    store.save_book(book)
    return store, PipelineRepository(store), default_registry()


def accept_payload(store, repository, registry, step_id, scope, result, *, inputs=None):
    step = registry.get(step_id)
    with store.lock, store.connect() as conn:
        identifier = repository.record_version(conn, 'book', step, scope, result, origin='run', provider='openai',
                                               model='gpt-6-luna', inputs=inputs)
        repository.decide(conn, 'book', step_id, 'accept', {scope: identifier}, mode='user')
    return identifier


def rebuild(store, repository, **options):
    with store.lock, store.connect() as conn:
        return evidence.refresh(repository, conn, store.book('book'), **options)


def test_invalid_evidence_is_dropped_and_counted_never_repaired(library):
    store, repository, registry = library
    book = store.book('book')
    first, second = book['chapters']
    whole = len(first['text'])
    payload = {'ranges': [{'start': 0, 'end': whole, 'unit': 'u1'}], 'candidates': [
        {'name': 'Mara', 'aliases': [], 'description': 'D.', 'direction': 'R.', 'range_start': 0,
         'evidence': ['Mara said', 'Mara  said', 'Elio opened the gate']},  # exact, whitespace-altered, other chapter
        {'name': 'Nobody', 'aliases': [], 'description': '', 'direction': '', 'range_start': 0, 'evidence': ['lit the lamp']},
    ]}
    discovery = accept_payload(store, repository, registry, 'discovery', first['id'], payload)
    profile = {'description': 'P.', 'direction': 'Q.', 'profile_refined': True, 'profile_provider': 'openai',
               'profile_model': 'gpt-6-luna', 'profile_priority': 'basic',
               'evidence': ['said', 'A line from an earlier volume.']}
    accept_payload(store, repository, registry, 'profiles', 'character_mara', profile,
                   inputs={'discovery': {first['id']: discovery}})
    entry = rebuild(store, repository)
    counts = entry['counts']
    # Two quotations are not exact slices of their range; "lit the lamp" names nobody in the cast;
    # the earlier-volume quotation has no location in this book.
    assert (counts['invalid'], counts['unresolved'], counts['unanchored']) == (2, 1, 1)
    rows = store.character_references('book')
    evidence_rows = [r for r in rows if r['kind'] == 'profile_evidence']
    assert {(r['step'], r['quote']) for r in evidence_rows} == {('discovery', 'Mara said'), ('profiles', 'said')}
    for row in rows:
        chapter = first if row['chapter_id'] == first['id'] else second
        assert chapter['text'][row['start']:row['end']] == row['quote']
    # The profile quotation is located inside the discovery quotation it was taken from.
    located = next(r for r in evidence_rows if r['step'] == 'profiles')
    anchor = next(r for r in evidence_rows if r['step'] == 'discovery')
    assert anchor['start'] <= located['start'] < located['end'] <= anchor['end'] and located['anchors'] == 1


def test_books_without_accepted_evidence_keep_their_references(library):
    store, repository, _ = library
    legacy = {'id': 'legacy', 'character_id': 'character_mara', 'chapter_id': store.book('book')['chapters'][0]['id'],
              'start': 0, 'end': 4, 'quote': 'Mara', 'kind': 'mention'}
    classic_references(store, 'book', [legacy])
    assert rebuild(store, repository) is None
    assert store.character_references('book') == [legacy]


def test_legacy_discovery_evidence_is_carried_until_discovery_is_accepted(library):
    store, repository, registry = library
    book = store.book('book')
    first = book['chapters'][0]
    start = first['text'].index('Stay close')
    kept = {'id': 'legacy-kept', 'character_id': 'character_mara', 'chapter_id': first['id'], 'segment_id': None,
            'start': start, 'end': start + 10, 'quote': 'Stay close', 'kind': 'profile_evidence'}
    broken = {**kept, 'id': 'legacy-broken', 'quote': 'Stay closer'}
    mention = {**kept, 'id': 'legacy-mention', 'kind': 'mention'}
    classic_references(store, 'book', [kept, broken, mention])
    with store.lock, store.connect() as conn:
        projection.sync(repository, registry, conn, book)  # baseline capture makes the projection authoritative
    rows = store.character_references('book')
    assert kept in rows and all(r['id'] not in {'legacy-broken', 'legacy-mention'} for r in rows)
    assert repository_state(store, repository)['counts']['carried'] == 1
    payload = {'ranges': [{'start': 0, 'end': len(first['text']), 'unit': 'u1'}], 'candidates': [
        {'name': 'Mara', 'aliases': [], 'description': 'D.', 'direction': 'R.', 'range_start': 0, 'evidence': ['Mara said']}]}
    accept_payload(store, repository, registry, 'discovery', first['id'], payload)
    rebuild(store, repository)
    assert all(r['id'] != 'legacy-kept' for r in store.character_references('book'))


def repository_state(store, repository):
    with store.connect() as conn:
        return repository.state(conn, 'book')[evidence.STATE_KEY]


def test_retaining_history_when_enabled_appends_new_rows_once(library, monkeypatch):
    # Disabled: it would only duplicate artifact history. Kept working in case the owner wants the table back.
    monkeypatch.setattr(evidence, 'RETAIN_OBSERVATIONS', True)
    store, repository, registry = library
    first = store.book('book')['chapters'][0]
    payload = {'ranges': [{'start': 0, 'end': len(first['text']), 'unit': 'u1'}], 'candidates': [
        {'name': 'Mara', 'aliases': [], 'description': 'D.', 'direction': 'R.', 'range_start': 0, 'evidence': ['Mara said']}]}
    accept_payload(store, repository, registry, 'discovery', first['id'], payload)
    rebuild(store, repository)

    def retained():
        with sqlite3.connect(store.db) as conn:
            return [json.loads(b) for (b,) in conn.execute("SELECT body FROM character_observations WHERE book_id='book'")]
    before = retained()
    assert [(o['quote'], o['source_hash']) for o in before if o['kind'] == 'profile_evidence'] == \
        [('Mara said', source_hash(first['text']))]
    rebuild(store, repository, force=True)
    assert len(retained()) == len(before)


def test_set_aside_repairs_references_another_writer_replaced(api):
    book = import_book(api)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    run(api, book['id'], ['discovery', 'profiles', 'directing'])
    rows = references(api, book['id'])
    api.provider.alternative = True
    run(api, book['id'], ['discovery'], gates={'discovery': 'review'}, fresh=True)
    candidate = latest(api, book['id'], 'discovery')
    store = api.app.state.runtime.store
    with store.lock, store.connect() as conn:
        write_references(conn, book['id'], [])
    assert references(api, book['id']) == []
    # No read path runs in between: the set-aside decision itself restores the projection.
    assert api.post(f"{base}/steps/discovery/versions/{candidate['id']}/reject", json={}).status_code == 200
    assert references(api, book['id']) == rows


def test_every_recorded_spelling_of_a_name_is_matched_exactly(library):
    store, repository, registry = library
    book = store.book('book')
    next(c for c in book['characters'] if c['id'] == 'character_mara')['aliases'] = ['MARA']
    store.save_book(book)
    with store.lock, store.connect() as conn:
        projection.sync(repository, registry, conn, store.book('book'))
    mentions = [r for r in store.character_references('book') if r['kind'] == 'mention']
    assert {r['quote'] for r in mentions} == {'Mara'} and len(mentions) == 3


def test_a_profile_that_read_no_discovery_locates_nothing(library):
    store, repository, registry = library
    first = store.book('book')['chapters'][0]
    payload = {'ranges': [{'start': 0, 'end': len(first['text']), 'unit': 'u1'}], 'candidates': [
        {'name': 'Mara', 'aliases': [], 'description': 'D.', 'direction': 'R.', 'range_start': 0, 'evidence': ['Mara said']}]}
    accept_payload(store, repository, registry, 'discovery', first['id'], payload)
    profile = {'description': 'P.', 'direction': 'Q.', 'evidence': ['Mara said']}
    # Built only from earlier-volume context: it recorded no discovery versions.
    accept_payload(store, repository, registry, 'profiles', 'character_mara', profile, inputs={'discovery': {}})
    entry = rebuild(store, repository)
    assert entry['counts']['unanchored'] == 1
    assert all(r['step'] != 'profiles' for r in store.character_references('book'))


def test_a_changed_passage_identity_rebuilds_segment_anchors(library):
    store, repository, registry = library
    first = store.book('book')['chapters'][0]
    payload = {'ranges': [{'start': 0, 'end': len(first['text']), 'unit': 'u1'}], 'candidates': [
        {'name': 'Mara', 'aliases': [], 'description': 'D.', 'direction': 'R.', 'range_start': 0,
         'evidence': ['Mara lit the lamp']}]}
    accept_payload(store, repository, registry, 'discovery', first['id'], payload)
    rebuild(store, repository)
    book = store.book('book')
    next(s for s in book['segments'] if s['text'].startswith('Mara lit'))['id'] = 'segment_renamed'
    store.save_book(book)
    assert rebuild(store, repository) is not None
    row = next(r for r in store.character_references('book') if r['step'] == 'discovery')
    assert row['segment_id'] == 'segment_renamed'


def test_demo_book_shows_references_after_baseline_capture(api):
    demo = api.post('/api/demo').json()
    assert references(api, demo['id']) == []
    api.get(f"/api/books/{demo['id']}/analysis-pipeline")  # first sync: baseline capture, then projection
    speaker = next(s['speaker_id'] for s in demo['passages'] if s['kind'] == 'dialogue' and s['speaker_id'] != 'unassigned')
    rows = references(api, demo['id'], speaker)
    assert {'dialogue', 'mention'} <= {r['kind'] for r in rows}
    assert {r['origin'] for r in rows if r['kind'] == 'dialogue'} == {'baseline'}
    assert_exact(api, demo['id'], rows)
