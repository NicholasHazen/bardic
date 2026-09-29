"""Helpers shared by the test modules: importing synthetic books and checking the book projection."""
from __future__ import annotations

import uuid
from typing import Any

from .client import Api, Reply
from .synthetic import SyntheticEpub, SyntheticTxt, txt_book


def token() -> str:
    """Short random text for names that must not collide with anything an earlier session left on the server."""
    return uuid.uuid4().hex[:10]



def import_book(api: Api, spec: SyntheticTxt | SyntheticEpub, *, filename: str | None = None,
                content_type: str = 'application/octet-stream') -> dict:
    """POST the synthetic file through ``importBook`` and return the book document."""
    reply = api.call('importBook', files={'file': (filename or spec.filename, spec.data, content_type)})
    return reply.json


def import_fresh_txt(api: Api, label: str = 'story') -> dict:
    """A new TXT book with a title no other book has (a book is not deduplicated)."""
    spec = txt_book(f'{label}_{token()}')
    return import_book(api, spec)


def chapter_by_id(book: dict, chapter_id: str) -> dict:
    return next(chapter for chapter in book['chapters'] if chapter['id'] == chapter_id)


def passages_of(book: dict, chapter_id: str) -> list[dict]:
    return [passage for passage in book['passages'] if passage['chapter_id'] == chapter_id]


def canonical_text(book: dict) -> list[tuple[str, str]]:
    """(chapter id, text) for every chapter, in order: the immutable source."""
    return [(chapter['id'], chapter['text']) for chapter in book['chapters']]


def passage_anchors(book: dict) -> list[tuple[str, str, int, int, str]]:
    """(id, chapter id, start, end, text) for every passage: what an edit must never move."""
    return [(s['id'], s['chapter_id'], s['start'], s['end'], s['text']) for s in book['passages']]


def assert_book_invariants(book: dict) -> None:
    """The reader projection's structural promises, checked from the response alone.

    * ``chapter.text[start:end] == passage.text`` counting Unicode code points (Python string indices);
    * ``leading_text + text`` of every passage plus ``trailing_text`` rebuilds the chapter text exactly;
    * passages are in reading order, do not overlap and lie inside their chapter;
    * scenes partition the passages of one chapter each;
    * every speaker is in the cast, narration is voiced by ``narrator``, confidence is within 0-1;
    * the cast has the reserved ``narrator`` and ``unassigned`` entries; IDs are unique.
    """
    for key in ('chapters', 'scenes', 'passages', 'characters'):
        ids = [item['id'] for item in book[key]]
        assert len(ids) == len(set(ids)), f'duplicate {key} ids'
    chapter_ids = {chapter['id'] for chapter in book['chapters']}
    cast = {character['id'] for character in book['characters']}
    assert {'narrator', 'unassigned'} <= cast, 'the cast always has narrator and unassigned'
    for chapter in book['chapters']:
        text = chapter['text']
        cursor = 0
        rebuilt = ''
        for passage in passages_of(book, chapter['id']):
            start, end = passage['start'], passage['end']
            assert cursor <= start < end <= len(text), (
                f'passage {passage["id"]} [{start}, {end}) is out of order or outside the chapter ({len(text)} code points)')
            assert text[start:end] == passage['text'], (
                f'passage {passage["id"]}: chapter.text[{start}:{end}] is not the passage text; '
                'offsets are zero-based Unicode code-point offsets with an exclusive end')
            assert passage['leading_text'] == text[cursor:start], f'passage {passage["id"]}: leading_text is not the gap before it'
            rebuilt += passage['leading_text'] + passage['text']
            cursor = end
        assert chapter['trailing_text'] == text[cursor:], f'chapter {chapter["id"]}: trailing_text is not the text after the last passage'
        assert rebuilt + chapter['trailing_text'] == text, f'chapter {chapter["id"]}: leading/text/trailing do not rebuild the chapter text'
    for passage in book['passages']:
        assert passage['chapter_id'] in chapter_ids
        assert passage['speaker_id'] in cast, f'passage {passage["id"]} is voiced by a character that is not in the cast'
        assert 0 <= passage['confidence'] <= 1
        if passage['kind'] == 'narration':
            assert passage['speaker_id'] == 'narrator', 'narration is voiced by the narrator'
    by_id = {passage['id']: passage for passage in book['passages']}
    seen: list[str] = []
    for scene in book['scenes']:
        assert scene['chapter_id'] in chapter_ids
        members = [by_id[passage_id] for passage_id in scene['passage_ids']]
        assert {member['chapter_id'] for member in members} <= {scene['chapter_id']}, 'a scene stays within one chapter'
        assert all(member['scene_id'] == scene['id'] for member in members)
        seen += scene['passage_ids']
    assert sorted(seen) == sorted(by_id), 'scenes partition the passages'
    assert seen == [passage['id'] for passage in book['passages']], 'scene passages follow reading order'


def library_summary(api: Api, book_id: str, *, include_archived: bool = False) -> dict:
    books = api.call('getLibrary', query={'include_archived': include_archived}).json['books']
    return next(book for book in books if book['id'] == book_id)


def find_job(api: Api, job_id: str, book_id: str | None = None) -> dict | None:
    query = {'book_id': book_id} if book_id else None
    return next((job for job in api.call('listJobs', query=query).json if job['id'] == job_id), None)


def assert_summary_matches_book(summary: dict, book: dict) -> None:
    """The library row and the book document describe the same book: counts, texts, cast."""
    texts = [chapter['text'] for chapter in book['chapters']]
    assert summary['title'] == book['title'] and summary['author'] == book['author']
    assert summary['source_name'] == book['source_name']
    assert summary['section_count'] == len(book['chapters'])
    kinds = [chapter.get('kind') for chapter in book['chapters']]
    narrative = sum(1 for kind in kinds if kind == 'chapter')
    assert summary['chapter_count'] == (narrative if any(kinds) else len(book['chapters']))
    assert summary['passage_count'] == len(book['passages'])
    assert summary['scene_count'] == len(book['scenes'])
    assert summary['character_count'] == len([c for c in book['characters'] if c['id'] not in ('narrator', 'unassigned')])
    assert summary['text_character_count'] == sum(len(text) for text in texts), 'counted in Unicode code points'
    assert summary['word_count'] == sum(len(text.split()) for text in texts), 'whitespace-separated tokens'


def only(reply: Reply, *keys: str) -> Any:
    """``reply.json`` restricted to ``keys`` (for readable assertion failures)."""
    body = reply.json
    return {key: body[key] for key in keys}


def new_series(api: Api, label: str = 'Saga') -> dict:
    """A new, empty series with a name no other series has."""
    name = f'{label} {token()}'
    return api.call('createSeries', json={'name': f'  {name}  '}).json


def book_in_series(api: Api, series: dict, position: float, label: str = 'volume') -> dict:
    """A new TXT book placed in ``series`` at ``position``."""
    book = import_fresh_txt(api, label)
    api.call('setBookSeries', path={'book_id': book['id']}, json={'series_id': series['id'], 'position': position})
    return book


def add_character(api: Api, book: dict, name: str) -> dict:
    return api.call('addCharacter', path={'book_id': book['id']}, json={'name': name}).json


def character_named(book: dict, name: str) -> dict:
    return next(c for c in book['characters'] if c['name'] == name)
