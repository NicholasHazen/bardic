"""Status, settings, provider reads that need no key, diagnostics and the voice library's local reads."""
from __future__ import annotations

import pytest

SECRET = 'conformance-fake-key-7f3a91c2e8b44d05-not-a-real-credential'


def _status(api):
    return api.call('getStatus').json


def _key_loaded(status, provider):
    return next(p for p in status['analysis_providers'] if p['id'] == provider)['has_api_key']


@pytest.fixture()
def restore_settings(api):
    """Put back the preferences a test changes (they are saved in the library). Runtime keys are the test's own."""
    before = _status(api)
    yield before
    keep = {name: before[name] for name in ('tts_model', 'analysis_provider', 'analysis_models_by_provider',
                                            'preprocess_models_by_provider')}
    limits = {model: {name: value for name, value in limit.items() if name in ('rpm', 'tpm', 'rpd')}
              for model, limit in before['tts_limits'].items()}
    api.call('updateSettings', json={**keep, 'tts_limits': limits, 'listen_chunking': before['listen_chunking']})


# ---------------------------------------------------------------- status

def test_status_is_self_consistent(api):
    status = _status(api)
    assert status['tts_model'] in status['tts_models']
    assert set(status['tts_limits']) == set(status['tts_models']), 'one limits entry for each speech model'
    assert set(status['tts_rate']) <= set(status['tts_models'])
    assert [p['id'] for p in status['providers']] == ['system', 'gemini', 'breeze']
    assert [p['id'] for p in status['analysis_providers']] == ['local', 'gemini', 'openai', 'anthropic']
    local = status['analysis_providers'][0]
    assert local['available'] is True and local['has_api_key'] is False, '`local` needs no key and never has one'
    for provider in status['analysis_providers'][1:]:
        assert provider['available'] == provider['has_api_key'], 'a cloud provider is available exactly when its key is loaded'
    assert 'timing_kind' not in status, 'the removed field stays gone'
    assert set(status['analysis_models_by_provider']) == {'gemini', 'openai', 'anthropic'}


def test_status_is_read_only_and_repeatable(api):
    assert _status(api) == _status(api)


# ---------------------------------------------------------------- settings

def test_settings_update_is_partial_and_idempotent(api, restore_settings):
    before = restore_settings
    same = api.call('updateSettings', json={}).json
    assert same == before, 'an empty update changes nothing and returns the status'
    changed = api.call('updateSettings', json={'analysis_models_by_provider': {'gemini': 'conformance-model-1'}}).json
    assert changed['analysis_models_by_provider']['gemini'] == 'conformance-model-1'
    assert changed['analysis_models_by_provider']['openai'] == before['analysis_models_by_provider']['openai'], (
        'a provider not sent keeps its model')
    again = api.call('updateSettings', json={'analysis_models_by_provider': {'gemini': 'conformance-model-1'}}).json
    assert again == changed
    assert _status(api) == changed, 'POST /api/settings returns the same object as GET /api/status'


def test_speech_limits_replace_only_the_limits_sent(api, restore_settings):
    before = restore_settings
    model = before['tts_model']
    changed = api.call('updateSettings', json={'tts_limits': {model: {'rpm': 7}}}).json
    assert changed['tts_limits'][model]['rpm'] == 7
    assert changed['tts_limits'][model]['tpm'] == before['tts_limits'][model]['tpm']
    assert changed['tts_limits'][model]['rpd'] == before['tts_limits'][model]['rpd']


def test_chunking_options_merge_over_saved_values(api, restore_settings):
    before = restore_settings
    changed = api.call('updateSettings', json={'listen_chunking': {'concurrency': 3}}).json
    assert changed['listen_chunking']['concurrency'] == 3
    assert changed['listen_chunking']['target_seconds'] == before['listen_chunking']['target_seconds']


