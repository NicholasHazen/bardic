import pytest

from bardic import pronunciation as p


def entry(term, respelling, **extra):
    return p.normalize_entry({"term": term, "respelling": respelling, **extra})


def test_whole_word_case_sensitive_substitution_keeps_possessives_and_skips_longer_words():
    lexicon = [entry("Will", "Wil"), entry("Cthaelor", "Kaylor")]
    text = "Cthaelor's cloak. Will will go to Willow with Cthaelor."
    spoken, replaced = p.apply(text, lexicon, "breeze")
    assert spoken == "Kaylor's cloak. Wil will go to Willow with Kaylor."
    assert [item["term"] for item in replaced] == ["Cthaelor", "Will", "Cthaelor"]
    for item in replaced:
        assert text[slice(*item["source"])] == item["term"]
        assert spoken[slice(*item["output"])] == item["spoken"]


def test_case_insensitive_entry_matches_shouted_dialogue():
    spoken, _ = p.apply("KVOTHE! kvothe.", [entry("Kvothe", "Kvohth", match_case=False)], "system")
    assert spoken == "Kvohth! Kvohth."


def test_longest_term_wins_and_multiword_terms_match_across_line_breaks():
    lexicon = [entry("Valon", "Vah-lon"), entry("Tar Valon", "Tahr Vah-lon")]
    spoken, replaced = p.apply("To Tar\nValon, then Valon.", lexicon, "gemini")
    assert spoken == "To Tahr Vah-lon, then Vah-lon."
    assert [item["term"] for item in replaced] == ["Tar Valon", "Valon"]


def test_provider_override_and_identity_spellings_are_not_replacements():
    lexicon = [entry("Aoibhe", "Eeva", providers={"breeze": "Aoibhe"})]
    assert p.apply("Aoibhe waved.", lexicon, "breeze") == ("Aoibhe waved.", [])
    assert p.apply("Aoibhe waved.", lexicon, "system")[0] == "Eeva waved."


def test_non_ascii_offsets_are_code_points():
    text = "“Ngaiovar”—🙂 Ngaiovar."
    spoken, replaced = p.apply(text, [entry("Ngaiovar", "ny-oh-var")], "breeze")
    assert spoken == "“ny-oh-var”—🙂 ny-oh-var."
    assert [text[slice(*item["source"])] for item in replaced] == ["Ngaiovar", "Ngaiovar"]


def test_to_source_maps_spoken_offsets_back_without_splitting_words():
    text = "Then Eilidh met Xhosari at dawn."
    spoken, replaced = p.apply(text, [entry("Eilidh", "Aylee"), entry("Xhosari", "Zosahree")], "breeze")
    assert spoken == "Then Aylee met Zosahree at dawn."
    assert p.to_source(0, replaced) == 0
    assert p.to_source(spoken.index("Aylee") + 2, replaced) == text.index("Eilidh")
    assert p.to_source(spoken.index(" met"), replaced) == text.index(" met")
    assert p.to_source(spoken.index(" at"), replaced) == text.index(" at")
    assert p.to_source(len(spoken), replaced) == len(text)
    assert p.to_source(5, []) == 5


def test_recipe_identity_lists_only_used_pairs():
    lexicon = [entry("Eilidh", "Aylee"), entry("Cthaelor", "Kaylor")]
    _, replaced = p.apply("Eilidh and Eilidh.", lexicon, "breeze")
    assert p.recipe_identity(replaced) == {"version": p.LEXICON_VERSION, "applied": [["Eilidh", "Aylee"]]}
    assert p.recipe_identity([]) is None


@pytest.mark.parametrize("respelling", ["(laugh) Eeva", "<sigh>", "[[inpt PHON]]", "Ee\\va", "123", ""])
def test_respelling_cannot_carry_markup_or_be_empty(respelling):
    with pytest.raises(p.PronunciationError):
        entry("Aoibhe", respelling)


