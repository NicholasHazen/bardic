"""Series memory on the step pipeline: accepted evidence of earlier volumes, staged consent, review pauses.

Synthetic prose, fake metered providers and tmp_path libraries only; no network access.
"""
import json
import time

import pytest

from bardic.series import SeriesRepository
from test_analysis_pipeline import run
from test_series_processing import (SECRET, children, client, collection, import_volume, pipeline_runs,  # noqa: F401
                                    preview, process, wait_job)


def mara(client, book_id):
    return next(c for c in client.get(f'/api/books/{book_id}').json()['characters'] if c['name'] == 'Mara')


def link(client, series, books, identity=None):
    """Confirm that every given book's Mara is one series identity."""
    identity = identity or client.post(f"/api/series/{series['id']}/characters", json={'name': 'Mara'}).json()
    for book in books:
        response = client.put(f"/api/books/{book['id']}/series/characters/{mara(client, book['id'])['id']}",
                              json={'series_character_id': identity['id']})
        assert response.status_code == 200, response.text
    return identity


def discovered(client, positions=(1, 2)):
    series, books = collection(client, positions=positions)
    for book in books:
        assert run(client, book['id'], ['discovery'])[0]['status'] == 'completed'
    return series, books


def context(client, book):
    response = client.get(f"/api/books/{book['id']}/series/context")
    assert response.status_code == 200, response.text
    return response.json()


def entries(value):
    return [o for character in value['characters'] for o in character['observations']]


def describe_profiles(client, text):
    """Profile results read `text` as their description; returns the profile prompts sent."""
    prompts = []

    def mangle(stage, prompt, result):
        if stage == 'profiles':
            prompts.append(prompt)
            for item in result['characters']:
                item['description'] = text[0]
    client.provider.mangle = mangle
    return prompts


def earlier_in(prompt):
    return json.JSONDecoder().raw_decode(prompt.split('EARLIER LINKED VOLUMES:\n', 1)[1])[0]


def stale(client, book, step='profiles'):
    overview = client.get(f"/api/books/{book['id']}/analysis-pipeline").json()
    return next(s for s in overview['steps'] if s['id'] == step)['stale_scopes']


