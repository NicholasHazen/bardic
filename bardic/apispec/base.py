"""Building blocks for the published HTTP contract.

The contract (``contract/openapi.json``) is the language-neutral description
of the API that clients are written against. The Python server produces it,
but the checked-in file is normative: a future server must satisfy it.

A route is described by one :class:`Op` in a family module of this package.
Response bodies are described by :class:`View` models. Views describe what the
server already sends; they are not applied at runtime (handlers still return
dictionaries). The test suite validates every JSON response the tests
receive against its view, and fails on any undeclared field at any depth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# none: no outbound request. network: contacts a provider or self-hosted server without billed
# generation (inventory, health, voice lists). may_charge: may send billed provider requests.
Cost = Literal['none', 'network', 'may_charge']
Method = Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE']


class View(BaseModel):
    """Base for response descriptions.

    ``extra='allow'`` keeps validation tolerant (clients must ignore unknown
    fields); the test walker reports extras so that views stay complete.
    """
    model_config = ConfigDict(extra='allow')


class ValidationIssue(View):
    """One FastAPI request-validation problem."""
    loc: list[str | int] = Field(description='Location of the invalid value, for example ["body", "limits", "max_requests"].')
    msg: str = Field(description='Human-readable reason.')
    type: str = Field(description='Stable Pydantic error type, for example "missing" or "extra_forbidden".')
    input: Any = Field(default=None, description='The rejected input value, echoed back. Absent for the diagnostics endpoint, which never echoes input.')
    ctx: dict[str, Any] | None = Field(default=None, description='Error-specific context, for example `{"le": 1000}` for a bound.')


class Error(View):
    """Error body for every non-2xx JSON response."""
    detail: str | list[ValidationIssue] = Field(
        description='A human-readable English sentence, or for 422 request validation a list of issues. '
                    'Display it; do not parse it.')
    code: str = Field(
        description='Stable, machine-readable error code in lower snake_case, for example `book_not_found` or '
                    '`job_active`. Each operation lists the codes it returns for each status; every operation can '
                    'also return the global codes listed in the contract introduction. Branch on `code`, not on '
                    '`detail`. Treat an unknown code like any other failure with the same status.')


@dataclass(frozen=True)
class Op:
    """The contract entry for one HTTP operation.

    ``id`` is the stable ``operationId``: generated clients use it as a method
    name, so choose it deliberately and change it only as a breaking change.
    Exactly one of ``response`` (a JSON body type) or ``media`` (a binary media
    type) describes a successful response. ``errors`` maps each documented
    failure status to when it happens. ``params`` describes path and query
    parameters by name. ``cost`` says whether the call can reach a provider.
    ``ranges`` marks a binary response served from a file, which honors HTTP
    ``Range`` requests (206 Partial Content, 416 Range Not Satisfiable).
    ``conditional`` marks a response with a strong ``ETag`` that honors
    ``If-None-Match`` (304 Not Modified, empty body).

    Each ``errors`` value is either a sentence (legacy) or a mapping from
    error code to when that code is returned; see :mod:`bardic.errors`.
    Responses with a mapping are checked: a code the mapping does not list
    fails the test suite.
    """
    method: Method
    path: str
    id: str
    tag: str
    summary: str
    description: str
    response: Any = None
    media: str | None = None
    response_description: str = ''
    errors: dict[int, str | dict[str, str]] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    cost: Cost = 'none'
    ranges: bool = False
    conditional: bool = False

    def __post_init__(self):
        if (self.response is None) == (self.media is None):
            raise ValueError(f'{self.method} {self.path}: give exactly one of response or media')
        if not re.fullmatch(r'[a-z][A-Za-z0-9]*', self.id):
            raise ValueError(f'{self.method} {self.path}: operation id must be lowerCamelCase: {self.id!r}')
        if not self.summary or not self.description:
            raise ValueError(f'{self.method} {self.path}: summary and description are required')
        if self.ranges and not self.media:
            raise ValueError(f'{self.method} {self.path}: only binary responses can honor Range requests')
        for status, documented in self.errors.items():
            if isinstance(documented, dict):
                bad = [code for code in documented if not re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*', code)]
                if bad or not documented or not all(documented.values()):
                    raise ValueError(f'{self.method} {self.path}: {status} needs described snake_case codes: {bad}')


@dataclass(frozen=True)
class Tag:
    name: str
    description: str


def op(method: Method, path: str, id: str, tag: str, summary: str, description: str, **kwargs) -> Op:
    return Op(method, path, id, tag, summary, description, **kwargs)


def internal(description: str, **kwargs) -> Any:
    """A field the server sends today that clients should not rely on.

    Use it for storage bookkeeping that reaches the wire by accident (edit
    tracking, fingerprints, server paths). It is published with
    ``x-bardic-internal: true`` so that clients avoid it and a later contract
    version can remove it.
    """
    return Field(description=f'Internal; do not rely on it. {description}',
                 json_schema_extra={'x-bardic-internal': True}, **kwargs)
