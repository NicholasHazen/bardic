"""Speaker attribution, delivery, cues and scene notes per passage batch (LLM).

This wraps the existing fused directing request. Later pipeline revisions can
split attribution from delivery by adding steps that own subsets of these fields.
"""
from __future__ import annotations

from copy import deepcopy

from ... import analysis as a
from ...progressive import direction_specs
from ...staged_analysis import _split_chapter
from ..contract import Conflict, LLMRequest, Step, Unit, locked

SEGMENT_FIELDS = ('scene_id', 'speaker_id', 'confidence', 'direction', 'cues', 'evidence', 'analysis_provider', 'analysis_model')
SCENE_FIELDS = ('title', 'summary', 'tone', 'direction')
# A manual speaker choice also protects the confidence and evidence that justify it.
LOCK_FIELD = {'confidence': 'speaker_id', 'evidence': 'speaker_id'}


def _chapter_book(book, chapter_id):
    """The minimal book shape the annotation helpers need, detached from ``book``."""
    return {'chapters': [c for c in book['chapters'] if c['id'] == chapter_id], 'characters': book['characters'],
            'scenes': deepcopy([s for s in book['scenes'] if s['chapter_id'] == chapter_id]),
            'segments': deepcopy([s for s in book['segments'] if s['chapter_id'] == chapter_id])}


