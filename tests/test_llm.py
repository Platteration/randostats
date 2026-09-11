"""The optional Claude path.

It is the only code here that talks to an external service, and the rule-based
punchline is already on screen by the time it runs. So every failure it can
have must end in None, never an exception that fails the request.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from randostats.api import create_app
from randostats.counterpoint import CounterpointEngine, llm


# The app only answers to the names it is meant to be reached by, so a test
# client has to use one of them (the default "testserver" is not one).
LOCAL = "http://localhost"


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
    anthropic = pytest.importorskip("anthropic", reason="the llm extra is optional")

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
    with TestClient(create_app(tmp_path / "llm.db", use_llm=True), base_url=LOCAL) as client:
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
    with TestClient(create_app(tmp_path / "llm2.db", use_llm=True), base_url=LOCAL) as client:
        body = client.post("/api/counterpoint", json={
            "text": "70% of people drink beer, and dinosaurs were 700 times older"}).json()
    assert len(calls) == 2, f"expected one call per claim, got {calls}"
    assert len(body["llm"]) == 2
    assert all(entry["punchline"].startswith("re: ") for entry in body["llm"].values())


def test_llm_is_off_unless_asked_for(tmp_path):
    with TestClient(create_app(tmp_path / "off.db", use_llm=False), base_url=LOCAL) as client:
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


def test_a_broken_credential_probe_never_stops_the_app_starting(monkeypatch, tmp_path):
    """available() runs inside create_app; raising here would break `serve --llm`."""
    class Hostile:
        @property
        def auth_headers(self):
            raise RuntimeError("this SDK does not work that way")

    monkeypatch.setattr(llm, "_client", Hostile())
    assert llm.available() is True  # unrecognised shape: let the call decide

    def explode():
        raise RuntimeError("cannot build a client at all")

    monkeypatch.setattr(llm, "_get_client", explode)
    assert llm.available() is False
    with TestClient(create_app(tmp_path / "probe.db", use_llm=True), base_url=LOCAL) as client:
        assert client.get("/api/status").json()["llm"] is False


def test_one_request_cannot_fan_out_into_unlimited_paid_calls(tmp_path, monkeypatch):
    """Every distinct claim was one Claude request, and a pasted article holds
    hundreds. The rule-based answers still cover the rest."""
    from randostats.api import MAX_LLM_GROUPS

    calls: list[str] = []

    def sharpen(claim_text, group):
        calls.append(claim_text)
        return {"fact_id": group[0].fact.id, "punchline": "p", "logic_gap": "g", "model": "stub"}

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "sharpen", sharpen)
    text = " ".join(f"{i + 1}% of group{i} agree." for i in range(30))
    with TestClient(create_app(tmp_path / "fanout.db", use_llm=True), base_url=LOCAL) as client:
        body = client.post("/api/counterpoint", json={"text": text}).json()
    assert len(body["results"]) > MAX_LLM_GROUPS, "the rule-based answers are not capped"
    assert len(calls) <= MAX_LLM_GROUPS, f"{len(calls)} paid calls for one request"


def test_many_requests_cannot_spend_without_limit(monkeypatch, counterpoints):
    """Capping one request's fan-out bounds the fan-out, not the bill.

    Nothing counted how many requests arrived, so whoever could reach a
    widened port could hold the owner's Anthropic account open at four billed
    calls a POST. Past the hour's ceiling this has to do what a missing
    credential already does: fall back, quietly, to the rule-based line.
    """
    made: list[dict] = []

    def parse(**kwargs):
        made.append(kwargs)
        return reply(counterpoints[0].fact.id)

    monkeypatch.setattr(llm, "_client", SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse))))
    # A ceiling low enough to reach, and a window that starts empty, so what
    # is counted is this test's and not whatever else ran in this process.
    ceiling = 3
    monkeypatch.setattr(llm, "MAX_CALLS_PER_HOUR", ceiling)
    monkeypatch.setattr(llm, "_calls", deque())

    got = [llm.sharpen("70% of people drink beer", counterpoints) for _ in range(ceiling * 3)]
    assert len(made) == ceiling, f"{len(made)} calls billed against a ceiling of {ceiling}"
    # Past it the caller is not failed, only unsharpened.
    assert all(g is not None for g in got[:ceiling])
    assert all(g is None for g in got[ceiling:])


def test_the_ceiling_is_reported_once_a_window_not_once_a_call(monkeypatch, counterpoints, caplog):
    """Bounding the bill must not hand the same caller an unbounded log.

    Every refused call wrote a WARNING, and a refused call is free: four lines
    per POST, for as long as whoever reached the port keeps posting, and with
    no logging configured that is a stderr the operator is usually
    redirecting to a file. The operator needs to know the ceiling was
    reached, not how many times.
    """
    monkeypatch.setattr(llm, "MAX_CALLS_PER_HOUR", 0)
    monkeypatch.setattr(llm, "_calls", deque())
    monkeypatch.setattr(llm, "_warned_at", float("-inf"))
    refused = 25

    with caplog.at_level(logging.WARNING, logger=llm.__name__):
        for _ in range(refused):
            assert llm.sharpen("70% of people drink beer", counterpoints) is None
        said = [r for r in caplog.records if "over" in r.getMessage()]
        assert len(said) == 1, f"{refused} refusals wrote {len(said)} lines"
        assert str(llm.MAX_CALLS_PER_HOUR) in said[0].getMessage(), "and it has to say what the ceiling is"

        # Once a window, though, not once ever: an hour later the operator is
        # told again, or a ceiling reached every day looks like a one-off.
        monkeypatch.setattr(llm, "_warned_at", time.monotonic() - llm._WINDOW_SECONDS - 1)
        assert llm.sharpen("70% of people drink beer", counterpoints) is None
        assert len([r for r in caplog.records if "over" in r.getMessage()]) == 2


def test_the_paid_call_cannot_hold_a_worker_thread_for_ten_minutes(monkeypatch, counterpoints):
    """`counterpoint` is a sync endpoint, so a stalled call occupies an anyio
    worker for as long as it stalls, and the SDK's own default is 600 s."""
    seen: dict = {}
    monkeypatch.setattr(llm, "_client", fake_client(reply(counterpoints[0].fact.id), seen=seen))
    monkeypatch.setattr(llm, "_calls", deque())
    llm.sharpen("70% of people drink beer", counterpoints)
    assert 0 < seen["timeout"] < 600, "the SDK's ten-minute default is back"
