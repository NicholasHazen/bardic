"""Series API behavior fixed for contract 0.2.0 (issue #17), on the step-pipeline series runs of 0.3.0.

Offline: synthetic prose, a fake key and no provider calls. Each test pins one
formerly defective or inconsistent behavior and the error code clients branch on.
"""
from classic_fixtures import classic_references
from test_app import import_text, wait_job
from test_series_processing import client, collection, preview  # noqa: F401  (client is a fixture)

# A run needs a confirmed fingerprint or a limit; a limit keeps these error-path tests independent of the preview.
RUN = {'steps': ['discovery'], 'limits': {'max_requests': 5}}


def active_series_run(client, series_id):
    """An active series parent job, as a running coordinator would leave it."""
    return client.app.state.runtime.store.create_job('series:' + series_id, 'series')


def test_archived_series_refuses_edits_with_series_archived_and_still_answers_reads(client):  # noqa: F811
    series, books = collection(client, (1,))
    other = import_text(client, 'Chapter One\n\nA later lamp was lit.')
    url = f"/api/series/{series['id']}"
    assert client.post(url + '/characters', json={'name': 'Mara'}).status_code == 200
    assert client.post(url + '/archive').status_code == 200

    refused = [client.patch(url, json={'name': 'Renamed'}),
               client.put(url + '/volumes', json={'position': 4, 'title': 'Later'}),
               client.delete(url + '/volumes/4'),
               client.post(url + '/characters', json={'name': 'Elio'}),
               client.post(url + '/plan', json={'steps': ['discovery']}),
               client.post(url + '/process', json=RUN),
               client.put(f"/api/books/{other['id']}/series", json={'series_id': series['id'], 'position': 2})]
    for response in refused:
        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'series_archived'
    assert client.get('/api/jobs').json() == []

    runs = client.get(url + '/runs')
    assert runs.status_code == 200 and runs.json() == {'runs': []}
    series_map = client.get(url + '/map')
    assert series_map.status_code == 200 and series_map.json()['series']['archived'] is True
    assert client.get(url + '/characters').status_code == 200


def test_linking_in_an_archived_series_is_series_archived(client):  # noqa: F811
    series, books = collection(client, (1,))
    store = client.app.state.runtime.store
    book = store.book(books[0]['id'])
    book['characters'].append({'id': 'mara', 'name': 'Mara'})
    store.save_book(book)
    identity = client.post(f"/api/series/{series['id']}/characters", json={'name': 'Mara'}).json()
    assert client.post(f"/api/series/{series['id']}/archive").status_code == 200
    response = client.put(f"/api/books/{books[0]['id']}/series/characters/mara",
                          json={'series_character_id': identity['id']})
    assert response.status_code == 409 and response.json()['code'] == 'series_archived'


def test_archive_and_restore_are_idempotent_and_record_nothing_when_unchanged(client):  # noqa: F811
    series, books = collection(client, (1,))
    url = f"/api/series/{series['id']}"

    def visibility_records():
        return client.get(f"/api/books/{books[0]['id']}/artifacts?kind=library_state").json()['total']

    first = client.post(url + '/archive')
    assert first.status_code == 200 and first.json()['archived'] is True
    recorded = visibility_records()
    again = client.post(url + '/archive')
    assert again.status_code == 200, again.text
    assert again.json() == {'id': series['id'], 'archived': True, 'retained': True}
    assert visibility_records() == recorded

    assert client.post(url + '/restore').json()['archived'] is False
    recorded = visibility_records()
    again = client.post(url + '/restore')
    assert again.status_code == 200 and again.json()['archived'] is False
    assert visibility_records() == recorded


def test_series_character_creation_is_refused_during_an_active_series_run(client):  # noqa: F811
    series, _ = collection(client, (1,))
    active_series_run(client, series['id'])
    response = client.post(f"/api/series/{series['id']}/characters", json={'name': 'Mara'})
    assert response.status_code == 409 and response.json()['code'] == 'series_run_active'


