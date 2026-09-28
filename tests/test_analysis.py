from copy import deepcopy

import pytest

from bardic import analysis
from bardic.analysis import AnalysisCancelled, analyze_book
from bardic.importer import make_demo_book, parse_book


def test_local_attribution_is_conservative_and_cannot_rewrite_text():
    book = parse_book("cast.txt", '“Come here,” Mara whispered.\n\n“Never,” he said.\n\nMara laughed. “Please?”'.encode())
    original = deepcopy(book)
    result = analyze_book(book, "local")
    assert book == original
    mara = next(c for c in result["characters"] if c["name"] == "Mara")
    dialogue = [s for s in result["segments"] if s["kind"] == "dialogue"]
    assert dialogue[0]["speaker_id"] == mara["id"]
    assert dialogue[0]["cues"] == ["quiet"]
    assert dialogue[1]["speaker_id"] == "unassigned"
    assert dialogue[2]["speaker_id"] == mara["id"]
    assert [s["text"] for s in result["segments"]] == [s["text"] for s in book["segments"]]
    assert result["chapters"] == book["chapters"]
    assert result["analysis"]["status"] == "draft"


def test_local_never_borrows_speech_tags_from_following_paragraph():
    book = parse_book("tags.txt", '“Hello,” Elias said. “Goodbye.”\n\nMara laughed. “Again?”\n\n“No.”\n\nElias replied.\n'.encode())
    result = analyze_book(book, "local")
    characters = {c["name"]: c["id"] for c in result["characters"]}
    lines = [s for s in result["segments"] if s["kind"] == "dialogue"]
    assert [s["speaker_id"] for s in lines] == [characters["Elias"], characters["Elias"], characters["Mara"], "unassigned"]


def test_reanalysis_preserves_character_voice_and_reviewed_annotations():
    book = analyze_book(make_demo_book(), "local")
    character = next(c for c in book["characters"] if c["name"] == "Mara")
    character.update(voice="Leda", system_voice="Samantha", direction="Steady and dry.", description="Reviewed profile", edited=True)
    segment = next(s for s in book["segments"] if s["kind"] == "dialogue")
    segment.update(speaker_id="narrator", direction="A private thought.", edited=True)
    book["scenes"][0].update(summary="My scene summary", direction="Slow build", edited=True)
    result = analyze_book(book, "local")
    assert next(c for c in result["characters"] if c["id"] == character["id"])["description"] == "Reviewed profile"
    assert next(c for c in result["characters"] if c["id"] == character["id"])["voice"] == "Leda"
    assert result["segments"][book["segments"].index(segment)] == segment
    assert result["scenes"][0]["summary"] == "My scene summary"


def test_cancelled_or_invalid_analysis_leaves_book_unchanged():
    book = make_demo_book()
    original = deepcopy(book)
    with pytest.raises(AnalysisCancelled):
        analyze_book(book, "local", cancelled=lambda: True)
    with pytest.raises(ValueError, match="API key"):
        analyze_book(book, "gemini")
    assert book == original


def test_source_mismatch_is_rejected_before_analysis():
    book = make_demo_book()
    book["segments"][0]["text"] = "Rewritten prose"
    with pytest.raises(ValueError, match="immutable chapter"):
        analyze_book(book, "local")


def fake_cloud(monkeypatch, book, *, provider="gemini", bad_id=False, bad_evidence=False, bad_speaker=False):
    calls = []
    profile = {"name": "Mara", "aliases": [], "description": "A quiet speaker.", "direction": "Understated.", "evidence": ['Mara whispered' if not bad_evidence else 'A nonexistent quotation.']}

    def request(client, model, key, prompt, schema, cancelled):
        calls.append(prompt)
        if len(calls) <= 2:
            return {"characters": [profile]}
        mara_id = "character_" + analysis.hashlib.sha256(b"mara").hexdigest()[:12]
        segments = [{"id": s["id"], "speaker_id": mara_id if s["kind"] == "dialogue" else "narrator", "confidence": 0.91, "direction": "Softly.", "cues": ["quiet"], "evidence": ["Mara whispered"] if s["kind"] == "dialogue" else []} for s in book["segments"]]
        if bad_id:
            segments[0]["id"] = "invented-id"
        if bad_speaker:
            segments[0]["speaker_id"] = "invented-character"
        return {"summary": "Mara makes a quiet request.", "tone": "Quiet tension", "direction": "Understated.", "segments": segments, "scene_starts": []}

    monkeypatch.setattr(analysis, {"gemini": "_request", "openai": "_openai_request", "anthropic": "_anthropic_request"}[provider], request)
    return calls


