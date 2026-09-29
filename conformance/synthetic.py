"""Synthetic, original books for the conformance suite.

Nothing here comes from a real book. The prose is invented for these tests and deliberately
contains characters that separate code-point offsets from UTF-8 bytes and UTF-16 units:
a non-BMP character (a crescent moon, U+1F319: 1 code point, 4 UTF-8 bytes, 2 UTF-16 units) and
accented Latin letters, plus quoted speech with speech tags and a scene-break ornament.
"""
from __future__ import annotations

import io
import struct
import zipfile
import zlib
from dataclasses import dataclass, field

MOON = '\U0001F319'

CHAPTER_ONE = (
    'Mira hung the lamp on the sea wall and waited for the tide. Behind her the harbour clock struck nine, '
    'and a gull walked along the rail as though it owned the whole grey morning.\n\n'
    '"The water is late," said Mira, and she pulled her scarf tighter.\n\n'
    f'Tomas came down the steps with a basket of pears. {MOON} He had walked all night from the orchard '
    'and his boots were white with salt. "It is never late," he answered, "only shy."\n\n'
    'They ate the pears one by one. The café on the corner opened its shutters, and the smell of '
    'roasted barley drifted across the water.\n\n'
    '***\n\n'
    'By noon the tide had come in and the lamp burned low. Mira counted the boats twice, then a third time, '
    'because the answer would not settle.\n\n'
    '"Eleven," she whispered. "There should be twelve."\n\n'
    '"Twelve was yesterday," said Tomas quietly.'
)

CHAPTER_TWO = (
    'The next morning the harbour was empty. Nobody spoke of the twelfth boat, and nobody looked at the '
    'far jetty where it had always been tied.\n\n'
    '"Someone should ask the ferryman," Mira said.\n\n'
    '"Someone should," agreed Tomas. "But not us." He kicked a loose pebble into the water and watched '
    'the rings spread.\n\n'
    'She did not argue. Instead she took the lamp down from the wall, trimmed the wick, and hung it '
    'again, a little higher, so that it could be seen from farther out.'
)

TXT_TITLE_STEM = 'the_lantern_road'


@dataclass(frozen=True)
class SyntheticTxt:
    filename: str
    data: bytes
    expected_title: str
    #: sentences that must survive into the canonical chapter text
    sentences: tuple[str, ...] = ()


def txt_book(stem: str = TXT_TITLE_STEM) -> SyntheticTxt:
    """A UTF-8 text book with two headed chapters. The title comes from the file name."""
    text = f'Chapter One\n\n{CHAPTER_ONE}\n\nChapter Two\n\n{CHAPTER_TWO}\n'
    return SyntheticTxt(
        filename=f'{stem}.txt', data=text.encode('utf-8'), expected_title=stem.replace('_', ' '),
        sentences=('the harbour clock struck nine', f'a basket of pears. {MOON} He had walked all night',
                   'roasted barley drifted across the water', 'Twelve was yesterday', 'trimmed the wick'))


# ---------------------------------------------------------------- EPUB

def png_bytes(width: int, height: int) -> bytes:
    """A valid RGB PNG with a two-tone pattern, built without an imaging library."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: none
        for x in range(width):
            band = (x // 16 + y // 16) % 2
            rows += bytes((200, 60, 40) if band else (30, 60, 160))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(bytes(rows), 9)) + chunk(b'IEND', b'')


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) from a JPEG's start-of-frame marker."""
    if data[:2] != b'\xff\xd8':
        raise ValueError('not a JPEG (missing SOI marker)')
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = struct.unpack('>H', data[index + 2:index + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack('>HH', data[index + 5:index + 9])
            return width, height
        index += 2 + length
    raise ValueError('no start-of-frame marker in the JPEG')


@dataclass(frozen=True)
class SyntheticEpub:
    filename: str
    data: bytes
    title: str
    authors: tuple[str, ...]
    language_in: str | None
    language_out: str | None
    chapter_titles: tuple[str, ...]
    cover_size: tuple[int, int] | None
    sentences: tuple[str, ...] = field(default=())


_XHTML = ('<?xml version="1.0" encoding="utf-8"?>\n'
          '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
          '<head><title>{title}</title></head><body><section epub:type="chapter">'
          '<h1>{title}</h1>{body}</section></body></html>')


def _paragraphs(text: str) -> str:
    from xml.sax.saxutils import escape
    return ''.join(f'<p>{escape(part)}</p>' for part in text.split('\n\n') if part.strip() and part.strip() != '***')


def epub_book(*, with_cover: bool = True, title: str = 'The Salt Orchard',
              authors: tuple[str, ...] = ('Ines Ward', 'Pell Osei'), language: str | None = 'en_GB',
              filename: str = 'salt-orchard.epub') -> SyntheticEpub:
    """A small valid EPUB 3 with a navigation document, two chapters and optionally a PNG cover."""
    chapter_titles = ('The Long Tide', 'The Empty Jetty')
    cover = png_bytes(320, 480) if with_cover else None
    creators = ''.join(f'<dc:creator id="c{i}">{name}</dc:creator>' for i, name in enumerate(authors))
    lang = f'<dc:language>{language}</dc:language>' if language else ''
    cover_meta = '<meta name="cover" content="cover-img"/>' if with_cover else ''
    cover_item = ('<item id="cover-img" href="cover.png" media-type="image/png" properties="cover-image"/>'
                  if with_cover else '')
    package = ('<?xml version="1.0" encoding="utf-8"?>\n'
               '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
               f'<dc:identifier id="bookid">urn:uuid:5c1f0d2e-8b74-4c0e-9f2e-0a4f3b9a7d11</dc:identifier>'
               f'<dc:title>{title}</dc:title>{creators}{lang}{cover_meta}'
               '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata>'
               '<manifest>'
               '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
               '<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
               '<item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
               f'{cover_item}</manifest>'
               '<spine><itemref idref="ch1"/><itemref idref="ch2"/></spine></package>')
    nav = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
           '<head><title>Contents</title></head><body><nav epub:type="toc"><ol>'
           f'<li><a href="ch1.xhtml">{chapter_titles[0]}</a></li>'
           f'<li><a href="ch2.xhtml">{chapter_titles[1]}</a></li></ol></nav></body></html>')
    container = ('<?xml version="1.0"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        archive.writestr('META-INF/container.xml', container, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr('OEBPS/content.opf', package, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr('OEBPS/nav.xhtml', nav, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr('OEBPS/ch1.xhtml', _XHTML.format(title=chapter_titles[0], body=_paragraphs(CHAPTER_ONE)),
                         compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr('OEBPS/ch2.xhtml', _XHTML.format(title=chapter_titles[1], body=_paragraphs(CHAPTER_TWO)),
                         compress_type=zipfile.ZIP_DEFLATED)
        if cover is not None:
            archive.writestr('OEBPS/cover.png', cover, compress_type=zipfile.ZIP_STORED)
    return SyntheticEpub(
        filename=filename, data=buffer.getvalue(), title=title, authors=authors, language_in=language,
        language_out=language.replace('_', '-') if language else None, chapter_titles=chapter_titles,
        cover_size=(320, 480) if with_cover else None,
        sentences=('the harbour clock struck nine', 'Twelve was yesterday', 'trimmed the wick'))
