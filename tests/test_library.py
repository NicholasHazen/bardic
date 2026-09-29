"""Metadata, bounded EPUB covers, honest sizes and reversible library visibility."""
from copy import deepcopy
import base64
import io
import json
from pathlib import Path
import zipfile

from PIL import Image
import pytest

from bardic.artifacts import ArtifactRepository
from bardic.errors import Conflict
from bardic.importer import parse_book
from bardic.library import LibraryRepository
from bardic.series import SeriesRepository
from bardic.store import Store
from classic_fixtures import classic_references


def illustrated_epub(*, cover_path='images/cover.png', cover_type='image/png', raster=None, epub2=False, cover_page=False):
    if raster is None:
        output = io.BytesIO()
        Image.new('RGB', (900, 1400), '#416859').save(output, 'PNG')
        raster = output.getvalue()
    metadata = '<meta name="cover" content="art"/>' if epub2 else ''
    image_item = f'<item id="art" href="{cover_path}" media-type="{cover_type}" properties="{"" if epub2 or cover_page else "cover-image"}"/>'
    guide = '<guide><reference type="cover" href="cover.xhtml"/></guide>' if cover_page else ''
    files = {
        'META-INF/container.xml': '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
        'OPS/book.opf': '<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>The Lantern</dc:title><dc:creator>A. Writer</dc:creator>' + metadata + '</metadata><manifest><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>' + image_item + '</manifest><spine><itemref idref="one"/></spine>' + guide + '</package>',
        'OPS/one.xhtml': '<html><body><h1>Chapter 1</h1><p>Mara spoke softly.</p></body></html>',
        'OPS/cover.xhtml': f'<html><body><img src="{cover_path}"/></body></html>',
    }
    if not cover_path.startswith(('https:', '/', '../..')):
        files['OPS/' + cover_path] = raster
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path)
    book = parse_book('Book.txt', b'Chapter 1\n\nMara spoke softly.')
    store.save_book(book)
    return store, LibraryRepository(store), book


@pytest.mark.parametrize('epub2,cover_page', [(False, False), (True, False), (False, True)])
def test_cover_metadata_yields_small_reencoded_thumbnail_without_changing_source(tmp_path, epub2, cover_page):
    book = parse_book('Book.epub', illustrated_epub(epub2=epub2, cover_page=cover_page))
    chapters = deepcopy(book['chapters'])
    assert book['_cover_data']
    store = Store(tmp_path)
    store.save_book(book)
    assert '_cover_data' not in book and '_cover_data' not in store.book(book['id'])
    data, media_type, digest = LibraryRepository(store).cover(book['id'])
    assert media_type == 'image/jpeg' and len(data) < 256 * 1024
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == 'JPEG' and image.width <= 240 and image.height <= 360
        assert not image.getexif()
    assert book['cover']['sha256'] == digest
    assert store.book(book['id'])['chapters'] == chapters
    assert LibraryRepository(store).summary(book['id'])['cover']['url'].endswith(digest)


@pytest.mark.parametrize('kwargs', [
    {'cover_path': 'https://example.invalid/cover.png'},
    {'cover_path': '../../private.png'},
    {'cover_type': 'image/svg+xml', 'raster': b'<svg><script>alert(1)</script></svg>'},
    {'raster': b'Not an image'},
])
def test_optional_unsafe_or_invalid_cover_does_not_block_prose(kwargs):
    book = parse_book('Book.epub', illustrated_epub(**kwargs))
    assert '_cover_data' not in book
    assert 'Mara spoke softly.' in book['chapters'][0]['text']


def test_oversized_pixel_cover_is_rejected_before_decoding(monkeypatch):
    class TooLarge:
        format = 'PNG'
        width, height = 10000, 10000
        def __enter__(self): return self
        def __exit__(self, *args): pass
        @property
        def size(self): return self.width, self.height
    monkeypatch.setattr(Image, 'open', lambda *args, **kwargs: TooLarge())
    book = parse_book('Book.epub', illustrated_epub(raster=b'Pretend raster'))
    assert '_cover_data' not in book


