"""Speaker attribution, delivery, cues and scene notes per passage batch (LLM or service).

With an LLM provider this wraps the existing fused directing request. Two
self-hosted services are alternative providers whose versions compare directly
with a model's: the Novel Analyzer (speakers, delivery, cues and scene breaks
per chapter) and BookNLP (speakers from the accepted Quote attribution step,
with only the manner its speech tags state). Later pipeline revisions can split
attribution from delivery by adding steps that own subsets of these fields.

When Quote attribution is accepted for a chapter, every provider's proposal is
checked against it: agreement raises the speaker confidence, disagreement caps
it, and ``speaker_check`` records the comparison. See ``_check_speakers``.
"""
from __future__ import annotations

from copy import deepcopy

from ... import analysis as a
from ... import local_services as ls
from ...errors import Invalid
from ...progressive import direction_specs
from ...staged_analysis import _split_chapter
from ..contract import LLM_PROVIDERS, Conflict, LLMRequest, ServiceRequest, Step, Unit, locked
from .quotes import CHECK_LABELS, compare

SEGMENT_FIELDS = ('scene_id', 'speaker_id', 'confidence', 'direction', 'cues', 'evidence', 'analysis_provider',
                  'analysis_model', 'speaker_check')
SCENE_FIELDS = ('title', 'summary', 'tone', 'direction')
# A manual speaker choice also protects the confidence, evidence and check that justify it.
LOCK_FIELD = {'confidence': 'speaker_id', 'evidence': 'speaker_id', 'speaker_check': 'speaker_id'}
# Present only on passages a check covered; absent (not null) otherwise, so older payloads still match.
OPTIONAL_FIELDS = ('speaker_check',)

# Confidence for service attributions, which report none. As with model output, a line below 0.65 stays unassigned.
TAGGED = .85      # a speech tag names the speaker
BEAT = .75        # an action beat by the speaker sits beside the line
UNTAGGED = .7     # inferred from turn-taking and context
CONFLICTED = .4   # BookNLP's own tag names someone else: left unassigned
# The BookNLP check: two independent methods agreeing, or disagreeing. These are rankings chosen
# to fit the 0.65 assignment rule, not measured probabilities.
AGREE_FLOOR = .9            # BookNLP had a tag or beat beside the line
AGREE_FLOOR_UNTAGGED = .8   # both inferred from turn-taking, which can drift the same way
DISAGREE_CAP = .65
# A service result must label this share of a section's dialogue passages, or it is rejected
# (the analyzer does not detect single-quoted or dash dialogue, which the importer does).
MIN_COVERAGE = .9
# The analyzer gives one speaker per paragraph; text without blank-line paragraphs would merge speakers.
MIN_PARAGRAPHS_PER_DIALOGUE = .25
PLAIN_SPEECH = frozenset({'say', 'ask', 'reply', 'answer', 'tell', 'add', 'continue', 'respond'})
# Adverbs about time or discourse, not delivery ("she said then", "he was smiling, though").
NOT_MANNER = frozenset({'then', 'though', 'again', 'finally', 'already', 'still', 'just', 'now', 'also', 'too', 'even',
                        'only', 'later', 'soon', 'once', 'instead', 'anyway', 'back', 'first', 'next', 'yet', 'so'})


def _require_coverage(label, dialogue_ids, covered):
    """Reject a service result that labelled too little of a section's dialogue.

    Using it would silently turn every line it missed into unassigned dialogue.
    """
    if not dialogue_ids:
        return
    labelled = sum(i in covered for i in dialogue_ids)
    if labelled < MIN_COVERAGE * len(dialogue_ids):
        raise ValueError(f'{label} labelled {labelled} of {len(dialogue_ids)} dialogue passages in this section '
                         '(it may not detect single-quoted or dash-introduced dialogue). Its result was not used.')


def _manner(tag):
    """The delivery a BookNLP speech tag states outright ("Growled.", "Softly.", "Sighed heavily, with a smile."), or ''."""
    if not tag or tag.get('kind') != 'speech':
        return ''
    head = [tag['verb']] if tag.get('lemma') and tag['lemma'] not in PLAIN_SPEECH and tag.get('verb') else []
    head += [word for word in tag.get('adverbs') or [] if word.casefold() not in NOT_MANNER]
    parts = ([' '.join(head)] if head else []) + [f'with {phrase}' for phrase in tag.get('with') or []]
    text = ', '.join(parts)
    return text[:1].upper() + text[1:] + '.' if text else ''


