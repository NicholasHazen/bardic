"""Project accepted step evidence onto the book's current character references.

``character_references`` is a current-book projection, like the characters and
passages in ``books.body``. It is rebuilt from what is accepted now:

* ``discovery`` (chapter scope): each candidate's exact quotations, resolved to
  a current character, become ``profile_evidence`` rows. A quotation is located
  in the candidate's own discovery range; one that is not an exact slice there
  is dropped.
* ``profiles`` (character scope): each profile quotation is located inside the
  discovery evidence of the same character that the profile run read (its
  recorded discovery inputs). Every distinct matching location becomes a row
  (``anchors`` > 1 keeps that ambiguity visible). A quotation from an earlier
  volume's context has no location in this book and is dropped.
* ``directing`` (chapter scope): the book's attributed dialogue. Manual speaker
  choices win over the accepted version (``provider: 'reviewed'``), exactly as
  directing's projection honours them.
* ``mention``: exact, case-sensitive whole-word matches of each recorded spelling
  of an unambiguous cast name or alias.
  A mention is not proof that the character is present.

Every row must pass :func:`bardic.series.reference_is_valid` (chapter exists,
zero-based code-point offsets in bounds, the slice equals the quote, a real
character). Invalid evidence is counted and dropped, never repaired. Row IDs
hash their content, so a rebuild is deterministic and idempotent.

The rebuild runs from :func:`bardic.pipeline.projection.sync`, which every
pipeline decision (accept, rollback, set aside, auto, baseline and external
capture) and read path (overview, plan, run start, preview) calls inside its
transaction. It is skipped when a digest of its inputs and of the stored rows
is unchanged. Any other writer of ``character_references`` (structure repair of
a legacy Classic checkpoint) changes the stored rows, so the next sync rebuilds them.

A book without any accepted discovery, profiles or directing version is left
untouched. Rows the removed Classic engine wrote as ``profile_evidence`` (no
``projection`` field) are carried, revalidated, until a discovery version is
accepted: discovery cannot be captured as a baseline, so they are its only
record in the current projection. They stay in ``character_observations``.

These rows are also the series memory: a later volume's profiles read an
earlier volume's current rows through confirmed links
(:meth:`bardic.series.SeriesRepository.context_for_book`). The state entry
records the chapter source hashes the rows were validated against, so a later
volume never reads a row whose source has changed since.

The projection writes nothing to ``character_observations`` (decided with the
series-memory follow-up, 2026-09-28). Nothing reads that table for prompts any
more; the history of accepted evidence is its step_output versions, and each
earlier-volume entry a profile request sends is retained as a
``character_observation`` artifact. :func:`retain_history` still does the append
(tested) and stays disabled by :data:`RETAIN_OBSERVATIONS`; enabling it would
only duplicate that history. Legacy rows stay until the owner-gated data drop.
"""
from __future__ import annotations

import bisect
import json
import re
from collections import Counter
from copy import deepcopy

from .. import analysis as a
from ..processing import digest
from ..series import reference_is_valid, retain_observations, source_hash
from ..store import now
from .contract import RESERVED_CHARACTERS, locked
from .repository import KIND

# Bump when the row mapping changes; stored projections are then rebuilt.
PROJECTION_VERSION = 1
STEPS = ('discovery', 'profiles', 'directing')
STATE_KEY = 'evidence'
# Off: series context reads the projection itself, and artifacts keep the history.
# Enabling it would only duplicate history (SERIES-MEMORY-PLAN.md section 2). See the module notes.
RETAIN_OBSERVATIONS = False


def _producers(conn, identifiers):
    """{artifact_id: (provider, model)} as recorded on each step_output version."""
    identifiers = list(dict.fromkeys(identifiers))
    result = {}
    for start in range(0, len(identifiers), 500):
        chunk = identifiers[start:start + 500]
        marks = ','.join('?' * len(chunk))
        for identifier, provider, model in conn.execute(
                f'SELECT id,provider,model FROM artifact_versions WHERE kind=? AND id IN ({marks})', [KIND, *chunk]):
            result[identifier] = (provider, model)
    return result


class _Segments:
    """First passage overlapping a source span, per chapter, in O(log n)."""

    def __init__(self, book):
        self.by_chapter = {}
        for segment in book['segments']:
            self.by_chapter.setdefault(segment['chapter_id'], []).append(segment)
        for items in self.by_chapter.values():
            items.sort(key=lambda s: (s['start'], s['end']))
        self.ends = {cid: [s['end'] for s in items] for cid, items in self.by_chapter.items()}

    def at(self, chapter_id, start, end):
        items = self.by_chapter.get(chapter_id, [])
        index = bisect.bisect_right(self.ends.get(chapter_id, []), start)
        if index < len(items) and items[index]['start'] < end:
            return items[index]['id']
        return None


