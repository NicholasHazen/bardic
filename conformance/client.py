"""An HTTP client that checks every exchange against the contract.

``Api.call(operation_id, ...)`` builds the request from the contract (path template, parameters),
checks the request against the operation's schemas before sending, sends it over real HTTP, then
checks the response with :mod:`conformance.contract` and fails the test on any problem. Every
exchange is recorded for the coverage report.
"""
from __future__ import annotations

import http.client
import json as jsonlib
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Collection, Iterable, Mapping
from urllib.parse import urlsplit

import httpx

from .contract import MISSING, Contract, Operation


class ContractViolation(AssertionError):
    """A request or response the contract does not allow."""


@dataclass
class Reply:
    """One response. ``problems`` is empty for a conforming exchange."""

    method: str
    path: str
    status: int
    headers: httpx.Headers
    content: bytes
    operation: Operation | None
    problems: list[str] = field(default_factory=list)

    @property
    def json(self) -> Any:
        return jsonlib.loads(self.content.decode('utf-8'))

    @property
    def text(self) -> str:
        return self.content.decode('utf-8', errors='replace')

    @property
    def code(self) -> str | None:
        """The ``code`` of a JSON error body, else None."""
        try:
            body = self.json
        except ValueError:
            return None
        return body.get('code') if isinstance(body, dict) else None

    @property
    def content_type(self) -> str:
        return self.headers.get('content-type', '').split(';')[0].strip().lower()

    def summary(self) -> str:
        body = self.text if len(self.content) <= 400 else self.text[:400] + '...'
        return f'{self.method} {self.path} -> {self.status} {self.content_type or "(no content type)"} {body!r}'


class Recorder:
    """What a session exercised: operations that got a 2xx, statuses and error codes seen."""

    def __init__(self) -> None:
        self.calls = 0
        self.succeeded: dict[str, int] = {}
        self.statuses: dict[tuple[str, int], int] = {}
        self.error_codes: dict[tuple[str, int, str], int] = {}
        self.unrouted: dict[tuple[int, str], int] = {}

    def record(self, reply: Reply) -> None:
        self.calls += 1
        op = reply.operation
        if op is None:
            if reply.status >= 400:
                self.unrouted[(reply.status, reply.code or '')] = self.unrouted.get((reply.status, reply.code or ''), 0) + 1
            return
        self.statuses[(op.id, reply.status)] = self.statuses.get((op.id, reply.status), 0) + 1
        if 200 <= reply.status < 300:
            self.succeeded[op.id] = self.succeeded.get(op.id, 0) + 1
        elif reply.status >= 400 and reply.code:
            key = (op.id, reply.status, reply.code)
            self.error_codes[key] = self.error_codes.get(key, 0) + 1


def _query_value(value: Any) -> Any:
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    return value


