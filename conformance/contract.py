"""Load ``contract/openapi.json`` and check HTTP exchanges against it.

This module knows nothing about any server. It reads the OpenAPI 3.1 document and answers
two questions for a concrete exchange: "is this request one the contract allows?" and "is this
response one the contract describes?". Every answer is a list of human-readable problems; an
empty list means the exchange conforms.

The response rules reproduce the in-process checks that ``tests/conftest.py`` applies to the
Python server, and add the closed-schema rule the contract file itself does not encode:

* the operation exists (a path that matches no operation fails, except the router's own
  ``route_not_found`` 404/405);
* the status is documented for the operation. Exceptions, as in the Python suite: 403 on a write
  (the write guard) and 422 from request validation;
* success is 200, or 206 on a range-capable file, or 304 on a conditional one;
* an error body is ``{"detail", "code"}`` and ``code`` is listed for that status
  (``x-bardic-error-codes``) or is one of the global codes of the contract introduction;
* a binary response carries the documented media type;
* a JSON body validates against the operation's response schema with **closed** objects: any
  object schema that declares ``properties`` and no ``additionalProperties`` rejects undeclared
  fields, at any depth. Objects that declare ``additionalProperties`` (free-form maps) stay open;
* every ``/api/`` response carries ``Cache-Control: no-store``, except a conditional file
  (the cover image), which manages its own caching on 200 and 304;
* when the contract introduction documents a ``Bardic-Contract-Version`` header, every ``/api/`` response
  carries it, equal to ``info.version``.

A test can leave one of the two header checks out of one exchange (``skip=``) when a deviation has its own test.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Collection, Iterable, Mapping
from urllib.parse import quote

from jsonschema import Draft202012Validator, ValidationError, validators
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACT_URI = 'urn:bardic:contract'
METHODS = ('get', 'post', 'put', 'patch', 'delete')

# Codes the contract introduction lists as returnable by any operation, with their statuses. The
# introduction is parsed at load time (so a new global code needs no change here); this table is
# the floor used when parsing finds fewer.
KNOWN_GLOBAL_CODES = {
    'validation_error': (422,),
    'cross_origin_write': (403,),
    'internal_error': (500,),
    'route_not_found': (404, 405),
}


class MissingBody:
    """Sentinel: no request body was supplied (distinct from an explicit JSON ``null``)."""

    def __repr__(self) -> str:
        return 'MISSING'


MISSING = MissingBody()


# ------------------------------------------------------------------ closed validation

_BASE = Draft202012Validator.VALIDATORS


def _undeclared(validator, instance, schema) -> list[str]:
    if not validator.is_type(instance, 'object'):
        return []
    declared = schema.get('properties', {})
    patterns = schema.get('patternProperties', {})
    return [name for name in instance
            if name not in declared and not any(re.search(pattern, name) for pattern in patterns)]


def _closed_properties(validator, properties, instance, schema):
    """``properties`` that also rejects undeclared fields unless the schema opts out."""
    yield from _BASE['properties'](validator, properties, instance, schema)
    if 'additionalProperties' not in schema:
        for name in _undeclared(validator, instance, schema):
            yield ValidationError(f'undeclared field {name!r}', path=[name], validator='undeclared')


def _additional_properties(validator, additional, instance, schema):
    """``additionalProperties: false`` reported per field, at the field's own path."""
    if additional is False:
        for name in _undeclared(validator, instance, schema):
            yield ValidationError(f'undeclared field {name!r}', path=[name], validator='undeclared')
    else:
        yield from _BASE['additionalProperties'](validator, additional, instance, schema)


def _is_integer(checker, instance) -> bool:
    # A JSON number written 3.0 is not an integer for a typed client; booleans never are.
    return isinstance(instance, int) and not isinstance(instance, bool)


ClosedValidator = validators.extend(
    Draft202012Validator,
    validators={'properties': _closed_properties, 'additionalProperties': _additional_properties},
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine('integer', _is_integer),
)


