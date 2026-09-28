"""Progressive work uses bounded fake responses, never provider accounts."""
from copy import deepcopy
import json
import re

import pytest

from bardic import analysis as a
from bardic.importer import parse_book
from bardic.legacy_phase import LegacyProcessingStore as ProcessingStore, coverage
from bardic.processing import source_hash
from bardic.progressive import discoveries, plan, profile_specs, run
from bardic.series import SeriesRepository
from bardic.store import Store


def json_after(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]


def story(chapters=2):
    return parse_book('story.txt', '\n\n'.join(
        f'Chapter {number}\n\nMara spoke quietly on day {number}.\n\n“Stay,” Mara said.\n\n'
        + ('“Perhaps,” Elio said.' if number == chapters else '')
        for number in range(1,chapters+1)).encode())


class FakeProvider:
    def __init__(self, monkeypatch):
        self.calls = []
        self.transform = lambda stage,result,prompt: result
        monkeypatch.setattr(a, '_openai_request', self.request)
        monkeypatch.setattr(a, '_anthropic_request', self.request)

    def request(self, client, model, key, prompt, schema, cancelled):
        if 'BOOK EXCERPT:\n' in prompt:
            stage = 'discovery'
            excerpt = prompt.split('BOOK EXCERPT:\n',1)[1]
            characters = []
            for name in ('Mara','Elio'):
                if name in excerpt:
                    quote = next(iter(re.findall(rf'{name} spoke quietly on day \d+\.', excerpt)), f'{name} said')
                    characters.append({'name':name,'aliases':[],'description':f'{name} draft vocal profile.',
                                       'direction':'Read naturally.','evidence':[quote]})
            result = {'characters':characters}
        elif 'CANDIDATES:\n' in prompt:
            stage = 'profiles'
            candidate = deepcopy(json_after(prompt,'CANDIDATES:\n')[0])
            candidate.update(description=f"Refined {candidate['name']} profile with explicit uncertainty.",
                             direction='A composed voice; temporary feelings follow scene notes.',evidence=candidate['evidence'][:8])
            result = {'characters':[candidate]}
        else:
            stage = 'directing'
            passages = json_after(prompt,'PASSAGES:\n')
            cast = json_after(prompt,'CAST:\n')
            mara = next(c for c in cast if c['name'] == 'Mara')
            result = {'summary':'A restrained meeting.','tone':'Reflective','direction':'Keep a measured pace.',
                      'scene_starts':[], 'segments':[
                        {'id':p['id'],'speaker_id':mara['id'] if p['kind']=='dialogue' else 'narrator',
                         'confidence':.9,'direction':'Quiet resolve.','cues':[],
                         'evidence':['Mara said'] if p['kind']=='dialogue' else []} for p in passages]}
        self.calls.append({'stage':stage,'model':model,'key':key,'prompt':prompt})
        return self.transform(stage,result,prompt)


@pytest.fixture
def setup(tmp_path,monkeypatch):
    book = story()
    store = Store(tmp_path)
    store.save_book(book)
    provider = FakeProvider(monkeypatch)
    return book,store,provider


def process(book,store,phase='scan',**kwargs):
    return run(book,kwargs.pop('provider','openai'),'fake-key',kwargs.pop('model','gpt-6-sol'),
               kwargs.pop('progress',lambda *_:None),kwargs.pop('cancelled',lambda:False),
               store=store,phase=phase,scan_model=kwargs.pop('scan_model','gpt-6-luna'),**kwargs)


def assert_same_source(before,after):
    assert [(c['id'],c['text']) for c in after['chapters']] == [(c['id'],c['text']) for c in before['chapters']]
    assert [(s['id'],s['chapter_id'],s['start'],s['end'],s['text']) for s in after['segments']] == [(s['id'],s['chapter_id'],s['start'],s['end'],s['text']) for s in before['segments']]


