"""Assemble, publish and check the HTTP contract.

``finalize`` turns FastAPI's generated document (paths, parameters and request
DTOs) into the published contract by adding each operation's documentation,
response schema, errors and cost from the :class:`~bardic.apispec.base.Op`
registry. ``validate_response`` is used by the test suite.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import threading
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from ..errors import GLOBAL_CODES
from .base import Error, Op, Tag

# Semantic version of the contract (not of the server). While 0.x, a breaking
# change bumps the minor version and an additive change bumps the patch
# version. Every change is recorded in contract/CHANGELOG.md.
VERSION = '0.5.1'

FAMILIES = ('system', 'library', 'series', 'books', 'inspection', 'listening', 'voices', 'pipeline')

TAGS = [
    Tag('System', 'Runtime status, settings, provider catalogs and explicit provider checks.'),
    Tag('Jobs', 'Durable background work: listing, polling and cancellation.'),
    Tag('Diagnostics', 'Best-effort, allowlisted operational events for troubleshooting playback.'),
    Tag('Library', 'Importing books, the library snapshot, metadata, covers, removal and restoration, and exports.'),
    Tag('Series', 'Series membership, volume placeholders, cross-book character identities and collection runs.'),
    Tag('Books', 'The book document and manual edits to its characters, passages and scenes.'),
    Tag('Pronunciations', 'Per-book respellings sent to narrators in place of a word; book text never changes.'),
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
  as command-line clients, are accepted. CORS can allow other browser
  origins; it is off by default (next item).
- **CORS is opt-in.** The server's operator enables it with the setting
  `BARDIC_CORS_ORIGINS`: `*` (every origin) or a comma-separated list of
  exact origins such as `https://app.example,http://localhost:5173` (scheme,
  host and optional port; no paths, no wildcards inside a list). A server
  where it is unset or empty sends no CORS header, answers a preflight
  `OPTIONS` like any other unrouted method (405 `route_not_found`) and
  refuses every cross-origin browser write as described above, so a browser
  page from another origin can read nothing and write nothing. Where it is
  set, for `/api/` requests:
  - `Access-Control-Allow-Origin` is `*` for `*`. For a list it is the
    request's own `Origin` when that origin is listed, and absent
    otherwise; the response then also carries `Vary: Origin`.
    `Access-Control-Expose-Headers` names `Bardic-Contract-Version`, `ETag`,
    `Content-Range`, `Content-Length`, `Accept-Ranges`,
    `Content-Disposition` and `Content-Type`, so browser code can read them.
  - An `OPTIONS` request to any `/api/` path from an allowed origin is a
    preflight, answered with 204 and no body, `Access-Control-Allow-Methods:
    GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS`,
    `Access-Control-Allow-Headers: Content-Type, Range, If-None-Match,
    If-Match, Authorization` and `Access-Control-Max-Age: 600`. One from an
    origin that is not allowed gets the 405 it gets when CORS is off.
  - The write guard lets a write through when its `Origin` is allowed, and
    still refuses every other cross-origin write with 403
    `cross_origin_write`. `*` allows every `Origin`, including `null` (sent
    by sandboxed frames and local files, which any web page can produce
    anyway). A list allows `null` only when it contains the entry `null`.
  - `Access-Control-Allow-Credentials` is never sent, so browsers do not
    attach cookies or HTTP authentication. The trusted `Host` check above is
    not relaxed, and a preflight with an untrusted `Host` is refused like any
    other request. A preflight carries `Cache-Control: no-store` and
    `Bardic-Contract-Version` like every other `/api/` response.
  - This is not authentication. An allowed origin can read and change the
    whole library and start paid work, and `*` allows any web page in any
    browser that can reach the server. Clients cannot see the setting; a
    call that fails in the browser with a CORS error means the server has not
    allowed that page's origin.
- Every `/api/` response carries `Cache-Control: no-store`, except a
  successful cover image (`getBookCover`), which sets its own caching
  headers: a strong `ETag`, and `immutable` at its content-addressed `?v=`
  URL.
- Every `/api/` response, success or error, also carries the header
  `Bardic-Contract-Version`: the `info.version` of the contract the server
  implements (for example `0.4.0`). It lets a client notice a server that is
  newer or older than the contract it was generated from without an extra
  request.
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
- The reader unit is the passage. Every name on the wire says "passage" (`passage_id`, `passages`, `passage_count`).
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
  running job is not a result: poll `GET /api/jobs/{job_id}` (`getJob`), or
  `GET /api/jobs`, until the job reaches a terminal status. Failures,
  cancellations and allowance stops appear in the job, while polling itself
  still returns 200. A terminal status is final.
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
  - `cross_origin_write` (403): the write guard rejected a browser write from
    an origin that the server's CORS setting does not allow.
  - `internal_error` (500): an unexpected server defect.
  - `route_not_found` (404, 405): no route matches the method and path.

## Compatibility rules for clients

- **Version handshake.** `GET /api/status` (and `POST /api/settings`, which
  returns the same object) has `contract`: `{version, sha256}`. `version`
  equals `info.version` of this document and the `Bardic-Contract-Version`
  header. `sha256` is the lowercase hex SHA-256 of the exact bytes of this
  document as checked in (`contract/openapi.json`, UTF-8), the value recorded
  under this version in `contract/CHANGELOG.md`. A client generated from a
  document compares `version` for compatibility (the rules below and in the
  changelog) and `sha256` when it must be sure of the exact document. A server
  implementation embeds the two values of the document it was built against.
- Ignore response fields you do not know. New fields can appear in any
  new contract version.
- Treat enumerated string values (statuses, kinds, states) as open sets:
  write the client so that an unknown value is handled gracefully (a `default`
  branch that shows the raw value or treats it like an unfinished state), not
  as a failure. The contract cannot make a generator do this. Generators
  differ: a TypeScript type from openapi-typescript is a closed union of
  literals, so a `switch` needs a default branch; openapi-generator's
  `enumUnknownDefaultCase` exists only for some of its generators and its Rust
  generator does not emit the unknown case; and the Rust generators
  (typify, progenitor) emit closed enums that refuse an unknown value when a
  response is deserialized. A Rust client therefore needs a post-processing
  step on the generated code that adds an unrecognized variant to every
  response enum (for example `#[serde(other)]` on a unit variant). Request
  enums stay closed: send only listed values. The contract keeps response
  enumerations as `enum` so that generated code names the known values.
- **Tagged unions are the exception: they are closed.** A value that can be
  one of several object shapes is a named `oneOf` whose members each carry one
  required tag property (its name is `discriminator.propertyName`, usually
  `kind`) with a single-value `enum`, and `discriminator.mapping` maps each tag
  value to its own member schema (the unions whose members are written inline,
  `Job` and `SeriesRunChild`, have no mapping: their tags are the inline enums). Select the member on the tag. A tag value is
  a member, not an open enumeration, so the union has no catch-all member and
  a client cannot decode a member it was not generated with. A new member
  arrives only with a new contract version (`Bardic-Contract-Version`), which
  the changelog announces; a client pinned to a version knows every member it
  can receive from a server at that version. A member that has a
  tag is never nested inside another union, and a nullable union is
  `anyOf: [{$ref: Union}, {type: null}]`.
- A request field with a documented default is optional: omit it to get the
  default. Configure generators accordingly (openapi-typescript:
  `defaultNonNullable: false`). Response schemas carry no defaults; a response
  field is always present exactly when it is listed in `required`.
- Avoid any field marked `x-bardic-internal`: bookkeeping that a later
  version may remove. This version has none.
- `info.version` follows the rules in `contract/CHANGELOG.md`.
"""