def test_metadata_edit_and_original_refresh_preserve_all_source_and_reviewed_names(library):
    store, repository, book = library
    original = illustrated_epub()
    # This saved book's exact source may predate new import logic; metadata refresh
    # must not replace the source with a freshly parsed book.
    book['source_name'] = 'Book.epub'
    book['characters'].append({'id': 'mara', 'name': 'Mara', 'edited': True, 'description': 'Reviewed'})
    store.save_book(book)
    directory = store.root / 'originals' / book['id']
    directory.mkdir(parents=True)
    (directory / 'source.epub').write_bytes(original)
    prior = store.book(book['id'])
    repository.update_book(book['id'], 'My title', 'My author')
    result = repository.refresh_metadata(book['id'])
    updated = store.book(book['id'])
    assert result['title'] == 'My title' and result['author'] == 'My author' and result['cover']
    for field in ('id', 'chapters', 'segments', 'characters', 'scenes'):
        assert updated[field] == prior[field]


def test_book_removal_hides_only_visibility_and_restore_keeps_audio_and_history(library):
    store, repository, book = library
    segment = book['segments'][0]
    store.save_take(book['id'], segment['id'], {'fingerprint': 'old', 'duration': 3})
    directory = store.root / 'audio' / book['id']
    directory.mkdir(parents=True)
    audio = directory / 'old.wav'
    audio.write_bytes(b'retained audio')
    before = store.book(book['id'])
    count = ArtifactRepository(store).counts(book['id'])['total']
    repository.archive_book(book['id'])
    assert store.books() == [] and len(store.books(include_archived=True)) == 1
    assert store.book(book['id']) == before and audio.read_bytes() == b'retained audio'
    assert ArtifactRepository(store).counts(book['id'])['total'] >= count
    assert repository.snapshot()['books'] == []
    assert repository.snapshot(include_archived=True)['books'][0]['archived']
    with pytest.raises(Conflict) as refused:
        store.require_active(book['id'])
    assert refused.value.code == 'book_archived'
    repository.archive_book(book['id'], False)
    store.require_active(book['id'])
    assert store.books()[0] == before and audio.exists()


def test_series_removal_preserves_memberships_links_observations_and_book_visibility(library):
    store, repository, book = library
    book['characters'].append({'id': 'mara', 'name': 'Mara'})
    store.save_book(book)
    series = SeriesRepository(store)
    saga = series.create_series('Lanterns')
    identity = series.create_character(saga['id'], 'Mara')
    series.set_membership(book['id'], saga['id'], 9)
    series.link_character(book['id'], 'mara', identity['id'])
    links = series.links_for_book(book['id'])
    repository.archive_series(saga['id'])
    assert series.list_series() == [] and series.list_series(include_archived=True)[0]['archived']
    assert len(store.books()) == 1 and series.membership(book['id']) is None
    assert series.links_for_book(book['id']) == links
    with pytest.raises(Conflict) as refused:
        repository.require_active_series(saga['id'])
    assert refused.value.code == 'series_archived'
    assert series.context_for_book(book['id'])['characters'] == []
    repository.archive_series(saga['id'], False)
    assert series.membership(book['id'])['position'] == 9
    assert series.links_for_book(book['id']) == links


def test_archived_earlier_books_are_excluded_from_series_context_until_restored(library):
    store, repository, early = library
    later = deepcopy(early)
    later['id'] = 'later-book'
    for book in (early, later):
        book['characters'].append({'id': 'mara', 'name': 'Mara'})
        store.save_book(book)
    series = SeriesRepository(store)
    saga = series.create_series('Lanterns')
    identity = series.create_character(saga['id'], 'Mara')
    for book, position in ((early, 1), (later, 9)):
        series.set_membership(book['id'], saga['id'], position)
        series.link_character(book['id'], 'mara', identity['id'])
    chapter = early['chapters'][0]
    start = chapter['text'].index('Mara')
    classic_references(store, early['id'], [{'id': 'evidence', 'character_id': 'mara',
        'chapter_id': chapter['id'], 'start': start, 'end': start + 4, 'quote': 'Mara', 'kind': 'profile_evidence'}])
    assert series.context_for_book(later['id'])['included_observations'] == 1
    repository.archive_book(early['id'])
    assert series.context_for_book(later['id'])['included_observations'] == 0
    assert series.list_series()[0]['volumes'][0]['status'] == 'archived'
    repository.archive_book(early['id'], False)
    assert series.context_for_book(later['id'])['included_observations'] == 1


