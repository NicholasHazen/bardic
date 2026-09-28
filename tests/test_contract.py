"""The published contract (contract/) is complete, current and self-consistent."""
import json

import pytest
from fastapi.routing import APIRoute
from pydantic import Field

from bardic.apispec import match, validate_response
from bardic.apispec.__main__ import CONTRACT, ContractError, generate, record_version, stale
from bardic.apispec.base import View
from bardic.apispec.spec import extras, registry
from bardic.app import create_app


def test_contract_files_are_current():
    try:
        files = generate()
    except ContractError as error:
        pytest.fail(str(error))
    changed = stale(files)
    assert not changed, (f'contract/{changed} is stale. Run `uv run --frozen python -m bardic.apispec` and review the '
                         'diff (docs/API-WORKFLOW.md).')


def published() -> dict:
    return json.loads((CONTRACT / 'openapi.json').read_text(encoding='utf-8'))


def test_every_operation_is_described():
    schema = published()
    undocumented = [f'{method.upper()} {path}' for path, methods in schema['paths'].items()
                    for method, operation in methods.items() if operation.get('x-bardic-undocumented')]
    assert not undocumented, f'Describe these operations in bardic/apispec/: {undocumented}'
    ops, _ = registry()
    in_schema = {(method.upper(), path) for path, methods in schema['paths'].items() for method in methods}
    assert set(ops) == in_schema, f'Entries for operations that do not exist: {sorted(set(ops) - in_schema)}'


def test_no_api_route_is_hidden_from_the_contract(tmp_path):
    hidden = [route.path for route in create_app(tmp_path).routes
              if isinstance(route, APIRoute) and route.path.startswith('/api/') and not route.include_in_schema]
    assert not hidden, f'Every /api route must be in the contract: {hidden}'


def _undescribed(schema: dict) -> list[str]:
    missing = []
    for path, methods in schema['paths'].items():
        for method, operation in methods.items():
            missing += [f'parameter {parameter["name"]} of {method.upper()} {path}'
                        for parameter in operation.get('parameters', []) if not parameter.get('description')]
    for name, component in schema['components']['schemas'].items():
        if not component.get('description'):
            missing.append(f'schema {name}')
        missing += [f'field {name}.{field}' for field, spec in component.get('properties', {}).items()
                    if not spec.get('description') and set(spec) != {'$ref'}]
    return missing


def test_every_schema_field_and_parameter_is_described():
    missing = _undescribed(published())
    assert not missing, ('Describe these in bardic/apispec/ (Field(description=...), REQUEST_DOCS or params):\n  '
                         + '\n  '.join(missing))


def test_a_contract_change_needs_a_new_changelog_version():
    changelog = '# Contract changelog\n\n## 0.2.0 — 2026-10-01\n\n- Added a field.\n'
    recorded = record_version(changelog, '0.2.0', '{"a": 1}\n')
    assert '<!-- contract-sha256: ' in recorded
    assert record_version(recorded, '0.2.0', '{"a": 1}\n') == recorded
    with pytest.raises(ContractError, match='changed after version 0.2.0'):
        record_version(recorded, '0.2.0', '{"a": 2}\n')
    with pytest.raises(ContractError, match='0.2.1'):
        record_version(recorded, '0.2.1', '{"a": 2}\n')


def test_literal_segments_win_over_parameters():
    assert match('POST', '/api/voices/drafts').id != match('PATCH', '/api/voices/v_1').id
    assert match('POST', '/api/voices/defaults').path == '/api/voices/defaults'
    assert match('GET', '/api/nowhere') is None


def test_statuses_types_and_unknown_paths_are_checked():
    status = match('GET', '/api/status')
    assert status is not None
    assert validate_response('GET', '/api/status', 201, 'application/json', b'{}')[0].endswith('undocumented success status 201')
    assert 'undocumented error status 403' in validate_response('GET', '/api/status', 403, 'application/json', b'{"detail":"x"}')[0]
    assert validate_response('POST', '/api/demo', 403, 'application/json', b'{"detail":"Cross-origin writes are not allowed"}') == []
    assert 'undocumented error status 422' in validate_response('GET', '/api/status', 422, 'application/json', b'{"detail":"x"}')[0]
    assert validate_response('GET', '/api/nowhere', 404, 'application/json', b'{"detail":"Not Found"}') == []
    assert 'no contract operation' in validate_response('GET', '/api/nowhere', 200, 'application/json', b'{}')[0]
    jobs = validate_response('GET', '/api/jobs', 200, 'application/json',
                             b'[{"id":"j","book_id":"b","kind":"render","status":"queued","progress":"1","total":0,'
                             b'"message":"","error":null,"created_at":"t","updated_at":"t","cancel_requested":false}]')
    assert jobs and 'progress' in jobs[0], 'a string where the contract says integer must fail'


class Leaf(View):
    name: str = Field(description='Name.')


class Tree(View):
    leaves: list[Leaf]
    by_key: dict[str, Leaf]


def test_undeclared_fields_are_found_at_any_depth():
    value = Tree.model_validate({'leaves': [{'name': 'a', 'secret': 1}, {'name': 'b'}],
                                 'by_key': {'x': {'name': 'c', 'hidden': True}}, 'top': 0})
    assert extras(value) == ['$.by_key{}.hidden', '$.leaves[].secret', '$.top']
