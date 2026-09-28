"""Breeze TTS: a self-hosted narration server on the local network.

Breeze voices are mutable server records (a reference clip can be replaced
under the same id), so a voice choice is pinned locally as ``{id, revision,
seed}``. The revision hashes only speech-affecting server state. Fingerprints
read the pinned copy and never the network; the worker compares it with the
live voice immediately before each request and refuses to send on a mismatch.

Generation uses the streaming endpoint because a client disconnect stops the
server's GPU work there, so Stop and timeouts do not leave work running. The
collected PCM is validated and published like every other take; nothing is
played before that. Requests are free but not unlimited: one GPU renders at
about real time, and every request here runs at the server's interactive
priority.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .audio import BREEZE_MODEL, MAX_AUDIO_BYTES, SAMPLE_RATE, AudioError, _wav_from_pcm, voice_selection

ADAPTER_VERSION = 1
REVISION_SCHEMA = 1
DEFAULT_SEED = 42
MAX_SEED = 4_294_967_295
MAX_INPUT_CHARS = 10_000
MAX_INSTRUCTION_CHARS = 1_000
# Sent explicitly so a server default change cannot silently alter a recipe.
SEGMENTATION = {"mode": "auto", "max_chars": 300, "sentence_pause_ms": 120, "paragraph_pause_ms": 500}
SETTING_RANGES = {"temperature": (0.05, 2.0), "cfg_scale": (0.5, 10.0), "top_p": (0.01, 1.0), "top_k": (1, 1024)}
MAX_BUSY_RETRIES = 3
MAX_RETRY_AFTER = 90.0
# English and Chinese vocal-event markup is performed, not read aloud.
VOCAL_EVENTS = re.compile(r"\((?:laugh|sigh|cough|clears throat)\)|\[(?:笑|咳嗽|清嗓子|叹气)\]", re.IGNORECASE)
_VOICE_ID = re.compile(r"[a-z0-9_-]{1,64}")
_ERROR_CODE = re.compile(r"[a-z_]{1,40}")
_REVISION = re.compile(r"[a-f0-9]{64}")

# Test seams: an httpx transport and the wait function used between retries.
_transport: httpx.BaseTransport | None = None
_sleep = time.sleep


# Configuration -------------------------------------------------------------

def normalize_base_url(value: str) -> str:
    """Accept a plain http(s) server root; reject credentials, paths and queries."""
    if not isinstance(value, str):
        raise ValueError("Enter the Breeze server URL.")
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if len(value) > 500:
        raise ValueError("The Breeze server URL is too long.")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Use an http:// or https:// Breeze server URL, for example http://host.local:7860.")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("Enter only the Breeze server address and port, without a path, query or credentials.")
    try:
        parts.port
    except ValueError:
        raise ValueError("The Breeze server port is invalid.") from None
    return f"{parts.scheme}://{parts.netloc}"


def _config(config: Any) -> tuple[str, str]:
    base_url = (config or {}).get("base_url") if isinstance(config, dict) else None
    if not base_url:
        raise AudioError("Add the Breeze server URL in Settings before generating Breeze narration.")
    return base_url, ((config or {}).get("api_key") or "").strip()


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _client(timeout: httpx.Timeout) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=False, transport=_transport)


# Recipes -------------------------------------------------------------------

def instruction(segment: dict, character: dict, scene: dict) -> str:
    """Performance notes as Breeze delivery direction, without Gemini's prompt wording."""
    from .audio import _cue_text
    fields = (
        ("Character", character.get("direction")),
        ("Scene mood", scene.get("tone")),
        ("Scene", scene.get("direction")),
        ("Passage", segment.get("direction")),
        ("Cues", _cue_text(segment.get("cues"))),
    )
    parts = [f"{label}: {value.strip().rstrip('.')}" for label, value in fields if isinstance(value, str) and value.strip()]
    return ". ".join(parts) + "." if parts else ""