class DirectingStep(Step):
    id = 'directing'
    label = 'Speakers & delivery'
    summary = ('Annotates every passage with its speaker (with evidence and confidence), delivery notes and explicit '
               'vocal cues, summarizes scenes and proposes clear scene breaks. Low-confidence dialogue stays unassigned.')
    method = 'llm'
    scope = 'chapter'
    chapter_scoped = True
    capturable = True
    inputs = ('discovery', 'profiles')
    parallel = 2
    owns = ('scenes[]', 'scenes.character_ids', *('scenes.' + f for f in SCENE_FIELDS), *('segments.' + f for f in SEGMENT_FIELDS))

    def units(self, ctx):
        work = deepcopy(ctx.book)
        for segment in work['segments']:
            # Only a manual speaker choice becomes a REVIEWED ASSIGNMENT in the prompt.
            segment['edited'] = locked(segment, 'speaker_id')
        units = []
        for spec in direction_specs(work, ctx.selected_chapters()):
            batch = spec['batch']
            units.append(Unit(key=f"{spec['chapter_id']}:{spec['scene']['id']}:{batch[0]['id']}-{batch[-1]['id']}",
                              scope=spec['chapter_id'], label=f"{spec['scene']['title']} · {len(batch)} passages",
                              request=LLMRequest(spec['prompt'], spec['schema'], spec['output_cap']),
                              data={'scene_id': spec['scene']['id'], 'segment_ids': [s['id'] for s in batch]},
                              chapter_id=spec['chapter_id']))
        return units

    def _annotate(self, work, unit, result, boundaries):
        scene = next(s for s in work['scenes'] if s['id'] == unit.data['scene_id'])
        segments = {s['id']: s for s in work['segments']}
        a._apply_annotations(work, scene, [segments[i] for i in unit.data['segment_ids']], result, boundaries)

    def validate(self, ctx, unit, result):
        work = _chapter_book(ctx.book, unit.scope)
        for item in work['segments'] + work['scenes']:
            item.pop('edited', None)
        self._annotate(work, unit, deepcopy(result), {})
        return result

    def assemble(self, ctx, done):
        payloads = {}
        by_chapter = {}
        for unit, result in done:
            by_chapter.setdefault(unit.scope, []).append((unit, result))
        for chapter_id, items in by_chapter.items():
            # The payload is what the model proposed; manual edits are applied later as locks.
            work = _chapter_book(ctx.book, chapter_id)
            for item in work['segments'] + work['scenes']:
                item.pop('edited', None)
                item.pop('edited_fields', None)
            for scene in work['scenes']:
                scene['summary'] = ''
            boundaries = {}
            for unit, result in items:
                self._annotate(work, unit, deepcopy(result), boundaries)
                for segment_id in unit.data['segment_ids']:
                    segment = next(s for s in work['segments'] if s['id'] == segment_id)
                    segment.update(analysis_provider=ctx.provider, analysis_model=ctx.model)
            _split_chapter(work, chapter_id, boundaries)
            payloads[chapter_id] = self.capture(work, chapter_id)
        return payloads

    def capture(self, book, scope):
        scenes = [s for s in book['scenes'] if s['chapter_id'] == scope]
        segments = [s for s in book['segments'] if s['chapter_id'] == scope]
        # Only a chapter whose scenes partition exactly its passages can be restored.
        if not segments or sorted(i for s in scenes for i in s['segment_ids']) != sorted(s['id'] for s in segments):
            return None
        return {'scenes': [{'id': s['id'], **{f: s.get(f) for f in SCENE_FIELDS}, 'segment_ids': list(s['segment_ids'])} for s in scenes],
                'segments': {s['id']: {f: deepcopy(s.get(f)) for f in SEGMENT_FIELDS} for s in segments}}

    def apply(self, book, payloads):
        conflicts = []
        known = {c['id'] for c in book['characters']}
        by_chapter = {}
        for segment in book['segments']:
            by_chapter.setdefault(segment['chapter_id'], {})[segment['id']] = segment
        for chapter_id, payload in payloads.items():
            segments = by_chapter.get(chapter_id, {})
            current = [s for s in book['scenes'] if s['chapter_id'] == chapter_id]
            before = ([s['segment_ids'] for s in current], [(i, s.get('speaker_id')) for i, s in segments.items()])
            proposed = payload['scenes']
            covered = [i for scene in proposed for i in scene['segment_ids']]
            if sorted(covered) != sorted(segments):
                raise ValueError('A scene map version does not cover exactly this chapter\'s passages.')
            proposed_ids = {s['id'] for s in proposed}
            # A legacy whole-scene edit, or an edited scene the version would drop, keeps today's scene breaks.
            keep_partition = [s for s in current if (s.get('edited') and not isinstance(s.get('edited_fields'), list))
                              or (isinstance(s.get('edited_fields'), list) and s['edited_fields'] and s['id'] not in proposed_ids)]
            if keep_partition and [s['segment_ids'] for s in current] != [s['segment_ids'] for s in proposed]:
                conflicts.append(Conflict(chapter_id, keep_partition[0]['id'], 'scene_breaks',
                                          'A manually edited scene keeps the current scene breaks.'))
            existing = {s['id']: s for s in current}
            if keep_partition:
                replacement = current
            else:
                replacement = []
                for item in proposed:
                    scene = deepcopy(existing.get(item['id'], {'id': item['id'], 'chapter_id': chapter_id}))
                    scene['segment_ids'] = list(item['segment_ids'])
                    replacement.append(scene)
                    for segment_id in item['segment_ids']:
                        segments[segment_id]['scene_id'] = item['id']
            notes = {s['id']: s for s in proposed}
            for scene in replacement:
                for name in SCENE_FIELDS:
                    if scene['id'] not in notes:
                        continue
                    if locked(scene, name):
                        continue
                    value = notes[scene['id']].get(name)
                    if value is None:
                        scene.pop(name, None)
                    else:
                        scene[name] = value
            index = next((i for i, s in enumerate(book['scenes']) if s['chapter_id'] == chapter_id), None)
            others = [s for s in book['scenes'] if s['chapter_id'] != chapter_id]
            if index is None:
                order = {c['id']: i for i, c in enumerate(book['chapters'])}
                index = sum(order.get(s['chapter_id'], 0) < order.get(chapter_id, 0) for s in others)
            book['scenes'] = others[:index] + replacement + others[index:]
            for segment_id, values in payload['segments'].items():
                segment = segments.get(segment_id)
                if segment is None:
                    conflicts.append(Conflict(chapter_id, segment_id, 'passage', 'This passage no longer exists.'))
                    continue
                substituted = False
                for name in SEGMENT_FIELDS:
                    if name == 'scene_id':
                        continue
                    if locked(segment, LOCK_FIELD.get(name, name)):
                        if name in ('speaker_id', 'direction', 'cues') and values.get(name) != segment.get(name):
                            conflicts.append(Conflict(chapter_id, segment_id, name, 'Kept the manually edited value.'))
                        continue
                    value = deepcopy(values.get(name))
                    if name == 'speaker_id' and value is not None and value not in known:
                        conflicts.append(Conflict(chapter_id, segment_id, name, 'The proposed speaker is not in the cast; left unassigned.'))
                        value = 'unassigned'
                        substituted = True
                    if value is None:
                        segment.pop(name, None)
                    else:
                        segment[name] = value
                if substituted and not locked(segment, 'speaker_id'):
                    # The proposed speaker's confidence and evidence no longer describe this passage.
                    segment.update(confidence=0.0, evidence=[])
            # Derived membership is refreshed only when scene breaks or speakers changed.
            if before != ([s['segment_ids'] for s in replacement], [(i, s.get('speaker_id')) for i, s in segments.items()]):
                for scene in replacement:
                    scene['character_ids'] = list(dict.fromkeys(segments[i]['speaker_id'] for i in scene['segment_ids'] if i in segments))
        return conflicts

    def summarize(self, book, payloads):
        names = {c['id']: c['name'] for c in book['characters']}
        texts = {s['id']: (s['text'], s['kind']) for s in book['segments']}
        order = {s['id']: i for i, s in enumerate(book['segments'])}
        rows, scenes = [], 0
        for scope, payload in payloads.items():
            titles = {s['id']: s.get('title') or '' for s in payload['scenes']}
            scenes += len(payload['scenes'])
            for segment_id, values in payload['segments'].items():
                text, kind = texts.get(segment_id, ('', ''))
                rows.append({'id': segment_id, 'scope': scope, 'scene': titles.get(values.get('scene_id'), ''),
                             'kind': kind, 'text': text if len(text) <= 160 else text[:157] + '…',
                             'speaker': names.get(values.get('speaker_id'), values.get('speaker_id') or ''),
                             'confidence': values.get('confidence'), 'direction': values.get('direction') or '',
                             'cues': ', '.join(values.get('cues') or [])})
        rows.sort(key=lambda row: order.get(row['id'], len(order)))
        dialogue = [r for r in rows if r['kind'] == 'dialogue']
        unassigned = sum(payloads[r['scope']]['segments'][r['id']].get('speaker_id') == 'unassigned' for r in dialogue)
        return {'stats': {'sections': len(payloads), 'scenes': scenes, 'passages': len(rows), 'dialogue': len(dialogue),
                          'unassigned_dialogue': unassigned, 'attributed_dialogue': len(dialogue) - unassigned},
                'columns': [{'key': 'scene', 'label': 'Scene'}, {'key': 'text', 'label': 'Passage'},
                            {'key': 'speaker', 'label': 'Speaker'}, {'key': 'confidence', 'label': 'Confidence'},
                            {'key': 'direction', 'label': 'Delivery'}, {'key': 'cues', 'label': 'Cues'}],
                'rows': rows}