def test_missing_and_planned_slots_are_explicit_and_replaced_by_real_book(library):
    store, repository, book = library
    series = SeriesRepository(store)
    saga = series.create_series('Lanterns')
    repository.add_volume(saga['id'], 1, 'Missing first book', 'missing')
    repository.add_volume(saga['id'], 1.5, 'Upcoming novella', 'planned')
    assert [v['status'] for v in series.list_series()[0]['volumes']] == ['missing', 'planned']
    series.set_membership(book['id'], saga['id'], 1)
    volumes = series.list_series()[0]['volumes']
    assert len(volumes) == 2 and volumes[0]['book_id'] == book['id'] and volumes[0]['status'] == 'available'
    with pytest.raises(ValueError, match='already has this reading order'):
        repository.add_volume(saga['id'], 1, status='missing')
    repository.remove_volume(saga['id'], 1.5)
    assert len(series.list_series()[0]['volumes']) == 1


@pytest.mark.parametrize('position', [True, -1, float('inf'), '1', 1000001])
def test_invalid_placeholder_order_has_no_effect(library, position):
    store, repository, _ = library
    saga = SeriesRepository(store).create_series('Lanterns')
    with pytest.raises(ValueError):
        repository.add_volume(saga['id'], position)
    assert SeriesRepository(store).list_series()[0]['volumes'] == []


def test_storage_counts_files_and_payload_without_assigning_shared_database(library, tmp_path):
    store, repository, book = library
    for folder, size in (('originals', 17), ('audio', 31), ('listen-audio', 23), ('voice-previews', 13)):
        directory = store.root / folder / book['id']
        directory.mkdir(parents=True)
        (directory / 'file').write_bytes(b'x' * size)
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.write_bytes(b'not a book asset')
    (store.root / 'audio' / book['id'] / 'symlink').symlink_to(outside)
    try:
        summary = repository.summary(book['id'])
        assert summary['storage']['original_bytes'] == 17
        assert summary['storage']['audio_bytes'] == 31
        assert summary['storage']['simple_listen_bytes'] == 23
        assert summary['storage']['voice_preview_bytes'] == 13
        assert summary['storage']['file_bytes'] == 84
        assert summary['storage']['database_payload_bytes'] > 0
        assert 'database_bytes' not in summary['storage']
        shared = repository.snapshot()['storage']
        assert shared['shared_database_bytes'] >= shared['database_bytes'] > 0
    finally:
        outside.unlink()


def test_running_book_blocks_metadata_removal_and_series_removal(library):
    store, repository, book = library
    series = SeriesRepository(store)
    saga = series.create_series('Lanterns')
    series.set_membership(book['id'], saga['id'], 1)
    job = store.create_job(book['id'], 'analyze')
    for action in (lambda: repository.update_book(book['id'], 'Changed', ''),
                   lambda: repository.archive_book(book['id']), lambda: repository.archive_series(saga['id'])):
        with pytest.raises(Conflict) as refused:
            action()
        assert refused.value.code == 'job_active'
    assert not store.is_archived(book['id']) and not series.list_series()[0]['archived']
    store.update_job(job['id'], status='cancelled')
    repository.archive_book(book['id'])


def test_moving_from_removed_series_drops_obsolete_character_links(library):
    store, repository, book = library
    book['characters'].append({'id': 'mara', 'name': 'Mara'})
    store.save_book(book)
    series = SeriesRepository(store)
    first, second = series.create_series('First'), series.create_series('Second')
    identity = series.create_character(first['id'], 'Mara')
    series.set_membership(book['id'], first['id'], 1)
    series.link_character(book['id'], 'mara', identity['id'])
    repository.archive_series(first['id'])
    series.set_membership(book['id'], second['id'], 1)
    assert series.links_for_book(book['id']) == []