def _seed(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_SEED:
        raise AudioError("A Breeze seed must be a whole number from 0 to 4294967295.")
    return value


def normalize_settings(value: Any) -> dict:
    """Validate optional sampling overrides; unset fields keep the voice's own settings."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(SETTING_RANGES):
        raise AudioError("Breeze settings accept only temperature, cfg_scale, top_p and top_k.")
    result = {}
    for name, raw in value.items():
        if raw is None:
            continue
        low, high = SETTING_RANGES[name]
        valid = type(raw) is int if name == "top_k" else type(raw) in (int, float)
        if not valid or not low <= raw <= high:
            raise AudioError(f"Breeze {name} must be between {low} and {high}.")
        result[name] = raw if name == "top_k" else float(raw)
    return result


def recipe_fields(segment: dict, character: dict, scene: dict, model: str | None) -> dict:
    """Pure: every input comes from the passage and the locally pinned voice choice."""
    if model not in (None, "", BREEZE_MODEL):
        raise AudioError("Breeze narration uses the breeze-tts-2 model.")
    selection = voice_selection(character, "breeze") or {}
    voice = selection.get("id")
    if not isinstance(voice, str) or not _VOICE_ID.fullmatch(voice):
        raise AudioError("Choose a Breeze voice before generating Breeze narration.")
    revision = selection.get("revision")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise AudioError("Refresh Breeze voices in Settings, then choose this voice again.")
    seed = _seed(segment.get("seed"))
    if seed is None:
        seed = _seed(selection.get("seed"))
    style = instruction(segment, character, scene)
    if len(style) > MAX_INSTRUCTION_CHARS:
        raise AudioError("The performance notes for this passage are longer than Breeze accepts "
                         f"({MAX_INSTRUCTION_CHARS} characters). Shorten the character, scene or passage direction.")
    return {
        "model": BREEZE_MODEL, "voice": voice, "voice_revision": revision,
        "seed": DEFAULT_SEED if seed is None else seed,
        "settings": normalize_settings(selection.get("settings")),
        "segmentation": dict(SEGMENTATION), "style": style, "adapter_version": ADAPTER_VERSION,
    }


# Voices --------------------------------------------------------------------

def voice_revision(voice: dict, reference_sha256: str | None) -> str:
    """Hash only server state that changes speech; labels and descriptions do not."""
    reference = voice.get("reference") if isinstance(voice.get("reference"), dict) else {}
    material = {"schema": REVISION_SCHEMA, "id": voice.get("id"), "kind": voice.get("kind"),
                "created_at": voice.get("created_at"), "instruction": voice.get("instruction"),
                "settings": voice.get("settings") if isinstance(voice.get("settings"), dict) else {},
                "reference_text": reference.get("text"), "reference_sha256": reference_sha256}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _reference_sha256(client: httpx.Client, base_url: str, api_key: str, voice_id: str) -> str:
    response = client.get(f"{base_url}/v1/voices/{voice_id}/reference", headers=_headers(api_key))
    if response.status_code != 200:
        raise AudioError(f"The Breeze voice '{voice_id}' has no readable reference clip.")
    return hashlib.sha256(response.content).hexdigest()


def _voice_view(voice: dict, revision: str | None) -> dict:
    settings = voice.get("settings") if isinstance(voice.get("settings"), dict) else {}
    usable = voice.get("kind") == "cloned" and revision is not None
    reason = None
    if voice.get("kind") != "cloned":
        reason = "Designed voices change between requests. Save a preview as a cloned voice in Breeze to narrate with it."
    elif revision is None:
        reason = "Its reference clip could not be read."
    labels = voice.get("labels") if isinstance(voice.get("labels"), dict) else {}
    return {"id": voice["id"], "name": str(voice.get("name") or voice["id"])[:200],
            "kind": voice.get("kind"), "description": str(voice.get("description") or "")[:500],
            "labels": {str(k)[:60]: str(v)[:200] for k, v in list(labels.items())[:20]},
            "usable": usable, "reason": reason, "revision": revision,
            "seed": settings.get("seed") if type(settings.get("seed")) is int else None}


def fetch_catalog(config: Any, *, timeout: float = 10.0) -> dict:
    """Check the server and pin every voice's current revision. Free; no generation."""
    base_url, api_key = _config(config)
    try:
        with _client(httpx.Timeout(timeout, connect=5.0)) as client:
            health = client.get(f"{base_url}/health")
            health_body = health.json() if health.headers.get("content-type", "").startswith("application/json") else {}
            if health.status_code == 503:
                return {"state": "loading", "message": "The Breeze server is starting or its model is loading. Try again in a minute.",
                        "model": None, "voices": [], "default_voice_id": None}
            if health.status_code != 200:
                return {"state": "error", "message": f"The Breeze server returned HTTP {health.status_code} for its health check.",
                        "model": None, "voices": [], "default_voice_id": None}
            listing = client.get(f"{base_url}/v1/voices", headers=_headers(api_key))
            if listing.status_code == 401:
                return {"state": "error", "message": "The Breeze server rejected the API key.",
                        "model": health_body.get("model"), "voices": [], "default_voice_id": None}
            if listing.status_code != 200:
                return {"state": "error", "message": f"The Breeze server returned HTTP {listing.status_code} listing voices.",
                        "model": health_body.get("model"), "voices": [], "default_voice_id": None}
            body = listing.json()
            voices = []
            for voice in body.get("data", [])[:200] if isinstance(body.get("data"), list) else []:
                if not isinstance(voice, dict) or not isinstance(voice.get("id"), str) or not _VOICE_ID.fullmatch(voice["id"]):
                    continue
                revision = None
                if voice.get("kind") == "cloned":
                    try:
                        revision = voice_revision(voice, _reference_sha256(client, base_url, api_key, voice["id"]))
                    except AudioError:
                        revision = None
                voices.append(_voice_view(voice, revision))
    except httpx.HTTPError:
        return {"state": "unreachable", "message": f"Could not reach the Breeze server at {base_url}.",
                "model": None, "voices": [], "default_voice_id": None}
    except ValueError:
        return {"state": "error", "message": "The Breeze server returned an unreadable response.",
                "model": None, "voices": [], "default_voice_id": None}
    default = body.get("default_voice_id")
    model = health_body.get("model") if isinstance(health_body.get("model"), str) else None
    state = "ready" if model in (None, BREEZE_MODEL) else "error"
    # The voice count is shown next to this message from the voice list itself.
    message = ("Connected." if state == "ready" else
               f"The Breeze server runs {model[:60]}, but Bardic supports {BREEZE_MODEL}.")
    return {"state": state, "message": message, "model": model, "voices": voices,
            "default_voice_id": default if isinstance(default, str) and _VOICE_ID.fullmatch(default) else None}


def pin(catalog: dict, voice_id: str | None, *, seed: int | None = None, settings: Any = None) -> dict:
    """Build a pinned voice choice from the last checked catalog. Local only."""
    voices = {voice["id"]: voice for voice in (catalog or {}).get("voices", [])}
    voice_id = voice_id or (catalog or {}).get("default_voice_id")
    voice = voices.get(voice_id) if isinstance(voice_id, str) else None
    if voice is None:
        raise ValueError("That Breeze voice is not in the last voice check. Refresh Breeze voices in Settings.")
    if not voice["usable"]:
        raise ValueError(f"The Breeze voice '{voice_id}' cannot narrate. {voice['reason'] or ''}".strip())
    try:
        chosen = _seed(seed)
        extra = normalize_settings(settings)
    except AudioError as error:
        raise ValueError(str(error)) from None
    result = {"id": voice["id"], "revision": voice["revision"],
              "seed": chosen if chosen is not None else voice["seed"] if voice["seed"] is not None else DEFAULT_SEED}
    if extra:
        result["settings"] = extra
    return result


def _check_live_voice(client: httpx.Client, base_url: str, api_key: str, voice_id: str, expected: str) -> None:
    response = client.get(f"{base_url}/v1/voices/{voice_id}", headers=_headers(api_key))
    if response.status_code == 404:
        raise AudioError(f"The Breeze voice '{voice_id}' is no longer on the server. Choose another voice.")
    if response.status_code != 200:
        raise AudioError(f"The Breeze server returned HTTP {response.status_code} while checking the voice.")
    try:
        voice = response.json()
    except ValueError:
        raise AudioError("The Breeze server returned an unreadable voice record.") from None
    if not isinstance(voice, dict) or voice.get("kind") != "cloned":
        raise AudioError(f"The Breeze voice '{voice_id}' is not a cloned voice, so its sound is not stable.")
    if voice_revision(voice, _reference_sha256(client, base_url, api_key, voice_id)) != expected:
        raise AudioError(f"The Breeze voice '{voice_id}' changed on the server after it was chosen. "
                         "Refresh Breeze voices and choose it again; earlier audio is kept.")


# Generation ----------------------------------------------------------------

_ERROR_HINTS = {
    "voice_not_found": "The Breeze voice is not on the server. Refresh Breeze voices.",
    "input_too_long": f"This text is longer than one Breeze request allows ({MAX_INPUT_CHARS:,} characters).",
    "segment_too_long": "A sentence in this passage is too long for Breeze to render in one piece.",
    "empty_input": "The passage has no speakable text.",
    "invalid_api_key": "Check the Breeze API key in Settings.",
    "invalid_value": "Breeze rejected a request setting.",
    "server_busy": "The Breeze server queue is full. Try again shortly.",
    "model_loading": "The Breeze model is still loading. Try again in a minute.",
    "model_unavailable": "The Breeze model is unavailable. Check the server.",
}


def _error(response: httpx.Response) -> tuple[str, str]:
    # Remote messages can echo input text; keep only the fixed error code.
    code = ""
    try:
        body = json.loads(response.read() or b"{}")
        code = body.get("error", {}).get("code", "") if isinstance(body, dict) else ""
    except (ValueError, AttributeError, httpx.HTTPError):
        pass
    code = code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else ""
    hint = _ERROR_HINTS.get(code, "The server could not complete this take.")
    return code, f"Breeze returned HTTP {response.status_code}{f' ({code})' if code else ''}. {hint}"


def _retry_after(response: httpx.Response) -> float:
    try:
        return min(MAX_RETRY_AFTER, max(1.0, float(response.headers.get("retry-after", "5"))))
    except ValueError:
        return 5.0


def _wait(seconds: float, check_cancel) -> None:
    remaining = seconds
    while remaining > 0:
        check_cancel()
        step = min(1.0, remaining)
        _sleep(step)
        remaining -= step


def _timing(text: str, segments: list, duration_ms: int) -> dict | None:
    """Sentence timing, accepted only when every offset resolves to the exact sent text."""
    result, cursor = [], 0
    for item in segments:
        if not isinstance(item, dict):
            return None
        start, end = item.get("char_start"), item.get("char_end")
        begin_ms, end_ms = item.get("start_ms"), item.get("end_ms")
        if (any(type(value) is not int for value in (start, end, begin_ms, end_ms)) or
                not 0 <= start < end <= len(text) or text[start:end] != item.get("text") or
                not cursor <= begin_ms <= end_ms <= duration_ms + 50):
            return None
        cursor = end_ms
        result.append({"char_start": start, "char_end": end, "start": begin_ms / 1000, "end": end_ms / 1000})
    return {"schema_version": 1, "kind": "sentence", "source": "breeze",
            "offsets": "recipe_text_code_points", "segments": result} if result else None


def generate(recipe: dict, config: Any, *, timeout: float | None = None) -> bytes:
    """Stream one take, then return complete WAV bytes with timing and usage attached."""
    from .resources import publish_metrics
    from .tts_limits import CANCEL_CHECK
    check_cancel = CANCEL_CHECK.get() or (lambda: None)
    publish_metrics(request_count=0, estimated_cost_usd=0., cost_basis='no_provider_request')
    base_url, api_key = _config(config)
    text = recipe["text"]
    if len(text) > MAX_INPUT_CHARS:
        raise AudioError(_ERROR_HINTS["input_too_long"])
    payload: dict[str, Any] = {
        "model": recipe["model"], "input": text, "voice": recipe["voice"],
        "settings": {**recipe["settings"], "seed": recipe["seed"]},
        "segmentation": recipe["segmentation"], "speed": 1.0,
        "output_format": f"pcm_{SAMPLE_RATE}", "stream_format": "sse",
    }
    if recipe["style"]:
        payload["instruction"] = recipe["style"]
    # About one second of GPU per audio second, plus queue time; never unbounded.
    deadline = time.monotonic() + max(timeout or 0.0, 180.0 + 0.25 * len(text))
    try:
        with _client(httpx.Timeout(max(120.0, timeout or 0.0), connect=10.0)) as client:
            _check_live_voice(client, base_url, api_key, recipe["voice"], recipe["voice_revision"])
            for attempt in range(MAX_BUSY_RETRIES + 1):
                check_cancel()
                if attempt == 0:
                    publish_metrics(request_count=1, estimated_cost_usd=0., cost_basis='self_hosted', usage_source='breeze')
                with client.stream("POST", f"{base_url}/v1/speech/stream", json=payload,
                                   headers={**_headers(api_key), "Accept": "text/event-stream"}) as response:
                    publish_metrics(http_status=response.status_code)
                    if response.status_code == 503 and attempt < MAX_BUSY_RETRIES:
                        # Busy or loading: nothing was generated, so waiting and resending is safe.
                        delay = _retry_after(response)
                        response.read()
                        _wait(delay, check_cancel)
                        continue
                    if response.status_code != 200:
                        raise AudioError(_error(response)[1])
                    return _collect(response, recipe, deadline, check_cancel)
    except httpx.ConnectError:
        raise AudioError(f"Could not reach the Breeze server at {base_url}. Check that it is running.") from None
    except httpx.TimeoutException:
        raise AudioError("The Breeze server stopped responding during this take. The request was closed, "
                         "which stops its generation; try again.") from None
    except httpx.HTTPError:
        raise AudioError("The connection to the Breeze server failed during this take. Try again.") from None
    raise AudioError(_ERROR_HINTS["server_busy"])


def _collect(response: httpx.Response, recipe: dict, deadline: float, check_cancel) -> bytes:
    pcm, segments, done = bytearray(), [], None
    for line in response.iter_lines():
        check_cancel()
        if time.monotonic() > deadline:
            raise AudioError("The Breeze take took far longer than expected, so it was stopped.")
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            raise AudioError("The Breeze server sent an unreadable stream event.") from None
        kind = event.get("type") if isinstance(event, dict) else None
        if kind == "speech.audio.delta":
            try:
                pcm.extend(base64.b64decode(event.get("audio", ""), validate=True))
            except (binascii.Error, ValueError, TypeError):
                raise AudioError("The Breeze server sent invalid audio data.") from None
            if len(pcm) > MAX_AUDIO_BYTES:
                raise AudioError("The Breeze server returned too much audio for one take.")
        elif kind == "speech.segment":
            segments.append(event.get("segment"))
        elif kind == "speech.audio.done":
            done = event
            break
        elif kind == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            code = error.get("code") if isinstance(error.get("code"), str) and _ERROR_CODE.fullmatch(error["code"]) else ""
            raise AudioError(f"Breeze stopped generating this take{f' ({code})' if code else ''}. Try again.")
        # Unknown event types are ignored, as the server's compatibility policy asks.
    if done is None:
        raise AudioError("The Breeze stream ended before the take was complete.")
    if done.get("output_format") not in (None, f"pcm_{SAMPLE_RATE}"):
        raise AudioError("The Breeze server returned an unexpected audio format.")
    wav = _wav_from_pcm(bytes(pcm), SAMPLE_RATE)
    duration_ms = done.get("duration_ms") if type(done.get("duration_ms")) is int else len(pcm) // 2 * 1000 // SAMPLE_RATE

    class GeneratedAudio(bytes):
        """Keep the bytes interface while carrying measured extras to the take."""

    result = GeneratedAudio(wav)
    usage = done.get("usage") if isinstance(done.get("usage"), dict) else {}
    result.resource_usage = {"usage_source": "breeze", "cost_basis": "self_hosted", "estimated_cost_usd": 0.,
                             **({"characters": usage["characters"]} if type(usage.get("characters")) is int else {})}
    timing = _timing(recipe["text"], segments, duration_ms)
    events = sorted({match.group(0).lower() for match in VOCAL_EVENTS.finditer(recipe["text"])})
    result.take_metadata = {
        "provider_timing": timing,
        "breeze": {"request_id": str(response.headers.get("x-request-id", ""))[:80] or None,
                   "timing_accepted": timing is not None, **({"vocal_event_markup": events} if events else {})},
    }
    return result