def test_lexicon_rejects_duplicate_terms_unless_both_are_distinct_case_sensitive():
    p.normalize_lexicon([{"term": "Rose", "respelling": "Rohz"}, {"term": "rose", "respelling": "rohz"}])
    with pytest.raises(p.PronunciationError):
        p.normalize_lexicon([{"term": "Rose", "respelling": "Rohz"}, {"term": "Rose", "respelling": "Roz"}])
    with pytest.raises(p.PronunciationError):
        p.normalize_lexicon([{"term": "Rose", "respelling": "Rohz", "match_case": False},
                             {"term": "rose", "respelling": "rohz"}])
    with pytest.raises(p.PronunciationError):
        p.normalize_entry({"term": "Rose", "respelling": "Rohz", "ipa": "roʊz"})


def test_normalize_keeps_id_and_collapses_whitespace():
    first = entry("  Tar   Valon ", " Tahr  Vah-lon ")
    assert first["term"] == "Tar Valon" and first["respelling"] == "Tahr Vah-lon"
    assert p.normalize_entry(first) == first


def test_usage_counts_whole_words_passages_and_longest_matches():
    chapters = [{"id": "c1", "text": "Will and Willow. Will went to Tar Valon."}, {"id": "c2", "text": "will"}]
    segments = [{"id": "s1", "text": "Will and Willow."}, {"id": "s2", "text": "Will went to Tar Valon."}]
    will, city, valon = entry("Will", "Wil"), entry("Tar Valon", "Tahr Vah-lon"), entry("Valon", "Vah-lon")
    stats = p.usage(chapters, segments, [will, city, valon])
    assert stats[will["id"]]["occurrences"] == 2 and stats[will["id"]]["passages"] == 2
    assert stats[will["id"]]["first_passage_id"] == "s1"
    assert stats[will["id"]]["examples"][0]["start"] == 0 and "Willow" in stats[will["id"]]["examples"][0]["context"]
    assert stats[city["id"]]["occurrences"] == 1 and stats[valon["id"]]["occurrences"] == 0, "narration reads the longer name"


def test_decomposed_text_and_curly_apostrophes_still_match():
    import unicodedata
    text = unicodedata.normalize("NFD", "Aoibhé waved to O’Brien.")
    spoken, replaced = p.apply(text, [entry("Aoibhé", "Eeva"), entry("O'Brien", "Oh Bryan")], "system")
    assert spoken == "Eeva waved to Oh Bryan."
    assert [text[slice(*item["source"])] for item in replaced] == [unicodedata.normalize("NFD", "Aoibhé"), "O’Brien"]


def test_candidates_never_drop_a_real_match():
    import unicodedata
    lexicon = [entry("Tar Valon", "Tahr"), entry("Mr.", "Mister"), entry("O'Brien", "Oh"),
               entry("Straße", "Strahsseh", match_case=False), entry("Aoibhé", "Eeva"), entry("Will", "Wil")]
    texts = ["To Tar\nValon", "Mr. Smith", "O’Brien", "STRASSE and Straße", unicodedata.normalize("NFD", "Aoibhé"),
             "Willow", "WILL"]
    for text in texts:
        found = p.candidates(text, lexicon)
        for item in lexicon:
            if p._pattern([item])[0].search(text):
                assert item in found, (text, item["term"])


def test_draft_replaces_conflicting_saved_entries_and_speech_entries_drop_private_fields():
    saved = entry("Eilidh", "Aylee", note="Private reminder")
    draft = entry("eilidh", "Ay-lee", match_case=False)
    merged = p.merged([saved], draft)
    assert merged == [draft]
    assert p.apply("“Wait,” Eilidh said.", merged, "system")[0] == "“Wait,” Ay-lee said."
    reduced = p.speech_entries("“Wait,” Eilidh said.", [saved, entry("Zyrrhan", "Zeer-an")])
    assert reduced == [{"term": "Eilidh", "respelling": "Aylee", "match_case": True}]
    assert p.apply("Eilidh.", reduced, "system")[0] == "Aylee."
