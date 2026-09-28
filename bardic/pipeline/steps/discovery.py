"""Find speaking characters with exact source evidence (LLM, economy tier)."""
from __future__ import annotations

from ... import analysis as a
from ..prompts import discovery_specs
from ..contract import Conflict, LLMRequest, RESERVED_CHARACTERS, Step, Unit, locked


class DiscoveryStep(Step):
    id = 'discovery'
    label = 'Character discovery'
    summary = ('Scans each story section in ~24,000-character ranges for named or distinct speakers, '
               'each backed by exact quotations. Adds new characters and aliases; never removes or renames one.')
    method = 'llm'
    scope = 'chapter'
    chapter_scoped = True
    accumulative = True
    parallel = 3
    default_model_role = 'scan'
    owns = ('characters[]', 'characters.aliases', 'characters.evidence')

    def units(self, ctx):
        units = []
        for chapter in ctx.selected_chapters():
            for spec in discovery_specs(ctx.book, [chapter]):
                units.append(Unit(key=f"{chapter['id']}:{spec['start']}-{spec['end']}", scope=chapter['id'],
                                  label=f"{chapter['title']} · characters {spec['start']:,}–{spec['end']:,}",
                                  request=LLMRequest(spec['prompt'], spec['schema'], spec['output_cap']),
                                  data={'source': spec['source'], 'start': spec['start'], 'end': spec['end']},
                                  chapter_id=chapter['id']))
        return units

    def validate(self, ctx, unit, result):
        a._cast_result(result, unit.data['source'])
        return result

    def assemble(self, ctx, done):
        payloads = {}
        for unit, result in sorted(done, key=lambda item: (item[0].scope, item[0].data['start'])):
            payload = payloads.setdefault(unit.scope, {'ranges': [], 'candidates': []})
            payload['ranges'].append({'start': unit.data['start'], 'end': unit.data['end'], 'unit': unit.key})
            for item in result['characters']:
                payload['candidates'].append({**{k: item[k] for k in ('name', 'aliases', 'description', 'direction', 'evidence')},
                                              'range_start': unit.data['start']})
        return payloads

    def apply(self, book, payloads):
        conflicts = []
        order = {c['id']: index for index, c in enumerate(book['chapters'])}
        for scope in sorted(payloads, key=lambda s: order.get(s, len(order))):
            candidates = payloads[scope]['candidates']
            # Names a person replaced still identify that character, without becoming visible aliases.
            remembered = {}
            for character in book['characters']:
                known = {a._name_key(n) for n in [character['name'], *character.get('aliases', [])]}
                extra = [n for n in character.get('former_names', []) if a._name_key(n) not in known]
                if extra:
                    remembered[character['id']] = extra
                    character['aliases'] = [*character.get('aliases', []), *extra]
            keep = []
            for candidate in candidates:
                names = {a._name_key(n) for n in [candidate['name'], *candidate.get('aliases', [])]}
                taken = any(a._name_key(n) in names for c in book['characters'] for n in [c['name'], *c.get('aliases', [])])
                if a._resolve_character_candidate(book['characters'], candidate) is None and taken:
                    conflicts.append(Conflict(scope, candidate['name'], 'identity',
                                              'The name or alias matches more than one character; left for review.'))
                    continue
                keep.append(candidate)
            # Discovery may add people and aliases; profile text belongs to the profile step.
            before = {c['id']: {k: c.get(k) for k in ('name', 'description', 'direction', 'aliases', 'edited')}
                      for c in book['characters']}
            # _merge_cast freezes aliases on the legacy whole-item flag; use the per-field lock instead.
            for character in book['characters']:
                character['edited'] = locked(character, 'aliases')
            a._merge_cast(book, keep)
            for character in book['characters']:
                previous = before.get(character['id'])
                if not previous:
                    character.pop('edited', None)
                    continue
                if previous['edited'] is None:
                    character.pop('edited', None)
                else:
                    character['edited'] = previous['edited']
                if character['id'] in RESERVED_CHARACTERS:
                    continue
                for name in ('name', 'description', 'direction'):
                    character[name] = previous[name]
                if locked(character, 'aliases'):
                    character['aliases'] = previous['aliases']
            for character in book['characters']:
                extra = remembered.get(character['id'])
                if extra:
                    hidden = {a._name_key(n) for n in extra}
                    character['aliases'] = [n for n in character.get('aliases', []) if a._name_key(n) not in hidden]
        return conflicts

    def summarize(self, book, payloads):
        titles = {c['id']: c['title'] for c in book['chapters']}
        rows, names = [], set()
        for scope, payload in payloads.items():
            for candidate in payload['candidates']:
                key = a._name_key(candidate['name'])
                names.add(key)
                rows.append({'id': f"{scope}:{candidate['range_start']}:{key}", 'scope': scope,
                             'chapter': titles.get(scope, scope), 'name': candidate['name'],
                             'aliases': ', '.join(candidate.get('aliases', [])), 'evidence': len(candidate['evidence']),
                             'description': candidate.get('description', '')})
        return {'stats': {'sections': len(payloads), 'mentions': len(rows), 'distinct_names': len(names),
                          'ranges': sum(len(p['ranges']) for p in payloads.values())},
                'columns': [{'key': 'chapter', 'label': 'Section'}, {'key': 'name', 'label': 'Name'},
                            {'key': 'aliases', 'label': 'Aliases'}, {'key': 'evidence', 'label': 'Quotes'},
                            {'key': 'description', 'label': 'Draft notes'}],
                'rows': rows}
