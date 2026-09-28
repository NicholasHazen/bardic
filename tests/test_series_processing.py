"""Series runs on the step pipeline: aggregated plans, consent, reading order, cancellation.

Synthetic prose and a fake metered provider only; no network access.
"""
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from test_analysis_pipeline import MODEL, FakeProvider

SECRET = 'test-openai-secret'


class RecordingProvider(FakeProvider):
    """Records which volume each discovery request read; can hold a request open."""

    def __init__(self):
        super().__init__()
        self.volumes = []
        self.hold = None       # threading.Event a discovery request waits on
        self.hold_volume = None  # only hold this volume's requests (None: any)
        self.entered = threading.Event()

    def __call__(self, client, model, key, prompt, schema, cancelled):
        if 'BOOK EXCERPT:\n' in prompt:
            excerpt = prompt.split('BOOK EXCERPT:\n', 1)[1]
            volume = next((n for n in range(1, 10) if f'volume {n}.' in excerpt), None)
            self.volumes.append(volume)
            if self.hold is not None and self.hold_volume in (None, volume):
                self.entered.set()
                deadline = time.monotonic() + 5
                while not self.hold.is_set() and not cancelled() and time.monotonic() < deadline:
                    time.sleep(.005)
        return super().__call__(client, model, key, prompt, schema, cancelled)


@pytest.fixture
def client(tmp_path, monkeypatch):
    for variable in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])

    def no_network(*_args, **_kwargs):
        pytest.fail('Series tests must not make real network requests')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', no_network)
    with TestClient(create_app(tmp_path)) as test_client:
        test_client.provider = RecordingProvider()
        monkeypatch.setattr('bardic.analysis._openai_request', test_client.provider)
        test_client.post('/api/settings', json={'api_keys': {'openai': SECRET}, 'analysis_provider': 'openai',
                                                'analysis_models_by_provider': {'openai': MODEL},
                                                'preprocess_models_by_provider': {'openai': MODEL}})
        yield test_client


