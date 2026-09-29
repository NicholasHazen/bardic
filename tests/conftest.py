"""Shared test isolation and contract checking."""
import json
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from bardic.apispec import VERSION, match, validate_response
from bardic.apispec.spec import registry
from bardic.tts_limits import LIMITER


@pytest.fixture(autouse=True)
def fresh_rate_limiter():
    # The Gemini speech limiter is process-wide by design; a 429 cooldown in
    # one test must not pace the next test's offline requests.
    LIMITER.reset()
    yield
    LIMITER.reset()


# Every /api response a test receives is checked against contract/openapi.json:
# its status must be documented and its body must match the operation's view
# strictly, with no undeclared field at any depth. A full run also requires
# every operation to have received a 2xx response somewhere in the suite.
# BARDIC_CONTRACT_REPORT=<file> records each checked call as a JSON line
# instead of failing (used while describing routes and to measure coverage).
_REPORT = os.environ.get('BARDIC_CONTRACT_REPORT')
_send = TestClient.send
_succeeded: set[str] = set()


def _checked_send(self, request, *args, **kwargs):
    response = _send(self, request, *args, **kwargs)
    path = request.url.path
    if not path.startswith('/api/') or kwargs.get('stream'):
        return response
    problems = validate_response(request.method, path, response.status_code,
                                 response.headers.get('content-type', ''), response.content)
    if response.headers.get('bardic-contract-version') != VERSION:
        problems.append(f'{request.method} {path} -> {response.status_code}: the Bardic-Contract-Version header is '
                        f'{response.headers.get("bardic-contract-version")!r}, expected {VERSION!r} on every /api response')
    entry = match(request.method, path)
    if entry is not None and 200 <= response.status_code < 300:
        _succeeded.add(entry.id)
    if _REPORT:
        with open(_REPORT, 'a', encoding='utf-8') as report:
            report.write(json.dumps({'method': request.method, 'path': path, 'status': response.status_code,
                                     'test': os.environ.get('PYTEST_CURRENT_TEST', ''), 'problems': problems}) + '\n')
    elif problems:
        raise AssertionError('Response does not match contract/openapi.json (see docs/API-WORKFLOW.md):\n  '
                             + '\n  '.join(problems))
    return response


TestClient.send = _checked_send


def _full_run(session) -> bool:
    """True when every test module ran unfiltered, so coverage can be judged."""
    option = session.config.option
    if any(getattr(option, name, None) for name in ('keyword', 'markexpr', 'deselect', 'lf', 'failedfirst')):
        return False
    collected = {Path(str(item.fspath)).name for item in session.items}
    modules = {path.name for path in Path(__file__).parent.glob('test_*.py')}
    return modules <= collected


def pytest_sessionfinish(session, exitstatus):
    if _REPORT or exitstatus != 0 or not _full_run(session):
        return
    ops, _ = registry()
    untested = sorted(f'{entry.id} ({entry.method} {entry.path})' for entry in ops.values() if entry.id not in _succeeded)
    if untested:
        print('\nContract coverage: no test received a 2xx response from these operations '
              '(add a test; see docs/API-WORKFLOW.md):\n  ' + '\n  '.join(untested))
        session.exitstatus = 1
