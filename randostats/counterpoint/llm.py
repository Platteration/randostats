"""Optional Claude-powered punchlines.

The rule-based engine always runs. When an Anthropic credential is available
(``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile) and the app was
started with ``--llm`` (or ``RANDOSTATS_LLM=1``), this module asks Claude to
pick the sharpest of the *already matched, sourced* facts and phrase the
rebuttal. Claude is never asked to invent statistics, so every number that
reaches the user still traces back to ``facts.json``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .engine import Counterpoint

log = logging.getLogger(__name__)

MODEL = os.environ.get("RANDOSTATS_MODEL", "claude-opus-5")

_client = None
_client_lock = threading.Lock()


def _get_client():
    """One client for the process; building it is what resolves the credential."""
    global _client
    with _client_lock:
        if _client is None:
            import anthropic

            _client = anthropic.Anthropic()
        return _client

SYSTEM = """You are the wit inside "randostats", a tool that answers a statistic thrown out in an argument
with a *real* statistic of the same magnitude that has nothing to do with it, to show that a matching
number is not evidence. You will be given the claim someone made and a short list of verified facts
with sources. Pick the fact that makes the funniest, most deflating parallel and write:

- punchline: one or two sentences, dry and quick, that quotes the fact and draws the absurd parallel.
  Use only the numbers given. Do not invent statistics, sources, or studies.
- logic_gap: one sentence naming the actual reasoning problem in the original claim
  (correlation vs causation, missing base rate, missing denominator, appeal to popularity, relative vs absolute risk).

Keep it friendly. Mock the logic, not the person."""


class Rebuttal(BaseModel):
    fact_id: str = Field(description="The id of the fact you chose, copied exactly from the list")
    punchline: str
    logic_gap: str


def _has_credential(client) -> bool:
    """Whether a credential resolves, without spending a request to find out.

    Building a client proves nothing: the SDK resolves lazily, so it succeeds
    with no credential at all and only fails at request time with "Could not
    resolve authentication method". These two attributes are the sources it
    draws on - a static key or token, and an ``ant auth login`` profile.
    """
    try:
        if client.auth_headers:  # ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN
            return True
        return getattr(client, "credentials", None) is not None  # ant auth login
    except Exception:  # noqa: BLE001 - an SDK shaped differently: let the call decide
        return True


def available() -> bool:
    """True when the package is installed and a credential is actually there.

    Without the credential check the app offered a feature that failed on
    every use: the checkbox appeared, and every rebuttal quietly fell back.
    """
    if os.environ.get("RANDOSTATS_LLM", "").lower() in ("0", "false", "no", "off"):
        return False
    try:
        if not _has_credential(_get_client()):
            log.info("Claude rebuttals unavailable: no Anthropic credential found")
            return False
    except Exception as exc:  # noqa: BLE001 - never let a probe stop the app starting
        log.info("Claude rebuttals unavailable: %s", exc)
        return False
    return True


def sharpen(claim_text: str, counterpoints: list["Counterpoint"]) -> dict | None:
    """Ask Claude to choose among the matched facts and phrase the rebuttal.

    Returns None on any failure, and means it: the rule-based punchline is
    already on screen, so a missing credential, a refusal or a network blip
    must degrade quietly rather than fail the request.
    """
    if not counterpoints:
        return None

    facts = "\n".join(
        f"- id={cp.fact.id}: {cp.fact.statement} (source: {cp.fact.source}, {cp.fact.year}; gap from claim: {cp.gap} points)"
        for cp in counterpoints
    )
    prompt = f"Claim made in the argument: \"{claim_text}\"\n\nVerified facts to choose from:\n{facts}"
    try:
        response = _get_client().beta.messages.parse(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Rebuttal,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring; nothing here is worth a 500
        log.warning("Claude rebuttal failed, keeping the rule-based one: %s", exc)
        return None

    # A refusal carries no usable content, so read stop_reason first.
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return None
    parsed = response.parsed_output
    if not parsed.punchline.strip() or not parsed.logic_gap.strip():
        return None
    # Claude picks among the facts we matched; it never supplies its own.
    valid_ids = {cp.fact.id for cp in counterpoints}
    if parsed.fact_id not in valid_ids:
        parsed.fact_id = counterpoints[0].fact.id
    return {"fact_id": parsed.fact_id, "punchline": parsed.punchline.strip(),
            "logic_gap": parsed.logic_gap.strip(), "model": response.model}
