"""The version handshake, when the contract has one: the `Bardic-Contract-Version` header on every `/api/`
response and the `contract` object of the status. Both are read from the contract file: a contract that does
not document them skips these tests."""
from __future__ import annotations

import re

import pytest

from .contract import Contract


@pytest.fixture()
def header_contract(contract: Contract) -> Contract:
    if contract.version_header is None:
        pytest.skip('this contract does not document a version header')
    return contract


@pytest.fixture()
def handshake_contract(contract: Contract) -> Contract:
    properties = contract.dereference(contract.document['components']['schemas']['Status']).get('properties', {})
    if 'contract' not in properties or contract.sha256 is None:
        pytest.skip('this contract has no Status.contract handshake (or was not loaded from a file)')
    return contract


def test_every_kind_of_response_carries_the_version_header(api, header_contract, txt_book):
    version = header_contract.version
    name = header_contract.version_header.lower()
    replies = [
        api.call('getStatus'),                                                                    # success
        api.call('getBook', path={'book_id': txt_book['id']}),
        api.call('getBook', path={'book_id': 'no-such-book'}, expect=404),                        # documented error
        api.call('listJobs', query={'active': 'perhaps'}, negative=True, expect=422),             # request validation
        api.call('createDemoBook', headers={'Origin': 'http://evil.example'}, expect=403),        # write guard
        api.request('GET', '/api/no-such-route', unrouted=True, expect=404),                      # router error
        api.request('GET', '/api/status', headers={'Host': 'evil.example'}, host_rejection=True),  # plain-text 400
    ]
    for reply in replies:
        assert reply.headers.get(name) == version, reply.summary()


def test_a_file_response_carries_the_version_header_too(api, header_contract, epub_book):
    reply = api.call('exportBookAnalysis', path={'book_id': epub_book['id']})
    assert reply.headers.get(header_contract.version_header.lower()) == header_contract.version
    partial = api.call('exportBookAnalysis', path={'book_id': epub_book['id']}, headers={'Range': 'bytes=0-3'}, expect=206)
    assert partial.headers.get(header_contract.version_header.lower()) == header_contract.version


def test_status_reports_the_contract_the_server_implements(api, handshake_contract):
    info = api.call('getStatus').json['contract']
    assert info['version'] == handshake_contract.version, 'the version of the contract file this suite was given'
    assert re.fullmatch(r'[0-9a-f]{64}', info['sha256'])
    assert info['sha256'] == handshake_contract.sha256, (
        'the SHA-256 of the exact bytes of the contract file: a server embeds the document it was built against')


def test_the_settings_response_is_the_status_and_carries_the_handshake(api, handshake_contract):
    assert api.call('updateSettings', json={}).json['contract'] == api.call('getStatus').json['contract']


def test_the_header_and_the_status_agree(api, handshake_contract):
    reply = api.call('getStatus')
    if handshake_contract.version_header:
        assert reply.headers[handshake_contract.version_header.lower()] == reply.json['contract']['version']
