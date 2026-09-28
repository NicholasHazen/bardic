"""Build each character's evidence-backed voice and personality profile (LLM)."""
from __future__ import annotations

from copy import deepcopy

from ... import analysis as a
from ...progressive import profile_specs
from ..contract import Conflict, LLMRequest, Step, Unit, locked

FIELDS = ('description', 'direction', 'profile_refined', 'profile_provider', 'profile_model', 'profile_priority')


class ProfilesStep(Step):
    id = 'profiles'
    label = 'Character profiles'
    summary = ('Builds a vocal and personality profile per character from the accepted discovery evidence, '
               'with bounded context from confirmed earlier series volumes. Main characters get more evidence.')
    method = 'llm'
    capturable = True
    scope = 'character'
    inputs = ('discovery',)
    parallel = 3
    owns = tuple('characters.' + name for name in FIELDS)

    def units(self, ctx):
        observations = []
        for scope, payload in ctx.inputs.get('discovery', {}).items():
            for window in payload['ranges']:
                candidates = [c for c in payload['candidates'] if c['range_start'] == window['start']]
                observations.append({'chapter_id': scope, 'unit_key': window['unit'], 'result': {'characters': candidates}})
        work = deepcopy(ctx.book)
        for character in work['characters']:
            # Legacy code skips 'edited' characters; here only locked profile text counts.
            character['edited'] = locked(character, 'description') and locked(character, 'direction')
        units = []
        for spec in profile_specs(work, ctx.store, observations):
            units.append(Unit(key=spec['character_id'], scope=spec['character_id'],
                              label=f"{spec['name']} · {spec['priority']} profile",
                              request=LLMRequest(spec['prompt'], spec['schema'], spec['output_cap']),
                              data={'evidence': spec['evidence'], 'name': spec['name'], 'aliases': spec['aliases'],
                                    'priority': spec['priority']},
                              dependencies=tuple(spec.get('series_context_artifact_ids', []))))
        return units

    def validate(self, ctx, unit, result):
        cast = a._profile_result(result, unit.data['evidence'])
        if len(cast) != 1 or cast[0]['name'] != unit.data['name'] or set(cast[0]['aliases']) != set(unit.data['aliases']):
            raise ValueError('Profile refinement must preserve the selected character identity and aliases.')
        return result

    def assemble(self, ctx, done):
        payloads = {}
        for unit, result in done:
            profile = result['characters'][0]
            payloads[unit.scope] = {'description': profile['description'], 'direction': profile['direction'],
                                    'evidence': profile['evidence'], 'profile_refined': True,
                                    'profile_provider': ctx.provider, 'profile_model': ctx.model,
                                    'profile_priority': unit.data['priority']}
        return payloads

    def capture(self, book, scope):
        character = next((c for c in book['characters'] if c['id'] == scope), None)
        if character is None:
            return None
        return {name: deepcopy(character[name]) for name in FIELDS if name in character}

    def apply(self, book, payloads):
        conflicts = []
        characters = {c['id']: c for c in book['characters']}
        for scope, payload in payloads.items():
            character = characters.get(scope)
            if character is None:
                conflicts.append(Conflict(scope, scope, 'character', 'This character is no longer in the cast.'))
                continue
            for name in FIELDS:
                if locked(character, name):
                    if name in ('description', 'direction') and payload.get(name) not in (None, character.get(name)):
                        conflicts.append(Conflict(scope, scope, name, 'Kept the manually edited value.'))
                    continue
                if payload.get(name) is None:
                    character.pop(name, None)
                else:
                    character[name] = deepcopy(payload[name])
        return conflicts

    def summarize(self, book, payloads):
        names = {c['id']: c['name'] for c in book['characters']}
        rows = [{'id': scope, 'scope': scope, 'name': names.get(scope, scope), 'priority': p.get('profile_priority') or '',
                 'description': p.get('description', ''), 'direction': p.get('direction', ''),
                 'evidence': len(p.get('evidence') or [])}
                for scope, p in payloads.items()]
        rows.sort(key=lambda row: row['name'].casefold())
        return {'stats': {'profiles': len(rows), 'refined': sum(bool(p.get('profile_refined')) for p in payloads.values())},
                'columns': [{'key': 'name', 'label': 'Character'}, {'key': 'priority', 'label': 'Effort'},
                            {'key': 'description', 'label': 'Profile'}, {'key': 'direction', 'label': 'Voice direction'},
                            {'key': 'evidence', 'label': 'Quotes'}],
                'rows': rows}
