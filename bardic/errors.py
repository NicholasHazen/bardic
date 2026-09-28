"""Typed API errors with stable, machine-readable codes.

Every JSON error response is ``{"detail": <sentence>, "code": <code>}``.
``detail`` is for people; ``code`` is for programs, and is part of the
published contract (each operation lists the codes it can return per status;
see docs/API-WORKFLOW.md). Codes are lower snake_case, name the condition
rather than the status (``book_not_found``, ``job_active``), and never change
once published.

Raise an :class:`ApiError` (or a subclass) from route or service code instead
of ``HTTPException``. ``NotFound`` is also a ``KeyError`` and ``Invalid`` is
also a ``ValueError``, so existing ``except KeyError``/``except ValueError``
handlers keep working. A bare ``KeyError`` that reaches the app is a server
defect (500 ``internal_error``), never a 404.

Details describe the condition, not the UI: say "No Gemini API key is
configured", not "Add a Gemini API key in Settings".
"""
from __future__ import annotations

import re

_CODE = re.compile(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*')

# Codes produced by the framework or middleware rather than by an operation.
# Every operation can return these; operations do not list them.
GLOBAL_CODES = {
    'validation_error': 'The request failed validation (422); `detail` is a list of issues.',
    'cross_origin_write': 'A browser write from another origin was rejected (403).',
    'internal_error': 'An unexpected server defect (500).',
    'route_not_found': 'No route matches the method and path (404, 405).',
}

# Fallback for HTTPException raised without a code (framework internals).
STATUS_CODES = {400: 'invalid_request', 404: 'route_not_found', 405: 'route_not_found', 409: 'conflict',
                413: 'too_large', 429: 'rate_limited', 500: 'internal_error', 502: 'provider_error',
                503: 'unavailable'}


class ApiError(Exception):
    """An error that becomes a JSON response with a stable ``code``."""
    status = 400

    def __init__(self, code: str, detail: str, *, status: int | None = None, headers: dict[str, str] | None = None):
        if not _CODE.fullmatch(code):
            raise ValueError(f'error code must be lower snake_case: {code!r}')
        super().__init__(detail)
        self.code = code
        self.detail = detail
        if status is not None:
            self.status = status
        self.headers = headers

    def __str__(self) -> str:  # KeyError would otherwise quote the message
        return self.detail


class Invalid(ApiError, ValueError):
    """400: the request is well-formed but not acceptable in itself."""
    status = 400


class NotFound(ApiError, KeyError):
    """404: a resource named by the request does not exist (or is not in this book)."""
    status = 404


class Conflict(ApiError):
    """409: the request conflicts with the current state (active job, stale plan, archived item)."""
    status = 409


class TooLarge(ApiError):
    """413: the request body is too large."""
    status = 413


class RateLimited(ApiError):
    """429: a request or quota limit applies; retry later."""
    status = 429


class ProviderFailure(ApiError):
    """502: a provider or self-hosted server failed or refused the request."""
    status = 502


class Unavailable(ApiError):
    """503: the server cannot take the work now (for example it is shutting down)."""
    status = 503
