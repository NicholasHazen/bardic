"""Book pronunciation lexicon: respell names in the text sent to a narrator.

Narration models guess invented and unusual names. None of the supported
providers accepts phonemes (Gemini reads a verbatim transcript; Breeze has no
lexicon; macOS `say` is the only one with a phoneme mode), so an entry is a
plain respelling that every provider reads as ordinary text.

Canonical chapter text never changes. `apply` returns the spoken text for one
request plus the replaced spans, which map spoken offsets back to source
code points. A recipe records only the entries a passage actually used, so
editing an entry changes the audio identity of the passages containing its
term and nothing else.

A 2026-09-28 listening trial (macOS and Breeze, five invented names) found a
natural-looking respelling ("Kaylor") never wrong, hyphenated syllables
sometimes read as separate words ("ay" as "aye"), and capitalized stress read
as letters or abbreviations. See docs/RESEARCH-VOICE.md.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any
from uuid import uuid4

LEXICON_VERSION = 1
MAX_ENTRIES = 500
MAX_TERM_CHARS = 80
MAX_RESPELLING_CHARS = 120
PROVIDERS = ("system", "gemini", "breeze")
# Brackets and angle brackets are performed rather than read by some providers:
# Gemini inline tags (<sigh>), Breeze events ((laugh)) and `say` commands ([[...]]).
# A respelling must stay speech text.
_FORBIDDEN = re.compile(r"[<>()\[\]{}\\]")
_ENTRY_ID = re.compile(r"pr_[0-9a-f]{12}")


class PronunciationError(ValueError):
    pass


def _clean(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise PronunciationError(f"{label} must be text.")
    value = unicodedata.normalize("NFC", " ".join(value.split()))
    if not value:
        raise PronunciationError(f"{label} is empty.")
    if len(value) > limit:
        raise PronunciationError(f"{label} is longer than {limit} characters.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise PronunciationError(f"{label} contains control characters.")
    return value


def _respelling(value: Any, label: str) -> str:
    value = _clean(value, label, MAX_RESPELLING_CHARS)
    if _FORBIDDEN.search(value):
        raise PronunciationError(f"{label} cannot contain brackets, parentheses or backslashes; "
                                 "narrators perform those as sound effects or commands.")
    if not any(char.isalpha() for char in value):
        raise PronunciationError(f"{label} needs at least one letter.")
    return value


def normalize_entry(value: Any, *, entry_id: str | None = None) -> dict:
    """Validate one entry. Unknown fields are refused rather than silently kept."""
    if not isinstance(value, dict):
        raise PronunciationError("A pronunciation entry must be an object.")
    allowed = {"id", "term", "respelling", "providers", "match_case", "character_id", "note"}
    if set(value) - allowed:
        raise PronunciationError("Unknown pronunciation fields: " + ", ".join(sorted(set(value) - allowed)) + ".")
    term = _clean(value.get("term"), "The word", MAX_TERM_CHARS)
    if not any(char.isalnum() for char in term):
        raise PronunciationError("The word needs at least one letter or digit.")
    providers = value.get("providers") or {}
    if not isinstance(providers, dict) or set(providers) - set(PROVIDERS):
        raise PronunciationError("Provider respellings accept only " + ", ".join(PROVIDERS) + ".")
    overrides = {name: _respelling(text, f"The {name} respelling")
                 for name, text in sorted(providers.items()) if text not in (None, "")}
    match_case = value.get("match_case", True)
    if type(match_case) is not bool:
        raise PronunciationError("match_case must be true or false.")
    character_id = value.get("character_id")
    if character_id is not None and (not isinstance(character_id, str) or not 0 < len(character_id) <= 128):
        raise PronunciationError("character_id must be a character ID.")
    note = value.get("note") or ""
    if not isinstance(note, str) or len(note) > 500:
        raise PronunciationError("The note must be text of at most 500 characters.")
    identifier = entry_id or value.get("id") or "pr_" + uuid4().hex[:12]
    if not isinstance(identifier, str) or not _ENTRY_ID.fullmatch(identifier):
        raise PronunciationError("Invalid pronunciation entry ID.")
    entry = {"id": identifier, "term": term, "respelling": _respelling(value.get("respelling"), "The respelling"),
             "match_case": match_case}
    if overrides:
        entry["providers"] = overrides
    if character_id:
        entry["character_id"] = character_id
    if note.strip():
        entry["note"] = note.strip()
    return entry


def normalize_lexicon(entries: Any) -> list[dict]:
    """Validate a whole lexicon; one term (under its case rule) may appear once."""
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise PronunciationError("Pronunciations must be a list.")
    if len(entries) > MAX_ENTRIES:
        raise PronunciationError(f"A book can have at most {MAX_ENTRIES} pronunciations.")
    result, seen_ids = [], set()
    for raw in entries:
        entry = normalize_entry(raw)
        if entry["id"] in seen_ids:
            raise PronunciationError("Two pronunciation entries share an ID.")
        seen_ids.add(entry["id"])
        if any(conflicts(entry, other) for other in result):
            raise PronunciationError(f"“{entry['term']}” already has a pronunciation.")
        result.append(entry)
    return result


def conflicts(entry: dict, other: dict) -> bool:
    """Two entries for one word: same spelling, or equal ignoring case unless both match case."""
    return (entry["term"].casefold() == other["term"].casefold() and
            (entry["term"] == other["term"] or not (entry["match_case"] and other["match_case"])))


def spoken_form(entry: dict, provider: str) -> str:
    return (entry.get("providers") or {}).get(provider) or entry["respelling"]


def speech_entries(text: str, entries: list[dict] | None) -> list[dict]:
    """The entries that match ``text``, reduced to the fields that change speech.

    Stored request recipes (voice examples) keep these rather than the whole
    lexicon, so unrelated edits, entry IDs and notes never enter them.
    """
    fields = ("term", "respelling", "providers", "match_case")
    return [{key: entry[key] for key in fields if key in entry} for entry in candidates(text, entries or [])
            if _compiled(((entry["term"], entry["match_case"]),)).search(text)]


# Matching -------------------------------------------------------------------

_APOSTROPHES = "'’ʼ"
_WORD = re.compile(r"\w+")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


@lru_cache(maxsize=4096)
def _term_words(term: str) -> frozenset[str]:
    return frozenset(_WORD.findall(_fold(term)))


def candidates(text: str, entries: list[dict]) -> list[dict]:
    """Entries whose words all occur in ``text``: a cheap superset of the regex matches."""
    if not entries:
        return []
    words = set(_WORD.findall(_fold(text)))
    return [entry for entry in entries if _term_words(entry["term"]) <= words]


def _body(term: str) -> str:
    # A space matches any whitespace (a name can wrap across a line break);
    # straight and curly apostrophes are interchangeable.
    parts = []
    for char in term:
        if char == " ":
            parts.append(r"\s+")
        elif char in _APOSTROPHES:
            parts.append(f"[{_APOSTROPHES}]")
        else:
            parts.append(re.escape(char))
    return "".join(parts)


@lru_cache(maxsize=512)
def _compiled(keys: tuple[tuple[str, bool], ...]) -> re.Pattern:
    parts = []
    for index, (term, match_case) in enumerate(keys):
        # Terms are stored composed (NFC); imported text may be decomposed.
        forms = dict.fromkeys(_body(unicodedata.normalize(form, term)) for form in ("NFC", "NFD"))
        body = "|".join(forms)
        parts.append(f"(?P<e{index}>{body})" if match_case else f"(?P<e{index}>(?i:{body}))")
    # Whole words only: a name inside a longer word ("Will" in "Willow") is not the name.
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)")


def _pattern(entries: list[dict]) -> tuple[re.Pattern | None, list[dict]]:
    # Longest first, so "Tar Valon" wins over "Valon" at the same position.
    ordered = sorted(entries, key=lambda entry: (-len(entry["term"]), entry["term"]))
    if not ordered:
        return None, []
    return _compiled(tuple((entry["term"], entry["match_case"]) for entry in ordered)), ordered


def apply(text: str, entries: list[dict] | None, provider: str) -> tuple[str, list[dict]]:
    """Return the spoken text and replacements in source order.

    Each replacement records source and spoken code-point spans, so provider
    timing measured against the spoken text can be mapped back to the source.
    Entries whose spoken form equals the matched text are not replacements.
    """
    pattern, ordered = _pattern(candidates(text, entries or []))
    if pattern is None:
        return text, []
    pieces, replacements, cursor, shift = [], [], 0, 0
    for match in pattern.finditer(text):
        entry = ordered[int(match.lastgroup[1:])]
        spoken = spoken_form(entry, provider)
        if spoken == match.group(0):
            continue
        start, end = match.span()
        pieces.append(text[cursor:start])
        pieces.append(spoken)
        replacements.append({"id": entry.get("id"), "term": entry["term"], "spoken": spoken,
                             "source": [start, end], "output": [start + shift, start + shift + len(spoken)]})
        shift += len(spoken) - (end - start)
        cursor = end
    if not replacements:
        return text, []
    pieces.append(text[cursor:])
    return "".join(pieces), replacements


def recipe_identity(replacements: list[dict]) -> dict | None:
    """What a render recipe records: the distinct term→spoken pairs actually used."""
    if not replacements:
        return None
    return {"version": LEXICON_VERSION,
            "applied": [list(pair) for pair in sorted({(item["term"], item["spoken"]) for item in replacements})]}


def to_source(offset: int, replacements: list[dict]) -> int:
    """Map a spoken-text code-point offset to the source text.

    An offset inside a respelled word maps to the start of the original word
    (or its end, for an offset at the respelling's end), so boundaries never
    split a source word.
    """
    shift = 0
    for item in replacements:
        out_start, out_end = item["output"]
        if offset < out_start:
            break
        if offset < out_end:
            return item["source"][0]
        if offset == out_end:
            return item["source"][1]
        shift = item["source"][1] - out_end
    return offset + shift


def usage(chapters: list[dict], segments: list[dict], entries: list[dict], *, rendered: set | frozenset = frozenset(),
          limit: int = 3) -> dict[str, dict]:
    """Per entry: matches in chapter text (with a few examples), the passages containing it and
    how many of those have audio in ``rendered`` (passage IDs).

    One combined scan per chapter and passage, as narration applies them: where
    terms overlap ("Tar Valon", "Valon") only the longer match counts.
    """
    stats = {entry["id"]: {"occurrences": 0, "passages": 0, "rendered_passages": 0, "first_passage_id": None, "examples": []}
             for entry in entries}
    for chapter in chapters:
        text = chapter.get("text") or ""
        pattern, ordered = _pattern(candidates(text, entries))
        for match in pattern.finditer(text) if pattern else ():
            item = stats[ordered[int(match.lastgroup[1:])]["id"]]
            item["occurrences"] += 1
            if len(item["examples"]) < limit:
                item["examples"].append({"chapter_id": chapter.get("id"), "start": match.start(), "end": match.end(),
                                         "context": text[max(0, match.start() - 60):match.end() + 60]})
    for segment in segments:
        text = segment.get("text") or ""
        pattern, ordered = _pattern(candidates(text, entries))
        found = dict.fromkeys(ordered[int(match.lastgroup[1:])]["id"] for match in pattern.finditer(text)) if pattern else {}
        for entry_id in found:
            stats[entry_id]["passages"] += 1
            stats[entry_id]["rendered_passages"] += segment.get("id") in rendered
            stats[entry_id]["first_passage_id"] = stats[entry_id]["first_passage_id"] or segment.get("id")
    return stats


def first_match(text: str, entry: dict) -> tuple[int, int] | None:
    if not candidates(text or "", [entry]):
        return None
    pattern, _ = _pattern([entry])
    match = pattern.search(text or "")
    return match.span() if match else None


def sentence_around(text: str, start: int, end: int, limit: int) -> tuple[int, int]:
    """Exact source bounds of the sentence holding [start, end), trimmed to ``limit`` code points."""
    # Sentence ends and line breaks (headings, paragraphs) bound the excerpt.
    before = [m.end() for m in re.finditer(r'[.!?…][”’"\')\]]*\s+|\n', text[:start])]
    begin = before[-1] if before else 0
    after = re.search(r'[.!?…][”’"\')\]]*(?=\s|$)|(?=\n)', text[end:])
    finish = end + after.end() if after else len(text)
    while begin < start and text[begin].isspace():
        begin += 1
    while finish > end and text[finish - 1].isspace():
        finish -= 1
    if finish - begin > limit:
        # Keep the name with some context on each side, cut at whitespace.
        begin = max(begin, start - limit // 2)
        finish = min(finish, begin + limit)
        space = text.find(" ", begin, start)
        begin = space + 1 if 0 <= space < start and begin > 0 else begin
        space = text.rfind(" ", end, finish)
        finish = space if space > end else finish
    return begin, finish


def merged(lexicon: list[dict], draft: dict) -> list[dict]:
    """The lexicon with an unsaved entry in place of the one it edits or would conflict with."""
    kept = [entry for entry in lexicon if entry["id"] != draft["id"] and not conflicts(entry, draft)]
    return [*kept, draft]


def book_lexicon(book: dict) -> list[dict]:
    value = book.get("pronunciations")
    return value if isinstance(value, list) else []


def with_lexicon(segment: dict, lexicon: list[dict] | None) -> dict:
    """The passage a renderer sees. A copy, so the lexicon never persists on a book segment."""
    return {**segment, "pronunciations": lexicon} if lexicon else segment