def test_scan_uses_only_fast_model_and_keeps_profiles_provisional_until_full_coverage(setup):
    book,store,provider = setup
    first = process(book,store,chapter_id=book['chapters'][0]['id'])
    assert [c['stage'] for c in provider.calls] == ['discovery']
    assert [c['model'] for c in provider.calls] == ['gpt-6-luna']
    assert coverage(first,store)['semantic_chapters_complete'] == 1
    assert first['analysis']['profiles_provisional']
    assert all(c['profile_provisional'] for c in first['characters'] if c['id'] not in {'narrator','unassigned'})
    assert all(not c.get('profile_refined') for c in first['characters'])
    complete = process(first,store)
    assert len(provider.calls) == 2
    assert coverage(complete,store)['whole_book_discovered']
    assert complete['analysis']['profiles_provisional'], 'Whole discovery alone does not make unrefined profiles current'
    assert_same_source(book,complete)


def test_discovery_references_record_actual_fast_model_provenance(setup):
    book,store,provider = setup
    complete = process(book,store)
    refs = [r for r in store.character_references(book['id']) if r['kind']=='profile_evidence']
    assert refs and {r['model'] for r in refs} == {'gpt-6-luna'}
    for ref in refs:
        chapter = next(c for c in complete['chapters'] if c['id']==ref['chapter_id'])
        assert chapter['text'][ref['start']:ref['end']] == ref['quote']


def test_profile_refinement_is_a_separate_stage_and_reuses_results(setup):
    book,store,provider = setup
    scanned = process(book,store)
    n = len(provider.calls)
    refined = process(scanned,store,'profiles')
    assert [c['stage'] for c in provider.calls[n:]] == ['profiles','profiles']
    assert all(c['model']=='gpt-6-sol' for c in provider.calls[n:])
    assert all(c.get('profile_refined') for c in refined['characters'] if c['id'] not in {'narrator','unassigned'})
    n = len(provider.calls)
    again = process(refined,store,'profiles')
    assert len(provider.calls) == n
    assert_same_source(book,again)


def test_profiles_select_early_and_late_observations_and_more_for_frequent_character(tmp_path,monkeypatch):
    book = story(20)
    store = Store(tmp_path)
    store.save_book(book)
    provider = FakeProvider(monkeypatch)
    scanned = process(book,store)
    accepted = discoveries(scanned,store,ProcessingStore(store))
    specs = {s['name']:s for s in profile_specs(scanned,store,accepted)}
    mara = json_after(specs['Mara']['prompt'],'CURRENT BOOK OBSERVATIONS:\n')
    elio = json_after(specs['Elio']['prompt'],'CURRENT BOOK OBSERVATIONS:\n')
    assert mara[0]['chapter_id']==book['chapters'][0]['id']
    assert mara[-1]['chapter_id']==book['chapters'][-1]['id']
    assert len(mara)>len(elio) and len(mara)<=16
    assert specs['Mara']['priority']=='deep'


