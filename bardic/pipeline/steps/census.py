"""Free whole-book counts that guide how much effort later steps spend (plain)."""
from __future__ import annotations

from ..contract import Step, Unit
from ...preprocessing import census


class CensusStep(Step):
    id = 'census'
    label = 'Name & dialogue census'
    summary = ('Counts speech tags, name mentions, dialogue and chapter spread without a model. '
               'Frequency guides profile effort; it does not prove identity or presence.')
    method = 'plain'
    scope = 'book'
    owns = ()

    def units(self, ctx):
        return [Unit(key='book', scope='book', label='Whole book')]

    def execute(self, ctx, unit):
        return census(ctx.book, ctx.store)

    def assemble(self, ctx, done):
        return {'book': done[0][1]} if done else {}

    def summarize(self, book, payloads):
        payload = payloads.get('book') or {}
        rows = [{'id': c['id'], 'scope': 'book', 'name': c['name'], 'priority': c.get('priority', ''),
                 'mentions': c.get('mentions', 0), 'speech_tags': c.get('explicit_speech_tags', 0),
                 'dialogue_turns': c.get('dialogue_turns', 0), 'chapters': c.get('chapter_count', 0),
                 'known': 'yes' if c.get('known_character') else 'candidate'}
                for c in payload.get('characters', [])]
        return {'stats': {'words': payload.get('words', 0), 'eligible_sections': payload.get('eligible_chapters', 0),
                          'name_candidates': len(rows),
                          'estimated_source_tokens': payload.get('estimated_source_tokens', 0)},
                'columns': [{'key': 'name', 'label': 'Name'}, {'key': 'known', 'label': 'Cast'},
                            {'key': 'priority', 'label': 'Priority'}, {'key': 'mentions', 'label': 'Mentions'},
                            {'key': 'speech_tags', 'label': 'Speech tags'}, {'key': 'dialogue_turns', 'label': 'Dialogue'},
                            {'key': 'chapters', 'label': 'Chapters'}],
                'rows': rows}
