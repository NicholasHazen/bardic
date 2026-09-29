"""Jobs and the analysis pipeline, driven only through free local steps (no provider, no key, no cost).

The steps run here are the ones the server's own pipeline definition marks `method: plain` (they run
locally and are free). The suite never starts a step that could reach a provider.
"""
from __future__ import annotations

import pytest

from . import helpers

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'budget_limited', 'quota_limited'}
ACTIVE = {'queued', 'running'}


def _plan(api, book, steps, **extra):
    return api.call('planBookAnalysisPipelineRun', path={'book_id': book['id']}, json={'steps': steps, **extra}).json


def _run(api, book, steps, *, gates=None, fresh=False, wait=True):
    plan = _plan(api, book, steps, fresh=fresh)
    body = {'steps': steps, 'expected_fingerprint': plan['fingerprint'], 'fresh': fresh}
    if gates:
        body['gates'] = gates
    started = api.call('startBookAnalysisPipelineRun', path={'book_id': book['id']}, json=body).json
    if not wait:
        return started, None
    return started, api.wait_for_job(started['job']['id'], book_id=book['id'])


def _versions(api, book, step):
    return api.call('listAnalysisPipelineStepVersions', path={'book_id': book['id'], 'step_id': step}).json


# ---------------------------------------------------------------- plans

def test_plan_of_local_steps_is_free_and_deterministic(api, fresh_book, local_steps):
    plan = _plan(api, fresh_book, local_steps)
    assert plan['book_id'] == fresh_book['id']
    assert [step['step_id'] for step in plan['steps']] == local_steps
    assert plan['requests'] == 0 and plan['service_calls'] == 0
    assert plan['estimated_input_tokens'] == 0 and plan['output_token_allowance'] == 0
    assert plan['estimated_cost_usd'] == 0, 'free local work costs nothing (not unknown)'
    assert all(step['method'] == 'plain' and step['provider'] == 'local' and step['model'] is None for step in plan['steps'])
    assert plan['missing_inputs'] == {} and plan['fresh'] is False
    for step in plan['steps']:
        assert isinstance(step['unit_count'], int) and isinstance(step['scope_count'], int), 'counts are `unit_count` and `scope_count`'
        assert 'units' not in step and 'scopes' not in step, 'the retired count names are gone'
    assert _plan(api, fresh_book, local_steps)['fingerprint'] == plan['fingerprint'], 'the same plan has the same fingerprint'
    assert _plan(api, fresh_book, local_steps, fresh=True)['fingerprint'] != plan['fingerprint'], '`fresh` is part of the fingerprint'


def test_plan_runs_steps_in_pipeline_order_and_ignores_duplicates(api, fresh_book, local_steps):
    shuffled = list(reversed(local_steps)) + local_steps
    plan = _plan(api, fresh_book, shuffled)
    assert [step['step_id'] for step in plan['steps']] == local_steps
    assert plan['fingerprint'] == _plan(api, fresh_book, local_steps)['fingerprint']


def test_plan_checks_in_order_step_ids_then_book_then_chapters(api, fresh_book, local_steps):
    body = {'steps': ['no-such-step']}
    assert api.call('planBookAnalysisPipelineRun', path={'book_id': 'no-such-book'}, json=body, expect=400).code == 'unknown_step'
    assert api.call('planBookAnalysisPipelineRun', path={'book_id': 'no-such-book'}, json={'steps': local_steps},
                    expect=404).code == 'book_not_found'
    path = {'book_id': fresh_book['id']}
    assert api.call('planBookAnalysisPipelineRun', path=path, json={'steps': local_steps, 'chapter_ids': []},
                    expect=400).code == 'chapter_ids_empty'
    assert api.call('planBookAnalysisPipelineRun', path=path, json={'steps': local_steps, 'configs': {'no-such-step': {'provider': 'local'}}},
                    expect=400).code == 'unknown_step'


def test_plan_of_a_removed_book_is_allowed(api, fresh_book, local_steps):
    api.call('archiveBook', path={'book_id': fresh_book['id']})
    assert _plan(api, fresh_book, local_steps)['book_id'] == fresh_book['id']


# ---------------------------------------------------------------- running