def test_restoring_a_series_is_refused_during_an_active_series_run(client):  # noqa: F811
    series, _ = collection(client, (1,))
    assert client.post(f"/api/series/{series['id']}/archive").status_code == 200
    active_series_run(client, series['id'])
    response = client.post(f"/api/series/{series['id']}/restore")
    assert response.status_code == 409 and response.json()['code'] == 'series_run_active'


def test_stale_series_fingerprint_and_an_active_run_are_409_conflicts(client):  # noqa: F811
    series, _ = collection(client, (1,))
    url = f"/api/series/{series['id']}"
    stale = client.post(url + '/process', json={'steps': ['discovery'], 'expected_fingerprint': 'f' * 64})
    assert stale.status_code == 409 and stale.json()['code'] == 'plan_stale'
    active_series_run(client, series['id'])
    running = client.post(url + '/process', json=RUN)
    assert running.status_code == 409 and running.json()['code'] == 'series_run_active'


def test_series_processing_errors_have_specific_codes_and_no_ui_locations(client, monkeypatch):  # noqa: F811
    empty = client.post('/api/series', json={'name': 'Empty shelf'}).json()
    response = client.post(f"/api/series/{empty['id']}/process", json=RUN)
    assert response.status_code == 400 and response.json()['code'] == 'series_empty'
    series, _ = collection(client, (1,))
    response = client.post(f"/api/series/{series['id']}/process", json={'steps': ['discovery']})
    assert response.status_code == 400 and response.json()['code'] == 'run_unconfirmed'
    client.app.state.runtime.api_keys['openai'] = ''
    response = client.post(f"/api/series/{series['id']}/process", json=RUN)
    assert response.status_code == 400 and response.json()['code'] == 'api_key_missing'
    assert 'Settings' not in response.json()['detail']
    # Series runs take pipeline steps; a step that does not take the provider is refused like the book pipeline.
    response = client.post(f"/api/series/{series['id']}/plan",
                           json={'steps': ['discovery'], 'configs': {'discovery': {'provider': 'local'}}})
    assert response.status_code == 400 and response.json()['code'] == 'step_config_invalid'
    assert client.get('/api/jobs').json() == []


def test_series_worker_that_cannot_start_is_503_shutting_down(client, monkeypatch):  # noqa: F811
    series, _ = collection(client, (1,))
    runtime = client.app.state.runtime

    def rejected(*args, **kwargs):
        raise RuntimeError('cannot schedule new futures after shutdown')

    monkeypatch.setattr(runtime.series_pool, 'submit', rejected)
    response = client.post(f"/api/series/{series['id']}/process", json=RUN)
    assert response.status_code == 503 and response.json()['code'] == 'shutting_down'
    assert all(job['status'] not in {'queued', 'running'} for job in runtime.store.jobs(limit=None))


def test_unknown_ids_in_request_bodies_are_400(client):  # noqa: F811
    series, books = collection(client, (1,))
    response = client.put(f"/api/books/{books[0]['id']}/series", json={'series_id': 'series_missing', 'position': 2})
    assert response.status_code == 400 and response.json()['code'] == 'unknown_series'
    store = client.app.state.runtime.store
    book = store.book(books[0]['id'])
    book['characters'].append({'id': 'mara', 'name': 'Mara'})
    store.save_book(book)
    response = client.put(f"/api/books/{books[0]['id']}/series/characters/mara",
                          json={'series_character_id': 'series_character_missing'})
    assert response.status_code == 400 and response.json()['code'] == 'unknown_series_character'
    assert client.get('/api/series/missing/map').json()['code'] == 'series_not_found'