# The 416 of every range-capable file (`Op.ranges`), added to that operation's documented errors.
RANGE_ERRORS = {'range_not_satisfiable': 'The requested `Range` cannot be satisfied: it starts beyond the end of the file. '
                                         'The response also carries `Content-Range: bytes */<size>`.'}


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


# Unions whose branches are written into the union itself (each branch is a full inline object schema, not a `$ref`),
# because generators turn only an inline `oneOf` of tagged objects into a tagged enum. The branch schemas stay as
# components when something else refers to them and are removed otherwise.
INLINE_UNIONS = ('Job', 'SeriesRunChild')


def _inline_unions(schema: dict, components: dict) -> None:
    branches_by_union = {}
    for name in INLINE_UNIONS:
        union = components[name]
        branches = []
        for member in union['oneOf']:
            branch = copy.deepcopy(components[member['$ref'].rsplit('/', 1)[-1]])
            branch.pop('title', None)
            branches.append(branch)
        branches_by_union[name] = [member['$ref'].rsplit('/', 1)[-1] for member in union['oneOf']]
        union['oneOf'] = branches
        union['discriminator'].pop('mapping', None)  # A mapping needs `$ref` targets; the tags are the inline enums.
    referenced = set()

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == '$ref' and isinstance(value, str):
                    referenced.add(value.rsplit('/', 1)[-1])
                else:
                    collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)
    collect(schema)
    for name in {name for branch_names in branches_by_union.values() for name in branch_names}:
        if name not in referenced:
            del components[name]


