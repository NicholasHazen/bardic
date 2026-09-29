"""Quote attribution by a self-hosted BookNLP service: a free second opinion on speakers (service).

The service returns every quotation with exact offsets, a speaker, the tag or
beat beside it and a conflict flag. This step writes nothing to the book. Its
accepted payload is an input to Speakers & delivery, which uses it to check
(and, if chosen as the provider, to supply) speaker attribution. The result
table compares it with the book's current speakers.
"""
from __future__ import annotations

from ... import local_services as ls
from ..contract import ServiceRequest, Step, Unit

# The results of a comparison with BookNLP's attribution. Result tables send the identifier, never a label.
CHECK_RESULTS = ('agrees', 'differs', 'suggests', 'not_in_cast', 'narrator', 'no_quote')


def compare(entry, speaker_id):
    """How a BookNLP attribution relates to a proposed speaker.

    ``narrator`` means BookNLP heard the first-person narrator but no cast
    character is marked as saying "I": not comparable, so never a disagreement.
    """
    theirs = entry.get('speaker_id')
    # The book's narration voice speaking the first-person narrator's own line is the same person.
    if entry.get('narrator') and speaker_id == 'narrator':
        return 'agrees'
    if theirs is None:
        return 'narrator' if entry.get('narrator') else 'not_in_cast'
    if speaker_id in (None, 'unassigned'):
        return 'suggests'
    return 'agrees' if speaker_id == theirs else 'differs'


def _tag(tag, paragraph_text):
    if not isinstance(tag, dict) or tag.get('kind') not in ('speech', 'beat'):
        return None
    words = lambda values: [str(v)[:80] for v in values if isinstance(v, str)][:6] if isinstance(values, list) else []
    return {'kind': tag['kind'], 'verb': str(tag.get('verb') or '')[:60], 'lemma': str(tag.get('lemma') or '')[:60],
            'adverbs': words(tag.get('adverbs')), 'with': words(tag.get('with')),
            'text': str(tag.get('text') or '')[:300],
            # An exact excerpt of the paragraph, or None: the service's tag text is tokenized, not source.
            'evidence': ls.locate(paragraph_text, tag.get('text'))}


def chapter_payload(book, chapter_id, result):
    """Map one BookNLP response onto the chapter's passages and the current cast."""
    chapter = next(c for c in book['chapters'] if c['id'] == chapter_id)
    text = chapter['text']
    segments = [s for s in book['segments'] if s['chapter_id'] == chapter_id]
    matched, unmatched = ls.match_quotes(text, segments, result['quotes'])
    index = ls.name_index(book)
    narrator = ls.first_person(book)
    bounds = ls.paragraphs(text)
    characters, mapped = [], {}
    for item in result['characters']:
        character_id = ls.resolve(index, item.get('name'), *(item.get('names') or []))
        if character_id is None and item.get('narrator') and narrator:
            character_id = narrator
        mapped[item['id']] = character_id
        gender = item.get('gender') if isinstance(item.get('gender'), dict) else {}
        counts = gender.get('pronoun_counts') if isinstance(gender.get('pronoun_counts'), dict) else {}
        characters.append({'name': str(item.get('name') or '')[:200], 'narrator': item.get('narrator') is True,
                           'character_id': character_id, 'mentions': item.get('mentions'), 'quotes': item.get('quotes'),
                           'gender': gender.get('from_pronouns') if gender.get('from_pronouns') in ('male', 'female') else 'unknown',
                           'pronouns': {k: counts.get(k) for k in ('he', 'she') if type(counts.get(k)) is int}})
    narrators = {i['id'] for i in result['characters'] if i.get('narrator') is True}
    passages = {}
    for quote, segment_ids in matched:
        paragraph = ls.paragraph_of(bounds, quote['start'])
        paragraph_text = text[bounds[paragraph][0]:bounds[paragraph][1]] if paragraph is not None else ''
        entry = {'speaker': quote.get('speaker') if isinstance(quote.get('speaker'), str) else None,
                 'speaker_id': mapped.get(quote.get('speaker_id')),
                 'narrator': quote.get('speaker_id') in narrators,
                 'tag': _tag(quote.get('tag'), paragraph_text),
                 'tag_conflict': quote.get('tag_conflict') is True}
        for segment_id in segment_ids:
            previous = passages.get(segment_id)
            if previous and previous['speaker_id'] != entry['speaker_id']:
                # Two quotations in one passage with different speakers: keep the ambiguity visible.
                passages[segment_id] = {**previous, 'speaker_id': None, 'speaker': None, 'narrator': False, 'tag_conflict': True}
            elif not previous:
                passages[segment_id] = dict(entry)
    places = [{'text': str(p.get('text') or '')[:200], 'cat': p.get('cat'), 'count': p.get('count')}
              for p in result.get('places', []) if isinstance(p, dict)][:100]
    return {'passages': passages, 'characters': characters, 'places': places, 'quotes': len(result['quotes']),
            'unmatched_quotes': [{'start': q['start'], 'end': q['end']} for q in unmatched][:100]}


