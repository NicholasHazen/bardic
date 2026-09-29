"""Build a minimal valid JSON instance for a schema of the real contract (test data for the checker itself)."""
from __future__ import annotations

from typing import Any

from .contract import Contract

MAX_DEPTH = 5


class Unsupported(Exception):
    """The schema uses something this generator cannot satisfy."""


def sample(contract: Contract, schema: dict, closed: list, path: tuple = (), depth: int = 0, *, everything: bool = True) -> Any:
    """A value valid for ``schema``. ``closed`` collects the paths of objects whose schema is closed
    (declares properties and no additionalProperties): the places where an undeclared field must be refused."""
    schema = contract.dereference(schema)
    if 'const' in schema:
        return schema['const']
    if 'enum' in schema:
        return schema['enum'][0]
    branches = schema.get('anyOf') or schema.get('oneOf')
    if branches:
        branches = [contract.dereference(branch) for branch in branches]
        objects = [b for b in branches if b.get('type') != 'null']
        if depth >= MAX_DEPTH and any(b.get('type') == 'null' for b in branches):
            return None
        return sample(contract, objects[0], closed, path, depth, everything=everything) if objects else None
    kind = schema.get('type')
    if isinstance(kind, list):
        kind = next((k for k in kind if k != 'null'), 'null')
    if kind == 'object' or 'properties' in schema:
        value: dict[str, Any] = {}
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        names = list(properties) if everything and depth < 3 else [n for n in properties if n in required]
        for name in names:
            value[name] = sample(contract, properties[name], closed, path + (name,), depth + 1, everything=everything)
        for name in required:
            if name not in value:
                raise Unsupported(f'required property {name!r} has no schema')
        if 'properties' in schema and schema.get('additionalProperties', False) is False:
            closed.append(path)
        return value
    if kind == 'array':
        items = schema.get('items', {})
        count = max(schema.get('minItems', 0), 1 if depth < MAX_DEPTH else 0)
        return [sample(contract, items, closed, path + (i,), depth + 1, everything=everything) for i in range(count)]
    if kind == 'string':
        if 'pattern' in schema:
            raise Unsupported(f'string pattern {schema["pattern"]}')
        return 'x' * max(schema.get('minLength', 0), 1)
    if kind in ('integer', 'number'):
        low = schema.get('minimum')
        if 'exclusiveMinimum' in schema:
            low = schema['exclusiveMinimum'] + 1
        value = 0 if low is None else int(low) if float(low).is_integer() else low
        if 'maximum' in schema and value > schema['maximum']:
            value = schema['maximum']
        return value
    if kind == 'boolean':
        return True
    if kind == 'null':
        return None
    if not schema or schema == {'type': 'object'}:
        return {}
    raise Unsupported(f'schema {list(schema)}')
