"""Explicit series identities and bounded, source-proven memory from earlier books.

Book character IDs are local even when their names (and ID hashes) happen to match.
Only user-confirmed links connect them to a series character. Observations are kept
separately from mutable profile summaries so later analysis cannot erase history.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from uuid import uuid4
from .errors import NotFound


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
        chapter = chapters.get(chapter_id)
        start, end = ref.get("start"), ref.get("end")
        if (cid not in characters or not chapter or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(chapter["text"])
                or chapter["text"][start:end] != ref.get("quote")
                or ref.get("kind") not in {"profile_evidence", "dialogue", "mention"}):
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
        raise NotFound('book_not_found', "Book not found")
    return json.loads(row[0])


def _series(conn, series_id):
    row = conn.execute("SELECT id,name,created_at FROM series WHERE id=?", (series_id,)).fetchone()
    if not row:
        raise NotFound('series_not_found', "Series not found")
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
                raise NotFound('character_not_found', "Character not found")
            member = _membership(conn, book_id)
            if member is None:
                raise ValueError("Add this book to a series before linking characters.")
            identity = conn.execute("SELECT series_id,name FROM series_characters WHERE id=?",
                                    (series_character_id,)).fetchone()
            if not identity:
                raise NotFound('series_character_not_found', "Series character not found")
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
        """Read confirmed, valid observations from earlier volumes only, without model calls.

        The character array is bounded by serialized length, including provenance.
        All historical observations remain queryable even when omitted from a prompt.
        A fixed fingerprint makes dependency changes detectable without timestamps.
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
                current_ids = {c["id"] for c in book.get("characters", [])} - {"narrator", "unassigned"}
                linked = [row for row in conn.execute("""SELECT l.character_id,l.series_character_id,c.name
                    FROM series_character_links l JOIN series_characters c ON c.id=l.series_character_id
                    WHERE l.book_id=? AND c.series_id=? ORDER BY l.character_id""", (book_id, member["series_id"]))
                          if row[0] in current_ids]
                dependencies["links"] = linked
                prior = {}
                for source_id, position, body in conn.execute("""SELECT sb.book_id,sb.position,b.body FROM series_books sb
                    JOIN books b ON b.id=sb.book_id WHERE sb.series_id=? AND sb.position<?
                    AND NOT EXISTS(SELECT 1 FROM library_archives a WHERE a.kind='book' AND a.entity_id=b.id)
                    ORDER BY sb.position,sb.book_id""",
                                                            (member["series_id"], member["position"])):
                    source = json.loads(body)
                    chapters = {c["id"]: (index, c, source_hash(c["text"]))
                                for index, c in enumerate(source.get("chapters", []))}
                    prior[source_id] = (position, source, chapters)
                    dependencies["sources"].append([source_id, position, [(cid, value[2]) for cid, value in chapters.items()]])
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
                        for (body,) in conn.execute("""SELECT body FROM character_observations
                            WHERE book_id=? AND character_id=? ORDER BY id""", (source_id, source_character_id)):
                            observation = json.loads(body)
                            chapter = chapters.get(observation["chapter_id"])
                            if not chapter or chapter[2] != observation["source_hash"]:
                                continue
                            if observation["kind"] == "mention":
                                continue
                            text, start, end = chapter[1]["text"], observation["start"], observation["end"]
                            if not 0 <= start < end <= len(text) or text[start:end] != observation["quote"]:
                                continue
                            entry = {key: value for key, value in observation.items() if key != "recorded_at"}
                            entry.update(book_title=source.get("title", "Untitled"), position=position,
                                         chapter_title=chapter[1]["title"], chapter_index=chapter[0])
                            observations.append(entry)
                    observations.sort(key=lambda o: (o["position"], o["book_id"], o["chapter_index"], o["start"], o["id"]))
                    result["available_observations"] += len(observations)
                    # Even sampling includes early, middle and late evidence. Contradictory
                    # interpretations at the same source remain separate observations.
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
            result["fingerprint"] = _hash({"version": 1, "dependencies": dependencies,
                                           "characters": result["characters"], "max_chars": max_chars,
                                           "max_observations_per_character": max_observations_per_character})
            return result
