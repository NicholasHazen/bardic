"""Character aliases must not select a different person by iteration order."""
from copy import deepcopy

from spintails import analysis as a
from spintails.importer import parse_book
from spintails.processing import ProcessingStore,source_hash
from spintails.progressive import discoveries,profile_specs
from spintails.staged_analysis import _references
from spintails.store import Store


def person(identifier,name,aliases=(),**extra):
    return {'id':identifier,'name':name,'aliases':list(aliases),'description':'Original profile.',
            'direction':'Original voice direction.','evidence':[],'voice':'Kore','system_voice':'',**extra}


def candidate(name,aliases=()):
    return {'name':name,'aliases':list(aliases),'description':'Later observation.','direction':'Quietly.',
            'evidence':['Mara Voss, called Mara, spoke quietly.']}


def test_later_canonical_form_is_retained_as_nonconflicting_alias():
    book=parse_book('names.txt',b'Mara Voss, called Mara, spoke quietly.')
    mara=person('mara','Mara')
    book['characters'].append(mara)
    names=a._merge_cast(book,[candidate('Mara Voss',['Mara'])])
    assert len(book['characters'])==3 and mara['aliases']==['Mara Voss']
    assert names['mara voss']['id']=='mara'


def test_shared_alias_never_chooses_whichever_character_was_last():
    first=person('first','Mara',['The Captain'])
    second=person('second','Elio',['The Captain'])
    for characters in ([first,second],[second,first]):
        book={'characters':deepcopy(characters)}
        before=deepcopy(book)
        assert a._resolve_character_candidate(book['characters'],candidate('The Captain')) is None
        names=a._merge_cast(book,[candidate('The Captain')])
        assert book==before and 'the captain' not in names


def test_conflicting_candidate_aliases_do_not_merge_existing_people():
    book={'characters':[person('mara','Mara'),person('elio','Elio')]}
    before=deepcopy(book['characters'])
    conflict=candidate('New Captain',['Mara','Elio'])
    assert a._resolve_character_candidate(book['characters'],conflict) is None
    a._merge_cast(book,[conflict])
    assert book['characters'][:2]==before
    assert book['characters'][-1]['name']=='New Captain' and book['characters'][-1]['aliases']==[]


def test_unique_canonical_name_wins_without_stealing_conflicting_alias():
    mara,elio=person('mara','Mara'),person('elio','Elio')
    book={'characters':[mara,elio]}
    assert a._resolve_character_candidate(book['characters'],candidate('Mara',['Elio'])) is mara
    a._merge_cast(book,[candidate('Mara',['Elio'])])
    assert mara['aliases']==[] and elio['description']=='Original profile.'


def test_reviewed_profile_keeps_identity_aliases_and_voice_but_gets_grounded_evidence():
    mara=person('mara','Mara',['M'],edited=True)
    before=deepcopy(mara)
    book={'characters':[mara]}
    a._merge_cast(book,[candidate('Mara Voss',['Mara','Voss'])])
    assert {k:v for k,v in mara.items() if k!='evidence'}=={k:v for k,v in before.items() if k!='evidence'}
    assert mara['evidence']==candidate('anything')['evidence']


def test_alias_resolver_keeps_later_canonical_evidence_in_profiles_and_references(tmp_path):
    book=parse_book('aliases.txt',b'Mara Voss, called Mara, spoke quietly.')
    book['characters'].append(person('mara','Mara'))
    store=Store(tmp_path)
    store.save_book(book)
    unit={'unit_key':'later-name','provider':'anthropic','model':'claude-haiku-4-5-20251001',
          'stage':'discovery','chapter_id':book['chapters'][0]['id'],'start':0,'end':len(book['chapters'][0]['text']),
          'result':{'characters':[candidate('Mara Voss',['Mara'])]}}
    repository=ProcessingStore(store)
    repository.save_unit(book['id'],unit['unit_key'],'discovery',source_hash(book),unit)
    accepted=discoveries(book,store,repository)
    specs=profile_specs(book,store,accepted)
    assert len(specs)==1 and specs[0]['name']=='Mara'
    assert 'Mara Voss, called Mara' in specs[0]['prompt']
    refs=_references(book,{unit['unit_key']:unit},'openai','gpt-6-sol',[])
    evidence=[r for r in refs if r['kind']=='profile_evidence']
    assert len(evidence)==1 and evidence[0]['character_id']=='mara'
    assert evidence[0]['provider']=='anthropic' and evidence[0]['model']=='claude-haiku-4-5-20251001'


def test_ambiguous_candidate_evidence_is_not_attributed_to_either_shared_alias():
    book=parse_book('alias.txt',b'The Captain spoke quietly.')
    book['characters'] += [person('mara','Mara',['The Captain']),person('elio','Elio',['The Captain'])]
    record=candidate('The Captain')
    record['evidence']=['The Captain spoke quietly.']
    unit={'stage':'discovery','chapter_id':book['chapters'][0]['id'],'start':0,'end':len(book['chapters'][0]['text']),
          'result':{'characters':[record]}}
    assert _references(book,{'unit':unit},'openai','gpt-6-luna',[])==[]
