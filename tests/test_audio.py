import base64
import copy
import io
import math
import os
import shutil
import struct
import sys
import wave
from pathlib import Path

import httpx
import pytest

from spintails import audio


def wav_bytes(frames=2400, rate=24000, *, silence=False):
    output = io.BytesIO()
    samples = b"".join(struct.pack("<h", 0 if silence else int(8000 * math.sin(i * 0.06))) for i in range(frames))
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples)
    return output.getvalue()


@pytest.fixture
def recipe_inputs():
    return (
        {"id": "s1", "text": ' “Come back,” she said.\n', "direction": "quiet urgency", "cues": ["fear"]},
        {"id": "mara", "voice": "Kore", "system_voice": "Samantha", "direction": "measured phrasing"},
        {"id": "scene1", "tone": "uneasy", "direction": "Build tension slowly."},
    )


def test_voice_listing_keeps_full_names_and_deduplicates():
    result = audio._parse_system_voices(
        "Samantha (English (US)) en_US # Hello\n"
        "Bad News            en_US # Hello\n"
        "Samantha (English (US)) en_US # Hello again\n"
        "Amélie              fr_CA # Bonjour\n"
        "invalid noise\n"
    )
    assert result == [
        {"id": "Bad News", "name": "Bad News", "locale": "en-US"},
        {"id": "Samantha (English (US))", "name": "Samantha (English (US))", "locale": "en-US"},
        {"id": "Amélie", "name": "Amélie", "locale": "fr-CA"},
    ]


def test_installed_voice_alias_and_missing_voice(monkeypatch):
    monkeypatch.setattr(audio, "list_system_voices", lambda: [{"id": "Samantha (English (US))", "locale": "en-US"}])
    assert audio._system_voice("Samantha") == "Samantha (English (US))"
    with pytest.raises(audio.AudioError, match="not installed"):
        audio._system_voice("Invented Voice")


@pytest.mark.parametrize("owner,key,value", [
    (0, "text", "Come back!"), (0, "direction", "angry"),
    (0, "cues", ["laughing"]), (1, "voice", "Puck"),
    (1, "direction", "brisk phrasing"), (2, "tone", "celebratory"),
    (2, "direction", "A restrained scene."),
])
def test_fingerprint_changes_for_audible_inputs(recipe_inputs, owner, key, value):
    old = audio.render_fingerprint(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL)
    updated = copy.deepcopy(recipe_inputs)
    updated[owner][key] = value
    assert audio.render_fingerprint(*updated, "gemini", audio.DEFAULT_TTS_MODEL) != old


def test_fingerprint_ignores_ui_metadata_and_previous_take(recipe_inputs):
    old = audio.render_fingerprint(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL)
    recipe_inputs[0]["audio"] = {"duration": 9, "fingerprint": "old"}
    recipe_inputs[1]["evidence"] = ["She spoke quietly."]
    assert audio.render_fingerprint(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL) == old
    assert audio.render_fingerprint(*recipe_inputs, "gemini", "gemini-3.8-flash-lite-tts") != old


def test_repeated_passages_have_independent_take_fingerprints(recipe_inputs):
    old = audio.render_fingerprint(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL)
    recipe_inputs[0]["id"] = "s2"
    assert audio.render_fingerprint(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL) != old


def test_system_fingerprint_does_not_depend_on_voice_availability(monkeypatch, recipe_inputs):
    first = audio.render_fingerprint(*recipe_inputs, "system", audio.SYSTEM_MODEL)
    monkeypatch.setattr(audio, "list_system_voices", lambda: [])
    assert audio.render_fingerprint(*recipe_inputs, "system", audio.SYSTEM_MODEL) == first


def test_current_model_keeps_exact_transcript_separate_from_direction(recipe_inputs):
    recipe = audio._recipe(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL)
    body = audio._gemini_payload(recipe)
    item = body["input"][0]["content"][0]
    assert item["text"] == recipe_inputs[0]["text"]
    assert item["annotations"][0]["type"] == "speech_metadata"
    style = item["annotations"][0]["style"]
    for direction in ("quiet urgency", "measured phrasing", "fear", "uneasy", "Build tension slowly."):
        assert direction in style
    assert body["generation_config"]["speech_config"] == [{"voice": "Kore"}]
    assert body["response_format"] == {"type": "audio"}


def test_gemini_wav_synthesis_is_valid_and_atomic(monkeypatch, tmp_path, recipe_inputs):
    response_audio = wav_bytes()
    requests = []

    def post(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [
            {"type": "text", "text": "ignore"},
            {"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(response_audio).decode()},
        ]}]})

    monkeypatch.setattr(audio.httpx, "post", post)
    target = tmp_path / "take.wav"
    result = audio.synthesize(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL, "private-test-key", target)
    assert target.read_bytes() == response_audio
    assert result["duration"] == 0.1
    assert result["fingerprint"] == audio.render_fingerprint(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL)
    assert requests[0][0] == audio._GEMINI_URL
    assert requests[0][1]["headers"]["x-goog-api-key"] == "private-test-key"
    assert not list(tmp_path.glob(".take-*"))


def test_legacy_pcm_response_is_wrapped_correctly():
    pcm = b"\x00\x01\x00\x02" * 500
    result = audio._extract_gemini_audio({"steps": [{"type": "model_output", "content": [
        {"type": "audio", "mime_type": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}
    ]}]}, "gemini-3.1-flash-tts-preview")
    with wave.open(io.BytesIO(result), "rb") as reader:
        assert reader.getframerate() == 24000
        assert reader.readframes(1000) == pcm


