"""Optional Claude-powered punchlines.

The rule-based engine always runs. When an Anthropic credential is available
(``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile) and the app was
started with ``--llm`` (or ``RANDOSTATS_LLM=1``), this module asks Claude to
pick the sharpest of the *already matched, sourced* facts and phrase the
rebuttal. Claude is never asked to invent statistics, so every number that
reaches the user still traces back to ``facts.json``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .engine import Counterpoint

MODEL = os.environ.get("RANDOSTATS_MODEL", "claude-opus-5")

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


def available() -> bool:
    if os.environ.get("RANDOSTATS_LLM", "").lower() in ("0", "false", "no", "off"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def sharpen(claim_text: str, counterpoints: list["Counterpoint"]) -> dict | None:
    """Ask Claude to choose among the matched facts and phrase the rebuttal. Returns None on any failure."""
    if not counterpoints:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    facts = "\n".join(
        f"- id={cp.fact.id}: {cp.fact.statement} (source: {cp.fact.source}, {cp.fact.year}; gap from claim: {cp.gap} points)"
        for cp in counterpoints
    )
    prompt = f"Claim made in the argument: \"{claim_text}\"\n\nVerified facts to choose from:\n{facts}"
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Rebuttal,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIError:
        return None
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return None
    parsed = response.parsed_output
    valid_ids = {cp.fact.id for cp in counterpoints}
    if parsed.fact_id not in valid_ids:
        parsed.fact_id = counterpoints[0].fact.id
    return {"fact_id": parsed.fact_id, "punchline": parsed.punchline, "logic_gap": parsed.logic_gap, "model": response.model}
