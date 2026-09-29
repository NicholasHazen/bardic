"""Importing a TXT and an EPUB, and reading the book, its chapters, passages and cover back."""
from __future__ import annotations

import hashlib

import pytest

from . import helpers
from . import synthetic
from .synthetic import MOON, jpeg_size


# ---------------------------------------------------------------- TXT

def test_txt_import_gives_a_free_local_draft(txt_book, txt_spec):
    book = txt_book
    assert book['title'] == txt_spec.expected_title, 'a TXT title is the file name without extension, underscores as spaces'
    assert book['author'] == '', 'a TXT has no author'
    assert book['source_name'] == txt_spec.filename
    assert book['revision'] == 1, 'the revision starts at 1 on import'
    assert book['analysis']['provider'] == 'local' and book['analysis']['status'] == 'draft'
    assert book['language'] is None, 'a TXT has no language'
    assert not book.get('cover'), 'a TXT has no cover'
    assert len(book['chapters']) >= 2 and book['passages'] and book['scenes']
    assert 'segments' not in book and all('segment_ids' not in scene for scene in book['scenes']), 'the wire says passages'


def test_txt_projection_keeps_the_promises_of_the_book_document(txt_book):
    helpers.assert_book_invariants(txt_book)


def test_txt_text_survives_import_exactly(txt_book, txt_spec):
    """The canonical chapter text is the source text; only scene-break ornaments are blanked."""
    text = '\n'.join(chapter['text'] for chapter in txt_book['chapters'])
    for sentence in txt_spec.sentences:
        assert sentence in text, f'source sentence lost or changed: {sentence!r}'
    assert MOON in text, 'a non-BMP character must survive'
    assert '***' not in text, 'a scene-break ornament line is replaced by the same number of spaces'


def test_txt_offsets_count_code_points_not_bytes_or_utf16_units(txt_book):
    """Passages after the crescent moon (1 code point, 4 UTF-8 bytes, 2 UTF-16 units) still slice exactly."""
    chapter = next(c for c in txt_book['chapters'] if MOON in c['text'])
    moon = chapter['text'].index(MOON)
    after = [s for s in txt_book['passages'] if s['chapter_id'] == chapter['id'] and s['start'] > moon]
    assert len(after) >= 3, 'the test text has several passages after the crescent moon'
    for passage in after:
        assert chapter['text'][passage['start']:passage['end']] == passage['text']
    late = after[-1]
    utf16_start = len(chapter['text'][:late['start']].encode('utf-16-le')) // 2
    utf8_start = len(chapter['text'][:late['start']].encode('utf-8'))
    assert late['start'] != utf16_start and late['start'] != utf8_start, 'offsets are not UTF-16 or byte offsets'


def test_scene_break_ornament_splits_scenes(txt_book):
    first = txt_book['chapters'][0]
    scenes = [scene for scene in txt_book['scenes'] if scene['chapter_id'] == first['id']]
    assert len(scenes) >= 2, 'a scene-break ornament line starts a new scene'


@pytest.mark.xfail(strict=True, reason='Known Python defect (contract 0.4.0): scenes[].character_ids is empty after a TXT/EPUB import. Fix in Python or Rust; strict, so this fails once it passes and the marker must go.')
def test_scene_character_ids_list_the_speakers_of_their_passages(txt_book, epub_book):
    """`BookScene.character_ids`: "IDs of characters attributed to its passages (including narrator/unassigned)"."""
    for book in (txt_book, epub_book):
        by_id = {passage['id']: passage for passage in book['passages']}
        for scene in book['scenes']:
            speakers = {by_id[passage_id]['speaker_id'] for passage_id in scene['passage_ids']}
            assert speakers <= set(scene['character_ids']), (scene['id'], sorted(speakers), scene['character_ids'])


def test_dialogue_is_told_from_narration(txt_book):
    kinds = {passage['kind'] for passage in txt_book['passages']}
    assert kinds == {'narration', 'dialogue'}
    quoted = [s for s in txt_book['passages'] if s['kind'] == 'dialogue']
    assert any('The water is late' in s['text'] for s in quoted)


