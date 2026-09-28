"""Free whole-book census; heuristic priority is not literary truth."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import re

from . import analysis as a
from .processing import ProcessingStore, digest, source_hash, token_estimate

VERSION = 1


def eligible_chapters(book):
    return [c for c in book['chapters'] if c.get('kind') not in {'front_matter', 'back_matter'}]


def census(book, store):
    repository = ProcessingStore(store)
    identity = digest([VERSION, source_hash(book), [(c['id'], c.get('kind'), c['title']) for c in book['chapters']],
                       [(c['id'], c['name'], c.get('aliases', [])) for c in book['characters']],
                       [(s['id'], s['chapter_id'], s['kind'], s.get('speaker_id'), s.get('confidence')) for s in book['segments']]])
    cached = repository.preprocessing(book['id'], identity)
    if cached:
        return cached
    from .resources import ResourceLedger, operation_active
    if operation_active():
        return _compute_census(book, repository, identity)
    with ResourceLedger(store).operation(book['id'], 'census', unit_key=identity, measure_cpu=True):
        return _compute_census(book, repository, identity)


def _compute_census(book, repository, identity):
    eligible = eligible_chapters(book)
    eligible_ids = {c['id'] for c in eligible}
    candidates, names = {}, {}
    for character in book['characters']:
        if character['id'] in {'narrator', 'unassigned'}:
            continue
        candidates[character['id']] = {'id': character['id'], 'name': character['name'], 'aliases': character.get('aliases', []), 'known_character': True}
        for name in [character['name'], *character.get('aliases', [])]:
            names.setdefault(a._name_key(name), set()).add(character['id'])
    explicit = Counter()
    for chapter in eligible:
        for match in a.NAME_TAG.finditer(chapter['text']):
            name = match.group(1) or match.group(4)
            normalized = a._name_key(name)
            if normalized in a.NOT_NAMES:
                continue
            existing = names.get(normalized, set())
            cid = next(iter(existing)) if len(existing) == 1 else 'candidate_' + digest(normalized)[:12]
            if cid not in candidates:
                candidates[cid] = {'id': cid, 'name': name, 'aliases': [], 'known_character': False}
                names.setdefault(normalized, set()).add(cid)
            explicit[cid] += 1
    stats = []
    dialogue = defaultdict(list)
    for segment in book['segments']:
        if segment['chapter_id'] in eligible_ids and segment['kind'] == 'dialogue':
            dialogue[segment['speaker_id']].append(segment)
    for candidate in candidates.values():
        cid = candidate['id']
        patterns = [n for n in [candidate['name'], *candidate['aliases']] if names.get(a._name_key(n)) == {cid}]
        mentions, chapter_counts = 0, {}
        if patterns:
            matcher = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(n) for n in sorted(set(patterns), key=len, reverse=True)) + r')(?!\w)')
            for chapter in eligible:
                count = sum(1 for _ in matcher.finditer(chapter['text']))
                if count:
                    chapter_counts[chapter['id']] = count
                    mentions += count
        turns = dialogue[cid]
        low_confidence = sum(type(s.get('confidence')) not in {int, float} or not math.isfinite(s['confidence'])
                             or s['confidence'] < .8 for s in turns)
        ambiguous = sum(len(names[a._name_key(n)]) > 1 for n in [candidate['name'], *candidate['aliases']])
        score = mentions + explicit[cid] * 2 + len(turns) * 3 + len(chapter_counts) * 3
        stats.append({**candidate, 'mentions': mentions, 'explicit_speech_tags': explicit[cid], 'dialogue_turns': len(turns),
                      'dialogue_words': sum(len(s['text'].split()) for s in turns), 'chapter_count': len(chapter_counts),
                      'chapter_mentions': chapter_counts, 'uncertain_attributions': low_confidence, 'ambiguous_aliases': ambiguous,
                      'priority_score': score})
    stats.sort(key=lambda item: (-item['priority_score'], item['name'].casefold()))
    maximum = max((item['priority_score'] for item in stats), default=0)
    for item in stats:
        item['priority'] = ('deep' if item['ambiguous_aliases'] or item['uncertain_attributions'] or item['priority_score'] >= max(20, maximum * .35)
                            else 'standard' if item['priority_score'] >= 8 else 'basic')
        item['recommended_evidence_limit'] = {'deep': 16, 'standard': 10, 'basic': 5}[item['priority']]
    chapters = []
    for c in book['chapters']:
        segments = [s for s in book['segments'] if s['chapter_id'] == c['id']]
        speech = [s for s in segments if s['kind'] == 'dialogue']
        chapters.append({'id': c['id'], 'title': c['title'], 'kind': c.get('kind', 'section'), 'eligible': c['id'] in eligible_ids,
                         'words': len(c['text'].split()), 'estimated_tokens': token_estimate(c['text']),
                         'passages': len(segments), 'dialogue_turns': len(speech),
                         'unassigned_dialogue': sum(s['speaker_id'] == 'unassigned' for s in speech)})
    result = {'version': VERSION, 'fingerprint': identity, 'source_hash': source_hash(book), 'local_complete': True,
              'local_chapters_scanned': len(book['chapters']), 'eligible_chapter_ids': [c['id'] for c in eligible],
              'eligible_chapters': len(eligible), 'chapters': chapters, 'characters': stats,
              'words': sum(c['words'] for c in chapters), 'estimated_source_tokens': sum(c['estimated_tokens'] for c in chapters if c['eligible']),
              'note': 'Free local census. Name mentions, speech tags and spread guide effort; they do not prove identity, presence, or narrative importance. Unknown pronouns and rare speakers still require review.'}
    repository.save_preprocessing(book['id'], identity, result)
    return result


def coverage(book, store):
    repository = ProcessingStore(store)
    local = census(book, store)
    units = repository.units(book['id'], 'discovery', source_hash(book))
    checkpoint_status = store.analysis_status(book['id']) or {}
    checkpoint = store.analysis_checkpoint(book['id'], checkpoint_status.get('fingerprint')) or {}
    # Include pre-upgrade validated chapter discovery without claiming unscanned text.
    rows = ({r['id']: r for r in checkpoint.get('chapters', [])}
            if 'phase' not in checkpoint and checkpoint.get('provider') in a.PROVIDER_LABELS else {})
    baseline = {c['id']: c['text'] for c in checkpoint.get('working_book', {}).get('chapters', [])}
    chapter_map = {c['id']: c for c in book['chapters']}
    intervals = defaultdict(list)
    for unit in units:
        chapter = chapter_map.get(unit.get('chapter_id'))
        start, end = unit.get('start'), unit.get('end')
        if (chapter is not None and unit.get('provider') in a.PROVIDER_LABELS
                and type(start) is int and type(end) is int and 0 <= start < end <= len(chapter['text'])):
            intervals[chapter['id']].append((start, end))
    complete = []
    for chapter in eligible_chapters(book):
        parts = sorted(intervals[chapter['id']])
        merged = []
        for start, end in parts:
            if merged and not chapter['text'][merged[-1][1]:start].strip():
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        is_complete = bool(merged and len(merged) == 1 and not chapter['text'][:merged[0][0]].strip() and not chapter['text'][merged[0][1]:].strip())
        legacy_complete = (rows.get(chapter['id'], {}).get('discovery_complete') is True
                           and baseline.get(chapter['id']) == chapter['text'])
        if is_complete or legacy_complete:
            complete.append(chapter['id'])
    all_discovered = bool(local['eligible_chapters']) and len(complete) == local['eligible_chapters']
    return {'local': local, 'semantic_chapters_complete': len(complete), 'semantic_chapter_ids': complete,
            'eligible_chapters': local['eligible_chapters'], 'whole_book_discovered': all_discovered,
            'profiles_provisional': not all_discovered,
            'usage': repository.usage(book['id']),
            'note': 'Full discovery coverage is required for comprehensive profiles; it does not guarantee that all identities or traits are correct.'}
