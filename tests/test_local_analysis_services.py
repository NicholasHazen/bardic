"""Self-hosted analysis: the Local LLM provider, BookNLP quote attribution and the Novel Analyzer.

Synthetic prose, fake services and mocked transports only; no network access.
The fake services derive their offsets from the text they receive, as the real
ones do, so these tests exercise the real mapping onto passages.
"""
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from bardic import analysis as a
from bardic import local_services as ls
from bardic.app import create_app
from bardic.pipeline import default_registry
from bardic.processing import ProcessingStore, RequestBudget
from test_analysis_pipeline import MODEL, FakeProvider, latest, run, wait_job

# A non-BMP character before the dialogue checks code-point offsets.
STORY = ('Chapter One\n\nMara lit the lamp by the harbor. 🌊\n\n“Stay close,” Mara whispered softly.\n\n'
         '“Always,” Elio said.\n\n“Then walk.” Mara pointed at the gate.\n\n'
         'Chapter Two\n\nElio opened the gate at dawn.\n\n“We leave now,” Elio said.\n\n“Not yet,” Mara said.\n')
QUOTE = re.compile(r'“[^”]*”')
TAG = re.compile(r'^[\s,.]*(\w+) (said|whispered|pointed)( softly)?')


def attributions(text):
    """(start, end, speaker, tag) for each quotation, read from the text like a service would."""
    for match in QUOTE.finditer(text):
        tag = TAG.match(text[match.end():text.find('\n', match.end()) if '\n' in text[match.end():] else len(text)])
        yield match.start(), match.end(), tag.group(1) if tag else None, tag


class FakeBookNLP:
    def __init__(self):
        self.calls = []
        self.overrides = {}

    def __call__(self, text, aliases):
        names = {}
        quotes = []
        for number, (start, end, speaker, tag) in enumerate(attributions(text), 1):
            speaker = self.overrides.get(text[start:end], speaker)
            speaker_id = names.setdefault(speaker, len(names) + 1)
            verb = tag.group(2) if tag else None
            quotes.append({'id': number, 'text': text[start:end], 'start': start, 'end': end, 'paragraph': 0,
                           'speaker_id': speaker_id, 'speaker': speaker, 'speaker_mention': speaker or '',
                           'tag': {'kind': 'speech' if verb in ('said', 'whispered') else 'beat', 'verb': verb,
                                   'lemma': {'said': 'say', 'whispered': 'whisper', 'pointed': 'point'}[verb],
                                   'adverbs': ['softly'] if tag.group(3) else [], 'with': [],
                                   # Tokenized, like the real service: not an exact source slice.
                                   'text': ' '.join(re.findall(r'\w+', tag.group(0)))} if tag else None,
                           'tag_conflict': False})
        characters = [{'id': i, 'name': n, 'names': [n], 'common_names': [], 'narrator': False, 'mentions': 3, 'quotes': 1,
                       'merged_ids': [i], 'gender': {'from_pronouns': 'female' if n == 'Mara' else 'unknown',
                                                     'pronoun_counts': {'he': 0, 'she': 2}, 'booknlp': None,
                                                     'booknlp_confidence': None}}
                      for n, i in names.items() if n]
        return {'seconds': .1, 'characters': characters, 'quotes': quotes,
                'places': [{'text': 'the harbor', 'cat': 'LOC', 'count': 1}]}


