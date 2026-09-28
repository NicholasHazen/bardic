"""Local library metadata, measured storage and reversible removal.

Removing an item changes visibility only. Original files, audio, book contents,
series links and immutable analysis history remain available for restoration.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
import math
import os
from pathlib import Path

from .store import now


def initialize_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS library_archives (
        kind TEXT NOT NULL CHECK(kind IN ('book','series')), entity_id TEXT NOT NULL,
        archived_at TEXT NOT NULL, PRIMARY KEY(kind,entity_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS book_covers (
        book_id TEXT PRIMARY KEY, media_type TEXT NOT NULL, width INTEGER NOT NULL,
        height INTEGER NOT NULL, sha256 TEXT NOT NULL, body BLOB NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS series_volume_slots (
        series_id TEXT NOT NULL, position REAL NOT NULL, title TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('missing','planned')), created_at TEXT NOT NULL,
        PRIMARY KEY(series_id,position))''')


def is_archived(conn, kind, identifier):
    return conn.execute('SELECT archived_at FROM library_archives WHERE kind=? AND entity_id=?',
                        (kind, identifier)).fetchone() is not None


def persist_cover(conn, book):
    """Move import-only thumbnail bytes out of book/checkpoint JSON."""
    encoded = book.pop('_cover_data', None)
    if encoded is None:
        return
    try:
        data = base64.b64decode(encoded, validate=True)
        from PIL import Image
        if len(data) > 256 * 1024:
            raise ValueError('Cover thumbnail is too large.')
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or not 0 < image.width <= 240 or not 0 < image.height <= 360:
                raise ValueError('Cover thumbnail must be a bounded JPEG image.')
            width, height = image.size
            image.verify()
    except Exception as exc:
        raise ValueError('The cover thumbnail could not be read safely.') from exc
    digest = hashlib.sha256(data).hexdigest()
    conn.execute('INSERT OR REPLACE INTO book_covers VALUES (?,?,?,?,?,?)',
                 (book['id'], 'image/jpeg', width, height, digest, data))
    book['cover'] = {'media_type': 'image/jpeg', 'width': width, 'height': height,
                     'sha256': digest, 'source': 'epub'}


def _position(value):
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1_000_000:
        raise ValueError('Set a finite reading order between 0 and 1,000,000; decimals allow side stories.')
    return float(value)


def _label(value, name, *, required=False, maximum=500):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in '\t\r\n' for c in value):
        raise ValueError(f'Choose a {name} of at most {maximum} characters.')
    value = ' '.join(value.split())
    if required and not value:
        raise ValueError(f'Enter a {name}.')
    return value


def _file_bytes(directory, *, boundary=None):
    """Count regular file lengths; never follow symlinks out of the data folder."""
    directory = Path(directory)
    boundary = Path(boundary or directory).resolve()
    if directory.is_symlink() or not directory.resolve().is_relative_to(boundary) or not directory.exists():
        return 0
    if directory.is_file():
        return directory.stat().st_size
    total = 0
    for root, folders, files in os.walk(directory, followlinks=False):
        folders[:] = [name for name in folders if not (Path(root) / name).is_symlink()]
        for name in files:
            path = Path(root) / name
            try:
                if not path.is_symlink() and path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
    return total


def _payload_bytes(conn, book_id):
    # Payload bytes are exact UTF-8/BLOB lengths, not a claim about shared pages,
    # indexes, freelists or SQLite compression/physical allocation.
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    total = conn.execute('SELECT COALESCE(length(CAST(body AS BLOB)),0) FROM books WHERE id=?', (book_id,)).fetchone()[0]
    for table, field in (('takes', 'body'), ('analysis_checkpoints', 'body'), ('character_references', 'body'),
                         ('character_observations', 'body'), ('analysis_units', 'body'), ('analysis_attempts', 'body'),
                         ('book_preprocessing', 'body'), ('pipeline_events', 'body'), ('artifact_versions', 'payload'),
                         ('book_covers', 'body'), ('resource_operations', 'body'), ('listening_sessions', 'body'),
                         ('listening_takes', 'body'), ('voice_preview_requests', 'body'), ('voice_preview_takes', 'body')):
        if table in tables:
            total += conn.execute(f'SELECT COALESCE(SUM(length(CAST({field} AS BLOB))),0) FROM {table} WHERE book_id=?',
                                  (book_id,)).fetchone()[0]
    return total


class LibraryRepository:
    def __init__(self, store):
        self.store = store

    def _idle(self, book_id):
        if any(j['status'] in {'queued', 'running'} for j in self.store.jobs(book_id, limit=None)):
            raise ValueError('Wait for this book’s processing to finish or cancel it before changing the library.')

    def require_active_series(self, series_id):
        with self.store.lock, self.store.connect() as conn:
            if not conn.execute('SELECT 1 FROM series WHERE id=?', (series_id,)).fetchone():
                raise KeyError('Series not found')
            if is_archived(conn, 'series', series_id):
                raise ValueError('Restore this series from Removed items before processing or editing it.')

    def summary(self, book_id):
        from .series import _membership
        with self.store.lock, self.store.connect() as conn:
            book = self.store.book(book_id)
            removed = conn.execute('SELECT archived_at FROM library_archives WHERE kind=? AND entity_id=?', ('book', book_id)).fetchone()
            cover = conn.execute('SELECT width,height,sha256 FROM book_covers WHERE book_id=?', (book_id,)).fetchone()
            membership = _membership(conn, book_id, include_archived=True)
            chapters, segments = book.get('chapters', []), book.get('segments', [])
            original = _file_bytes(self.store.root / 'originals' / book_id, boundary=self.store.root)
            audio = _file_bytes(self.store.root / 'audio' / book_id, boundary=self.store.root)
            listening = _file_bytes(self.store.root / 'listen-audio' / book_id, boundary=self.store.root)
            previews = _file_bytes(self.store.root / 'voice-previews' / book_id, boundary=self.store.root)
            result = {k: deepcopy(book.get(k)) for k in ('id', 'title', 'author', 'created_at', 'source_name', 'analysis')}
            result.update(archived=bool(removed), archived_at=removed[0] if removed else None,
                          section_count=len(chapters), chapter_count=sum(c.get('kind') == 'chapter' for c in chapters)
                          if any(c.get('kind') for c in chapters) else len(chapters),
                          word_count=sum(len(c.get('text', '').split()) for c in chapters),
                          character_count=max(0, len(book.get('characters', [])) - 2),
                          text_character_count=sum(len(c.get('text', '')) for c in chapters),
                          segment_count=len(segments), passage_count=len(segments), scene_count=len(book.get('scenes', [])),
                          audio_count=sum(bool(s.get('audio')) for s in segments), membership=membership,
                          cover={'width': cover[0], 'height': cover[1], 'sha256': cover[2],
                                 'url': f'/api/books/{book_id}/cover?v={cover[2]}'} if cover else None,
                          storage={'original_bytes': original, 'audio_bytes': audio,
                                   'simple_listen_bytes': listening, 'voice_preview_bytes': previews,
                                   'file_bytes': original + audio + listening + previews,
                                   'database_payload_bytes': _payload_bytes(conn, book_id),
                                   'note': 'File sizes are measured. Database payload bytes exclude shared pages, indexes and free space.'})
            return result

    def snapshot(self, include_archived=False):
        from .series import SeriesRepository
        with self.store.lock, self.store.connect() as conn:
            ids = [b['id'] for b in self.store.books(include_archived=include_archived)]
            database = _file_bytes(self.store.db, boundary=self.store.root)
            wal = _file_bytes(Path(str(self.store.db) + '-wal'), boundary=self.store.root)
            shm = _file_bytes(Path(str(self.store.db) + '-shm'), boundary=self.store.root)
            return {'books': [self.summary(identifier) for identifier in ids],
                    'series': SeriesRepository(self.store).list_series(include_archived=include_archived),
                    'storage': {'data_directory_bytes': _file_bytes(self.store.root), 'database_bytes': database,
                                'database_wal_bytes': wal, 'database_shm_bytes': shm,
                                'shared_database_bytes': database + wal + shm,
                                'backup_bytes': _file_bytes(self.store.root / 'backups', boundary=self.store.root),
                                'note': 'The SQLite database is shared by all books. Per-book payload sizes are not separate disk allocations. Removed items retain their files and data.'}}

    def update_book(self, book_id, title, author):
        title, author = _label(title, 'title', required=True), _label(author, 'author')
        with self.store.lock:
            self._idle(book_id)
            self.store.require_active(book_id)
            book = self.store.book(book_id)
            book.update(title=title, author=author, metadata_edited={'title': True, 'author': True}, revision=book.get('revision', 0) + 1)
            self.store.save_book(book)
        return self.summary(book_id)

    def refresh_metadata(self, book_id):
        from .importer import MAX_UPLOAD, parse_book
        with self.store.lock:
            self._idle(book_id)
            self.store.require_active(book_id)
            book = self.store.book(book_id)
            suffix = Path(book.get('source_name', '')).suffix.lower()
            path = self.store.root / 'originals' / book_id / ('source' + suffix)
            if suffix not in {'.epub', '.txt'} or path.is_symlink() or not path.resolve().is_relative_to(self.store.root) or not path.is_file():
                raise ValueError('The saved original EPUB or text file is unavailable.')
            if path.stat().st_size > MAX_UPLOAD:
                raise ValueError('The saved original exceeds the import size limit.')
            parsed = parse_book(book['source_name'], path.read_bytes())
            for key in ('title', 'author'):
                if not book.get('metadata_edited', {}).get(key):
                    book[key] = parsed[key]
            if parsed.get('_cover_data'):
                book['_cover_data'] = parsed['_cover_data']
            book['revision'] = book.get('revision', 0) + 1
            self.store.save_book(book)
        return self.summary(book_id)

    def cover(self, book_id):
        with self.store.lock, self.store.connect() as conn:
            if not conn.execute('SELECT 1 FROM books WHERE id=?', (book_id,)).fetchone():
                raise KeyError('Book not found')
            row = conn.execute('SELECT body,media_type,sha256 FROM book_covers WHERE book_id=?', (book_id,)).fetchone()
            if not row:
                raise KeyError('Cover not found')
            return bytes(row[0]), row[1], row[2]

    def _archive(self, kind, identifier, archived):
        from .artifacts import record
        if type(archived) is not bool:
            raise ValueError('Archived must be true or false.')
        with self.store.lock, self.store.connect() as conn:
            table = 'books' if kind == 'book' else 'series'
            if not conn.execute(f'SELECT 1 FROM {table} WHERE id=?', (identifier,)).fetchone():
                raise KeyError('Book not found' if kind == 'book' else 'Series not found')
            ids = [identifier] if kind == 'book' else [r[0] for r in conn.execute('SELECT book_id FROM series_books WHERE series_id=?', (identifier,))]
            for book_id in ids:
                self._idle(book_id)
            if archived:
                conn.execute('INSERT OR IGNORE INTO library_archives VALUES (?,?,?)', (kind, identifier, now()))
            else:
                conn.execute('DELETE FROM library_archives WHERE kind=? AND entity_id=?', (kind, identifier))
            for book_id in ids:
                record(conn, book_id, 'library_state', kind + ':' + identifier,
                       {'kind': kind, 'id': identifier, 'archived': archived}, label='Library visibility', stage='library')
        return {'id': identifier, 'archived': archived, 'retained': True}

    def archive_book(self, book_id, archived=True):
        return self._archive('book', book_id, archived)

    def archive_series(self, series_id, archived=True):
        return self._archive('series', series_id, archived)

    def rename_series(self, series_id, name):
        from .artifacts import capture_series
        name = _label(name, 'series name', required=True, maximum=200)
        with self.store.lock, self.store.connect() as conn:
            self.require_active_series(series_id)
            if not conn.execute('SELECT 1 FROM series WHERE id=?', (series_id,)).fetchone():
                raise KeyError('Series not found')
            if any(row[0].casefold() == name.casefold() for row in conn.execute('SELECT name FROM series WHERE id!=?', (series_id,))):
                raise ValueError('A series with that name already exists.')
            ids = [r[0] for r in conn.execute('SELECT book_id FROM series_books WHERE series_id=?', (series_id,))]
            for book_id in ids:
                self._idle(book_id)
                capture_series(conn, book_id, legacy_provenance=True, only_missing=True)
            conn.execute('UPDATE series SET name=? WHERE id=?', (name, series_id))
            for book_id in ids:
                capture_series(conn, book_id)
        return {'id': series_id, 'name': name}

    def add_volume(self, series_id, position, title='', status='missing'):
        position, title = _position(position), _label(title, 'volume title')
        if status not in {'missing', 'planned'}:
            raise ValueError('Choose missing or planned for a volume without an ebook.')
        with self.store.lock, self.store.connect() as conn:
            self.require_active_series(series_id)
            if conn.execute('SELECT 1 FROM series_books WHERE series_id=? AND position=?', (series_id, position)).fetchone():
                raise ValueError('A book already occupies that reading order, including removed books.')
            conn.execute('''INSERT INTO series_volume_slots VALUES (?,?,?,?,?) ON CONFLICT(series_id,position)
                DO UPDATE SET title=excluded.title,status=excluded.status''', (series_id, position, title, status, now()))
        return {'series_id': series_id, 'position': position, 'title': title, 'status': status, 'book_id': None}

    def remove_volume(self, series_id, position):
        position = _position(position)
        with self.store.lock, self.store.connect() as conn:
            self.require_active_series(series_id)
            conn.execute('DELETE FROM series_volume_slots WHERE series_id=? AND position=?', (series_id, position))
        return {'series_id': series_id, 'position': position, 'removed': True}
