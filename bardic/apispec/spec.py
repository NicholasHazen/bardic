"""Assemble, publish and check the HTTP contract.

``finalize`` turns FastAPI's generated document (paths, parameters and request
DTOs) into the published contract by adding each operation's documentation,
response schema, errors and cost from the :class:`~bardic.apispec.base.Op`
registry. ``validate_response`` is used by the test suite.
"""
from __future__ import annotations

import copy
import importlib
import json
import re
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from ..errors import GLOBAL_CODES
from .base import Error, Op, Tag

# Semantic version of the contract (not of the server). While 0.x, a breaking
# change bumps the minor version and an additive change bumps the patch
# version. Every change is recorded in contract/CHANGELOG.md.
VERSION = '0.2.0'

FAMILIES = ('system', 'library', 'series', 'books', 'inspection', 'listening', 'voices', 'pipeline')

TAGS = [
    Tag('System', 'Runtime status, settings, provider catalogs and explicit provider checks.'),
    Tag('Jobs', 'Durable background work: listing, polling and cancellation.'),
    Tag('Diagnostics', 'Best-effort, allowlisted operational events for troubleshooting playback.'),
    Tag('Library', 'Importing books, the library snapshot, metadata, covers, removal and restoration, and exports.'),
    Tag('Series', 'Series membership, volume placeholders, cross-book character identities and collection runs.'),
    Tag('Books', 'The book document and manual edits to its characters, passages and scenes.'),
    Tag('Pronunciations', 'Per-book respellings sent to narrators in place of a word; book text never changes.'),
    Tag('Classic analysis', 'The older phase-based story analysis, its plan preview and local preprocessing.'),
    Tag('Analysis pipeline', 'The step pipeline: step settings, previewed runs, and versioned results to accept or reject.'),
    Tag('Inspection', 'Read-only views of stages, artifacts, the story map, passage search and resource usage.'),
    Tag('Narration', 'Enhanced (cast) narration takes and their audio.'),
    Tag('Listening', 'Simple single-narrator listening: passage and chapter preparation and audio.'),
    Tag('Performances', 'Saved performances: named selections of chapters and narration settings over retained audio.'),
    Tag('Voice previews', 'Short explicit voice auditions against a book passage.'),
    Tag('Voices', 'The library-wide voice library: voices, versions, defaults, drafts and Breeze clones.'),
]

