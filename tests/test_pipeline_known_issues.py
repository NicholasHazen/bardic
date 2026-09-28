"""Analysis pipeline API behavior fixed for contract 0.2.0 (issue #17).

Offline: synthetic prose and the fake metered provider from test_analysis_pipeline.
Each test pins one formerly defective or inconsistent behavior and its error code.
"""
import json
import sqlite3

from test_analysis_pipeline import MODEL, client, import_book, latest, run  # noqa: F401  (client is a fixture)

import bardic.pipeline.repository


def base(book):
    return f"/api/books/{book['id']}/analysis-pipeline"


def row_counts(client):
    db = client.app.state.runtime.store.db
    with sqlite3.connect(db) as conn:
        return {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                for table in ('pipeline_decisions', 'pipeline_step_runs', 'artifact_versions', 'artifact_heads',
                              'pipeline_state')}


def test_unknown_step_in_a_body_is_400_and_in_a_path_is_404(client):  # noqa: F811
    book = import_book(client)
    for route in ('plan', 'runs'):
        response = client.post(f'{base(book)}/{route}', json={'steps': ['census', 'nope']})
        assert response.status_code == 400, response.text
        assert response.json()['code'] == 'unknown_step'
    response = client.get(f'{base(book)}/steps/nope/versions')
    assert response.status_code == 404 and response.json()['code'] == 'step_not_found'


def test_an_llm_step_without_a_model_is_refused_at_plan_and_run(client):  # noqa: F811
    book = import_book(client)
    run(client, book['id'], ['structure', 'census'])
    runtime = client.app.state.runtime
    runtime.preferences['preprocess_models_by_provider']['openai'] = None
    settings = next(s for s in client.get('/api/analysis-pipeline').json()['steps'] if s['id'] == 'discovery')['settings']
    assert settings['model'] is None
    plan = client.post(f'{base(book)}/plan', json={'steps': ['discovery']})
    assert plan.status_code == 400 and plan.json()['code'] == 'step_model_missing'
    started = client.post(f'{base(book)}/runs', json={'steps': ['discovery'], 'limits': {'max_requests': 5}})
    assert started.status_code == 400 and started.json()['code'] == 'step_model_missing'
    assert not [job for job in client.get('/api/jobs').json() if job['status'] in {'queued', 'running'}]
    # A per-request model still works.
    plan = client.post(f'{base(book)}/plan', json={'steps': ['discovery'],
                                                    'configs': {'discovery': {'provider': 'openai', 'model': MODEL}}})
    assert plan.status_code == 200, plan.text


def test_saved_settings_that_no_longer_validate_are_reported_as_not_saved(client):  # noqa: F811
    store = client.app.state.runtime.store
    with store.lock, store.connect() as conn:
        conn.execute('INSERT OR REPLACE INTO settings(id,body) VALUES (?,?)',
                     ('analysis_pipeline', json.dumps({'steps': {'discovery': {'provider': 'openai', 'model': 'no spaces allowed',
                                                                              'gate': 'review'}}})))
    steps = {s['id']: s['settings'] for s in client.get('/api/analysis-pipeline').json()['steps']}
    assert steps['discovery']['saved'] is False
    assert steps['discovery']['saved_invalid'] is True
    assert steps['discovery']['model'] == MODEL
    assert steps['census']['saved'] is False and steps['census']['saved_invalid'] is False


def test_rejecting_checks_the_book_and_can_decline_a_same_as_accepted_candidate(client):  # noqa: F811
    book = import_book(client)
    job, _ = run(client, book['id'], ['structure', 'census', 'discovery'])
    assert job['status'] == 'completed'
    job, _ = run(client, book['id'], ['discovery'], gates={'discovery': 'review'})
    assert job['status'] == 'completed'
    candidate = latest(client, book['id'], 'discovery')
    assert candidate['state'] == 'same_as_accepted'
    path = f"{base(book)}/steps/discovery/versions/{candidate['id']}/reject"

    missing = client.post(f"/api/books/missing/analysis-pipeline/steps/discovery/versions/{candidate['id']}/reject", json={})
    assert missing.status_code == 404 and missing.json()['code'] == 'book_not_found'
    assert client.post(f"/api/books/{book['id']}/archive").status_code == 200
    archived = client.post(path, json={})
    assert archived.status_code == 409 and archived.json()['code'] == 'book_archived'
    assert client.post(f"/api/books/{book['id']}/restore").status_code == 200

    heads = client.get(f"{base(book)}/steps/discovery/versions/accepted").json()['scopes']
    rejected = client.post(path, json={})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()['action'] == 'reject'
    assert latest(client, book['id'], 'discovery')['state'] == 'rejected'
    # Declining the duplicate run leaves the identical accepted content accepted.
    assert client.get(f"{base(book)}/steps/discovery/versions/accepted").json()['scopes'] == heads
    accepted_version = client.get(f"{base(book)}/steps/discovery/versions").json()['items'][1]
    assert accepted_version['state'] == 'accepted'
    refused = client.post(f"{base(book)}/steps/discovery/versions/{accepted_version['id']}/reject", json={})
    assert refused.status_code == 409 and refused.json()['code'] == 'version_accepted'


