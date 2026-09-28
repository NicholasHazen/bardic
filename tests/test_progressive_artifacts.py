"""Recipe identity, rejected responses and retry provenance stay inspectable."""
from copy import deepcopy
import hashlib
import json
import zipfile

import httpx
import pytest

from bardic import analysis, progressive
from bardic.artifacts import ArtifactRepository
from bardic.importer import parse_book
from bardic.pipeline_view import write_analysis_export
from bardic.preprocessing import coverage
from bardic.processing import BudgetReached, ProcessingStore, digest, source_hash
from bardic.series import SeriesRepository
from bardic.store import Store
from test_progressive import FakeProvider, process, story


@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path)
    book = story(1)
    store.save_book(book)
    return store, book


@pytest.mark.parametrize('setting', ['system_instruction', 'output_cap', 'adapter_version', 'validator_version', 'schema_version'])
def test_cache_identity_tracks_effective_recipe_settings(monkeypatch, setting):
    spec = progressive.discovery_specs(story(1), story(1)['chapters'])[0]
    before = progressive.unit_key(spec, 'openai', 'model')
    assert before == 'discovery:' + digest(progressive.request_recipe(spec, 'openai', 'model'))
    if setting == 'system_instruction':
        monkeypatch.setattr(analysis, 'DIRECTOR_INSTRUCTION', analysis.DIRECTOR_INSTRUCTION + ' New direction rule.')
    elif setting == 'output_cap':
        spec['output_cap'] += 1
    else:
        constant = {'adapter_version': 'ADAPTER_VERSION', 'validator_version': 'VALIDATOR_VERSION',
                    'schema_version': 'RECIPE_SCHEMA_VERSION'}[setting]
        monkeypatch.setattr(progressive, constant, getattr(progressive, constant) + 1)
    assert progressive.unit_key(spec, 'openai', 'model') != before


def test_recipe_ignores_display_metadata_and_keeps_effective_provider_settings():
    book = story(1)
    spec = progressive.discovery_specs(book, book['chapters'])[0]
    before = progressive.unit_key(spec, 'gemini', 'model')
    spec.update(label='Updated chapter title', estimated_cost=100, input_unit_keys=['same-text-new-observer'])
    assert progressive.unit_key(spec, 'gemini', 'model') == before
    recipe = progressive.request_recipe(spec, 'gemini', 'model')
    assert recipe['temperature'] == .2
    assert recipe['output_token_limit'] == spec['output_cap']
    assert progressive.request_recipe(spec, 'openai', 'model')['temperature'] is None


def test_changed_system_instruction_stales_profiles_but_keeps_accepted_discovery(library, monkeypatch):
    store, book = library
    provider = FakeProvider(monkeypatch)
    first = process(book, store, 'full')
    before = len(provider.calls)
    monkeypatch.setattr(analysis, 'DIRECTOR_INSTRUCTION', analysis.DIRECTOR_INSTRUCTION + ' A revised profile rule.')
    plan = progressive.plan(first, store, 'openai', 'gpt-6-sol', 'gpt-6-luna', 'profiles')
    assert plan['requests'] == 2
    assert all(c['state'] == 'stale' for c in plan['coverage']['characters'])
    scanned = process(first, store, 'scan')
    assert len(provider.calls) == before
    assert coverage(scanned, store)['whole_book_discovered']
    assert all(c['profile_state'] == 'stale' for c in scanned['characters'] if c['id'] not in {'narrator', 'unassigned'})
    refined = process(scanned, store, 'profiles')
    assert len(provider.calls) == before + 2
    assert all(c['profile_state'] == 'current' for c in refined['characters'] if c['id'] not in {'narrator', 'unassigned'})


