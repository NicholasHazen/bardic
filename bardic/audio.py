"""Narration providers and sample-accurate assembly of local WAV assets.

Provider calls are deliberately synchronous: the durable worker owns scheduling,
retry, cancellation between takes, and persistence. No network request happens
while listing capabilities or computing a render fingerprint.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx


DEFAULT_TTS_MODEL = "gemini-3.8-flash-tts"
TTS_MODELS = (
    DEFAULT_TTS_MODEL,
    "gemini-3.8-flash-lite-tts",
    "gemini-3.1-flash-tts-preview",
)
SYSTEM_MODEL = "macos-say"
SAMPLE_RATE = 24_000
MAX_AUDIO_BYTES = 64 * 1024 * 1024
_RECIPE_VERSION = 2
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
_VOICE_NAMES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)


class AudioError(ValueError):
    """A safe, user-facing rendering or audio validation error."""


class RateLimited(AudioError):
    """Gemini rejected a request with HTTP 429; nothing was generated.

    ``scope`` is ``day`` when the daily request quota is exhausted, otherwise
    ``minute`` or ``unknown``. Retrying a rejection is safe; retrying an
    uncertain request (timeout, connection loss) is not.
    """

    def __init__(self, message: str, scope: str, retry_after: float):
        super().__init__(message)
        self.scope = scope
        self.retry_after = retry_after


class UncertainRequest(AudioError):
    """The request may have been processed and billed; never resend it automatically."""


def _run(command: list[str], *, timeout: float, label: str) -> subprocess.CompletedProcess:
    """Never include input text, process stderr, or a credential in errors."""
    try:
        return subprocess.run(
            command, capture_output=True, check=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise AudioError(f"{label} timed out. Try a shorter passage.") from None
    except (subprocess.CalledProcessError, OSError):
        raise AudioError(f"{label} failed. Check that the required program and voice are installed.") from None


def _parse_system_voices(output: str) -> list[dict[str, str]]:
    voices: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        match = re.match(r"^(.+?)\s+([a-z]{2,3}_[A-Za-z0-9]+)\s+#", line)
        if match:
            name, locale = match.groups()
            name = name.strip()
            voices[name] = {"id": name, "name": name, "locale": locale.replace("_", "-")}
    return sorted(voices.values(), key=lambda item: (not item["locale"].startswith("en-"), item["name"]))


@lru_cache(maxsize=1)
def _installed_voice_snapshot() -> tuple[tuple[str, str], ...]:
    say = shutil.which("say")
    if not say:
        return ()
    try:
        result = _run([say, "-v", "?"], timeout=15, label="Voice discovery")
    except AudioError:
        return ()
    return tuple((voice["id"], voice["locale"]) for voice in _parse_system_voices(result.stdout.decode("utf-8", errors="replace")))


def list_system_voices() -> list[dict[str, str]]:
    """List installed macOS voices, never downloading additional voices."""
    return [{"id": name, "name": name, "locale": locale} for name, locale in _installed_voice_snapshot()]


def providers_status() -> dict[str, Any]:
    voices = list_system_voices()
    missing = [name for name in ("say", "ffmpeg") if not shutil.which(name)]
    reason = f"Missing {' and '.join(missing)}." if missing else ("No installed system voices found." if not voices else None)
    return {
        "system": {
            "available": reason is None,
            "reason": reason,
            "model": SYSTEM_MODEL,
            "voices": voices,
            "capabilities": {"offline": True, "performance_direction": False, "timing": "passage"},
        },
        "gemini": {
            "available": True,
            "requires_api_key": True,
            "default_model": DEFAULT_TTS_MODEL,
            "models": list(TTS_MODELS),
            "voices": [{"id": name, "name": name} for name in _VOICE_NAMES],
            "capabilities": {
                "offline": False, "performance_direction": True, "timing": "passage",
                "custom_voice_ids": True, "speakers_per_take": 1,
            },
        },
    }


def _provider(provider: str) -> str:
    if provider in ("system", "local"):
        return "system"
    if provider == "gemini":
        return provider
    raise AudioError("Unknown narration provider. Choose system or Gemini.")


def _system_voice(requested: str | None) -> str:
    voices = list_system_voices()
    names = [voice["id"] for voice in voices]
    if not names:
        raise AudioError("No system voices are available. macOS narration requires the say command.")
    if requested:
        if requested in names:
            return requested
        # macOS versions sometimes add a locale suffix to the same voice.
        aliases = [name for name in names if name.split(" (")[0] == requested]
        if len(aliases) == 1:
            return aliases[0]
        raise AudioError("The selected system voice is not installed. Choose a voice from the installed list.")
    for preferred in ("Samantha", "Daniel", "Karen", "Moira"):
        for name in names:
            if name.split(" (")[0] == preferred:
                return name
    return names[0]


def _cue_text(cues: Any) -> str:
    if isinstance(cues, str):
        return cues
    if isinstance(cues, list):
        parts = []
        for cue in cues:
            if isinstance(cue, str):
                parts.append(cue)
            elif isinstance(cue, dict):
                value = cue.get("direction") or cue.get("cue") or cue.get("text") or cue.get("type")
                if isinstance(value, str):
                    parts.append(value)
        return "; ".join(parts)
    return ""


def _performance_style(segment: dict, character: dict, scene: dict) -> str:
    # Speaker identity comes from the selected voice; descriptions and evidence
    # stay in the casting UI. Long persona prompts destabilize 3.8 voices.
    fields = (
        ("Character delivery", character.get("direction")),
        ("Scene mood", scene.get("tone")),
        ("Scene direction", scene.get("direction")),
        ("Passage delivery", segment.get("direction")),
        ("Performance cues", _cue_text(segment.get("cues"))),
    )
    directions = [f"{label}: {value.strip()}" for label, value in fields if isinstance(value, str) and value.strip()]
    if not directions:
        return ""
    return ". ".join(directions) + ". Convey direction through delivery; preserve every transcript word without adding speech."


def _recipe(segment: dict, character: dict, scene: dict, provider: str, model: str | None) -> dict:
    provider = _provider(provider)
    text = segment.get("text")
    if not isinstance(text, str) or not text.strip():
        raise AudioError("The passage has no text to narrate.")
    if provider == "system":
        # Hash the requested voice recipe without consulting the current host.
        # A previously rendered book must remain playable when say is absent.
        voice = character.get("system_voice") or "__system_default__"
        if not isinstance(voice, str) or len(voice) > 256:
            raise AudioError("The selected system voice is invalid.")
        model = SYSTEM_MODEL
    else:
        voice = character.get("voice") or "Kore"
        model = model or DEFAULT_TTS_MODEL
        if model not in TTS_MODELS:
            raise AudioError("Unsupported Gemini TTS model. Choose a model listed in Settings.")
        if not isinstance(voice, str) or not voice.strip() or len(voice) > 256:
            raise AudioError("The selected Gemini voice is invalid.")
        if model == "gemini-3.1-flash-tts-preview" and voice not in _VOICE_NAMES:
            raise AudioError("Gemini 3.1 requires a prebuilt voice. Use Gemini 3.8 for custom voice IDs.")
    return {
        "version": _RECIPE_VERSION,
        # Separate repeated source passages so regenerating a single take cannot
        # overwrite the audio (or invalidate timing) of another identical line.
        "segment_id": segment.get("id"),
        "provider": provider,
        "model": model,
        "voice": voice,
        "text": text,
        "style": _performance_style(segment, character, scene),
        "sample_rate": SAMPLE_RATE,
    }


def _fingerprint(recipe: dict) -> str:
    return hashlib.sha256(json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def render_fingerprint(segment: dict, character: dict, scene: dict, provider: str, model: str | None) -> str:
    """Hash rendering inputs and source-passage identity, excluding UI metadata."""
    return _fingerprint(_recipe(segment, character, scene, provider, model))


def _gemini_payload(recipe: dict) -> dict:
    if recipe["model"] == "gemini-3.1-flash-tts-preview":
        # The legacy model takes its direction in a natural-language prompt.
        transcript: Any = (
            "Read only the transcript below, verbatim. Do not read the directions or labels aloud.\n"
            f"Delivery instructions: {recipe['style'] or 'Natural audiobook narration.'}\n"
            f"TRANSCRIPT:\n{recipe['text']}"
        )
    else:
        item: dict[str, Any] = {"type": "text", "text": recipe["text"]}
        if recipe["style"]:
            item["annotations"] = [{"type": "speech_metadata", "style": recipe["style"]}]
        transcript = [{"type": "user_input", "content": [item]}]
    return {
        "model": recipe["model"],
        "input": transcript,
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": recipe["voice"]}]},
    }


def _wav_from_pcm(data: bytes, rate: int = SAMPLE_RATE) -> bytes:
    if not data or len(data) % 2:
        raise AudioError("The provider returned incomplete PCM audio.")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(data)
    return stream.getvalue()


def _extract_gemini_audio(response: dict, model: str) -> bytes:
    """Read the last unary audio block from the documented Interactions shape."""
    if not isinstance(response, dict):
        raise AudioError("Gemini returned an invalid audio response.")
    blocks = []
    steps = response.get("steps", [])
    if not isinstance(steps, list):
        raise AudioError("Gemini returned an invalid audio response.")
    for step in steps:
        if isinstance(step, dict) and step.get("type") == "model_output":
            contents = step.get("content", [])
            if not isinstance(contents, list):
                raise AudioError("Gemini returned an invalid audio response.")
            for content in contents:
                if isinstance(content, dict) and content.get("type") == "audio":
                    blocks.append(content)
    # SDK-shaped fixtures and future convenience responses can carry this too.
    if not blocks and isinstance(response.get("output_audio"), dict):
        blocks.append(response["output_audio"])
    if not blocks or not isinstance(blocks[-1].get("data"), str):
        raise AudioError("Gemini returned no audio. The request may be blocked, or the selected model or voice may be unavailable.")
    block = blocks[-1]
    encoded = block["data"]
    if len(encoded) > (MAX_AUDIO_BYTES * 4 // 3 + 4):
        raise AudioError("The provider returned too much audio for one passage.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise AudioError("Gemini returned invalid base64 audio.") from None
    mime = block.get("mime_type") or ""
    if not isinstance(mime, str):
        raise AudioError("Gemini returned an invalid audio MIME type.")
    mime = mime.lower()
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return data
    if mime.startswith(("audio/l16", "audio/pcm")) or (not mime and model == "gemini-3.1-flash-tts-preview"):
        match = re.search(r"rate=(\d+)", mime)
        rate = int(match.group(1)) if match else SAMPLE_RATE
        if rate not in (8000, 16000, 22050, 24000, 44100, 48000):
            raise AudioError("Gemini returned an unsupported audio sample rate.")
        return _wav_from_pcm(data, rate)
    raise AudioError("Gemini returned an unsupported or corrupt audio format.")


def _generate_gemini(recipe: dict, api_key: str | None, *, timeout: float | None = None, pace: bool = True) -> bytes:
    from .resources import publish_metrics, tts_usage
    from .tts_limits import LIMITER, classify_rate_limit, estimate_input_tokens
    publish_metrics(request_count=0, estimated_cost_usd=0., cost_basis='no_provider_request')
    if not api_key or not api_key.strip():
        raise AudioError("Add a Gemini API key in Settings before generating cloud narration.")
    if pace:
        # Callers that schedule their own sends (chapter chunks) reserve first.
        LIMITER.acquire(recipe["model"], estimate_input_tokens(recipe["text"]))
    publish_metrics(request_count=1, estimated_cost_usd=None, cost_basis='unknown')
    try:
        response = httpx.post(
            _GEMINI_URL,
            headers={"x-goog-api-key": api_key.strip(), "Content-Type": "application/json"},
            json=_gemini_payload(recipe),
            timeout=httpx.Timeout(max(240.0, timeout or 0.0), connect=20.0),
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        raise UncertainRequest("Gemini narration timed out. The request may still have been processed, so it was not resent.") from None
    except httpx.ConnectError:
        raise AudioError("Could not reach Gemini. Check the network connection and try again.") from None
    except httpx.RequestError:
        raise UncertainRequest("The connection to Gemini failed during the request. It may have been processed, so it was not resent.") from None
    publish_metrics(http_status=response.status_code)
    if response.status_code == 429:
        from .tts_limits import seconds_until_reset
        scope, retry_after = classify_rate_limit(response)
        LIMITER.cool_down(recipe["model"], retry_after)
        if scope == 'day':
            LIMITER.block_day(recipe["model"], seconds_until_reset())
        detail = ("The daily request quota for this model is used up; it resets at midnight Pacific time."
                  if scope == 'day' else "The project reached its quota or rate limit; retry later.")
        raise RateLimited(f"Gemini returned HTTP 429. {detail}", scope, retry_after)
    if response.status_code >= 400:
        hints = {
            400: "Check the model, voice, and passage length.",
            401: "Check your API key.",
            403: "Check the API key's access and project billing.",
            404: "The selected TTS model or voice is unavailable for this project.",
            429: "The project reached its quota or rate limit; retry later.",
        }
        hint = hints.get(response.status_code, "The provider could not complete this take; retry later.")
        # Remote error bodies can echo request data, so they are never persisted.
        raise AudioError(f"Gemini returned HTTP {response.status_code}. {hint}")
    if 300 <= response.status_code < 400:
        raise AudioError("Gemini returned an unexpected redirect.")
    try:
        body = response.json()
    except ValueError:
        raise AudioError("Gemini returned an unreadable response.") from None
    usage = tts_usage(body, recipe['model'])
    publish_metrics(**usage)
    class GeneratedAudio(bytes):
        """Preserve the bytes interface while passing measured usage to the take."""
    result = GeneratedAudio(_extract_gemini_audio(body, recipe["model"]))
    result.resource_usage = usage
    return result


def _wave_info(path: Path, *, normalized: bool = False) -> tuple[int, int, int, int]:
    try:
        with wave.open(str(path), "rb") as reader:
            channels, width, rate, frames = reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes()
            if channels not in (1, 2) or width not in (1, 2, 3, 4) or not 8000 <= rate <= 192000 or frames <= 0:
                raise AudioError("Audio has an invalid format or zero duration.")
            if normalized and (channels, width, rate) != (1, 2, SAMPLE_RATE):
                raise AudioError("Audio must be mono 24 kHz 16-bit PCM WAV.")
            expected = frames * channels * width
            total = 0
            audible = False
            while chunk := reader.readframes(32768):
                total += len(chunk)
                if not audible:
                    # Reject truly empty output; this is not speech recognition QA.
                    audible = any(value != (128 if width == 1 else 0) for value in chunk)
            if total != expected:
                raise AudioError("Audio data is truncated.")
            if not audible:
                raise AudioError("The provider returned silent audio. Retry this passage.")
            return channels, width, rate, frames
    except (wave.Error, EOFError, OSError, OverflowError):
        raise AudioError("Audio is not a readable PCM WAV file.") from None


def _normalize(source: Path, target: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("Audio conversion requires ffmpeg. Install it before generating narration.")
    _run([
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le", "-f", "wav", str(target),
    ], timeout=120, label="Audio conversion")


def validate_audio(path: Path) -> float:
    """Validate an existing normalized take and return its actual duration."""
    _, _, rate, frames = _wave_info(Path(path), normalized=True)
    return frames / rate


def synthesize(
    segment: dict, character: dict, scene: dict, provider: str,
    model: str | None, api_key: str | None, output_path: Path,
    *, timeout: float | None = None, pace: bool = True,
) -> dict:
    """Render a take and atomically publish validated, normalized WAV audio.

    ``timeout`` extends the provider read timeout for long multi-passage takes;
    ``pace=False`` is for callers that already reserved a rate-limit slot.
    """
    recipe = _recipe(segment, character, scene, provider, model)
    actual_voice = recipe["voice"]
    resource_usage = None
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".take-", dir=output_path.parent) as directory:
        temporary = Path(directory)
        normalized_path = temporary / "normalized.wav"
        if recipe["provider"] == "system":
            say = shutil.which("say")
            if not say:
                raise AudioError("System narration requires the macOS say command.")
            actual_voice = _system_voice(None if recipe["voice"] == "__system_default__" else recipe["voice"])
            source = temporary / "source.aiff"
            transcript = temporary / "transcript.txt"
            transcript.write_text(recipe["text"], encoding="utf-8")
            _run([say, "-v", actual_voice, "-o", str(source), "-f", str(transcript)], timeout=max(240, timeout or 0), label="System narration")
            _normalize(source, normalized_path)
        else:
            source = temporary / "source.wav"
            data = _generate_gemini(recipe, api_key, timeout=timeout, pace=pace)
            resource_usage = getattr(data, 'resource_usage', None)
            source.write_bytes(data)
            channels, width, rate, _ = _wave_info(source)
            if (channels, width, rate) == (1, 2, SAMPLE_RATE):
                shutil.copyfile(source, normalized_path)
            else:
                _normalize(source, normalized_path)
        _, _, rate, frames = _wave_info(normalized_path, normalized=True)
        duration = frames / rate
        os.replace(normalized_path, output_path)
    return {
        "fingerprint": _fingerprint(recipe), "duration": duration,
        "provider": recipe["provider"], "model": recipe["model"], "voice": actual_voice,
        **({'resource_usage': resource_usage} if resource_usage is not None else {}),
    }


def assemble_audio(clips: list[tuple[dict, Path]], output_path: Path) -> list[dict]:
    """Concatenate normalized takes with timing derived from exact sample counts."""
    if not clips:
        raise AudioError("There are no rendered passages to export.")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    timings = []
    cursor = 0
    with tempfile.TemporaryDirectory(prefix=".assembly-", dir=output_path.parent) as directory:
        target = Path(directory) / "chapter.wav"
        with wave.open(str(target), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE)
            for segment, path in clips:
                _, _, _, frames = _wave_info(Path(path), normalized=True)
                with wave.open(str(path), "rb") as reader:
                    while chunk := reader.readframes(32768):
                        writer.writeframesraw(chunk)
                timings.append({"segment_id": segment["id"], "start": cursor / SAMPLE_RATE, "end": (cursor + frames) / SAMPLE_RATE})
                cursor += frames
        os.replace(target, output_path)
    return timings
