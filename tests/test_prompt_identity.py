"""Request builders are byte-stable: moving code must not change prompts or cache keys.

The golden digests below were recorded from the phase engine's builders
(``bardic/progressive.py``) before they moved to ``bardic/pipeline/prompts.py``
(Classic removal, stage 1). A changed digest means a changed prompt, schema,
output cap or source locator, and so a changed cache key for every saved unit.
Update a digest only for a deliberate request change, with the step's
``request_version`` raised where cached results must not be reused.
"""
from copy import deepcopy

from bardic.analysis_common import fingerprint
from bardic.importer import parse_book
from bardic.pipeline import prompts
from bardic.pipeline.contract import StepContext
from bardic.pipeline.runner import unit_identity
from bardic.pipeline.steps.directing import DirectingStep
from bardic.pipeline.steps.discovery import DiscoveryStep
from bardic.pipeline.steps.profiles import ProfilesStep
from bardic.processing import digest
from bardic.store import Store

BOOK_ID = 'prompt-identity-book'
# Recorded before the move (commit 7b1f5b0). See the module docstring before editing.
GOLDEN = {
    'discovery_specs': 'dea8642d80c954d99e30728e836e060f9185ac9bf08f5465372d9fb8b9c2996b',
    'discovery_specs_resumed': 'f7b9d357b399cd0b13c602b5caa5307f914011f41276a9c5ac80e9b796726bc9',
    'profile_specs': '53ebab4e8048940a2b692661f612de16ca1a54251388a8569c8e2bbc96a181e0',
    'direction_specs': 'fba8a641e4ffd4d91cb5f61c7316a0a4f96a83f88910ec2094d1e46957e7a13f',
    'pipeline_unit_keys': '8bb42234bfb7f45b32728449d7da6a0ec90d88dba0223c28e67ff0f72049b581',
}


def fixture_book():
    """Original synthetic prose with stable IDs (the importer's IDs are random)."""
    text = '\n\n'.join([
        'Chapter 1',
        'The lamplighter Mara Venn climbed the tower before the fog came in.',
        '“Keep the wick short,” Mara said. “Salt air eats a long flame.”',
        '“I always do,” Tomas answered, and set down the oil tin.',
        'Chapter 2',
        'By noon the harbour bells had gone quiet.',
        '“Did you hear that?” Tomas asked.',
        '“Only the gulls,” said the Warden, who never raised her voice.',
        'Mara laughed. “The Warden hears everything, Tomas.”',
        'Chapter 3',
        'Night returned with rain.',
        '“Light it,” the Warden said.',
        # A long section: more than one discovery range and more than one directing batch.
        'Chapter 4',
        *['The tide went out and the tower hummed. ' * 60 for _ in range(14)],
        *[f'“Mark {n},” Tomas called.' for n in range(35)],
    ])
    book = parse_book('lamplighter.txt', text.encode())
    names = {}
    for kind, items in (('chapter', book['chapters']), ('scene', book['scenes']), ('segment', book['segments'])):
        for index, item in enumerate(items, 1):
            names[item['id']] = f'{kind}-{index}'
            item['id'] = names[item['id']]
    for item in [*book['scenes'], *book['segments']]:
        for field in ('chapter_id', 'scene_id'):
            if field in item:
                item[field] = names[item[field]]
    for scene in book['scenes']:
        scene['segment_ids'] = [names[i] for i in scene.get('segment_ids', [])]
    book['id'] = BOOK_ID
    book['created_at'] = '2026-01-01T00:00:00+00:00'
    book['characters'] += [
        {'id': 'mara', 'name': 'Mara Venn', 'aliases': ['Mara'], 'description': 'A practical lamplighter.',
         'direction': 'Dry, unhurried.', 'evidence': []},
        {'id': 'tomas', 'name': 'Tomas', 'aliases': [], 'description': 'Her apprentice.',
         'direction': 'Eager.', 'evidence': []},
        {'id': 'warden', 'name': 'the Warden', 'aliases': ['Warden'], 'description': 'A reviewed profile.',
         'direction': 'Level and quiet.', 'evidence': [], 'edited': True},
    ]
    # A reviewed speaker choice appears in the direction prompt's REVIEWED ASSIGNMENTS.
    dialogue = [s for s in book['segments'] if s['kind'] == 'dialogue']
    dialogue[0].update(speaker_id='mara', edited=True)
    return book