class Api:
    """The server under test, seen through the contract."""

    def __init__(self, base_url: str, contract: Contract, recorder: Recorder | None = None, *,
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip('/')
        self.contract = contract
        self.recorder = recorder or Recorder()
        self.http = httpx.Client(base_url=self.base_url, transport=transport, timeout=timeout, follow_redirects=False)
        self.netloc = urlsplit(self.base_url).netloc

    def close(self) -> None:
        self.http.close()

    # ---- operation calls

    def call(self, operation_id: str, *, path: Mapping[str, Any] | None = None, query: Mapping[str, Any] | None = None,
             json: Any = MISSING, form: Mapping[str, Any] | None = None,
             files: Mapping[str, tuple[str, bytes, str]] | None = None, headers: Mapping[str, str] | None = None,
             content: bytes | None = None, expect: int | Iterable[int] | None = 200, negative: bool = False,
             skip: Collection[str] = ()) -> Reply:
        """Send one operation and check the exchange.

        ``expect`` is the status (or statuses) the test requires; None accepts any status the contract
        documents. ``negative=True`` skips the request check, for tests that send a deliberately
        invalid request; the response is still held to the contract.
        """
        op = self.contract.operation(operation_id)
        if not negative:
            problems = self.contract.request_problems(op, path_params=path, query=query, body=json, form=form,
                                                      files=(files or {}).keys())
            if problems:
                raise ContractViolation('The harness built a request the contract does not allow:\n  ' + '\n  '.join(problems))
        url_path = self.contract.url_path(op, path)
        kwargs: dict[str, Any] = {}
        if query:
            kwargs['params'] = {name: _query_value(value) for name, value in query.items() if value is not None}
        if json is not MISSING:
            kwargs['json'] = json
        if form:
            kwargs['data'] = {name: _query_value(value) for name, value in form.items()}
        if files:
            kwargs['files'] = dict(files)
        if content is not None:
            kwargs['content'] = content
        response = self.http.request(op.method, url_path, headers=dict(headers or {}), **kwargs)
        return self._finish(op.method, url_path, response, expect=expect, skip=skip)

    # ---- raw requests (transport tests; paths need not name an operation)

    def request(self, method: str, path: str, *, headers: Mapping[str, str] | None = None, content: bytes | None = None,
                json: Any = MISSING, params: Mapping[str, Any] | None = None,
                expect: int | Iterable[int] | None = None, unrouted: bool = False,
                host_rejection: bool = False, skip: Collection[str] = ()) -> Reply:
        """Send a request without contract-driven construction. The response is still checked.

        ``unrouted=True`` requires the router's own ``route_not_found``; ``host_rejection=True``
        requires the plain-text 400 refusal of an untrusted Host header.
        """
        kwargs: dict[str, Any] = {}
        if json is not MISSING:
            kwargs['json'] = json
        if content is not None:
            kwargs['content'] = content
        if params:
            kwargs['params'] = {name: _query_value(value) for name, value in params.items()}
        response = self.http.request(method.upper(), path, headers=dict(headers or {}), **kwargs)
        return self._finish(method.upper(), path, response, expect=expect, unrouted=unrouted,
                            host_rejection=host_rejection, skip=skip)

    def raw_request(self, method: str, path: str, *, headers: Mapping[str, str], body_prefix: bytes = b'',
                    expect: int | Iterable[int] | None = None, timeout: float = 15.0) -> Reply:
        """Send hand-written HTTP/1.1: the headers as given plus a partial body, then read the reply.

        Used to declare a ``Content-Length`` larger than the bytes sent, which an HTTP client library
        refuses to do, to see whether a server refuses an oversized body before reading it.
        """
        parts = urlsplit(self.base_url)
        head = [f'{method.upper()} {path} HTTP/1.1', f'Host: {parts.netloc}', 'Connection: close']
        head += [f'{name}: {value}' for name, value in headers.items()]
        with socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout) as sock:
            sock.sendall(('\r\n'.join(head) + '\r\n\r\n').encode('latin-1') + body_prefix)
            response = http.client.HTTPResponse(sock, method=method.upper())
            response.begin()
            content = response.read()
            reply_headers = httpx.Headers(response.getheaders())
            status = response.status
        fake = httpx.Response(status, headers=reply_headers, content=content)
        return self._finish(method.upper(), path, fake, expect=expect)

    # ---- checking

    def _finish(self, method: str, path: str, response: httpx.Response, *, expect: int | Iterable[int] | None,
                unrouted: bool = False, host_rejection: bool = False, skip: Collection[str] = ()) -> Reply:
        try:  # the path as sent (a client library may normalise the one the test asked for)
            path = response.request.url.raw_path.decode('ascii')
        except (RuntimeError, AttributeError):
            pass
        path_only = path.split('?', 1)[0]
        matched = self.contract.match(method, path_only)
        problems = self.contract.response_problems(method, path_only, response.status_code, response.headers,
                                                   response.content, unrouted=unrouted, host_rejection=host_rejection,
                                                   skip=skip)
        reply = Reply(method, path_only, response.status_code, response.headers, response.content,
                      matched.operation if matched else None, problems)
        self.recorder.record(reply)
        if problems:
            raise ContractViolation('The response does not match the contract:\n  ' + '\n  '.join(problems)
                                    + '\n  ' + reply.summary())
        wanted = None if expect is None else ({expect} if isinstance(expect, int) else set(expect))
        if reply.status == 500 and not (wanted and 500 in wanted):
            raise AssertionError(f'the server reported an unexpected defect (500 internal_error): {reply.summary()}')
        if wanted is not None and reply.status not in wanted:
            raise AssertionError(f'expected status {sorted(wanted)}, got: {reply.summary()}')
        return reply

    # ---- helpers shared by tests

    def wait_for_job(self, job_id: str, *, book_id: str | None = None, timeout: float = 60.0,
                     interval: float = 0.2) -> dict:
        """Poll ``listJobs`` until the job leaves ``queued``/``running``, and return it."""
        deadline = time.monotonic() + timeout
        query = {'book_id': book_id} if book_id else None
        job = None
        while time.monotonic() < deadline:
            jobs = self.call('listJobs', query=query).json
            job = next((item for item in jobs if item['id'] == job_id), None)
            if job is not None and job['status'] not in ('queued', 'running'):
                return job
            time.sleep(interval)
        raise AssertionError(f'job {job_id} did not finish within {timeout:.0f}s; last seen: {job}')