def json_path(parts: Iterable[Any]) -> str:
    path = '$'
    for part in parts:
        path += f'[{part}]' if isinstance(part, int) else f'.{part}'
    return path


def _leaves(error: ValidationError) -> list[ValidationError]:
    """The most specific errors under ``error``.

    ``anyOf``/``oneOf`` failures carry one error group per branch. Report the branch a reader would
    call "the one intended": a branch that fails only deeper than the value itself (a right type
    with a wrong field) beats a branch that fails on the value's type (the ``null`` alternative of a
    nullable field), then the branch with fewest problems.
    """
    if error.validator in ('anyOf', 'oneOf') and error.context:
        branches: dict[Any, list[ValidationError]] = {}
        for sub in error.context:
            branches.setdefault(sub.relative_schema_path[0], []).append(sub)
        scored = []
        for index, subs in branches.items():
            leaves = [leaf for sub in subs for leaf in _leaves(sub)]
            shape_mismatch = any(sub.validator in ('type', 'const', 'enum') and not sub.relative_path for sub in subs)
            scored.append((shape_mismatch, len(leaves), index, leaves))
        return min(scored, key=lambda item: item[:3])[3]
    return [error]


def schema_problems(validator: Draft202012Validator, instance: Any, limit: int = 12) -> list[str]:
    """``"$.path: message"`` strings for every way ``instance`` violates the schema."""
    problems: list[str] = []
    seen: set[str] = set()
    for error in validator.iter_errors(instance):
        for leaf in _leaves(error):
            message = leaf.message if len(leaf.message) <= 160 else leaf.message[:157] + '...'
            line = f'{json_path(leaf.absolute_path)}: {message}'
            if line not in seen:
                seen.add(line)
                problems.append(line)
    problems.sort()
    if len(problems) > limit:
        problems = problems[:limit] + [f'... and {len(problems) - limit} more']
    return problems


def _absolute_refs(node: Any) -> Any:
    """Copy ``node`` with ``#/...`` references pointing into the contract document."""
    if isinstance(node, dict):
        return {key: (CONTRACT_URI + value if key == '$ref' and isinstance(value, str) and value.startswith('#/')
                      else _absolute_refs(value)) for key, value in node.items()}
    if isinstance(node, list):
        return [_absolute_refs(item) for item in node]
    return node


# ------------------------------------------------------------------ operations

@dataclass(frozen=True)
class Parameter:
    name: str
    location: str  # 'path' | 'query' | 'header'
    required: bool
    schema: dict


@dataclass(frozen=True)
class Operation:
    id: str
    method: str  # upper case
    path: str  # template such as /api/books/{book_id}
    tag: str
    cost: str | None
    parameters: tuple[Parameter, ...]
    request_bodies: Mapping[str, tuple[bool, dict]]  # media type -> (required, schema)
    responses: Mapping[int, dict]
    regex: re.Pattern = field(compare=False, repr=False)
    literals: int = 0

    @property
    def path_names(self) -> list[str]:
        return re.findall(r'\{([^}]+)\}', self.path)

    @property
    def error_codes(self) -> dict[int, list[str]]:
        """Status -> codes the operation lists for it (``x-bardic-error-codes``)."""
        return {status: list(spec['x-bardic-error-codes']) for status, spec in self.responses.items()
                if 'x-bardic-error-codes' in spec}

    @property
    def media(self) -> str | None:
        """The binary media type of a success response, or None for JSON operations."""
        for media, body in (self.responses.get(200, {}).get('content') or {}).items():
            if body.get('schema', {}).get('format') == 'binary':
                return media
        return None

    @property
    def json_response(self) -> dict | None:
        content = self.responses.get(200, {}).get('content') or {}
        return content.get('application/json', {}).get('schema')

    @property
    def ranges(self) -> bool:
        return 206 in self.responses

    @property
    def conditional(self) -> bool:
        return 304 in self.responses

    def __str__(self) -> str:
        return f'{self.id} ({self.method} {self.path})'


@dataclass(frozen=True)
class Match:
    operation: Operation
    path_params: dict[str, str]