def _discovery_spans(payload, chapter, cast, counts):
    """(character_id, start, end, candidate) for each exactly located candidate quotation."""
    text = chapter['text']
    try:
        ranges = {r['start']: r['end'] for r in payload['ranges']}
        candidates = list(payload['candidates'])
    except (KeyError, TypeError):
        counts['malformed'] += 1
        return []
    spans = []
    for candidate in candidates:
        evidence = candidate.get('evidence') if isinstance(candidate, dict) else None
        if not isinstance(evidence, list):
            counts['malformed'] += 1
            continue
        low = candidate.get('range_start')
        high = ranges.get(low)
        if type(low) is not int or type(high) is not int or not 0 <= low < high <= len(text):
            counts['invalid'] += len(evidence)
            continue
        try:
            character = a._resolve_character_candidate(cast, candidate)
        except (KeyError, TypeError, AttributeError):
            character = None
        for quote in evidence:
            # Validated quotations are exact source slices; the first occurrence in the
            # range is where validation anchored them. Anything else is not repaired.
            offset = text.find(quote, low, high) if isinstance(quote, str) and quote else -1
            if offset < 0 or offset + len(quote) > high:
                counts['invalid'] += 1
            elif character is None:
                # A name no current character uniquely owns: left unresolved, never guessed.
                counts['unresolved'] += 1
            else:
                spans.append((character['id'], offset, offset + len(quote), candidate))
    return spans


def _mentions(book, cast, chapters):
    """Whole-word matches of names and aliases that identify exactly one character."""
    names = {}
    for character in cast:
        for name in [character['name'], *(character.get('aliases') or [])]:
            if isinstance(name, str) and name.strip():
                names.setdefault(a._name_key(name), {}).setdefault(character['id'], set()).add(name)
    found = set()
    for key, owners in names.items():
        if len(owners) != 1 or not key:
            continue
        cid, spellings = next(iter(owners.items()))
        # Each spelling the cast records is matched exactly (case-sensitive), never a variant of it.
        for actual in sorted(spellings):
            pattern = re.compile(r'(?<!\w)' + re.escape(actual) + r'(?!\w)')
            for chapter in chapters.values():
                for match in pattern.finditer(chapter['text']):
                    found.add((cid, chapter['id'], match.start(), match.end()))
    # A longer alias and its suffix at the same location are one mention.
    kept, occupied = [], {}
    for cid, chapter_id, start, end in sorted(found, key=lambda m: (m[0], m[1], m[2], -m[3])):
        if start < occupied.get((cid, chapter_id), -1):
            continue
        occupied[(cid, chapter_id)] = end
        kept.append((cid, chapter_id, start, end))
    return kept