INFO = """\
The local HTTP interface of Bardic, an ebook analysis, audiobook production
and read-along application. This document is the contract that clients are
written against. It is generated from the server and checked in as
`contract/openapi.json`; the prose guide is `docs/API.md` and a readable
reference is `contract/API-REFERENCE.md`.

## Transport and security

- The server binds to loopback (`127.0.0.1:8765` by default). An opt-in
  local-network mode adds a `.local` host name. There is **no
  authentication**: anyone who can reach the port is treated as the owner.
- Requests with an unexpected `Host` header are rejected on every route
  with status 400 and the plain-text (not JSON) body `Invalid host header`,
  before any operation runs. Writes (anything
  but GET, HEAD and OPTIONS) are rejected with 403 when they come from
  another browser origin (`Origin` differs from `Host`, or
  `Sec-Fetch-Site: cross-site`). Requests without an `Origin` header, such
  as command-line clients, are accepted. There is no CORS support.
- Every `/api/` response carries `Cache-Control: no-store`, except a
  successful cover image (`getBookCover`), which sets its own caching
  headers: a strong `ETag`, and `immutable` at its content-addressed `?v=`
  URL.
- URLs returned inside responses (audio, covers, auditions) are
  root-relative. Resolve them against the server's base URL.

## Conventions

- JSON requests use `Content-Type: application/json`; imports and voice
  clones use multipart form data. Request bodies reject unknown fields
  (422). Omitted fields take their documented defaults.
- IDs are opaque strings. URL-encode them and obtain them from responses.
- Source text offsets (`start`, `end`) are zero-based Unicode code-point
  offsets into the chapter text, with an exclusive end. They are not UTF-8
  byte offsets or JavaScript UTF-16 indices.
- "Passage" and "segment" name the same reader unit.
- GET requests never start paid generation and never create or change
  library records (books, jobs, runs, artifacts, decisions or resource
  measurements). A few write a disposable derived cache, such as the
  analysis census cache or the passage search index, which can be deleted
  without loss; those operations say so. `x-bardic-cost` on each operation
  says whether it can reach a provider: `none`, `network` (contacts a
  provider or self-hosted server without billed generation) or
  `may_charge`.
- Out-of-range paging parameters are clamped to the allowed range; the
  response reports the values used.

## Work and errors

- Long work is queued as a job and returned immediately. A queued or
  running job is not a result: poll `GET /api/jobs` until the job reaches a
  terminal status. Failures, cancellations and allowance stops appear in the
  job, while polling itself still returns 200. A terminal status is final.
- Statuses mean the same thing on every operation:
  - 400: the request is well-formed but cannot be carried out as asked,
    because of its content or the library's data. Examples are an unknown
    ID inside a request body, an empty selection, a missing original file,
    or a name or position already taken. An operation that deliberately
    ignores an unknown body ID says so.
  - 404: a resource named in the path, or a session named in the query,
    does not exist.
  - 409: a conflict with current state that waiting, restoring or
    previewing again resolves: an active job or series run, a stale
    previewed plan, or an archived book or series.
  - 413: the body is too large.
  - 429: a request or quota limit applies.
  - 502: a provider or self-hosted server failed or refused the work.
    Operations whose purpose is to report a provider's state (account
    checks, model refresh, Breeze refresh) return 200 with the classified
    state instead.
  - 503: the server is shutting down; nothing was queued.
  - Archiving or restoring something already in that state succeeds
    without change.
- Errors are JSON `{"detail": ..., "code": ...}`. `detail` is an English
  sentence, or a list of issues for 422 request validation. Display it; do
  not parse it. `code` is a stable snake_case identifier: branch on it.
  Each operation lists its codes per status (`x-bardic-error-codes`). Any
  operation can also return these global codes:
  - `validation_error` (422): the request failed validation.
  - `cross_origin_write` (403): the write guard rejected a browser write.
  - `internal_error` (500): an unexpected server defect.
  - `route_not_found` (404, 405): no route matches the method and path.

## Compatibility rules for clients

- Ignore response fields you do not know. New fields can appear in any
  new contract version.
- Treat enumerated string values (statuses, kinds, states) as open sets:
  handle an unknown value gracefully.
  Configure code generators to accept unknown enum values (for example
  openapi-generator's `enumUnknownDefaultCase=true`).
- A request field with a documented default is optional: omit it to get the
  default. Configure generators accordingly (openapi-typescript:
  `defaultNonNullable: false`). Response schemas carry no defaults; a response
  field is always present exactly when it is listed in `required`.
- Avoid any field marked `x-bardic-internal`: bookkeeping that a later
  version may remove. This version has none.
- `info.version` follows the rules in `contract/CHANGELOG.md`.
"""


@lru_cache(maxsize=1)
def registry() -> tuple[dict[tuple[str, str], Op], dict[str, dict[str, str]]]:
    """All operations keyed by (method, path), and request-schema field docs."""
    ops: dict[tuple[str, str], Op] = {}
    request_docs: dict[str, dict[str, str]] = {}
    for family in FAMILIES:
        module = importlib.import_module(f'{__package__}.{family}')
        for entry in module.OPS:
            key = (entry.method, entry.path)
            if key in ops:
                raise ValueError(f'{entry.method} {entry.path} is described twice')
            ops[key] = entry
        for schema, docs in getattr(module, 'REQUEST_DOCS', {}).items():
            if schema in request_docs:
                raise ValueError(f'request schema {schema} is documented twice')
            request_docs[schema] = docs
    ids = [entry.id for entry in ops.values()]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f'duplicate operation ids: {duplicates}')
    tag_names = {tag.name for tag in TAGS}
    unknown = sorted({entry.tag for entry in ops.values()} - tag_names)
    if unknown:
        raise ValueError(f'unknown tags: {unknown}')
    return ops, request_docs