class FakeAnalyzer:
    def __init__(self):
        self.calls = []
        self.corrupt = False
        self.keep = None        # keep only the first N lines (e.g. dialogue it cannot detect)
        self.duplicate = None   # also label the first line as this speaker

    def __call__(self, text, sheet):
        bounds = ls.paragraphs(text)
        names = {c['name'] for c in (sheet or {}).get('characters', [])}
        lines = []
        for number, (start, end, speaker, tag) in enumerate(attributions(text), 1):
            lines.append({'id': f'Q{number}', 'paragraph': ls.paragraph_of(bounds, start) + 1,
                          'text': text[start:end] + ('x' if self.corrupt else ''), 'start': start, 'end': end,
                          'speaker': speaker if speaker in names else None,
                          'narration': tag.group(0).strip(' ,') if tag else '', 'reason': None, 'emotion': 'calm',
                          'intensity': 'low', 'delivery': 'quiet, steady', 'cues': ['leans in'] if tag and tag.group(3) else [],
                          'vocal_events': [], 'instruction': 'Quiet, steady; calm.', 'untagged': not tag})
        if self.keep is not None:
            lines = lines[:self.keep]
        if self.duplicate and lines:
            lines.insert(1, {**lines[0], 'id': 'Q0', 'speaker': self.duplicate})
        scenes = [{'scene': 1, 'first_paragraph': 1, 'last_paragraph': 2, 'setting': 'the harbor', 'speakers': [], 'marked_break': False}]
        if len(bounds) > 3:
            scenes.append({'scene': 2, 'first_paragraph': 3, 'last_paragraph': len(bounds), 'setting': 'unknown',
                           'speakers': [], 'marked_break': False})
        else:
            scenes[0]['last_paragraph'] = len(bounds)
        return {'seconds': 1., 'narrator': None, 'characters': [], 'scenes': scenes, 'lines': lines,
                'descriptions': [], 'script': [{'type': 'narration', 'text': text}], 'llm_calls': []}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for variable in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                     *(service['env'] for service in ls.SERVICES.values())):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])

    def no_network(*_args, **_kwargs):
        pytest.fail('These tests must not make real network requests')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', no_network)
    booknlp, analyzer = FakeBookNLP(), FakeAnalyzer()

    def service(_client, provider, base_url, body, _cancelled):
        fake = {'booknlp': booknlp, 'novel_analyzer': analyzer}[provider]
        assert base_url == {'booknlp': 'http://nlp.local:8100', 'novel_analyzer': 'http://nlp.local:8200'}[provider]
        fake.calls.append(body)
        return fake(body['text'], body.get('aliases') if provider == 'booknlp' else body.get('character_sheet'))

    monkeypatch.setattr('bardic.pipeline.runner._service_call', service)
    monkeypatch.setattr('bardic.local_services.health', lambda *_: {'status': 'ok', 'model': 'fake-service'})
    with TestClient(create_app(tmp_path)) as test_client:
        test_client.provider, test_client.booknlp, test_client.analyzer = FakeProvider(), booknlp, analyzer
        monkeypatch.setattr('bardic.analysis._openai_request', test_client.provider)
        test_client.post('/api/settings', json={'api_keys': {'openai': 'test-openai-secret'}, 'analysis_provider': 'openai',
                                                'analysis_models_by_provider': {'openai': MODEL},
                                                'preprocess_models_by_provider': {'openai': MODEL}})
        yield test_client


def configure(client, **urls):
    response = client.post('/api/settings', json={'local_service_urls': urls})
    assert response.status_code == 200, response.text