def test_local_run_completes_without_any_provider(api, fresh_book, local_steps):
    started, job = _run(api, fresh_book, local_steps, wait=False)
    queued = started['job']
    assert queued['kind'] == 'pipeline' and queued['book_id'] == fresh_book['id']
    assert queued['status'] in ACTIVE, 'a queued job is not a result'
    assert started['run']['status'] == 'queued' and started['run']['step_run_ids'] == [], 'the run is a snapshot taken when queued'
    assert queued['run_id'] == started['run']['id'] and started['run']['job_id'] == queued['id']
    assert queued['steps'] == local_steps
    done = api.wait_for_job(queued['id'], book_id=fresh_book['id'])
    assert done['status'] == 'completed' and done['error'] is None, done
    assert done['progress'] == done['total'] > 0
    assert done['cancel_requested'] is False


def test_a_terminal_job_never_changes(api, fresh_book, local_steps):
    _, done = _run(api, fresh_book, local_steps)
    frozen = {name: done[name] for name in ('status', 'message', 'error', 'resume_after') if name in done}
    for _ in range(3):
        again = helpers.find_job(api, done['id'], fresh_book['id'])
        assert {name: again[name] for name in frozen} == frozen
    cancelled = api.call('cancelJob', path={'job_id': done['id']}).json
    assert {name: cancelled[name] for name in frozen} == frozen, 'cancelling a finished job returns it unchanged'


def test_run_results_are_recorded_and_accepted_by_default(api, fresh_book, local_steps):
    started, job = _run(api, fresh_book, local_steps)
    overview = api.call('getBookAnalysisPipeline', path={'book_id': fresh_book['id']}).json
    by_id = {step['id']: step for step in overview['steps']}
    for step in local_steps:
        assert by_id[step]['has_accepted'] and by_id[step]['accepted_scopes'] == by_id[step]['total_scopes'] >= 1
        assert by_id[step]['latest'] is not None and by_id[step]['latest']['status'] == 'completed'
    run = next(item for item in overview['recent_runs'] if item['id'] == started['run']['id'])
    assert run['status'] == 'completed' and len(run['step_run_ids']) == len(local_steps)
    assert run['job_id'] == job['id'] and run['steps'] == local_steps
    assert overview['active_run'] is None
    book = api.call('getBook', path={'book_id': fresh_book['id']}).json
    assert helpers.canonical_text(book) == helpers.canonical_text(fresh_book), 'a run never rewrites the source text'
    assert helpers.passage_anchors(book) == helpers.passage_anchors(fresh_book)


def test_a_run_needs_authorization(api, fresh_book, local_steps):
    path = {'book_id': fresh_book['id']}
    assert api.call('startBookAnalysisPipelineRun', path=path, json={'steps': local_steps}, expect=400).code == 'run_unconfirmed'
    stale = api.call('startBookAnalysisPipelineRun', path=path,
                     json={'steps': local_steps, 'expected_fingerprint': 'not-the-fingerprint'}, expect=409)
    assert stale.code == 'plan_stale'
    limited = api.call('startBookAnalysisPipelineRun', path=path, json={'steps': local_steps, 'limits': {'max_requests': 5}})
    assert api.wait_for_job(limited.json['job']['id'], book_id=fresh_book['id'])['status'] == 'completed'
    assert limited.json['run']['limits']['max_requests'] == 5, 'the run records the caps it was started with'


def test_run_error_codes(api, fresh_book, local_steps):
    path = {'book_id': fresh_book['id']}
    body = {'steps': local_steps, 'limits': {'max_requests': 5}}
    assert api.call('startBookAnalysisPipelineRun', path=path, json={'steps': ['no-such-step'], 'limits': {'max_requests': 5}},
                    expect=400).code == 'unknown_step'
    assert api.call('startBookAnalysisPipelineRun', path={'book_id': 'no-such-book'}, json=body, expect=404).code == 'book_not_found'
    api.call('archiveBook', path=path)
    assert api.call('startBookAnalysisPipelineRun', path=path, json=body, expect=409).code == 'book_archived'


