"""Transport and security conventions of the contract introduction: Host check, write guard,
caching header, router errors and request validation."""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from .client import Api

UNTRUSTED_HOSTS = ('evil.example', 'evil.example:{port}', '127.0.0.1.evil.example', 'localhost.evil.example:{port}',
                   'example.com')


# A refused write is a response like any other, and the contract asks for Cache-Control: no-store on it.
# The guard tests below check the refusal itself; test_api_responses_are_never_cached checks the header.
CACHE_TESTED_ELSEWHERE = ('cache-control',)


def _port(api: Api) -> int:
    return urlsplit(api.base_url).port


def _book_ids(api: Api) -> set[str]:
    return {book['id'] for book in api.call('listBooks').json}


# ---------------------------------------------------------------- Host header

@pytest.mark.parametrize('method, path', [('GET', '/api/status'), ('POST', '/api/demo'), ('GET', '/api/no-such-route'),
                                          ('PATCH', '/api/books/x/metadata'), ('GET', '/')])
def test_untrusted_host_is_refused_with_plain_text_before_any_route_runs(api, method, path):
    for pattern in UNTRUSTED_HOSTS:
        host = pattern.format(port=_port(api))
        reply = api.request(method, path, headers={'Host': host}, host_rejection=True)
        assert reply.status == 400 and reply.text == 'Invalid host header', host
        assert reply.content_type.startswith('text/plain'), host


def test_refused_host_changes_nothing(api):
    before = _book_ids(api)
    api.request('POST', '/api/demo', headers={'Host': 'evil.example'}, host_rejection=True)
    assert _book_ids(api) == before, 'a request with an untrusted Host must not reach the operation'


@pytest.mark.parametrize('host', ['{loopback}', 'localhost:{port}'])
def test_loopback_names_are_trusted(api, host):
    value = host.format(port=_port(api), loopback=urlsplit(api.base_url).netloc)
    reply = api.call('getStatus', headers={'Host': value})
    assert reply.status == 200


# ---------------------------------------------------------------- write guard

CROSS_ORIGIN_HEADERS = (
    {'Origin': 'http://evil.example'},
    {'Origin': 'https://evil.example:{port}'},
    {'Origin': 'http://127.0.0.1:1'},  # same host, other port: a different origin
    {'Origin': 'null'},
    {'Sec-Fetch-Site': 'cross-site'},
    {'Origin': 'http://{netloc}', 'Sec-Fetch-Site': 'cross-site'},
)


def _guard_headers(api: Api, template: dict) -> dict:
    return {name: value.format(port=_port(api), netloc=urlsplit(api.base_url).netloc) for name, value in template.items()}


@pytest.mark.parametrize('template', CROSS_ORIGIN_HEADERS, ids=lambda t: ','.join(f'{k}={v}' for k, v in t.items()))
def test_cross_origin_write_is_refused_and_does_nothing(api, template):
    before = _book_ids(api)
    reply = api.call('createDemoBook', headers=_guard_headers(api, template), expect=403, skip=CACHE_TESTED_ELSEWHERE)
    assert reply.code == 'cross_origin_write'
    assert _book_ids(api) == before, 'a refused write must not create a book'


@pytest.mark.parametrize('operation, kwargs', [
    ('updateSettings', {'json': {}}),
    ('updateBookMetadata', {'path': {'book_id': 'no-such-book'}, 'json': {'title': 'Changed'}}),
    ('deletePronunciation', {'path': {'book_id': 'no-such-book', 'entry_id': 'no-such-entry'}}),
    ('putSeriesVolume', {'path': {'series_id': 'no-such-series'}, 'json': {'position': 1, 'title': 'One'}}),
    ('cancelJob', {'path': {'job_id': 'no-such-job'}}),
], ids=lambda value: value if isinstance(value, str) else '')
def test_write_guard_applies_to_every_write_method_before_lookup(api, operation, kwargs):
    """PATCH, PUT, DELETE and POST alike: the guard answers 403, not 404 or 422."""
    reply = api.call(operation, headers={'Origin': 'http://evil.example'}, expect=403, skip=CACHE_TESTED_ELSEWHERE, **kwargs)
    assert reply.code == 'cross_origin_write'


def test_same_origin_and_originless_writes_are_accepted(api):
    netloc = urlsplit(api.base_url).netloc
    api.call('updateSettings', json={}, headers={'Origin': f'http://{netloc}'})
    api.call('updateSettings', json={}, headers={'Sec-Fetch-Site': 'same-origin'})
    api.call('updateSettings', json={})  # no Origin: a command-line client


def test_reads_are_not_subject_to_the_write_guard(api):
    api.call('getStatus', headers={'Origin': 'http://evil.example', 'Sec-Fetch-Site': 'cross-site'})


# ---------------------------------------------------------------- caching header

def test_api_responses_are_never_cached(api):
    """Every /api/ response carries `Cache-Control: no-store`: successes, errors, router errors, the write guard."""
    replies = [
        api.call('getStatus'),
        api.call('getBook', path={'book_id': 'no-such-book'}, expect=404),
        api.call('listJobs', query={'active': 'perhaps'}, negative=True, expect=422),
        api.request('GET', '/api/no-such-route', unrouted=True, expect=404),
        api.call('createDemoBook', headers={'Origin': 'http://evil.example'}, expect=403, skip=CACHE_TESTED_ELSEWHERE),
    ]
    for reply in replies:
        assert 'no-store' in reply.headers.get('cache-control', '').lower(), reply.summary()


