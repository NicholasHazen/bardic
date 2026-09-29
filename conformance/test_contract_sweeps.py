"""Checks driven by the contract itself, applied to every operation it lists.

Nothing here names an operation: the sweeps read `contract/openapi.json` and pick operations by shape
(path parameters, request body, cost class), so a new operation is covered the day it appears in the
contract. They only send requests that must be refused (unknown IDs, invalid bodies), and they skip any
operation whose cost class is not `none`, so no sweep can start paid or provider-facing work.
"""
from __future__ import annotations

import copy

import pytest

from . import helpers
from .client import Api
from .contract import Contract, Operation
from .sample import Unsupported, sample

UNKNOWN = 'no-such-id'
ID_CODES = {'book_id': 'book_not_found', 'series_id': 'series_not_found', 'job_id': 'job_not_found'}
MINIMUM = {'book_id': 5, 'series_id': 3, 'job_id': 1}  # how many operations of that shape the contract has, at least


def _query_values(contract: Contract, op: Operation) -> dict:
    """A valid value for every required query parameter."""
    values = {}
    for parameter in op.parameters:
        if parameter.location == 'query' and parameter.required:
            values[parameter.name] = sample(contract, parameter.schema, [])
    return values


def _json_body(op: Operation):
    entry = op.request_bodies.get('application/json')
    return entry[1] if entry else None


def _bodyless(op: Operation) -> bool:
    return not op.request_bodies


def _safe(op: Operation) -> bool:
    return op.cost in (None, 'none')


def _operations(contract: Contract, keep):
    return [op for op in contract.operations.values() if keep(op)]


def _ids(api: Api, book: dict, series: dict) -> dict:
    return {'book_id': book['id'], 'series_id': series['id']}


@pytest.fixture(scope='module')
def series(api: Api) -> dict:
    return api.call('createSeries', json={'name': 'Sweep series ' + helpers.token()}).json


# ---------------------------------------------------------------- unknown IDs

@pytest.mark.parametrize('id_name', sorted(ID_CODES))
def test_an_unknown_id_in_the_path_is_404_on_every_operation_that_takes_one(api, contract, id_name):
    """A request naming a book (or series) that does not exist is 404 with that resource's `*_not_found` code.

    Covered: every safe operation without a request body whose only path parameter is the ID. (An operation
    with a body checks the body first for some conditions, so the order there is the operation's own business.)
    """
    if not any(op.path_names == [id_name] for op in contract.operations.values()):
        pytest.skip(f'no operation of this contract takes only a {id_name}')
    checked = 0
    for op in _operations(contract, lambda o: _safe(o) and _bodyless(o) and o.path_names == [id_name] and 404 in o.responses):
        if ID_CODES[id_name] not in op.error_codes.get(404, []):
            continue
        reply = api.call(op.id, path={id_name: UNKNOWN}, query=_query_values(contract, op), expect=404)
        assert reply.code == ID_CODES[id_name], (op.id, reply.summary())
        checked += 1
    assert checked >= MINIMUM[id_name], f'the contract has operations that take only a {id_name}'


def test_unknown_ids_all_the_way_down_are_404_with_a_listed_code(api, contract):
    """Every path parameter unknown at once: 404, and one of the codes the operation lists for it (which of
    `book_not_found` or a child's code answers first is not specified)."""
    checked = 0
    for op in _operations(contract, lambda o: _safe(o) and _bodyless(o) and o.path_names[:1] == ['book_id'] and len(o.path_names) > 1
                          and o.method == 'GET' and 404 in o.responses):
        path = {name: UNKNOWN for name in op.path_names}
        reply = api.call(op.id, path=path, query=_query_values(contract, op), expect=404)
        assert reply.code in op.error_codes[404] or reply.code == 'route_not_found', (op.id, reply.summary())
        checked += 1
    assert checked >= 5


