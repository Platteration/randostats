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
