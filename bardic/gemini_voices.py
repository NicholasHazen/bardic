"""Gemini custom voices: list, design (paid), fetch a sample, delete.

Creating a prompted voice is a billed request that stores a ``voice_...`` id in
the Google project (200 per project, one-year TTL). Callers must obtain an
explicit confirmation before :func:`create_voice`, and a create that times out
is never resent: it may already have made (and charged for) a voice.
Listing, reading and deleting stored voices generate no audio.
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Any

import httpx

from .audio import AudioError, MAX_AUDIO_BYTES, UncertainRequest

VOICES_URL = "https://generativelanguage.googleapis.com/v1beta/voices"
DESIGN_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")
MAX_DESCRIPTION = 1000
MAX_PAGES = 5
_VOICE_ID = re.compile(r"voice_[A-Za-z0-9_-]{1,120}")
_LANGUAGE = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*")
GENDERS = ("female", "male", "neutral")

# Test seam: an httpx transport.
_transport: httpx.BaseTransport | None = None


def _field(item: dict, snake: str) -> Any:
    """REST responses may use snake_case (as documented) or lowerCamelCase."""
    if snake in item:
        return item[snake]
    head, *rest = snake.split("_")
    return item.get(head + "".join(part.title() for part in rest))


def _headers(api_key: str) -> dict[str, str]:
    if not api_key or not api_key.strip():
        raise AudioError("Add a Gemini API key in Settings before managing Gemini voices.")
    return {"x-goog-api-key": api_key.strip(), "Content-Type": "application/json"}


_HINTS = {400: "Check the voice description, language and model.", 401: "Check your API key.",
          403: "Check the API key's access and project billing.", 404: "That Gemini voice no longer exists.",
          429: "The project reached a quota or its 200 stored-voice limit; try later or delete unused voices."}


def _raise_for(response: httpx.Response, action: str) -> None:
    if response.status_code >= 300:
        # Remote error bodies can echo request data, so they are never shown.
        hint = _HINTS.get(response.status_code, "Try again later.")
        raise AudioError(f"Gemini returned HTTP {response.status_code} while {action}. {hint}")


def _request(method: str, url: str, api_key: str, *, timeout: float = 30.0, action: str,
             uncertain: str | None = None, **kwargs) -> httpx.Response:
    """``uncertain`` is the message for a request that may have been processed
    after it was sent (a billed create); such a request is never resent."""
    headers = _headers(api_key)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=20.0), follow_redirects=False,
                          transport=_transport) as client:
            response = client.request(method, url, headers=headers, **kwargs)
    except httpx.ConnectError:
        raise AudioError("Could not reach Gemini. Check the network connection and try again.") from None
    except httpx.HTTPError:
        if uncertain:
            raise UncertainRequest(uncertain) from None
        raise AudioError(f"The connection to Gemini failed while {action}. Try again.") from None
    _raise_for(response, action)
    return response


def _json(response: httpx.Response, action: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise AudioError(f"Gemini returned an unreadable response while {action}. Try again later.") from None


def _voice(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    voice_id = item.get("id") or item.get("name")
    if isinstance(voice_id, str) and voice_id.startswith("voices/"):
        voice_id = voice_id.split("/", 1)[1]
    if not isinstance(voice_id, str) or len(voice_id) > 128:
        return None
    text = lambda key, limit: str(_field(item, key) or "")[:limit]
    return {"id": voice_id, "display_name": text("display_name", 200) or voice_id, "type": text("type", 20),
            "description": text("description", 500), "language_code": text("language_code", 20),
            "gender": text("gender", 20), "create_time": text("create_time", 40) or None,
            "expire_time": text("expire_time", 40) or None}


def list_voices(api_key: str, *, types=("prompted", "replicated")) -> list[dict]:
    """The project's stored voices, newest first. Metadata only; generates no audio."""
    voices, token = [], None
    for _ in range(MAX_PAGES):
        params = [("type", kind) for kind in types] + [("page_size", "100")]
        if token:
            params.append(("page_token", token))
        body = _json(_request("GET", VOICES_URL, api_key, params=params, action="listing voices"), "listing voices")
        for item in body.get("voices", []) if isinstance(body, dict) else []:
            voice = _voice(item)
            if voice and (not types or voice["type"] in types):
                voices.append(voice)
        token = _field(body, "next_page_token") if isinstance(body, dict) else None
        if not token:
            break
    return voices


