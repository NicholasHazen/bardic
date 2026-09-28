"""Series reservations, simultaneous failure boundaries and preview identity."""
from concurrent.futures import Future, wait as wait_all
from copy import deepcopy
import threading

from bardic.processing import BudgetReached
from bardic import series_processing
from test_app import import_text, wait_job
from test_series_processing import client, collection


def test_completed_child_remains_reserved_until_parent_finishes(client, monkeypatch):
    series, books = collection(client, 2)
    second_started, release = threading.Event(), threading.Event()
    def fake(book, *args, **kwargs):
        if book['id'] == books[1]['id']:
            second_started.set()
            assert release.wait(3)
        return deepcopy(book)
    monkeypatch.setattr('bardic.analysis.analyze_book', fake)
    parent = client.post(f"/api/series/{series['id']}/process", json={'provider': 'openai', 'phase': 'scan', 'concurrency': 1}).json()
    try:
        assert second_started.wait(2)
        store = client.app.state.runtime.store
        assert store.jobs(books[0]['id'])[0]['status'] == 'completed'
        assert client.patch(f"/api/books/{books[0]['id']}/metadata", json={'title': 'Cannot change yet'}).status_code == 409
        assert client.post(f"/api/books/{books[0]['id']}/archive").status_code == 409
        assert client.put(f"/api/books/{books[0]['id']}/series", json={'series_id': None}).status_code == 409
    finally:
        release.set()
    assert wait_job(client, parent['id'])['status'] == 'completed'
    assert client.patch(f"/api/books/{books[0]['id']}/metadata", json={'title': 'Now editable'}).status_code == 200


def test_cancelling_queued_parent_immediately_releases_children_and_never_resurrects(client, monkeypatch):
    first, first_books = collection(client, 1)
    second = client.post('/api/series', json={'name': 'Other series'}).json()
    second_book = import_text(client, 'Chapter 1\n\nAnother book.')
    client.put(f"/api/books/{second_book['id']}/series", json={'series_id': second['id'], 'position': 1})
    started, release = threading.Event(), threading.Event()
    calls = []
    def fake(book, *args, **kwargs):
        calls.append(book['id'])
        started.set()
        assert release.wait(3)
        return deepcopy(book)
    monkeypatch.setattr('bardic.analysis.analyze_book', fake)
    first_parent = client.post(f"/api/series/{first['id']}/process", json={'provider': 'openai'}).json()
    try:
        assert started.wait(2)
        second_parent = client.post(f"/api/series/{second['id']}/process", json={'provider': 'openai'}).json()
        assert second_parent['status'] == 'queued'
        assert client.post(f"/api/jobs/{second_parent['id']}/cancel").json()['status'] == 'cancelled'
        children = client.get(f"/api/series/{second['id']}/runs").json()['runs'][0]['children']
        assert all(child['status'] == 'cancelled' for child in children)
        assert client.patch(f"/api/books/{second_book['id']}/metadata", json={'title': 'No longer reserved'}).status_code == 200
    finally:
        release.set()
    assert wait_job(client, first_parent['id'])['status'] == 'completed'
    client.app.state.runtime.series_pool.submit(lambda: None).result(timeout=3)
    assert client.app.state.runtime.store.job(second_parent['id'])['status'] == 'cancelled'
    assert calls == [first_books[0]['id']]


def test_all_simultaneously_finished_results_are_checked_before_more_paid_work(client, monkeypatch):
    series, books = collection(client, 3)
    calls = []
    def fake(book, *args, **kwargs):
        calls.append(book['id'])
        if book['id'] == books[1]['id']:
            raise BudgetReached('Stop this batch')
        return deepcopy(book)
    def finished_together(futures, **kwargs):
        done, pending = wait_all(futures)
        # Force the successful item first. The old per-result scheduling loop
        # would launch book three before noticing the failure in the same batch.
        return sorted(done, key=lambda f: not f.result()), pending
    monkeypatch.setattr('bardic.analysis.analyze_book', fake)
    monkeypatch.setattr(series_processing, 'wait', finished_together)
    parent = client.post(f"/api/series/{series['id']}/process", json={'provider': 'openai', 'concurrency': 2}).json()
    assert wait_job(client, parent['id'])['status'] == 'failed'
    assert set(calls) == {books[0]['id'], books[1]['id']}
    assert client.app.state.runtime.store.jobs(books[2]['id'])[0]['status'] == 'interrupted'