def test_profiles_include_only_confirmed_earlier_volume_observations(setup):
    book,store,provider = setup
    scanned = process(book,store)
    earlier = parse_book('earlier.txt',b'Mara used a low voice.')
    earlier['characters'].append({'id':'early-mara','name':'Mara','aliases':[],'description':'','direction':''})
    store.save_book(earlier)
    series = SeriesRepository(store)
    identity = series.create_series('Saga')
    series.set_membership(earlier['id'],identity['id'],1)
    series.set_membership(book['id'],identity['id'],9)
    mara = next(c for c in scanned['characters'] if c['name']=='Mara')
    linked = series.create_character(identity['id'],'Mara')
    series.link_character(earlier['id'],'early-mara',linked['id'])
    series.link_character(book['id'],mara['id'],linked['id'])
    ref = {'id':'observation','character_id':'early-mara','chapter_id':earlier['chapters'][0]['id'],
           'start':0,'end':len(earlier['chapters'][0]['text']),'quote':earlier['chapters'][0]['text'],
           'kind':'profile_evidence','profile_description':'Low voice in the first volume.','provider':'openai','model':'older-model'}
    store.save_analysis_checkpoint(earlier['id'],'earlier',{'references':[ref]})
    accepted = discoveries(scanned,store,ProcessingStore(store))
    spec = next(s for s in profile_specs(scanned,store,accepted) if s['name']=='Mara')
    prior = json_after(spec['prompt'],'EARLIER LINKED VOLUMES:\n')
    # The prompt shows where the evidence is from; IDs and offsets stay in the retained recipe.
    assert len(prior)==1 and prior[0]['book_title']==earlier['title'] and 'book_id' not in prior[0]
    assert spec['prior_observations'][0]['book_id']==earlier['id']
    assert prior[0]['quote']=='Mara used a low voice.' and prior[0]['position']==1
    series.unlink_character(book['id'],mara['id'])
    spec = next(s for s in profile_specs(scanned,store,accepted) if s['name']=='Mara')
    assert json_after(spec['prompt'],'EARLIER LINKED VOLUMES:\n') == []


def test_reviewed_character_and_passage_survive_full_processing(setup):
    book,store,provider = setup
    scanned = process(book,store)
    mara = next(c for c in scanned['characters'] if c['name']=='Mara')
    mara.update(edited=True,description='My reviewed profile.',direction='My exact voice direction.',aliases=['M'])
    passage = next(s for s in scanned['segments'] if s['kind']=='dialogue')
    passage.update(edited=True,speaker_id=mara['id'],direction='My reviewed passage.',cues=['breath'],confidence=1)
    store.save_book(scanned)
    result = process(scanned,store,'full')
    updated_mara = next(c for c in result['characters'] if c['id']==mara['id'])
    for field in ('name','aliases','description','direction'):
        assert updated_mara[field]==mara[field]
    assert next(s for s in result['segments'] if s['id']==passage['id']) == passage
    assert not any(c['stage']=='profiles' and json_after(c['prompt'],'CANDIDATES:\n')[0]['name']=='Mara' for c in provider.calls)


def test_failure_resumes_paid_discovery_after_checkpoint_was_replaced(setup):
    book,store,provider = setup
    def fail_later(stage,result,prompt):
        if stage=='discovery' and 'Chapter 2' in prompt:
            raise RuntimeError('temporary problem')
        return result
    provider.transform = fail_later
    with pytest.raises(ValueError,match='saved for resume'):
        process(book,store)
    assert len(ProcessingStore(store).units(book['id'],'discovery',source_hash(book)))==1
    # A separate local/different-provider stage may replace the legacy singleton checkpoint.
    store.delete_analysis_checkpoint(book['id'])
    provider.transform = lambda _stage,result,_prompt:result
    n = len(provider.calls)
    finished = process(store.book(book['id']),store)
    assert len(provider.calls)==n+1 and 'Chapter 2' in provider.calls[-1]['prompt']
    assert coverage(finished,store)['whole_book_discovered']


def test_cache_keys_keep_model_specific_profiles_and_reuse_accepted_scan_provenance(setup):
    book,store,provider = setup
    full = process(book,store,'full')
    n = len(provider.calls)
    resumed = process(full,store,'scan',scan_model='other-fast-model')
    assert len(provider.calls)==n
    assert {u['model'] for u in discoveries(resumed,store,ProcessingStore(store))}=={'gpt-6-luna'}
    process(resumed,store,'profiles',model='other-refining-model')
    assert len(provider.calls)==n+2
    assert all(c['model']=='other-refining-model' for c in provider.calls[n:])


