from randostats.counterpoint import CounterpointEngine, extract_claims


def claims(text):
    return [(c.kind, c.value) for c in extract_claims(text)]


def test_extracts_numeric_forms():
    assert claims("70% of people drink beer") == [("percent", 70.0)]
    assert claims("seventy percent of individuals drink beer") == [("percent", 70.0)]
    assert claims("twenty-five percent agree") == [("percent", 25.0)]
    assert claims("1 in 5 adults have a tattoo") == [("percent", 20.0)]
    assert claims("three out of four dentists") == [("percent", 75.0)]
    assert claims("men are 3 times more likely to crash") == [("ratio", 3.0)]
    assert claims("it's twice as likely") == [("ratio", 2.0)]


def test_extracts_vague_quantifiers():
    (c,) = extract_claims("most people hate mondays")
    assert c.quantifier == "vague" and c.value == 65.0 and c.subject.startswith("people")
    assert claims("half of the country agrees") == [("percent", 50.0)]
    assert claims("almost everyone drinks coffee") == [("percent", 95.0)]


def test_no_claim_in_plain_text():
    assert claims("see you at 8 tonight, bring 2 friends") == []


def test_subject_capture():
    (c,) = extract_claims("Look, 70 percent of individuals drink beer, so it's fine.")
    assert c.subject == "individuals drink beer"


def test_respond_matches_magnitude_and_cites():
    engine = CounterpointEngine(seed=7)
    results = engine.respond("seventy percent of individuals drink beer")
    assert results and all(abs(r.fact.value - 70) <= 6 for r in results)
    assert all(r.lines[-1].startswith("Source:") for r in results)
    assert all(r.fallacy for r in results)
    ratio = engine.respond("men are 3 times more likely to crash")
    assert ratio and all(r.fact.kind == "ratio" for r in ratio)


def test_session_dedupes_repeated_claims():
    engine = CounterpointEngine(seed=1)
    seen = set()
    first = engine.respond("70% of people like it", seen=seen)
    again = engine.respond("like I said, 70% of people like it", seen=seen)
    assert first and again == []


def test_spurious_pair_is_close_and_unrelated():
    engine = CounterpointEngine(seed=2)
    pair = engine.spurious_pair()
    assert pair["gap"] <= 2.5
    assert "health" not in pair["a"]["tags"] and "health" not in pair["b"]["tags"]


def test_facts_are_well_formed():
    engine = CounterpointEngine()
    ids = [f.id for f in engine.facts]
    assert len(ids) == len(set(ids))
    for f in engine.facts:
        assert f.kind in ("percent", "ratio") and f.source and f.year >= 1990
        if f.kind == "percent":
            assert 0 <= f.value <= 100


def test_packs_load_and_dedupe():
    from randostats.counterpoint import packs

    ids = {p["id"] for p in packs.list_packs()}
    assert {"core", "sports", "money", "deep_time"} <= ids
    core_only = packs.load_facts(set())
    with_sports = packs.load_facts({"sports"})
    assert len(with_sports) > len(core_only)
    assert len({f["id"] for f in with_sports}) == len(with_sports)  # no duplicate ids
    assert all(f.get("pack") for f in with_sports)


def test_pack_facts_are_well_formed():
    from randostats.counterpoint import packs

    for fact in packs.load_facts({"sports", "money", "deep_time"}):
        assert fact["kind"] in ("percent", "ratio")
        assert fact["source"] and fact["short"] and fact["statement"]
        assert not fact["statement"].endswith("."), f"{fact['id']} should not carry its own full stop"
        if fact["kind"] == "percent":
            assert 0 <= fact["value"] <= 100


def test_enabling_a_pack_widens_the_matches():
    plain = CounterpointEngine(seed=3)
    sporty = CounterpointEngine(seed=3, packs={"sports"})
    assert len(sporty.facts) > len(plain.facts)
    ids = {f.id for f in sporty.facts}
    assert "sp-ft" in ids and "sp-ft" not in {f.id for f in plain.facts}


def test_every_voice_renders_every_claim_shape():
    from randostats.counterpoint import packs

    for voice in packs.list_voices():
        engine = CounterpointEngine(seed=11, voice=voice["id"])
        assert engine.voice["id"] == voice["id"]
        for text in ("70% of people drink beer", "most people agree", "3 times more likely to crash"):
            results = engine.respond(text)
            assert results, f"{voice['id']} produced nothing for {text!r}"
            for r in results:
                assert "{" not in r.lines[0], f"unfilled placeholder in {voice['id']}"
                assert "{" not in r.fallacy
                assert r.lines[0].strip()


def test_unknown_voice_falls_back_to_house():
    engine = CounterpointEngine(seed=1, voice="does-not-exist")
    assert engine.voice["id"] == "house"
