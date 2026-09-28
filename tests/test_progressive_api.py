"""The public API defaults to discovery and exposes a free plan before work."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from spintails.app import create_app
from test_progressive import FakeProvider
from test_staged_api import import_book,wait_job


@pytest.fixture
def client(tmp_path,monkeypatch):
    for name in ('GEMINI_API_KEY','GOOGLE_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setattr('spintails.app.list_system_voices',lambda:[])
    with TestClient(create_app(tmp_path)) as client:
        yield client


def test_default_cloud_action_is_scan_with_separate_fast_model(client,monkeypatch):
    book=import_book(client)
    provider=FakeProvider(monkeypatch)
    client.post('/api/settings',json={'api_keys':{'openai':'fake-key'},'analysis_provider':'openai',
                                     'analysis_models_by_provider':{'openai':'gpt-6-sol'},
                                     'preprocess_models_by_provider':{'openai':'gpt-6-luna'}})
    response=client.post(f"/api/books/{book['id']}/analyze",json={})
    assert response.status_code==200,response.text
    job=wait_job(client,response.json()['id'])
    assert job['status']=='completed',job
    assert job['phase']=='scan' and job['scan_model']=='gpt-6-luna'
    assert len(provider.calls)==2 and all(c['stage']=='discovery' and c['model']=='gpt-6-luna' for c in provider.calls)
    assert 'fake-key' not in json.dumps(job)


def test_preprocessing_and_plan_need_no_key_and_do_not_queue_or_generate(client,monkeypatch):
    book=import_book(client)
    provider=FakeProvider(monkeypatch)
    base=f"/api/books/{book['id']}"
    local=client.get(base+'/preprocessing')
    assert local.status_code==200,local.text
    assert local.json()['local']['local_complete']
    assert local.json()['local']['local_chapters_scanned']==2
    assert local.json()['profiles_provisional']
    plan=client.post(base+'/analysis-plan',json={'provider':'openai'})
    assert plan.status_code==200,plan.text
    assert plan.json()['phase']=='scan' and plan.json()['requests']==2
    assert plan.json()['steps_by_stage']=={'discovery':2,'profiles':0,'directing':0}
    assert plan.json()['limits']['budget_usd']==1.0
    assert client.get('/api/jobs').json()==[] and provider.calls==[]


@pytest.mark.parametrize('body',[
    {'phase':'everything'}, {'limits':{'max_requests':0}}, {'limits':{'max_requests':1001}},
    {'limits':{'budget_usd':0}}, {'limits':{'budget_usd':-1}},
    {'limits':{'max_input_tokens':0}}, {'limits':{'max_output_tokens':0}},
])
def test_bad_phases_and_limits_never_start_jobs(client,body):
    book=import_book(client)
    response=client.post(f"/api/books/{book['id']}/analyze",json=body)
    assert response.status_code==422,response.text
    assert client.get('/api/jobs').json()==[]


def test_explicit_full_phase_and_limits_reach_worker_unchanged(client,monkeypatch):
    book=import_book(client)
    calls=[]
    def analyze(source,provider,key,model,progress,cancelled,**options):
        calls.append(options)
        return deepcopy(source)
    monkeypatch.setattr('spintails.app.analyze_book',analyze)
    client.post('/api/settings',json={'api_keys':{'anthropic':'fake-key'},'preprocess_models_by_provider':{'anthropic':'custom-fast'}})
    limits={'max_requests':3,'max_input_tokens':10000,'max_output_tokens':9000,'budget_usd':None}
    response=client.post(f"/api/books/{book['id']}/analyze",json={'provider':'anthropic','phase':'full','limits':limits})
    assert response.status_code==200,response.text
    assert wait_job(client,response.json()['id'])['status']=='completed'
    assert calls[0]['phase']=='full' and calls[0]['scan_model']=='custom-fast'
    assert calls[0]['limits']==limits


def test_plan_validates_scope_provider_and_origin(client):
    book=import_book(client)
    path=f"/api/books/{book['id']}/analysis-plan"
    assert client.post(path,json={'chapter_id':'wrong'}).status_code==400
    assert client.post(path,json={'provider':'wrong'}).status_code==400
    assert client.post(path,json={},headers={'origin':'https://foreign.example'}).status_code==403
    assert client.get('/api/books/missing/preprocessing').status_code==404
    assert client.post('/api/books/missing/analysis-plan',json={}).status_code==404