def import_story(client):
    response = client.post('/api/books', files={'file': ('story.txt', STORY.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()


def dialogue(client, book_id):
    book = client.get(f'/api/books/{book_id}').json()
    names = {c['id']: c['name'] for c in book['characters']}
    return [(s['text'], names[s['speaker_id']], s['confidence'], s.get('speaker_check'), s['direction'], s.get('evidence'))
            for s in book['segments'] if s['kind'] == 'dialogue']


def with_cast(client):
    book = import_story(client)
    job, _ = run(client, book['id'], ['discovery'])
    assert job['status'] == 'completed', job
    return book


# --- pure mapping ------------------------------------------------------------------------------------------

def test_urls_are_plain_server_roots():
    assert ls.normalize_url(' http://host.local:8100/ ', 'booknlp') == 'http://host.local:8100'
    assert ls.normalize_url('', 'booknlp') == ''
    for bad in ('ftp://host', 'http://host/v1', 'http://user:pw@host', 'http://host?q=1', 'http://host:99999'):
        with pytest.raises(ValueError):
            ls.normalize_url(bad, 'novel_analyzer')


def test_quotes_map_to_exact_passages_and_reject_moved_offsets():
    text = 'A 🌊 wave. “Go,” she said. “No.”'
    go, no = text.index('“Go'), text.index('“No')
    assert go == 10  # code points: the emoji is one character, not two UTF-16 units
    segments = [{'id': 'n1', 'kind': 'narration', 'start': 0, 'end': go},
                {'id': 'd1', 'kind': 'dialogue', 'start': go, 'end': go + 5},
                # The importer splits an over-long quotation into several passages.
                {'id': 'd2a', 'kind': 'dialogue', 'start': no, 'end': no + 2},
                {'id': 'd2b', 'kind': 'dialogue', 'start': no + 2, 'end': no + 5}]
    quotes = [{'start': go, 'end': go + 5, 'text': '“Go,”'}, {'start': no, 'end': no + 5, 'text': '“No.”'}]
    matched, unmatched = ls.match_quotes(text, segments, quotes)
    assert [ids for _, ids in matched] == [['d1'], ['d2a', 'd2b']] and not unmatched
    with pytest.raises(ls.ServiceError):  # UTF-16 offsets would land one character late
        ls.match_quotes(text, segments, [{'start': go + 1, 'end': go + 6, 'text': '“Go,”'}])


def test_tag_evidence_is_an_exact_excerpt_or_nothing():
    paragraph = '“Coward,” she said, but she was laughing, and then, softer, “Show me.”'
    assert ls.locate(paragraph, 'she was laughing then') == 'she was laughing'
    assert ls.locate(paragraph, 'she said') == 'she said'
    assert ls.locate(paragraph, 'he growled') is None
    assert ls.locate(paragraph, 'laughing') is None  # one word is too little to cite


def test_cast_names_skip_ambiguous_aliases_and_mark_the_first_person_narrator():
    book = {'characters': [{'id': 'narrator', 'name': 'Narrator', 'aliases': []},
                           {'id': 'c1', 'name': 'Vance', 'aliases': ['I', 'the Archivist', 'Doc']},
                           {'id': 'c2', 'name': 'Odile', 'aliases': ['Doc']},
                           {'id': 'c3', 'name': 'Odile', 'aliases': []}]}
    index = ls.name_index(book)
    assert ls.resolve(index, 'doc', 'the archivist') == 'c1' and 'odile' not in index
    assert ls.booknlp_aliases(book) == [['Vance', 'the Archivist', 'NARRATOR']]
    sheet, names = ls.character_sheet(book)
    assert sheet['narrator'] == 'Vance' and names == {'Vance': 'c1', 'Odile': 'c2', 'Odile (2)': 'c3'}
    assert sheet['characters'][0]['aliases'] == ['I (narrator)', 'the Archivist']


def test_a_character_named_as_the_narrator_is_the_first_person_speaker():
    # Found live: a model's discovery named the "I" character "Narrator (I)" rather than aliasing it.
    book = {'characters': [{'id': 'narrator', 'name': 'Narrator', 'aliases': []},
                           {'id': 'c1', 'name': 'Narrator (I)', 'aliases': ['Archivist']},
                           {'id': 'c2', 'name': 'Odile', 'aliases': []}]}
    assert ls.first_person(book) == 'c1'
    assert ls.booknlp_aliases(book) == [['Archivist', 'NARRATOR'], ['Odile']]
    assert ls.character_sheet(book)[0]['narrator'] == 'Narrator (I)'
    two = {'characters': [*book['characters'], {'id': 'c3', 'name': 'Me', 'aliases': []}]}
    assert ls.first_person(two) is None  # ambiguous: no one is guessed
    # Also found live, on the next run: a label appended to the character's name.
    labelled = {'characters': [{'id': 'c1', 'name': 'Miss Vance (Narrator)', 'aliases': []}, {'id': 'c2', 'name': 'Odile', 'aliases': []}]}
    assert ls.first_person(labelled) == 'c1'
    assert ls.booknlp_aliases(labelled)[0] == ['Miss Vance (Narrator)', 'Miss Vance', 'NARRATOR']
    assert ls.first_person({'characters': [{'id': 'c1', 'name': 'Ivan (Iceland)', 'aliases': []}]}) is None


def test_the_check_treats_the_narration_voice_as_the_first_person_narrator():
    from bardic.pipeline.steps.quotes import compare
    narrator_line = {'speaker_id': None, 'narrator': True}
    assert compare(narrator_line, 'narrator') == 'agrees' and compare(narrator_line, 'c2') == 'narrator'
    mapped = {'speaker_id': 'c1', 'narrator': True}
    assert compare(mapped, 'narrator') == 'agrees' and compare(mapped, 'c1') == 'agrees' and compare(mapped, 'c2') == 'differs'
    assert compare({'speaker_id': 'c1'}, 'unassigned') == 'suggests' and compare({'speaker_id': None}, 'c1') == 'not_in_cast'


def test_speech_tag_manner_keeps_delivery_words_only():
    from bardic.pipeline.steps.directing import _manner
    assert _manner({'kind': 'speech', 'verb': 'sighed', 'lemma': 'sigh', 'adverbs': ['heavily'], 'with': []}) == 'Sighed heavily.'
    assert _manner({'kind': 'speech', 'verb': 'laughing', 'lemma': 'laugh', 'adverbs': ['then'], 'with': []}) == 'Laughing.'
    assert _manner({'kind': 'speech', 'verb': 'said', 'lemma': 'say', 'adverbs': ['though'], 'with': ['a smile']}) == 'With a smile.'
    assert _manner({'kind': 'speech', 'verb': 'said', 'lemma': 'say', 'adverbs': [], 'with': []}) == ''
    assert _manner({'kind': 'beat', 'verb': 'sniffed', 'lemma': 'sniff', 'adverbs': [], 'with': []}) == ''


# --- transport ---------------------------------------------------------------------------------------------

def test_service_retries_only_work_it_did_not_do():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if len(seen) == 1:
            raise httpx.ConnectError('refused', request=request)
        if len(seen) == 2:
            return httpx.Response(503, json={'detail': 'loading'}, headers={'retry-after': '1'})
        return httpx.Response(200, json={'quotes': []})

    waits = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = ls.analyze(client, 'booknlp', 'http://nlp.local:8100', {'text': 'x'}, lambda: False,
                            wait=lambda seconds, _: waits.append(seconds))
    assert result == {'quotes': []} and seen == ['/v1/analyze'] * 3 and waits == [2, 1.0]

    def timeout(request):
        seen.append('timeout')
        raise httpx.ReadTimeout('slow', request=request)

    seen.clear()
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client, pytest.raises(ls.ServiceError, match='not repeated'):
        ls.analyze(client, 'novel_analyzer', 'http://nlp.local:8200', {'text': 'x'}, lambda: False, wait=lambda *_: None)
    assert seen == ['timeout']
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(413, json={'detail': 'Too long'}))) as client, \
            pytest.raises(ls.ServiceError, match='HTTP 413: Too long'):
        ls.analyze(client, 'novel_analyzer', 'http://nlp.local:8200', {'text': 'x'}, lambda: False, wait=lambda *_: None)


def test_local_llm_uses_responses_without_a_key_and_is_metered_at_zero_cost(tmp_path):
    captured = {}

    def handler(request):
        captured.update(url=str(request.url), auth=request.headers.get('authorization'), body=json.loads(request.content))
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'status': 'completed', 'content': [{'type': 'output_text', 'text': '{"ok": true}'}]}],
            'usage': {'input_tokens': 40, 'output_tokens': 5}})

    from bardic.store import Store
    store = Store(tmp_path)
    budget = RequestBudget(ProcessingStore(store), 'book-1', 'run-1', max_requests=2, budget_usd=.01)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, budget.context('directing', 'unit', 512):
        result = a._local_llm_request(client, 'qwen3.6-35b-a3b', 'http://llm.local:8000', 'Prompt', {'type': 'object'},
                                      lambda: False)
    assert result == {'ok': True}
    assert captured['url'] == 'http://llm.local:8000/v1/responses' and captured['auth'] is None
    assert captured['body']['model'] == 'qwen3.6-35b-a3b' and captured['body']['max_output_tokens'] == 512
    [attempt] = ProcessingStore(store).attempts('book-1')
    assert attempt['provider'] == 'local_llm' and attempt['charged_estimate_usd'] == 0 and attempt['output_tokens'] == 5


