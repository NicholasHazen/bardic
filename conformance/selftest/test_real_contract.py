"""The checker against every schema of the real contract file: it must accept a valid instance of each
response and refuse an undeclared field at every closed object, including nested ones."""
from __future__ import annotations

import copy
import json


from ..contract import Contract, json_path
from ..coverage import load_needs_provider
from ..sample import Unsupported, sample

NO_STORE = {'content-type': 'application/json', 'cache-control': 'no-store'}


def _headers(contract):
    headers = dict(NO_STORE)
    if contract.version_header:
        headers[contract.version_header] = contract.version
    return headers


def _at(instance, path):
    for step in path:
        instance = instance[step]
    return instance


def test_the_contract_loads_and_every_operation_is_indexed(contract: Contract):
    assert contract.version and contract.operations
    total = sum(1 for item in contract.document['paths'].values() for method in item if method in ('get', 'post', 'put', 'patch', 'delete'))
    assert len(contract.operations) == total, 'every operation of every path has a unique operationId'
    for op in contract.operations.values():
        assert 200 in op.responses, f'{op.id} documents no 200'
        assert op.json_response is not None or op.media is not None, f'{op.id} has neither a JSON nor a binary success'


def test_every_operation_is_found_by_its_own_path(contract: Contract):
    for op in contract.operations.values():
        concrete = contract.url_path(op, {name: f'{name}-value' for name in op.path_names})
        found = contract.match(op.method, concrete)
        assert found is not None and found.operation.id == op.id, f'{op} resolved to {found}'
        assert found.path_params == {name: f'{name}-value' for name in op.path_names}


def test_every_error_status_documents_its_codes(contract: Contract):
    for op in contract.operations.values():
        for status, spec in op.responses.items():
            if status >= 400:
                assert 'x-bardic-error-codes' in spec, f'{op.id} {status} lists no error codes'
                assert spec['x-bardic-error-codes'], f'{op.id} {status} lists an empty set of codes'


def test_the_global_codes_of_the_introduction_are_understood(contract: Contract):
    assert {'validation_error', 'cross_origin_write', 'internal_error', 'route_not_found'} <= set(contract.global_codes)
    assert contract.global_codes['validation_error'] == (422,)
    assert set(contract.global_codes['route_not_found']) == {404, 405}


def test_a_valid_instance_of_every_response_passes(contract: Contract):
    checked, skipped = 0, []
    for op in contract.operations.values():
        if op.json_response is None:
            continue
        closed: list = []
        try:
            instance = sample(contract, op.json_response, closed)
        except Unsupported as reason:
            skipped.append((op.id, str(reason)))
            continue
        problems = contract.response_problems(op.method, contract.url_path(op, {n: 'x' for n in op.path_names}), 200,
                                              _headers(contract), json.dumps(instance).encode())
        assert problems == [], (op.id, problems)
        checked += 1
    assert checked >= 0.9 * sum(1 for op in contract.operations.values() if op.json_response is not None), skipped


def test_an_undeclared_field_is_refused_at_every_closed_object_of_every_response(contract: Contract):
    injected = 0
    for op in contract.operations.values():
        if op.json_response is None:
            continue
        closed: list = []
        try:
            instance = sample(contract, op.json_response, closed)
        except Unsupported:
            continue
        concrete = contract.url_path(op, {n: 'x' for n in op.path_names})
        for path in closed:
            broken = copy.deepcopy(instance)
            _at(broken, path)['__undeclared__'] = 1
            problems = contract.response_problems(op.method, concrete, 200, _headers(contract), json.dumps(broken).encode())
            expected = f"{json_path(path + ('__undeclared__',))}: undeclared field '__undeclared__'"
            assert any(expected in problem for problem in problems), (op.id, expected, problems)
            injected += 1
    # Every closed object is checked; the floor only guards against the sampler silently skipping most of them. A `Job`
    # is one union branch per sample now, not one 50-field object, so the count is lower than it was (about 500).
    assert injected > 400, 'the real contract has hundreds of closed objects'


def test_a_wrong_type_is_refused_in_every_response(contract: Contract):
    """Replace the first scalar of a sampled instance with a value of the wrong type."""
    refused = 0
    for op in contract.operations.values():
        if op.json_response is None:
            continue
        try:
            instance = sample(contract, op.json_response, [])
        except Unsupported:
            continue
        spot = _first_scalar(instance)
        if spot is None:
            continue
        path, value = spot
        broken = copy.deepcopy(instance)
        parent = _at(broken, path[:-1]) if path else None
        wrong = {bool: 'a string', int: 'a string', float: 'a string', str: 12345}[type(value)]
        if parent is None:
            continue
        parent[path[-1]] = wrong
        problems = contract.response_problems(op.method, contract.url_path(op, {n: 'x' for n in op.path_names}), 200,
                                              _headers(contract), json.dumps(broken).encode())
        assert problems, (op.id, json_path(path), 'accepted a value of the wrong type')
        refused += 1
    assert refused > 40


def _first_scalar(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            found = _first_scalar(value, path + (key,))
            if found:
                return found
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found = _first_scalar(value, path + (index,))
            if found:
                return found
    elif isinstance(node, (bool, int, float, str)):
        return path, node
    return None


def test_request_bodies_accept_valid_samples_and_refuse_unknown_fields(contract: Contract):
    valid = refused = 0
    for op in contract.operations.values():
        body = op.request_bodies.get('application/json')
        if body is None:
            continue
        closed: list = []
        try:
            instance = sample(contract, body[1], closed, everything=False)
        except Unsupported:
            continue
        path_params = {name: 'x' for name in op.path_names}
        assert contract.request_problems(op, path_params=path_params, body=instance) == [], op.id
        valid += 1
        if () in closed:
            broken = dict(instance, __undeclared__=1)
            assert contract.request_problems(op, path_params=path_params, body=broken), (
                f'{op.id}: a request body with an undeclared field was accepted')
            refused += 1
    assert valid > 20 and refused > 15


def test_query_parameters_are_checked_by_type(contract: Contract):
    checked = 0
    for op in contract.operations.values():
        for parameter in (p for p in op.parameters if p.location == 'query'):
            kind = contract.dereference(parameter.schema).get('type')
            if kind == 'integer':
                path_params = {name: 'x' for name in op.path_names}
                required = {p.name: 1 for p in op.parameters if p.location == 'query' and p.required and p.name != parameter.name}
                assert contract.request_problems(op, path_params=path_params, query={**required, parameter.name: 'not a number'}), (
                    op.id, parameter.name)
                checked += 1
    assert checked >= 3


def test_needs_provider_lists_only_operations_the_contract_has(contract: Contract):
    listed = load_needs_provider()
    assert listed, 'the allowlist is not empty'
    unknown = sorted(set(listed) - set(contract.operations))
    assert not unknown, f'needs_provider.txt names operations that do not exist: {unknown}'
    assert all(listed.values()), 'every entry says why it needs a provider'
