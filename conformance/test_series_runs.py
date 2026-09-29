"""A series run over free local steps: parent and child jobs, reading order, and refusals that queue nothing."""
from __future__ import annotations

from . import helpers
from .helpers import book_in_series, new_series


def test_series_run_runs_each_book_in_reading_order_without_a_provider(api, local_steps):
    series = new_series(api)
    later = book_in_series(api, series, 2, 'later')
    earlier = book_in_series(api, series, 1, 'earlier')
    path = {'series_id': series['id']}
    plan = api.call('planSeriesProcessing', path=path, json={'steps': local_steps}).json
    assert [entry['book_id'] for entry in plan['books']] == [earlier['id'], later['id']], 'plans follow reading order'
    assert plan['requests'] == 0 and plan['missing_credentials'] == [] and plan['missing_inputs'] == {}
    body = {'steps': local_steps, 'expected_fingerprint': plan['fingerprint']}
    parent = api.call('startSeriesProcessing', path=path, json=body).json
    assert parent['kind'] == 'series' and parent['book_id'] == f'series:{series["id"]}' and parent['total'] == 2
    assert parent['book_ids'] == [earlier['id'], later['id']] and len(parent['child_job_ids']) == 2
    assert parent['status'] in ('queued', 'running'), 'a queued job is not a result'
    done = api.wait_for_job(parent['id'], book_id=parent['book_id'])
    assert done['status'] == 'completed', done
    runs = api.call('listSeriesRuns', path=path).json['runs']
    run = next(item for item in runs if item['id'] == parent['id'])
    assert [child['id'] for child in run['children']] == parent['child_job_ids'], 'children in reading order'
    assert [child['book_id'] for child in run['children']] == [earlier['id'], later['id']]
    for child in run['children']:
        assert child['kind'] == 'pipeline' and child['status'] == 'completed' and child['series_run_id'] == parent['id']
        assert child['run']['status'] == 'completed'
    for book in (earlier, later):
        overview = api.call('getBookAnalysisPipeline', path={'book_id': book['id']}).json
        assert all(next(s for s in overview['steps'] if s['id'] == step)['has_accepted'] for step in local_steps)
        assert helpers.canonical_text(api.call('getBook', path={'book_id': book['id']}).json) == helpers.canonical_text(book)
    children = api.call('listJobs', query={'book_id': earlier['id']}).json
    assert any(job['id'] == run['children'][0]['id'] for job in children)


def test_series_run_refusals(api, local_steps):
    empty = new_series(api)
    assert api.call('startSeriesProcessing', path={'series_id': empty['id']}, json={'steps': local_steps, 'limits': {'max_requests': 5}},
                    expect=400).code == 'series_empty'
    series = new_series(api)
    book_in_series(api, series, 1)
    path = {'series_id': series['id']}
    assert api.call('startSeriesProcessing', path=path, json={'steps': local_steps}, expect=400).code == 'run_unconfirmed'
    assert api.call('startSeriesProcessing', path=path, json={'steps': local_steps, 'expected_fingerprint': 'stale'},
                    expect=409).code == 'plan_stale'
    assert api.call('startSeriesProcessing', path=path, json={'steps': ['no-such-step'], 'limits': {'max_requests': 5}},
                    expect=400).code == 'unknown_step'
    assert api.call('listSeriesRuns', path=path).json == {'runs': []}, 'a refused request queues nothing'