# --- settings and definitions ---------------------------------------------------------------------------

def test_steps_list_their_providers_and_services_take_no_model(client):
    configure(client, booknlp='http://nlp.local:8100/')
    body = client.get('/api/analysis-pipeline').json()
    steps = {s['id']: s for s in body['steps']}
    assert steps['quotes']['providers'] == ['booknlp'] and steps['quotes']['settings']['provider'] == 'booknlp'
    assert steps['directing']['providers'][-2:] == ['novel_analyzer', 'booknlp'] and 'local_llm' in steps['profiles']['providers']
    ready = {p['id']: p['has_api_key'] for p in body['providers']}
    assert ready['booknlp'] and not ready['novel_analyzer'] and not ready['local_llm']
    put = lambda step, value: client.put(f'/api/analysis-pipeline/steps/{step}/settings', json=value)
    assert put('directing', {'provider': 'novel_analyzer', 'model': 'x'}).status_code == 400
    assert put('directing', {'provider': 'novel_analyzer'}).json()['model'] is None
    assert put('discovery', {'provider': 'booknlp'}).status_code == 400
    assert put('profiles', {'provider': 'local_llm', 'model': 'qwen3.6-35b-a3b'}).status_code == 200
    assert client.post('/api/settings', json={'local_service_urls': {'booknlp': 'http://host/path'}}).status_code == 400
    assert client.post('/api/settings', json={'local_service_urls': {'breeze': 'http://host'}}).status_code == 400
    assert client.get('/api/status').json()['local_service_urls']['booknlp'] == 'http://nlp.local:8100'