def test_repeated_full_processing_reuses_direction_even_after_scene_split(setup):
    book,store,provider = setup
    def split(stage,result,prompt):
        if stage=='directing':
            passages = json_after(prompt,'PASSAGES:\n')
            result['scene_starts']=[{'segment_id':passages[-1]['id'],'title':'Later','summary':'A later beat.','tone':'Quiet','direction':'Unhurried.'}]
        return result
    provider.transform=split
    first = process(book,store,'full')
    assert len(first['scenes'])>len(book['scenes'])
    n=len(provider.calls)
    second = process(first,store,'full')
    assert len(provider.calls)==n
    assert [(s['id'],s['title'],s['summary']) for s in second['scenes']] == [(s['id'],s['title'],s['summary']) for s in first['scenes']]
    assert_same_source(book,second)


def test_plan_never_calls_provider_and_distinguishes_cached_known_work(setup):
    book,store,provider=setup
    before=plan(book,store,'openai','gpt-6-sol','gpt-6-luna')
    assert before['requests']==2 and before['steps_by_stage']['profiles']==0
    assert before['estimated_cost_usd']>0 and provider.calls==[]
    scanned=process(book,store)
    after=plan(scanned,store,'openai','gpt-6-sol','gpt-6-luna')
    assert after['requests']==0 and after['cached_units']==2
    changed=plan(scanned,store,'openai','unknown-model','gpt-6-luna','profiles')
    assert changed['estimated_cost_usd'] is None


def test_invalid_evidence_repair_is_bounded_and_does_not_cache_rejected_unit(setup):
    book,store,provider=setup
    provider.transform=lambda stage,result,prompt:{'characters':[{'name':'Mara','aliases':[], 'description':'Unsupported','direction':'Natural','evidence':['invented source quotation']}]} if stage=='discovery' else result
    with pytest.raises(ValueError,match='quoted evidence'):
        process(book,store)
    assert len(provider.calls)==2
    assert ProcessingStore(store).units(book['id'],'discovery',source_hash(book))==[]
    assert store.book(book['id'])['characters']==book['characters']


def test_explicit_fresh_scan_regenerates_without_discarding_other_stage_cache(setup):
    book,store,provider=setup
    first=process(book,store,'full')
    n=len(provider.calls)
    second=process(first,store,'scan',resume=False)
    assert len(provider.calls)==n+2
    process(second,store,'profiles')
    assert len(provider.calls)==n+2


def test_profile_stage_recovers_accepted_cast_after_publication_failed(setup):
    book,store,provider=setup
    def progress(_done,_total,message):
        if 'character evidence saved' in message:
            raise RuntimeError('Publication interrupted after paid work')
    with pytest.raises(ValueError,match='saved for resume'):
        process(book,store,progress=progress)
    assert not any(c['name']=='Mara' for c in store.book(book['id'])['characters'])
    assert len(ProcessingStore(store).units(book['id'],'discovery',source_hash(book)))==1
    n=len(provider.calls)
    result=process(store.book(book['id']),store,'profiles')
    assert [c['stage'] for c in provider.calls[n:]]==['profiles']
    assert next(c for c in result['characters'] if c['name']=='Mara')['profile_refined']


def test_identical_source_units_retain_distinct_chapter_locators(tmp_path,monkeypatch):
    from test_structure import epub
    book=parse_book('repeat.epub',epub({'one':'<p>“Stay,” Mara said.</p>', 'two':'<p>“Stay,” Mara said.</p>'}))
    store=Store(tmp_path)
    store.save_book(book)
    provider=FakeProvider(monkeypatch)
    result=process(book,store)
    units=ProcessingStore(store).units(book['id'],'discovery',source_hash(book))
    assert {u['chapter_id'] for u in units}=={c['id'] for c in book['chapters']}
    refs=[r for r in store.character_references(book['id']) if r['kind']=='profile_evidence']
    assert {r['chapter_id'] for r in refs}=={c['id'] for c in book['chapters']}
    assert coverage(result,store)['whole_book_discovered']


