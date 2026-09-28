"""Small durable repository. Each update commits before a worker moves on."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .errors import NotFound


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
# Written once, when a job reaches its terminal status; never changed afterwards.
JOB_OUTCOME_FIELDS = frozenset({"status", "message", "error", "resume_after"})
# Stored bookkeeping that is not part of the published Job (the series plan
# fingerprint stays in storage and in the `series_run` artifact).
INTERNAL_JOB_FIELDS = frozenset({"plan_fingerprint"})
# Job fields renamed in contract 0.2.0, by kind: stored documents from older
# versions are translated when read, and new documents use the new names.
LEGACY_JOB_FIELDS = {"pipeline": {"mode": "scheduling"}, "series": {"limits": "analysis_limits"},
                     "listen_chapter": {"limits": "speech_limits"}}


def upgrade_job(job: dict) -> dict:
    """A stored job document with current field names (the stored row is not rewritten)."""
    for old, new in LEGACY_JOB_FIELDS.get(job.get("kind"), {}).items():
        if old in job:
            value = job.pop(old)
            job.setdefault(new, value)
    return job


def public_job(job: dict) -> dict:
    """The job as the API presents it: without internal bookkeeping."""
    return {name: value for name, value in job.items() if name not in INTERNAL_JOB_FIELDS}


class Store:
    def __init__(self, root: Path):
        from .series import initialize_schema
        from .artifacts import initialize_schema as initialize_artifacts
        from .library import initialize_schema as initialize_library

        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = self.root / "library.sqlite3"
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS books (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, book_id TEXT NOT NULL, body TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS takes (book_id TEXT, segment_id TEXT, body TEXT NOT NULL, PRIMARY KEY(book_id,segment_id))")
            conn.execute("CREATE TABLE IF NOT EXISTS analysis_checkpoints (book_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL)")
            conn.execute("""CREATE TABLE IF NOT EXISTS character_references (
                book_id TEXT NOT NULL, id TEXT NOT NULL, character_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL, segment_id TEXT, body TEXT NOT NULL,
                PRIMARY KEY(book_id,id))""")
            conn.execute("CREATE INDEX IF NOT EXISTS character_references_character ON character_references(book_id,character_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS character_references_chapter ON character_references(book_id,chapter_id)")
            initialize_artifacts(conn)
            initialize_library(conn)
            initialize_schema(conn)
        for job in self.jobs(limit=None):
            if job["status"] in {"running", "queued"}:
                message = ("Server restarted. Analyze story again to resume from saved chapter analysis."
                           if job["kind"] in {"analyze", "pipeline"} else
                           "Server restarted. Generate again to resume from saved takes.")
                self.update_job(job["id"], status="interrupted", message=message)
        # Runtime acquires InstanceLock before constructing Store, so a second
        # server cannot mistake another worker's in-flight analysis for a restart.
        with self.lock, self.connect() as conn:
            for book_id, body in conn.execute("SELECT book_id,body FROM analysis_checkpoints").fetchall():
                checkpoint = json.loads(body)
                if checkpoint.get("status") not in {"running", "queued"}:
                    continue
                checkpoint.update(status="interrupted", updated_at=now(),
                                  error="Server restarted. Analyze story again to resume from saved chapter analysis.")
                for chapter in checkpoint.get("chapters", []):
                    if chapter.get("status") == "running":
                        chapter["status"] = "interrupted"
                conn.execute("UPDATE analysis_checkpoints SET body=? WHERE book_id=?",
                             (json.dumps(checkpoint, ensure_ascii=False), book_id))

    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def books(self, include_archived=False) -> list[dict]:
        with self.lock, self.connect() as conn:
            where = '' if include_archived else " WHERE NOT EXISTS(SELECT 1 FROM library_archives a WHERE a.kind='book' AND a.entity_id=books.id)"
            return [self._hydrate(json.loads(row[0]), conn) for row in conn.execute("SELECT body FROM books" + where + " ORDER BY rowid DESC")]

    def is_archived(self, book_id: str) -> bool:
        from .library import is_archived
        with self.lock, self.connect() as conn:
            return is_archived(conn, 'book', book_id)

    def require_active(self, book_id: str):
        self.book(book_id)
        if self.is_archived(book_id):
            raise ValueError('Restore this book from Removed items before processing or editing it.')

    def _hydrate(self, book, conn):
        takes = {row[0]: json.loads(row[1]) for row in conn.execute("SELECT segment_id,body FROM takes WHERE book_id=?", (book["id"],))}
        for segment in book["segments"]:
            segment["audio"] = takes.get(segment["id"])
        return book

    def book(self, book_id: str) -> dict:
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT body FROM books WHERE id=?", (book_id,)).fetchone()
            if not row:
                raise NotFound('book_not_found', "Book not found")
            return self._hydrate(json.loads(row[0]), conn)

    def save_book(self, book: dict) -> dict:
        with self.lock, self.connect() as conn:
            self._save_book(conn, book)
        return book

    def _save_book(self, conn, book: dict):
        from .artifacts import capture_book
        from .library import persist_cover

        previous = conn.execute("SELECT body FROM books WHERE id=?", (book['id'],)).fetchone()
        if previous:
            # Upgrade-time writes must retain the old projection before replacing it.
            capture_book(conn, self._hydrate(json.loads(previous[0]), conn), legacy_provenance=True, only_missing=True)
        persist_cover(conn, book)
        conn.execute("INSERT OR REPLACE INTO books(id,body) VALUES (?,?)", (book["id"], json.dumps(book, ensure_ascii=False)))
        conn.execute("DELETE FROM takes WHERE book_id=?", (book["id"],))
        conn.executemany("INSERT INTO takes(book_id,segment_id,body) VALUES (?,?,?)", [(book["id"], s["id"], json.dumps(s["audio"])) for s in book["segments"] if s.get("audio")])
        capture_book(conn, book)

    def analysis_checkpoint(self, book_id: str, fingerprint: str) -> dict | None:
        """Return saved work only when its input, provider and model still match."""
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT body FROM analysis_checkpoints WHERE book_id=? AND fingerprint=?",
                               (book_id, fingerprint)).fetchone()
            return json.loads(row[0]) if row else None

    def _save_analysis_checkpoint(self, conn, book_id: str, fingerprint: str, checkpoint: dict) -> dict:
        from .series import retain_observations

        checkpoint = dict(checkpoint, fingerprint=fingerprint, updated_at=now())
        conn.execute("INSERT OR REPLACE INTO analysis_checkpoints(book_id,fingerprint,body) VALUES (?,?,?)",
                     (book_id, fingerprint, json.dumps(checkpoint, ensure_ascii=False)))
        conn.execute("DELETE FROM character_references WHERE book_id=?", (book_id,))
        conn.executemany("""INSERT INTO character_references
            (book_id,id,character_id,chapter_id,segment_id,body) VALUES (?,?,?,?,?,?)""",
            [(book_id, ref["id"], ref["character_id"], ref["chapter_id"], ref.get("segment_id"),
              json.dumps(ref, ensure_ascii=False)) for ref in checkpoint.get("references", [])])
        retain_observations(conn, book_id, checkpoint.get("references", []))
        return checkpoint

    def save_analysis_checkpoint(self, book_id: str, fingerprint: str, checkpoint: dict) -> dict:
        """Save validated progress and its references without changing the book."""
        with self.lock, self.connect() as conn:
            return self._save_analysis_checkpoint(conn, book_id, fingerprint, checkpoint)

    def commit_analysis(self, book: dict, fingerprint: str, checkpoint: dict) -> dict:
        """Publish a book snapshot and its corresponding progress atomically."""
        with self.lock, self.connect() as conn:
            self._save_book(conn, book)
            self._save_analysis_checkpoint(conn, book["id"], fingerprint, checkpoint)
        return book

    def delete_analysis_checkpoint(self, book_id: str):
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM analysis_checkpoints WHERE book_id=?", (book_id,))
            conn.execute("DELETE FROM character_references WHERE book_id=?", (book_id,))

    def analysis_status(self, book_id: str) -> dict | None:
        """Progress suitable for the UI, without source text or model responses."""
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT body FROM analysis_checkpoints WHERE book_id=?", (book_id,)).fetchone()
        if not row:
            return None
        checkpoint = json.loads(row[0])
        fields = ("fingerprint", "provider", "model", "status", "completed_units", "total_units",
                  "stage", "phase", "scan_model", "current_chapter_id", "scope_chapter_id", "error", "updated_at")
        summary = {field: checkpoint[field] for field in fields if field in checkpoint}
        chapter_fields = ("id", "title", "stage", "status", "completed_units", "total_units", "error",
                          "discovery_complete", "directing_complete")
        summary["chapters"] = [{field: chapter[field] for field in chapter_fields if field in chapter}
                               for chapter in checkpoint.get("chapters", [])]
        return summary

    def character_references(self, book_id: str, character_id: str | None = None) -> list[dict]:
        with self.lock, self.connect() as conn:
            query = "SELECT body FROM character_references WHERE book_id=?"
            args = (book_id,)
            if character_id is not None:
                query += " AND character_id=?"
                args += (character_id,)
            query += " ORDER BY rowid"
            return [json.loads(row[0]) for row in conn.execute(query, args)]

    def save_take(self, book_id: str, segment_id: str, metadata: dict):
        from .artifacts import capture_book, capture_take, output_head

        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT body FROM books WHERE id=?", (book_id,)).fetchone()
            book = self._hydrate(json.loads(row[0]), conn) if row else {'id': book_id, 'segments': []}
            segment = next((s for s in book.get('segments', []) if s.get('id') == segment_id), {'id': segment_id})
            if row and not output_head(conn, book_id, 'structure', 'book'):
                capture_book(conn, book, legacy_provenance=True, only_missing=True)
            previous = conn.execute("SELECT body FROM takes WHERE book_id=? AND segment_id=?", (book_id, segment_id)).fetchone()
            if previous and not output_head(conn, book_id, 'audio_take', segment_id):
                capture_take(conn, book, segment, json.loads(previous[0]), legacy_provenance=True)
            conn.execute("INSERT OR REPLACE INTO takes(book_id,segment_id,body) VALUES (?,?,?)", (book_id, segment_id, json.dumps(metadata)))
            capture_take(conn, book, segment, metadata)

    def jobs(self, book_id: str | None = None, limit: int | None = 100, active: bool = False) -> list[dict]:
        clauses = (["book_id=?"] if book_id else []) + (["json_extract(body,'$.status') IN ('queued','running')"] if active else [])
        with self.lock, self.connect() as conn:
            sql = "SELECT body FROM jobs" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY rowid DESC" + (f" LIMIT {int(limit)}" if limit is not None else "")
            return [upgrade_job(json.loads(row[0])) for row in conn.execute(sql, (book_id,) if book_id else ())]

    def job(self, job_id: str) -> dict:
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise NotFound('job_not_found', "Job not found")
            return upgrade_job(json.loads(row[0]))

    def create_job(self, book_id: str, kind: str, total: int = 0) -> dict:
        job = dict(id=uuid4().hex, book_id=book_id, kind=kind, status="queued", progress=0,
                   total=total, message="Waiting for the local worker", error=None,
                   created_at=now(), updated_at=now(), cancel_requested=False)
        with self.lock, self.connect() as conn:
            conn.execute("INSERT INTO jobs(id,book_id,body) VALUES (?,?,?)", (job["id"], book_id, json.dumps(job)))
        return job

    def update_job(self, job_id: str, **fields) -> dict:
        with self.lock:
            job = self.job(job_id)
            if job["status"] not in ACTIVE_JOB_STATUSES:
                # A terminal outcome is final: a late worker, done-callback or shutdown race
                # must not turn `cancelled` into `interrupted` or rewrite what the job reported.
                fields = {name: value for name, value in fields.items() if name not in JOB_OUTCOME_FIELDS}
                if not fields:
                    return job
            job.update(fields, updated_at=now())
            with self.connect() as conn:
                conn.execute("UPDATE jobs SET body=? WHERE id=?", (json.dumps(job), job_id))
            return job

    def settings(self) -> dict:
        with self.lock, self.connect() as conn:
            row = conn.execute("SELECT body FROM settings WHERE id='preferences'").fetchone()
            return json.loads(row[0]) if row else {}

    def save_settings(self, preferences: dict):
        with self.lock, self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings(id,body) VALUES ('preferences',?)", (json.dumps(preferences),))


class InstanceLock:
    """Hold an OS lock before performing restart recovery on this data directory."""

    def __init__(self, root: Path):
        import os
        root.mkdir(parents=True, exist_ok=True)
        self.file = (root / "server.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Bardic is already using this data directory. Open the running app or stop it before starting another server.") from None

    def close(self):
        self.file.close()
