"""Series run lifecycle on the step pipeline: reservations, coordinator start and preview identity."""
from concurrent.futures import Future
import threading

from test_series_processing import (children, client, collection, import_volume, pipeline_runs, preview, process,  # noqa: F401
                                    wait_job)


def test_completed_child_remains_reserved_until_parent_finishes(client):
    series, books = collection(client, positions=(1, 2))
    client.provider.hold, client.provider.hold_volume = threading.Event(), 2
    parent = process(client, series).json()
    try:
        assert client.provider.entered.wait(3)
        assert children(client, series)[0]['status'] == 'completed'
        assert client.patch(f"/api/books/{books[0]['id']}/metadata", json={'title': 'Cannot change yet', 'author': ''}).status_code == 409
        assert client.post(f"/api/books/{books[0]['id']}/archive").status_code == 409
        assert client.put(f"/api/books/{books[0]['id']}/series", json={'series_id': None}).status_code == 409
        accepted = client.post(f"/api/books/{books[0]['id']}/analysis-pipeline/steps/discovery/versions/accepted/accept", json={})
        assert accepted.status_code == 409
    finally:
        client.provider.hold.set()
    assert wait_job(client, parent['id'])['status'] == 'completed'
    assert client.patch(f"/api/books/{books[0]['id']}/metadata", json={'title': 'Now editable', 'author': ''}).status_code == 200


def test_cancelled_before_coordinator_start_settles_children_without_requests(client, monkeypatch):
    series, books = collection(client, positions=(1, 2))
    runtime = client.app.state.runtime
    queued = []
    monkeypatch.setattr(runtime.series_pool, 'submit', lambda fn, *args: queued.append((fn, args)) or Future())
    parent = process(client, series).json()
    runtime.store.update_job(parent['id'], cancel_requested=True)
    fn, args = queued[0]
    fn(*args)
    assert runtime.store.job(parent['id'])['status'] == 'cancelled'
    assert [c['status'] for c in children(client, series)] == ['cancelled', 'cancelled']
    assert all(c['not_started'] for c in children(client, series))
    assert client.provider.calls == [] and all(pipeline_runs(client, b['id']) == [] for b in books)


def test_series_fingerprint_tracks_order_metadata_models_source_and_fresh(client):
    series, books = collection(client, positions=(1,))
    runtime = client.app.state.runtime

    def fingerprint(**body):
        return preview(client, series, **body)['fingerprint']
    previous = fingerprint()
    assert fingerprint() == previous
    assert fingerprint(fresh=True) != previous
    client.patch(f"/api/books/{books[0]['id']}/metadata", json={'title': 'Renamed book', 'author': ''})
    assert fingerprint() != previous
    previous = fingerprint()
    client.put(f"/api/books/{books[0]['id']}/series", json={'series_id': series['id'], 'position': 9})
    assert fingerprint() != previous
    previous = fingerprint()
    assert fingerprint(configs={'discovery': {'provider': 'openai', 'model': 'other-model'}}) != previous
    changed = runtime.store.book(books[0]['id'])
    changed['chapters'][0]['text'] += ' Additional source.'
    runtime.store.save_book(changed)
    assert fingerprint() != previous
    other = import_volume(client, 2)
    previous = fingerprint()
    client.put(f"/api/books/{other['id']}/series", json={'series_id': series['id'], 'position': 2})
    assert fingerprint() != previous