def test_started_run_is_a_snapshot_not_the_object_the_worker_mutates(client, monkeypatch):  # noqa: F811
    book = import_book(client)
    runtime = client.app.state.runtime

    class Immediate:
        """Run the job inside the request, as a worker that wins the race against serialization would."""
        def submit(self, fn, *args, **kwargs):
            fn(*args, **kwargs)

        def shutdown(self, *args, **kwargs):
            pass

    class Worker:
        """Mutates the run it was handed, as RunExecutor does while it records step versions."""
        def __init__(self, store, registry, run, **kwargs):
            self.run = run

        def execute(self):
            self.run['step_run_ids'].append('recorded-by-the-worker')
            self.run['status'] = 'running'

    monkeypatch.setattr(runtime, 'pool', Immediate())
    monkeypatch.setattr('bardic.pipeline.api.RunExecutor', Worker)
    plan = client.post(f'{base(book)}/plan', json={'steps': ['structure']}).json()
    response = client.post(f'{base(book)}/runs', json={'steps': ['structure'], 'expected_fingerprint': plan['fingerprint']})
    assert response.status_code == 200, response.text
    assert response.json()['run']['status'] == 'queued'
    assert response.json()['run']['step_run_ids'] == []


def test_book_overview_get_records_nothing_but_reports_the_state_a_post_would_record(client):  # noqa: F811
    book = import_book(client)
    before = row_counts(client)
    overview = client.get(base(book)).json()
    assert row_counts(client) == before
    # A later POST records the outside state; the GET already reported the same counts.
    assert client.post(f'{base(book)}/plan', json={'steps': ['structure']}).status_code == 200
    assert row_counts(client) != before
    recorded = client.get(base(book)).json()
    summary = lambda view: [(s['id'], s['accepted_scopes'], s['has_accepted'], s['accepted_origins'], s['stale_scopes'])
                            for s in view['steps']]
    assert summary(overview) == summary(recorded)


def test_pipeline_schema_is_initialized_once_not_per_request(client, monkeypatch):  # noqa: F811
    book = import_book(client)
    calls = []
    monkeypatch.setattr(bardic.pipeline.repository, 'initialize_schema', lambda conn: calls.append(conn))
    client.get(base(book))
    client.get(f'{base(book)}/steps/structure/versions')
    client.post(f'{base(book)}/plan', json={'steps': ['structure']})
    assert calls == []


def test_provider_alias_and_internal_step_run_fields_are_gone(client):  # noqa: F811
    providers = client.get('/api/analysis-pipeline').json()['providers']
    assert all('has_api_key' not in p and isinstance(p['configured'], bool) for p in providers)
    book = import_book(client)
    run(client, book['id'], ['structure'])
    version = latest(client, book['id'], 'structure')
    detail = client.get(f"{base(book)}/steps/structure/versions/{version['id']}").json()
    assert detail['run'] and 'conflicts' not in detail['run']


def test_version_paging_is_clamped_and_an_unknown_compare_is_400(client):  # noqa: F811
    book = import_book(client)
    run(client, book['id'], ['structure'])
    version = latest(client, book['id'], 'structure')
    path = f"{base(book)}/steps/structure/versions/{version['id']}"
    clamped = client.get(path, params={'offset': -5, 'limit': 5000})
    assert clamped.status_code == 200, clamped.text
    assert clamped.json()['offset'] == 0 and clamped.json()['limit'] == 1000
    assert client.get(path, params={'limit': 0}).json()['limit'] == 1
    unknown = client.get(path, params={'compare': 'f' * 32})
    assert unknown.status_code == 400 and unknown.json()['code'] == 'unknown_version'
    missing = client.get(f"{base(book)}/steps/structure/versions/{'f' * 32}")
    assert missing.status_code == 404 and missing.json()['code'] == 'step_version_not_found'


def test_missing_credentials_have_a_code_and_no_ui_location(client):  # noqa: F811
    book = import_book(client)
    run(client, book['id'], ['structure', 'census'])
    client.app.state.runtime.api_keys.pop('openai', None)
    client.post('/api/settings', json={'api_keys': {'openai': ''}})
    response = client.post(f'{base(book)}/runs', json={'steps': ['discovery'], 'limits': {'max_requests': 5}})
    assert response.status_code == 400 and response.json()['code'] == 'api_key_missing'
    assert 'Settings' not in response.json()['detail']
