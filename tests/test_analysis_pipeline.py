"""Step pipeline: candidates, acceptance, rollback, edit locks and cost guards.

All provider work uses synthetic prose and fake responses; no network access.
"""
from copy import deepcopy
import json
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.importer import parse_book
from bardic.pipeline import Registry, Step, default_registry
from bardic.pipeline.contract import locked
from bardic.processing import ProcessingStore, request_context

STORY = ('Chapter One\n\nMara lit the lamp by the harbor.\n\n“Stay close,” Mara said.\n\n'
         'Chapter Two\n\nElio opened the gate at dawn.\n\n“We leave now,” Elio said.\n\n“Not yet,” Mara said.\n')
MODEL = 'gpt-6-luna'


class FakeProvider:
    """Deterministic stand-in for the metered OpenAI adapter."""

    def __init__(self):
        self.calls = []
        self.speaker = 'Mara'
        self.fail = None

    def __call__(self, _client, model, key, prompt, schema, cancelled):
        assert key == 'test-openai-secret'
        if 'BOOK EXCERPT:\n' in prompt:
            stage = 'discovery'
            excerpt = prompt.split('BOOK EXCERPT:\n', 1)[1]
            result = {'characters': [
                {'name': name, 'aliases': [], 'description': f'{name} draft.', 'direction': 'Natural.',
                 'evidence': [f'{name} said']} for name in ('Mara', 'Elio') if f'{name} said' in excerpt]}
        elif 'CANDIDATES:\n' in prompt:
            stage = 'profiles'
            candidate = json.JSONDecoder().raw_decode(prompt.split('CANDIDATES:\n', 1)[1])[0][0]
            result = {'characters': [{**candidate, 'description': f"Refined {candidate['name']}.",
                                      'direction': 'Warm and steady.', 'evidence': candidate['evidence'][:1]}]}
        else:
            stage = 'directing'
            passages = json.loads(prompt.split('\nPASSAGES:\n', 1)[1].split('\nCONTEXT AFTER:\n', 1)[0])
            cast = json.JSONDecoder().raw_decode(prompt.split('CAST:\n', 1)[1])[0]
            speaker = next(c['id'] for c in cast if c['name'] == self.speaker)
            result = {'summary': 'A quiet exchange.', 'tone': 'Calm', 'direction': 'Measured.', 'scene_starts': [],
                      'segments': [{'id': p['id'], 'speaker_id': speaker if p['kind'] == 'dialogue' else 'narrator',
                                    'confidence': .9 if p['kind'] == 'dialogue' else 1, 'direction': 'Low and even.',
                                    'cues': [], 'evidence': [p['text']] if p['kind'] == 'dialogue' else []}
                                   for p in passages]}
        context = request_context()
        if context:  # Meter like the real HTTP layer: reserve before the request is sent.
            attempt = context['budget'].reserve('openai', model, {'input': prompt}, context)
            context['budget'].finish(attempt, {'usage': {'input_tokens': 100, 'output_tokens': 50}}, 200)
        self.calls.append(stage)
        if self.fail and self.fail(stage, len(self.calls)):
            raise ValueError('Simulated provider failure')
        return result


@pytest.fixture
def client(tmp_path, monkeypatch):
    for variable in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])

    def no_network(*_args, **_kwargs):
        pytest.fail('Pipeline tests must not make real network requests')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', no_network)
    with TestClient(create_app(tmp_path)) as test_client:
        test_client.provider = FakeProvider()
        monkeypatch.setattr('bardic.analysis._openai_request', test_client.provider)
        test_client.post('/api/settings', json={'api_keys': {'openai': 'test-openai-secret'}, 'analysis_provider': 'openai',
                                                'analysis_models_by_provider': {'openai': MODEL},
                                                'preprocess_models_by_provider': {'openai': MODEL}})
        yield test_client