def validate_response(result):
    if not isinstance(result, dict) or not isinstance(result.get('quotes'), list) or not isinstance(result.get('characters'), list):
        raise ValueError('BookNLP returned an unexpected response.')
    ids = set()
    for item in result['characters']:
        if not isinstance(item, dict) or type(item.get('id')) is not int or not isinstance(item.get('name'), str):
            raise ValueError('BookNLP returned an invalid character.')
        ids.add(item['id'])
    for quote in result['quotes']:
        if not isinstance(quote, dict) or type(quote.get('speaker_id')) is not int:
            raise ValueError('BookNLP returned an invalid quotation.')


class QuotesStep(Step):
    id = 'quotes'
    label = 'Quote attribution (BookNLP)'
    summary = ('Sends each chapter to your BookNLP server, which finds every quotation, who says it and the dialogue tag '
               'beside it, in seconds and for free. It changes nothing in the book: Speakers & delivery uses it to check '
               'speakers (or as a speaker source), and this table lists where it differs from the current speakers.')
    method = 'service'
    providers = ('booknlp',)
    scope = 'chapter'
    chapter_scoped = True
    # Cast names become BookNLP aliases, so accepted discovery changes the request. It maps onto
    # the cast in the book, so discovery is recorded for staleness but not required.
    inputs = ('discovery',)
    requires = ()
    owns = ()

    def units(self, ctx):
        aliases = ls.booknlp_aliases(ctx.book)
        with_passages = {s['chapter_id'] for s in ctx.book['segments'] if s['kind'] == 'dialogue'}
        return [Unit(key=chapter['id'], scope=chapter['id'], label=chapter.get('title') or chapter['id'],
                     service=ServiceRequest({'text': chapter['text'], 'aliases': aliases}), chapter_id=chapter['id'])
                for chapter in ctx.selected_chapters() if chapter['id'] in with_passages]

    def validate(self, ctx, unit, result):
        validate_response(result)
        chapter_payload(ctx.book, unit.scope, result)
        return result

    def assemble(self, ctx, done):
        return {unit.scope: chapter_payload(ctx.book, unit.scope, result) for unit, result in done}

    def summarize(self, book, payloads):
        names = {c['id']: c['name'] for c in book['characters']}
        segments = {s['id']: s for s in book['segments']}
        order = {s['id']: i for i, s in enumerate(book['segments'])}
        rows, counts = [], {key: 0 for key in CHECK_RESULTS}
        conflicts = unmatched = uncovered = 0
        for scope, payload in payloads.items():
            unmatched += len(payload.get('unmatched_quotes', []))
            # Dialogue BookNLP found no quotation for (for example single-quoted or dash dialogue).
            uncovered += sum(s['chapter_id'] == scope and s['kind'] == 'dialogue' and s['id'] not in payload.get('passages', {})
                             for s in book['segments'])
            for segment_id, entry in payload.get('passages', {}).items():
                segment = segments.get(segment_id)
                if segment is None:
                    continue
                result = compare(entry, segment.get('speaker_id'))
                counts[result] += 1
                conflicts += bool(entry.get('tag_conflict'))
                text = segment['text']
                rows.append({'id': segment_id, 'scope': scope, 'kind': 'quotation',
                             'text': text if len(text) <= 160 else text[:157] + '…',
                             'booknlp': names.get(entry.get('speaker_id')) or entry.get('speaker') or '',
                             'current_speaker': names.get(segment.get('speaker_id'), segment.get('speaker_id') or ''),
                             'check': result, 'tag': (entry.get('tag') or {}).get('text', ''),
                             'conflict': bool(entry.get('tag_conflict'))})
            for index, character in enumerate(payload.get('characters', [])):
                pronouns = character.get('pronouns') or {}
                rows.append({'id': f'{scope}:character:{index}', 'scope': scope, 'kind': 'character',
                             'text': f"{character['name']} · {character['gender']} from pronouns "
                                     f"(he {pronouns.get('he', 0)}, she {pronouns.get('she', 0)})",
                             'booknlp': character['name'], 'current_speaker': names.get(character.get('character_id'), ''),
                             'check': 'in_cast' if character.get('character_id') else 'not_in_cast', 'tag': '', 'conflict': False})
        rows.sort(key=lambda row: (row['kind'] != 'quotation', order.get(row['id'], len(order)), row['id']))
        compared = counts['agrees'] + counts['differs']
        return {'stats': {'sections': len(payloads), 'quotations': sum(p.get('quotes', 0) for p in payloads.values()),
                          'agrees': counts['agrees'], 'differs': counts['differs'], 'suggests_speaker': counts['suggests'],
                          'not_in_cast': counts['not_in_cast'], 'tag_conflicts': conflicts, 'unmatched_quotations': unmatched,
                          'dialogue_without_quotation': uncovered,
                          'agreement': round(counts['agrees'] / compared, 3) if compared else None},
                'columns': [{'key': 'kind', 'label': 'Kind'}, {'key': 'text', 'label': 'Passage'},
                            {'key': 'booknlp', 'label': 'BookNLP'}, {'key': 'current_speaker', 'label': 'Current speaker'},
                            {'key': 'check', 'label': 'Check'}, {'key': 'tag', 'label': 'Tag or beat'},
                            {'key': 'conflict', 'label': 'Tag conflict'}],
                'rows': rows}
