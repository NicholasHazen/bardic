"""Contract coverage for analysis pipeline shapes the flow tests do not reach.

Offline: synthetic prose, the fake metered provider from test_analysis_pipeline, no network.
The conftest hook validates every response below against contract views.
"""
import bardic.analysis

from test_analysis_pipeline import client, import_book, latest, run  # noqa: F401  (client is a fixture)


def test_version_tables_diffs_and_decision_errors(client, monkeypatch):  # noqa: F811
    book = import_book(client)
    base = f"/api/books/{book['id']}/analysis-pipeline"

    job, _ = run(client, book['id'], ['structure', 'census'], scheduling='parallel')
    assert job['status'] == 'completed'
    structure = client.get(f'{base}/steps/structure/versions/accepted').json()
    assert structure['rows'] and {'title', 'kind', 'source'} <= set(structure['rows'][0])
    version = latest(client, book['id'], 'structure')['id']
    assert client.get(f'{base}/steps/structure/versions/{version}', params={'compare': 'none'}).json()['diff']['compared_with'] is None

    job, _ = run(client, book['id'], ['discovery'])
    assert job['status'] == 'completed'
    original = bardic.analysis._openai_request

    def reworded(*args):
        result = original(*args)
        for character in result.get('characters', []):
            character['description'] = 'Reworded ' + character['description']
        return result

    monkeypatch.setattr('bardic.analysis._openai_request', reworded)
    job, _ = run(client, book['id'], ['discovery'], fresh=True, gates={'discovery': 'review'})
    assert job['status'] == 'completed'
    candidate = latest(client, book['id'], 'discovery')
    assert candidate['state'] == 'candidate'
    detail = client.get(f"{base}/steps/discovery/versions/{candidate['id']}", params={'changed_only': True}).json()
    assert detail['diff']['changed'] and detail['rows'][0]['_changed'] == ['description']
    assert detail['rows'][0]['_previous']['description'].endswith('draft.')
    scope = detail['scopes'][0]['scope']
    assert all(row['scope'] == scope for row in client.get(
        f"{base}/steps/discovery/versions/{candidate['id']}", params={'scope': scope}).json()['rows'])

    path = f"{base}/steps/discovery/versions/{candidate['id']}"
    assert client.get(path, params={'offset': -1}).json()['offset'] == 0  # clamped
    assert client.get(path, params={'compare': 'f' * 32}).status_code == 400
    assert client.get(f'{base}/steps/nope/versions').status_code == 404
    assert client.get(f"{base}/steps/discovery/versions/{'f' * 32}").status_code == 404
    assert client.get(f"{base}/steps/census/versions/{candidate['id']}").status_code == 404
    assert client.post(f'{path}/preview', json={'scopes': []}).status_code == 400
    assert client.post(f'{base}/steps/discovery/versions/accepted/reject', json={}).status_code == 409

    impact = client.post(f'{path}/preview', json={}).json()
    assert impact['changed_scopes']
    stale = client.post(f'{path}/accept', json={'expected_revision': impact['revision'] + 1})
    assert stale.status_code == 409
    accepted = client.post(f'{path}/accept', json={'expected_revision': impact['revision']}).json()
    assert accepted['decision']['action'] == 'accept' and accepted['revision'] == impact['revision'] + 1
    rejected = client.post(f'{path}/reject', json={})
    assert rejected.status_code == 409  # an accepted version cannot be rejected
    assert client.get(base).json()['steps'][2]['latest']['state'] == 'accepted'
