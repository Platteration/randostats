"""The optional Claude path.

It is the only code here that talks to an external service, and the rule-based
punchline is already on screen by the time it runs. So every failure it can
have must end in None, never an exception that fails the request.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from randostats.api import create_app
from randostats.counterpoint import CounterpointEngine, llm


@pytest.fixture(autouse=True)
def no_cached_client(monkeypatch):
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.delenv("RANDOSTATS_LLM", raising=False)
    yield
    llm._client = None


@pytest.fixture
def counterpoints():
    return CounterpointEngine(seed=1).respond("seventy percent of people drink beer")


def fake_client(result=None, error=None, seen=None):
    def parse(**kwargs):
        if seen is not None:
            seen.update(kwargs)
        if error is not None:
            raise error
        return result

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))


def reply(fact_id, punchline="Ha.", logic_gap="No denominator.", stop_reason="end_turn", model="claude-opus-5"):
    parsed = llm.Rebuttal(fact_id=fact_id, punchline=punchline, logic_gap=logic_gap)
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed, model=model)


def test_happy_path(monkeypatch, counterpoints):
    chosen = counterpoints[0].fact.id
    monkeypatch.setattr(llm, "_client", fake_client(reply(chosen, "  Same number, no link.  ")))
    out = llm.sharpen("70% of people drink beer", counterpoints)
    assert out == {"fact_id": chosen, "punchline": "Same number, no link.",
                   "logic_gap": "No denominator.", "model": "claude-opus-5"}


def test_the_prompt_only_offers_facts_we_matched(monkeypatch, counterpoints):
    """Claude picks among sourced facts; it is never asked to supply one."""
    seen: dict = {}
    monkeypatch.setattr(llm, "_client", fake_client(reply(counterpoints[0].fact.id), seen=seen))
    llm.sharpen("70% of people drink beer", counterpoints)
    prompt = seen["messages"][0]["content"]
    for cp in counterpoints:
        assert f"id={cp.fact.id}" in prompt
        assert cp.fact.statement in prompt and cp.fact.source in prompt
    assert "Do not invent statistics" in seen["system"]
    assert seen["output_format"] is llm.Rebuttal
    # the documented pairing: the "default" scalar form needs the -07-01 beta
    assert seen["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["betas"]


def test_an_invented_fact_id_falls_back_to_one_we_offered(monkeypatch, counterpoints):
    monkeypatch.setattr(llm, "_client", fake_client(reply("a-fact-we-never-sent")))
    assert llm.sharpen("70% of people", counterpoints)["fact_id"] == counterpoints[0].fact.id


@pytest.mark.parametrize("response", [
    pytest.param(reply("x", stop_reason="refusal"), id="refusal"),
    pytest.param(SimpleNamespace(stop_reason="end_turn", parsed_output=None, model="m"), id="unparseable"),
    pytest.param(reply("x", punchline="   "), id="blank punchline"),
    pytest.param(reply("x", logic_gap=""), id="blank logic gap"),
])
def test_unusable_replies_fall_back_to_the_rule_based_line(monkeypatch, counterpoints, response):
    monkeypatch.setattr(llm, "_client", fake_client(response))
    assert llm.sharpen("70% of people", counterpoints) is None


@pytest.mark.parametrize("error", [
    RuntimeError("connection reset"),
    TimeoutError("took too long"),
    ValueError("nonsense from the server"),
])
def test_any_exception_becomes_none(monkeypatch, counterpoints, error):
    monkeypatch.setattr(llm, "_client", fake_client(error=error))
    assert llm.sharpen("70% of people", counterpoints) is None


def test_no_counterpoints_means_nothing_to_sharpen():
    assert llm.sharpen("70% of people", []) is None


def test_a_missing_credential_is_reported_not_raised(monkeypatch, counterpoints):
    """anthropic.Anthropic() raises AnthropicError, which is APIError's parent."""
    import anthropic

    def refuse():
        raise anthropic.AnthropicError("The api_key client option must be set")

    monkeypatch.setattr(anthropic, "Anthropic", refuse)
    assert llm.available() is False
    assert llm.sharpen("70% of people", counterpoints) is None


def test_the_off_switch_wins_over_a_working_credential(monkeypatch):
    monkeypatch.setenv("RANDOSTATS_LLM", "off")
    assert llm.available() is False


def test_the_endpoint_degrades_when_claude_says_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "sharpen", lambda claim, group: None)
    with TestClient(create_app(tmp_path / "llm.db", use_llm=True)) as client:
        assert client.get("/api/status").json()["llm"] is True
        body = client.post("/api/counterpoint", json={"text": "70% of people drink beer"}).json()
        assert body["results"], "the rule-based answer must still be there"
        assert body.get("llm") == {}


def test_the_endpoint_sharpens_every_claim_in_one_sentence(tmp_path, monkeypatch):
    calls: list[str] = []

    def sharpen(claim_text, group):
        calls.append(claim_text)
        return {"fact_id": group[0].fact.id, "punchline": f"re: {claim_text}",
                "logic_gap": "No baseline.", "model": "stub"}

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "sharpen", sharpen)
    with TestClient(create_app(tmp_path / "llm2.db", use_llm=True)) as client:
        body = client.post("/api/counterpoint", json={
            "text": "70% of people drink beer, and dinosaurs were 700 times older"}).json()
    assert len(calls) == 2, f"expected one call per claim, got {calls}"
    assert len(body["llm"]) == 2
    assert all(entry["punchline"].startswith("re: ") for entry in body["llm"].values())


def test_llm_is_off_unless_asked_for(tmp_path):
    with TestClient(create_app(tmp_path / "off.db", use_llm=False)) as client:
        assert client.get("/api/status").json()["llm"] is False
        assert "llm" not in client.post("/api/counterpoint", json={"text": "70% of people"}).json()


def test_availability_needs_a_credential_not_just_a_client(monkeypatch):
    """Building a client proves nothing: the SDK resolves auth at request time.

    Reported as available with no credential, the app showed the option and
    then fell back on every single use.
    """
    monkeypatch.setattr(llm, "_client", SimpleNamespace(auth_headers={}, credentials=None))
    assert llm.available() is False

    monkeypatch.setattr(llm, "_client", SimpleNamespace(auth_headers={"X-Api-Key": "k"}, credentials=None))
    assert llm.available() is True

    # `ant auth login` sets no header up front; it injects one per request
    monkeypatch.setattr(llm, "_client", SimpleNamespace(auth_headers={}, credentials=object()))
    assert llm.available() is True


def test_an_unfamiliar_sdk_shape_does_not_disable_a_working_feature(monkeypatch):
    monkeypatch.setattr(llm, "_client", SimpleNamespace())
    assert llm.available() is True
