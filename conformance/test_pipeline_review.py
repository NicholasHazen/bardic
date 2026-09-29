"""Setting aside a candidate version of a free local step."""
from __future__ import annotations

import pytest


def _versions(api, book, step):
    return api.call('listAnalysisPipelineStepVersions', path={'book_id': book['id'], 'step_id': step}).json


def test_a_candidate_can_be_set_aside_without_changing_the_book(api, fresh_book, local_steps):
    """Rejecting a version records a decision only: the book, accepted versions and retained results stay."""
    step = local_steps[-1]
    plan = api.call('planBookAnalysisPipelineRun', path={'book_id': fresh_book['id']}, json={'steps': [step]}).json
    started = api.call('startBookAnalysisPipelineRun', path={'book_id': fresh_book['id']},
                       json={'steps': [step], 'expected_fingerprint': plan['fingerprint'], 'gates': {step: 'review'}}).json
    api.wait_for_job(started['job']['id'], book_id=fresh_book['id'])
    version = next(item for item in _versions(api, fresh_book, step)['items'] if item['run_id'] == started['run']['id'])
    if version['state'] != 'candidate':
        pytest.skip(f'the {step} step produced a version in state {version["state"]}, not a candidate to review')
    path = {'book_id': fresh_book['id'], 'step_id': step, 'version_id': version['id']}
    before = api.call('getBook', path={'book_id': fresh_book['id']}).json
    unknown_scope = api.call('rejectAnalysisPipelineStepVersion', path=path, json={'scopes': ['no-such-scope']}, expect=400)
    assert unknown_scope.code in ('unknown_scope', 'scopes_empty')
    decision = api.call('rejectAnalysisPipelineStepVersion', path=path, json={}).json
    assert decision['action'] == 'reject' and decision['mode'] == 'user' and decision['step_id'] == step
    assert api.call('getBook', path={'book_id': fresh_book['id']}).json == before, 'a rejection changes no book field'
    state = next(item for item in _versions(api, fresh_book, step)['items'] if item['id'] == version['id'])['state']
    assert state == 'rejected'
    api.call('getAnalysisPipelineStepVersion', path=path)  # a rejected version stays inspectable
