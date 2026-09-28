"""Self-hosted analysis services on the owner's network.

Three optional servers, each configured by its root URL:

* ``local_llm``: an OpenAI-compatible server (vLLM) that answers the same
  Responses requests and JSON schemas as the cloud providers.
* ``booknlp``: BookNLP quote attribution. Given a chapter it returns every
  quotation with exact code-point offsets, a speaker, the dialogue tag or beat
  beside it and a conflict flag, plus pronoun-based gender and places.
* ``novel_analyzer``: a chapter-to-script service (an LLM behind a fixed
  pipeline). It returns speakers, delivery, cues and scene breaks.

They cost nothing per request, but they share the owner's GPU (and usually
Breeze narration's). Chapter-service requests run one at a time per server host
across the whole process; Local LLM requests follow the run's concurrency, and
nothing here coordinates with Breeze. Addresses are
configuration, not credentials; they never appear in artifacts. This module
does the HTTP and the pure mapping from service output onto Bardic's source
coordinates. It never rewrites prose, and a result whose quotations do not
match the exact source slice is rejected rather than repaired.
"""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import urlsplit

import httpx

LOCAL_LLM = 'local_llm'
BOOKNLP = 'booknlp'
NOVEL_ANALYZER = 'novel_analyzer'
SERVICES = {
    LOCAL_LLM: {'label': 'Local LLM', 'env': 'BARDIC_LOCAL_LLM_URL', 'example': 'http://host.local:8000'},
    BOOKNLP: {'label': 'BookNLP', 'env': 'BARDIC_BOOKNLP_URL', 'example': 'http://host.local:8100'},
    NOVEL_ANALYZER: {'label': 'Novel Analyzer', 'env': 'BARDIC_NOVEL_ANALYZER_URL', 'example': 'http://host.local:8200'},
}
LOCAL_LLM_DEFAULT_MODEL = 'qwen3.6-35b-a3b'
MAX_CHARACTERS = {BOOKNLP: 2_000_000, NOVEL_ANALYZER: 250_000}
# A 10,000-word (~60,000-character) chapter takes 1-2 minutes on the analyzer with an idle GPU,
# and about 1.6x that while sharing it; allow several times that, scaled by length.
TIMEOUTS = {BOOKNLP: 600.0, NOVEL_ANALYZER: 900.0, LOCAL_LLM: 900.0}
MAX_TIMEOUT = 3600.0
SECONDS_PER_CHARACTER = {BOOKNLP: .002, NOVEL_ANALYZER: .012}
# 503: loading or busy, no work done (both services). 502: the analyzer's LLM ran and failed,
# so repeating it repeats minutes of GPU work; it gets at most one retry.
NO_WORK_STATUSES = {BOOKNLP: {503}, NOVEL_ANALYZER: {503}}
FAILED_WORK_STATUSES = {BOOKNLP: set(), NOVEL_ANALYZER: {502}}
ATTEMPTS = 3
MAX_RETRY_DELAY = 30.0
# One chapter-service request at a time per server host, across every run in this process:
# BookNLP and the analyzer usually share one GPU. The Local LLM (which batches) and Breeze
# narration on the same machine are not coordinated with this.
_HOST_LOCKS: dict[str, threading.Lock] = {}
_HOST_LOCKS_GUARD = threading.Lock()


class ServiceError(ValueError):
    pass


def host_lock(base_url) -> threading.Lock:
    host = (urlsplit(base_url).hostname or base_url).casefold()
    with _HOST_LOCKS_GUARD:
        return _HOST_LOCKS.setdefault(host, threading.Lock())


def timeout_for(provider, text) -> float:
    return min(MAX_TIMEOUT, TIMEOUTS[provider] + SECONDS_PER_CHARACTER.get(provider, 0) * len(text or ''))


def normalize_url(value, provider) -> str:
    """Accept a plain http(s) server root; reject credentials, paths and queries."""
    label = SERVICES[provider]['label']
    if not isinstance(value, str):
        raise ValueError(f'Enter the {label} server URL.')
    value = value.strip().rstrip('/')
    if not value:
        return ''
    if len(value) > 500:
        raise ValueError(f'The {label} server URL is too long.')
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError(f'Use an http:// or https:// {label} server URL, for example {SERVICES[provider]["example"]}.')
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise ValueError(f'Enter only the {label} server address and port, without a path, query or credentials.')
    try:
        parts.port
    except ValueError:
        raise ValueError(f'The {label} server port is invalid.') from None
    return f'{parts.scheme}://{parts.netloc}'


def _wait(seconds, cancelled):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancelled():
            raise InterruptedError('Stopped while waiting for a local service.')
        time.sleep(min(.5, max(0., deadline - time.monotonic())))


