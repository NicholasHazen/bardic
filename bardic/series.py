"""Explicit series identities and bounded, source-proven memory from earlier books.

Book character IDs are local even when their names (and ID hashes) happen to match.
Only user-confirmed links connect them to a series character.

Series context (what a later volume's profiles read) comes from each earlier
volume's **current** ``character_references``: the accepted evidence the step
pipeline projects there (:mod:`bardic.pipeline.evidence`). Rolling back or
setting aside a version in volume 1 therefore changes what volume 2 is told.
The append-only ``character_observations`` table is legacy history written by
the removed Classic engine; it is no longer read for prompts (it only re-validates
the source hash of reference rows that engine wrote). History of what was sent lives in
artifacts: step_output versions and the ``character_observation`` artifacts a
profile request records for each earlier-volume entry it includes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from uuid import uuid4


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def initialize_schema(conn):
    had_observations = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='character_observations'").fetchone()
    conn.execute("""CREATE TABLE IF NOT EXISTS series (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS series_books (
        book_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, position REAL NOT NULL,
        UNIQUE(series_id, position))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS series_characters (
        id TEXT PRIMARY KEY, series_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS series_characters_series ON series_characters(series_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS series_character_links (
        book_id TEXT NOT NULL, character_id TEXT NOT NULL, series_character_id TEXT NOT NULL,
        confirmed_at TEXT NOT NULL, PRIMARY KEY(book_id,character_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS series_character_links_identity ON series_character_links(series_character_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS character_observations (
        id TEXT PRIMARY KEY, book_id TEXT NOT NULL, character_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL, source_hash TEXT NOT NULL, body TEXT NOT NULL)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS character_observations_character
        ON character_observations(book_id,character_id)""")
    if not had_observations:
        for (book_id,) in conn.execute("SELECT DISTINCT book_id FROM character_references").fetchall():
            references = [json.loads(row[0]) for row in conn.execute(
                "SELECT body FROM character_references WHERE book_id=?", (book_id,))]
            retain_observations(conn, book_id, references)


REFERENCE_KINDS = frozenset({"profile_evidence", "dialogue", "mention"})


def reference_is_valid(ref, chapters, characters):
    """True when a reference is an exact span of committed source for a real character.

    ``chapters`` maps chapter IDs to chapters; ``characters`` is the set of
    eligible character IDs (narrator and unassigned excluded). Offsets are
    zero-based Python code points with an exclusive end. Nothing is repaired.
    """
    chapter = chapters.get(ref.get("chapter_id"))
    start, end = ref.get("start"), ref.get("end")
    return bool(ref.get("character_id") in characters and chapter and type(start) is int and type(end) is int
                and 0 <= start < end <= len(chapter["text"])
                and chapter["text"][start:end] == ref.get("quote")
                and ref.get("kind") in REFERENCE_KINDS)


def retain_observations(conn, book_id, references):
    """Append valid reference observations inside the caller's publication transaction.

    Only committed book source supplies provenance. Missing/obsolete source spans
    are ignored, never repaired with a different quotation or uncommitted text.
    Replaying a checkpoint inserts no duplicates; a changed interpretation becomes
    a new observation rather than overwriting an earlier one.
    """
    from .artifacts import capture_book, capture_observation, output_head

    row = conn.execute("SELECT body FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        return
    book = json.loads(row[0])
    chapters = {c["id"]: c for c in book.get("chapters", [])}
    characters = {c["id"] for c in book.get("characters", [])} - {"narrator", "unassigned"}
    hashes = {cid: source_hash(c["text"]) for cid, c in chapters.items()}
    saved = []
    for ref in references:
        cid, chapter_id = ref.get("character_id"), ref.get("chapter_id")
        start, end = ref.get("start"), ref.get("end")
        if not reference_is_valid(ref, chapters, characters):
            continue
        observation = {
            "book_id": book_id, "character_id": cid, "chapter_id": chapter_id,
            "segment_id": ref.get("segment_id"), "start": start, "end": end,
            "source_hash": hashes[chapter_id], "quote": ref["quote"], "kind": ref["kind"],
            "description": ref.get("profile_description", ""),
            "direction": ref.get("profile_direction", ""),
            "provider": ref.get("provider"), "model": ref.get("model"),
            "confidence": ref.get("confidence"),
        }
        observation["id"] = _hash(observation)
        observation["recorded_at"] = _now()
        saved.append((observation["id"], book_id, cid, chapter_id, hashes[chapter_id],
                      json.dumps(observation, ensure_ascii=False)))
    conn.executemany("""INSERT OR IGNORE INTO character_observations
        (id,book_id,character_id,chapter_id,source_hash,body) VALUES (?,?,?,?,?,?)""", saved)
    if saved and not output_head(conn, book_id, 'structure', 'book'):
        capture_book(conn, book, legacy_provenance=True, only_missing=True)
    for item in saved:
        capture_observation(conn, book_id, json.loads(item[-1]), chapters)


def _name(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200:
        raise ValueError(f"Choose a {label} of 1–200 characters.")
    return " ".join(value.split())


def _book(conn, book_id):
    row = conn.execute("SELECT body FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        raise KeyError("Book not found")
    return json.loads(row[0])


def _series(conn, series_id):
    row = conn.execute("SELECT id,name,created_at FROM series WHERE id=?", (series_id,)).fetchone()
    if not row:
        raise KeyError("Series not found")
    return dict(zip(("id", "name", "created_at"), row))


def _membership(conn, book_id, include_archived=False):
    from .library import is_archived

    row = conn.execute("""SELECT sb.series_id,s.name,sb.position FROM series_books sb
        JOIN series s ON s.id=sb.series_id WHERE sb.book_id=?""", (book_id,)).fetchone()
    if row and (include_archived or not is_archived(conn, 'series', row[0]) and not is_archived(conn, 'book', book_id)):
        return dict(zip(("series_id", "series_name", "position"), row))
    return None


class SeriesRepository:
    def __init__(self, store):
        self.store = store

    def list_series(self, include_archived=False):
        from .library import is_archived

        with self.store.lock, self.store.connect() as conn:
            result = []
            for row in conn.execute("SELECT id,name,created_at FROM series ORDER BY name COLLATE NOCASE,id"):
                item = dict(zip(("id", "name", "created_at"), row))
                item['archived'] = is_archived(conn, 'series', item['id'])
                if item['archived'] and not include_archived:
                    continue
                item["books"] = []
                item['volumes'] = []
                for book_id, position, body in conn.execute("""SELECT sb.book_id,sb.position,b.body
                    FROM series_books sb JOIN books b ON b.id=sb.book_id
                    WHERE sb.series_id=? ORDER BY sb.position,sb.book_id""", (item["id"],)):
                    book = json.loads(body)
                    archived = is_archived(conn, 'book', book_id)
                    volume = {"book_id": book_id, "position": position, "title": book.get("title", "Untitled"),
                              "author": book.get("author", ""), 'archived': archived}
                    if include_archived or not archived:
                        item["books"].append(volume)
                    item['volumes'].append(dict(volume, status='archived' if archived else 'available'))
                item['volumes'].extend({'series_id': item['id'], 'position': position, 'title': title, 'status': status, 'book_id': None}
                                       for position, title, status in conn.execute('SELECT position,title,status FROM series_volume_slots WHERE series_id=?', (item['id'],)))
                item['volumes'].sort(key=lambda v: (v['position'], v.get('book_id') or ''))
                item["character_count"] = conn.execute("SELECT count(*) FROM series_characters WHERE series_id=?",
                                                        (item["id"],)).fetchone()[0]
                result.append(item)
            return result

    def create_series(self, name):
        name = _name(name, "series name")
        item = {"id": "series_" + uuid4().hex, "name": name, "created_at": _now()}
        with self.store.lock, self.store.connect() as conn:
            if any(row[0].casefold() == name.casefold() for row in conn.execute("SELECT name FROM series")):
                raise ValueError("A series with that name already exists. Select the existing series.")
            conn.execute("INSERT INTO series(id,name,created_at) VALUES (?,?,?)", tuple(item.values()))
        return dict(item, books=[], character_count=0)

    def membership(self, book_id, *, include_archived=False):
        with self.store.lock, self.store.connect() as conn:
            _book(conn, book_id)
            return _membership(conn, book_id, include_archived=include_archived)

    def set_membership(self, book_id, series_id=None, position=None):
        from .artifacts import capture_series
        from .library import LibraryRepository

        with self.store.lock, self.store.connect() as conn:
            _book(conn, book_id)
            self.store.require_active(book_id)
            previous = _membership(conn, book_id, include_archived=True)
            capture_series(conn, book_id, legacy_provenance=True, only_missing=True)
            if series_id is None:
                if position is not None:
                    raise ValueError("Choose a series before setting its reading order.")
                conn.execute("DELETE FROM series_books WHERE book_id=?", (book_id,))
                conn.execute("DELETE FROM series_character_links WHERE book_id=?", (book_id,))
                capture_series(conn, book_id)
                return None
            _series(conn, series_id)
            LibraryRepository(self.store).require_active_series(series_id)
            if (type(position) not in {int, float} or not math.isfinite(position)
                    or not 0 <= position <= 1_000_000):
                raise ValueError("Set a finite reading order between 0 and 1,000,000; decimals allow prequels or side stories.")
            conflict = conn.execute("SELECT book_id FROM series_books WHERE series_id=? AND position=? AND book_id!=?",
                                    (series_id, position, book_id)).fetchone()
            if conflict:
                raise ValueError("Another book already has that reading order in this series.")
            if previous and previous["series_id"] != series_id:
                conn.execute("DELETE FROM series_character_links WHERE book_id=?", (book_id,))
            conn.execute("INSERT OR REPLACE INTO series_books(book_id,series_id,position) VALUES (?,?,?)",
                         (book_id, series_id, position))
            conn.execute('DELETE FROM series_volume_slots WHERE series_id=? AND position=?', (series_id, position))
            capture_series(conn, book_id)
            return _membership(conn, book_id)

    def list_characters(self, series_id):
        with self.store.lock, self.store.connect() as conn:
            _series(conn, series_id)
            result = []
            for row in conn.execute("""SELECT id,series_id,name,created_at FROM series_characters
                WHERE series_id=? ORDER BY name COLLATE NOCASE,id""", (series_id,)):
                item = dict(zip(("id", "series_id", "name", "created_at"), row))
                item["links"] = [dict(zip(("book_id", "character_id", "confirmed_at"), link))
                                 for link in conn.execute("""SELECT book_id,character_id,confirmed_at
                                     FROM series_character_links WHERE series_character_id=?
                                     ORDER BY book_id,character_id""", (item["id"],))]
                result.append(item)
            return result

    def create_character(self, series_id, name):
        name = _name(name, "character name")
        item = {"id": "series_character_" + uuid4().hex, "series_id": series_id,
                "name": name, "created_at": _now()}
        with self.store.lock, self.store.connect() as conn:
            _series(conn, series_id)
            # Duplicate names are permitted: a shared name is not shared identity.
            conn.execute("INSERT INTO series_characters(id,series_id,name,created_at) VALUES (?,?,?,?)", tuple(item.values()))
        return dict(item, links=[])

    def links_for_book(self, book_id):
        with self.store.lock, self.store.connect() as conn:
            book = _book(conn, book_id)
            characters = {c["id"] for c in book.get("characters", [])}
            return [dict(zip(("character_id", "series_character_id", "name", "confirmed_at"), row),
                         stale=row[0] not in characters)
                    for row in conn.execute("""SELECT l.character_id,l.series_character_id,c.name,l.confirmed_at
                        FROM series_character_links l JOIN series_characters c ON c.id=l.series_character_id
                        WHERE l.book_id=? ORDER BY l.character_id""", (book_id,))]

    def link_character(self, book_id, character_id, series_character_id):
        from .artifacts import capture_series

        with self.store.lock, self.store.connect() as conn:
            book = _book(conn, book_id)
            if character_id in {"narrator", "unassigned"}:
                raise ValueError("Narrator and unassigned dialogue cannot be linked to series characters.")
            if character_id not in {c["id"] for c in book.get("characters", [])}:
                raise KeyError("Character not found")
            member = _membership(conn, book_id)
            if member is None:
                raise ValueError("Add this book to a series before linking characters.")
            identity = conn.execute("SELECT series_id,name FROM series_characters WHERE id=?",
                                    (series_character_id,)).fetchone()
            if not identity:
                raise KeyError("Series character not found")
            if identity[0] != member["series_id"]:
                raise ValueError("Choose a character from this book's series.")
            previous = conn.execute("""SELECT series_character_id,confirmed_at FROM series_character_links
                WHERE book_id=? AND character_id=?""", (book_id, character_id)).fetchone()
            confirmed_at = previous[1] if previous and previous[0] == series_character_id else _now()
            capture_series(conn, book_id, legacy_provenance=True, only_missing=True)
            conn.execute("""INSERT OR REPLACE INTO series_character_links
                (book_id,character_id,series_character_id,confirmed_at) VALUES (?,?,?,?)""",
                         (book_id, character_id, series_character_id, confirmed_at))
            capture_series(conn, book_id)
            return {"character_id": character_id, "series_character_id": series_character_id,
                    "name": identity[1], "confirmed_at": confirmed_at, "stale": False}

    def unlink_character(self, book_id, character_id):
        from .artifacts import capture_series

        with self.store.lock, self.store.connect() as conn:
            _book(conn, book_id)
            capture_series(conn, book_id, legacy_provenance=True, only_missing=True)
            conn.execute("DELETE FROM series_character_links WHERE book_id=? AND character_id=?", (book_id, character_id))
            capture_series(conn, book_id)
        return {"character_id": character_id, "linked": False}

    def observations(self, book_id, character_id=None):
        """Return durable historical observations for inspection, including old source versions."""
        with self.store.lock, self.store.connect() as conn:
            _book(conn, book_id)
            query, args = "SELECT body FROM character_observations WHERE book_id=?", (book_id,)
            if character_id is not None:
                query += " AND character_id=?"
                args += (character_id,)
            return [json.loads(row[0]) for row in conn.execute(query + " ORDER BY rowid", args)]

    def context_for_book(self, book_id, max_chars=12000, max_observations_per_character=8):
        """Read earlier volumes' accepted evidence for confirmed links, without model calls.

        Only strictly earlier, active volumes of the same active series count, and
        only characters with a confirmed identity link. Each entry is an earlier
        volume's current ``character_references`` row (``profile_evidence`` or
        ``dialogue``; a mention is not proof of presence), re-validated against that
        volume's current source: the quote must equal the slice, and the chapter's
        source hash must match the one the row was produced from (see
        :class:`_SourceCheck`). The character array is bounded by serialized length,
        including provenance. A fixed fingerprint makes dependency changes
        detectable without timestamps.
        """
        if type(max_chars) is not int or not 256 <= max_chars <= 100_000:
            raise ValueError("Series context must allow between 256 and 100,000 characters.")
        if type(max_observations_per_character) is not int or not 1 <= max_observations_per_character <= 30:
            raise ValueError("Choose 1–30 series observations per character.")
        with self.store.lock, self.store.connect() as conn:
            book = _book(conn, book_id)
            member = _membership(conn, book_id)
            result = {"series": None, "membership": member, "characters": [],
                      "available_observations": 0, "included_observations": 0, "truncated": False}
            dependencies = {"book_id": book_id, "membership": member, "links": [], "sources": []}
            if member:
                result["series"] = {"id": member["series_id"], "name": member["series_name"]}
                linked = _linked(conn, book, member)
                dependencies["links"] = linked
                prior = _earlier_volumes(conn, member)
                for source_id, (position, _, chapters) in prior.items():
                    dependencies["sources"].append([source_id, position, [(cid, value[2]) for cid, value in chapters.items()]])
                sources = _SourceCheck(conn)
                candidates = []
                for character_id, identity_id, name in linked:
                    observations = []
                    source_links = list(conn.execute("""SELECT book_id,character_id FROM series_character_links
                        WHERE series_character_id=? ORDER BY book_id,character_id""", (identity_id,)))
                    dependencies.setdefault("source_links", []).extend([identity_id, *link] for link in source_links if link[0] in prior)
                    for source_id, source_character_id in source_links:
                        if source_id not in prior:
                            continue
                        position, source, chapters = prior[source_id]
                        if source_character_id not in {c["id"] for c in source.get("characters", [])}:
                            continue
                        by_id = {cid: value[1] for cid, value in chapters.items()}
                        for (body,) in conn.execute("""SELECT body FROM character_references
                            WHERE book_id=? AND character_id=? ORDER BY rowid""", (source_id, source_character_id)):
                            ref = json.loads(body)
                            if ref.get("kind") not in CONTEXT_KINDS:
                                continue  # a mention is not proof of presence
                            if not reference_is_valid(ref, by_id, {source_character_id}):
                                continue
                            chapter = chapters[ref["chapter_id"]]
                            observation = observation_of(source_id, ref, chapter[2])
                            if not sources.current(source_id, ref, observation):
                                continue
                            entry = {**observation, "step": ref.get("step"), "version_id": ref.get("version_id"),
                                     "origin": ref.get("origin"), "book_title": source.get("title", "Untitled"),
                                     "position": position, "chapter_title": chapter[1]["title"], "chapter_index": chapter[0]}
                            entry["description"] = entry["description"] or ""
                            entry["direction"] = entry["direction"] or ""
                            observations.append(entry)
                    # One entry per reading of a location: the same row can be reached twice
                    # through two source characters linked to one identity.
                    observations = list({o["id"]: o for o in observations}.values())
                    observations.sort(key=lambda o: (o["position"], o["book_id"], o["chapter_index"], o["start"], o["id"]))
                    result["available_observations"] += len(observations)
                    # Even sampling includes early, middle and late evidence. Contradictory
                    # interpretations at the same source remain separate entries.
                    if len(observations) > max_observations_per_character:
                        if max_observations_per_character == 1:
                            observations = observations[-1:]
                        else:
                            n, k = len(observations), max_observations_per_character
                            observations = [observations[round(i * (n - 1) / (k - 1))] for i in range(k)]
                    candidates.append(({"character_id": character_id, "series_character_id": identity_id,
                                        "name": name, "observations": []}, observations))
                # Round-robin allocation stops a major character consuming every slot.
                for index in range(max_observations_per_character):
                    for character, observations in candidates:
                        if index >= len(observations):
                            continue
                        previous = list(result["characters"])
                        if character not in result["characters"]:
                            result["characters"].append(character)
                        character["observations"].append(observations[index])
                        if len(json.dumps(result["characters"], ensure_ascii=False)) > max_chars:
                            character["observations"].pop()
                            result["characters"] = previous
                        else:
                            result["included_observations"] += 1
            result["truncated"] = result["included_observations"] < result["available_observations"]
            result["context_chars"] = len(json.dumps(result["characters"], ensure_ascii=False))
            result["fingerprint"] = _hash({"version": 2, "dependencies": dependencies,
                                           "characters": result["characters"], "max_chars": max_chars,
                                           "max_observations_per_character": max_observations_per_character})
            return result

    def suggestions(self, book_id):
        """Proposed identity links for this book's unlinked characters. Nothing is linked here.

        A proposal needs an exact normalized (case- and whitespace-insensitive)
        match between one of the character's names or aliases and a name or alias
        of a character that already has a confirmed link in a strictly earlier,
        active volume. Several identities matching one character (namesakes), or
        one identity matching several characters, are marked ``ambiguous``: the
        owner must choose. Only the owner's confirmation (the link route) links.
        """
        with self.store.lock, self.store.connect() as conn:
            book = _book(conn, book_id)
            member = _membership(conn, book_id)
            result = {"book_id": book_id, "series_id": member["series_id"] if member else None, "suggestions": []}
            if not member:
                return result
            linked_here = {row[0] for row in conn.execute(
                "SELECT character_id FROM series_character_links WHERE book_id=?", (book_id,))}
            index = {}
            for source_id, (position, source, _) in _earlier_volumes(conn, member).items():
                cast = {c["id"]: c for c in source.get("characters", [])}
                for character_id, identity_id, identity_name in conn.execute("""SELECT l.character_id,l.series_character_id,c.name
                    FROM series_character_links l JOIN series_characters c ON c.id=l.series_character_id
                    WHERE l.book_id=? AND c.series_id=? ORDER BY l.character_id""", (source_id, member["series_id"])):
                    character = cast.get(character_id)
                    if character is None or character_id in RESERVED:
                        continue
                    origin = {"book_id": source_id, "title": source.get("title", "Untitled"), "position": position,
                              "character_id": character_id, "character_name": character.get("name", "")}
                    for spelling in _spellings(character):
                        entry = index.setdefault(_name_key(spelling), {}).setdefault(
                            identity_id, {"name": identity_name, "sources": []})
                        if origin not in entry["sources"]:
                            entry["sources"].append(origin)
            suggestions = []
            for character in book.get("characters", []):
                if character["id"] in RESERVED or character["id"] in linked_here:
                    continue
                found = {}
                for spelling in _spellings(character):
                    for identity_id, entry in index.get(_name_key(spelling), {}).items():
                        item = found.setdefault(identity_id, {"series_character_id": identity_id, "name": entry["name"],
                                                              "matched_names": [], "sources": []})
                        if spelling not in item["matched_names"]:
                            item["matched_names"].append(spelling)
                        for origin in entry["sources"]:
                            if origin not in item["sources"]:
                                item["sources"].append(origin)
                if found:
                    candidates = sorted(found.values(), key=lambda c: (c["name"].casefold(), c["series_character_id"]))
                    for item in candidates:
                        item["sources"].sort(key=lambda o: (o["position"], o["book_id"], o["character_id"]))
                    suggestions.append({"character_id": character["id"], "character_name": character.get("name", ""),
                                        "candidates": candidates, "ambiguous": len(candidates) > 1})
            # One identity proposed for two characters of this book is a choice too.
            proposed = {}
            for suggestion in suggestions:
                for item in suggestion["candidates"]:
                    proposed.setdefault(item["series_character_id"], []).append(suggestion)
            for shared in proposed.values():
                if len(shared) > 1:
                    for suggestion in shared:
                        suggestion["ambiguous"] = True
            result["suggestions"] = sorted(suggestions, key=lambda s: (s["character_name"].casefold(), s["character_id"]))
            return result


RESERVED = frozenset({"narrator", "unassigned"})
CONTEXT_KINDS = frozenset({"profile_evidence", "dialogue"})
EVIDENCE_STEPS = ("discovery", "profiles", "directing")


def _name_key(name):
    return " ".join(name.casefold().split())


def _spellings(character):
    return [n for n in [character.get("name"), *(character.get("aliases") or [])] if isinstance(n, str) and n.strip()]


def observation_of(book_id, ref, chapter_hash):
    """The observation record of a valid reference, in :func:`retain_observations`' exact format.

    Its ``id`` is the content hash that function gives, so a reference the phase
    engine wrote maps to the observation it retained against the same source.
    """
    observation = {
        "book_id": book_id, "character_id": ref["character_id"], "chapter_id": ref["chapter_id"],
        "segment_id": ref.get("segment_id"), "start": ref["start"], "end": ref["end"],
        "source_hash": chapter_hash, "quote": ref["quote"], "kind": ref["kind"],
        "description": ref.get("profile_description", ""),
        "direction": ref.get("profile_direction", ""),
        "provider": ref.get("provider"), "model": ref.get("model"),
        "confidence": ref.get("confidence"),
    }
    observation["id"] = _hash(observation)
    return observation


class _SourceCheck:
    """Is a reference row still anchored to the chapter source it was produced from?

    * A pipeline projection row (it has ``projection``): the book's evidence state
      records the chapter hashes the projection was built from, and a chapter whose
      text changed since is excluded until the next rebuild. A state recorded
      before those hashes existed falls back to the exact-slice check.
    * A row the removed Classic engine wrote: it counts only while the observation that the
      same checkpoint retained has the current source hash.
    """

    def __init__(self, conn):
        self.conn = conn
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.has_state = "pipeline_state" in tables
        self.has_observations = "character_observations" in tables
        self.states = {}

    def _sources(self, book_id):
        if book_id not in self.states:
            recorded = None
            if self.has_state:
                row = self.conn.execute("SELECT body FROM pipeline_state WHERE book_id=?", (book_id,)).fetchone()
                recorded = ((json.loads(row[0]).get("evidence") or {}) if row else {}).get("sources")
            self.states[book_id] = recorded
        return self.states[book_id]

    def current(self, book_id, ref, observation):
        if "projection" in ref:
            recorded = self._sources(book_id)
            return recorded is None or recorded.get(ref["chapter_id"]) == observation["source_hash"]
        if not self.has_observations:
            return False
        return self.conn.execute("SELECT 1 FROM character_observations WHERE id=?", (observation["id"],)).fetchone() is not None


def _linked(conn, book, member):
    """[character_id, series_character_id, identity name] for confirmed links of current characters."""
    current_ids = {c["id"] for c in book.get("characters", [])} - RESERVED
    return [list(row) for row in conn.execute("""SELECT l.character_id,l.series_character_id,c.name
        FROM series_character_links l JOIN series_characters c ON c.id=l.series_character_id
        WHERE l.book_id=? AND c.series_id=? ORDER BY l.character_id""", (book["id"], member["series_id"]))
            if row[0] in current_ids]


def _earlier_volumes(conn, member):
    """{book_id: (position, book, {chapter_id: (index, chapter, source hash)})} strictly before ``member``."""
    prior = {}
    for source_id, position, body in conn.execute("""SELECT sb.book_id,sb.position,b.body FROM series_books sb
        JOIN books b ON b.id=sb.book_id WHERE sb.series_id=? AND sb.position<?
        AND NOT EXISTS(SELECT 1 FROM library_archives a WHERE a.kind='book' AND a.entity_id=b.id)
        ORDER BY sb.position,sb.book_id""", (member["series_id"], member["position"])):
        source = json.loads(body)
        chapters = {c["id"]: (index, c, source_hash(c["text"])) for index, c in enumerate(source.get("chapters", []))}
        prior[source_id] = (position, source, chapters)
    return prior


def evidence_heads(conn, book_id):
    """{step: {scope: artifact_id}} of a book's accepted evidence-step versions."""
    heads = {step: {} for step in EVIDENCE_STEPS}
    for key, artifact_id in conn.execute("""SELECT logical_key,artifact_id FROM artifact_heads
        WHERE book_id=? AND kind='step_output'""", (book_id,)):
        step, _, scope = key.partition(":")
        if step in heads:
            heads[step][scope] = artifact_id
    return heads


def evidence_inputs(conn, book_id):
    """{character_id: {source_book_id: digest}} of the earlier-volume evidence each linked character can read.

    For each confirmed link of this book and each strictly earlier, active volume
    where the same identity is linked, the digest covers the characters linked
    there, that volume's accepted discovery and directing versions (every chapter)
    and those characters' accepted profiles. A profile version records it at run
    time; when it differs later, the profile is stale
    (:func:`bardic.pipeline.projection.stale_scopes`). Nothing re-runs. A manual
    speaker edit in the earlier volume changes its references but no version, so
    it is not detected (the same limit as staleness within one book).
    """
    member = _membership(conn, book_id)
    if not member:
        return {}
    row = conn.execute("SELECT body FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        return {}
    book = json.loads(row[0])
    earlier = dict(conn.execute("""SELECT sb.book_id,sb.position FROM series_books sb
        WHERE sb.series_id=? AND sb.position<?
        AND NOT EXISTS(SELECT 1 FROM library_archives a WHERE a.kind='book' AND a.entity_id=sb.book_id)""",
                                (member["series_id"], member["position"])).fetchall())
    heads, result = {}, {}
    for character_id, identity_id, _ in _linked(conn, book, member):
        sources = {}
        for source_id, source_character_id in conn.execute("""SELECT book_id,character_id FROM series_character_links
            WHERE series_character_id=? ORDER BY book_id,character_id""", (identity_id,)):
            if source_id in earlier:
                sources.setdefault(source_id, []).append(source_character_id)
        for source_id, characters in sources.items():
            if source_id not in heads:
                heads[source_id] = evidence_heads(conn, source_id)
            accepted = heads[source_id]
            result.setdefault(character_id, {})[source_id] = _hash({
                "version": 1, "position": earlier[source_id], "characters": sorted(characters),
                "discovery": accepted["discovery"], "directing": accepted["directing"],
                "profiles": {c: accepted["profiles"].get(c) for c in sorted(characters)}})
    return result