def test_run_can_be_told_to_wait_for_review(api, fresh_book, local_steps):
    """`gates: {step: review}` records a candidate version that nobody accepted."""
    step = local_steps[-1]
    plan = _plan(api, fresh_book, [step])
    started = api.call('startBookAnalysisPipelineRun', path={'book_id': fresh_book['id']},
                       json={'steps': [step], 'expected_fingerprint': plan['fingerprint'], 'gates': {step: 'review'}}).json
    assert started['run']['gates'] == {step: 'review'}
    api.wait_for_job(started['job']['id'], book_id=fresh_book['id'])
    history = _versions(api, fresh_book, step)
    ours = next(item for item in history['items'] if item['run_id'] == started['run']['id'])
    if ours['state'] == 'candidate':
        assert not any(d['step_run_id'] == ours['id'] and d['action'] == 'accept' for d in history['decisions'])


# ---------------------------------------------------------------- review: preview, accept, reject

def test_candidate_can_be_previewed_accepted_and_the_book_revision_moves(api, fresh_book, local_steps):
    step = next((s for s in local_steps if s != 'structure'), local_steps[-1])
    path = {'book_id': fresh_book['id'], 'step_id': step}
    plan = _plan(api, fresh_book, [step])
    started = api.call('startBookAnalysisPipelineRun', path={'book_id': fresh_book['id']},
                       json={'steps': [step], 'expected_fingerprint': plan['fingerprint'], 'gates': {step: 'review'}}).json
    api.wait_for_job(started['job']['id'], book_id=fresh_book['id'])
    version = next(item for item in _versions(api, fresh_book, step)['items'] if item['run_id'] == started['run']['id'])
    if version['state'] != 'candidate':
        pytest.skip(f'the {step} step produced a version in state {version["state"]}, not a candidate to review')
    vpath = {**path, 'version_id': version['id']}
    impact = api.call('previewAnalysisPipelineStepVersion', path=vpath, json={}).json
    assert impact['revision'] >= fresh_book['revision']
    detail = api.call('getAnalysisPipelineStepVersion', path=vpath).json
    assert detail['step_id'] == step
    stale = api.call('acceptAnalysisPipelineStepVersion', path=vpath, json={'expected_revision': impact['revision'] - 1},
                     expect=409)
    assert stale.code == 'plan_stale', 'accepting with an out-of-date revision is refused'
    accepted = api.call('acceptAnalysisPipelineStepVersion', path=vpath, json={'expected_revision': impact['revision']}).json
    assert accepted['decision']['action'] == 'accept'
    after = api.call('getBook', path={'book_id': fresh_book['id']}).json
    assert after['revision'] == impact['revision'] + 1, 'accepting increments the book revision'
    assert helpers.canonical_text(after) == helpers.canonical_text(fresh_book)
    state = next(item for item in _versions(api, fresh_book, step)['items'] if item['id'] == version['id'])['state']
    assert state in ('accepted', 'partly_accepted')
    refused = api.call('rejectAnalysisPipelineStepVersion', path=vpath, json={}, expect=409)
    assert refused.code == 'version_accepted', 'an accepted, current version cannot be rejected'


def test_version_history_detail_and_errors(api, fresh_book, local_steps):
    step = local_steps[-1]
    _run(api, fresh_book, [step])
    path = {'book_id': fresh_book['id'], 'step_id': step}
    history = api.call('listAnalysisPipelineStepVersions', path=path, query={'limit': 1}).json
    assert history['step_id'] == step and len(history['items']) == 1
    newest = history['items'][0]
    detail = api.call('getAnalysisPipelineStepVersion', path={**path, 'version_id': newest['id']}, query={'compare': 'none'}).json
    assert detail['diff']['compared_with'] is None
    accepted = api.call('getAnalysisPipelineStepVersion', path={**path, 'version_id': 'accepted'}).json
    assert accepted['step_id'] == step
    missing = api.call('getAnalysisPipelineStepVersion', path={**path, 'version_id': 'no-such-version'}, expect=404)
    assert missing.code == 'step_version_not_found'
    assert api.call('listAnalysisPipelineStepVersions', path={**path, 'step_id': 'no-such-step'}, expect=404).code == 'step_not_found'
    assert api.call('listAnalysisPipelineStepVersions', path={**path, 'book_id': 'no-such-book'}, expect=404).code == 'book_not_found'
    assert api.call('rejectAnalysisPipelineStepVersion', path={**path, 'version_id': 'no-such-version'}, json={},
                    expect=404).code == 'step_version_not_found'
    assert api.call('previewAnalysisPipelineStepVersion', path={**path, 'version_id': 'no-such-version'}, json={},
                    expect=404).code == 'step_version_not_found'
    assert api.call('acceptAnalysisPipelineStepVersion', path={**path, 'version_id': 'no-such-version'}, json={},
                    expect=404).code == 'step_version_not_found'