def test_cached_discovery_replay_keeps_original_provider_and_historical_observations(setup):
    book,store,provider=setup
    first=process(book,store)
    observations=SeriesRepository(store).observations(book['id'])
    before_refs=store.character_references(book['id'])
    n=len(provider.calls)
    second=process(first,store,provider='anthropic',model='claude-sonnet-5',scan_model='claude-haiku-4-5-20251001')
    assert len(provider.calls)==n
    assert store.character_references(book['id'])==before_refs
    assert SeriesRepository(store).observations(book['id'])==observations
    assert all(r['provider']=='openai' and r['model']=='gpt-6-luna' for r in before_refs if r['kind']=='profile_evidence')
    assert_same_source(book,second)


def test_later_book_observations_make_earlier_refinement_provisional_until_rebuilt(setup):
    book,store,provider=setup
    first=process(book,store,chapter_id=book['chapters'][0]['id'])
    refined=process(first,store,'profiles')
    mara=next(c for c in refined['characters'] if c['name']=='Mara')
    assert mara['profile_refined'] and mara['profile_provisional']
    first_key=mara['profile_input_key']
    whole=process(refined,store)
    stale=next(c for c in whole['characters'] if c['name']=='Mara')
    assert stale['profile_input_key']==first_key and stale['profile_provisional']
    assert whole['analysis']['profiles_provisional']
    final=process(whole,store,'profiles')
    current=next(c for c in final['characters'] if c['name']=='Mara')
    assert current['profile_input_key']!=first_key
    assert not current['profile_provisional'] and not final['analysis']['profiles_provisional']


def test_importing_old_checkpoint_cannot_promote_old_unit_over_newer_accepted_retry(setup):
    book,store,provider=setup
    process(book,store,chapter_id=book['chapters'][0]['id'])
    repository=ProcessingStore(store)
    old=repository.units(book['id'],'discovery',source_hash(book))[0]
    newer=deepcopy(old)
    newer.update(unit_key='discovery:newer',model='newer-fast-model')
    newer['result']['characters'][0]['description']='Newer accepted observation'
    repository.save_unit(book['id'],newer['unit_key'],'discovery',source_hash(book),newer)
    for _ in range(3):
        accepted=discoveries(book,store,repository)
        assert len(accepted)==1 and accepted[0]['model']=='newer-fast-model'
    assert len(repository.units(book['id'],'discovery',source_hash(book)))==2


def test_dense_long_chapter_uses_prose_chunks_and_keeps_complete_coverage(tmp_path,monkeypatch):
    from bardic.progressive import discovery_specs
    book=parse_book('dense.txt',('Chapter One\n\n'+('“Stay,” Mara said.\n\n'*2600)).encode())
    store=Store(tmp_path)
    store.save_book(book)
    provider=FakeProvider(monkeypatch)
    before=deepcopy(book)
    specs=discovery_specs(book,book['chapters'])
    assert len(book['segments'])>5000
    assert 2<=len(specs)<=3, 'Many short passages must not force dozens of discovery calls'
    assert all(0<len(s['source'])<=24000 for s in specs)
    cursor=0
    text=book['chapters'][0]['text']
    for spec in specs:
        assert not text[cursor:spec['start']].strip()
        assert spec['source']==text[spec['start']:spec['end']]
        cursor=spec['end']
    assert not text[cursor:].strip()
    result=process(book,store)
    assert len(provider.calls)==len(specs)
    assert coverage(result,store)['whole_book_discovered']
    assert_same_source(before,result)


@pytest.mark.parametrize('text',[
    'a '*30000,
    'one paragraph.\n\n'*5000,
    'unbroken'*8000,
    '\n\n'+' words '*8000+'\n\n   ',
])
def test_discovery_range_limits_do_not_omit_nonwhitespace(text):
    from bardic.progressive import discovery_ranges
    ranges=discovery_ranges(text)
    assert ranges
    cursor=0
    for start,end in ranges:
        assert cursor<=start<end<=len(text)
        assert end-start<=24000
        assert not text[cursor:start].strip()
        cursor=end
    assert not text[cursor:].strip()