def import_book(client):
    response = client.post('/api/books', files={'file': ('pipeline.txt', STORY.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()


def wait_job(client, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = next(item for item in client.get('/api/jobs').json() if item['id'] == job_id)
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(.01)
    pytest.fail('pipeline worker did not finish')


def fingerprint(client, book_id, steps):
    return client.post(f'/api/books/{book_id}/analysis-pipeline/plan', json={'steps': steps}).json()['fingerprint']


def run(client, book_id, steps, **body):
    # Like the Analysis tab: preview, then confirm with the plan's fingerprint.
    if 'expected_fingerprint' not in body and 'limits' not in body:
        preview = {'steps': steps, **{k: body[k] for k in ('chapter_ids', 'configs', 'fresh') if k in body}}
        body['expected_fingerprint'] = client.post(f'/api/books/{book_id}/analysis-pipeline/plan', json=preview).json()['fingerprint']
    response = client.post(f'/api/books/{book_id}/analysis-pipeline/runs', json={'steps': steps, **body})
    assert response.status_code == 200, response.text
    return wait_job(client, response.json()['job']['id']), response.json()['run']


def step_state(client, book_id, step_id):
    overview = client.get(f'/api/books/{book_id}/analysis-pipeline').json()
    return next(s for s in overview['steps'] if s['id'] == step_id)


def latest(client, book_id, step_id):
    return client.get(f'/api/books/{book_id}/analysis-pipeline/steps/{step_id}/versions').json()['items'][0]


def speakers(client, book_id):
    book = client.get(f'/api/books/{book_id}').json()
    names = {c['id']: c['name'] for c in book['characters']}
    return [names[s['speaker_id']] for s in book['segments'] if s['kind'] == 'dialogue']


# --- registry and contract -----------------------------------------------------------------------

def test_registry_rejects_invalid_step_graphs():
    class A(Step):
        id, owns = 'alpha', ('segments.speaker_id',)

    class B(Step):
        id, inputs = 'beta', ('alpha',)

    class Claims(Step):
        id, owns = 'gamma', ('segments.speaker_id',)

    assert Registry([A(), B()]).downstream('alpha') == ['beta']
    with pytest.raises(ValueError, match='declared earlier'):
        Registry([B(), A()])
    with pytest.raises(ValueError, match='both claim'):
        Registry([A(), Claims()])
    with pytest.raises(ValueError, match='Duplicate'):
        Registry([A(), A()])
    class Requires(Step):
        id, inputs, requires = 'delta', ('alpha',), ('beta',)

    with pytest.raises(ValueError, match='only require steps it reads'):
        Registry([A(), B(), Requires()])
    bad = A()
    bad.id = 'Bad-ID'
    with pytest.raises(ValueError, match='Invalid pipeline step id'):
        Registry([bad])


def test_builtin_steps_capture_and_apply_are_inverse():
    book = parse_book('story.txt', STORY.encode())
    book['characters'].append({'id': 'character_x', 'name': 'Mara', 'aliases': [], 'description': 'Kind.',
                               'direction': 'Soft.', 'evidence': [], 'profile_refined': True})
    book['segments'][1].update(speaker_id='character_x', confidence=.9, direction='Quietly.', cues=['quiet'])
    for step in default_registry():
        captured = {scope: step.capture(book, scope) for scope in step.scopes(book)}
        captured = {scope: payload for scope, payload in captured.items() if payload is not None}
        after = deepcopy(book)
        assert step.apply(after, deepcopy(captured)) == []
        assert after == book, step.id


def test_locks_are_per_field_and_legacy_edits_lock_everything():
    assert locked({'edited_fields': ['voices']}, 'voices')
    assert not locked({'edited_fields': ['voices'], 'edited': True}, 'description')
    assert locked({'edited': True}, 'description')
    assert locked({'edited_fields': ['*']}, 'direction')
    assert not locked({}, 'direction')


# --- API flow --------------------------------------------------------------------------------------

def test_definitions_expose_steps_and_validated_settings(client):
    body = client.get('/api/analysis-pipeline').json()
    assert [s['id'] for s in body['steps']] == ['structure', 'census', 'discovery', 'quotes', 'profiles', 'directing']
    discovery = next(s for s in body['steps'] if s['id'] == 'discovery')
    assert {s['id']: s['requires'] for s in body['steps']}['directing'] == ['profiles']
    assert discovery['settings'] == {'provider': 'openai', 'model': MODEL, 'gate': 'auto', 'saved': False}
    assert client.put('/api/analysis-pipeline/steps/structure/settings', json={'provider': 'openai', 'model': MODEL}).status_code == 400
    assert client.put('/api/analysis-pipeline/steps/profiles/settings', json={'provider': 'local'}).status_code == 400
    saved = client.put('/api/analysis-pipeline/steps/profiles/settings',
                       json={'provider': 'anthropic', 'model': 'claude-haiku-4-5-20251001', 'gate': 'review'})
    assert saved.status_code == 200 and saved.json()['gate'] == 'review'
    assert client.put('/api/analysis-pipeline/steps/nope/settings', json={'provider': 'local'}).status_code == 404


def test_existing_projection_becomes_a_restorable_baseline(client):
    book = import_book(client)
    state = step_state(client, book['id'], 'directing')
    assert state['accepted_scopes'] == 2 and state['accepted_origins'] == {'baseline': 2}
    assert step_state(client, book['id'], 'discovery')['accepted_scopes'] == 0
    # Idempotent: a second read records nothing new.
    before = client.get(f"/api/books/{book['id']}/analysis-pipeline/steps/directing/versions").json()['items']
    step_state(client, book['id'], 'directing')
    assert client.get(f"/api/books/{book['id']}/analysis-pipeline/steps/directing/versions").json()['items'] == before


def test_full_run_auto_accepts_in_order_and_reuses_validated_units(client):
    book = import_book(client)
    plan = client.post(f"/api/books/{book['id']}/analysis-pipeline/plan", json={'steps': ['discovery', 'profiles', 'directing']}).json()
    assert plan['steps'][0]['requests'] == 2 and plan['steps'][1]['inputs_pending'] == ['discovery']
    job, _ = run(client, book['id'], ['directing', 'discovery', 'profiles'], expected_fingerprint=plan['fingerprint'])
    assert job['status'] == 'completed', job
    assert client.provider.calls.count('discovery') == 2 and client.provider.calls.count('profiles') == 2
    assert speakers(client, book['id']) == ['Mara', 'Mara', 'Mara']
    current = client.get(f"/api/books/{book['id']}").json()
    characters = {c['name']: c for c in current['characters']}
    assert characters['Elio']['description'] == 'Refined Elio.'
    assert (current['analysis']['provider'], current['analysis']['model']) == ('openai', MODEL)
    assert latest(client, book['id'], 'directing')['state'] == 'accepted'
    calls = len(client.provider.calls)
    job, _ = run(client, book['id'], ['discovery'])
    assert job['status'] == 'completed' and len(client.provider.calls) == calls
    repeat = latest(client, book['id'], 'discovery')
    assert repeat['units']['cached'] == 2 and sorted(repeat['unchanged_scopes']) == sorted(c['id'] for c in book['chapters'])


def test_review_gate_candidate_diff_accept_and_rollback(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    assert run(client, book['id'], ['discovery', 'profiles', 'directing'])[0]['status'] == 'completed'
    accepted_version = latest(client, book['id'], 'directing')
    client.provider.speaker = 'Elio'
    job, _ = run(client, book['id'], ['directing'], gates={'directing': 'review'},
                 configs={'directing': {'provider': 'openai', 'model': 'gpt-6-sol'}})
    assert job['status'] == 'completed'
    candidate = latest(client, book['id'], 'directing')
    assert candidate['model'] == 'gpt-6-sol'
    assert candidate['state'] == 'candidate' and step_state(client, book['id'], 'directing')['pending_versions'] == 1
    assert speakers(client, book['id']) == ['Mara', 'Mara', 'Mara']  # the reader is unchanged
    detail = client.get(f"{base}/steps/directing/versions/{candidate['id']}", params={'changed_only': True}).json()
    assert detail['diff']['changed'] == 3 and detail['diff']['agreement'] < 1
    assert {row['_previous']['speaker'] for row in detail['rows']} == {'Mara'}
    impact = client.post(f"{base}/steps/directing/versions/{candidate['id']}/preview", json={}).json()
    assert len(impact['changed_scopes']) == 2 and impact['conflicts'] == []
    stale = client.post(f"{base}/steps/directing/versions/{candidate['id']}/accept", json={'expected_revision': -1})
    assert stale.status_code == 409
    assert client.post(f"{base}/steps/directing/versions/{candidate['id']}/accept", json={}).status_code == 200
    assert speakers(client, book['id']) == ['Elio', 'Elio', 'Elio']
    # Rollback is accepting an earlier version.
    assert client.post(f"{base}/steps/directing/versions/{accepted_version['id']}/accept", json={}).status_code == 200
    assert speakers(client, book['id']) == ['Mara', 'Mara', 'Mara']
    decisions = client.get(f"{base}/steps/directing/versions").json()['decisions']
    assert [d['mode'] for d in decisions[:3]] == ['user', 'user', 'auto']
    assert client.post(f"{base}/steps/directing/versions/{accepted_version['id']}/reject", json={}).status_code == 400
    assert client.post(f"{base}/steps/directing/versions/{candidate['id']}/reject", json={}).status_code == 200
    assert latest(client, book['id'], 'directing')['state'] == 'rejected'


def test_manual_edits_survive_acceptance_and_only_lock_their_fields(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    run(client, book['id'], ['discovery', 'profiles', 'directing'])
    current = client.get(f"/api/books/{book['id']}").json()
    elio = next(c for c in current['characters'] if c['name'] == 'Elio')
    first_dialogue = next(s for s in current['segments'] if s['kind'] == 'dialogue')
    edited = client.patch(f"/api/books/{book['id']}/segments/{first_dialogue['id']}", json={'speaker_id': elio['id']})
    assert edited.status_code == 200
    voice = client.patch(f"/api/books/{book['id']}/characters/{elio['id']}", json={'voices': {'gemini': {'id': 'Puck'}}})
    assert voice.status_code == 200
    client.provider.speaker = 'Mara'
    client.provider.calls.clear()
    job, _ = run(client, book['id'], ['profiles', 'directing'], gates={'directing': 'review'})
    assert job['status'] == 'completed'
    # A voice-only edit does not stop profile refinement (the unit is planned, and reused from cache).
    assert latest(client, book['id'], 'profiles')['units']['total'] == 2
    candidate = latest(client, book['id'], 'directing')
    impact = client.post(f"{base}/steps/directing/versions/{candidate['id']}/preview", json={}).json()
    assert any(c['item_id'] == first_dialogue['id'] and c['field'] == 'speaker_id' for c in impact['conflicts'])
    client.post(f"{base}/steps/directing/versions/{candidate['id']}/accept", json={})
    after = client.get(f"/api/books/{book['id']}").json()
    segment = next(s for s in after['segments'] if s['id'] == first_dialogue['id'])
    assert segment['speaker_id'] == elio['id'] and segment['edited_fields'] == ['speaker_id']
    assert segment['direction'] == 'Low and even.'  # unlocked fields still update


def test_outside_changes_are_recorded_as_external_versions(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    step_state(client, book['id'], 'directing')
    before = client.get(f"/api/books/{book['id']}").json()
    response = client.post(f"/api/books/{book['id']}/analyze", json={'provider': 'local', 'phase': 'full'})
    assert wait_job(client, response.json()['id'])['status'] == 'completed'
    state = step_state(client, book['id'], 'directing')
    assert state['accepted_origins'].get('external', 0) >= 1
    baseline = next(v for v in client.get(f"{base}/steps/directing/versions").json()['items'] if v['origin'] == 'baseline')
    assert client.post(f"{base}/steps/directing/versions/{baseline['id']}/accept", json={}).status_code == 200
    restored = client.get(f"/api/books/{book['id']}").json()
    assert [s['speaker_id'] for s in restored['segments']] == [s['speaker_id'] for s in before['segments']]


def test_budget_limit_keeps_completed_scopes_and_resume_reuses_them(client):
    book = import_book(client)
    job, _ = run(client, book['id'], ['discovery'], limits={'max_requests': 1})
    assert job['status'] == 'budget_limited'
    partial = latest(client, book['id'], 'discovery')
    assert partial['status'] == 'budget_limited' and partial['scope_count'] == 1
    assert partial['state'] == 'candidate'  # partial runs are never auto-accepted
    job, _ = run(client, book['id'], ['discovery'])
    assert job['status'] == 'completed' and client.provider.calls.count('discovery') == 2


def test_runs_have_no_default_caps_but_still_record_each_attempt(client):
    book = import_book(client)
    # An unpriced model used to stop at the default dollar guard before its first request.
    configs = {'discovery': {'provider': 'openai', 'model': 'unpriced-model-x'}}
    job, stored = run(client, book['id'], ['discovery'], configs=configs)
    assert job['status'] == 'completed'
    assert stored['limits'] == {'max_requests': None, 'max_input_tokens': None, 'max_output_tokens': None, 'budget_usd': None}
    assert client.provider.calls.count('discovery') == 2
    recorded = [a for a in ProcessingStore(client.app.state.runtime.store).attempts(book['id']) if a['run_id'] == job['id']]
    assert len(recorded) == 2 and all(a['cost_basis'] == 'unknown' for a in recorded)


def test_steps_need_accepted_inputs_unless_requested_together(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    plan = client.post(f'{base}/plan', json={'steps': ['profiles']}).json()
    assert plan['missing_inputs'] == {'profiles': ['discovery']} and plan['steps'][0]['missing_inputs'] == ['discovery']
    refused = client.post(f'{base}/runs', json={'steps': ['profiles']})
    assert refused.status_code == 400 and 'needs accepted results from Character discovery' in refused.json()['detail']
    # Directing reads the cast in the book; it requires profiles, not discovery.
    refused = client.post(f'{base}/runs', json={'steps': ['directing']})
    assert refused.status_code == 400 and 'from Character profiles.' in refused.json()['detail']
    assert client.post(f'{base}/plan', json={'steps': ['discovery', 'profiles']}).json()['missing_inputs'] == {}
    assert client.provider.calls == [], 'a refused run sends nothing'
    job, _ = run(client, book['id'], ['discovery'])
    assert job['status'] == 'completed'
    assert client.post(f'{base}/plan', json={'steps': ['profiles']}).json()['missing_inputs'] == {}
    assert run(client, book['id'], ['profiles'])[0]['status'] == 'completed'


def test_only_required_inputs_held_for_review_skip_a_step_in_the_same_run(client):
    book = import_book(client)
    run(client, book['id'], ['discovery', 'profiles'])
    # Directing records discovery but reads the accepted cast: a held discovery does not stop it.
    job, _ = run(client, book['id'], ['discovery', 'directing'], gates={'discovery': 'review'}, fresh=True)
    outcomes = client.get(f"/api/books/{book['id']}/analysis-pipeline").json()['recent_runs'][0]['outcomes']
    assert job['status'] == 'completed' and outcomes['discovery']['accepted'] is False
    assert outcomes['directing']['status'] == 'completed'
    # A held required input does.
    run(client, book['id'], ['profiles', 'directing'], gates={'profiles': 'review'}, fresh=True)
    outcomes = client.get(f"/api/books/{book['id']}/analysis-pipeline").json()['recent_runs'][0]['outcomes']
    assert outcomes['directing'] == {'status': 'skipped', 'reason': 'Character profiles is waiting for your review.'}


def test_failed_step_skips_dependents_and_redacts_key(client):
    book = import_book(client)
    client.provider.fail = lambda stage, _count: stage == 'discovery'
    job, run_body = run(client, book['id'], ['discovery', 'profiles'])
    assert job['status'] == 'failed' and 'test-openai-secret' not in json.dumps(job)
    stored = client.get(f"/api/books/{book['id']}/analysis-pipeline").json()['recent_runs'][0]
    assert stored['outcomes']['profiles']['status'] == 'skipped'


def test_run_validation_rejects_unsafe_requests(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    assert client.post(f'{base}/runs', json={'steps': ['nope']}).status_code == 404
    assert client.post(f'{base}/runs', json={'steps': ['discovery'], 'chapter_ids': ['missing']}).status_code == 400
    assert client.post(f'{base}/runs', json={'steps': ['discovery'], 'expected_fingerprint': 'stale'}).status_code == 409
    unconfirmed = client.post(f'{base}/runs', json={'steps': ['census']})
    assert unconfirmed.status_code == 400 and 'expected_fingerprint' in unconfirmed.json()['detail']
    capped = client.post(f'{base}/runs', json={'steps': ['census'], 'limits': {'max_requests': 5}})
    assert capped.status_code == 200 and wait_job(client, capped.json()['job']['id'])['status'] == 'completed'
    client.post('/api/settings', json={'api_keys': {'openai': ''}})
    assert 'API key' in client.post(f'{base}/runs', json={'steps': ['discovery']}).json()['detail']


def test_plain_steps_run_without_keys_and_chapter_scope_narrows_work(client):
    book = import_book(client)
    job, _ = run(client, book['id'], ['structure', 'census'], mode='parallel')
    assert job['status'] == 'completed'
    census = client.get(f"/api/books/{book['id']}/analysis-pipeline/steps/census/versions/accepted").json()
    assert census['stats']['eligible_sections'] == 2
    plan = client.post(f"/api/books/{book['id']}/analysis-pipeline/plan",
                       json={'steps': ['discovery'], 'chapter_ids': [book['chapters'][0]['id']]}).json()
    assert plan['steps'][0]['requests'] == 1


def test_decision_log_is_append_only(client, tmp_path):
    book = import_book(client)
    step_state(client, book['id'], 'directing')
    with sqlite3.connect(tmp_path / 'library.sqlite3') as conn:
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            conn.execute('DELETE FROM pipeline_decisions')


def test_fresh_runs_resample_instead_of_reusing_units(client):
    book = import_book(client)
    run(client, book['id'], ['discovery'])
    calls = len(client.provider.calls)
    run(client, book['id'], ['discovery'])
    assert len(client.provider.calls) == calls
    job, _ = run(client, book['id'], ['discovery'], fresh=True)
    assert job['status'] == 'completed' and len(client.provider.calls) == calls + 2


def test_cancel_keeps_validated_scopes_and_marks_versions_cancelled(client):
    book = import_book(client)
    job_holder = {}

    def cancel_after_first(stage, count):
        # Cancelling while the second request is in flight discards only that response.
        if count == 2:
            client.post(f"/api/jobs/{job_holder['id']}/cancel")
        return False

    client.provider.fail = cancel_after_first
    response = client.post(f"/api/books/{book['id']}/analysis-pipeline/runs", json={'steps': ['discovery'], 'concurrency': 1,
                                                                                  'expected_fingerprint': fingerprint(client, book['id'], ['discovery'])})
    job_holder['id'] = response.json()['job']['id']
    job = wait_job(client, job_holder['id'])
    assert job['status'] == 'cancelled', job
    version = latest(client, book['id'], 'discovery')
    assert version['status'] == 'cancelled' and version['scope_count'] == 1 and version['state'] == 'candidate'


def test_step_failing_before_units_is_settled_and_dependents_skip(client, monkeypatch):
    book = import_book(client)
    from bardic.pipeline.steps.discovery import DiscoveryStep

    def broken(self, ctx):
        raise ValueError('planning failed')

    monkeypatch.setattr(DiscoveryStep, 'units', broken)
    # Planning fails too, so this API caller runs with explicit limits instead of a preview.
    job, _ = run(client, book['id'], ['discovery', 'profiles'], mode='parallel', limits={'max_requests': 50})
    assert job['status'] == 'failed' and 'planning failed' in job['error']
    assert latest(client, book['id'], 'discovery')['status'] == 'failed'
    outcomes = client.get(f"/api/books/{book['id']}/analysis-pipeline").json()['recent_runs'][0]['outcomes']
    assert outcomes['discovery']['status'] == 'failed' and outcomes['profiles']['status'] == 'skipped'


def test_accepting_new_profiles_marks_dependent_direction_stale(client):
    book = import_book(client)
    run(client, book['id'], ['discovery', 'profiles', 'directing'])
    assert step_state(client, book['id'], 'directing')['stale_scopes'] == []
    run(client, book['id'], ['profiles'], configs={'profiles': {'provider': 'openai', 'model': 'gpt-6-sol'}})
    assert len(step_state(client, book['id'], 'directing')['stale_scopes']) == 2


def test_renamed_character_is_not_recreated_by_later_discovery(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    run(client, book['id'], ['discovery'])
    first = latest(client, book['id'], 'discovery')
    mara = next(c for c in client.get(f"/api/books/{book['id']}").json()['characters'] if c['name'] == 'Mara')
    assert client.patch(f"/api/books/{book['id']}/characters/{mara['id']}", json={'name': 'Captain Voss'}).status_code == 200
    job, _ = run(client, book['id'], ['discovery'], configs={'discovery': {'provider': 'openai', 'model': 'gpt-6-sol'}})
    assert job['status'] == 'completed'
    assert client.post(f"{base}/steps/discovery/versions/{first['id']}/accept", json={}).status_code == 200  # rollback
    cast = client.get(f"/api/books/{book['id']}").json()['characters']
    names = [c['name'] for c in cast]
    assert 'Mara' not in names and 'Captain Voss' in names
    voss = next(c for c in cast if c['name'] == 'Captain Voss')
    assert voss['id'] == mara['id'] and 'Mara' not in voss.get('aliases', [])


def test_fresh_plan_counts_cached_units_as_new_requests(client):
    book = import_book(client)
    run(client, book['id'], ['discovery'])
    base = f"/api/books/{book['id']}/analysis-pipeline/plan"
    cached = client.post(base, json={'steps': ['discovery']}).json()
    fresh = client.post(base, json={'steps': ['discovery'], 'fresh': True}).json()
    assert cached['requests'] == 0 and fresh['requests'] == 2 and cached['fingerprint'] != fresh['fingerprint']


def test_unknown_speaker_fallback_clears_confidence_and_evidence():
    book = parse_book('story.txt', STORY.encode())
    step = default_registry().get('directing')
    chapter = book['chapters'][0]['id']
    payload = step.capture(book, chapter)
    dialogue = next(s for s in book['segments'] if s['chapter_id'] == chapter and s['kind'] == 'dialogue')
    payload['segments'][dialogue['id']].update(speaker_id='character_gone', confidence=.9, evidence=['Mara said'])
    conflicts = step.apply(book, {chapter: payload})
    assert [c.field for c in conflicts] == ['speaker_id']
    assert (dialogue['speaker_id'], dialogue['confidence'], dialogue['evidence']) == ('unassigned', 0.0, [])


def test_cancelling_a_queued_run_settles_it(client):
    import threading
    first, second = import_book(client), import_book(client)
    release = threading.Event()
    client.provider.fail = lambda stage, count: not release.wait(5)
    busy = client.post(f"/api/books/{first['id']}/analysis-pipeline/runs",
                       json={'steps': ['discovery'], 'expected_fingerprint': fingerprint(client, first['id'], ['discovery'])}).json()['job']
    queued = client.post(f"/api/books/{second['id']}/analysis-pipeline/runs",
                         json={'steps': ['census'], 'expected_fingerprint': fingerprint(client, second['id'], ['census'])}).json()['job']
    assert client.post(f"/api/jobs/{queued['id']}/cancel").status_code == 200
    release.set()
    wait_job(client, busy['id'])
    assert wait_job(client, queued['id'])['status'] == 'cancelled'
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        overview = client.get(f"/api/books/{second['id']}/analysis-pipeline").json()
        if overview['active_run'] is None:
            break
        time.sleep(.01)
    assert overview['active_run'] is None and overview['recent_runs'][0]['status'] == 'cancelled'


def test_accept_keeps_takes_it_did_not_invalidate(client):
    book = import_book(client)
    store = client.app.state.runtime.store
    segment = book['segments'][1]
    # A take already hidden by an earlier voice change (its recipe no longer matches).
    store.save_take(book['id'], segment['id'], {'provider': 'system', 'model': 'say', 'fingerprint': 'stale', 'asset_id': 'kept'})
    job, _ = run(client, book['id'], ['census', 'structure'])
    assert job['status'] == 'completed'
    assert store.book(book['id'])['segments'][1]['audio']['asset_id'] == 'kept'


def test_accept_only_gives_new_characters_a_device_voice(client, monkeypatch):
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [{'id': 'Samantha', 'name': 'Samantha', 'locale': 'en-US'}])
    book = import_book(client)
    assert client.patch(f"/api/books/{book['id']}/characters/narrator", json={'voices': {'system': None}}).status_code == 200
    job, _ = run(client, book['id'], ['census', 'discovery'])
    assert job['status'] == 'completed'
    cast = {c['id']: c for c in client.app.state.runtime.store.book(book['id'])['characters']}
    assert 'system' not in cast['narrator'].get('voices', {})  # the explicit Default choice survives
    mara = next(c for c in cast.values() if c['name'] == 'Mara')
    assert mara['voices']['system'] == {'id': 'Samantha'}


def test_added_character_locks_only_the_fields_the_owner_set(client):
    book = import_book(client)
    revision = client.get(f"/api/books/{book['id']}").json()['revision']
    added = client.post(f"/api/books/{book['id']}/characters", json={'name': 'Wren'}).json()
    wren = next(c for c in added['characters'] if c['name'] == 'Wren')
    assert wren['edited_fields'] == ['name'] and not locked(wren, 'description')
    assert added['revision'] == revision + 1


def test_saving_a_whole_cast_form_locks_only_changed_fields(client):
    book = import_book(client)
    run(client, book['id'], ['discovery', 'profiles'])
    elio = next(c for c in client.get(f"/api/books/{book['id']}").json()['characters'] if c['name'] == 'Elio')
    # The Cast editor submits every field; only the voice actually changes.
    saved = client.patch(f"/api/books/{book['id']}/characters/{elio['id']}",
                         json={'description': elio['description'], 'direction': elio['direction'],
                               'voices': {'gemini': {'id': 'Charon'}}})
    assert saved.status_code == 200
    elio = next(c for c in saved.json()['characters'] if c['id'] == elio['id'])
    assert elio['edited_fields'] == ['voices']
    client.patch(f"/api/books/{book['id']}/characters/{elio['id']}", json={'description': 'A tired ferryman.'})
    table = client.get(f"/api/books/{book['id']}/analysis-pipeline/steps/profiles/versions/accepted").json()
    assert next(r for r in table['rows'] if r['id'] == elio['id'])['edited'] == 'description'


def test_version_states_reflect_decisions_not_coincidence(client):
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"
    run(client, book['id'], ['census'])
    first = latest(client, book['id'], 'census')
    assert first['state'] == 'accepted'
    # An identical rerun held for review shares the accepted artifact, but nobody accepted it.
    run(client, book['id'], ['census'], gates={'census': 'review'})
    identical = latest(client, book['id'], 'census')
    assert identical['state'] == 'same_as_accepted'
    client.post(f"/api/books/{book['id']}/characters", json={'name': 'Wren'})
    run(client, book['id'], ['census'], gates={'census': 'review'})
    newer = latest(client, book['id'], 'census')
    assert newer['state'] == 'candidate' and step_state(client, book['id'], 'census')['pending_versions'] == 1
    assert client.post(f"{base}/steps/census/versions/{newer['id']}/accept", json={}).status_code == 200
    states = {v['id']: v['state'] for v in client.get(f"{base}/steps/census/versions").json()['items']}
    assert states[newer['id']] == 'accepted' and states[first['id']] == 'superseded'
    assert states[identical['id']] == 'superseded'  # its content was accepted before; it is not waiting
    assert step_state(client, book['id'], 'census')['pending_versions'] == 0
