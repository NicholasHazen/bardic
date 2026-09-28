"""Safe, deterministic import into immutable text and source-anchored passages."""
from __future__ import annotations

from datetime import datetime, timezone
from bisect import bisect_right
from pathlib import PurePosixPath
import base64
import io
import re
import uuid
import warnings
import zipfile
from urllib.parse import unquote, urlsplit

from defusedxml import ElementTree as ET


MAX_UPLOAD = 30 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
MAX_SEGMENT = 800
BREAK_LINE = re.compile(r"(?m)^[ \t]*(?:\*[ \t]*\*[ \t]*\*[* \t]*|---[- \t]*|[•⁂][•⁂ \t]*)[ \t]*$")
HEADING = re.compile(r"(?im)^(?:chapter\s+[^\n]{1,100}|part\s+[^\n]{1,100}|prologue|epilogue)\s*$")
BLOCKS = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "tr"}
SKIP_TAGS = {"script", "style", "head", "nav", "noscript"}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""


def _xml(data: bytes, label: str):
    try:
        return ET.fromstring(data)
    except Exception as exc:
        raise ValueError(f"Cannot read {label}: invalid or unsafe XML.") from exc


def _safe_member(name: str) -> str:
    if not name or "\\" in name or name.startswith("/") or "\x00" in name or ".." in PurePosixPath(name).parts:
        raise ValueError("The EPUB contains an unsafe file path.")
    return str(PurePosixPath(name))