def test_accepted_and_cached_events_link_to_exact_saved_input_and_output(library, monkeypatch):
    store, book = library
    provider = FakeProvider(monkeypatch)
    first = process(book, store, 'full')
    repository, artifacts = ProcessingStore(store), ArtifactRepository(store)
    accepted = [e for e in repository.events(book['id']) if e['event'] == 'accepted']
    assert len(accepted) == len(provider.calls)
    for event in accepted:
        output = artifacts.get(book['id'], event['artifact_id'])
        assert output['kind'] == 'analysis_output'
        assert 'input_recipe' not in output['payload']
        assert len(output['dependencies']) == 1
        recipe = artifacts.get(book['id'], output['dependencies'][0])
        assert recipe['kind'] == 'analysis_input'
        assert recipe['payload']['system_instruction'] == analysis.DIRECTOR_INSTRUCTION
        assert recipe['payload']['repair_instruction'] == ''
        assert recipe['payload']['model'] == ('gpt-6-luna' if event['stage'] == 'discovery' else 'gpt-6-sol')
        assert 'fake-key' not in json.dumps(recipe)
        assert recipe['dependencies']
    before = len(provider.calls)
    process(first, store, 'full')
    assert len(provider.calls) == before
    cached = [e for e in repository.events(book['id']) if e['event'] == 'cache_hit']
    assert len(cached) == before
    assert {e['artifact_id'] for e in cached} == {e['artifact_id'] for e in accepted}


