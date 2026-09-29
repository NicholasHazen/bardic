"""The API client, exercised against an in-memory fake server (httpx.MockTransport): it must build requests
from the contract, refuse to send ones the contract does not allow, and fail on a nonconforming reply."""
from __future__ import annotations

import json

import httpx
import pytest

from ..client import Api, ContractViolation, Recorder
from .tiny import GOOD_THING, contract as tiny_contract

HEADERS = {'content-type': 'application/json', 'cache-control': 'no-store'}


class FakeServer:
    """Answers with whatever the test queued, and remembers what it was asked."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.status, self.body, self.headers = 200, GOOD_THING, dict(HEADERS)

    def reply(self, status=200, body=None, headers=None):
        self.status, self.body, self.headers = status, GOOD_THING if body is None else body, HEADERS if headers is None else headers

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        content = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return httpx.Response(self.status, headers=self.headers, content=content)


@pytest.fixture()
def fake():
    return FakeServer()


@pytest.fixture()
def api(fake):
    client = Api('http://127.0.0.1:9', tiny_contract(), Recorder(), transport=httpx.MockTransport(fake))
    yield client
    client.close()


def test_a_conforming_exchange_passes_and_is_recorded(api, fake):
    reply = api.call('getThing', path={'thing_id': 'a b/c'})
    assert reply.status == 200 and reply.json == GOOD_THING and reply.operation.id == 'getThing'
    assert fake.requests[0].url.raw_path == b'/api/things/a%20b%2Fc', 'path parameters are URL-encoded'
    assert api.recorder.succeeded == {'getThing': 1} and api.recorder.statuses == {('getThing', 200): 1}


def test_a_nonconforming_reply_fails_the_test(api, fake):
    fake.reply(200, {**GOOD_THING, 'surprise': 1})
    with pytest.raises(ContractViolation, match="undeclared field 'surprise'"):
        api.call('getThing', path={'thing_id': 'x'})
    assert api.recorder.succeeded == {'getThing': 1}, 'the exchange is still recorded'


def test_an_unexpected_status_fails_even_when_the_contract_allows_it(api, fake):
    fake.reply(404, {'detail': 'x', 'code': 'thing_not_found'})
    with pytest.raises(AssertionError, match=r'expected status \[200\]'):
        api.call('getThing', path={'thing_id': 'x'})
    reply = api.call('getThing', path={'thing_id': 'x'}, expect=404)
    assert reply.code == 'thing_not_found'
    assert api.call('getThing', path={'thing_id': 'x'}, expect=(200, 404)).status == 404
    assert api.call('getThing', path={'thing_id': 'x'}, expect=None).status == 404
    assert api.recorder.error_codes == {('getThing', 404, 'thing_not_found'): 4}
    assert api.recorder.succeeded == {}


def test_an_undocumented_status_fails_whatever_the_test_expected(api, fake):
    fake.reply(409, {'detail': 'x', 'code': 'conflict'})
    with pytest.raises(ContractViolation, match='undocumented error status 409'):
        api.call('getThing', path={'thing_id': 'x'}, expect=409)


def test_a_server_defect_500_fails_unless_the_test_asks_for_it(api, fake):
    fake.reply(500, {'detail': 'The server hit an unexpected error.', 'code': 'internal_error'})
    for expect in (200, None, (200, 404)):
        with pytest.raises(AssertionError, match='unexpected defect'):
            api.call('getThing', path={'thing_id': 'x'}, expect=expect)
    assert api.call('getThing', path={'thing_id': 'x'}, expect=500).status == 500


def test_a_wrong_media_type_fails(api, fake):
    fake.reply(200, b'%PDF', {'content-type': 'text/plain', 'cache-control': 'no-store'})
    with pytest.raises(ContractViolation, match='application/pdf'):
        api.call('getThingFile', path={'thing_id': 'x'})


def test_a_request_the_contract_does_not_allow_is_never_sent(api, fake):
    with pytest.raises(ContractViolation, match="undeclared field 'colour'"):
        api.call('createThing', json={'name': 'n', 'colour': 'red'})
    with pytest.raises(ContractViolation, match="'name' is a required property"):
        api.call('createThing', json={})
    with pytest.raises(ContractViolation, match='request body is required'):
        api.call('createThing')
    with pytest.raises(ContractViolation, match="query parameter 'nope' is not declared"):
        api.call('listThings', query={'nope': 1})
    with pytest.raises(ContractViolation, match="query parameter 'limit'"):
        api.call('listThings', query={'limit': 'ten'})
    with pytest.raises(ContractViolation, match='declares no request body'):
        api.call('getThing', path={'thing_id': 'x'}, json={})
    with pytest.raises(ContractViolation, match='path parameters'):
        api.call('getThing', path={})
    with pytest.raises(KeyError, match='not an operationId'):
        api.call('noSuchOperation')
    assert fake.requests == [], 'nothing reached the server'


def test_a_negative_call_sends_the_invalid_request_and_still_checks_the_reply(api, fake):
    fake.reply(422, {'detail': [{'loc': ['body', 'name'], 'msg': 'required'}], 'code': 'validation_error'})
    reply = api.call('createThing', json={'colour': 'red'}, negative=True, expect=422)
    assert reply.status == 422 and len(fake.requests) == 1
    fake.reply(422, {'detail': 'not a list but a sentence', 'code': 'made_up'})
    with pytest.raises(ContractViolation, match="error code 'made_up'"):
        api.call('createThing', json={'colour': 'red'}, negative=True, expect=422)


def test_query_values_are_sent_as_text(api, fake):
    fake.reply(200, [GOOD_THING])
    api.call('listThings', query={'limit': 3})
    assert fake.requests[-1].url.query == b'limit=3'


def test_raw_requests_can_expect_the_router_error(api, fake):
    fake.reply(404, {'detail': 'Not Found', 'code': 'route_not_found'})
    assert api.request('GET', '/api/nowhere', unrouted=True, expect=404).code == 'route_not_found'
    with pytest.raises(ContractViolation, match='no contract operation matches'):
        api.request('GET', '/api/nowhere')
    assert api.recorder.unrouted == {(404, 'route_not_found'): 2}, 'a refused exchange is recorded too'


def test_a_host_header_can_be_overridden_and_its_refusal_checked(api, fake):
    fake.reply(400, b'Invalid host header', {'content-type': 'text/plain; charset=utf-8'})
    reply = api.request('GET', '/api/things', headers={'Host': 'evil.example'}, host_rejection=True)
    assert reply.status == 400 and fake.requests[-1].headers['host'] == 'evil.example'
    fake.reply(200, [GOOD_THING])
    with pytest.raises(ContractViolation, match='untrusted Host header must be refused with 400'):
        api.request('GET', '/api/things', headers={'Host': 'evil.example'}, host_rejection=True)


def test_wait_for_job_polls_until_a_terminal_status(monkeypatch):
    from ..contract import Contract
    doc = tiny_contract().document
    job = {'type': 'object', 'required': ['id', 'status'], 'properties': {'id': {'type': 'string'}, 'status': {'type': 'string'}}}
    doc['paths']['/api/jobs'] = {'get': {'operationId': 'listJobs', 'parameters': [
        {'name': 'book_id', 'in': 'query', 'required': False, 'schema': {'type': 'string'}}],
        'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': {'type': 'array', 'items': job}}}}}}}
    statuses = iter(['queued', 'running', 'running', 'completed', 'completed'])

    def handler(request):
        return httpx.Response(200, headers=HEADERS, content=json.dumps([{'id': 'j1', 'status': next(statuses)}]).encode())

    client = Api('http://127.0.0.1:9', Contract(doc), Recorder(), transport=httpx.MockTransport(handler))
    assert client.wait_for_job('j1', interval=0)['status'] == 'completed'
    slow = Api('http://127.0.0.1:9', Contract(doc), Recorder(),
               transport=httpx.MockTransport(lambda r: httpx.Response(200, headers=HEADERS, content=b'[{"id":"j1","status":"running"}]')))
    with pytest.raises(AssertionError, match='did not finish'):
        slow.wait_for_job('j1', timeout=0.05, interval=0.01)