def test_legacy_uses_legacy_prompt_and_rejects_custom_voice(recipe_inputs):
    recipe = audio._recipe(*recipe_inputs, "gemini", "gemini-3.1-flash-tts-preview")
    body = audio._gemini_payload(recipe)
    assert isinstance(body["input"], str)
    assert body["input"].endswith(recipe_inputs[0]["text"])
    recipe_inputs[1]["voice"] = "voice_custom"
    with pytest.raises(audio.AudioError, match="prebuilt voice"):
        audio._recipe(*recipe_inputs, "gemini", "gemini-3.1-flash-tts-preview")


@pytest.mark.parametrize("payload,match", [
    ({"steps": []}, "no audio"),
    ({"steps": None}, "invalid audio response"),
    ({"steps": [{"type": "model_output", "content": None}]}, "invalid audio response"),
    ({"output_audio": {"data": "!!!"}}, "base64"),
    ({"output_audio": {"data": base64.b64encode(b"bad bytes").decode(), "mime_type": "audio/wav"}}, "corrupt"),
    ({"output_audio": {"data": "AQ==", "mime_type": "audio/l16"}}, "incomplete PCM"),
])
def test_rejects_bad_provider_audio(payload, match):
    with pytest.raises(audio.AudioError, match=match):
        audio._extract_gemini_audio(payload, audio.DEFAULT_TTS_MODEL)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 503])
def test_provider_errors_never_echo_credentials(monkeypatch, tmp_path, recipe_inputs, status):
    monkeypatch.setattr(audio.httpx, "post", lambda *args, **kwargs: httpx.Response(status, json={"error": {"message": "private-test-key"}}))
    target = tmp_path / "existing.wav"
    target.write_bytes(b"existing take")
    with pytest.raises(audio.AudioError) as error:
        audio.synthesize(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL, "private-test-key", target)
    assert f"HTTP {status}" in str(error.value)
    assert "private-test-key" not in str(error.value)
    assert target.read_bytes() == b"existing take"
    assert not list(tmp_path.glob(".take-*"))


def test_bad_or_silent_take_does_not_replace_existing_audio(monkeypatch, tmp_path, recipe_inputs):
    target = tmp_path / "existing.wav"
    target.write_bytes(wav_bytes())
    previous = target.read_bytes()
    monkeypatch.setattr(audio, "_generate_gemini", lambda *args: wav_bytes(silence=True))
    with pytest.raises(audio.AudioError, match="silent"):
        audio.synthesize(*recipe_inputs, "gemini", audio.DEFAULT_TTS_MODEL, "test-key", target)
    assert target.read_bytes() == previous


def test_truncated_wave_is_rejected(tmp_path):
    target = tmp_path / "truncated.wav"
    target.write_bytes(wav_bytes()[:-100])
    with pytest.raises(audio.AudioError, match="truncated"):
        audio._wave_info(target)


def test_public_audio_validation_reads_samples_instead_of_trusting_header(tmp_path):
    target = tmp_path / "take.wav"
    target.write_bytes(wav_bytes(frames=3601))
    assert audio.validate_audio(target) == 3601 / 24000
    target.write_bytes(wav_bytes(frames=3601)[:-200])
    with pytest.raises(audio.AudioError, match="truncated"):
        audio.validate_audio(target)
    target.write_bytes(wav_bytes(silence=True))
    with pytest.raises(audio.AudioError, match="silent"):
        audio.validate_audio(target)


def test_assembly_preserves_samples_and_has_no_accumulating_timing_roundoff(tmp_path):
    clips = []
    total_frames = 0
    for i, frames in enumerate((2401, 3500, 5013)):
        path = tmp_path / f"{i}.wav"
        path.write_bytes(wav_bytes(frames=frames))
        clips.append(({"id": f"s{i}"}, path))
        total_frames += frames
    target = tmp_path / "chapter.wav"
    timings = audio.assemble_audio(clips, target)
    assert timings[0]["start"] == 0
    assert timings[0]["end"] == timings[1]["start"]
    assert timings[1]["end"] == timings[2]["start"]
    assert timings[-1]["end"] == total_frames / 24000
    with wave.open(str(target), "rb") as reader:
        assert reader.getnframes() == total_frames
        actual_samples = reader.readframes(total_frames)
    expected_samples = b""
    for _, path in clips:
        with wave.open(str(path), "rb") as reader:
            expected_samples += reader.readframes(reader.getnframes())
    assert actual_samples == expected_samples


def test_bad_assembly_preserves_existing_export(tmp_path):
    good = tmp_path / "good.wav"
    bad = tmp_path / "bad.wav"
    target = tmp_path / "chapter.wav"
    good.write_bytes(wav_bytes())
    bad.write_bytes(wav_bytes(rate=16000))
    target.write_bytes(b"previous export")
    with pytest.raises(audio.AudioError, match="24 kHz"):
        audio.assemble_audio([({"id": "good"}, good), ({"id": "bad"}, bad)], target)
    assert target.read_bytes() == b"previous export"


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("ffmpeg") or os.environ.get("SPINTAILS_TEST_SYSTEM_AUDIO") != "1",
    reason="Opt-in local integration: SPINTAILS_TEST_SYSTEM_AUDIO=1; requires macOS speech and ffmpeg",
)
def test_real_macos_narration_and_assembly(tmp_path):
    segment = {"id": "hello", "text": "The moon rose over the quiet harbor."}
    take = tmp_path / "take.wav"
    result = audio.synthesize(segment, {}, {}, "system", None, None, take)
    assert result["provider"] == "system"
    assert 0.5 < result["duration"] < 15
    assert result["voice"] in {voice["id"] for voice in audio.list_system_voices()}
    assert audio._wave_info(take, normalized=True)[3] > 0
    timings = audio.assemble_audio([(segment, take)], tmp_path / "chapter.wav")
    assert timings == [{"segment_id": "hello", "start": 0, "end": result["duration"]}]