def test_a_run_without_the_server_url_is_refused_before_queueing(client):
    book = with_cast(client)
    response = client.post(f"/api/books/{book['id']}/analysis-pipeline/runs", json={'steps': ['quotes']})
    assert response.status_code == 400 and 'BookNLP server URL' in response.json()['detail']


# --- BookNLP -----------------------------------------------------------------------------------------------

def test_quote_attribution_maps_to_the_cast_writes_nothing_and_is_cached(client):
    configure(client, booknlp='http://nlp.local:8100')
    book = with_cast(client)
    before = client.get(f"/api/books/{book['id']}").json()['segments']
    plan = client.post(f"/api/books/{book['id']}/analysis-pipeline/plan", json={'steps': ['quotes']}).json()
    assert plan['service_calls'] == 2 and plan['requests'] == 0 and plan['estimated_cost_usd'] == 0
    job, _ = run(client, book['id'], ['quotes'])
    assert job['status'] == 'completed', job
    assert sorted(client.booknlp.calls[0]['aliases']) == [['Elio'], ['Mara']]
    assert client.get(f"/api/books/{book['id']}").json()['segments'] == before
    version = latest(client, book['id'], 'quotes')
    detail = client.get(f"/api/books/{book['id']}/analysis-pipeline/steps/quotes/versions/{version['id']}").json()
    quotes = [r for r in detail['rows'] if r['kind'] == 'Quotation']
    assert [r['booknlp'] for r in quotes] == ['Mara', 'Elio', 'Mara', 'Elio', 'Mara']
    assert {r['check'] for r in quotes} == {'Suggests a speaker'}  # nothing attributed yet
    assert detail['stats']['suggests_speaker'] == 5
    assert any(r['kind'] == 'Character' and 'female from pronouns' in r['text'] for r in detail['rows'])
    # The same chapters and cast are reused, not requested again.
    assert client.post(f"/api/books/{book['id']}/analysis-pipeline/plan", json={'steps': ['quotes']}).json()['cached_units'] == 2
    run(client, book['id'], ['quotes'])
    assert len(client.booknlp.calls) == 2


