"""Character aliases must not select a different person by iteration order."""
from copy import deepcopy

from bardic import analysis as a
from bardic.importer import parse_book
from bardic.pipeline import default_registry, evidence
from bardic.pipeline.prompts import profile_specs
from bardic.pipeline.repository import PipelineRepository
from bardic.store import Store


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


def accept_discovery(store, book, candidates):
    """Accept one discovery version for the book's first chapter, as a run would publish it."""
    chapter = book['chapters'][0]
    payload = {'ranges': [{'start': 0, 'end': len(chapter['text']), 'unit': 'u1'}],
               'candidates': [{**c, 'range_start': 0} for c in candidates]}
    repository, registry = PipelineRepository(store), default_registry()
    with store.lock, store.connect() as conn:
        identifier = repository.record_version(conn, book['id'], registry.get('discovery'), chapter['id'], payload,
                                               origin='run', provider='anthropic', model='claude-haiku-4-5-20251001')
        repository.decide(conn, book['id'], 'discovery', 'accept', {chapter['id']: identifier}, mode='user')
        evidence.refresh(repository, conn, store.book(book['id']))
    return payload


def test_alias_resolver_keeps_later_canonical_evidence_in_profiles_and_references(tmp_path):
    book=parse_book('aliases.txt',b'Mara Voss, called Mara, spoke quietly.')
    book['characters'].append(person('mara','Mara'))
    store=Store(tmp_path)
    store.save_book(book)
    payload=accept_discovery(store,book,[candidate('Mara Voss',['Mara'])])
    # The Profiles step reads accepted discovery candidates in this shape.
    observations=[{'chapter_id':book['chapters'][0]['id'],'unit_key':'u1','result':{'characters':payload['candidates']}}]
    specs=profile_specs(book,store,observations)
    assert len(specs)==1 and specs[0]['name']=='Mara'
    assert 'Mara Voss, called Mara' in specs[0]['prompt']
    refs=[r for r in store.character_references(book['id']) if r['kind']=='profile_evidence']
    assert len(refs)==1 and refs[0]['character_id']=='mara' and refs[0]['step']=='discovery'
    assert refs[0]['provider']=='anthropic' and refs[0]['model']=='claude-haiku-4-5-20251001'


def test_ambiguous_candidate_evidence_is_not_attributed_to_either_shared_alias(tmp_path):
    book=parse_book('alias.txt',b'The Captain spoke quietly.')
    book['characters'] += [person('mara','Mara',['The Captain']),person('elio','Elio',['The Captain'])]
    record=candidate('The Captain')
    record['evidence']=['The Captain spoke quietly.']
    store=Store(tmp_path)
    store.save_book(book)
    accept_discovery(store,book,[record])
    assert [r for r in store.character_references(book['id']) if r['kind']=='profile_evidence']==[]
    with store.connect() as conn:
        counts=PipelineRepository(store).state(conn,book['id'])[evidence.STATE_KEY]['counts']
    assert counts['unresolved']==1