# ---------------------------------------------------------------- router errors

def test_unknown_route_is_404_route_not_found(api):
    for path in ('/api/no-such-route', '/api/books/some-id/no-such-child', '/api/nothing/here/at/all'):
        reply = api.request('GET', path, unrouted=True)
        assert reply.status == 404 and reply.code == 'route_not_found', reply.summary()
        assert isinstance(reply.json['detail'], str)


def test_unknown_route_with_a_write_method_is_route_not_found(api):
    """404, or 405 where the router treats the path as known for another method."""
    for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        reply = api.request(method, '/api/no-such-route', unrouted=True)
        assert reply.status in (404, 405) and reply.code == 'route_not_found', reply.summary()


def test_wrong_method_on_a_known_path_is_route_not_found(api):
    for method, path in (('DELETE', '/api/status'), ('PUT', '/api/library'), ('POST', '/api/jobs')):
        reply = api.request(method, path, unrouted=True)
        assert reply.status in (404, 405) and reply.code == 'route_not_found', reply.summary()


# ---------------------------------------------------------------- request validation

def _assert_validation_error(reply, *, issues: bool = True):
    """422 `validation_error`; `detail` lists the issues (the diagnostics POST never echoes input: a sentence)."""
    assert reply.status == 422 and reply.code == 'validation_error', reply.summary()
    detail = reply.json['detail']
    if issues:
        assert isinstance(detail, list) and detail, '422 request validation lists its issues in `detail`'
        assert all(isinstance(issue['loc'], list) and isinstance(issue['msg'], str) for issue in detail)
    else:
        assert detail


def test_missing_required_field_is_422(api):
    _assert_validation_error(api.call('createSeries', json={}, negative=True, expect=422))
    _assert_validation_error(api.call('recordDiagnostic', json={}, negative=True, expect=422), issues=False)


def test_unknown_request_field_is_422(api):
    """Request bodies reject unknown fields."""
    _assert_validation_error(api.call('updateSettings', json={'no_such_setting': 1}, negative=True, expect=422))
    _assert_validation_error(api.call('createSeries', json={'name': 'Valid name', 'extra': True}, negative=True, expect=422))


def test_wrong_field_type_is_422_and_strict(api):
    for value in ('404', True, 404.5):
        _assert_validation_error(api.call('recordDiagnostic', json={'event': 'buffer_failed', 'http_status': value},
                                          negative=True, expect=422), issues=False)
    _assert_validation_error(api.call('createSeries', json={'name': 5}, negative=True, expect=422))
    _assert_validation_error(api.call('createSeries', json={'name': True}, negative=True, expect=422))


def test_out_of_range_field_is_422(api):
    _assert_validation_error(api.call('createSeries', json={'name': ''}, negative=True, expect=422))
    _assert_validation_error(api.call('createSeries', json={'name': 'x' * 201}, negative=True, expect=422))
    _assert_validation_error(api.call('recordDiagnostic', json={'event': 'buffer_failed', 'http_status': 99},
                                      negative=True, expect=422), issues=False)
    _assert_validation_error(api.call('recordDiagnostic', json={'event': 'playback_waiting', 'playback_rate': 9},
                                      negative=True, expect=422), issues=False)


def test_unknown_enum_value_is_422(api):
    _assert_validation_error(api.call('recordDiagnostic', json={'event': 'made_up_event'}, negative=True, expect=422),
                             issues=False)


def test_dependent_required_field_is_422(api):
    """`passage_id` requires `book_id` (JSON Schema dependentRequired)."""
    _assert_validation_error(api.call('recordDiagnostic', json={'event': 'buffer_failed', 'passage_id': 'p_0123456789ab'},
                                      negative=True, expect=422), issues=False)


def test_bad_query_parameter_is_422(api):
    _assert_validation_error(api.call('listJobs', query={'active': 'perhaps'}, negative=True, expect=422))


def test_malformed_json_body_is_422(api):
    reply = api.call('updateSettings', content=b'{"tts_model": ', headers={'Content-Type': 'application/json'},
                     negative=True, expect=422)
    _assert_validation_error(reply)


def test_non_object_json_body_is_422(api):
    _assert_validation_error(api.call('updateSettings', json=[1, 2, 3], negative=True, expect=422))


def test_a_well_formed_request_for_a_missing_book_is_404_not_422(api):
    reply = api.call('updateBookMetadata', path={'book_id': 'no-such-book'}, json={'title': 'Anything'}, expect=404)
    assert reply.code == 'book_not_found'


# ---------------------------------------------------------------- body size

def test_oversized_upload_is_refused_before_its_body_is_read(api):
    """`importBook` refuses a request whose Content-Length exceeds 30 MiB plus framing with 413."""
    declared = 40 * 1024 * 1024
    reply = api.raw_request(
        'POST', '/api/books',
        headers={'Content-Type': 'multipart/form-data; boundary=conformance-boundary', 'Content-Length': str(declared)},
        body_prefix=b'--conformance-boundary\r\nContent-Disposition: form-data; name="file"; filename="big.txt"\r\n\r\nstart',
        expect=413)
    assert reply.code == 'upload_too_large'
