"""Chapter names and section kinds, re-read from the saved original (plain)."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ..contract import Step, Unit
from ...errors import Invalid
from ...structure import CHAPTER_FIELDS, STRUCTURE_VERSION, repair_structure

MAX_ORIGINAL_BYTES = 30 * 1024 * 1024


class StructureStep(Step):
    id = 'structure'
    label = 'Chapters & titles'
    summary = ('Reads chapter names and front/back matter from the saved EPUB navigation or text headings. '
               'Metadata only: chapter text, order and IDs never change.')
    method = 'plain'
    capturable = True
    scope = 'book'
    owns = tuple('chapters.' + name for name in CHAPTER_FIELDS) + ('book.structure_version',)

    def units(self, ctx):
        return [Unit(key='book', scope='book', label='Saved original')]

    def execute(self, ctx, unit):
        book = ctx.book
        suffix = Path(book.get('source_name', '')).suffix.lower()
        originals = (Path(ctx.store.root) / 'originals').resolve()
        path = (originals / book['id'] / f'source{suffix}').resolve()
        if suffix not in {'.epub', '.txt'} or not path.is_relative_to(originals) or not path.is_file():
            raise ValueError('This book has no saved original EPUB or text file to read structure from.')
        data = path.read_bytes()
        if len(data) > MAX_ORIGINAL_BYTES:
            raise ValueError('The saved original is too large to re-read.')
        repaired = repair_structure(book, book['source_name'], data)
        return self.capture(repaired, 'book')

    def assemble(self, ctx, done):
        return {'book': done[0][1]} if done else {}

    def capture(self, book, scope):
        return {'structure_version': book.get('structure_version'),
                'chapters': [{'id': c['id'], **{f: deepcopy(c[f]) for f in CHAPTER_FIELDS if f in c}} for c in book['chapters']]}

    def apply(self, book, payloads):
        payload = payloads.get('book')
        if not payload:
            return []
        updated = {c['id']: c for c in payload['chapters']}
        if set(updated) != {c['id'] for c in book['chapters']}:
            raise Invalid('version_incompatible', 'The structure version does not match this book\'s chapters.')
        for chapter in book['chapters']:
            for name in CHAPTER_FIELDS:
                chapter.pop(name, None)
                if name in updated[chapter['id']]:
                    chapter[name] = deepcopy(updated[chapter['id']][name])
        if payload.get('structure_version') is None:
            book.pop('structure_version', None)
        else:
            book['structure_version'] = payload['structure_version']
        return []

    def summarize(self, book, payloads):
        payload = payloads.get('book') or {'chapters': []}
        rows = [{'id': c['id'], 'scope': 'book', 'title': c.get('title', ''), 'kind': c.get('kind', 'section'),
                 'source': c.get('title_source', '')} for c in payload['chapters']]
        kinds = {}
        for row in rows:
            kinds[row['kind']] = kinds.get(row['kind'], 0) + 1
        return {'stats': {'sections': len(rows), **{f'{kind}': count for kind, count in sorted(kinds.items())},
                          'structure_version': payload.get('structure_version') or STRUCTURE_VERSION},
                'columns': [{'key': 'title', 'label': 'Title'}, {'key': 'kind', 'label': 'Kind'},
                            {'key': 'source', 'label': 'Named from'}],
                'rows': rows}