def test_booknlp_check_raises_agreement_and_caps_disagreement(client):
    configure(client, booknlp='http://nlp.local:8100')
    book = with_cast(client)
    client.provider.speaker = 'Mara'  # the model says Mara spoke every line
    run(client, book['id'], ['quotes'])
    job, _ = run(client, book['id'], ['profiles', 'directing'])
    assert job['status'] == 'completed', job
    lines = dialogue(client, book['id'])
    by_text = {text: (speaker, confidence, check) for text, speaker, confidence, check, *_ in lines}
    assert by_text['“Not yet,”'][:2] == ('Mara', .9) and by_text['“Not yet,”'][2]['result'] == 'agrees'
    speaker, confidence, check = by_text['“We leave now,”']
    # BookNLP says Elio: the model's speaker is kept, but only just assigned, and the disagreement is recorded.
    assert (speaker, confidence, check['result']) == ('Mara', .65, 'differs')
    detail = client.get(f"/api/books/{book['id']}/analysis-pipeline/steps/directing/versions/accepted").json()
    assert detail['stats']['booknlp_differs'] == 2 and any('BookNLP: Elio' in r.get('check', '') for r in detail['rows'])
    # A BookNLP candidate compared with the model's accepted speakers: 3 of 5 lines are Mara's in both.
    run(client, book['id'], ['directing'], configs={'directing': {'provider': 'booknlp'}}, gates={'directing': 'review'})
    candidate = latest(client, book['id'], 'directing')
    stats = client.get(f"/api/books/{book['id']}/analysis-pipeline/steps/directing/versions/{candidate['id']}").json()['stats']
    assert stats['dialogue'] == 5 and stats['same_speaker_as_book'] == 3


def test_booknlp_as_speaker_source_uses_tags_and_leaves_conflicts_unassigned(client):
    configure(client, booknlp='http://nlp.local:8100')
    book = with_cast(client)
    missing = client.post(f"/api/books/{book['id']}/analysis-pipeline/runs",
                          json={'steps': ['directing'], 'configs': {'directing': {'provider': 'booknlp'}},
                                'limits': {'max_requests': 25}})
    assert wait_job(client, missing.json()['job']['id'])['status'] == 'failed'
    run(client, book['id'], ['quotes'])
    job, _ = run(client, book['id'], ['directing'], configs={'directing': {'provider': 'booknlp'}})
    assert job['status'] == 'completed', job
    lines = {text: rest for text, *rest in dialogue(client, book['id'])}
    assert lines['“Stay close,”'][:4] == ['Mara', .85, None, 'Whispered softly.']
    assert lines['“Stay close,”'][4] == ['Mara whispered softly']  # exact source, re-anchored from tokens
    assert lines['“Then walk.”'][:2] == ['Mara', .75]  # an action beat
    assert client.provider.calls.count('directing') == 0


# --- Novel Analyzer -----------------------------------------------------------------------------------

def test_analyzer_attributes_directs_and_adds_scene_breaks_from_the_cast_sheet(client):
    configure(client, novel_analyzer='http://nlp.local:8200')
    book = with_cast(client)
    job, _ = run(client, book['id'], ['directing'], configs={'directing': {'provider': 'novel_analyzer'}})
    assert job['status'] == 'completed', job
    sheet = client.analyzer.calls[0]['character_sheet']
    assert sorted(c['name'] for c in sheet['characters']) == ['Elio', 'Mara']
    lines = {text: rest for text, *rest in dialogue(client, book['id'])}
    assert lines['“Always,”'][:4] == ['Elio', .85, None, 'Quiet, steady; calm.']
    assert lines['“Always,”'][4] == ['Elio said']
    after = client.get(f"/api/books/{book['id']}").json()
    chapter = after['chapters'][0]['id']
    scenes = [s for s in after['scenes'] if s['chapter_id'] == chapter]
    assert len(scenes) == 2 and scenes[0]['summary'] == 'Setting (unverified): the harbor.'
    assert all(s['analysis_provider'] == 'novel_analyzer' for s in after['segments'])
    # The script restates the chapter and is not retained.
    unit = json.dumps(client.get(f"/api/books/{book['id']}/artifacts").json())
    assert '"script"' not in unit


def test_analyzer_output_that_moved_the_text_is_rejected_and_retained(client):
    configure(client, novel_analyzer='http://nlp.local:8200')
    book = with_cast(client)
    before = dialogue(client, book['id'])
    client.analyzer.corrupt = True
    job, _ = run(client, book['id'], ['directing'], configs={'directing': {'provider': 'novel_analyzer'}})
    assert job['status'] == 'failed' and 'does not match the chapter text' in job['error']
    assert dialogue(client, book['id']) == before