def _chapter_book(book, chapter_id):
    """The minimal book shape the annotation helpers need, detached from ``book``."""
    return {'chapters': [c for c in book['chapters'] if c['id'] == chapter_id], 'characters': book['characters'],
            'scenes': deepcopy([s for s in book['scenes'] if s['chapter_id'] == chapter_id]),
            'segments': deepcopy([s for s in book['segments'] if s['chapter_id'] == chapter_id])}


class DirectingStep(Step):
    id = 'directing'
    label = 'Speakers & delivery'
    summary = ('Annotates every passage with its speaker (with evidence and confidence), delivery notes and explicit '
               'vocal cues, summarizes scenes and proposes clear scene breaks. Low-confidence dialogue stays unassigned. '
               'Use a model, your Novel Analyzer server, or BookNLP (speakers only, from Quote attribution). '
               'An accepted Quote attribution checks every provider\'s speakers.')
    method = 'llm'
    providers = (*LLM_PROVIDERS, 'novel_analyzer', 'booknlp')
    scope = 'chapter'
    chapter_scoped = True
    capturable = True
    inputs = ('discovery', 'profiles', 'quotes')
    # Passages are attributed to the cast in the book; discovery is recorded for staleness only.
    # Quote attribution is optional: when accepted it checks speakers (and BookNLP as provider needs it).
    requires = ('profiles',)
    parallel = 2
    # 2: adds the BookNLP speaker check to every provider's assembly. Model requests and their
    # validation are unchanged, so cached units from version 1 stay valid and are reused.
    version = 2
    request_version = 1
    # BookNLP here reads the accepted Quote attribution version and makes no request.
    offline_providers = ('booknlp',)
    owns = ('scenes[]', 'scenes.character_ids', *('scenes.' + f for f in SCENE_FIELDS), *('segments.' + f for f in SEGMENT_FIELDS))

    def units(self, ctx):
        if ctx.provider == 'novel_analyzer':
            return self._analyzer_units(ctx)
        if ctx.provider == 'booknlp':
            return [Unit(key=chapter['id'], scope=chapter['id'], label=chapter.get('title') or chapter['id'])
                    for chapter in self._chapters(ctx)]
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

    def _chapters(self, ctx):
        with_passages = {s['chapter_id'] for s in ctx.book['segments']}
        return [c for c in ctx.selected_chapters() if c['id'] in with_passages]

    def _analyzer_units(self, ctx):
        sheet, _ = ls.character_sheet(ctx.book)
        units = []
        for chapter in self._chapters(ctx):
            body = {'text': chapter['text']}
            # Without a cast the analyzer writes its own sheet; its names are then matched to the cast.
            if sheet['characters']:
                body['character_sheet'] = sheet
            units.append(Unit(key=chapter['id'], scope=chapter['id'], label=chapter.get('title') or chapter['id'],
                              service=ServiceRequest(body), chapter_id=chapter['id']))
        return units

    def execute(self, ctx, unit):
        # BookNLP as the provider: its attributions come from the accepted Quote attribution version.
        dialogue = [s['id'] for s in ctx.book['segments'] if s['chapter_id'] == unit.scope and s['kind'] == 'dialogue']
        if not dialogue:
            return {'passages': {}}
        payload = ctx.inputs.get('quotes', {}).get(unit.scope)
        if not payload:
            raise ValueError('Run and accept Quote attribution (BookNLP) for this section before using BookNLP here.')
        _require_coverage('BookNLP', dialogue, payload['passages'])
        return {'passages': deepcopy(payload['passages'])}

    def _annotate(self, work, unit, result, boundaries):
        scene = next(s for s in work['scenes'] if s['id'] == unit.data['scene_id'])
        segments = {s['id']: s for s in work['segments']}
        a._apply_annotations(work, scene, [segments[i] for i in unit.data['segment_ids']], result, boundaries)

    def validate(self, ctx, unit, result):
        if unit.service is not None:
            self._analyzer_lines(ctx, unit.scope, result)
            return result
        if unit.request is None:
            return result
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
            # The payload is what the provider proposed; manual edits are applied later as locks.
            work = _chapter_book(ctx.book, chapter_id)
            for item in work['segments'] + work['scenes']:
                item.pop('edited', None)
                item.pop('edited_fields', None)
                item.pop('speaker_check', None)
            for scene in work['scenes']:
                scene['summary'] = ''
            boundaries = {}
            if ctx.provider == 'novel_analyzer':
                self._apply_analyzer(ctx, work, chapter_id, items[0][1], boundaries)
            elif ctx.provider == 'booknlp':
                # Speakers only: BookNLP says nothing about scenes, so their notes stay as they are.
                work['scenes'] = deepcopy([s for s in ctx.book['scenes'] if s['chapter_id'] == chapter_id])
                self._apply_booknlp(work, items[0][1])
            else:
                for unit, result in items:
                    self._annotate(work, unit, deepcopy(result), boundaries)
                    for segment_id in unit.data['segment_ids']:
                        segment = next(s for s in work['segments'] if s['id'] == segment_id)
                        segment.update(analysis_provider=ctx.provider, analysis_model=ctx.model)
            _split_chapter(work, chapter_id, boundaries)
            if ctx.provider != 'booknlp':
                # Checking BookNLP against itself would count one opinion twice.
                self._check_speakers(ctx, work, chapter_id)
            payloads[chapter_id] = self.capture(work, chapter_id)
        return payloads

    # --- self-hosted providers ------------------------------------------------------------------------
    def _analyzer_lines(self, ctx, chapter_id, result):
        """Validate an analyzer response against the exact chapter text; return (lines, passages) matches."""
        if not isinstance(result, dict) or not isinstance(result.get('lines'), list) or not isinstance(result.get('scenes'), list):
            raise ValueError('The Novel Analyzer returned an unexpected response.')
        for line in result['lines']:
            if not isinstance(line, dict) or not isinstance(line.get('paragraph'), int) or \
                    not (line.get('speaker') is None or isinstance(line.get('speaker'), str)):
                raise ValueError('The Novel Analyzer returned an invalid line.')
        chapter = next(c for c in ctx.book['chapters'] if c['id'] == chapter_id)
        segments = [s for s in ctx.book['segments'] if s['chapter_id'] == chapter_id]
        matched, _ = ls.match_quotes(chapter['text'], segments, result['lines'])
        dialogue = [s['id'] for s in segments if s['kind'] == 'dialogue']
        _require_coverage('The Novel Analyzer', dialogue, {i for _, ids in matched for i in ids})
        paragraphs = len(ls.paragraphs(chapter['text']))
        if len(dialogue) >= 8 and paragraphs < MIN_PARAGRAPHS_PER_DIALOGUE * len(dialogue):
            raise ValueError(f'This section has {len(dialogue)} dialogue passages in {paragraphs} paragraphs. The Novel '
                             'Analyzer gives one speaker per blank-line paragraph, so its speakers were not used.')
        return matched

    @staticmethod
    def _reset(work, provider):
        for segment in work['segments']:
            dialogue = segment['kind'] == 'dialogue'
            segment.update(speaker_id='unassigned' if dialogue else 'narrator', confidence=0.0 if dialogue else 1.0,
                           direction='', cues=[], evidence=[], analysis_provider=provider, analysis_model=None)

    def _apply_analyzer(self, ctx, work, chapter_id, result, boundaries):
        chapter = next(c for c in ctx.book['chapters'] if c['id'] == chapter_id)
        text = chapter['text']
        bounds = ls.paragraphs(text)
        _, names = ls.character_sheet(ctx.book)
        index = ls.name_index(ctx.book)
        segments = {s['id']: s for s in work['segments']}
        narrator = ls.first_person(ctx.book)
        self._reset(work, 'novel_analyzer')
        # It proposes no scene tone or direction; notes left from another provider would be misattributed.
        for scene in work['scenes']:
            scene.update(tone='', direction='')
        labelled, ambiguous = {}, set()
        for line, segment_ids in self._analyzer_lines(ctx, chapter_id, result):
            speaker = line.get('speaker')
            character_id = None
            if isinstance(speaker, str):
                character_id = names.get(speaker) or ls.resolve(index, speaker) or \
                    (narrator if ls.FIRST_PERSON_ALIASES.intersection({' '.join(speaker.casefold().split())}) else None)
            untagged = line.get('untagged') is True
            evidence = []
            paragraph = ls.paragraph_of(bounds, line['start'])
            if not untagged and paragraph is not None and isinstance(line.get('narration'), str):
                excerpt = ls.locate(text[bounds[paragraph][0]:bounds[paragraph][1]], line['narration'])
                evidence = [excerpt] if excerpt else []
            direction = line.get('instruction') if isinstance(line.get('instruction'), str) else line.get('delivery')
            raw_cues = line.get('cues') if isinstance(line.get('cues'), list) else []
            cues = [c.strip()[:120] for c in raw_cues if isinstance(c, str) and c.strip()][:12]
            for segment_id in segment_ids:
                if segment_id in ambiguous:
                    continue
                if segment_id in labelled and labelled[segment_id] != character_id:
                    # Two lines in one passage with different speakers: keep the ambiguity visible.
                    ambiguous.add(segment_id)
                    segments[segment_id].update(speaker_id='unassigned', confidence=0.0, evidence=[])
                    continue
                labelled[segment_id] = character_id
                segments[segment_id].update(speaker_id=character_id or 'unassigned',
                                            confidence=(UNTAGGED if untagged else TAGGED) if character_id else 0.0,
                                            direction=str(direction or '')[:1500], cues=cues, evidence=evidence)
        # Scene breaks count paragraphs; use them only if every line's paragraph matches ours.
        lines = result['lines']
        if not lines or any(ls.paragraph_of(bounds, line['start']) != line['paragraph'] - 1 for line in lines):
            return
        starts = {}
        for number, scene in enumerate(result['scenes'], 1):
            first = scene.get('first_paragraph') if isinstance(scene, dict) else None
            if type(first) is not int or not 1 <= first <= len(bounds):
                continue
            segment = next((s for s in work['segments'] if s['start'] == bounds[first - 1][0]), None)
            setting = scene.get('setting') if isinstance(scene.get('setting'), str) else ''
            if segment is not None:
                starts[segment['id']] = {'title': f"{chapter.get('title') or 'Section'} · Scene {number}",
                                         'summary': f'Setting (unverified): {setting[:300]}.' if setting and setting != 'unknown' else '',
                                         'tone': '', 'direction': ''}
        for scene in work['scenes']:
            first = scene['segment_ids'][0] if scene['segment_ids'] else None
            if first in starts:
                scene['summary'] = starts.pop(first)['summary']
        boundaries.update(starts)

    def _apply_booknlp(self, work, result):
        segments = {s['id']: s for s in work['segments']}
        self._reset(work, 'booknlp')
        for segment_id, entry in result['passages'].items():
            segment = segments.get(segment_id)
            if segment is None or segment['kind'] != 'dialogue':
                continue
            speaker = entry.get('speaker_id') or ('narrator' if entry.get('narrator') else None)
            tag = entry.get('tag') or {}
            if entry.get('tag_conflict') or not speaker:
                segment.update(confidence=CONFLICTED if entry.get('tag_conflict') else 0.0)
                continue
            segment.update(speaker_id=speaker, confidence=TAGGED if tag.get('kind') == 'speech' else BEAT if tag.get('kind') == 'beat' else UNTAGGED,
                           direction=_manner(tag), evidence=[tag['evidence']] if tag.get('evidence') else [])

    def _check_speakers(self, ctx, work, chapter_id):
        """Compare the proposal with accepted BookNLP attributions and adjust confidence.

        Agreement between two independent methods lifts confidence to at least
        AGREE_FLOOR (AGREE_FLOOR_UNTAGGED when BookNLP saw no tag or beat). A
        disagreement keeps the proposed speaker but caps its confidence at
        DISAGREE_CAP and records BookNLP's speaker for review; BookNLP is not
        trusted over the model. When BookNLP's own tag contradicts its speaker
        (tag_conflict), the comparison is only recorded. An unassigned line stays
        unassigned: the suggestion is only recorded.
        """
        payload = ctx.inputs.get('quotes', {}).get(chapter_id)
        if not payload:
            return
        passages = payload.get('passages', {})
        for segment in work['segments']:
            if segment['kind'] != 'dialogue':
                continue
            entry = passages.get(segment['id'])
            if entry is None:
                segment['speaker_check'] = {'source': 'booknlp', 'result': 'no_quote'}
                continue
            result = compare(entry, segment['speaker_id'])
            if entry.get('tag_conflict'):
                pass
            elif result == 'agrees':
                floor = AGREE_FLOOR if (entry.get('tag') or {}).get('kind') in ('speech', 'beat') else AGREE_FLOOR_UNTAGGED
                segment['confidence'] = max(segment['confidence'], floor)
            elif result == 'differs':
                segment['confidence'] = min(segment['confidence'], DISAGREE_CAP)
            segment['speaker_check'] = {'source': 'booknlp', 'result': result, 'speaker_id': entry.get('speaker_id'),
                                        'speaker': entry.get('speaker'), 'tag_conflict': bool(entry.get('tag_conflict'))}

    def capture(self, book, scope):
        scenes = [s for s in book['scenes'] if s['chapter_id'] == scope]
        segments = [s for s in book['segments'] if s['chapter_id'] == scope]
        # Only a chapter whose scenes partition exactly its passages can be restored.
        if not segments or sorted(i for s in scenes for i in s['segment_ids']) != sorted(s['id'] for s in segments):
            return None
        return {'scenes': [{'id': s['id'], **{f: s.get(f) for f in SCENE_FIELDS}, 'segment_ids': list(s['segment_ids'])} for s in scenes],
                'segments': {s['id']: {f: deepcopy(s.get(f)) for f in SEGMENT_FIELDS if f not in OPTIONAL_FIELDS or f in s}
                             for s in segments}}

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
                raise Invalid('version_incompatible', 'A scene map version does not cover exactly this chapter\'s passages.')
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
                    # The proposed speaker's confidence, evidence and check no longer describe this passage.
                    segment.update(confidence=0.0, evidence=[])
                    segment.pop('speaker_check', None)
            # Derived membership is refreshed only when scene breaks or speakers changed.
            if before != ([s['segment_ids'] for s in replacement], [(i, s.get('speaker_id')) for i, s in segments.items()]):
                for scene in replacement:
                    scene['character_ids'] = list(dict.fromkeys(segments[i]['speaker_id'] for i in scene['segment_ids'] if i in segments))
        return conflicts

    def summarize(self, book, payloads):
        names = {c['id']: c['name'] for c in book['characters']}
        texts = {s['id']: (s['text'], s['kind']) for s in book['segments']}
        current = {s['id']: s for s in book['segments']}
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
                             'cues': ', '.join(values.get('cues') or []),
                             'check': CHECK_LABELS.get((values.get('speaker_check') or {}).get('result'), '')
                             + (f" · BookNLP: {names.get(values['speaker_check'].get('speaker_id')) or values['speaker_check'].get('speaker') or '?'}"
                                if (values.get('speaker_check') or {}).get('result') in ('differs', 'suggests') else ''),
                             # Fields a person edited keep their value whichever version is accepted.
                             'edited': ', '.join(name for name in ('speaker_id', 'direction', 'cues')
                                                 if locked(current.get(segment_id, {}), name)).replace('speaker_id', 'speaker')})
        rows.sort(key=lambda row: order.get(row['id'], len(order)))
        dialogue = [r for r in rows if r['kind'] == 'dialogue']
        unassigned = sum(payloads[r['scope']]['segments'][r['id']].get('speaker_id') == 'unassigned' for r in dialogue)
        checks = [(payloads[r['scope']]['segments'][r['id']].get('speaker_check') or {}).get('result') for r in dialogue]
        # The comparison that matters between providers: the row diff also counts every wording change in delivery.
        same = sum(payloads[r['scope']]['segments'][r['id']].get('speaker_id') == current.get(r['id'], {}).get('speaker_id')
                   for r in dialogue)
        stats = {'sections': len(payloads), 'scenes': scenes, 'passages': len(rows), 'dialogue': len(dialogue),
                 'unassigned_dialogue': unassigned, 'attributed_dialogue': len(dialogue) - unassigned,
                 'same_speaker_as_book': same}
        if any(checks):
            stats.update(booknlp_agrees=checks.count('agrees'), booknlp_differs=checks.count('differs'),
                         booknlp_suggests=checks.count('suggests'))
        columns = [{'key': 'scene', 'label': 'Scene'}, {'key': 'text', 'label': 'Passage'},
                   {'key': 'speaker', 'label': 'Speaker'}, {'key': 'confidence', 'label': 'Confidence'},
                   {'key': 'direction', 'label': 'Delivery'}, {'key': 'cues', 'label': 'Cues'}]
        if any(checks):
            columns.append({'key': 'check', 'label': 'BookNLP check'})
        return {'stats': stats, 'columns': [*columns, {'key': 'edited', 'label': 'Your edit (kept)'}], 'rows': rows}