INT32_MAX = 2**31 - 1
INT32_MIN = -2**31


def _integer_formats(node: Any) -> None:
    """Give every integer a `format`, so generators pick a width instead of guessing 32 bits.

    A schema whose bounds keep it within 32 bits is `int32`; every other integer (counters, sizes,
    offsets, seeds up to 2^32 - 1) is `int64`.
    """
    if isinstance(node, dict):
        if node.get('type') == 'integer' and 'format' not in node:
            low = node.get('minimum', node.get('exclusiveMinimum'))
            high = node.get('maximum', node.get('exclusiveMaximum'))
            small = low is not None and high is not None and low >= INT32_MIN and high <= INT32_MAX
            node['format'] = 'int32' if small else 'int64'
        for value in node.values():
            _integer_formats(value)
    elif isinstance(node, list):
        for item in node:
            _integer_formats(item)


def _resolve(components: dict, node: dict) -> dict:
    while isinstance(node, dict) and '$ref' in node:
        node = components[node['$ref'].rsplit('/', 1)[-1]]
    return node


def _consts_to_enums(node: Any) -> None:
    """Express every constant as a single-value `enum`, not a `const`.

    Some generators ignore `const` (typify turns it into a plain string), while a single-value `enum` is honored by
    all of them. It is what makes each member of a tagged union refuse the other members' tag values, and it types a
    fixed label (`phase: simple_listen`) as the one value it can have.
    """
    if isinstance(node, dict):
        if 'const' in node and 'enum' not in node:
            node['enum'] = [node.pop('const')]
        for value in node.values():
            _consts_to_enums(value)
    elif isinstance(node, list):
        for item in node:
            _consts_to_enums(item)


def _is_object(components: dict, node: dict) -> bool:
    target = _resolve(components, node)
    return target.get('type') == 'object' or 'properties' in target or 'oneOf' in target


