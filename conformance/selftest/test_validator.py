"""The response checker must fail on every kind of deviation it exists to catch, and pass on a correct exchange."""
from __future__ import annotations

import json

import pytest

from .tiny import GOOD_THING, NO_STORE, contract as tiny_contract

C = tiny_contract()


def check(method, path, status, body, headers=None, **flags):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return C.response_problems(method, path, status, NO_STORE if headers is None else headers, raw, **flags)


def ok(*args, **kwargs):
    problems = check(*args, **kwargs)
    assert problems == [], problems


def fails(fragment, *args, **kwargs):
    problems = check(*args, **kwargs)
    assert problems, 'the checker accepted a response it must refuse'
    assert any(fragment in problem for problem in problems), (fragment, problems)
    return problems


# ---------------------------------------------------------------- a correct body passes

def test_a_correct_body_passes():
    ok('GET', '/api/things/t1', 200, GOOD_THING)
    ok('GET', '/api/things', 200, [GOOD_THING, {**GOOD_THING, 'owner': {'name': 'n', 'address': {'city': 'c'}}, 'tags': ['x'],
                                                 'labels': {'any': 'string'}, 'kind': 'a', 'ratio': 1}])


def test_optional_fields_may_be_null_where_the_schema_allows_it():
    ok('GET', '/api/things/t1', 200, {**GOOD_THING, 'owner': None})


# ---------------------------------------------------------------- closed objects

def test_an_undeclared_top_level_field_fails():
    fails("$.surprise: undeclared field 'surprise'", 'GET', '/api/things/t1', 200, {**GOOD_THING, 'surprise': 1})


def test_an_undeclared_nested_field_fails_with_its_json_path():
    body = {**GOOD_THING, 'owner': {'name': 'n', 'address': {'city': 'c', 'zip': '1'}}}
    fails("$.owner.address.zip: undeclared field 'zip'", 'GET', '/api/things/t1', 200, body)


def test_an_undeclared_field_inside_an_array_item_fails_with_an_index():
    fails("$[1].extra: undeclared field 'extra'", 'GET', '/api/things', 200, [GOOD_THING, {**GOOD_THING, 'extra': True}])


def test_free_form_maps_stay_open():
    ok('GET', '/api/things/t1', 200, {**GOOD_THING, 'labels': {'a': 'x', 'b': 'y'}, 'anything': {'deep': {'er': [1, 2]}},
                                       'freeform': {'whatever': 1}})


def test_a_map_still_checks_the_type_of_its_values():
    fails("$.labels.a", 'GET', '/api/things/t1', 200, {**GOOD_THING, 'labels': {'a': 5}})


# ---------------------------------------------------------------- required and types

def test_a_missing_required_field_fails():
    body = dict(GOOD_THING)
    del body['size']
    fails("'size' is a required property", 'GET', '/api/things/t1', 200, body)


def test_a_missing_required_field_in_a_nested_object_fails():
    fails("'city' is a required property", 'GET', '/api/things/t1', 200, {**GOOD_THING, 'owner': {'name': 'n', 'address': {}}})


@pytest.mark.parametrize('field, value', [
    ('size', '3'),          # string where an integer belongs
    ('size', True),         # a boolean is not an integer
    ('size', 3.0),          # a float is not an integer for a typed client
    ('size', 3.5),
    ('ok', 'true'),         # string where a boolean belongs
    ('ok', 1),
    ('id', 7),
    ('ratio', '1.5'),
    ('ratio', True),
    ('tags', 'a'),
    ('tags', [1]),
    ('kind', 'c'),          # not in the enum
    ('owner', 'someone'),
])
def test_a_wrong_type_fails(field, value):
    fails(f'$.{field}', 'GET', '/api/things/t1', 200, {**GOOD_THING, field: value})


def test_null_is_not_accepted_where_it_is_not_declared():
    fails('$.id', 'GET', '/api/things/t1', 200, {**GOOD_THING, 'id': None})