def metered_responses(monkeypatch, results):
    calls = []
    def post(client, url, **kwargs):
        calls.append(deepcopy(kwargs['json']))
        result = results[min(len(calls) - 1, len(results) - 1)]
        return httpx.Response(200, json={
            'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 80},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]})
    monkeypatch.setattr(httpx.Client, 'post', post)
    return calls


def rejected_response():
    return {'characters': [{'name': 'Mara', 'aliases': [], 'description': 'Unsupported description',
                            'direction': 'Natural', 'evidence': ['An invented quotation.']}]}


def test_repaired_response_keeps_rejection_and_distinct_attempt_input_lineage(library, monkeypatch):
    store, book = library
    invalid = rejected_response()
    calls = metered_responses(monkeypatch, [invalid, {'characters': []}])
    process(book, store, 'scan', run_id='repair-success')
    assert len(calls) == 2
    repository, artifacts = ProcessingStore(store), ArtifactRepository(store)
    attempts = repository.attempts(book['id'], 'repair-success')
    assert len(attempts) == 2
    assert attempts[0]['input_artifact_id'] != attempts[1]['input_artifact_id']
    events = repository.events(book['id'])
    rejected = next(e for e in events if e['event'] == 'validation_rejected')
    accepted = next(e for e in events if e['event'] == 'accepted')
    rejection = artifacts.get(book['id'], rejected['artifact_id'])
    assert rejection['kind'] == 'analysis_rejection'
    assert rejection['payload']['result'] == invalid
    assert rejection['payload']['attempt_id'] == rejected['attempt_id'] == attempts[0]['id']
    assert rejection['dependencies'] == [attempts[0]['input_artifact_id']]
    output = artifacts.get(book['id'], accepted['artifact_id'])
    assert output['payload']['result'] == {'characters': []}
    assert output['payload']['producing_attempt_id'] == accepted['attempt_id'] == attempts[1]['id']
    assert output['dependencies'] == [attempts[1]['input_artifact_id']]
    recipe = artifacts.get(book['id'], attempts[1]['input_artifact_id'])['payload']
    assert recipe['repair_instruction']
    assert calls[1]['input'][0]['content'] == recipe['prompt'] + '\n\n' + recipe['repair_instruction']


def test_repair_budget_refusal_is_not_attributed_to_preceding_paid_attempt(library, monkeypatch):
    store, book = library
    calls = metered_responses(monkeypatch, [rejected_response()])
    with pytest.raises(BudgetReached):
        process(book, store, 'scan', run_id='repair-budget', limits={'max_requests': 1})
    assert len(calls) == 1
    repository = ProcessingStore(store)
    attempts = repository.attempts(book['id'], 'repair-budget')
    events = repository.events(book['id'])
    rejected = next(e for e in events if e['event'] == 'validation_rejected')
    limited = next(e for e in events if e['event'] == 'budget_limited')
    assert rejected['attempt_id'] == attempts[0]['id']
    assert limited['attempt_id'] is None
    assert repository.units(book['id'], 'discovery', source_hash(book)) == []


@pytest.mark.parametrize('regeneration_succeeds', [True, False])
def test_rejected_cache_is_replaced_with_bounded_generation_and_history_survives(library, monkeypatch, regeneration_succeeds):
    store, book = library
    provider = FakeProvider(monkeypatch)
    scanned = process(book, store, 'scan')
    repository, artifacts = ProcessingStore(store), ArtifactRepository(store)
    unit = repository.units(book['id'], 'discovery', source_hash(book))[0]
    unit['result'] = rejected_response()
    rejected_id = repository.save_unit(book['id'], unit['unit_key'], 'discovery', source_hash(book), unit)
    before = len(provider.calls)
    if not regeneration_succeeds:
        def unavailable(*args):
            raise ValueError('Provider unavailable')
        provider.transform = unavailable
        with pytest.raises(ValueError, match='Provider unavailable'):
            process(scanned, store, 'scan')
        assert repository.unit(book['id'], unit['unit_key']) is None
        assert not coverage(store.book(book['id']), store)['whole_book_discovered']
    else:
        result = process(scanned, store, 'scan')
        assert coverage(result, store)['whole_book_discovered']
        assert repository.unit(book['id'], unit['unit_key'])['result'] != rejected_response()
    assert len(provider.calls) == before + 1
    assert artifacts.get(book['id'], rejected_id)['payload']['result'] == rejected_response()
    rejected = next(e for e in repository.events(book['id']) if e['event'] == 'cache_rejected')
    assert rejected['artifact_id'] == rejected_id


def legacy_earlier_volume(store, current):
    """Pre-upgrade rows have observations/links but no artifact records at all."""
    earlier = parse_book('earlier.txt', b'Mara used a low voice.\n\nElio spoke briskly.')
    series = SeriesRepository(store)
    saga = series.create_series('The Lantern Books')
    series.set_membership(current['id'], saga['id'], 9)
    identities = {}
    for name in ('Mara', 'Elio'):
        identities[name] = series.create_character(saga['id'], name)['id']
        character = next(c for c in current['characters'] if c['name'] == name)
        series.link_character(current['id'], character['id'], identities[name])
        earlier['characters'].append({'id': 'early-' + name.lower(), 'name': name, 'aliases': [],
                                      'description': '', 'direction': ''})
    chapter = earlier['chapters'][0]
    source = chapter['text']
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    with store.connect() as conn:
        conn.execute('INSERT INTO books(id,body) VALUES (?,?)', (earlier['id'], json.dumps(earlier)))
        conn.execute('INSERT INTO series_books(book_id,series_id,position) VALUES (?,?,?)', (earlier['id'], saga['id'], 1))
        for name, quote in (('Mara', 'Mara used a low voice.'), ('Elio', 'Elio spoke briskly.')):
            character_id = 'early-' + name.lower()
            conn.execute('INSERT INTO series_character_links VALUES (?,?,?,?)',
                         (earlier['id'], character_id, identities[name], 'before-upgrade'))
            start = source.index(quote)
            observation = {'id': 'legacy-' + name.lower(), 'book_id': earlier['id'], 'character_id': character_id,
                           'chapter_id': chapter['id'], 'start': start, 'end': start + len(quote),
                           'source_hash': source_hash, 'quote': quote, 'kind': 'profile_evidence',
                           'description': 'An earlier vocal observation.', 'direction': '',
                           'provider': 'anthropic', 'model': 'earlier-model'}
            conn.execute('INSERT INTO character_observations VALUES (?,?,?,?,?,?)',
                         (observation['id'], earlier['id'], character_id, chapter['id'], source_hash, json.dumps(observation)))
    return earlier, identities


def test_legacy_series_inputs_backfill_once_and_export_transitive_source_and_identity_provenance(library, monkeypatch, tmp_path):
    store, book = library
    provider = FakeProvider(monkeypatch)
    scanned = process(book, store, 'scan')
    earlier, identities = legacy_earlier_volume(store, scanned)
    artifacts = ArtifactRepository(store)
    assert artifacts.counts(earlier['id'])['total'] == 0
    backfills = []
    original = ArtifactRepository.backfill

    def backfill(repository, identifier):
        backfills.append(identifier)
        return original(repository, identifier)

    monkeypatch.setattr(ArtifactRepository, 'backfill', backfill)
    process(scanned, store, 'profiles')
    assert backfills == [earlier['id']]
    assert [call['stage'] for call in provider.calls] == ['discovery', 'profiles', 'profiles']
    recipes = artifacts.list(book['id'], kind='analysis_input', stage='profiles')['items']
    assert len(recipes) == 2
    for item in recipes:
        recipe = artifacts.get(book['id'], item['id'])
        assert len(recipe['payload']['prior_observations']) == 1
        dependencies = [artifacts.get(link['book_id'], link['id']) for link in recipe['dependency_links']]
        observations = [d for d in dependencies if d['kind'] == 'character_observation']
        contexts = [d for d in dependencies if d['kind'] == 'series_context']
        assert len(observations) == 1 and observations[0]['book_id'] == earlier['id']
        assert observations[0]['legacy_provenance'] is True
        observation = observations[0]
        assert len(observation['dependencies']) == 1
        source = artifacts.get(earlier['id'], observation['dependencies'][0])
        assert source['kind'] == 'source' and source['payload']['text'] == earlier['chapters'][0]['text']
        assert {d['book_id'] for d in contexts} == {book['id'], earlier['id']}
        for context in contexts:
            assert {link['series_character_id'] for link in context['payload']['links']} == set(identities.values())
    path = tmp_path / 'analysis.zip'
    manifest = write_analysis_export(store, book['id'], path)
    assert manifest['external_book_dependencies'] == [earlier['id']]
    with zipfile.ZipFile(path) as archive:
        exported = [json.loads(line) for line in archive.read('artifacts.jsonl').decode().splitlines()]
    earlier_artifacts = [a for a in exported if a['book_id'] == earlier['id']]
    assert {a['kind'] for a in earlier_artifacts} == {'source', 'character_observation', 'series_context'}
    assert any(a['payload'].get('text') == earlier['chapters'][0]['text'] for a in earlier_artifacts)


def test_profile_records_series_links_that_selected_evidence_even_if_links_change_during_run(library, monkeypatch):
    store, book = library
    provider = FakeProvider(monkeypatch)
    scanned = process(book, store, 'scan')
    earlier, identities = legacy_earlier_volume(store, scanned)
    series = SeriesRepository(store)
    changed = False

    def unlink_during_request(stage, result, prompt):
        nonlocal changed
        if stage == 'profiles' and not changed:
            changed = True
            series.unlink_character(earlier['id'], 'early-elio')
        return result

    provider.transform = unlink_during_request
    process(scanned, store, 'profiles')
    artifacts = ArtifactRepository(store)
    recipes = artifacts.list(book['id'], kind='analysis_input', stage='profiles')['items']
    elio = next(artifacts.get(book['id'], r['id']) for r in recipes
                if artifacts.get(book['id'], r['id'])['payload']['prior_observations'][0]['character_id'] == 'early-elio')
    earlier_context = next(artifacts.get(link['book_id'], link['id']) for link in elio['dependency_links']
                           if link['book_id'] == earlier['id']
                           and artifacts.get(link['book_id'], link['id'])['kind'] == 'series_context')
    assert any(link['series_character_id'] == identities['Elio'] for link in earlier_context['payload']['links'])
    current = artifacts.get(earlier['id'], artifacts.output_head(earlier['id'], 'series_context', 'book'))
    assert all(link['series_character_id'] != identities['Elio'] for link in current['payload']['links'])
