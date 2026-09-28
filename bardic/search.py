"""Rebuildable lexical retrieval over immutable passage text; no model calls."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from .errors import Invalid
from .processing import digest, source_hash


def _anchored_passages(book):
    chapters = {chapter['id']: chapter for chapter in book.get('chapters', [])}
    for segment in book.get('segments', []):
        chapter = chapters.get(segment.get('chapter_id'))
        start, end = segment.get('start'), segment.get('end')
        if (chapter and type(start) is int and type(end) is int
                and 0 <= start < end <= len(chapter['text'])
                and chapter['text'][start:end] == segment.get('text')):
            yield segment


def _schema(store, conn):
    """Create the index tables once per store (server process); False when SQLite has no FTS5."""
    ready = getattr(store, '_search_schema', None)
    if ready is None:
        conn.execute('CREATE TABLE IF NOT EXISTS search_books(book_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)')
        try:
            conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS passage_search USING fts5(book_id UNINDEXED, chapter_id UNINDEXED, passage_id UNINDEXED, text, tokenize="unicode61")')
            ready = True
        except sqlite3.OperationalError as exc:
            if 'no such module' not in str(exc).lower():
                raise
            ready = False
        store._search_schema = ready
    return ready


def ensure_index(store, book, conn):
    """Bring one book's rows of the index up to date.

    The index (``search_books`` and ``passage_search``) is a disposable derived
    cache of saved passage text: deleting it loses nothing, and a book's rows are
    rebuilt whenever its fingerprint is missing or stale.
    """
    if not _schema(store, conn):
        return False
    fingerprint = digest({'version': 2, 'source_hash': source_hash(book),
                          'passages': [(s['id'], s.get('chapter_id'), s.get('start'), s.get('end'), s['text'])
                                       for s in book.get('segments', [])]})
    row = conn.execute('SELECT fingerprint FROM search_books WHERE book_id=?', (book['id'],)).fetchone()
    if row and row[0] == fingerprint:
        return True
    conn.execute('DELETE FROM passage_search WHERE book_id=?', (book['id'],))
    conn.executemany('INSERT INTO passage_search(book_id,chapter_id,passage_id,text) VALUES (?,?,?,?)',
                     [(book['id'], s['chapter_id'], s['id'], s['text']) for s in _anchored_passages(book)])
    conn.execute('INSERT OR REPLACE INTO search_books VALUES (?,?)', (book['id'], fingerprint))
    return True


def search(store, book_id, query, *, scope='book', limit=20):
    if scope not in {'book', 'earlier'}:
        raise Invalid('search_scope_invalid', 'The search scope must be book or earlier.')
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        raise Invalid('search_query_invalid', 'The search text must have 1 to 300 characters and not be only whitespace.')
    words = re.findall(r'\w+', query, re.UNICODE)
    if not words:
        return {'items': [], 'available': True, 'note': 'Enter one or more words.'}
    match = ' AND '.join('"' + word.replace('"', '""') + '"' for word in words)
    limit = max(1, min(50, int(limit)))
    with store.lock, store.connect() as conn:
        book = store.book(book_id)
        books = {book_id: book}
        if scope == 'earlier':
            membership = conn.execute('SELECT series_id,position FROM series_books WHERE book_id=?', (book_id,)).fetchone()
            if membership and conn.execute("SELECT 1 FROM library_archives WHERE kind='series' AND entity_id=?", (membership[0],)).fetchone():
                membership = None
            if membership:
                for source_id, body in conn.execute('SELECT b.id,b.body FROM books b JOIN series_books sb ON b.id=sb.book_id WHERE sb.series_id=? AND sb.position<?', membership):
                    if not store.is_archived(source_id):
                        books[source_id] = json.loads(body)
        for source in books.values():
            if not ensure_index(store, source, conn):
                return {'items': [], 'available': False, 'note': 'This SQLite build has no FTS5 module. Source artifacts remain available.'}
        placeholders = ','.join('?' for _ in books)
        rows = conn.execute(f'SELECT book_id,chapter_id,passage_id,rank FROM passage_search WHERE passage_search MATCH ? AND book_id IN ({placeholders}) ORDER BY rank LIMIT ?',
                            (match, *books, limit)).fetchall()
        passages = {bid: {s['id']: s for s in b['segments']} for bid, b in books.items()}
        chapters = {bid: {c['id']: c for c in b['chapters']} for bid, b in books.items()}
        hashes = {(bid, cid): hashlib.sha256(chapter['text'].encode('utf-8')).hexdigest()
                  for bid, source in chapters.items() for cid, chapter in source.items()}
        result = []
        for bid, cid, sid, rank in rows:
            segment = passages[bid][sid]
            chapter = chapters[bid][cid]
            if chapter['text'][segment['start']:segment['end']] != segment['text']:
                continue
            result.append({'book_id': bid, 'book_title': books[bid]['title'], 'chapter_id': cid,
                           'chapter_title': chapter['title'], 'passage_id': sid, 'start': segment['start'],
                           'end': segment['end'], 'text': segment['text'], 'source_hash': hashes[bid, cid], 'rank': rank})
    return {'items': result, 'available': True, 'scope': scope,
            'note': 'Literal word search over saved passages. Lower rank means a stronger lexical match, not confidence or proof of character identity. Series scope includes this book and earlier volumes only.'}