def test_number_accepts_integers_and_floats():
    ok('GET', '/api/things/t1', 200, {**GOOD_THING, 'ratio': 2})
    ok('GET', '/api/things/t1', 200, {**GOOD_THING, 'ratio': 2.5})


def test_a_body_that_is_not_json_fails():
    fails('not valid JSON', 'GET', '/api/things/t1', 200, b'{"id": ')
    fails('not valid JSON', 'GET', '/api/things/t1', 200, b'{"id": "a", "size": NaN, "ok": true}')


def test_an_array_where_an_object_belongs_fails():
    fails('is not of type', 'GET', '/api/things/t1', 200, [GOOD_THING])


# ---------------------------------------------------------------- statuses

def test_an_undocumented_error_status_fails():
    fails('undocumented error status 409', 'GET', '/api/things/t1', 409, {'detail': 'x', 'code': 'conflict'})
    fails('undocumented error status 418', 'GET', '/api/things/t1', 418, {'detail': 'x', 'code': 'teapot'})


def test_an_undocumented_success_status_fails():
    fails('undocumented success status 201', 'POST', '/api/things', 201, GOOD_THING)
    fails('undocumented success status 204', 'GET', '/api/things/t1', 204, b'')


def test_206_and_304_are_accepted_only_where_documented():
    part = {'content-type': 'application/pdf', 'cache-control': 'no-store', 'content-range': 'bytes 0-3/100'}
    ok('GET', '/api/things/t1/file', 206, b'part', part)
    fails('carries Content-Range', 'GET', '/api/things/t1/file', 206, b'part', {**part, 'content-range': 'items 0-3/100'})
    fails('carries Content-Range', 'GET', '/api/things/t1/file', 206, b'part', {k: v for k, v in part.items() if k != 'content-range'})
    fails('the body has 3 bytes', 'GET', '/api/things/t1/file', 206, b'par', part)
    fails('206 is not documented', 'GET', '/api/things/t1', 206, GOOD_THING)
    ok('GET', '/api/things/t1/picture', 304, b'', {'etag': '"x"'})
    fails('304 is not documented', 'GET', '/api/things/t1', 304, b'')
    fails('304 response has a body', 'GET', '/api/things/t1/picture', 304, b'oops', {'etag': '"x"'})


def test_a_redirect_fails():
    fails('not documented', 'GET', '/api/things/t1', 302, b'', {'location': '/elsewhere', 'cache-control': 'no-store'})


def test_the_write_guard_403_is_accepted_on_writes_only():
    body = {'detail': 'Cross-origin writes are not allowed', 'code': 'cross_origin_write'}
    ok('POST', '/api/things', 403, body)
    fails('undocumented error status 403', 'GET', '/api/things/t1', 403, body)


def test_request_validation_422_is_accepted_wherever_it_comes_from():
    body = {'detail': [{'loc': ['body', 'name'], 'msg': 'Field required'}], 'code': 'validation_error'}
    ok('POST', '/api/things', 422, body)
    ok('GET', '/api/things/latest', 422, body)  # not documented for this operation, but request validation is universal


def test_a_success_status_body_that_is_an_error_object_fails():
    fails('undeclared field', 'GET', '/api/things/t1', 200, {'detail': 'nope', 'code': 'x'})


# ---------------------------------------------------------------- error bodies

def test_a_documented_error_passes():
    ok('GET', '/api/things/t1', 404, {'detail': 'No such thing', 'code': 'thing_not_found'})


def test_an_unlisted_error_code_fails():
    fails("error code 'weird_code' is not documented for status 404", 'GET', '/api/things/t1', 404,
          {'detail': 'x', 'code': 'weird_code'})
    fails("error code 'name_taken'", 'GET', '/api/things/t1', 404, {'detail': 'x', 'code': 'name_taken'})


def test_a_code_listed_for_another_status_fails():
    fails("error code 'thing_not_found' is not documented for status 500", 'GET', '/api/things/t1', 500,
          {'detail': 'x', 'code': 'thing_not_found'})


