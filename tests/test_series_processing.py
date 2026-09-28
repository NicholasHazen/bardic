"""Series scans overlap only independent titles; interpretation follows reading order."""
from copy import deepcopy
import threading
import time

import pytest
from fastapi.testclient import TestClient

from bardic.app import create_app
from bardic.processing import BudgetReached
from test_app import import_text, wait_job


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ('GEMINI_API_KEY','GOOGLE_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setattr('bardic.app.list_system_voices',lambda:[])
    with TestClient(create_app(tmp_path)) as client:
        client.app.state.runtime.api_keys['openai']='private-test-key'
        yield client


def collection(client, count=3):
    series=client.post('/api/series',json={'name':'The Lanterns'}).json()
    books=[]
    for n in range(count):
        book=import_text(client,f'Chapter One\n\nMara found the lamp in volume {n+1}.')
        assert client.put(f"/api/books/{book['id']}/series",json={'series_id':series['id'],'position':float(n+1)}).status_code==200
        books.append(book)
    return series,books


def test_plan_never_sends_text_and_incomplete_series_can_start_at_nine(client,monkeypatch):
    series,books=collection(client,1)
    client.put(f"/api/books/{books[0]['id']}/series",json={'series_id':series['id'],'position':9.0})
    assert client.put(f"/api/series/{series['id']}/volumes",json={'position':1,'title':'Earlier book','status':'missing'}).status_code==200
    def forbidden(*args,**kwargs): pytest.fail('Preview cannot call a model')
    monkeypatch.setattr('bardic.analysis.analyze_book',forbidden)
    response=client.post(f"/api/series/{series['id']}/plan",json={'provider':'openai','phase':'full'})
    assert response.status_code==200,response.text
    plan=response.json()
    assert [b['position'] for b in plan['books']]==[9]
    assert any(v['status']=='missing' for v in plan['volumes'])
    assert client.get('/api/jobs').json()==[]
    assert 'private-test-key' not in response.text


def test_parallel_scans_then_ordered_interpretation_reserves_every_book(client,monkeypatch):
    series,books=collection(client)
    calls=[]; active=0; maximum=0; lock=threading.Lock(); overlap=threading.Event()
    def fake(book,provider,key,model,progress,cancelled,**options):
        nonlocal active,maximum
        phase=options['phase']
        with lock:
            calls.append((book['id'],phase,'start',options['run_id']))
            if phase=='scan':
                active+=1;maximum=max(maximum,active)
                if active==2: overlap.set()
        if phase=='scan':
            assert overlap.wait(2)
            time.sleep(.015)
        with lock:
            if phase=='scan': active-=1
            calls.append((book['id'],phase,'end',options['run_id']))
        return deepcopy(book)
    monkeypatch.setattr('bardic.analysis.analyze_book',fake)
    response=client.post(f"/api/series/{series['id']}/process",json={'provider':'openai','phase':'full','concurrency':2})
    assert response.status_code==200,response.text
    parent=wait_job(client,response.json()['id'])
    assert parent['status']=='completed',parent
    assert maximum==2
    assert [b for b,p,e,_ in calls if p=='full' and e=='start']==[b['id'] for b in books]
    first_ordered=next(i for i,c in enumerate(calls) if c[1]=='full')
    assert all(calls.index(c)<first_ordered for c in calls if c[1]=='scan' and c[2]=='end')
    for book in books:
        assert len({c[3] for c in calls if c[0]==book['id']})==1 # One allowance across phases.
    runs=client.get(f"/api/series/{series['id']}/runs").json()['runs']
    assert len(runs[0]['children'])==3
    assert all(j['status']=='completed' for j in runs[0]['children'])
    assert 'private-test-key' not in str(runs)


def test_budget_failure_does_not_start_later_books_and_history_remains(client,monkeypatch):
    series,books=collection(client)
    calls=[]
    def fail(book,*args,**options):
        calls.append(book['id']);raise BudgetReached('Allowance reached')
    monkeypatch.setattr('bardic.analysis.analyze_book',fail)
    result=client.post(f"/api/series/{series['id']}/process",json={'provider':'openai','phase':'full','concurrency':1}).json()
    parent=wait_job(client,result['id'])
    assert parent['status']=='failed'
    assert calls==[books[0]['id']]
    children=client.get(f"/api/series/{series['id']}/runs").json()['runs'][0]['children']
    assert [j['status'] for j in children]==['budget_limited','interrupted','interrupted']
    for b in books:
        assert client.get(f"/api/books/{b['id']}/artifacts?kind=series_run").json()['total']>=2


def test_cancel_parent_cancels_children_and_blocks_book_edit_until_finished(client,monkeypatch):
    series,books=collection(client,2)
    started=threading.Event()
    def wait_cancel(book,*args,**options):
        cancelled=args[4] # provider, key, model, progress, cancelled
        started.set()
        deadline=time.monotonic()+3
        while not cancelled() and time.monotonic()<deadline: time.sleep(.005)
        if cancelled(): raise InterruptedError('Stopped')
        pytest.fail('Cancellation did not propagate')
    monkeypatch.setattr('bardic.analysis.analyze_book',wait_cancel)
    parent=client.post(f"/api/series/{series['id']}/process",json={'provider':'openai','phase':'scan'}).json()
    assert started.wait(2)
    assert client.patch(f"/api/books/{books[0]['id']}/metadata",json={'title':'Changed','author':''}).status_code==409
    assert client.post(f"/api/jobs/{parent['id']}/cancel").status_code==200
    assert wait_job(client,parent['id'])['status']=='cancelled'
    children=client.get(f"/api/series/{series['id']}/runs").json()['runs'][0]['children']
    assert all(c['status'] not in {'running','queued'} for c in children)


def test_archived_books_and_placeholders_are_not_scheduled(client,monkeypatch):
    series,books=collection(client,2)
    assert client.post(f"/api/books/{books[0]['id']}/archive").status_code==200
    calls=[]
    def fake(book,*args,**kwargs):calls.append(book['id']);return deepcopy(book)
    monkeypatch.setattr('bardic.analysis.analyze_book',fake)
    result=client.post(f"/api/series/{series['id']}/process",json={'provider':'openai','phase':'scan'}).json()
    assert wait_job(client,result['id'])['status']=='completed'
    assert calls==[books[1]['id']]
    assert client.post(f"/api/series/{series['id']}/archive").status_code==200
    assert client.post(f"/api/series/{series['id']}/plan",json={'provider':'openai'}).status_code==404


@pytest.mark.parametrize('body',[{'provider':'local'},{'provider':'unknown'},{'concurrency':3},{'limits':{'max_requests':0}}])
def test_invalid_series_configuration_never_creates_jobs(client,body):
    series,_=collection(client,1)
    assert client.post(f"/api/series/{series['id']}/process",json=body).status_code in {400,422}
    assert client.get('/api/jobs').json()==[]