def test_executor_submission_failure_releases_books_redacts_error_and_preserves_run_history(client, monkeypatch):
    series, books = collection(client, 2)
    runtime = client.app.state.runtime
    def rejected(*args, **kwargs):
        raise RuntimeError('Executor shutdown private-test-key')
    with monkeypatch.context() as scoped:
        scoped.setattr(runtime.series_pool, 'submit', rejected)
        response = client.post(f"/api/series/{series['id']}/process", json={'provider': 'openai'})
    assert response.status_code == 503 and response.json()['code'] == 'shutting_down'
    jobs = runtime.store.jobs(limit=None)
    assert all(job['status'] not in {'queued', 'running'} for job in jobs)
    assert 'private-test-key' not in str(jobs)
    for book in books:
        assert client.patch(f"/api/books/{book['id']}/metadata", json={'title': 'Still editable'}).status_code == 200
        assert client.get(f"/api/books/{book['id']}/artifacts?kind=series_run").json()['total'] >= 2
    monkeypatch.setattr('bardic.analysis.analyze_book', lambda book, *args, **kwargs: deepcopy(book))
    retried = client.post(f"/api/series/{series['id']}/process", json={'provider': 'openai'}).json()
    assert wait_job(client, retried['id'])['status'] == 'completed'


def test_cancelled_before_coordinator_start_cleans_children_without_api_side_effects(client, monkeypatch):
    series, books = collection(client, 2)
    runtime = client.app.state.runtime
    queued = []
    monkeypatch.setattr(runtime.series_pool, 'submit', lambda fn: queued.append(fn) or Future())
    parent = client.post(f"/api/series/{series['id']}/process", json={'provider': 'openai'}).json()
    runtime.store.update_job(parent['id'], cancel_requested=True)
    queued[0]()
    assert runtime.store.job(parent['id'])['status'] == 'cancelled'
    assert all(runtime.store.jobs(book['id'])[0]['status'] == 'cancelled' for book in books)


def test_preview_fingerprint_is_stable_and_rejects_changed_scope_before_jobs(client, monkeypatch):
    series, books = collection(client, 1)
    url = f"/api/series/{series['id']}"
    body = {'provider': 'openai', 'phase': 'scan', 'concurrency': 2}
    first = client.post(url + '/plan', json=body).json()
    assert len(first['plan_fingerprint']) == 64
    assert client.post(url + '/plan', json=body).json()['plan_fingerprint'] == first['plan_fingerprint']
    other = import_text(client, 'Chapter 1\n\nA new member.')
    client.put(f"/api/books/{other['id']}/series", json={'series_id': series['id'], 'position': 2})
    response = client.post(url + '/process', json={**body, 'expected_plan_fingerprint': first['plan_fingerprint']})
    assert response.status_code == 409 and response.json()['code'] == 'plan_stale'
    assert client.get('/api/jobs').json() == []
    current = client.post(url + '/plan', json=body).json()
    assert current['plan_fingerprint'] != first['plan_fingerprint']
    monkeypatch.setattr('bardic.analysis.analyze_book', lambda book, *args, **kwargs: deepcopy(book))
    accepted = client.post(url + '/process', json={**body, 'expected_plan_fingerprint': current['plan_fingerprint']})
    assert accepted.status_code == 200, accepted.text
    assert wait_job(client, accepted.json()['id'])['status'] == 'completed'


def test_preview_fingerprint_tracks_order_metadata_models_limits_and_source(client):
    series, books = collection(client, 1)
    runtime = client.app.state.runtime
    args = {'provider': 'openai', 'limits': {'max_requests': 5}}
    def fingerprint():
        return series_processing.plan(runtime, series['id'], **args)['plan_fingerprint']
    previous = fingerprint()
    client.patch(f"/api/books/{books[0]['id']}/metadata", json={'title': 'Renamed book'})
    assert fingerprint() != previous
    previous = fingerprint()
    client.put(f"/api/books/{books[0]['id']}/series", json={'series_id': series['id'], 'position': 9})
    assert fingerprint() != previous
    previous = fingerprint()
    runtime.preferences['preprocess_models_by_provider']['openai'] = 'new-model'
    assert fingerprint() != previous
    previous = fingerprint()
    args['limits'] = {'max_requests': 6}
    assert fingerprint() != previous
    previous = fingerprint()
    changed = runtime.store.book(books[0]['id'])
    changed['chapters'][0]['text'] += ' Additional source.'
    runtime.store.save_book(changed)
    assert fingerprint() != previous
