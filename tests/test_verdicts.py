"""The engine's answers before the parallels, and the claim shapes behind them.

Every row here was wrong once. A realistic set of things people say in
arguments was run through the engine, and it read "up 40%" as a share, took
"I'm 100% sure" for a statistic, answered famous myths with parallels, and
never used the real figures it already had. These pin each fix.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from randostats.api import create_app
from randostats.counterpoint import CounterpointEngine, extract_claims, packs
from randostats.counterpoint.engine import _TOPICS


def claims(text):
    return [(c.kind, c.value) for c in extract_claims(text)]


def verdicts(text, **kw):
    return CounterpointEngine(seed=1, **kw).analyse(text)[0]


# ---------------------------------------------------------------- claim shapes

@pytest.mark.parametrize("text, expected", [
    ("Crime went up 40% last year", [("change", 40.0)]),
    ("Coffee drinkers are 20% less likely to get diabetes", [("change", -20.0)]),
    ("It's up 300 percent", [("change", 300.0)]),
    ("prices fell by about 8 percent", [("change", -8.0)]),
    ("it increases your risk of cancer by 20%", [("change", 20.0)]),
    ("a 30% increase in crime", [("change", 30.0)]),
    ("it rose from 20% to 30%", [("change", 50.0)]),
])
def test_a_change_is_not_a_share(text, expected):
    """Answering "crime went up 40%" with "40% of Americans live on the coast"
    commits the very confusion the app exists to point out."""
    assert claims(text) == expected


def test_up_to_is_a_ceiling_not_a_change():
    assert claims("up to 40% of people") == [("percent", 40.0)]
    assert claims("the score went up by 3.") == []


@pytest.mark.parametrize("text, expected", [
    ("Smoking makes you 15 to 30 times more likely to get cancer", [("ratio", 22.5)]),
    ("Smoking increases your risk of cancer by 15 to 30 times", [("ratio", 22.5)]),
    ("20-30% of people", [("percent", 25.0)]),
    ("a threefold increase", [("ratio", 3.0)]),
    ("by a factor of 3.", [("ratio", 3.0)]),
])
def test_ranges_and_multipliers_are_read(text, expected):
    assert claims(text) == expected


@pytest.mark.parametrize("text", ["I'm 100% sure", "I 100% agree", "I'm 90 percent certain",
                                  "I'm 100 percent with you on this"])
def test_figures_of_speech_are_not_statistics(text):
    assert claims(text) == []


def test_a_real_hundred_percent_is_still_a_claim():
    assert claims("one hundred percent of people who drink water die") == [("percent", 100.0)]
    assert claims("twenty-five percent agree") == [("percent", 25.0)]


def test_a_vague_word_inside_a_claim_is_not_a_second_claim():
    assert claims("0.1% of people control most of the wealth") == [("percent", 0.1)]
    # two real numbers in one sentence are still two claims
    assert len(claims("80 percent of crime is committed by 5 percent of people")) == 2


def test_a_change_keeps_what_changed():
    (claim,) = extract_claims("Honestly, crime went up 40% last year")
    assert claim.raw == "crime went up 40% last year"  # the clause, from its last boundary


def test_a_proper_noun_keeps_its_capital_mid_sentence():
    """"By that math, russia is about 1.7 times the size..." was on screen."""
    facts = {f.id: f for f in CounterpointEngine(packs={"deep_time", "money", "sports"}).facts}
    assert facts["russia-us"].statement_lc.startswith("Russia")
    assert facts["dt-cleopatra"].statement_lc.startswith("Cleopatra")
    assert facts["sp-ft"].statement_lc.startswith("NBA")  # an acronym needs no marking
    assert facts["earth-water"].statement_lc.startswith("about")
    assert facts["water-air"].statement_lc.startswith("water")


def test_a_change_is_matched_against_multipliers_only():
    engine = CounterpointEngine(seed=1)
    results = engine.respond("Crime went up 40% last year")
    assert results and all(r.fact.kind == "ratio" for r in results)
    assert all(r.fallacy and "{" not in r.fallacy for r in results)


# ----------------------------------------------------------------- fact-check

def check_of(text, **kw):
    found = [v for v in verdicts(text, **kw) if v["kind"] == "check"]
    return found[0] if found else None


@pytest.mark.parametrize("text, fact_id, verdict", [
    ("70% of people drink beer", "alcohol-us", "close"),
    ("seventy percent of individuals drink beer", "alcohol-us", "close"),
    ("1 in 5 adults have a tattoo", "tattoo-us", "off"),
    ("About a third of Americans are obese", "obese-us", "off"),
    ("most people drink coffee", "coffee-us", "fair"),
    ("90% of Americans own a smartphone", "smartphone-us", "fair"),
    ("70% of the earth is covered by water", "earth-water", "fair"),
    ("the human body is 60% water", "body-water", "fair"),
    ("30% of the world has no clean water", "no-safe-water", "fair"),
])
def test_a_claim_the_facts_cover_is_checked(text, fact_id, verdict):
    found = check_of(text)
    assert found, f"no check for {text!r}"
    assert found["fact"]["id"] == fact_id and found["verdict"] == verdict
    assert found["source"] and found["year"]
    assert found["fact"]["statement"] in found["line"]


def test_the_check_says_which_way_and_how_far():
    found = check_of("70% of people drink beer")
    assert found["delta"] == 8 and "8 points high" in found["line"]
    assert "12 points low" in check_of("1 in 5 adults have a tattoo")["line"]
    assert "“a third” is too low" in check_of("About a third of Americans are obese")["line"]
    assert "“most Americans” is too high" in check_of(
        "Most Americans can't afford a $400 emergency", packs={"money"})["line"]


@pytest.mark.parametrize("text", [
    "70% of people don't drink beer",    # framed the other way round
    "70% of Brits drink beer",           # a population the facts do not measure
    "70% of cars are red",               # the word, not the topic
    "Over 90% of startups fail",         # no fact on the subject at all
    "Unemployment is at 3.5%",
    "Crime went up 40% last year",       # a change, not a share
    # The first topic decides. Beer rules itself out (framing), and falling
    # through to books would check beer drinkers against all adults.
    "70% of beer drinkers don't read books",
])
def test_when_in_doubt_there_is_no_verdict(text):
    """A wrong fact-check is far worse than none."""
    assert check_of(text) is None


def test_the_real_figure_is_not_also_offered_as_a_parallel():
    """91% is one point from 90%, so without the rule it would be the first parallel."""
    engine = CounterpointEngine(seed=1)
    for _ in range(20):
        found, results = engine.analyse("90% of Americans own a smartphone", per_claim=5)
        checked = {v["fact"]["id"] for v in found if v["kind"] == "check"}
        assert checked and not checked & {r.fact.id for r in results}


def test_the_parallels_still_follow_a_check():
    found, results = CounterpointEngine(seed=1).analyse("70% of people drink beer")
    assert found and results


def test_a_check_only_uses_facts_that_are_loaded():
    assert check_of("NBA players hit 90% of free throws") is None
    assert check_of("NBA players hit 90% of free throws", packs={"sports"})["fact"]["id"] == "sp-ft"


def test_every_topic_names_real_percentage_facts():
    """A typo in a fact id would make a topic silently check nothing."""
    every = {f["id"]: f for f in packs.load_facts({p["id"] for p in packs.list_packs()})}
    for pattern, ids in _TOPICS:
        re.compile(pattern)
        for fact_id in ids:
            assert fact_id in every, f"{pattern!r} names unknown fact {fact_id}"
            assert every[fact_id]["kind"] == "percent", fact_id


# ---------------------------------------------------------------------- myths

MYTHS = packs.load_myths()


@pytest.mark.parametrize("myth", MYTHS, ids=lambda m: m["id"])
def test_each_myth_is_sourced_and_catches_its_own_examples(myth):
    assert myth["truth"] and myth["source"] and myth["claim"]
    assert not myth["truth"].endswith("."), "the engine adds the full stop"
    assert myth["examples"], "a myth without examples is untested"
    engine = CounterpointEngine(seed=1)
    for example in myth["examples"]:
        hits = [m["id"] for m, _ in engine.myths_in(example)]
        assert hits == [myth["id"]], f"{example!r} -> {hits}"


@pytest.mark.parametrize("text", [
    "my dog is 7 years old", "spiders eat flies", "the heat went to my head",
    "give me 5 seconds", "I shaved fast this morning", "he has blue blood",
    "10% of my salary goes on rent", "we watched a turkey documentary",
    "I've been chewing gum for 7 years", "the bats are in the shed",
])
def test_ordinary_sentences_are_not_myths(text):
    assert CounterpointEngine(seed=1).myths_in(text) == []


def test_a_myth_is_refuted_instead_of_paralleled():
    found, results = CounterpointEngine(seed=1).analyse("You only use 10% of your brain")
    assert [v["kind"] for v in found] == ["myth"]
    assert found[0]["title"] == "That's a myth." and found[0]["source"]
    assert results == [], "an equally irrelevant number is the wrong answer to a myth"


def test_a_myth_only_silences_its_own_sentence():
    found, results = CounterpointEngine(seed=1).analyse(
        "You only use 10% of your brain. Also 70% of people drink beer.")
    assert {v["kind"] for v in found} == {"myth", "check"}
    assert results and all(r.claim.value == 70 for r in results)


def test_a_pack_can_bring_its_own_myths(monkeypatch):
    extra = {"id": "extra", "name": "Extra", "facts": [], "myths": [{
        "id": "x-moon", "claim": "The Moon is made of cheese", "patterns": [r"\bmoon\b", r"\bcheese\b"],
        "truth": "It is rock", "source": "NASA", "examples": ["the moon is made of cheese"]}],
        "always_on": False, "description": ""}
    real = packs._packs()
    monkeypatch.setattr(packs, "_packs", lambda: {**real, "extra": extra})
    assert "x-moon" not in {m["id"] for m in packs.load_myths()}
    assert "x-moon" in {m["id"] for m in packs.load_myths({"extra"})}
    monkeypatch.setenv("RANDOSTATS_LOCKED", "extra")
    assert "x-moon" not in {m["id"] for m in packs.load_myths({"extra"})}


# -------------------------------------------------------------------- appeals

@pytest.mark.parametrize("text, title", [
    ("Studies show it's bad for you", "Which study?"),
    ("Everyone knows that", "Everyone who?"),
    ("It's been scientifically proven", "Proven how?"),
    ("Millions of people die from this every year", "Out of how many?"),
])
def test_an_appeal_with_no_number_still_gets_an_answer(text, title):
    """"No number in there" is the worst possible reply mid-argument."""
    found = verdicts(text)
    assert [v["title"] for v in found] == [title]
    assert found[0]["line"] and "{" not in found[0]["line"]


def test_one_appeal_per_sentence():
    found = verdicts("It's been proven that 97% of scientists agree")
    assert [v["title"] for v in found if v["kind"] == "appeal"] == ["Proven how?"]


# --------------------------------------------------------------------- voices

def test_every_voice_can_say_every_new_thing():
    for voice in packs.list_voices():
        engine = CounterpointEngine(seed=3, voice=voice["id"])
        for text in ("Crime went up 40%", "prices fell 20%", "You only use 10% of your brain",
                     "Studies show it", "Everyone knows", "It's proven", "Millions of people agree"):
            found, results = engine.analyse(text)
            lines = [v["line"] for v in found] + [v["note"] for v in found if v["note"]]
            lines += [r.fallacy for r in results] + [r.lines[0] for r in results]
            assert lines, f"{voice['id']}: nothing for {text!r}"
            for line in lines:
                assert "{" not in line, f"{voice['id']}: unfilled placeholder in {line!r}"


# ------------------------------------------------------------------------ API

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "v.db", use_llm=False)) as c:
        yield c


def test_the_api_returns_verdicts_before_results(client):
    body = client.post("/api/counterpoint", json={"text": "70% of people drink beer"}).json()
    assert list(body)[:3] == ["session", "verdicts", "results"]
    assert body["verdicts"][0]["kind"] == "check" and body["results"]
    myth = client.post("/api/counterpoint", json={"text": "vaccines cause autism"}).json()
    assert myth["verdicts"][0]["kind"] == "myth" and myth["results"] == []


def test_a_live_session_does_not_repeat_a_verdict(client):
    sid = client.post("/api/counterpoint/session").json()["session"]
    ask = lambda: client.post("/api/counterpoint", json={"text": "Everyone knows that", "session": sid}).json()  # noqa: E731
    assert ask()["verdicts"] and ask()["verdicts"] == []