def test_directing_capture_and_apply_stay_inverse_with_a_speaker_check():
    from bardic.importer import parse_book
    step = default_registry().get('directing')
    book = parse_book('story.txt', STORY.encode())
    segment = next(s for s in book['segments'] if s['kind'] == 'dialogue')
    segment['speaker_check'] = {'source': 'booknlp', 'result': 'agrees', 'speaker_id': None, 'speaker': 'X', 'tag_conflict': False}
    for chapter in book['chapters']:
        payload = step.capture(book, chapter['id'])
        copy = json.loads(json.dumps(book))
        step.apply(copy, {chapter['id']: payload})
        assert copy == book
    other = next(s for s in book['segments'] if s['kind'] == 'dialogue' and s is not segment)
    assert 'speaker_check' not in step.capture(book, other['chapter_id'])['segments'][other['id']]


# --- red-team regressions -------------------------------------------------------------------------------

def test_an_analyzer_result_missing_most_dialogue_is_rejected_not_applied(client):
    # Found in review: dialogue the analyzer cannot detect (single quotes) became unassigned in a "completed" version.
    configure(client, novel_analyzer='http://nlp.local:8200')
    book = with_cast(client)
    run(client, book['id'], ['profiles', 'directing'])
    before = dialogue(client, book['id'])
    client.analyzer.keep = 1
    job, _ = run(client, book['id'], ['directing'], configs={'directing': {'provider': 'novel_analyzer'}})
    assert job['status'] == 'failed' and 'labelled 1 of 3 dialogue passages' in job['error']
    assert dialogue(client, book['id']) == before


def test_two_analyzer_lines_on_one_passage_leave_it_unassigned(client):
    configure(client, novel_analyzer='http://nlp.local:8200')
    book = with_cast(client)
    client.analyzer.duplicate = 'Elio'  # the first line (Mara's) is also labelled as Elio's
    job, _ = run(client, book['id'], ['directing'], configs={'directing': {'provider': 'novel_analyzer'}})
    assert job['status'] == 'completed', job
    lines = {text: rest for text, *rest in dialogue(client, book['id'])}
    assert lines['“Stay close,”'][:2] == ['Unassigned dialogue', 0.0] and lines['“Stay close,”'][4] == []


def test_an_analyzer_version_does_not_carry_another_providers_scene_notes(client):
    configure(client, novel_analyzer='http://nlp.local:8200')
    book = with_cast(client)
    run(client, book['id'], ['profiles', 'directing'])
    assert {s['tone'] for s in client.get(f"/api/books/{book['id']}").json()['scenes']} == {'Calm'}
    run(client, book['id'], ['directing'], configs={'directing': {'provider': 'novel_analyzer'}})
    assert {(s['tone'], s['direction']) for s in client.get(f"/api/books/{book['id']}").json()['scenes']} == {('', '')}


def test_booknlp_on_directing_needs_no_url_and_skips_chapters_without_dialogue(client):
    configure(client, booknlp='http://nlp.local:8100')
    story = STORY + '\nChapter Three\n\nThe sea was quiet all night. Nobody spoke.\n'
    book = client.post('/api/books', files={'file': ('story.txt', story.encode(), 'text/plain')}).json()
    run(client, book['id'], ['discovery'])
    run(client, book['id'], ['quotes'])
    configure(client, booknlp='')  # the step reads accepted results and sends nothing
    job, _ = run(client, book['id'], ['directing'], configs={'directing': {'provider': 'booknlp'}})
    assert job['status'] == 'completed', job
    assert latest(client, book['id'], 'directing')['scope_count'] == 3


def test_a_manual_speaker_edit_drops_the_stale_booknlp_check(client):
    configure(client, booknlp='http://nlp.local:8100')
    book = with_cast(client)
    run(client, book['id'], ['quotes'])
    run(client, book['id'], ['profiles', 'directing'])
    current = client.get(f"/api/books/{book['id']}").json()
    elio = next(c['id'] for c in current['characters'] if c['name'] == 'Elio')
    disputed = next(s for s in current['segments'] if (s.get('speaker_check') or {}).get('result') == 'differs')
    edited = client.patch(f"/api/books/{book['id']}/segments/{disputed['id']}", json={'speaker_id': elio}).json()
    segment = next(s for s in edited['segments'] if s['id'] == disputed['id'])
    assert segment['speaker_id'] == elio and 'speaker_check' not in segment