def test_import_is_not_idempotent_or_deduplicated(api, txt_book, txt_spec):
    again = helpers.import_book(api, txt_spec)
    assert again['id'] != txt_book['id']
    assert helpers.canonical_text(again)[0][1] == helpers.canonical_text(txt_book)[0][1]
    ids = {book['id'] for book in api.call('listBooks').json}
    assert {again['id'], txt_book['id']} <= ids


def test_get_book_returns_what_import_returned(api, txt_book):
    assert api.call('getBook', path={'book_id': txt_book['id']}).json == txt_book


def test_format_follows_the_extension_not_the_content_type(api, txt_spec):
    book = helpers.import_book(api, txt_spec, filename='SHOUTED_FILE.TXT', content_type='application/pdf')
    assert book['source_name'] == 'SHOUTED_FILE.TXT' and book['title'] == 'SHOUTED FILE'


def test_a_part_without_a_file_name_is_book_txt(api, txt_spec):
    """A file part whose `filename` is empty (what a browser sends for a nameless upload) is `book.txt`."""
    boundary = 'conformance-nameless-part'
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename=""\r\n'
            'Content-Type: text/plain\r\n\r\n').encode() + txt_spec.data + f'\r\n--{boundary}--\r\n'.encode()
    reply = api.call('importBook', content=body, headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
                     negative=True, expect=200)
    assert reply.json['source_name'] == 'book.txt' and reply.json['title'] == 'book'


def test_import_rejects_what_it_cannot_read_and_leaves_nothing_behind(api, txt_spec):
    before = {book['id'] for book in api.call('listBooks').json}
    bad_utf8 = api.call('importBook', files={'file': ('broken.txt', b'\xff\xfe\x00broken \x80 bytes', 'text/plain')}, expect=400)
    not_an_epub = api.call('importBook', files={'file': ('broken.epub', b'this is not a zip archive', 'application/epub+zip')},
                           expect=400)
    unsupported = api.call('importBook', files={'file': ('notes.pdf', b'%PDF-1.4 not a book', 'application/pdf')}, expect=400)
    empty = api.call('importBook', files={'file': ('empty.txt', b'', 'text/plain')}, expect=400)
    for reply in (bad_utf8, not_an_epub, unsupported, empty):
        assert reply.code in ('book_file_invalid', 'invalid_request'), reply.summary()
    assert {book['id'] for book in api.call('listBooks').json} == before, 'a failed import leaves no book'


def test_import_without_the_file_field_is_422(api):
    api.call('importBook', form={'note': 'no file here'}, negative=True, expect=422)


# ---------------------------------------------------------------- EPUB

def test_epub_import_reads_package_metadata(epub_book, epub_spec):
    book = epub_book
    assert book['title'] == epub_spec.title
    assert book['author'] == ', '.join(epub_spec.authors), 'authors are joined with a comma'
    assert book['language'] == epub_spec.language_out, 'a BCP 47 tag; underscores read as hyphens'
    assert book['source_name'] == epub_spec.filename
    assert book['analysis']['provider'] == 'local' and book['revision'] == 1


def test_epub_chapters_take_titles_from_navigation(epub_book, epub_spec):
    assert [chapter['title'] for chapter in epub_book['chapters']] == list(epub_spec.chapter_titles)
    for chapter in epub_book['chapters']:
        assert chapter['source_href'] and chapter['source_href'].endswith('.xhtml')


def test_epub_projection_keeps_the_promises_of_the_book_document(epub_book, epub_spec):
    helpers.assert_book_invariants(epub_book)
    text = '\n'.join(chapter['text'] for chapter in epub_book['chapters'])
    for sentence in epub_spec.sentences:
        assert sentence in text, f'source sentence lost or changed: {sentence!r}'


def test_epub_without_language_or_cover_has_neither(api):
    spec = synthetic.epub_book(with_cover=False, language=None, filename='plain.epub', authors=('Ines Ward',))
    book = helpers.import_book(api, spec)
    assert book['language'] is None and not book.get('cover') and book['author'] == 'Ines Ward'
    reply = api.call('getBookCover', path={'book_id': book['id']}, expect=404)
    assert reply.code == 'cover_not_found'


# ---------------------------------------------------------------- cover