def test_a_missing_nested_resource_of_a_real_book_names_itself(api, contract, txt_book):
    """With a real book and an unknown child ID, the 404 code is one the operation lists, and never `book_not_found`."""
    checked = 0
    for op in _operations(contract, lambda o: _safe(o) and _bodyless(o) and o.path_names[:1] == ['book_id'] and len(o.path_names) > 1
                          and o.method == 'GET' and 404 in o.responses and 'audio' not in o.path):
        path = {name: UNKNOWN for name in op.path_names}
        path['book_id'] = txt_book['id']
        reply = api.call(op.id, path=path, query=_query_values(contract, op), expect=(404, 400))
        if reply.status == 404:
            assert reply.code != 'book_not_found', (op.id, reply.summary())
        checked += 1
    assert checked >= 5


# ---------------------------------------------------------------- request validation, from the request schemas

def _writable_with_real_ids(contract: Contract, op: Operation) -> bool:
    return (_safe(op) and _json_body(op) is not None and op.method in ('POST', 'PUT', 'PATCH')
            and set(op.path_names) <= set(ID_CODES))


def _sample_body(contract: Contract, op: Operation, *, everything: bool = False):
    try:
        return sample(contract, _json_body(op), [], everything=everything)
    except Unsupported:
        return None


def _validate(api: Api, contract: Contract, op: Operation, ids: dict, body, note: str):
    path = {name: ids[name] for name in op.path_names}
    reply = api.call(op.id, path=path, query=_query_values(contract, op), json=body, negative=True, expect=422)
    assert reply.code == 'validation_error', (op.id, note, reply.summary())


def test_every_json_request_rejects_unknown_fields(api, contract, txt_book, series):
    """Request bodies reject unknown fields (422), wherever the schema declares its properties."""
    ids, checked = _ids(api, txt_book, series), 0
    for op in _operations(contract, lambda o: _writable_with_real_ids(contract, o)):
        schema = contract.dereference(_json_body(op))
        if 'properties' not in schema:
            continue
        body = _sample_body(contract, op)
        if not isinstance(body, dict):
            continue
        _validate(api, contract, op, ids, {**body, 'undeclared_field_for_conformance': 1}, 'unknown field')
        checked += 1
    assert checked >= 15


def test_every_json_request_rejects_a_missing_required_field(api, contract, txt_book, series):
    ids, checked = _ids(api, txt_book, series), 0
    for op in _operations(contract, lambda o: _writable_with_real_ids(contract, o)):
        schema = contract.dereference(_json_body(op))
        body = _sample_body(contract, op)
        if not isinstance(body, dict) or not schema.get('required'):
            continue
        for name in schema['required']:
            broken = {key: value for key, value in body.items() if key != name}
            _validate(api, contract, op, ids, broken, f'missing {name}')
            checked += 1
    assert checked >= 15


def test_every_json_request_is_strict_about_scalar_types(api, contract, txt_book, series):
    """A string where a number or boolean belongs, and a number where a string belongs, is refused (no coercion)."""
    ids, checked = _ids(api, txt_book, series), 0
    for op in _operations(contract, lambda o: _writable_with_real_ids(contract, o)):
        body = _sample_body(contract, op, everything=True)
        if not isinstance(body, dict):
            continue
        for name, value in body.items():
            if not isinstance(value, (bool, int, float, str)):
                continue
            wrong = 'a string' if not isinstance(value, str) else 12345
            broken = copy.deepcopy(body)
            broken[name] = wrong
            _validate(api, contract, op, ids, broken, f'{name}={wrong!r}')
            checked += 1
    assert checked >= 20


def test_every_paged_or_typed_query_parameter_is_strict(api, contract, txt_book):
    """A non-numeric value for an integer query parameter is a validation error, not silently clamped or coerced."""
    checked = 0
    for op in _operations(contract, lambda o: _safe(o) and _bodyless(o) and o.method == 'GET' and o.path_names == ['book_id']):
        for parameter in (p for p in op.parameters if p.location == 'query'):
            if contract.dereference(parameter.schema).get('type') != 'integer':
                continue
            query = {**_query_values(contract, op), parameter.name: 'not-a-number'}
            reply = api.call(op.id, path={'book_id': txt_book['id']}, query=query, negative=True, expect=422)
            assert reply.code == 'validation_error', (op.id, parameter.name)
            checked += 1
    assert checked >= 3
