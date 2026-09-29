"""A small OpenAPI 3.1 document for testing the harness itself, in the style of the real contract."""
from __future__ import annotations

import copy

from ..contract import Contract

INFO = """\
A tiny contract.

## Work and errors

- Any operation can also return these global codes:
  - `validation_error` (422): the request failed validation.
  - `cross_origin_write` (403): the write guard rejected a browser write.
  - `internal_error` (500): an unexpected server defect.
  - `route_not_found` (404, 405): no route matches the method and path.
"""


def _errors(*statuses_and_codes):
    out = {}
    for status, codes in statuses_and_codes:
        out[str(status)] = {'description': 'x', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}},
                            **({'x-bardic-error-codes': list(codes)} if codes else {})}
    return out


def _json(schema):
    return {'200': {'description': 'ok', 'content': {'application/json': {'schema': schema}}}}


def document() -> dict:
    thing = {'$ref': '#/components/schemas/Thing'}
    return {
        'openapi': '3.1.0',
        'info': {'title': 'Tiny', 'version': '9.9.9', 'description': INFO},
        'paths': {
            '/api/things': {
                'get': {'operationId': 'listThings', 'parameters': [
                    {'name': 'limit', 'in': 'query', 'required': False, 'schema': {'type': 'integer', 'default': 10}}],
                        'responses': {**_json({'type': 'array', 'items': thing}), **_errors((422, ['validation_error']), (500, ['internal_error']))}},
                'post': {'operationId': 'createThing',
                         'requestBody': {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/NewThing'}}}},
                         'responses': {**_json(thing), **_errors((400, ['name_taken']), (403, ['cross_origin_write']),
                                                                (422, ['validation_error']), (500, ['internal_error']))}},
            },
            '/api/things/{thing_id}': {
                'get': {'operationId': 'getThing', 'parameters': [{'name': 'thing_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}}],
                        'responses': {**_json(thing), **_errors((404, ['thing_not_found']), (422, ['validation_error']), (500, ['internal_error']))}},
            },
            '/api/upload': {
                'post': {'operationId': 'uploadThing',
                         'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': {'$ref': '#/components/schemas/UploadForm'}}}},
                         'responses': {**_json({'$ref': '#/components/schemas/Thing'}), **_errors((413, ['upload_too_large']), (500, ['internal_error']))}},
            },
            '/api/things/latest': {
                'get': {'operationId': 'getLatestThing', 'responses': {**_json(thing), **_errors((500, ['internal_error']))}},
            },
            '/api/things/{thing_id}/file': {
                'get': {'operationId': 'getThingFile', 'parameters': [{'name': 'thing_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}}],
                        'responses': {
                            '200': {'description': 'pdf', 'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}},
                            '206': {'description': 'part', 'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}},
                            **_errors((404, ['thing_not_found']), (416, ['range_not_satisfiable']), (500, ['internal_error']))}},
            },
            '/api/things/{thing_id}/picture': {
                'get': {'operationId': 'getThingPicture', 'parameters': [{'name': 'thing_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}}],
                        'responses': {
                            '200': {'description': 'jpeg', 'content': {'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}}}},
                            '304': {'description': 'not modified'},
                            **_errors((404, ['thing_not_found']), (500, ['internal_error']))}},
            },
        },
        'components': {'schemas': {
            'UploadForm': {'type': 'object', 'required': ['file'], 'properties': {
                'file': {'type': 'string', 'contentMediaType': 'application/octet-stream'}, 'count': {'type': 'integer'}}},
            'Owner': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'address': {'$ref': '#/components/schemas/Address'}},
                      'required': ['name']},
            'Address': {'type': 'object', 'properties': {'city': {'type': 'string'}}, 'required': ['city']},
            'Thing': {'type': 'object', 'required': ['id', 'size', 'ok'], 'properties': {
                'id': {'type': 'string'},
                'size': {'type': 'integer'},
                'ok': {'type': 'boolean'},
                'ratio': {'type': 'number'},
                'kind': {'enum': ['a', 'b'], 'type': 'string'},
                'tags': {'type': 'array', 'items': {'type': 'string'}},
                'owner': {'anyOf': [{'$ref': '#/components/schemas/Owner'}, {'type': 'null'}]},
                'labels': {'type': 'object', 'additionalProperties': {'type': 'string'}},
                'anything': {'type': 'object', 'additionalProperties': True},
                'freeform': {'type': 'object'},
            }},
            'NewThing': {'type': 'object', 'required': ['name'], 'additionalProperties': False,
                         'properties': {'name': {'type': 'string', 'minLength': 1}, 'size': {'type': 'integer'}}},
            'Error': {'type': 'object', 'required': ['detail', 'code'], 'properties': {
                'detail': {'anyOf': [{'type': 'string'}, {'type': 'array', 'items': {'$ref': '#/components/schemas/Issue'}}]},
                'code': {'type': 'string'}}},
            'Issue': {'type': 'object', 'required': ['loc', 'msg'], 'properties': {
                'loc': {'type': 'array', 'items': {'anyOf': [{'type': 'string'}, {'type': 'integer'}]}}, 'msg': {'type': 'string'}}},
        }},
    }


def contract(**changes) -> Contract:
    doc = copy.deepcopy(document())
    doc['info'].update(changes.get('info', {}))
    return Contract(doc, 'tiny')


GOOD_THING = {'id': 't1', 'size': 3, 'ok': True}
NO_STORE = {'content-type': 'application/json', 'cache-control': 'no-store'}