def health(client, provider, base_url) -> dict | None:
    """The service's own description of itself (for provenance), or None if it does not answer."""
    try:
        response = client.get(f'{base_url}/health', timeout=httpx.Timeout(10, connect=5))
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    # Not 'llm': the analyzer reports its backend's network address there, which must not be retained.
    return {key: payload[key][:200] for key in ('status', 'model') if isinstance(payload.get(key), str)}


def analyze(client, provider, base_url, body, cancelled, *, wait=_wait) -> dict:
    """POST one chapter to a chapter service, one request per server host at a time.

    A connection failure sent nothing and a 503 did no work, so both are retried
    up to ATTEMPTS in all. An analyzer 502 did work (its LLM ran and failed), so it
    is retried once. A timeout is not repeated: the server may still be working on
    it. A request already sent cannot be interrupted; cancellation is checked
    before each attempt and while waiting.
    """
    label = SERVICES[provider]['label']
    if not base_url:
        raise ServiceError(f'Add the {label} server URL in Settings first.')
    text = body.get('text') or ''
    if len(text) > MAX_CHARACTERS[provider]:
        raise ServiceError(f'This section has {len(text):,} characters; {label} accepts at most '
                           f'{MAX_CHARACTERS[provider]:,}. Nothing was sent.')
    timeout = httpx.Timeout(timeout_for(provider, text), connect=10)
    failed_work_retried = False
    lock = host_lock(base_url)
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        while not lock.acquire(timeout=.5):
            if cancelled():
                raise InterruptedError(f'Stopped while waiting for {label}.')
        unreachable = None
        try:
            if cancelled():
                raise InterruptedError(f'Stopped before the {label} request.')
            response = client.post(f'{base_url}/v1/analyze', json=body, timeout=timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            unreachable = exc
        except httpx.RequestError as exc:
            raise ServiceError(f'{label} did not finish the request ({type(exc).__name__}). It was not repeated.') from exc
        finally:
            lock.release()
        if unreachable is not None:
            if not last:
                wait(2 + 2 * attempt, cancelled)
                continue
            raise ServiceError(f'{label} could not be reached ({type(unreachable).__name__}). '
                               'Check that the server is running.') from unreachable
        try:
            payload = response.json()
        except ValueError:
            payload = None
        status = response.status_code
        retry = not last and (status in NO_WORK_STATUSES[provider] or
                              (status in FAILED_WORK_STATUSES[provider] and not failed_work_retried))
        if retry:
            failed_work_retried = failed_work_retried or status in FAILED_WORK_STATUSES[provider]
            try:
                delay = float(response.headers.get('retry-after', 5 + 5 * attempt))
            except ValueError:
                delay = 5 + 5 * attempt
            wait(min(max(delay, 1.), MAX_RETRY_DELAY), cancelled)
            continue
        if status != 200:
            detail = payload.get('detail') if isinstance(payload, dict) else None
            detail = detail if isinstance(detail, str) else 'Request failed'
            raise ServiceError(f'{label} returned HTTP {status}: {detail[:300]}')
        if not isinstance(payload, dict):
            raise ServiceError(f'{label} returned an invalid response.')
        return payload
    raise AssertionError('unreachable: the last attempt always returns or raises')


# --- the cast as the services should see it ----------------------------------------------

RESERVED = frozenset({'narrator', 'unassigned'})
# A name or alias like these marks the cast character who says "I" (models name them either way).
FIRST_PERSON_ALIASES = frozenset({'i', 'me', 'myself', 'i (narrator)', 'narrator (i)', 'narrator', 'the narrator',
                                  'first-person narrator', 'the first-person narrator'})


def _key(name):
    return ' '.join(str(name).casefold().split())


def _names(character):
    return list(dict.fromkeys(n.strip() for n in [character['name'], *character.get('aliases', []),
                                                  *character.get('former_names', [])]
                              if isinstance(n, str) and n.strip()))


def cast(book):
    return [c for c in book['characters'] if c['id'] not in RESERVED]


def name_index(book) -> dict[str, str]:
    """{normalized name: character ID} for names that identify exactly one character."""
    owners: dict[str, set[str]] = {}
    for character in cast(book):
        for name in _names(character):
            owners.setdefault(_key(name), set()).add(character['id'])
    return {name: next(iter(ids)) for name, ids in owners.items() if len(ids) == 1}


def resolve(index, *names):
    """The first name that identifies exactly one cast character, or None."""
    for name in names:
        if isinstance(name, str) and _key(name) in index:
            return index[_key(name)]
    return None


# A label a model adds to a name, e.g. "Miss Vance (Narrator)" or "Ken (I)".
NARRATOR_MARK = re.compile(r'\s*\((?:the\s+)?(?:first[- ]person\s+)?(?:narrator|i)\)\s*$', re.IGNORECASE)


def _first_person_name(name):
    return _key(name) in FIRST_PERSON_ALIASES or bool(NARRATOR_MARK.search(name))


def first_person(book) -> str | None:
    """The one cast character named or aliased as the first-person narrator, or None (none, or ambiguous)."""
    matches = [c['id'] for c in cast(book) if any(_first_person_name(n) for n in [c['name'], *c.get('aliases', [])])]
    return matches[0] if len(matches) == 1 else None


def booknlp_aliases(book) -> list[list[str]]:
    """Name groups BookNLP should merge. Ambiguous names are left out rather than guessed."""
    index = name_index(book)
    narrator = first_person(book)
    groups = []
    for character in cast(book):
        names = []
        for name in _names(character):
            if index.get(_key(name)) != character['id'] or _key(name) in FIRST_PERSON_ALIASES:
                continue
            # "Miss Vance (Narrator)" is a label; the text says "Miss Vance".
            bare = NARRATOR_MARK.sub('', name).strip()
            for candidate in (name, bare):
                if candidate and _key(candidate) not in {_key(n) for n in names} and index.get(_key(candidate), character['id']) == character['id']:
                    names.append(candidate)
        if character['id'] == narrator:
            # BookNLP's first-person cluster is named NARRATOR.
            names.append('NARRATOR')
        if names:
            groups.append(names)
    return groups


def character_sheet(book) -> tuple[dict, dict[str, str]]:
    """The analyzer's character sheet from the cast, and {sheet name: character ID}.

    Speakers can only be sheet names, so each sheet name must identify exactly
    one character; a repeated display name gets a numbered suffix.
    """
    index = name_index(book)
    narrator = first_person(book)
    sheet, names, used = [], {}, set()
    for character in cast(book):
        name, number = character['name'].strip() or character['id'], 2
        while _key(name) in used:
            name, number = f"{character['name'].strip()} ({number})", number + 1
        used.add(_key(name))
        names[name] = character['id']
        aliases = [n for n in _names(character)[1:] if index.get(_key(n)) == character['id']]
        if character['id'] == narrator:
            aliases = ['I (narrator)', *[a for a in aliases if _key(a) not in FIRST_PERSON_ALIASES]]
        sheet.append({'name': name, 'aliases': aliases[:20], 'description': str(character.get('description') or '')[:400]})
    narrator_name = next((n for n, i in names.items() if i == narrator), '')
    return {'narrator': narrator_name, 'characters': sheet}, names


# --- mapping service output onto source coordinates ---------------------------------------------

PARAGRAPH = re.compile(r"\S[^\n]*(?:\n(?![ \t]*\n)[^\n]+)*")


def paragraphs(text) -> list[tuple[int, int]]:
    """Blank-line separated paragraphs, as the importer and both services count them."""
    return [(m.start(), m.end()) for m in PARAGRAPH.finditer(text)]


def match_quotes(chapter_text, segments, quotes) -> tuple[list[tuple[dict, list[str]]], list[dict]]:
    """Pair each service quotation with the dialogue passages it covers.

    Every quotation must be the exact source slice at its offsets, otherwise
    the whole result is rejected: the offsets would not be trustworthy.
    """
    dialogue = sorted((s for s in segments if s['kind'] == 'dialogue'), key=lambda s: s['start'])
    matched, unmatched = [], []
    for quote in quotes:
        start, end, text = quote.get('start'), quote.get('end'), quote.get('text')
        if type(start) is not int or type(end) is not int or not isinstance(text, str) or not 0 <= start < end <= len(chapter_text):
            raise ServiceError('A quotation has invalid source offsets.')
        if chapter_text[start:end] != text:
            raise ServiceError('A quotation does not match the chapter text at its offsets.')
        exact = [s['id'] for s in dialogue if s['start'] == start and s['end'] == end]
        # The importer splits a very long quotation into several passages.
        inside = [s['id'] for s in dialogue if start <= s['start'] and s['end'] <= end]
        around = [s['id'] for s in dialogue if s['start'] <= start and end <= s['end']]
        ids = exact or inside or around[:1]
        if ids:
            matched.append((quote, ids))
        else:
            unmatched.append(quote)
    return matched, unmatched


def locate(paragraph_text, phrase) -> str | None:
    """An exact source excerpt for a service's tag phrase, or None. Never a paraphrase.

    BookNLP joins tokens with spaces and may skip words; the analyzer copies a
    tag "briefly". The longest leading run of the phrase's words that appears
    contiguously in the paragraph (punctuation and spacing as written) is used.
    """
    # A tag is a short phrase; the same bound applies as to model evidence.
    words = re.findall(r"[\w'’-]+", (phrase or '')[:300])[:40]
    for size in range(len(words), 1, -1):
        pattern = r'(?<![\w])' + r"[\W_]+".join(re.escape(w) for w in words[:size]) + r'(?![\w])'
        found = re.search(pattern, paragraph_text)
        if found and found.end() - found.start() <= MAX_EVIDENCE:
            return paragraph_text[found.start():found.end()]
    return None


MAX_EVIDENCE = 600


def paragraph_of(bounds, offset) -> int | None:
    for index, (start, end) in enumerate(bounds):
        if start <= offset < end:
            return index
    return None