def test_a_rejected_settings_request_changes_nothing(api, restore_settings):
    """The whole request is checked before anything is saved: one bad field voids the good one beside it."""
    before = restore_settings
    for bad in ({'breeze_url': 'ftp://not-http.example'},
                {'analysis_provider': 'no-such-provider'},
                {'tts_model': 'no-such-speech-model'},
                {'analysis_models_by_provider': {'gemini': 'bad model id!'}}):
        reply = api.call('updateSettings', json={'preprocess_models_by_provider': {'gemini': 'conformance-model-2'}, **bad},
                         expect=400)
        assert reply.code in ('breeze_url_invalid', 'analysis_provider_unknown', 'tts_model_unsupported', 'model_id_invalid')
        assert _status(api) == before, bad


@pytest.mark.parametrize('payload, code', [
    ({'analysis_provider': 'no-such-provider'}, 'analysis_provider_unknown'),
    ({'tts_model': 'no-such-speech-model'}, 'tts_model_unsupported'),
    ({'breeze_url': 'gopher://x'}, 'breeze_url_invalid'),
    ({'api_keys': {'no-such-provider': 'x'}}, 'cloud_provider_unknown'),
    ({'analysis_models_by_provider': {'no-such-provider': 'model-a'}}, 'cloud_provider_unknown'),
    ({'analysis_models_by_provider': {'gemini': ''}}, 'model_id_invalid'),
    ({'local_service_urls': {'no-such-service': 'http://127.0.0.1:1'}}, 'local_service_unknown'),
    ({'local_service_urls': {'booknlp': 'not a url'}}, 'service_url_invalid'),
])
def test_settings_validation_codes(api, restore_settings, payload, code):
    assert api.call('updateSettings', json=payload, expect=400).code == code


def test_speech_limits_out_of_range_are_422(api):
    model = _status(api)['tts_model']
    api.call('updateSettings', json={'tts_limits': {model: {'rpm': 0}}}, negative=True, expect=422)
    api.call('updateSettings', json={'tts_limits': {model: {'rpm': 'ten'}}}, negative=True, expect=422)
    api.call('updateSettings', json={'tts_limits': {model: {'no_such_limit': 5}}}, negative=True, expect=422)


def test_api_keys_are_kept_in_memory_and_never_returned(api, restore_settings):
    unloaded = [p for p in ('anthropic', 'openai', 'gemini') if not _key_loaded(restore_settings, p)]
    if not unloaded:
        pytest.skip('every cloud provider already has a key on this server; the test would overwrite one')
    provider = unloaded[0]
    try:
        reply = api.call('updateSettings', json={'api_keys': {provider: SECRET}})
        assert SECRET not in reply.text, 'a key value is never returned'
        assert _key_loaded(reply.json, provider) is True
        status = api.call('getStatus')
        assert SECRET not in status.text and _key_loaded(status.json, provider) is True
        for operation in ('getLibrary', 'getVoiceLibrary', 'listDiagnostics', 'listJobs'):
            assert SECRET not in api.call(operation).text
    finally:
        cleared = api.call('updateSettings', json={'api_keys': {provider: ''}}).json
    assert _key_loaded(cleared, provider) is False, 'an empty key string clears the runtime key'


def test_analysis_pipeline_step_settings_are_validated_and_saved(api):
    steps = api.call('getAnalysisPipeline').json['steps']
    plain = next(step for step in steps if step['method'] == 'plain')
    saved = api.call('saveAnalysisPipelineStepSettings', path={'step_id': plain['id']},
                     json={'provider': 'local', 'gate': 'auto'}).json
    assert saved['provider'] == 'local' and saved['model'] is None and saved['gate'] == 'auto' and saved['saved'] is True
    after = next(step for step in api.call('getAnalysisPipeline').json['steps'] if step['id'] == plain['id'])
    assert after['settings'] == saved
    assert api.call('saveAnalysisPipelineStepSettings', path={'step_id': plain['id']},
                    json={'provider': 'gemini', 'model': 'some-model'}, expect=400).code == 'step_config_invalid', (
        'a local step accepts only provider local')
    missing = api.call('saveAnalysisPipelineStepSettings', path={'step_id': 'no-such-step'}, json={'provider': 'local'},
                       expect=404)
    assert missing.code == 'step_not_found'