def _resolve(base: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise ValueError("The EPUB references remote reading content.")
    path = unquote(parsed.path)
    if not path:
        return _safe_member(base)
    if path.startswith("/") or "\\" in path:
        raise ValueError("The EPUB contains an unsafe content path.")
    # Relative references may legitimately contain '..', but must stay in the archive.
    parts = list(PurePosixPath(base).parent.parts)
    for part in path.split("/"):
        if part == "..":
            if not parts:
                raise ValueError("The EPUB content path escapes its archive.")
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return _safe_member("/".join(parts))


def _xhtml_content(data: bytes, label: str) -> dict:
    root = _xml(data, label)
    body = next((e for e in root.iter() if _tag(e) == "body"), root)
    pieces: list[str] = []
    headings: list[dict] = []
    anchors: dict[str, int] = {}
    semantics: list[dict] = []
    position = 0

    def append(value):
        nonlocal position
        pieces.append(value)
        position += len(value)

    def walk(e):
        tag = _tag(e)
        if tag in SKIP_TAGS or e.get("hidden") is not None or e.get("aria-hidden") == "true":
            return
        if tag == "hr":
            append("\n\n***\n\n")
            return
        if tag == "br":
            append("\n")
            return
        if tag in BLOCKS:
            append("\n\n")
        if e.get("id"):
            anchors.setdefault(e.get("id"), position)
        types = e.get("{http://www.idpf.org/2007/ops}type", "").split()
        types += [role.removeprefix("doc-") for role in e.get("role", "").split() if role.startswith("doc-")]
        if types:
            semantics.append({"types": types, "start": position})
        if tag in {"h1", "h2", "h3"}:
            title = " ".join("".join(e.itertext()).split())
            if title:
                headings.append({"title": title, "start": position, "level": int(tag[1])})
        if e.text:
            append(e.text)
        for child in e:
            walk(child)
            if child.tail:
                append(child.tail)
        if tag in BLOCKS:
            append("\n\n")

    walk(body)
    # XML normalizes line endings. Keep exactly the previous whitespace rules,
    # while retaining a mapping from XML anchors to immutable extracted text.
    raw = "".join(pieces)
    runs, output = [], []
    output_length = 0
    matches = list(re.finditer(r"\S+|\s+", raw))
    for index, match in enumerate(matches):
        value = match.group()
        whitespace = value.isspace()
        if whitespace:
            newline_count = len(re.findall(r"\r\n?|\n", value))
            value = "\n" * min(2, newline_count) if newline_count else " "
            if index in {0, len(matches) - 1}:
                value = ""
        runs.append((match.start(), match.end(), output_length, value, whitespace))
        output.append(value)
        output_length += len(value)
    text = "".join(output)
    starts = [run[0] for run in runs]

    def offset(raw_offset):
        if not runs:
            return 0
        start, end, target, value, whitespace = runs[max(0, bisect_right(starts, raw_offset) - 1)]
        return target + (len(value) if whitespace else min(raw_offset - start, len(value)))

    return {"text": text, "heading": headings[0]["title"] if headings else "",
            "headings": [dict(h, start=offset(h["start"])) for h in headings],
            "anchors": {key: offset(value) for key, value in anchors.items()},
            "semantics": [dict(s, start=offset(s["start"])) for s in semantics]}


def _xhtml_text(data: bytes, label: str) -> tuple[str, str]:
    content = _xhtml_content(data, label)
    return content["text"], content["heading"]


def _cover_thumbnail(package, package_path, manifest, read):
    """Use explicit EPUB cover metadata; emit a small decoded raster only."""
    candidates = [item for item in manifest.values() if 'cover-image' in item.get('properties', '').split()]
    cover_ids = [e.get('content') for e in package.iter() if _tag(e) == 'meta' and e.get('name', '').lower() == 'cover']
    candidates += [manifest[key] for key in cover_ids if key in manifest]
    guide = [e.get('href') for e in package.iter() if _tag(e) == 'reference' and e.get('type') == 'cover']
    paths = []
    for item in candidates:
        if item.get('media-type') in {'image/jpeg', 'image/png', 'image/webp', 'application/xhtml+xml', 'text/html'}:
            paths.append((item.get('href', ''), item.get('media-type')))
    paths += [(href, 'application/xhtml+xml') for href in guide if href]
    for href, media_type in paths[:8]:
        try:
            path = _resolve(package_path, href)
            if media_type in {'application/xhtml+xml', 'text/html'}:
                root = _xml(read(path), 'EPUB cover page')
                image = next((e for e in root.iter() if _tag(e) in {'img', 'image'}), None)
                if image is None:
                    continue
                href = image.get('src') or image.get('{http://www.w3.org/1999/xlink}href') or image.get('href')
                if not href:
                    continue
                path = _resolve(path, href)
            data = read(path)
            if len(data) > 8 * 1024 * 1024:
                continue
            from PIL import Image, ImageOps
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 20_000_000 or max(image.size) > 12000:
                        continue
                    image = ImageOps.exif_transpose(image)
                    image.thumbnail((240, 360), Image.Resampling.LANCZOS)
                    if image.mode in {'RGBA', 'LA'} or 'transparency' in image.info:
                        rgba = image.convert('RGBA')
                        flattened = Image.new('RGB', rgba.size, 'white')
                        flattened.paste(rgba, mask=rgba.getchannel('A'))
                        image = flattened
                    else:
                        image = image.convert('RGB')
                    output = io.BytesIO()
                    image.save(output, 'JPEG', quality=82, optimize=True)
                    return base64.b64encode(output.getvalue()).decode('ascii')
        except Exception:
            # Optional, malformed/remote/unsupported artwork never blocks prose.
            continue
    return None


def _epub(data: bytes) -> tuple[str, str, list[dict], str | None]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("This file is not a readable EPUB archive.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 5000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise ValueError("The EPUB expands beyond the import limit (100 MB / 5,000 files).")
        names = set()
        for info in infos:
            name = _safe_member(info.filename)
            if name in names:
                raise ValueError("The EPUB contains duplicate file paths.")
            names.add(name)
            if info.flag_bits & 1:
                raise ValueError("Encrypted EPUB content is not supported. Supply a DRM-free EPUB or TXT.")
            if info.file_size > 10 * 1024 * 1024 and info.file_size > max(info.compress_size, 1) * 1000:
                raise ValueError("The EPUB has an unsafe compression ratio.")

        def read(name: str) -> bytes:
            try:
                return archive.read(name)
            except (KeyError, RuntimeError, zipfile.BadZipFile) as exc:
                raise ValueError(f"The EPUB is missing or cannot read {name}.") from exc

        if "META-INF/encryption.xml" in names:
            enc = _xml(read("META-INF/encryption.xml"), "EPUB encryption metadata")
            for element in enc.iter():
                if _tag(element) == "encryptionmethod" and element.get("Algorithm") not in {
                    "http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"
                }:
                    raise ValueError("Encrypted EPUB content is not supported. Supply a DRM-free EPUB or TXT.")
            # Only the two standard font-obfuscation methods are permitted.
            for element in enc.iter():
                if _tag(element) == "cipherreference":
                    uri = unquote(element.get("URI", "")).lower()
                    if not uri.endswith((".otf", ".ttf", ".woff", ".woff2")):
                        raise ValueError("Encrypted reading content is not supported; font obfuscation alone is allowed.")

        container = _xml(read("META-INF/container.xml"), "EPUB container")
        package_path = next((e.get("full-path") for e in container.iter() if _tag(e) == "rootfile"), None)
        if not package_path:
            raise ValueError("The EPUB has no package document.")
        package_path = _safe_member(package_path)
        package = _xml(read(package_path), "EPUB package")
        title = next(("".join(e.itertext()).strip() for e in package.iter() if _tag(e) == "title"), "")
        author = ", ".join("".join(e.itertext()).strip() for e in package.iter() if _tag(e) == "creator")
        manifest = {e.get("id"): e for e in package.iter() if _tag(e) == "item"}
        chapters = []
        for itemref in (e for e in package.iter() if _tag(e) == "itemref"):
            if itemref.get("linear", "yes") == "no":
                continue
            item = manifest.get(itemref.get("idref"))
            if item is None:
                raise ValueError("The EPUB spine references a missing manifest item.")
            if "nav" in item.get("properties", "").split():
                continue
            media_type = item.get("media-type", "")
            if media_type not in {"application/xhtml+xml", "text/html"}:
                raise ValueError(f"Unsupported EPUB reading content: {media_type or 'unknown media type'}.")
            path = _resolve(package_path, item.get("href", ""))
            content = _xhtml_content(read(path), path)
            if content["text"].strip():
                chapters.append(dict(content, source_href=path))
        if not chapters:
            raise ValueError("No readable text was found in the EPUB's reading order.")
        from .structure import describe_epub_structure
        return title, author, describe_epub_structure(package, package_path, manifest, read, chapters), _cover_thumbnail(package, package_path, manifest, read)


def _text_chapters(text: str) -> list[tuple[str, str]]:
    headings = list(HEADING.finditer(text))
    if not headings:
        return [("Section 1", text)]
    starts = [m.start() for m in headings]
    chapters = []
    if text[:starts[0]].strip():
        chapters.append(("Opening", text[:starts[0]]))
    elif starts[0] > 0:
        starts[0] = 0
    for index, match in enumerate(headings):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        chapters.append((match.group().strip(), text[starts[index]:end]))
    return chapters


def _split_long(text: str, start: int, end: int):
    while end - start > MAX_SEGMENT:
        limit = start + MAX_SEGMENT
        candidates = list(re.finditer(r"[.!?;:]\s+|\s+", text[start:limit]))
        suitable = [start + m.end() for m in candidates if m.end() > MAX_SEGMENT // 2]
        cut = suitable[-1] if suitable else limit
        yield start, cut
        start = cut
    if end > start:
        yield start, end


def _quoted_ranges(text: str, start: int, end: int):
    """Recognize quoted speech without treating contractions as closing quotes.

    In conventional multi-paragraph speech each paragraph opens with a quotation
    mark, while only the final paragraph closes it. An opening quote without a
    close therefore extends to the paragraph boundary, never into the next one.
    """
    closing = {"“": "”", "‘": "’", '"': '"', "'": "'", "«": "»", "‹": "›", "„": "“", "‚": "‘", "「": "」", "『": "』"}
    cursor = start
    while cursor < end:
        opener = text[cursor]
        if opener not in closing:
            cursor += 1
            continue
        if opener == "'" and (cursor > start and text[cursor - 1].isalnum() or cursor + 1 == end or text[cursor + 1].isspace()):
            cursor += 1
            continue
        stop = cursor + 1
        while stop < end:
            if text[stop] == closing[opener]:
                # Curly or straight apostrophes inside don't/I've/mother's are
                # part of the word, even in single-quoted British dialogue.
                apostrophe = text[stop] in {"'", "’"} and stop > cursor + 1 and stop + 1 < end and text[stop - 1].isalnum() and text[stop + 1].isalnum()
                if not apostrophe:
                    break
            stop += 1
        if stop == end and opener == "'":
            # An unmatched straight apostrophe may be an elision ('tis, 'em).
            # A paragraph-opening speech mark followed by capitalized prose is
            # the conservative exception for continued multi-paragraph speech.
            if cursor != start or not text[cursor + 1].isupper():
                cursor += 1
                continue
        quote_end = min(stop + 1, end)
        yield cursor, quote_end
        cursor = quote_end


def _passages(text: str, start: int, end: int):
    """Split paragraphs and quotations while preserving exact source ranges."""
    cursor = start
    for paragraph in re.finditer(r"\S[^\n]*(?:\n(?![ \t]*\n)[^\n]+)*", text[start:end]):
        pstart, pend = start + paragraph.start(), start + paragraph.end()
        if pstart > cursor and text[cursor:pstart].strip():
            for a, b in _split_long(text, cursor, pstart):
                yield a, b, "narration"
        cursor = pstart
        for quote_start, quote_end in _quoted_ranges(text, pstart, pend):
            if quote_start > cursor:
                for a, b in _split_long(text, cursor, quote_start):
                    if text[a:b].strip():
                        yield a, b, "narration"
            for a, b in _split_long(text, quote_start, quote_end):
                yield a, b, "dialogue"
            cursor = quote_end
        if cursor < pend:
            for a, b in _split_long(text, cursor, pend):
                if text[a:b].strip():
                    yield a, b, "narration"
        cursor = pend


def _base_characters() -> list[dict]:
    return [
        {"id": "narrator", "name": "Narrator", "aliases": [], "description": "The book's narrative voice.", "evidence": [], "voice": "Kore", "system_voice": "", "direction": "Clear, warm literary narration. Natural pacing; restrained expression."},
        {"id": "unassigned", "name": "Unassigned dialogue", "aliases": [], "description": "Dialogue whose speaker needs review.", "evidence": [], "voice": "Puck", "system_voice": "", "direction": "Natural, understated dialogue."},
    ]


def parse_book(filename: str, data: bytes) -> dict:
    if not data:
        raise ValueError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD:
        raise ValueError("Please use an ebook smaller than 30 MB.")
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    extension = PurePosixPath(safe_name).suffix.lower()
    fallback_title = PurePosixPath(safe_name).stem.replace("_", " ").strip() or "Untitled book"
    cover_data = None
    if extension == ".epub":
        title, author, chapter_texts, cover_data = _epub(data)
    elif extension == ".txt":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("TXT imports must be UTF-8 encoded. Save the text as UTF-8 and try again.") from exc
        if "\x00" in text:
            raise ValueError("This file contains binary data, not UTF-8 book text.")
        title, author = fallback_title, ""
        from .structure import describe_text_structure
        chapter_texts = describe_text_structure(_text_chapters(text.replace("\r\n", "\n").replace("\r", "\n")))
    else:
        raise ValueError("Supported formats are DRM-free .epub and UTF-8 .txt files.")
    book = {"id": str(uuid.uuid4()), "title": title or fallback_title, "author": author, "source_name": safe_name,
            "created_at": datetime.now(timezone.utc).isoformat(), "chapters": [], "characters": _base_characters(),
            "scenes": [], "segments": [], "analysis": {"provider": "local", "status": "draft", "notes": "Imported text. Analyze the book to draft cast and performance directions."}, "revision": 1, "structure_version": 2}
    for index, chapter_data in enumerate(chapter_texts):
        heading, raw_text = chapter_data["title"], chapter_data["text"]
        if not raw_text.strip():
            continue
        breaks = list(BREAK_LINE.finditer(raw_text))
        # Scene ornaments are structural metadata, not words to narrate. Preserve
        # their offsets as whitespace while keeping all prose verbatim.
        text = BREAK_LINE.sub(lambda m: " " * len(m.group()), raw_text)
        chapter = {**chapter_data, "id": _id("chapter"), "index": index, "text": text}
        book["chapters"].append(chapter)
        boundaries = [0, *[m.end() for m in breaks], len(text)]
        for a, b in zip(boundaries, boundaries[1:]):
            if not text[a:b].strip():
                continue
            scene = {"id": _id("scene"), "chapter_id": chapter["id"], "title": f"{heading} · Scene {sum(s['chapter_id'] == chapter['id'] for s in book['scenes']) + 1}", "summary": "", "tone": "Unreviewed", "direction": "", "segment_ids": [], "character_ids": []}
            book["scenes"].append(scene)
            for start, end, kind in _passages(text, a, b):
                segment = {"id": _id("segment"), "chapter_id": chapter["id"], "scene_id": scene["id"], "start": start, "end": end, "text": text[start:end], "kind": kind, "speaker_id": "unassigned" if kind == "dialogue" else "narrator", "confidence": 0.0 if kind == "dialogue" else 1.0, "direction": "", "cues": [], "audio": None}
                book["segments"].append(segment)
                scene["segment_ids"].append(segment["id"])
    if not book["segments"]:
        raise ValueError("No readable text was found in this book.")
    if cover_data:
        book['_cover_data'] = cover_data
    return book


def make_demo_book() -> dict:
    text = '''Chapter One — The Last Light

Mara found the lighthouse door open. That was wrong. For twenty years, her father had locked it at sunset, even when the sea lay flat as a sheet of silver.

“Elias?” Mara called. Her voice sounded small beneath the iron stairs.

“Up here,” Elias answered. His usual gravelly warmth had thinned to a whisper.

She climbed. Each step rang through the tower. At the top, her brother stood beside the dark lens, holding a blue envelope between two oil-stained fingers.

“It came this morning,” Elias said. “I thought you should open it.”

Mara laughed once, without amusement. “After all this time, he sends a letter?”

Outside, the first foghorn sounded. Elias looked toward the water, then back at her.

“Not a letter,” he said gently. “An invitation.”

She took the envelope. Her name was written in their father's careful hand. Beneath it, three words had been pressed so firmly that they marked the paper on the other side: Bring the light.

***

The harbor had emptied by the time they reached the pier. Mist curled around the mooring posts, hiding the water and leaving the boats suspended in pale air.

“This is a terrible idea,” Mara whispered.

“You said that when we stole his boat at twelve,” Elias replied.

“It was a terrible idea then, too.”

He grinned, and for a moment she saw the boy who had taught her to tie a bowline with her eyes closed. Then a bell rang somewhere beyond the breakwater. Once. Twice. A third note trembled and went silent.

Mara stopped smiling. Their father had always rung three times.

“Do you hear it?” Elias asked, barely breathing.

She nodded. Her hands shook as she lifted the old storm lantern. Its glass was cold, but a tiny amber flame had begun to bloom behind it.

“All right,” Mara said. The fear remained; something steadier stood beside it now. “Let's bring him home.”

Elias untied the rope. The boat slid into the fog, and the lantern laid a narrow golden road across the sea.
'''
    book = parse_book("The Last Light.txt", text.encode())
    book["title"] = "The Last Light"
    book["author"] = "An original Bardic demonstration"
    return book