def build(repository, conn, book, previous=()):
    """(rows, counts) for ``book`` from the currently accepted versions.

    ``previous`` are the stored rows; only legacy ``profile_evidence`` rows are
    read from it (see the module notes). Returns ``(None, counts)`` when the
    book has no accepted evidence step, meaning "leave the references alone".
    """
    book_id = book['id']
    heads = {step: repository.heads(conn, book_id, step) for step in STEPS}
    counts = Counter()
    if not any(heads.values()):
        return None, counts
    chapters = {c['id']: c for c in book['chapters']}
    order = {cid: index for index, cid in enumerate(chapters)}
    cast = [c for c in book['characters'] if c['id'] not in RESERVED_CHARACTERS]
    eligible = {c['id'] for c in cast}
    segments = _Segments(book)
    head_ids = [i for scopes in heads.values() for i in scopes.values()]
    payloads = repository.payloads(conn, head_ids)
    producers = _producers(conn, head_ids)
    rows = {}

    def add(character_id, chapter_id, start, end, kind, quote=None, segment_id=None, **fields):
        chapter = chapters.get(chapter_id)
        if not chapter or type(start) is not int or type(end) is not int:
            counts['invalid'] += 1
            return
        row = {'character_id': character_id, 'chapter_id': chapter_id,
               'segment_id': segment_id or segments.at(chapter_id, start, end), 'start': start, 'end': end,
               # Dialogue carries the passage text, checked against the source below.
               'quote': chapter['text'][start:end] if quote is None else quote, 'kind': kind, 'confidence': None,
               'provider': None, 'model': None, 'step': None, 'version_id': None, 'origin': None,
               'projection': PROJECTION_VERSION, **fields}
        if not reference_is_valid(row, chapters, eligible):
            counts['invalid'] += 1
            return
        row['id'] = digest(row)
        rows[row['id']] = row

    # --- discovery: candidate quotations ------------------------------------------------------
    discovery_spans = {}

    def spans_of(identifier, chapter_id, payload):
        if identifier not in discovery_spans:
            chapter = chapters.get(chapter_id)
            # Only accepted discovery rows are counted; an older version a profile read is just searched.
            tally = counts if heads['discovery'].get(chapter_id) == identifier else Counter()
            if not payload or payload.get('step_id') != 'discovery' or not isinstance(payload.get('result'), dict):
                tally['malformed'] += 1
                discovery_spans[identifier] = []
            elif not chapter:
                tally['invalid'] += 1
                discovery_spans[identifier] = []
            else:
                discovery_spans[identifier] = _discovery_spans(payload['result'], chapter, cast, tally)
        return discovery_spans[identifier]

    for chapter_id, identifier in heads['discovery'].items():
        payload = payloads.get(identifier)
        provider, model = producers.get(identifier, (None, None))
        for cid, start, end, candidate in spans_of(identifier, chapter_id, payload):
            add(cid, chapter_id, start, end, 'profile_evidence', provider=provider, model=model, step='discovery',
                version_id=identifier, origin=(payload or {}).get('origin'),
                profile_description=candidate.get('description', ''), profile_direction=candidate.get('direction', ''))

    # --- profiles: quotations located in the discovery evidence the profile read ----------------
    for character_id, identifier in heads['profiles'].items():
        payload = payloads.get(identifier) or {}
        result = payload.get('result') or {}
        evidence = result.get('evidence')
        if not evidence:
            continue  # baseline/external profiles carry no quotations
        if not isinstance(evidence, list):
            counts['malformed'] += 1
            continue
        if character_id not in eligible:
            counts['unresolved'] += len(evidence)
            continue
        inputs = payload.get('inputs') if isinstance(payload.get('inputs'), dict) else {}
        # The discovery versions this profile read. An empty record (a profile built only from
        # earlier volumes) locates nothing; only a payload with no record falls back to today's heads.
        read = inputs['discovery'] if isinstance(inputs.get('discovery'), dict) else heads['discovery']
        missing = [i for i in read.values() if i not in payloads]
        if missing:
            payloads.update(repository.payloads(conn, missing))
        located = [(chapter_id, start, end) for chapter_id, source in read.items()
                   for cid, start, end, _ in spans_of(source, chapter_id, payloads.get(source)) if cid == character_id]
        provider = result.get('profile_provider') or producers.get(identifier, (None, None))[0]
        model = result.get('profile_model') or producers.get(identifier, (None, None))[1]
        for quote in evidence:
            positions = set()
            if isinstance(quote, str) and quote:
                for chapter_id, start, end in located:
                    text = chapters[chapter_id]['text']
                    offset = text.find(quote, start, end)
                    while offset >= 0 and offset + len(quote) <= end:
                        positions.add((chapter_id, offset))
                        offset = text.find(quote, offset + 1, end)
            if not positions:
                # Typically an earlier volume's quotation: it has no location in this book.
                counts['unanchored'] += 1
                continue
            for chapter_id, offset in sorted(positions, key=lambda p: (order[p[0]], p[1])):
                add(character_id, chapter_id, offset, offset + len(quote), 'profile_evidence', provider=provider,
                    model=model, step='profiles', version_id=identifier, origin=payload.get('origin'),
                    anchors=len(positions), profile_description=result.get('description', ''),
                    profile_direction=result.get('direction', ''))

    # --- directing: attributed dialogue as the book shows it ----------------------------------
    for segment in book['segments']:
        if segment.get('kind') != 'dialogue' or segment.get('speaker_id') not in eligible:
            continue
        chapter_id = segment['chapter_id']
        head = heads['directing'].get(chapter_id)
        if locked(segment, 'speaker_id'):
            provenance = {'provider': 'reviewed', 'model': None, 'origin': 'manual'}
        elif head:
            provenance = {'provider': segment.get('analysis_provider'), 'model': segment.get('analysis_model'),
                          'step': 'directing', 'version_id': head, 'origin': (payloads.get(head) or {}).get('origin')}
        else:
            # Attributed outside any accepted directing version (for example a chapter
            # whose scenes could not be captured): the producer is unknown.
            provenance = {'provider': segment.get('analysis_provider'), 'model': segment.get('analysis_model'),
                          'origin': 'book'}
        add(segment['speaker_id'], chapter_id, segment.get('start'), segment.get('end'), 'dialogue',
            quote=segment.get('text'), segment_id=segment['id'], confidence=segment.get('confidence'), **provenance)

    # --- mentions: current cast names ---------------------------------------------------------
    for cid, chapter_id, start, end in _mentions(book, cast, chapters):
        add(cid, chapter_id, start, end, 'mention', provider='local', origin='cast_names')

    # --- legacy discovery evidence, until discovery has an accepted version ----------------------
    if not heads['discovery']:
        for ref in previous:
            if 'projection' in ref or ref.get('kind') != 'profile_evidence' or ref.get('id') in rows:
                continue
            if isinstance(ref.get('id'), str) and reference_is_valid(ref, chapters, eligible):
                rows[ref['id']] = deepcopy(ref)
                counts['carried'] += 1
            else:
                counts['invalid'] += 1

    ordered = sorted(rows.values(), key=lambda r: (order.get(r['chapter_id'], len(order)), r['start'], r['end'],
                                                   r['kind'], r['character_id'], r['id']))
    counts['rows'] = len(ordered)
    return ordered, counts