def import_volume(client, n):
    text = f'Chapter One\n\nMara lit the lamp in volume {n}.\n\n“Stay close,” Mara said.\n'
    response = client.post('/api/books', files={'file': (f'volume-{n}.txt', text.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()


def collection(client, positions=(1, 2, 3)):
    series = client.post('/api/series', json={'name': 'The Lanterns'}).json()
    books = []
    for n in positions:
        book = import_volume(client, n)
        assert client.put(f"/api/books/{book['id']}/series", json={'series_id': series['id'], 'position': float(n)}).status_code == 200
        books.append(book)
    return series, books


def wait_job(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.app.state.runtime.store.job(job_id)
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(.01)
    pytest.fail('series worker did not finish')


def preview(client, series, **body):
    response = client.post(f"/api/series/{series['id']}/plan", json={'steps': ['discovery'], **body})
    assert response.status_code == 200, response.text
    return response.json()


def process(client, series, plan=None, **body):
    plan = plan or preview(client, series, **{k: body[k] for k in ('steps', 'configs', 'fresh') if k in body})
    body = {'steps': ['discovery'], 'expected_fingerprint': plan['fingerprint'], **body}
    return client.post(f"/api/series/{series['id']}/process", json=body)


def children(client, series):
    return client.get(f"/api/series/{series['id']}/runs").json()['runs'][0]['children']


def pipeline_runs(client, book_id):
    from bardic.pipeline.repository import PipelineRepository
    return PipelineRepository(client.app.state.runtime.store).runs(book_id)


# --- plan -----------------------------------------------------------------------------------------

def test_plan_aggregates_book_plans_in_reading_order_without_side_effects(client):
    series, books = collection(client, positions=(9, 2))
    assert client.put(f"/api/series/{series['id']}/volumes", json={'position': 1, 'title': 'Earlier book', 'status': 'missing'}).status_code == 200
    response = client.post(f"/api/series/{series['id']}/plan", json={'steps': ['discovery']})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert [b['position'] for b in plan['books']] == [2, 9]
    assert [b['book_id'] for b in plan['books']] == [books[1]['id'], books[0]['id']]
    single = [client.post(f"/api/books/{b['book_id']}/analysis-pipeline/plan", json={'steps': ['discovery']}).json()
              for b in plan['books']]
    # Each book's plan is exactly what the book's own Analysis tab would preview.
    assert [b['fingerprint'] for b in plan['books']] == [p['fingerprint'] for p in single]
    assert plan['requests'] == sum(p['requests'] for p in single) >= 2
    assert plan['estimated_cost_usd'] == pytest.approx(sum(p['estimated_cost_usd'] for p in single))
    assert plan['estimated_input_tokens'] == sum(p['estimated_input_tokens'] for p in single)
    assert plan['configs'] == {'discovery': {'provider': 'openai', 'model': MODEL}}
    assert len(plan['fingerprint']) == 64 and plan['missing_inputs'] == {} and plan['missing_credentials'] == []
    assert [v['title'] for v in plan['skipped_volumes']] == ['Earlier book']
    assert client.post(f"/api/series/{series['id']}/plan", json={'steps': ['discovery']}).json()['fingerprint'] == plan['fingerprint']
    assert client.get('/api/jobs').json() == []
    assert client.provider.calls == []
    assert SECRET not in response.text


def test_unknown_price_is_never_counted_as_zero(client):
    series, books = collection(client, positions=(1, 2))
    unpriced = {'discovery': {'provider': 'openai', 'model': 'unpriced-test-model'}}
    # Volume one already has validated units for this model, so it costs nothing new.
    first = client.post(f"/api/books/{books[0]['id']}/analysis-pipeline/plan", json={'steps': ['discovery'], 'configs': unpriced}).json()
    run = client.post(f"/api/books/{books[0]['id']}/analysis-pipeline/runs",
                      json={'steps': ['discovery'], 'configs': unpriced, 'expected_fingerprint': first['fingerprint']})
    assert run.status_code == 200, run.text
    assert wait_job(client, run.json()['job']['id'])['status'] == 'completed'
    plan = preview(client, series, configs=unpriced)
    by_book = {b['book_id']: b['plan'] for b in plan['books']}
    assert by_book[books[0]['id']]['requests'] == 0 and by_book[books[0]['id']]['estimated_cost_usd'] == 0
    assert by_book[books[1]['id']]['requests'] >= 1 and by_book[books[1]['id']]['estimated_cost_usd'] is None
    assert plan['estimated_cost_usd'] is None
    assert plan['known_cost_usd'] == 0 and plan['unknown_cost_books'] == [books[1]['id']]


def test_plan_reports_missing_inputs_and_process_refuses_them(client):
    series, books = collection(client, positions=(1, 2))
    plan = preview(client, series, steps=['profiles'])
    assert set(plan['missing_inputs']) == {b['id'] for b in books}
    response = process(client, series, plan, steps=['profiles'])
    assert response.status_code == 400 and 'Character discovery' in response.text
    assert client.get('/api/jobs').json() == []


# --- consent --------------------------------------------------------------------------------------

def test_process_requires_a_confirmed_fingerprint_or_limits(client):
    series, _ = collection(client, positions=(1,))
    response = client.post(f"/api/series/{series['id']}/process", json={'steps': ['discovery']})
    assert response.status_code == 400 and 'expected_fingerprint' in response.text
    assert client.get('/api/jobs').json() == []
    limited = client.post(f"/api/series/{series['id']}/process", json={'steps': ['discovery'], 'limits': {'max_requests': 5}})
    assert limited.status_code == 200, limited.text
    assert wait_job(client, limited.json()['id'])['status'] == 'completed'


def test_changed_book_membership_or_settings_after_preview_is_refused_with_409(client):
    series, _ = collection(client, positions=(1,))
    plan = preview(client, series)
    newcomer = import_volume(client, 2)
    client.put(f"/api/books/{newcomer['id']}/series", json={'series_id': series['id'], 'position': 2})
    response = process(client, series, plan)
    assert response.status_code == 409 and 'changed' in response.text
    plan = preview(client, series)
    client.put('/api/analysis-pipeline/steps/discovery/settings', json={'provider': 'openai', 'model': 'another-model'})
    assert process(client, series, plan).status_code == 409
    assert client.get('/api/jobs').json() == []
    assert client.provider.calls == []


def test_book_changed_after_queueing_is_not_run_and_later_books_never_start(client, monkeypatch):
    series, books = collection(client)
    runtime = client.app.state.runtime
    queued = []
    monkeypatch.setattr(runtime.series_pool, 'submit', lambda fn, *args: queued.append((fn, args)))
    parent = process(client, series).json()
    # Simulate a change that bypassed the reservation (the store, not the API).
    book = runtime.store.book(books[1]['id'])
    book['revision'] += 1
    runtime.store.save_book(book)
    fn, args = queued[0]
    fn(*args)
    parent = runtime.store.job(parent['id'])
    assert parent['status'] == 'failed' and 'changed after the preview' in parent['message']
    statuses = [c['status'] for c in children(client, series)]
    assert statuses == ['completed', 'failed', 'interrupted']
    assert client.provider.volumes == [1]
    assert pipeline_runs(client, books[1]['id']) == [] and pipeline_runs(client, books[2]['id']) == []


# --- execution ------------------------------------------------------------------------------------

def test_series_runs_each_book_in_reading_order_as_pipeline_children(client):
    series, books = collection(client, positions=(3, 1, 2))
    response = process(client, series)
    assert response.status_code == 200, response.text
    parent = wait_job(client, response.json()['id'])
    assert parent['status'] == 'completed', parent
    assert parent['kind'] == 'series' and parent['progress'] == 3
    assert client.provider.volumes == [1, 2, 3]
    kids = children(client, series)
    assert [c['position'] for c in kids] == [1, 2, 3]
    assert all(c['kind'] == 'pipeline' and c['status'] == 'completed' and c['run_id'] for c in kids)
    assert all(c['run']['status'] == 'completed' and c['run']['outcomes']['discovery']['accepted'] for c in kids)
    assert all(c['message'] == 'Done; results are in use.' for c in kids)
    for book in books:
        runs = pipeline_runs(client, book['id'])
        assert len(runs) == 1 and runs[0]['series_run_id'] == parent['id'] and runs[0]['limits']['max_requests'] is None
        overview = client.get(f"/api/books/{book['id']}/analysis-pipeline").json()
        assert next(s for s in overview['steps'] if s['id'] == 'discovery')['has_accepted']
        assert client.get(f"/api/books/{book['id']}/artifacts?kind=series_run").json()['total'] >= 2
    # Every paid attempt was reserved and recorded against its book's run.
    from bardic.processing import ProcessingStore
    attempts = ProcessingStore(client.app.state.runtime.store).attempts(books[0]['id'])
    assert attempts and {a['run_id'] for a in attempts} == {kids[2]['id']}
    assert SECRET not in str(client.get(f"/api/series/{series['id']}/runs").json())


def test_cancelling_the_parent_cancels_queued_children_which_never_start(client):
    series, books = collection(client)
    client.provider.hold = threading.Event()
    parent = process(client, series).json()
    assert client.provider.entered.wait(3)
    assert client.post(f"/api/jobs/{parent['id']}/cancel").status_code == 200
    kids = {c['book_id']: c for c in children(client, series)}
    assert kids[books[1]['id']]['status'] == 'cancelled' and kids[books[2]['id']]['status'] == 'cancelled'
    assert wait_job(client, parent['id'])['status'] == 'cancelled'
    client.app.state.runtime.series_pool.submit(lambda: None).result(timeout=5)
    kids = children(client, series)
    assert [c['status'] for c in kids] == ['cancelled', 'cancelled', 'cancelled']
    assert kids[1]['run_id'] is None and kids[2]['run_id'] is None
    assert pipeline_runs(client, books[1]['id']) == [] and pipeline_runs(client, books[2]['id']) == []
    assert set(client.provider.volumes) == {1}
    # Nothing is reserved any more.
    assert client.patch(f"/api/books/{books[2]['id']}/metadata", json={'title': 'Editable again', 'author': ''}).status_code == 200


def test_cancelling_the_running_book_stops_the_series_there(client):
    series, books = collection(client)
    client.provider.hold = threading.Event()
    parent = process(client, series).json()
    assert client.provider.entered.wait(3)
    running = children(client, series)[0]
    assert running['status'] == 'running' and running['run_id']
    assert client.post(f"/api/jobs/{running['id']}/cancel").status_code == 200
    assert wait_job(client, parent['id'])['status'] == 'cancelled'
    kids = children(client, series)
    assert [c['status'] for c in kids] == ['cancelled', 'cancelled', 'cancelled']
    assert kids[0]['run']['status'] == 'cancelled' and not kids[0].get('not_started')
    assert kids[1]['not_started'] and kids[2]['not_started']
    assert set(client.provider.volumes) == {1}


def test_cancelling_a_queued_series_releases_books_and_never_resurrects(client):
    first, first_books = collection(client, positions=(1,))
    second = client.post('/api/series', json={'name': 'Other series'}).json()
    other = import_volume(client, 5)
    client.put(f"/api/books/{other['id']}/series", json={'series_id': second['id'], 'position': 1})
    client.provider.hold = threading.Event()
    running = process(client, first).json()
    try:
        assert client.provider.entered.wait(3)
        waiting = process(client, second).json()
        assert waiting['status'] == 'queued'
        assert client.post(f"/api/jobs/{waiting['id']}/cancel").json()['status'] == 'cancelled'
        assert [c['status'] for c in children(client, second)] == ['cancelled']
        assert client.patch(f"/api/books/{other['id']}/metadata", json={'title': 'No longer reserved', 'author': ''}).status_code == 200
    finally:
        client.provider.hold.set()
    assert wait_job(client, running['id'])['status'] == 'completed'
    client.app.state.runtime.series_pool.submit(lambda: None).result(timeout=5)
    assert client.app.state.runtime.store.job(waiting['id'])['status'] == 'cancelled'
    assert children(client, second)[0]['run_id'] is None
    assert client.provider.volumes == [1]


def test_reservations_block_edits_single_book_runs_and_a_second_series_run(client):
    series, books = collection(client, positions=(1, 2))
    client.provider.hold = threading.Event()
    parent = process(client, series).json()
    try:
        assert client.provider.entered.wait(3)
        # The waiting second book is reserved, not only the running one.
        assert client.patch(f"/api/books/{books[1]['id']}/metadata", json={'title': 'Blocked', 'author': ''}).status_code == 409
        single = client.post(f"/api/books/{books[1]['id']}/analysis-pipeline/runs", json={'steps': ['structure'], 'limits': {'max_requests': 1}})
        assert single.status_code == 409
        assert client.put(f"/api/books/{books[1]['id']}/series", json={'series_id': None}).status_code == 409
        again = client.post(f"/api/series/{series['id']}/process", json={'steps': ['structure'], 'limits': {'max_requests': 1}})
        assert again.status_code == 409
    finally:
        client.provider.hold.set()
    assert wait_job(client, parent['id'])['status'] == 'completed'
    assert client.patch(f"/api/books/{books[1]['id']}/metadata", json={'title': 'Now editable', 'author': ''}).status_code == 200


def test_a_book_with_its_own_active_job_blocks_the_series(client):
    series, books = collection(client, positions=(1, 2))
    runtime = client.app.state.runtime
    job = runtime.store.create_job(books[1]['id'], 'pipeline')
    try:
        assert process(client, series).status_code == 409
        assert [j['kind'] for j in runtime.store.jobs(limit=None)] == ['pipeline']
    finally:
        runtime.store.update_job(job['id'], status='cancelled')


def test_a_stopped_book_stops_the_series_and_later_books_never_start(client):
    series, books = collection(client)
    client.provider.fail = lambda stage, count: count == 2   # the second book's discovery request fails
    parent = wait_job(client, process(client, series).json()['id'])
    assert parent['status'] == 'failed'
    kids = children(client, series)
    assert [c['status'] for c in kids] == ['completed', 'failed', 'interrupted']
    assert kids[2]['not_started'] is True and kids[2]['run_id'] is None
    assert client.provider.volumes == [1, 2]
    assert SECRET not in str(kids)


def test_optional_limits_cap_each_book_and_stop_the_series(client):
    series, books = collection(client, positions=(1, 2))
    # A dollar guard below one request's reserve stops the first book before anything is sent.
    response = process(client, series, limits={'budget_usd': 0.000001})
    parent = wait_job(client, response.json()['id'])
    first = pipeline_runs(client, books[0]['id'])[0]
    assert first['limits'] == {'max_requests': None, 'max_input_tokens': None, 'max_output_tokens': None, 'budget_usd': 0.000001}
    assert parent['status'] == 'budget_limited'
    kids = children(client, series)
    assert [c['status'] for c in kids] == ['budget_limited', 'interrupted'] and kids[1]['not_started']
    assert client.provider.calls == []


def test_submission_failure_releases_books_and_redacts(client, monkeypatch):
    series, books = collection(client, positions=(1, 2))
    runtime = client.app.state.runtime
    def rejected(*args, **kwargs):
        raise RuntimeError(f'Executor shutdown {SECRET}')
    with monkeypatch.context() as scoped:
        scoped.setattr(runtime.series_pool, 'submit', rejected)
        response = process(client, series)
    assert response.status_code == 400 and 'could not start' in response.text
    jobs = runtime.store.jobs(limit=None)
    assert all(job['status'] not in {'queued', 'running'} for job in jobs)
    assert SECRET not in str(jobs)
    for book in books:
        assert client.patch(f"/api/books/{book['id']}/metadata", json={'title': 'Still editable', 'author': ''}).status_code == 200
    assert client.provider.calls == []


def test_archived_books_and_placeholders_are_not_scheduled(client):
    series, books = collection(client, positions=(1, 2))
    assert client.post(f"/api/books/{books[0]['id']}/archive").status_code == 200
    plan = preview(client, series)
    assert [b['book_id'] for b in plan['books']] == [books[1]['id']]
    assert wait_job(client, process(client, series, plan).json()['id'])['status'] == 'completed'
    assert client.provider.volumes == [2]
    assert client.post(f"/api/series/{series['id']}/archive").status_code == 200
    assert client.post(f"/api/series/{series['id']}/plan", json={'steps': ['discovery']}).status_code == 404


def test_local_steps_run_across_the_series_without_keys(client):
    client.app.state.runtime.api_keys['openai'] = ''
    series, _ = collection(client, positions=(1, 2))
    plan = preview(client, series, steps=['census'])
    assert plan['requests'] == 0 and plan['estimated_cost_usd'] == 0 and plan['missing_credentials'] == []
    assert wait_job(client, process(client, series, plan, steps=['census']).json()['id'])['status'] == 'completed'


def test_missing_key_is_reported_by_plan_and_refused_by_process(client):
    client.app.state.runtime.api_keys['openai'] = ''
    series, _ = collection(client, positions=(1,))
    plan = preview(client, series)
    assert plan['missing_credentials'] == [{'provider': 'openai', 'label': 'OpenAI', 'needs': 'api_key'}]
    response = process(client, series, plan)
    assert response.status_code == 400 and 'API key' in response.text
    assert client.get('/api/jobs').json() == []


@pytest.mark.parametrize('body', [{'steps': ['unknown']}, {'steps': []}, {'steps': ['discovery'], 'concurrency': 5},
                                  {'steps': ['discovery'], 'configs': {'discovery': {'provider': 'local'}}},
                                  {'steps': ['discovery'], 'phase': 'scan'},
                                  {'steps': ['discovery'], 'limits': {'max_requests': 0}}])
def test_invalid_series_requests_never_create_jobs(client, body):
    series, _ = collection(client, positions=(1,))
    assert client.post(f"/api/series/{series['id']}/process", json=body).status_code in {400, 422}
    assert client.get('/api/jobs').json() == []


def test_empty_and_unknown_series_and_invalid_plans_are_refused_without_jobs(client):
    empty = client.post('/api/series', json={'name': 'Nothing yet'}).json()
    plan = preview(client, empty)
    assert plan['books'] == [] and plan['requests'] == 0 and plan['estimated_cost_usd'] == 0
    assert plan['unknown_cost_books'] == [] and len(plan['fingerprint']) == 64
    response = process(client, empty, plan)
    assert response.status_code == 400 and 'Add a book' in response.text
    for method, route, body in (('post', 'plan', {'steps': ['discovery']}),
                                ('post', 'process', {'steps': ['discovery'], 'limits': {'max_requests': 1}}),
                                ('get', 'runs', None)):
        kwargs = {'json': body} if body is not None else {}
        missing = getattr(client, method)(f'/api/series/series_missing/{route}', **kwargs)
        assert missing.status_code == 404 and missing.json()['detail'] == 'Series not found'
    series, _ = collection(client, positions=(1,))
    for body in ({'steps': ['unknown']}, {'steps': ['discovery'], 'configs': {'discovery': {'provider': 'local'}}}):
        assert client.post(f"/api/series/{series['id']}/plan", json=body).status_code == 400
    assert client.get(f"/api/series/{series['id']}/runs").json() == {'runs': []}
    assert client.get('/api/jobs').json() == []


def test_new_accepted_evidence_in_an_earlier_book_does_not_stop_a_linked_profiles_run(client):
    """Book 1 accepts new profile evidence during the run, and book 2 reads it (series memory).

    Book 2's profiles are context-pending: its prompts change, but its consent fingerprint covers
    only the unit set, providers, models and versions, so the series completes. The projection
    still writes no observations (history is in artifacts).
    """
    from bardic.series import SeriesRepository
    from test_analysis_pipeline import run

    series, books = collection(client, positions=(1, 2))
    for book in books:
        assert run(client, book['id'], ['discovery', 'profiles'])[0]['status'] == 'completed'
    store = client.app.state.runtime.store
    repository = SeriesRepository(store)
    identity = repository.create_character(series['id'], 'Mara')
    for book in books:
        mara = next(c for c in store.book(book['id'])['characters'] if c['name'] == 'Mara')
        repository.link_character(book['id'], mara['id'], identity['id'])
    provider = client.provider
    original = provider.__class__.__call__

    def reread(self, *args):
        result = original(self, *args)
        if self.calls[-1] == 'profiles':
            for item in result['characters']:
                item['description'] = 'A second reading.'
        return result
    provider.__class__.__call__ = reread
    try:
        plan = preview(client, series, steps=['profiles'], fresh=True)
        assert plan['context_pending_books'] == [books[1]['id']]
        parent = wait_job(client, process(client, series, plan, steps=['profiles'], fresh=True).json()['id'])
        assert parent['status'] == 'completed', parent
        assert [child['status'] for child in children(client, series)] == ['completed', 'completed']
    finally:
        provider.__class__.__call__ = original
    # Book 1 did accept new evidence: its Cast references carry the new profile reading.
    first_mara = next(c for c in store.book(books[0]['id'])['characters'] if c['name'] == 'Mara')
    profiled = [r for r in store.character_references(books[0]['id'], first_mara['id']) if r['step'] == 'profiles']
    assert profiled and {r['profile_description'] for r in profiled} == {'A second reading.'}
    # Book 2's context now carries it.
    later = repository.context_for_book(books[1]['id'])
    assert 'A second reading.' in {o['description'] for c in later['characters'] for o in c['observations']}
    assert repository.observations(books[0]['id']) == []