def _sample(item: dict) -> bytes | None:
    sample = _field(item, "sample_audio")
    data = sample.get("data") if isinstance(sample, dict) else None
    if not isinstance(data, str) or len(data) > MAX_AUDIO_BYTES * 4 // 3 + 4:
        return None
    try:
        audio = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        return None
    return audio if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE" else None


def validate_design(model: str, display_name: str, description: str, language_code: str, gender: str | None) -> None:
    if model not in DESIGN_MODELS:
        raise AudioError("Gemini voice design needs Gemini 3.8 Flash TTS or 3.8 Flash-Lite TTS.")
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100:
        raise AudioError("Name the voice in 1–100 characters.")
    if not isinstance(description, str) or not 3 <= len(description.strip()) <= MAX_DESCRIPTION:
        raise AudioError("Describe the voice in 3–1,000 characters. One or two sentences work best.")
    if not isinstance(language_code, str) or not _LANGUAGE.fullmatch(language_code):
        raise AudioError("Use a language tag such as en-US or en-GB.")
    if gender not in (None, "", *GENDERS):
        raise AudioError("Choose female, male or neutral, or leave the gender unset.")


def create_voice(api_key: str, *, model: str, display_name: str, description: str,
                 language_code: str = "en-US", gender: str | None = None) -> dict:
    """Create and store a prompted voice. BILLED; never retried automatically."""
    validate_design(model, display_name, description, language_code, gender)
    voice: dict[str, Any] = {"model": model, "type": "prompted", "display_name": display_name.strip(),
                             "language_code": language_code, "prompted": {"input": description.strip()}}
    if gender:
        voice["gender"] = gender
    response = _request("POST", VOICES_URL, api_key, json={"store": True, "voice": voice}, timeout=180.0,
                        action="creating a voice",
                        uncertain="The connection to Gemini failed while creating a voice. It may have been created and "
                                  "charged, so it was not resent. Refresh Gemini voices to check.")
    try:
        body = response.json()
    except ValueError:
        raise UncertainRequest("Gemini returned an unreadable response while creating a voice. It may have been created; "
                               "refresh Gemini voices to check.") from None
    created = _voice(body)
    if not created or not _VOICE_ID.fullmatch(created["id"]):
        raise UncertainRequest("Gemini did not return a usable voice id. Refresh Gemini voices to check whether one was created.")
    return {**created, "sample": _sample(body)}


def voice_sample(api_key: str, voice_id: str) -> bytes | None:
    if not _VOICE_ID.fullmatch(voice_id or ""):
        raise AudioError("That Gemini voice id is invalid.")
    body = _json(_request("GET", f"{VOICES_URL}/{voice_id}", api_key, action="reading a voice"), "reading a voice")
    return _sample(body) if isinstance(body, dict) else None


def delete_voice(api_key: str, voice_id: str) -> None:
    if not _VOICE_ID.fullmatch(voice_id or ""):
        raise AudioError("That Gemini voice id is invalid.")
    headers = _headers(api_key)
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=20.0), follow_redirects=False,
                          transport=_transport) as client:
            response = client.delete(f"{VOICES_URL}/{voice_id}", headers=headers)
    except httpx.HTTPError:
        raise AudioError("Could not reach Gemini to delete the voice. Try again.") from None
    if response.status_code != 404:  # Already gone is the outcome we wanted.
        _raise_for(response, "deleting a voice")