@lru_cache(maxsize=None)
def adapter(response: Any) -> TypeAdapter:
    return TypeAdapter(response)


def _ref(name: str) -> dict:
    return {'$ref': f'#/components/schemas/{name}'}


def _rename_refs(node: Any, renames: dict[str, str]) -> Any:
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            name = value.rsplit('/', 1)[-1] if key == '$ref' and isinstance(value, str) else None
            result[key] = _ref(renames[name])['$ref'] if name in renames else _rename_refs(value, renames)
        return result
    if isinstance(node, list):
        return [_rename_refs(item, renames) for item in node]
    return node


def _any_of_without_discriminator(node: Any) -> None:
    if isinstance(node, dict):
        if 'oneOf' in node and 'discriminator' not in node:
            node['anyOf'] = node.pop('oneOf')
        for value in node.values():
            _any_of_without_discriminator(value)
    elif isinstance(node, list):
        for item in node:
            _any_of_without_discriminator(item)


def finalize(generated: dict) -> dict:
    """Merge the operation registry into FastAPI's generated document."""
    ops, request_docs = registry()
    schema = copy.deepcopy(generated)
    components = schema.setdefault('components', {}).setdefault('schemas', {})

    # FastAPI names multipart bodies after Python handlers; use operation ids.
    renames = {}
    for path, methods in schema['paths'].items():
        for method, operation in methods.items():
            entry = ops.get((method.upper(), path))
            content = operation.get('requestBody', {}).get('content', {})
            for media in content.values():
                name = media.get('schema', {}).get('$ref', '').rsplit('/', 1)[-1]
                if entry and name.startswith('Body_'):
                    renames[name] = entry.id[0].upper() + entry.id[1:] + 'Form'
    schema = _rename_refs(schema, renames)
    components = schema['components']['schemas']
    for old, new in renames.items():
        components[new] = components.pop(old)
        components[new]['title'] = new

    # FastAPI's own validation-error schemas are replaced by Error.
    for name in ('HTTPValidationError', 'ValidationError'):
        components.pop(name, None)

    inputs = [(key, 'validation', adapter(entry.response)) for key, entry in ops.items() if entry.response is not None]
    inputs.append((('error', ''), 'validation', adapter(Error)))
    response_schemas, definitions = TypeAdapter.json_schemas(inputs, ref_template='#/components/schemas/{model}')
    for name, definition in definitions.get('$defs', {}).items():
        if '__' in name:
            raise ValueError(f'two different response models are named like {name}; give each view a unique name')
        if name in components and components[name] != definition:
            raise ValueError(f'response model {name} collides with a request schema of the same name')
        # The server never fills defaults into responses; a `default` there only misleads generators
        # into treating a sometimes-absent field as always present. Absence is stated by `required`.
        for prop in definition.get('properties', {}).values():
            prop.pop('default', None)
        components[name] = definition

    for path, methods in schema['paths'].items():
        for method, operation in methods.items():
            entry = ops.get((method.upper(), path))
            if entry is None:
                operation['x-bardic-undocumented'] = True
                continue
            operation['operationId'] = entry.id
            operation['summary'] = entry.summary
            operation['description'] = entry.description
            operation['tags'] = [entry.tag]
            operation['x-bardic-cost'] = entry.cost
            if set(operation.get('responses', {})) - {'422'} != {'200'}:
                raise ValueError(f'{method.upper()} {path}: the contract assumes 200 is the only success status')
            if entry.media:
                content = {entry.media: {'schema': {'type': 'string', 'format': 'binary'}}}
            else:
                content = {'application/json': {'schema': response_schemas[((method.upper(), path), 'validation')]}}
            responses = {'200': {'description': entry.response_description or 'Success.', 'content': content}}
            if entry.ranges:
                responses['206'] = {'description': 'Partial content for a `Range` request (served from a file; see `Content-Range`).',
                                    'content': content}
            errors = dict(entry.errors)
            if entry.ranges:
                errors.setdefault(416, 'The requested `Range` cannot be satisfied (empty body; see `Content-Range`).')
            if method.upper() != 'GET':
                errors.setdefault(403, {'cross_origin_write': 'A browser write from another origin was rejected by the write guard (see Transport and security).'})
            if 'requestBody' in operation or operation.get('parameters'):
                errors.setdefault(422, {'validation_error': 'The request failed validation: a missing, extra or out-of-range field or parameter.'})
            # Published so that generated clients model it; the test suite still fails on any 500 it sees.
            errors.setdefault(500, {'internal_error': 'An unexpected server defect, such as damaged stored data.'})
            if entry.conditional:
                responses['304'] = {'description': 'Not modified: `If-None-Match` matched the current `ETag` (empty body).'}
            for status in sorted(errors):
                # An unsatisfiable range (416) has an empty body.
                documented = errors[status]
                if status == 416:
                    responses[str(status)] = {'description': documented}
                    continue
                response = {'description': documented, 'content': {'application/json': {'schema': _ref('Error')}}}
                if isinstance(documented, dict):
                    response['description'] = '\n'.join(f'- `{code}`: {text}' for code, text in documented.items())
                    response['x-bardic-error-codes'] = list(documented)
                responses[str(status)] = response
            operation['responses'] = responses
            for parameter in operation.get('parameters', []):
                if parameter['name'] in entry.params:
                    parameter['description'] = entry.params[parameter['name']]
            missing = sorted(set(entry.params) - {p['name'] for p in operation.get('parameters', [])})
            if missing:
                raise ValueError(f'{entry.method} {entry.path}: params documents unknown parameters {missing}')

    for name, docs in request_docs.items():
        target = components.get(name)
        if target is None:
            raise ValueError(f'request schema {name} does not exist (multipart bodies are named <OperationId>Form)')
        for field_name, text in docs.items():
            if field_name == '__doc__':
                target['description'] = text
            elif field_name in target.get('properties', {}):
                target['properties'][field_name]['description'] = text
            else:
                raise ValueError(f'request schema {name} has no field {field_name}')

    # Codegen hygiene. `additionalProperties: true` is the JSON Schema default and makes generators emit
    # untyped catch-all maps; property titles become order-dependent inline type names (Audio, Audio1...).
    for component in components.values():
        if component.get('additionalProperties') is True:
            del component['additionalProperties']
        for prop in component.get('properties', {}).values():
            prop.pop('title', None)

    # A union told apart by a callable (not a discriminator property) is emitted as oneOf, but its
    # variants can overlap (open objects), so strict validators would match more than one. anyOf is exact.
    _any_of_without_discriminator(schema)

    schema['info'] = {'title': 'Bardic', 'version': VERSION, 'description': INFO}
    schema['tags'] = [{'name': tag.name, 'description': tag.description} for tag in TAGS]
    schema['paths'] = {path: schema['paths'][path] for path in sorted(schema['paths'])}
    schema['components']['schemas'] = {name: components[name] for name in sorted(components)}
    return schema