def _check_shapes(node: Any, components: dict, where: str = '') -> None:
    """Refuse the shapes that generate badly (docs/API-WORKFLOW.md, "Shapes generators handle").

    - Every `oneOf` is a discriminated union: each member has a required single-value `enum` tag on the
      discriminator property, tags are distinct, and a `mapping`, when present, maps each tag to its member.
    - `anyOf` is only a nullable (`[X, null]`) or a union of scalars and arrays; a union of objects is a `oneOf`.
    - `anyOf` and `oneOf` are never nested, and a bare `type: null` appears only as the `null` of an `anyOf`.
    """
    if isinstance(node, dict):
        for key in ('oneOf', 'anyOf'):
            members = node.get(key)
            if members is None:
                continue
            kinds = [m for m in members if isinstance(m, dict)]
            nested = [m for m in kinds if 'anyOf' in m or 'oneOf' in m]
            if nested:
                raise ValueError(f'{where}: {key} contains another anyOf/oneOf; name the inner union and reference it')
            non_null = [m for m in kinds if m.get('type') != 'null']
            if key == 'anyOf' and len(non_null) > 1 and any(_is_object(components, m) for m in non_null):
                raise ValueError(f'{where}: anyOf of several object schemas; make it a named oneOf with a tag')
            if key == 'oneOf':
                discriminator = node.get('discriminator')
                if not discriminator:
                    raise ValueError(f'{where}: oneOf without a discriminator')
                prop = discriminator['propertyName']
                tags = []
                for member in members:
                    target = _resolve(components, member)
                    tag = target.get('properties', {}).get(prop)
                    values = tag.get('enum') if isinstance(tag, dict) else None
                    if not (isinstance(values, list) and len(values) == 1 and isinstance(values[0], str)) \
                            or prop not in target.get('required', []):
                        raise ValueError(f'{where}: a oneOf member needs a required single-value string enum `{prop}`')
                    tags.append(values[0])
                if len(set(tags)) != len(tags):
                    raise ValueError(f'{where}: oneOf members share a tag value: {tags}')
                mapping = discriminator.get('mapping')
                if mapping is not None:
                    refs = [m.get('$ref') for m in members]
                    if sorted(mapping) != sorted(tags) or sorted(mapping.values()) != sorted(refs):
                        raise ValueError(f'{where}: discriminator mapping is not one-to-one with the members')
        if node.get('type') == 'null' and not where.endswith(('/anyOf', '/oneOf')):
            raise ValueError(f'{where}: a bare null type; make the field a nullable (anyOf with null) or remove it')
        for key, value in node.items():
            _check_shapes(value, components, f'{where}/{key}')
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _check_shapes(item, components, f'{where}/{index}' if not where.endswith(('/anyOf', '/oneOf')) else where)


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
                errors.setdefault(416, RANGE_ERRORS)
            if method.upper() != 'GET':
                errors.setdefault(403, {'cross_origin_write': 'A browser write from another origin was rejected by the write guard (see Transport and security).'})
            if 'requestBody' in operation or operation.get('parameters'):
                errors.setdefault(422, {'validation_error': 'The request failed validation: a missing, extra or out-of-range field or parameter.'})
            # Published so that generated clients model it; the test suite still fails on any 500 it sees.
            errors.setdefault(500, {'internal_error': 'An unexpected server defect, such as damaged stored data.'})
            if entry.conditional:
                responses['304'] = {'description': 'Not modified: `If-None-Match` matched the current `ETag` (empty body).'}
            for status in sorted(errors):
                documented = errors[status]
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

    _consts_to_enums(schema)
    _inline_unions(schema, components)
    _integer_formats(schema)
    _check_shapes(schema, components, '#')

    schema['info'] = {'title': 'Bardic', 'version': VERSION, 'description': INFO}
    schema['tags'] = [{'name': tag.name, 'description': tag.description} for tag in TAGS]
    schema['paths'] = {path: schema['paths'][path] for path in sorted(schema['paths'])}
    schema['components']['schemas'] = {name: components[name] for name in sorted(components)}
    return schema


def install(app) -> None:
    """Serve the published contract from the app's ``/openapi.json``."""
    generate = app.openapi
    lock = threading.Lock()  # finalize() is not idempotent: two first callers must not both run it

    def openapi():
        with lock:
            if getattr(app, '_bardic_contract', None) is None:
                app._bardic_contract = finalize(generate())
                app.openapi_schema = app._bardic_contract
            return app._bardic_contract

    app.openapi = openapi


def dumps(schema: dict) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + '\n'


_IDENTITY: dict[str, str] | None = None
_IDENTITY_LOCK = threading.Lock()


def identity(app) -> dict[str, str]:
    """The contract this server implements: its version and the SHA-256 of ``contract/openapi.json``.

    The hash is of the document ``python -m bardic.apispec`` writes (tests fail
    when the checked-in file differs), so it is computed from the generated
    document once per process instead of reading a file that a packaged install
    would not have. Every app of a process serves the same contract.
    """
    global _IDENTITY
    with _IDENTITY_LOCK:
        if _IDENTITY is None:
            _IDENTITY = {'version': VERSION, 'sha256': hashlib.sha256(dumps(app.openapi()).encode('utf-8')).hexdigest()}
        return dict(_IDENTITY)


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
        if not json_body:
            return [f'{where}: error response is {content_type!r}, not JSON']
        try:
            code = json.loads(body).get('code')
        except (ValueError, AttributeError):
            code = None
        documented = entry.errors.get(status) or (RANGE_ERRORS if range_refused else None)
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