@pytest.mark.parametrize("provider", ["gemini", "openai", "anthropic"])
def test_cloud_runs_cast_global_profile_and_id_anchored_annotation(monkeypatch, provider):
    book = parse_book("sample.txt", '“Come here,” Mara whispered.'.encode())
    calls = fake_cloud(monkeypatch, book, provider=provider)
    progress = []
    result = analyze_book(book, provider, api_key="fake-test-key", progress=lambda *args: progress.append(args))
    assert len(calls) == 3
    assert result["analysis"]["provider"] == provider
    assert result["analysis"]["model"] == analysis.DEFAULT_MODELS[provider]
    assert result["chapters"] == book["chapters"]
    assert result["segments"][0]["speaker_id"].startswith("character_")
    assert result["segments"][0]["text"] == book["segments"][0]["text"]
    assert progress[-1][:2] == (3, 3)


@pytest.mark.parametrize("provider", ["gemini", "openai", "anthropic"])
@pytest.mark.parametrize("kwargs,match", [({"bad_id": True}, "source IDs"), ({"bad_evidence": True}, "evidence passage"), ({"bad_speaker": True}, "unknown speaker")])
def test_cloud_rejects_invented_ids_or_evidence_without_partial_mutation(monkeypatch, provider, kwargs, match):
    book = parse_book("sample.txt", '“Come here,” Mara whispered.'.encode())
    original = deepcopy(book)
    fake_cloud(monkeypatch, book, provider=provider, **kwargs)
    with pytest.raises(ValueError, match=match):
        analyze_book(book, provider, api_key="fake-test-key")
    assert book == original


@pytest.mark.parametrize("provider", ["gemini", "openai", "anthropic"])
def test_cloud_preserves_reviewed_cast_scene_and_passage_choices(monkeypatch, provider):
    book = analyze_book(parse_book("sample.txt", '“Come here,” Mara whispered.'.encode()), "local")
    mara = next(c for c in book["characters"] if c["name"] == "Mara")
    mara.update(voice="Leda", system_voice="Samantha", direction="Dry and steady.", description="My reviewed profile", edited=True)
    book["segments"][0].update(speaker_id="narrator", direction="An inner thought.", edited=True)
    book["scenes"][0].update(summary="My reviewed scene", tone="Hopeful", direction="Build gently.", edited=True)
    original = deepcopy(book)
    fake_cloud(monkeypatch, book, provider=provider)
    result = analyze_book(book, provider, api_key="fake-test-key")
    reviewed = next(c for c in result["characters"] if c["id"] == mara["id"])
    for field in ("voice", "system_voice", "direction", "description"):
        assert reviewed[field] == mara[field]
    assert result["segments"][0] == book["segments"][0]
    for field in ("summary", "tone", "direction"):
        assert result["scenes"][0][field] == book["scenes"][0][field]
    assert book == original