def install(app) -> None:
    """Serve the published contract from the app's ``/openapi.json``."""
    generate = app.openapi

    def openapi():
        if getattr(app, '_bardic_contract', None) is None:
            app._bardic_contract = finalize(generate())
            app.openapi_schema = app._bardic_contract
        return app._bardic_contract

    app.openapi = openapi


def dumps(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + '\n'


# ---------------------------------------------------------------- validation

@lru_cache(maxsize=1)
def _matchers() -> list[tuple[re.Pattern, int, Op]]:
    ops, _ = registry()
    matchers = []
    for (method, path), entry in ops.items():
        pattern = re.sub(r'\\\{[^}]+\\\}', '[^/]+', re.escape(path))
        literals = sum(1 for part in path.split('/') if part and not part.startswith('{'))
        matchers.append((re.compile(f'{method} {pattern}'), literals, entry))
    return matchers


def match(method: str, path: str) -> Op | None:
    """The operation for a concrete request path, preferring literal segments."""
    candidates = [(literals, entry) for pattern, literals, entry in _matchers() if pattern.fullmatch(f'{method.upper()} {path}')]
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def extras(value: Any, path: str = '$') -> list[str]:
    """Undeclared fields anywhere in a validated view, with indexes collapsed."""
    found: list[str] = []
    if isinstance(value, BaseModel):
        found += [f'{path}.{key}' for key in (value.__pydantic_extra__ or {})]
        for name in type(value).model_fields:
            found += extras(getattr(value, name), f'{path}.{name}')
    elif isinstance(value, (list, tuple)):
        for item in value:
            found += extras(item, f'{path}[]')
    elif isinstance(value, dict):
        for item in value.values():
            found += extras(item, f'{path}{{}}')
    return sorted(set(found))


def validate_response(method: str, path: str, status: int, content_type: str, body: bytes) -> list[str]:
    """Contract violations for one response; an empty list means it conforms."""
    if status == 400 and content_type.startswith('text/plain') and body == b'Invalid host header':
        return []  # Trusted-host rejection happens before routing; documented in the conventions.
    entry = match(method, path)
    if entry is None:
        if status == 404 and body == b'{"detail":"Not Found","code":"route_not_found"}':
            return []  # No route: the router's own 404.
        return [f'{method.upper()} {path} -> {status}: no contract operation matches this request']
    where = f'{entry.method} {entry.path} -> {status}'
    json_body = content_type.split(';')[0].strip() == 'application/json'
    if status == 304 and entry.conditional:
        return [] if not body else [f'{where}: 304 response has a body']
    if status >= 400:
        write_guard = status == 403 and entry.method != 'GET'
        request_validation = status == 422 and body.startswith(b'{"detail":[')
        range_refused = status == 416 and entry.ranges
        if status not in entry.errors and not (write_guard or request_validation or range_refused):
            return [f'{where}: undocumented error status {status}']
        if status == 416:
            return []  # Empty body, documented as such.
        if not json_body:
            return [f'{where}: error response is {content_type!r}, not JSON']
        try:
            code = json.loads(body).get('code')
        except (ValueError, AttributeError):
            code = None
        documented = entry.errors.get(status)
        if isinstance(documented, dict) and code not in documented and code not in GLOBAL_CODES:
            return [f'{where}: error code {code!r} is not documented for this status (documented: {sorted(documented)})']
        target: Any = Error
    elif status != 200 and not (status == 206 and entry.ranges):
        return [f'{where}: undocumented success status {status}']
    elif entry.media:
        return [] if content_type.startswith(entry.media) else [f'{where}: content type {content_type!r}, expected {entry.media}']
    else:
        if not json_body:
            return [f'{where}: content type {content_type!r}, expected application/json']
        target = entry.response
    try:
        # Strict: a JSON string is not accepted where the contract says number or boolean.
        value = adapter(target).validate_json(body, strict=True)
    except ValidationError as error:
        problems = [f"{'.'.join(map(str, issue['loc'])) or '$'}: {issue['msg']}" for issue in error.errors()[:10]]
        return [f'{where}: does not match {getattr(target, "__name__", target)}: ' + '; '.join(problems)]
    return [f'{where}: undeclared field {field}' for field in extras(value)]
