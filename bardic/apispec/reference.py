"""Render the contract as a readable Markdown reference (contract/API-REFERENCE.md)."""
from __future__ import annotations

import json
import re

HEADER = """\
<!-- Generated from contract/openapi.json by `uv run --frozen python -m bardic.apispec`. Do not edit. -->
"""


def _name(ref: str) -> str:
    return ref.rsplit('/', 1)[-1]


def _anchor(name: str) -> str:
    return 'schema-' + re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def type_of(schema: dict | None) -> str:
    """A compact, linked type expression for a JSON schema."""
    if not schema:
        return 'any'
    if '$ref' in schema:
        name = _name(schema['$ref'])
        return f'[{name}](#{_anchor(name)})'
    for key in ('anyOf', 'oneOf'):
        if key in schema:
            parts = [type_of(item) for item in schema[key]]
            return ' \\| '.join(dict.fromkeys(parts))
    if 'const' in schema:
        return f'`{json.dumps(schema["const"])}`'
    if 'enum' in schema:
        return ' \\| '.join(f'`"{value}"`' if isinstance(value, str) else f'`{value}`' for value in schema['enum'])
    kind = schema.get('type')
    if kind == 'array':
        return f'list of {type_of(schema.get("items"))}'
    if kind == 'object':
        extra = schema.get('additionalProperties')
        if isinstance(extra, dict) and extra and not schema.get('properties'):
            return f'map of string → {type_of(extra)}'
        return 'object'
    if kind == 'string' and schema.get('format') == 'binary':
        return 'binary'
    if isinstance(kind, list):
        return ' \\| '.join(kind)
    return kind or 'any'


def _cell(text: str | None) -> str:
    return (text or '').replace('\n', ' ').replace('|', '\\|').strip()


def _limits(schema: dict) -> str:
    notes = []
    for key, label in (('minimum', '≥'), ('exclusiveMinimum', '>'), ('maximum', '≤'), ('exclusiveMaximum', '<'),
                       ('minLength', 'min length'), ('maxLength', 'max length'), ('minItems', 'min items'),
                       ('maxItems', 'max items'), ('pattern', 'pattern')):
        if key in schema:
            notes.append(f'{label} `{schema[key]}`')
    if 'default' in schema:
        notes.append(f'default `{json.dumps(schema["default"])}`')
    return '; '.join(notes)


def _properties(schema: dict) -> list[str]:
    properties = schema.get('properties', {})
    if not properties:
        return []
    required = set(schema.get('required', []))
    lines = ['| Field | Type | Required | Description |', '| --- | --- | --- | --- |']
    for field, spec in properties.items():
        description = _cell(spec.get('description'))
        limits = _limits(spec)
        if limits:
            description = f'{description} ({limits})' if description else limits
        lines.append(f'| `{field}` | {type_of(spec)} | {"yes" if field in required else ""} | {description} |')
    return lines


def render(schema: dict) -> str:
    out = [HEADER, f'# {schema["info"]["title"]} {schema["info"]["version"]}', '', schema['info']['description'].strip(), '']
    operations: dict[str, list[tuple[str, str, dict]]] = {tag['name']: [] for tag in schema.get('tags', [])}
    for path, methods in schema['paths'].items():
        for method, operation in methods.items():
            for tag in operation.get('tags', ['Undocumented']):
                operations.setdefault(tag, []).append((method.upper(), path, operation))

    out += ['## Operations', '']
    for tag in schema.get('tags', []):
        entries = operations.get(tag['name'], [])
        out.append(f'- **{tag["name"]}** — ' + ', '.join(
            f'[`{m} {p}`](#{operation["operationId"].lower()})' for m, p, operation in entries))
    out.append('')

    descriptions = {tag['name']: tag.get('description', '') for tag in schema.get('tags', [])}
    for tag, entries in operations.items():
        if not entries:
            continue
        out += [f'## {tag}', '', descriptions.get(tag, ''), '']
        for method, path, operation in entries:
            out += [f'<a id="{operation["operationId"].lower()}"></a>', f'### `{method} {path}`', '',
                    f'**{operation.get("summary", "")}** · operation `{operation["operationId"]}` · '
                    f'cost `{operation.get("x-bardic-cost", "?")}`', '', operation.get('description', '').strip(), '']
            parameters = operation.get('parameters', [])
            if parameters:
                out += ['| Parameter | In | Type | Required | Description |', '| --- | --- | --- | --- | --- |']
                for parameter in parameters:
                    spec = parameter.get('schema', {})
                    description = _cell(parameter.get('description'))
                    limits = _limits(spec)
                    if limits:
                        description = f'{description} ({limits})' if description else limits
                    out.append(f'| `{parameter["name"]}` | {parameter["in"]} | {type_of(spec)} | '
                               f'{"yes" if parameter.get("required") else ""} | {description} |')
                out.append('')
            body = operation.get('requestBody')
            if body:
                for media, content in body.get('content', {}).items():
                    out += [f'Request body (`{media}`): {type_of(content.get("schema"))}', '']
            out += ['| Status | Body | Meaning |', '| --- | --- | --- |']
            for status, response in operation.get('responses', {}).items():
                for media, content in (response.get('content') or {'': {}}).items():
                    body_type = type_of(content.get('schema')) if media else ''
                    if media and media != 'application/json':
                        body_type = f'`{media}`'
                    out.append(f'| {status} | {body_type} | {_cell(response.get("description"))} |')
            out.append('')

    out += ['## Schemas', '']
    for name, component in schema.get('components', {}).get('schemas', {}).items():
        out += [f'<a id="{_anchor(name)}"></a>', f'### {name}', '']
        if component.get('description'):
            out += [component['description'].strip(), '']
        if 'enum' in component or 'anyOf' in component:
            out += [f'Type: {type_of({k: v for k, v in component.items() if k != "title"})}', '']
        out += _properties(component)
        out.append('')
    return '\n'.join(out).rstrip() + '\n'