class Contract:
    """An OpenAPI 3.1 document, indexed for exchange checking."""

    def __init__(self, document: dict, source: str = '<memory>'):
        if not str(document.get('openapi', '')).startswith('3.1'):
            raise ValueError(f'{source}: expected an OpenAPI 3.1 document, found openapi={document.get("openapi")!r}')
        self.document = document
        self.source = source
        self.version = document.get('info', {}).get('version', '')
        self.sha256: str | None = None  # of the file's exact bytes, when loaded from a file
        self.registry = Registry().with_resource(
            CONTRACT_URI, Resource.from_contents(document, default_specification=DRAFT202012))
        self._validators: dict[str, Draft202012Validator] = {}
        self.operations: dict[str, Operation] = {}
        self._build_operations()
        self.global_codes = self._parse_global_codes()
        # A contract that documents a version header ("Bardic-Contract-Version") requires it on every
        # /api/ response, with the contract's own version as its value.
        description = document.get('info', {}).get('description', '')
        self.version_header = 'Bardic-Contract-Version' if 'bardic-contract-version' in description.lower() else None

    @classmethod
    def load(cls, path: str | Path) -> 'Contract':
        path = Path(path)
        raw = path.read_bytes()
        contract = cls(json.loads(raw.decode('utf-8')), str(path))
        contract.sha256 = hashlib.sha256(raw).hexdigest()
        return contract

    # ---- indexing

    def _build_operations(self) -> None:
        for path, item in self.document.get('paths', {}).items():
            shared = item.get('parameters', [])
            for method in METHODS:
                spec = item.get(method)
                if spec is None:
                    continue
                op_id = spec['operationId']
                if op_id in self.operations:
                    raise ValueError(f'duplicate operationId {op_id}')
                parameters = tuple(
                    Parameter(p['name'], p['in'], bool(p.get('required')), p.get('schema', {}))
                    for p in shared + spec.get('parameters', []))
                bodies = {media: (bool(spec['requestBody'].get('required')), body.get('schema', {}))
                          for media, body in (spec.get('requestBody') or {}).get('content', {}).items()}
                literals = sum(1 for part in path.split('/') if part and not part.startswith('{'))
                regex = re.compile(re.sub(r'\\\{[^}]+\\\}', '[^/]+', re.escape(path)))
                self.operations[op_id] = Operation(
                    id=op_id, method=method.upper(), path=path, tag=(spec.get('tags') or [''])[0],
                    cost=spec.get('x-bardic-cost'), parameters=parameters, request_bodies=bodies,
                    responses={int(status): value for status, value in spec.get('responses', {}).items() if status.isdigit()},
                    regex=regex, literals=literals)

    def _parse_global_codes(self) -> dict[str, tuple[int, ...]]:
        found = dict(KNOWN_GLOBAL_CODES)
        text = self.document.get('info', {}).get('description', '')
        marker = text.find('global codes')
        if marker >= 0:
            for code, statuses in re.findall(r'^\s*-\s*`([a-z][a-z0-9_]*)`\s*\(([\d,\s]+)\)', text[marker:], re.M):
                found[code] = tuple(int(part) for part in re.findall(r'\d+', statuses))
        return found

    def operation(self, op_id: str) -> Operation:
        try:
            return self.operations[op_id]
        except KeyError:
            raise KeyError(f'{op_id!r} is not an operationId in {self.source}') from None

    def match(self, method: str, path: str) -> Match | None:
        """The operation for a concrete request path, preferring literal segments."""
        best: tuple[int, Operation] | None = None
        for op in self.operations.values():
            if op.method == method.upper() and op.regex.fullmatch(path) and (best is None or op.literals > best[0]):
                best = (op.literals, op)
        if best is None:
            return None
        op = best[1]
        values = re.fullmatch(re.sub(r'\\\{[^}]+\\\}', '([^/]+)', re.escape(op.path)), path).groups()
        return Match(op, dict(zip(op.path_names, values)))

    def url_path(self, op: Operation, path_params: Mapping[str, Any] | None) -> str:
        """The request path for ``op`` with URL-encoded path parameters."""
        given = dict(path_params or {})
        missing = [name for name in op.path_names if name not in given]
        extra = [name for name in given if name not in op.path_names]
        if missing or extra:
            raise ValueError(f'{op.id}: path parameters {sorted(given)} do not match {op.path_names}')
        return re.sub(r'\{([^}]+)\}', lambda m: quote(str(given[m.group(1)]), safe=''), op.path)

    # ---- schema validators

    def dereference(self, schema: dict) -> dict:
        """Follow local ``$ref`` links until a schema with content is reached."""
        seen = 0
        while '$ref' in schema and seen < 20:
            target = schema['$ref']
            if not target.startswith('#/'):
                raise ValueError(f'only local references are supported: {target}')
            node: Any = self.document
            for part in target[2:].split('/'):
                node = node[part.replace('~1', '/').replace('~0', '~')]
            schema = node
            seen += 1
        return schema

    def _validator(self, key: str, schema: dict) -> Draft202012Validator:
        if key not in self._validators:
            self._validators[key] = ClosedValidator(_absolute_refs(schema), registry=self.registry)
        return self._validators[key]

    def component_validator(self, name: str) -> Draft202012Validator:
        return self._validator(f'#{name}', {'$ref': f'#/components/schemas/{name}'})

    def response_validator(self, op: Operation) -> Draft202012Validator:
        return self._validator(f'{op.id}:response', op.json_response or {})

    # ---- requests

    def request_problems(self, op: Operation, *, path_params: Mapping[str, Any] | None = None,
                         query: Mapping[str, Any] | None = None, body: Any = MISSING,
                         form: Mapping[str, Any] | None = None, files: Iterable[str] = ()) -> list[str]:
        """Problems that a request would have against ``op``'s parameters and request schema."""
        problems: list[str] = []
        given_path = dict(path_params or {})
        if sorted(given_path) != sorted(op.path_names):
            problems.append(f'{op.id}: path parameters {sorted(given_path)} do not match {sorted(op.path_names)}')
        declared = {p.name: p for p in op.parameters if p.location == 'query'}
        query = dict(query or {})
        for name in query:
            if name not in declared:
                problems.append(f'{op.id}: query parameter {name!r} is not declared (declared: {sorted(declared)})')
        for name, parameter in declared.items():
            if name not in query:
                if parameter.required:
                    problems.append(f'{op.id}: required query parameter {name!r} is missing')
                continue
            validator = self._validator(f'{op.id}:query:{name}', parameter.schema)
            for problem in schema_problems(validator, query[name]):
                problems.append(f'{op.id}: query parameter {name!r}{problem[1:]}' if problem.startswith('$') else problem)
        multipart = 'multipart/form-data' in op.request_bodies
        json_body = 'application/json' in op.request_bodies
        supplied = body is not MISSING or bool(form) or bool(files)
        if not op.request_bodies:
            if supplied:
                problems.append(f'{op.id}: the operation declares no request body')
        elif body is not MISSING:
            if not json_body:
                problems.append(f'{op.id}: the operation takes {sorted(op.request_bodies)}, not JSON')
            else:
                validator = self._validator(f'{op.id}:body', op.request_bodies['application/json'][1])
                problems += [f'{op.id} request body {p}' for p in schema_problems(validator, body)]
        elif form or files:
            if not multipart:
                problems.append(f'{op.id}: the operation takes {sorted(op.request_bodies)}, not multipart form data')
            else:
                instance = dict(form or {})
                instance.update({name: '<file>' for name in files})
                schema = self.dereference(op.request_bodies['multipart/form-data'][1])
                # Form values arrive as text: check names and presence, and types only where text is right.
                loose = self._validator(f'{op.id}:form', _text_only(schema))
                problems += [f'{op.id} request form {p}' for p in schema_problems(loose, instance)]
        else:
            required = [required for required, _ in op.request_bodies.values() if required]
            if required:
                problems.append(f'{op.id}: the request body is required')
        return problems

    # ---- responses

    def response_problems(self, method: str, path: str, status: int, headers: Mapping[str, str], body: bytes,
                          *, unrouted: bool = False, host_rejection: bool = False,
                          skip: Collection[str] = ()) -> list[str]:
        """Problems that a response has against the contract; empty means it conforms.

        ``path`` excludes the query string. ``unrouted`` says the request deliberately names no
        operation (the response must be the router's ``route_not_found``). ``host_rejection``
        says the request carried an untrusted Host header (the response must be the plain-text 400).
        ``skip`` names header checks to leave out of this exchange, so that a test about something
        else is not failed a second time for a deviation that has its own test:
        ``cache-control`` and ``version-header``.
        """
        headers = {name.lower(): value for name, value in headers.items()}
        content_type = headers.get('content-type', '').split(';')[0].strip().lower()
        what = f'{method.upper()} {path} -> {status}'
        if host_rejection:
            problems = []
            if status != 400:
                problems.append(f'{what}: an untrusted Host header must be refused with 400')
            if not content_type.startswith('text/plain') or body != b'Invalid host header':
                problems.append(f'{what}: the refusal body must be the plain text "Invalid host header" '
                                f'(got {content_type!r} {body[:60]!r})')
            if path.startswith('/api/') and 'version-header' not in skip:
                problems += self._version_problems(headers, what)  # even the plain-text refusal carries it
            return problems
        matched = self.match(method, path)
        if matched is None:
            return self._unrouted_problems(what, status, content_type, body, headers, allowed=unrouted, skip=skip)
        if unrouted:
            return [f'{what}: expected no operation to match, but {matched.operation} does']
        op = matched.operation
        where = f'{op.id} {status}'
        problems: list[str] = []
        if 'cache-control' not in skip:
            problems += self._cache_problems(op, status, headers, where)
        if 'version-header' not in skip:
            problems += self._version_problems(headers, where)
        if 200 <= status < 300 or status == 304:
            return problems + self._success_problems(op, status, content_type, body, headers, where)
        if status < 400:
            return problems + [f'{where}: redirects and other {status} responses are not documented']
        return problems + self._error_problems(op, status, content_type, body, headers, where)

    def _cache_problems(self, op: Operation, status: int, headers: Mapping[str, str], where: str) -> list[str]:
        if op.conditional and status in (200, 304):
            return []  # A conditional file (the cover) sets its own ETag and caching.
        if 'no-store' not in headers.get('cache-control', '').lower():
            return [f'{where}: every /api/ response carries Cache-Control: no-store '
                    f'(got {headers.get("cache-control")!r})']
        return []

    def _version_problems(self, headers: Mapping[str, str], where: str) -> list[str]:
        if self.version_header is None:
            return []
        value = headers.get(self.version_header.lower())
        if value != self.version:
            return [f'{where}: the {self.version_header} header is {value!r}; the contract requires '
                    f'{self.version!r} on every /api/ response']
        return []

    def _success_problems(self, op: Operation, status: int, content_type: str, body: bytes,
                          headers: Mapping[str, str], where: str) -> list[str]:
        if status == 304:
            if not op.conditional:
                return [f'{where}: 304 is not documented for this operation']
            return [f'{where}: a 304 response has a body'] if body else []
        if status == 206:
            if not op.ranges:
                return [f'{where}: 206 is not documented for this operation']
            span = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)', headers.get('content-range', ''))
            if span is None:
                return [f'{where}: a 206 response carries Content-Range: bytes <first>-<last>/<size> '
                        f'(got {headers.get("content-range")!r})']
            if int(span.group(2)) - int(span.group(1)) + 1 != len(body):
                return [f'{where}: the body has {len(body)} bytes but Content-Range says {headers["content-range"]!r}']
        elif status != 200:
            return [f'{where}: undocumented success status {status}; success is 200 (206 for a range, 304 if conditional)']
        media = op.media
        if media is not None:
            if not content_type.startswith(media.lower()):
                return [f'{where}: media type {content_type!r}, the contract says {media}']
            return []
        if content_type != 'application/json':
            return [f'{where}: media type {content_type!r}, the contract says application/json']
        parsed, problem = _parse_json(body)
        if problem:
            return [f'{where}: {problem}']
        return [f'{where}: {p}' for p in schema_problems(self.response_validator(op), parsed)]

    def _error_problems(self, op: Operation, status: int, content_type: str, body: bytes,
                        headers: Mapping[str, str], where: str) -> list[str]:
        documented = status in op.responses
        guard = status == 403 and op.method != 'GET'
        validation = status == 422 and (body.startswith(b'{"detail":[') or _error_code(body) == 'validation_error')
        if not (documented or guard or validation):
            return [f'{where}: undocumented error status {status} (documented: {sorted(op.responses)})']
        if content_type != 'application/json':
            return [f'{where}: error response is {content_type!r}, not application/json']
        parsed, problem = _parse_json(body)
        if problem:
            return [f'{where}: {problem}']
        problems = [f'{where}: {p}' for p in schema_problems(self.component_validator('Error'), parsed)]
        if status == 416 and not re.fullmatch(r'bytes \*/\d+', headers.get('content-range', '')):
            problems.append(f'{where}: a 416 response carries Content-Range: bytes */<size> '
                            f'(got {headers.get("content-range")!r})')
        code = parsed.get('code') if isinstance(parsed, dict) else None
        listed = op.error_codes.get(status)
        allowed_here = listed is not None and code in listed
        global_here = code in self.global_codes and status in self.global_codes[code]
        if isinstance(code, str) and not (allowed_here or global_here):
            problems.append(f'{where}: error code {code!r} is not documented for status {status} '
                            f'(operation lists {sorted(listed or [])}; global: '
                            f'{sorted(c for c, s in self.global_codes.items() if status in s)})')
        return problems

    def _unrouted_problems(self, what: str, status: int, content_type: str, body: bytes,
                           headers: Mapping[str, str], *, allowed: bool, skip: Collection[str] = ()) -> list[str]:
        if not allowed:
            return [f'{what}: no contract operation matches this request']
        problems = []
        if status not in (404, 405):
            problems.append(f'{what}: a path no operation serves must answer 404 (or 405 for a wrong method), '
                            'with code route_not_found')
        if content_type != 'application/json':
            return problems + [f'{what}: the router error is {content_type!r}, not application/json']
        parsed, problem = _parse_json(body)
        if problem:
            return problems + [f'{what}: {problem}']
        problems += [f'{what}: {p}' for p in schema_problems(self.component_validator('Error'), parsed)]
        if not isinstance(parsed, dict) or parsed.get('code') != 'route_not_found':
            problems.append(f'{what}: the router error code must be route_not_found '
                            f'(got {parsed.get("code") if isinstance(parsed, dict) else parsed!r})')
        if what.split(' ')[1].startswith('/api/'):
            if 'cache-control' not in skip and 'no-store' not in headers.get('cache-control', '').lower():
                problems.append(f'{what}: every /api/ response carries Cache-Control: no-store')
            if 'version-header' not in skip:
                problems += self._version_problems(headers, what)
        return problems


def _text_only(schema: dict) -> dict:
    """A multipart schema reduced to what text form fields can be checked against.

    Property schemas are dropped (a form carries text where the schema says integer or boolean), so
    the check covers required fields and undeclared names only.
    """
    reduced = {key: value for key, value in schema.items() if key not in ('properties', 'required')}
    reduced['properties'] = {name: {} for name in schema.get('properties', {})}
    if 'required' in schema:
        reduced['required'] = schema['required']
    return reduced


def _parse_json(body: bytes) -> tuple[Any, str | None]:
    def refuse(constant):
        raise ValueError(f'{constant} is not valid JSON')
    try:
        return json.loads(body.decode('utf-8'), parse_constant=refuse), None
    except (ValueError, UnicodeDecodeError) as error:
        return None, f'body is not valid JSON ({error})'


def _error_code(body: bytes) -> str | None:
    parsed, problem = _parse_json(body)
    return parsed.get('code') if not problem and isinstance(parsed, dict) else None