def _stored(conn, book_id):
    return [json.loads(body) for (body,) in conn.execute(
        'SELECT body FROM character_references WHERE book_id=? ORDER BY rowid', (book_id,))]


def _inputs(repository, conn, book):
    """Everything a rebuild reads, as one digest (skips unchanged rebuilds)."""
    heads = {step: repository.heads(conn, book['id'], step) for step in STEPS}
    return digest([PROJECTION_VERSION, heads,
                   [[c['id'], c.get('name'), c.get('aliases')] for c in book['characters']],
                   [[c['id'], digest(c['text'])] for c in book['chapters']],
                   # Every passage: evidence and mention rows take segment_id from the passage covering them.
                   [[s['id'], s['chapter_id'], s.get('start'), s.get('end'), s.get('kind'), s.get('speaker_id'),
                     locked(s, 'speaker_id'), s.get('confidence'), s.get('analysis_provider'), s.get('analysis_model')]
                    for s in book['segments']]])


def retain_history(conn, book_id, rows):
    """Append projected rows to ``character_observations`` (content-addressed, ``INSERT OR IGNORE``).

    Called by :func:`refresh` only when :data:`RETAIN_OBSERVATIONS` is on; pass new
    rows only (earlier ones were retained when first written).
    """
    retain_observations(conn, book_id, rows)


def refresh(repository, conn, book, *, force=False):
    """Rebuild ``book``'s references inside the caller's transaction when inputs changed.

    Returns the recorded state entry, or ``None`` when nothing was done (no
    accepted evidence step, or nothing changed since the last rebuild).
    """
    book_id = book['id']
    state = repository.state(conn, book_id).get(STATE_KEY) or {}
    stored = digest(sorted(i for (i,) in conn.execute('SELECT id FROM character_references WHERE book_id=?', (book_id,))))
    inputs = _inputs(repository, conn, book)
    # A state from before chapter hashes were recorded is rebuilt once to record them.
    if not force and state.get('inputs') == inputs and state.get('rows') == stored and 'sources' in state:
        return None
    previous = _stored(conn, book_id)
    rows, counts = build(repository, conn, book, previous)
    if rows is None:
        return None
    known = {r.get('id') for r in previous}
    conn.execute('DELETE FROM character_references WHERE book_id=?', (book_id,))
    conn.executemany('''INSERT INTO character_references (book_id,id,character_id,chapter_id,segment_id,body)
        VALUES (?,?,?,?,?,?)''', [(book_id, r['id'], r['character_id'], r['chapter_id'], r.get('segment_id'),
                                   json.dumps(r, ensure_ascii=False)) for r in rows])
    if RETAIN_OBSERVATIONS:
        retain_history(conn, book_id, [r for r in rows if r['id'] not in known])
    # The chapter sources the rows were validated against. Series context of a later
    # volume excludes a row whose chapter text has changed since (bardic.series).
    entry = {'version': PROJECTION_VERSION, 'inputs': inputs, 'rows': digest(sorted(r['id'] for r in rows)),
             'sources': {c['id']: source_hash(c['text']) for c in book['chapters']},
             'counts': dict(sorted(counts.items())), 'rebuilt_at': now()}
    repository.set_state(conn, book_id, **{STATE_KEY: entry})
    return entry
