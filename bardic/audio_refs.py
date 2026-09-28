"""The one builder for audio objects in API responses (contract view ``AudioRef``).

Every response object that points at playable audio starts from
:func:`audio_ref`, so the core fields have the same names, presence and
meaning everywhere. Kind-specific fields are passed as keyword extras.
"""
from __future__ import annotations

from typing import Any


def audio_ref(url: str, *, asset_id: str | None, duration: float | None, provider: str | None,
              model: str | None, voice: str | None, created_at: str | None, **extra: Any) -> dict[str, Any]:
    core = {'url': url, 'asset_id': asset_id, 'duration': duration, 'provider': provider,
            'model': model, 'voice': voice, 'created_at': created_at}
    clash = sorted(set(core) & set(extra))
    if clash:
        raise ValueError(f'audio_ref extras repeat core fields: {clash}')
    return {**core, **extra}