def test_global_codes_are_accepted_only_at_their_statuses():
    ok('GET', '/api/things/t1', 500, {'detail': 'x', 'code': 'internal_error'})
    ok('GET', '/api/things/t1', 422, {'detail': [{'loc': ['query'], 'msg': 'm'}], 'code': 'validation_error'})
    fails("error code 'validation_error' is not documented for status 404", 'GET', '/api/things/t1', 404,
          {'detail': 'x', 'code': 'validation_error'})


def test_an_error_body_without_a_code_or_detail_fails():
    fails("'code' is a required property", 'GET', '/api/things/t1', 404, {'detail': 'x'})
    fails("'detail' is a required property", 'GET', '/api/things/t1', 404, {'code': 'thing_not_found'})


def test_an_error_body_with_an_extra_field_fails():
    fails("$.hint: undeclared field 'hint'", 'GET', '/api/things/t1', 404, {'detail': 'x', 'code': 'thing_not_found', 'hint': 'y'})


def test_a_non_json_error_fails():
    fails('not application/json', 'GET', '/api/things/t1', 404, b'Not Found', {'content-type': 'text/plain', 'cache-control': 'no-store'})


def test_a_416_is_the_json_error_and_carries_a_content_range():
    error = {'detail': 'The requested range cannot be satisfied.', 'code': 'range_not_satisfiable'}
    headers = {**NO_STORE, 'content-range': 'bytes */10'}
    ok('GET', '/api/things/t1/file', 416, error, headers)
    fails('not application/json', 'GET', '/api/things/t1/file', 416, b'', {**headers, 'content-type': 'text/plain'})
    fails('Content-Range', 'GET', '/api/things/t1/file', 416, error, NO_STORE)
    fails("error code 'other_code' is not documented", 'GET', '/api/things/t1/file', 416, {**error, 'code': 'other_code'}, headers)
    fails('undocumented error status 416', 'GET', '/api/things/t1', 416, error, headers)


# ---------------------------------------------------------------- media types

def test_a_binary_response_needs_the_documented_media_type():
    ok('GET', '/api/things/t1/file', 200, b'%PDF', {'content-type': 'application/pdf', 'cache-control': 'no-store'})
    ok('GET', '/api/things/t1/file', 200, b'%PDF', {'content-type': 'application/pdf; charset=binary', 'cache-control': 'no-store'})
    fails("media type 'audio/wav', the contract says application/pdf", 'GET', '/api/things/t1/file', 200, b'RIFF',
          {'content-type': 'audio/wav', 'cache-control': 'no-store'})
    fails('the contract says application/pdf', 'GET', '/api/things/t1/file', 200, b'{}',
          {'content-type': 'application/json', 'cache-control': 'no-store'})