def test_epub_cover_is_a_small_jpeg_thumbnail(api, epub_book, epub_spec):
    cover = epub_book['cover']
    assert cover['media_type'] == 'image/jpeg' and cover['source'] == 'epub'
    assert 0 < cover['width'] <= 240 and 0 < cover['height'] <= 360
    source_w, source_h = epub_spec.cover_size
    assert abs(cover['width'] / cover['height'] - source_w / source_h) < 0.02, 'the thumbnail keeps the aspect ratio'
    reply = api.call('getBookCover', path={'book_id': epub_book['id']})
    assert reply.content_type == 'image/jpeg'
    assert jpeg_size(reply.content) == (cover['width'], cover['height'])
    assert hashlib.sha256(reply.content).hexdigest() == cover['sha256']
    assert len(reply.content) <= 256 * 1024


def test_cover_carries_a_strong_etag_and_answers_conditional_requests(api, epub_book):
    sha = epub_book['cover']['sha256']
    path = {'book_id': epub_book['id']}
    first = api.call('getBookCover', path=path)
    assert first.headers['etag'] == f'"{sha}"', 'the ETag is the quoted SHA-256 of the bytes'
    for validator in (f'"{sha}"', f'W/"{sha}"', f'"other", "{sha}"', '*'):
        cached = api.call('getBookCover', path=path, headers={'If-None-Match': validator}, expect=304)
        assert cached.content == b'', validator
    fresh = api.call('getBookCover', path=path, headers={'If-None-Match': '"not-the-current-tag"'})
    assert fresh.content == first.content


def test_cover_caching_depends_on_the_content_addressed_url(api, epub_book):
    sha = epub_book['cover']['sha256']
    path = {'book_id': epub_book['id']}
    immutable = api.call('getBookCover', path=path, query={'v': sha}, negative=True)
    assert 'immutable' in immutable.headers['cache-control'] and 'max-age=31536000' in immutable.headers['cache-control']
    other = api.call('getBookCover', path=path, query={'v': 'stale'}, negative=True)
    assert 'no-cache' in other.headers['cache-control'] and 'immutable' not in other.headers['cache-control']
    plain = api.call('getBookCover', path=path)
    assert 'no-cache' in plain.headers['cache-control']


def test_cover_of_an_unknown_book_is_404(api):
    assert api.call('getBookCover', path={'book_id': 'no-such-book'}, expect=404).code == 'book_not_found'


def test_txt_book_has_no_cover(api, txt_book):
    assert api.call('getBookCover', path={'book_id': txt_book['id']}, expect=404).code == 'cover_not_found'


# ---------------------------------------------------------------- reading

def test_unknown_book_is_404(api):
    reply = api.call('getBook', path={'book_id': 'no-such-book'}, expect=404)
    assert reply.code == 'book_not_found' and isinstance(reply.json['detail'], str)


def test_odd_ids_name_no_book_and_do_not_escape_their_route(api):
    """IDs are opaque, URL-encoded path segments. An ID full of separators names nothing: 404, never another route."""
    for odd in ('a b', 'a/b', '%2e%2e', 'x?y=1', 'x#y', 'é\U0001F319'):
        reply = api.call('getBook', path={'book_id': odd}, expect=404)
        assert reply.code in ('book_not_found', 'route_not_found'), (odd, reply.summary())


def test_library_row_and_book_document_agree(api, txt_book, epub_book):
    for book in (txt_book, epub_book):
        helpers.assert_summary_matches_book(helpers.library_summary(api, book['id']), book)


def test_epub_library_row_reports_the_cover_with_a_url(api, epub_book):
    summary = helpers.library_summary(api, epub_book['id'])
    assert summary['cover']['sha256'] == epub_book['cover']['sha256']
    assert summary['cover']['url'].startswith('/api/books/') and epub_book['cover']['sha256'] in summary['cover']['url']
    reply = api.request('GET', summary['cover']['url'].split('?')[0], params={'v': epub_book['cover']['sha256']})
    assert reply.status == 200 and reply.content_type == 'image/jpeg'


def test_passage_ids_and_anchors_are_stable_across_reads(api, txt_book, epub_book):
    for book in (txt_book, epub_book):
        again = api.call('getBook', path={'book_id': book['id']}).json
        assert helpers.passage_anchors(again) == helpers.passage_anchors(book)
        assert helpers.canonical_text(again) == helpers.canonical_text(book)