def test_pipeline_definitions_list_steps_and_providers(api):
    definition = api.call('getAnalysisPipeline').json
    ids = [step['id'] for step in definition['steps']]
    assert len(ids) == len(set(ids)) and ids, 'step IDs are unique'
    provider_ids = {provider['id'] for provider in definition['providers']}
    assert 'local_llm' in provider_ids
    for step in definition['steps']:
        assert step['settings']['provider'] in step['providers']
        for name in step['requires'] + step['inputs']:
            assert name in ids, f'{step["id"]} refers to unknown step {name}'
        if step['method'] == 'plain':
            assert step['providers'] == ['local'], 'a plain step runs locally'
    assert any(step['method'] == 'plain' for step in definition['steps']), 'there are free local steps'
    for provider in definition['providers']:
        if provider['kind'] == 'service':
            assert provider['self_hosted'] is True


# ---------------------------------------------------------------- provider reads that need no key

def test_provider_checks_without_a_key_report_missing_key_and_send_nothing(api):
    status = _status(api)
    if any(_key_loaded(status, provider) for provider in ('gemini', 'openai', 'anthropic')):
        pytest.skip('a provider key is loaded on this server; the check would send a billed request')
    for provider in ('gemini', 'openai', 'anthropic'):
        check = api.call('checkProviderAccount', path={'provider': provider})
        assert check.json['state'] == 'missing_key' and check.json['provider'] == provider
        assert 'balance' not in check.json and check.json['balance_note'], 'a balance is never available through an inference key'
        catalog = api.call('refreshProviderModels', path={'provider': provider})
        assert catalog.json['state'] == 'missing_key' and catalog.json['models'], 'the curated list stays usable'


def test_unknown_cloud_provider_is_400(api):
    assert api.call('checkProviderAccount', path={'provider': 'no-such-provider'}, expect=400).code == 'cloud_provider_unknown'
    assert api.call('refreshProviderModels', path={'provider': 'no-such-provider'}, expect=400).code == 'cloud_provider_unknown'


def test_provider_checks_that_need_a_url_or_key_say_so(api, restore_settings):
    status = restore_settings
    if not status['breeze_url']:
        assert api.call('refreshBreeze', expect=400).code == 'breeze_url_missing'
    if not _key_loaded(status, 'gemini'):
        assert api.call('refreshGeminiVoices', expect=400).code == 'gemini_key_missing'


# ---------------------------------------------------------------- diagnostics

def test_diagnostics_are_recorded_listed_and_coalesced(api, txt_book):
    event = {'event': 'playback_waiting', 'book_id': txt_book['id'], 'playback_rate': 1.25}
    first = api.call('recordDiagnostic', json=event).json
    assert first['recorded'] is True and first['id']
    duplicate = api.call('recordDiagnostic', json=event).json
    assert duplicate['recorded'] is False and duplicate['reason'] == 'duplicate' and duplicate['id'] == first['id']
    listed = api.call('listDiagnostics', query={'book_id': txt_book['id']}).json
    assert listed['retention_limit'] == 5000
    ours = [item for item in listed['events'] if item['id'] == first['id']]
    assert len(ours) == 1 and ours[0]['source'] == 'client' and ours[0]['playback_rate'] == 1.25
    assert ours[0]['book_id'] == txt_book['id'] and ours[0]['event'] == 'playback_waiting'
    assert all(item['book_id'] == txt_book['id'] for item in listed['events']), 'the book filter applies'


def test_diagnostics_list_is_newest_first_and_limited(api, txt_book):
    api.call('recordDiagnostic', json={'event': 'buffer_failed', 'book_id': txt_book['id'], 'http_status': 502})
    api.call('recordDiagnostic', json={'event': 'cache_read_failed', 'book_id': txt_book['id']})
    events = api.call('listDiagnostics', query={'book_id': txt_book['id'], 'limit': 2}).json['events']
    assert len(events) == 2 and events[0]['created_at'] >= events[1]['created_at'], 'newest first'


