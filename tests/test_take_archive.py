import hashlib
import io
import wave

import pytest

from bardic.audio import AudioError, DEFAULT_TTS_MODEL, render_fingerprint
from bardic.take_archive import produce_take


@pytest.fixture
def inputs():
    return (
        {"id": "line-one", "text": "The lamp flickered.", "direction": "hushed"},
        {"id": "narrator", "voice": "Kore"},
        {"id": "scene-one", "tone": "uneasy"},
        "gemini", DEFAULT_TTS_MODEL, "unused-test-key",
    )


def wav_bytes(sample=16, frames=2400):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        writer.writeframes(sample.to_bytes(2, "little", signed=True) * frames)
    return output.getvalue()


def fake_renderer(data, calls=None):
    def render(segment, character, scene, provider, model, api_key, path):
        if calls is not None:
            calls.append(path)
        path.write_bytes(data)
        return {"fingerprint": render_fingerprint(segment, character, scene, provider, model),
                "duration": 999, "provider": provider, "model": model, "voice": character["voice"]}
    return render


def test_changed_take_preserves_both_versions_of_same_recipe(tmp_path, inputs):
    first_bytes, second_bytes = wav_bytes(), wav_bytes(32)
    first = produce_take(*inputs, tmp_path, synthesizer=fake_renderer(first_bytes))
    second = produce_take(*inputs, tmp_path, synthesizer=fake_renderer(second_bytes))
    assert first["fingerprint"] == second["fingerprint"]
    assert first["asset_id"] != second["asset_id"]
    for metadata, data in ((first, first_bytes), (second, second_bytes)):
        assert metadata["asset_id"] == hashlib.sha256(data).hexdigest()
        assert (tmp_path / f"{metadata['asset_id']}.wav").read_bytes() == data
        assert metadata["duration"] == .1
    assert len(list(tmp_path.iterdir())) == 2


def test_identical_bytes_deduplicate_without_rewriting_asset(tmp_path, inputs):
    data, calls = wav_bytes(), []
    first = produce_take(*inputs, tmp_path, synthesizer=fake_renderer(data, calls))
    target = tmp_path / f"{first['asset_id']}.wav"
    before = target.stat()
    second = produce_take(*inputs, tmp_path, synthesizer=fake_renderer(data, calls))
    assert len(calls) == 2  # Runtime, rather than the archive, decides reuse.
    assert calls[0] != calls[1]
    assert second == first
    assert target.stat().st_ino == before.st_ino
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    assert list(tmp_path.iterdir()) == [target]


def test_new_take_leaves_legacy_recipe_file_unchanged(tmp_path, inputs):
    legacy = tmp_path / f"{render_fingerprint(*inputs[:5])}.wav"
    legacy.write_bytes(wav_bytes(24))
    before = legacy.read_bytes()
    result = produce_take(*inputs, tmp_path, synthesizer=fake_renderer(wav_bytes(48)))
    assert legacy.read_bytes() == before
    assert (tmp_path / f"{result['asset_id']}.wav").is_file()
    assert len(list(tmp_path.iterdir())) == 2


def test_failed_retry_cleans_partial_output_preserving_old_assets(tmp_path, inputs):
    old = produce_take(*inputs, tmp_path, synthesizer=fake_renderer(wav_bytes()))
    old_path = tmp_path / f"{old['asset_id']}.wav"
    old_bytes = old_path.read_bytes()
    legacy = tmp_path / f"{old['fingerprint']}.wav"
    legacy.write_bytes(wav_bytes(48))

    def fail(*args):
        args[-1].write_bytes(b"unfinished replacement")
        raise AudioError("Provider stopped")

    with pytest.raises(AudioError, match="Provider stopped"):
        produce_take(*inputs, tmp_path, synthesizer=fail)
    assert old_path.read_bytes() == old_bytes
    assert legacy.read_bytes() == wav_bytes(48)
    assert set(tmp_path.iterdir()) == {old_path, legacy}


@pytest.mark.parametrize("data", [b"not a WAV", wav_bytes(0), wav_bytes()[:-10]])
def test_invalid_output_never_becomes_an_asset(tmp_path, inputs, data):
    with pytest.raises(AudioError):
        produce_take(*inputs, tmp_path, synthesizer=fake_renderer(data))
    assert list(tmp_path.iterdir()) == []


def test_conflicting_content_address_is_not_overwritten(tmp_path, inputs):
    data = wav_bytes()
    target = tmp_path / f"{hashlib.sha256(data).hexdigest()}.wav"
    target.write_bytes(b"existing corrupt archive")
    with pytest.raises(AudioError, match="content integrity"):
        produce_take(*inputs, tmp_path, synthesizer=fake_renderer(data))
    assert target.read_bytes() == b"existing corrupt archive"
    assert list(tmp_path.iterdir()) == [target]


def test_wrong_recipe_metadata_is_rejected_before_publishing(tmp_path, inputs):
    def render(*args):
        args[-1].write_bytes(wav_bytes())
        return {"fingerprint": "wrong recipe"}
    with pytest.raises(AudioError, match="render recipe"):
        produce_take(*inputs, tmp_path, synthesizer=render)
    assert list(tmp_path.iterdir()) == []