def test_existing_legacy_discovery_ranges_are_replayed_without_new_calls(tmp_path,monkeypatch):
    from bardic.progressive import discovery_specs
    book=parse_book('legacy-dense.txt',('Chapter One\n\n'+('“Stay,” Mara said.\n\n'*2300)).encode())
    store=Store(tmp_path)
    store.save_book(book)
    repository=ProcessingStore(store)
    chapter=book['chapters'][0]
    batches=a._batches(book['segments'],limit=10000)
    accepted=[]
    for number,batch in enumerate(batches):
        unit={'unit_key':f'legacy:{number}','stage':'discovery','provider':'anthropic','model':'previous-model',
              'chapter_id':chapter['id'],'start':batch[0]['start'],'end':batch[-1]['end'],
              'result':{'characters':[{'name':'Mara','aliases':[],'description':'Unknown traits.',
                                       'direction':'Natural.','evidence':['Mara said']}]}}
        accepted.append(unit)
        repository.save_unit(book['id'],unit['unit_key'],'discovery',source_hash(book),unit)
    assert len(accepted)>30
    specs=discovery_specs(book,book['chapters'],accepted)
    assert [(s['start'],s['end']) for s in specs]==[(u['start'],u['end']) for u in accepted]
    provider=FakeProvider(monkeypatch)
    preview=plan(book,store,'openai','gpt-6-sol','gpt-6-luna')
    assert preview['requests']==0 and preview['cached_units']==len(accepted)
    result=process(book,store)
    assert provider.calls==[] and coverage(result,store)['whole_book_discovered']
    assert_same_source(book,result)


def test_direction_roster_is_bounded_and_prioritizes_local_or_reviewed_speakers():
    from bardic.progressive import direction_cast
    book=story(1)
    def character(n,name=None,aliases=()):
        return {'id':f'person-{n}','name':name or f'Remote Person {n}', 'aliases':list(aliases),
                'description':f'profile {n} '+('voice quality '*200),
                'direction':f'direction {n} '+('restrained '*200)}
    book['characters'] += [character(n) for n in range(200)]
    book['characters'] += [character(201,'Mara'),character(202,'Silent One'),character(203,'Hidden Name',['Elio'])]
    batch=deepcopy(book['segments'])
    batch[0].update(edited=True,speaker_id='person-202')
    roster=direction_cast(book,batch,'','')
    by_id={c['id']:c for c in roster}
    assert len(json.dumps(roster,ensure_ascii=False))<=14000
    assert {'narrator','unassigned','person-201','person-202','person-203'}<=by_id.keys()
    for identifier in ('person-201','person-202','person-203'):
        assert by_id[identifier]['description'] and len(by_id[identifier]['description'])<=900
        assert by_id[identifier]['direction'] and len(by_id[identifier]['direction'])<=600
    remote=[c for c in roster if c['id'] not in {'narrator','unassigned','person-201','person-202','person-203'}]
    assert remote and all('description' not in c and 'direction' not in c for c in remote)
    assert len(roster)<len(book['characters'])


def test_remote_profile_edits_do_not_invalidate_local_direction_request():
    from bardic.progressive import direction_specs,unit_key
    book=story(1)
    book['characters'] += [
        {'id':'mara','name':'Mara','aliases':[],'description':'Local voice.','direction':'Calm.'},
        {'id':'remote','name':'Distant Queen','aliases':[],'description':'Old remote profile.','direction':'Distant poise.'},
    ]
    first=direction_specs(book,book['chapters'])[0]
    before=unit_key(first,'openai','gpt-6-sol')
    book['characters'][-1].update(description='A newly understood remote voice.',direction='A different faraway tone.')
    second=direction_specs(book,book['chapters'])[0]
    assert unit_key(second,'openai','gpt-6-sol')==before
    book['characters'][-2]['description']='A changed local voice.'
    third=direction_specs(book,book['chapters'])[0]
    assert unit_key(third,'openai','gpt-6-sol')!=before