def test_environment_urls_are_used_but_never_saved_and_a_cleared_url_stays_cleared(tmp_path, monkeypatch):
    monkeypatch.setenv('BARDIC_BOOKNLP_URL', 'http://gpu.local:8100')
    monkeypatch.setattr('bardic.app.list_system_voices', lambda: [])
    with TestClient(create_app(tmp_path)) as test_client:
        assert test_client.get('/api/status').json()['local_service_urls']['booknlp'] == 'http://gpu.local:8100'
        test_client.post('/api/settings', json={'tts_model': test_client.get('/api/status').json()['tts_model']})
        test_client.app.state.runtime.store.settings()
        assert 'booknlp' not in test_client.app.state.runtime.store.settings().get('local_service_urls', {})
        test_client.post('/api/settings', json={'local_service_urls': {'booknlp': ''}})
    with TestClient(create_app(tmp_path)) as test_client:
        assert test_client.get('/api/status').json()['local_service_urls']['booknlp'] == ''


def test_service_transport_bounds_retries_size_and_cancellation():
    seen = []

    def failing(request):
        seen.append(1)
        return httpx.Response(502, json={'detail': 'LLM failed'})

    with httpx.Client(transport=httpx.MockTransport(failing)) as client, pytest.raises(ls.ServiceError, match='HTTP 502'):
        ls.analyze(client, 'novel_analyzer', 'http://nlp.local:8200', {'text': 'x'}, lambda: False, wait=lambda *_: None)
    assert len(seen) == 2  # a 502 did work: one retry only

    seen.clear()
    with httpx.Client(transport=httpx.MockTransport(failing)) as client, pytest.raises(ls.ServiceError, match='Nothing was sent'):
        ls.analyze(client, 'novel_analyzer', 'http://nlp.local:8200', {'text': 'x' * 250_001}, lambda: False)
    assert not seen

    def refused(request):
        seen.append(1)
        raise httpx.ConnectError('refused', request=request)

    with httpx.Client(transport=httpx.MockTransport(refused)) as client, pytest.raises(ls.ServiceError, match='could not be reached'):
        ls.analyze(client, 'booknlp', 'http://nlp.local:8100', {'text': 'x'}, lambda: False, wait=lambda *_: None)
    assert len(seen) == 3

    # Another run holds the host (both services on one GPU): a cancel while waiting stops without sending.
    lock = ls.host_lock('http://NLP.local:9999')
    lock.acquire()
    try:
        seen.clear()
        with httpx.Client(transport=httpx.MockTransport(failing)) as client, pytest.raises(InterruptedError):
            ls.analyze(client, 'booknlp', 'http://nlp.local:8100', {'text': 'x'}, lambda: True)
        assert not seen
    finally:
        lock.release()
    assert ls.timeout_for('novel_analyzer', 'x' * 60_000) > ls.timeout_for('novel_analyzer', 'x')


def test_directing_keeps_reusing_cached_model_requests_from_version_one():
    from bardic.pipeline.contract import LLMRequest, Unit
    from bardic.pipeline.runner import unit_identity
    step = default_registry().get('directing')
    recipe, _ = unit_identity(step, Unit('k', 'c1', 'l', request=LLMRequest('p', {}, 10)), 'openai', 'm')
    assert step.version == 2 and recipe['step_version'] == 1


def test_a_cached_service_result_from_another_server_model_is_not_reused(client, monkeypatch):
    configure(client, booknlp='http://nlp.local:8100')
    book = with_cast(client)
    run(client, book['id'], ['quotes'])
    run(client, book['id'], ['quotes'])
    assert len(client.booknlp.calls) == 2  # cached on the second run
    monkeypatch.setattr('bardic.local_services.health', lambda *_: {'status': 'ok', 'model': 'upgraded-service'})
    run(client, book['id'], ['quotes'])
    assert len(client.booknlp.calls) == 4