def test_a_json_response_needs_a_json_media_type():
    fails("media type 'text/html', the contract says application/json", 'GET', '/api/things/t1', 200, json.dumps(GOOD_THING).encode(),
          {'content-type': 'text/html', 'cache-control': 'no-store'})
    ok('GET', '/api/things/t1', 200, GOOD_THING, {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'})


# ---------------------------------------------------------------- routing

def test_a_path_matching_no_operation_fails():
    fails('no contract operation matches', 'GET', '/api/no-such', 200, {})
    fails('no contract operation matches', 'DELETE', '/api/things/t1', 200, {})


def test_route_not_found_is_accepted_only_when_the_request_expected_no_operation():
    body = {'detail': 'Not Found', 'code': 'route_not_found'}
    ok('GET', '/api/no-such', 404, body, unrouted=True)
    ok('DELETE', '/api/things/t1', 405, body, unrouted=True)
    fails('no contract operation matches', 'GET', '/api/no-such', 404, body)
    fails('route_not_found', 'GET', '/api/no-such', 404, {'detail': 'Not Found', 'code': 'not_found'}, unrouted=True)
    fails('must answer 404', 'GET', '/api/no-such', 200, body, unrouted=True)
    fails('expected no operation to match', 'GET', '/api/things/t1', 404, body, unrouted=True)


def test_a_literal_segment_beats_a_variable_one():
    assert C.match('GET', '/api/things/latest').operation.id == 'getLatestThing'
    assert C.match('GET', '/api/things/other').operation.id == 'getThing'
    assert C.match('GET', '/api/things/other').path_params == {'thing_id': 'other'}
    assert C.match('POST', '/api/things/latest') is None
    assert C.match('GET', '/api/things/a/b/c') is None


def test_untrusted_host_refusal_is_plain_text_400():
    text = {'content-type': 'text/plain; charset=utf-8'}
    ok('GET', '/api/things', 400, b'Invalid host header', text, host_rejection=True)
    fails('plain text', 'GET', '/api/things', 400, b'{"detail":"Invalid host header"}', {'content-type': 'application/json'}, host_rejection=True)
    fails('must be refused with 400', 'GET', '/api/things', 200, b'Invalid host header', text, host_rejection=True)
    fails('undocumented error status 400', 'GET', '/api/things', 400, b'Invalid host header', text)


# ---------------------------------------------------------------- headers

def test_every_api_response_needs_no_store():
    fails('Cache-Control: no-store', 'GET', '/api/things/t1', 200, GOOD_THING, {'content-type': 'application/json'})
    fails('Cache-Control: no-store', 'GET', '/api/things/t1', 404, {'detail': 'x', 'code': 'thing_not_found'},
          {'content-type': 'application/json', 'cache-control': 'max-age=60'})
    ok('GET', '/api/things/t1', 200, GOOD_THING, {'content-type': 'application/json', 'cache-control': 'no-cache, no-store, must-revalidate'})


def test_a_conditional_file_manages_its_own_caching_on_success_only():
    ok('GET', '/api/things/t1/picture', 200, b'\xff\xd8', {'content-type': 'image/jpeg', 'cache-control': 'private, max-age=31536000, immutable'})
    ok('GET', '/api/things/t1/picture', 304, b'', {'cache-control': 'private, no-cache'})
    fails('Cache-Control: no-store', 'GET', '/api/things/t1/picture', 404, {'detail': 'x', 'code': 'thing_not_found'},
          {'content-type': 'application/json', 'cache-control': 'private, no-cache'})


def test_a_skipped_header_check_is_skipped_and_nothing_else():
    body = {'detail': 'x', 'code': 'cross_origin_write'}
    bare = {'content-type': 'application/json'}
    assert C.response_problems('POST', '/api/things', 403, bare, json.dumps(body).encode(), skip=('cache-control',)) == []
    assert C.response_problems('POST', '/api/things', 403, bare, json.dumps({**body, 'code': 'other'}).encode(), skip=('cache-control',))


def test_a_contract_that_documents_a_version_header_requires_it():
    versioned = tiny_contract(info={'description': 'Every response carries `Bardic-Contract-Version`.\n'})
    assert versioned.version_header == 'Bardic-Contract-Version'
    raw = json.dumps(GOOD_THING).encode()
    bad = versioned.response_problems('GET', '/api/things/t1', 200, NO_STORE, raw)
    assert any('Bardic-Contract-Version header is None' in problem and '9.9.9' in problem for problem in bad), bad
    wrong = versioned.response_problems('GET', '/api/things/t1', 200, {**NO_STORE, 'Bardic-Contract-Version': '0.1.0'}, raw)
    assert any("'0.1.0'" in problem for problem in wrong)
    assert versioned.response_problems('GET', '/api/things/t1', 200, {**NO_STORE, 'Bardic-Contract-Version': '9.9.9'}, raw) == []
    assert C.version_header is None, 'a contract that does not mention the header does not require it'


def test_global_codes_are_read_from_the_contract_introduction():
    assert C.global_codes['cross_origin_write'] == (403,) and C.global_codes['route_not_found'] == (404, 405)
    extended = tiny_contract(info={'description': 'global codes:\n  - `rate_limited` (429): slow down.\n'})
    assert extended.global_codes['rate_limited'] == (429,)