def accepted_units(book):
    """Validated discovery results in the phase engine's unit shape."""
    units = []
    quotes = {'Mara Venn': 'Keep the wick short', 'Tomas': 'I always do', 'the Warden': 'Only the gulls'}
    for spec in prompts.discovery_specs(book, book['chapters']):
        found = [{'name': name, 'aliases': [], 'description': f'{name} as seen here.', 'direction': 'Plain.',
                  'evidence': [quote]} for name, quote in quotes.items() if quote in spec['source']]
        units.append({'stage': 'discovery', 'chapter_id': spec['chapter_id'], 'start': spec['start'], 'end': spec['end'],
                      'unit_key': 'discovery:' + digest([spec['chapter_id'], spec['start'], spec['end']]),
                      'result': {'characters': found}})
    return units


def discovery_payloads(units):
    """The accepted Discovery step payloads that the Profiles step reads."""
    payloads = {}
    for unit in units:
        payload = payloads.setdefault(unit['chapter_id'], {'ranges': [], 'candidates': []})
        payload['ranges'].append({'start': unit['start'], 'end': unit['end'], 'unit': unit['unit_key']})
        payload['candidates'] += [{**item, 'range_start': unit['start']} for item in unit['result']['characters']]
    return payloads


def test_request_builders_and_pipeline_cache_keys_are_unchanged(tmp_path):
    book = fixture_book()
    store = Store(tmp_path)
    store.save_book(deepcopy(book))
    units = accepted_units(book)
    assert any(u['result']['characters'] for u in units)

    discovery = prompts.discovery_specs(book, book['chapters'])
    # Saved ranges from earlier work are replayed as they were, and the rest re-split around them.
    long_chapter = book['chapters'][3]['id']
    resumed = prompts.discovery_specs(book, book['chapters'], [{'chapter_id': long_chapter, 'start': 5000, 'end': 9000}])
    profiles = prompts.profile_specs(deepcopy(book), store, units)
    direction = prompts.direction_specs(deepcopy(book), book['chapters'])
    assert discovery and profiles and direction
    assert sum(s['chapter_id'] == long_chapter for s in discovery) > 1
    assert len([s for s in direction if s['chapter_id'] == long_chapter]) > 1
    assert {s['character_id'] for s in profiles} == {'mara', 'tomas'}, 'A reviewed character is not re-profiled'

    keys = []
    for step, inputs in ((DiscoveryStep(), {}), (ProfilesStep(), {'discovery': discovery_payloads(units)}),
                         (DirectingStep(), {})):
        ctx = StepContext(book=deepcopy(book), store=store, inputs=inputs, input_heads={}, provider='openai', model='gpt-test')
        for unit in step.units(ctx):
            keys.append([step.id, unit.key, unit_identity(step, unit, 'openai', 'gpt-test')[1],
                         unit_identity(step, unit, 'gemini', 'gemini-test')[1]])

    assert {k[0] for k in keys} == {'discovery', 'profiles', 'directing'}
    actual = {'discovery_specs': digest(discovery),
              'discovery_specs_resumed': digest(resumed),
              'profile_specs': digest(profiles),
              'direction_specs': digest(direction),
              'pipeline_unit_keys': digest(keys)}
    assert actual == GOLDEN


def test_checkpoint_fingerprint_is_unchanged_by_the_move():
    """Structure repair re-keys saved checkpoints with this value; it moved from staged_analysis."""
    book = fixture_book()
    assert fingerprint(book, 'openai', 'gpt-test') == '57ac161f939534f3ce78c596cb9768fa7708dac57b5fa5d8dc761d5e604ada05'
    assert fingerprint(book, 'gemini', None) == 'c46a4126d80960c3b7f73883256b9e0e192c83b0e302ef9a3fc6bbf9892a72ad'