def test_series_context_does_not_expose_internal_source_hashes(client, monkeypatch):  # noqa: F811
    series, books = collection(client, (1, 2))
    store = client.app.state.runtime.store
    for book_id in (books[0]['id'], books[1]['id']):
        book = store.book(book_id)
        book['characters'].append({'id': 'mara', 'name': 'Mara'})
        store.save_book(book)
    identity = client.post(f"/api/series/{series['id']}/characters", json={'name': 'Mara'}).json()
    for book in books:
        assert client.put(f"/api/books/{book['id']}/series/characters/mara",
                          json={'series_character_id': identity['id']}).status_code == 200
    first = store.book(books[0]['id'])
    chapter = first['chapters'][0]
    start = chapter['text'].index('Mara')
    segment = next(s for s in first['segments'] if s['chapter_id'] == chapter['id'] and s['start'] <= start < s['end'])
    reference = {'id': 'reference-1', 'character_id': 'mara', 'chapter_id': chapter['id'], 'segment_id': segment['id'],
                 'start': start, 'end': start + 4, 'quote': 'Mara', 'kind': 'profile_evidence',
                 'profile_description': 'Keeps the lamp', 'profile_direction': 'Quiet',
                 'provider': 'test', 'model': 'test-model', 'confidence': .9}
    classic_references(store, books[0]['id'], [reference])
    context = client.get(f"/api/books/{books[1]['id']}/series/context").json()
    [entry] = context['characters']
    assert entry['observations'] and all('source_hash' not in item for item in entry['observations'])


def test_completed_series_run_still_starts_after_the_fixes(client):  # noqa: F811
    series, _ = collection(client, (1,))
    url = f"/api/series/{series['id']}"
    plan = preview(client, series)
    started = client.post(url + '/process', json={'steps': ['discovery'], 'expected_fingerprint': plan['fingerprint']})
    assert started.status_code == 200, started.text
    assert wait_job(client, started.json()['id'])['status'] == 'completed'


def test_series_runs_list_existing_children_when_a_child_id_dangles(client):  # noqa: F811
    series, books = collection(client, (1,))
    store = client.app.state.runtime.store
    child = store.create_job(books[0]['id'], 'analyze')
    parent = active_series_run(client, series['id'])
    store.update_job(parent['id'], status='completed', book_ids=[books[0]['id']], child_job_ids=['0' * 32, child['id']])
    runs = client.get(f"/api/series/{series['id']}/runs")
    assert runs.status_code == 200, runs.text
    assert [c['id'] for c in runs.json()['runs'][0]['children']] == [child['id']]


def test_a_restart_ends_a_paused_series_run_and_clears_its_pause(tmp_path):
    """Startup recovery interrupts a paused parent and clears `waiting_for_review`; resume is then not waiting."""
    from bardic.store import Store
    store = Store(tmp_path)
    parent = store.create_job('series:series_paused', 'series')
    store.update_job(parent['id'], status='running', series_id='series_paused', child_job_ids=['0' * 32],
                     waiting_for_review={'book_id': 'b', 'child_job_id': 'c', 'title': '', 'position': 1.0,
                                         'steps': ['profiles'], 'since': '2026-09-28T00:00:00+00:00'})
    restarted = Store(tmp_path).job(parent['id'])
    assert restarted['status'] == 'interrupted' and restarted['waiting_for_review'] is None
    assert 'restarted' in restarted['message'] and 'Settings' not in restarted['message']


def test_cancelling_a_paused_run_skips_a_dangling_child(client):  # noqa: F811
    from bardic.series_processing import cancel_paused
    series, books = collection(client, (1,))
    store = client.app.state.runtime.store
    child = store.create_job(books[0]['id'], 'pipeline')
    store.update_job(child['id'], status='completed')
    parent = active_series_run(client, series['id'])
    parent = store.update_job(parent['id'], series_id=series['id'], book_ids=[books[0]['id']],
                              child_job_ids=['0' * 32, child['id']])
    with store.lock:
        settled = cancel_paused(client.app.state.runtime, parent)
    assert settled['status'] == 'cancelled'
