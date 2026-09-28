"""Publish immutable audio assets separately from their render recipes.

The caller decides whether a recipe needs another take. A repeated recipe may
produce different audio, so only the final WAV bytes identify an audio asset.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Callable

from .audio import AudioError, render_fingerprint, synthesize, validate_audio


def _content_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def produce_take(
    segment: dict, character: dict, scene: dict, provider: str,
    model: str | None, api_key: str | None, audio_dir: Path,
    *, synthesizer: Callable | None = None,
) -> dict:
    """Render and return metadata for a validated, immutable WAV asset.

    This deliberately has no cache/force policy or database writes. Even on a
    failed retry, existing assets and legacy recipe-named WAVs stay untouched.
    ``synthesizer`` is injectable so callers can retain their provider boundary.
    """
    audio_dir = Path(audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    expected = render_fingerprint(segment, character, scene, provider, model)
    render = synthesizer or synthesize
    with tempfile.TemporaryDirectory(prefix=".archive-take-", dir=audio_dir) as directory:
        temporary = Path(directory) / "take.wav"
        metadata = render(segment, character, scene, provider, model, api_key, temporary)
        if not isinstance(metadata, dict) or metadata.get("fingerprint") != expected:
            raise AudioError("The generated take does not match its render recipe.")
        duration = validate_audio(temporary)
        asset_id = _content_hash(temporary)
        target = audio_dir / f"{asset_id}.wav"
        # The temporary lives on the same filesystem. link() atomically creates
        # a complete file and fails if the destination exists; replace() would
        # discard a previous take. Cleanup removes only the temporary link.
        with temporary.open("rb") as source:
            os.fsync(source.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if _content_hash(target) != asset_id:
                raise AudioError("An archived audio asset failed its content integrity check.") from None
        return {**metadata, "duration": duration, "asset_id": asset_id}