def test_diagnostics_reject_malformed_book_ids(api):
    reply = api.call('listDiagnostics', query={'book_id': 'not a uuid'}, expect=400)
    assert reply.code == 'book_id_invalid'


def test_diagnostics_never_echo_rejected_input(api):
    reply = api.call('recordDiagnostic', json={'event': 'buffer_failed', 'message': SECRET}, negative=True, expect=422)
    assert SECRET not in reply.text
    reply = api.call('recordDiagnostic', json={'event': SECRET}, negative=True, expect=422)
    assert SECRET not in reply.text


# ---------------------------------------------------------------- voice library (local reads)

def test_voice_library_reports_providers_without_contacting_them(api):
    library = api.call('getVoiceLibrary').json
    assert set(library['providers']) == {'breeze', 'gemini'}
    assert library['providers']['breeze']['has_api_key'] is False or library['providers']['breeze']['configured']
    assert library['builtin']['gemini'], 'the built-in Gemini voices are listed'
    assert isinstance(library['voices'], list) and isinstance(library['drafts'], list)


def test_voice_draft_lifecycle_without_a_provider(api):
    draft = api.call('createVoiceDraft', json={'provider': 'gemini', 'name': '  Conformance voice  ',
                                               'description': 'A calm, low voice.'}).json
    assert draft['status'] == 'open' and draft['provider'] == 'gemini' and draft['candidates'] == []
    assert draft['name'] == 'Conformance voice', 'values are trimmed'
    path = {'draft_id': draft['id']}
    edited = api.call('updateVoiceDraft', path=path, json={'description': ' A warm voice. '}).json
    assert edited['description'] == 'A warm voice.' and edited['name'] == 'Conformance voice', 'omitted fields are unchanged'
    assert draft['id'] in [d['id'] for d in api.call('getVoiceLibrary').json['drafts']]
    assert api.call('updateVoiceDraft', path={'draft_id': 'vd_nosuchdraft'}, json={}, expect=404).code == 'voice_draft_not_found'


def test_abandoning_a_draft_without_candidates_needs_no_provider(api):
    draft = api.call('createVoiceDraft', json={'provider': 'gemini', 'name': 'To abandon'}).json
    path = {'draft_id': draft['id']}
    abandoned = api.call('abandonVoiceDraft', path=path).json
    assert abandoned['id'] == draft['id'] and abandoned['status'] == 'abandoned' and abandoned['candidates'] == []
    assert api.call('updateVoiceDraft', path=path, json={'name': 'Too late'}, expect=409).code == 'draft_finished'
    assert api.call('abandonVoiceDraft', path=path, expect=409).code == 'draft_finished'
    assert api.call('abandonVoiceDraft', path={'draft_id': 'vd_nosuchdraft'}, expect=404).code == 'voice_draft_not_found'


def test_voice_draft_validation(api, txt_book):
    api.call('createVoiceDraft', json={'provider': 'gemini', 'book_id': 'no-such-book', 'character_id': 'narrator'}, expect=400)
    reply = api.call('createVoiceDraft', json={'provider': 'gemini', 'description': 'x' * 1000, 'name': 'n'})
    assert reply.json['status'] == 'open'
    api.call('createVoiceDraft', json={'provider': 'gemini', 'description': 'x' * 1001}, negative=True, expect=422)


def test_generating_voice_candidates_needs_a_key_and_consent_to_cost(api):
    status = _status(api)
    if _key_loaded(status, 'gemini'):
        pytest.skip('a Gemini key is loaded; generation would be billed')
    draft = api.call('createVoiceDraft', json={'provider': 'gemini', 'name': 'Needs a key', 'description': 'A brisk voice.'}).json
    reply = api.call('generateVoiceDraftCandidates', path={'draft_id': draft['id']}, json={'confirm_cost': False}, expect=400)
    assert reply.code in ('cost_not_confirmed', 'gemini_key_missing', 'description_too_short', 'book_id_required',
                          'sample_text_missing')