def wait_paused(client, parent_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.app.state.runtime.store.job(parent_id)
        if job.get('waiting_for_review') or job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(.01)
    pytest.fail('series run did not pause')


# --- section 2: context from accepted evidence ------------------------------------------------------

def test_context_reads_earlier_accepted_evidence_only_through_confirmed_links_and_earlier_positions(client):
    series, books = discovered(client, positions=(1, 2, 3))
    first, second, third = books
    assert entries(context(client, second)) == []  # accepted evidence, but no confirmed link yet
    identity = link(client, series, [first, second])
    found = entries(context(client, second))
    assert found and {o['book_id'] for o in found} == {first['id']}
    assert {o['kind'] for o in found} <= {'profile_evidence', 'dialogue'}  # never a mention
    assert all(o['step'] == 'discovery' and o['version_id'] and o['origin'] == 'run' for o in found)
    assert entries(context(client, first)) == []  # nothing earlier than the first volume
    # The third volume's Mara is not linked: nothing, even though names match.
    assert entries(context(client, third)) == []
    link(client, series, [third], identity)
    assert {o['book_id'] for o in entries(context(client, third))} == {first['id'], second['id']}
    # A later volume never feeds an earlier one.
    assert {o['book_id'] for o in entries(context(client, second))} == {first['id']}
    assert client.provider.calls == ['discovery'] * 3


def test_rollback_in_an_earlier_volume_removes_its_evidence_and_marks_the_later_profile_stale(client):
    series, (first, second) = discovered(client)
    link(client, series, [first, second])
    text = ['First reading.']
    prompts = describe_profiles(client, text)
    original = run(client, first['id'], ['profiles'])[1]
    text[0] = 'Second reading.'
    assert run(client, first['id'], ['profiles'], fresh=True)[0]['status'] == 'completed'
    assert {o['description'] for o in entries(context(client, second))} >= {'Second reading.'}
    assert run(client, second['id'], ['profiles'])[0]['status'] == 'completed'
    assert 'Second reading.' in {o['description'] for o in earlier_in(prompts[-1])}
    assert stale(client, second) == []
    calls = len(client.provider.calls)
    # Roll volume one back to its first profile version.
    step_run = next(v for v in client.get(f"/api/books/{first['id']}/analysis-pipeline/steps/profiles/versions").json()['items']
                    if v['run_id'] == original['id'])
    accepted = client.post(f"/api/books/{first['id']}/analysis-pipeline/steps/profiles/versions/{step_run['id']}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    descriptions = {o['description'] for o in entries(context(client, second))}
    assert 'Second reading.' not in descriptions and 'First reading.' in descriptions
    assert stale(client, second) == [mara(client, second['id'])['id']]
    assert len(client.provider.calls) == calls  # stale, not re-run
    # Unlinking in volume one also changes what volume two can read.
    client.put(f"/api/books/{first['id']}/series/characters/{mara(client, first['id'])['id']}", json={'series_character_id': None})
    assert entries(context(client, second)) == []


def test_earlier_evidence_from_a_changed_source_is_excluded_until_rebuilt(client):
    series, (first, second) = discovered(client)
    link(client, series, [first, second])
    assert entries(context(client, second))
    store = client.app.state.runtime.store
    book = store.book(first['id'])
    book['chapters'][0]['text'] += '\n\nAn appended line.'  # every stored quote still matches its slice
    store.save_book(book)
    assert entries(context(client, second)) == []
    client.get(f"/api/books/{first['id']}/analysis-pipeline")  # the next sync rebuilds and revalidates
    assert entries(context(client, second))


def test_invalidated_earlier_evidence_is_not_sent(client):
    series, (first, second) = discovered(client)
    link(client, series, [first, second])
    store = client.app.state.runtime.store
    with store.connect() as conn:
        conn.execute("UPDATE character_references SET body=json_set(body,'$.quote','Invented words') WHERE book_id=?",
                     (first['id'],))
    assert entries(context(client, second)) == []


# --- section 4: staged consent --------------------------------------------------------------------

def test_two_book_profiles_run_with_memory_completes_with_consent_up_to(client):
    series, (first, second) = discovered(client)
    link(client, series, [first, second])
    text = ['Series reading.']
    prompts = describe_profiles(client, text)
    plan = preview(client, series, steps=['profiles'])
    by_book = {b['book_id']: b for b in plan['books']}
    assert by_book[first['id']]['context_pending'] == [] and by_book[first['id']]['context_sources'] == []
    assert by_book[second['id']]['context_pending'] == ['profiles']
    assert by_book[second['id']]['context_sources'] == [first['id']]
    assert plan['context_pending_books'] == [second['id']]
    assert plan['up_to']['requests'] >= plan['requests'] and plan['up_to']['requests'] == 2
    parent = wait_job(client, process(client, series, plan, steps=['profiles']).json()['id'])
    assert parent['status'] == 'completed', parent
    assert [c['status'] for c in children(client, series)] == ['completed', 'completed']
    # Volume two read volume one's evidence as accepted during this run.
    assert 'Series reading.' in {o['description'] for o in earlier_in(prompts[-1])}
    assert stale(client, second) == []


def test_changed_unit_set_still_stops_the_series(client, monkeypatch):
    series, (first, second) = discovered(client)
    link(client, series, [first, second])
    runtime = client.app.state.runtime
    queued = []
    monkeypatch.setattr(runtime.series_pool, 'submit', lambda fn, *args: queued.append((fn, args)))
    parent = process(client, series, steps=['profiles']).json()
    # A bypassed reservation: volume two's Mara is now reviewed by hand, so she has no unit.
    book = runtime.store.book(second['id'])
    character = next(c for c in book['characters'] if c['name'] == 'Mara')
    character.update(edited=True)
    runtime.store.save_book(book)
    fn, args = queued[0]
    fn(*args)
    assert runtime.store.job(parent['id'])['status'] == 'failed'
    assert [c['status'] for c in children(client, series)] == ['completed', 'failed']


def test_review_gate_pauses_until_reviewed_then_resume_completes(client):
    series, (first, second) = discovered(client)
    link(client, series, [first, second])
    text = ['Reviewed reading.']
    prompts = describe_profiles(client, text)
    plan = preview(client, series, steps=['profiles'])
    started = process(client, series, plan, steps=['profiles'], gates={'profiles': 'review'})
    assert started.status_code == 200, started.text
    parent = wait_paused(client, started.json()['id'])
    assert parent['status'] == 'running'
    wait = parent['waiting_for_review']
    assert wait['book_id'] == first['id'] and wait['steps'] == ['profiles']
    assert parent['message'].startswith('Waiting for your review of')
    kids = children(client, series)
    assert kids[0]['status'] == 'completed' and kids[1]['status'] == 'queued' and kids[1]['run_id'] is None
    assert len(prompts) == 1
    # Unreviewed candidates are never read, and resuming is refused until the review is done.
    assert 'Reviewed reading.' not in {o['description'] for o in entries(context(client, second))}
    refused = client.post(f"/api/series/{series['id']}/runs/{parent['id']}/resume")
    assert refused.status_code == 409 and 'waiting for your review' in refused.text
    # Every reservation except the waiting book's decisions still holds.
    for book in (first, second):
        assert client.patch(f"/api/books/{book['id']}/metadata", json={'title': 'Blocked', 'author': ''}).status_code == 409
    other = client.post(f"/api/books/{second['id']}/analysis-pipeline/steps/discovery/versions/accepted/accept", json={})
    assert other.status_code == 409
    version = next(v for v in client.get(f"/api/books/{first['id']}/analysis-pipeline/steps/profiles/versions").json()['items']
                   if v['state'] == 'candidate')
    accepted = client.post(f"/api/books/{first['id']}/analysis-pipeline/steps/profiles/versions/{version['id']}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    resumed = client.post(f"/api/series/{series['id']}/runs/{parent['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()['waiting_for_review'] is None
    parent = wait_job(client, parent['id'])
    assert parent['status'] == 'completed', parent
    assert 'Reviewed reading.' in {o['description'] for o in earlier_in(prompts[-1])}
    kids = children(client, series)
    # The last book's version waits for review; nothing later reads it, so the run does not pause for it.
    assert kids[1]['status'] == 'completed' and kids[1]['message'].startswith('Waiting for your review')
    again = client.post(f"/api/series/{series['id']}/runs/{parent['id']}/resume")
    assert again.status_code == 409


def test_setting_aside_the_waiting_version_also_allows_resume(client):
    series, (first, second) = discovered(client)
    link(client, series, [first, second])
    parent = wait_paused(client, process(client, series, steps=['profiles'], gates={'profiles': 'review'}).json()['id'])
    version = next(v for v in client.get(f"/api/books/{first['id']}/analysis-pipeline/steps/profiles/versions").json()['items']
                   if v['state'] == 'candidate')
    assert client.post(f"/api/books/{first['id']}/analysis-pipeline/steps/profiles/versions/{version['id']}/reject",
                       json={}).status_code == 200
    assert client.post(f"/api/series/{series['id']}/runs/{parent['id']}/resume").status_code == 200
    assert wait_job(client, parent['id'])['status'] == 'completed'


def test_cancelling_while_paused_ends_the_run_and_queued_children_never_start(client):
    series, books = discovered(client, positions=(1, 2, 3))
    link(client, series, books)
    runtime = client.app.state.runtime
    parent = wait_paused(client, process(client, series, steps=['profiles'], gates={'profiles': 'review'}).json()['id'])
    assert parent['waiting_for_review']['book_id'] == books[0]['id']
    calls = len(client.provider.calls)
    cancelled = client.post(f"/api/jobs/{parent['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    assert cancelled.json()['waiting_for_review'] is None
    kids = children(client, series)
    assert [c['status'] for c in kids] == ['completed', 'cancelled', 'cancelled']
    assert all(c['not_started'] and c['run_id'] is None for c in kids[1:])
    assert client.post(f"/api/series/{series['id']}/runs/{parent['id']}/resume").status_code == 409
    runtime.series_pool.submit(lambda: None).result(timeout=5)
    assert not [r for b in books[1:] for r in pipeline_runs(client, b['id']) if r.get('series_run_id') == parent['id']]
    assert len(client.provider.calls) == calls
    assert runtime.store.job(parent['id'])['status'] == 'cancelled'
    # Nothing is reserved any more.
    assert client.patch(f"/api/books/{books[2]['id']}/metadata", json={'title': 'Editable', 'author': ''}).status_code == 200


def test_a_child_cancelled_while_paused_is_never_started_by_resume(client):
    series, books = discovered(client, positions=(1, 2, 3))
    link(client, series, books)
    parent = wait_paused(client, process(client, series, steps=['profiles'], gates={'profiles': 'review'}).json()['id'])
    kids = children(client, series)
    assert client.post(f"/api/jobs/{kids[1]['id']}/cancel").json()['status'] == 'cancelled'
    version = next(v for v in client.get(f"/api/books/{books[0]['id']}/analysis-pipeline/steps/profiles/versions").json()['items']
                   if v['state'] == 'candidate')
    client.post(f"/api/books/{books[0]['id']}/analysis-pipeline/steps/profiles/versions/{version['id']}/accept", json={})
    assert client.post(f"/api/series/{series['id']}/runs/{parent['id']}/resume").status_code == 200
    assert wait_job(client, parent['id'])['status'] == 'cancelled'
    kids = children(client, series)
    assert [c['status'] for c in kids] == ['completed', 'cancelled', 'cancelled']
    assert kids[1]['run_id'] is None and kids[2]['run_id'] is None
    assert not [r for b in books[1:] for r in pipeline_runs(client, b['id']) if r.get('series_run_id') == parent['id']]


def test_review_gate_without_a_later_reader_does_not_pause(client):
    series, books = discovered(client)
    parent = wait_job(client, process(client, series, steps=['profiles'], gates={'profiles': 'review'}).json()['id'])
    assert parent['status'] == 'completed' and not parent.get('waiting_for_review')
    assert all(c['message'].startswith('Waiting for your review') for c in children(client, series))


def test_resume_refuses_unknown_and_foreign_runs(client):
    series, _ = discovered(client, positions=(1,))
    other = client.post('/api/series', json={'name': 'Elsewhere'}).json()
    parent = wait_job(client, process(client, series, steps=['census']).json()['id'])
    assert client.post(f"/api/series/{series['id']}/runs/missing/resume").status_code == 404
    assert client.post(f"/api/series/{other['id']}/runs/{parent['id']}/resume").status_code == 404
    assert client.post(f"/api/series/{series['id']}/runs/{parent['id']}/resume").status_code == 409


# --- section 3: suggested links -------------------------------------------------------------------

def test_suggestions_never_link_without_confirmation_and_namesakes_need_a_choice(client):
    series, (first, second, third) = discovered(client, positions=(1, 2, 3))
    identity = link(client, series, [first])
    suggestions = client.get(f"/api/books/{third['id']}/series/suggestions")
    assert suggestions.status_code == 200, suggestions.text
    body = suggestions.json()
    assert body['series_id'] == series['id'] and len(body['suggestions']) == 1
    item = body['suggestions'][0]
    assert item['character_id'] == mara(client, third['id'])['id'] and not item['ambiguous']
    assert [c['series_character_id'] for c in item['candidates']] == [identity['id']]
    assert item['candidates'][0]['sources'][0]['book_id'] == first['id']
    # Reading suggestions links nothing.
    assert client.get(f"/api/books/{third['id']}/series").json()['links'] == []
    # A namesake identity linked in another earlier volume makes it a choice.
    namesake = client.post(f"/api/series/{series['id']}/characters", json={'name': 'Mara'}).json()
    link(client, series, [second], namesake)
    item = client.get(f"/api/books/{third['id']}/series/suggestions").json()['suggestions'][0]
    assert item['ambiguous'] and {c['series_character_id'] for c in item['candidates']} == {identity['id'], namesake['id']}
    assert client.get(f"/api/books/{third['id']}/series").json()['links'] == []
    # The first volume has nothing earlier to match; a later volume is never a source.
    assert client.get(f"/api/books/{first['id']}/series/suggestions").json()['suggestions'] == []
    # Confirming uses the existing link route; the suggestion then disappears.
    link(client, series, [third], identity)
    assert client.get(f"/api/books/{third['id']}/series/suggestions").json()['suggestions'] == []
    assert client.provider.calls == ['discovery'] * 3


def test_suggestions_for_a_book_outside_any_series_are_empty(client):
    book = import_volume(client, 7)
    assert client.get(f"/api/books/{book['id']}/series/suggestions").json() == {
        'book_id': book['id'], 'series_id': None, 'suggestions': []}
    assert client.get('/api/books/missing/series/suggestions').status_code == 404


def test_repository_suggestions_match_aliases_and_skip_reserved_characters(tmp_path):
    from bardic.store import Store

    store = Store(tmp_path)

    def book(book_id, characters):
        return {'id': book_id, 'title': book_id, 'author': '', 'chapters': [{'id': 'c1', 'title': 'One', 'text': 'Text.'}],
                'characters': [{'id': 'narrator', 'name': 'Narrator'}, {'id': 'unassigned', 'name': 'Unassigned'}, *characters],
                'segments': []}
    store.save_book(book('early', [{'id': 'e-mira', 'name': 'Mira Vance', 'aliases': ['The Lamplighter']}]))
    store.save_book(book('late', [{'id': 'l-mira', 'name': 'the  lamplighter', 'aliases': []},
                                  {'id': 'l-other', 'name': 'Tomas', 'aliases': []}]))
    repository = SeriesRepository(store)
    saga = repository.create_series('Lamps')
    repository.set_membership('early', saga['id'], 1)
    repository.set_membership('late', saga['id'], 2)
    identity = repository.create_character(saga['id'], 'Mira')
    repository.link_character('early', 'e-mira', identity['id'])
    result = repository.suggestions('late')
    assert [s['character_id'] for s in result['suggestions']] == ['l-mira']
    assert result['suggestions'][0]['candidates'][0]['matched_names'] == ['the  lamplighter']
    assert repository.links_for_book('late') == []