def test_low_confidence_dialogue_stays_unassigned_and_scene_splits_keep_spans():
    book = parse_book("sample.txt", 'A quiet room.\n\n“Come here,” Mara whispered.\n\nAt dawn, the ship sailed.'.encode())
    scene = book["scenes"][0]
    segments = book["segments"]
    boundary = segments[-1]["id"]
    result = {"summary": "A request, then departure.", "tone": "Quiet", "direction": "Steady.", "segments": [{"id": s["id"], "speaker_id": "narrator", "confidence": 0.2, "direction": "", "cues": [], "evidence": []} for s in segments], "scene_starts": [{"segment_id": boundary, "title": "Dawn", "summary": "The ship departs.", "tone": "Hopeful", "direction": "Open and easy."}]}
    boundaries = {}
    analysis._apply_annotations(book, scene, segments, result, boundaries)
    analysis._split_scenes(book, boundaries)
    assert len(book["scenes"]) == 2
    assert book["scenes"][1]["segment_ids"] == [boundary]
    assert next(s for s in segments if s["kind"] == "dialogue")["speaker_id"] == "unassigned"
    assert segments[-1]["text"] == "At dawn, the ship sailed."


@pytest.mark.parametrize("tag", ["Mara laughed once, without amusement.", "Mara gave a humorless laugh.", "Mara laughed mirthlessly.", "Mara didn't laugh.", "Mara did not laugh.", "Mara laughed nervously."])
def test_local_never_turns_negated_or_unhappy_laughter_into_a_smile(tag):
    book = parse_book("laughter.txt", f'{tag} “After all this time?”\n\n“No,” {tag}'.encode())
    result = analyze_book(book, "local")
    assert all("amusement" not in s["cues"] for s in result["segments"])
    assert all("smile" not in s["direction"] for s in result["segments"])


def test_global_profile_improves_automatic_direction_but_preserves_voice_choice():
    book = analyze_book(parse_book("mara.txt", '“Hello,” Mara said.'.encode()), "local")
    mara = next(c for c in book["characters"] if c["name"] == "Mara")
    mara["voice"] = "Aoede"
    analysis._merge_cast(book, [{"name": "Mara", "aliases": [], "description": "A low, measured voice.", "direction": "Measured and dry; clipped consonants.", "evidence": ["Mara said"]}])
    assert mara["direction"] == "Measured and dry; clipped consonants."
    assert mara["voice"] == "Aoede"


def test_global_alias_reconciliation_merges_only_unreviewed_draft_duplicates():
    book = analyze_book(parse_book("aliases.txt", '“Yes,” Mara said.\n\n“No,” Captain Voss replied.'.encode()), "local")
    profiles = [{"name": "Mara", "aliases": ["Captain Voss"]}]
    mara = next(c for c in book["characters"] if c["name"] == "Mara")
    analysis._reconcile_known_aliases(book, profiles)
    assert len(book["characters"]) == 3
    assert "Captain Voss" in mara["aliases"]
    assert all(s["speaker_id"] == mara["id"] for s in book["segments"] if s["kind"] == "dialogue")

    reviewed = analyze_book(parse_book("aliases.txt", '“Yes,” Mara said.\n\n“No,” Captain Voss replied.'.encode()), "local")
    for character in reviewed["characters"]:
        character["edited"] = True
    original = deepcopy(reviewed)
    analysis._reconcile_known_aliases(reviewed, profiles)
    assert reviewed == original


def test_cloud_cast_cannot_silently_drop_discovered_characters(monkeypatch):
    book = parse_book("mara.txt", '“Hello,” Mara said.'.encode())
    original = deepcopy(book)
    calls = []

    def request(*args):
        calls.append(1)
        return {"characters": [{"name": "Mara", "aliases": [], "description": "Unknown vocal traits.", "direction": "Natural.", "evidence": ["Mara said"]}]} if len(calls) == 1 else {"characters": []}

    monkeypatch.setattr(analysis, "_request", request)
    with pytest.raises(ValueError, match="omitted discovered characters"):
        analyze_book(book, "gemini", api_key="fake-test-key")
    assert book == original


def test_present_tense_attribution_supports_titles_and_apostrophized_names():
    book = parse_book("names.txt", '“Wait,” Dr. O’Neill says.\n\n“Why?” Anne-Marie asks.'.encode())
    result = analyze_book(book, "local")
    cast = {c["id"]: c["name"] for c in result["characters"]}
    speakers = [cast[s["speaker_id"]] for s in result["segments"] if s["kind"] == "dialogue"]
    assert speakers == ["Dr. O’Neill", "Anne-Marie"]