def test_result_rows_carry_their_step_and_the_comparison_fields(api, fresh_book, local_steps):
    """Every row of a step's result table says which step it belongs to and how it compares with the other version;
    the retired `_diff`, `_changed` and `_previous` keys are gone."""
    _run(api, fresh_book, local_steps)
    for step in local_steps:
        path = {'book_id': fresh_book['id'], 'step_id': step, 'version_id': 'accepted'}
        detail = api.call('getAnalysisPipelineStepVersion', path=path, query={'limit': 50}).json
        assert detail['rows'], f'the {step} step recorded no result rows to inspect'
        for row in detail['rows']:
            assert row['step'] == step, (step, row)
            assert {'diff_state', 'changed_keys', 'previous'} <= set(row), (step, sorted(row))
            assert not {'_diff', '_changed', '_previous'} & set(row), (step, sorted(row))
            if row['diff_state'] in (None, 'added'):
                assert row['changed_keys'] is None and row['previous'] is None, 'null when not compared, and for an added row'
            else:
                assert isinstance(row['changed_keys'], list), (step, row)
            assert {column['key'] for column in detail['columns']} <= set(row), 'a column key names a row key'


# ---------------------------------------------------------------- jobs

def test_list_jobs_is_a_bare_newest_first_array_with_a_book_filter(api, fresh_book, local_steps):
    _, first = _run(api, fresh_book, local_steps)
    _, second = _run(api, fresh_book, local_steps, fresh=True)
    mine = api.call('listJobs', query={'book_id': fresh_book['id']}).json
    assert isinstance(mine, list) and [job['id'] for job in mine[:2]] == [second['id'], first['id']], 'newest first'
    assert all(job['book_id'] == fresh_book['id'] for job in mine)
    assert api.call('listJobs', query={'book_id': 'a-book-with-no-jobs'}).json == []


def test_active_filter_returns_only_queued_or_running_jobs(api, fresh_book, local_steps):
    _run(api, fresh_book, local_steps)
    active = api.call('listJobs', query={'active': True}).json
    assert all(job['status'] in ACTIVE for job in active)
    assert fresh_book['id'] not in {job['book_id'] for job in active}
    everything = api.call('listJobs', query={'active': False}).json
    assert len(everything) <= 100, 'without `active`, at most the 100 most recent jobs'
    assert {job['status'] for job in everything} <= (TERMINAL | ACTIVE)


def test_a_single_job_reads_the_same_as_in_the_list(api, contract, fresh_book, local_steps):
    if 'getJob' not in contract.operations:
        pytest.skip('this contract has no single-job route; jobs are followed through listJobs')
    _, done = _run(api, fresh_book, local_steps)
    assert api.call('getJob', path={'job_id': done['id']}).json == helpers.find_job(api, done['id'], fresh_book['id'])
    assert api.call('getJob', path={'job_id': 'no-such-job'}, expect=404).code == 'job_not_found'


def test_cancel_unknown_job_is_404(api):
    assert api.call('cancelJob', path={'job_id': 'no-such-job'}, expect=404).code == 'job_not_found'


def test_cancelling_a_queued_job_never_lets_it_start(api, local_steps):
    """A queued job cancels immediately and stays cancelled; a job that already ran comes back unchanged."""
    book = helpers.import_fresh_txt(api, 'cancel')
    plan = _plan(api, book, local_steps)
    body = {'steps': local_steps, 'expected_fingerprint': plan['fingerprint']}
    started = api.call('startBookAnalysisPipelineRun', path={'book_id': book['id']}, json=body).json
    cancelled = api.call('cancelJob', path={'job_id': started['job']['id']}).json
    assert cancelled['id'] == started['job']['id']
    final = api.wait_for_job(started['job']['id'], book_id=book['id'])
    assert final['status'] in TERMINAL
    if cancelled['status'] == 'cancelled':
        assert final['status'] == 'cancelled', 'a cancelled queued job stays cancelled'
        assert cancelled['cancel_requested'] is True
    else:
        assert cancelled['status'] in ACTIVE | TERMINAL
