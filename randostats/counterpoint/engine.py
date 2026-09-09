"""Hear a statistic, answer with an equally true and equally irrelevant one.

The engine does three things:

1. ``extract_claims`` pulls quantitative claims out of free text. It
   understands "70%", "seventy percent", "1 in 5", "three out of four",
   "most people", "half of", and "X times more likely".
2. ``CounterpointEngine.match`` finds real, sourced facts of the same
   magnitude from ``facts.json``.
3. ``CounterpointEngine.respond`` renders the punchlines, plus a one-line
   note on the actual logical gap in the original claim.

Everything here is deterministic given a seed, and needs no network.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .packs import CORE_PATH as FACTS_PATH, DEFAULT_VOICE, load_facts, load_voice

# Percentage points within which two figures can fairly be called the same.
CLOSE_ENOUGH = 3.0

# ---------------------------------------------------------------------------
# Number words
# ---------------------------------------------------------------------------

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_UNITS["a"] = 1
_UNITS["an"] = 1
_NUMBER_WORD = r"(?:hundred|" + "|".join(list(_TENS) + [w for w in _UNITS if w not in ("a", "an")]) + r")"
_WORD_NUMBER = re.compile(rf"\b({_NUMBER_WORD}(?:[\s-]+{_NUMBER_WORD})*)\b", re.IGNORECASE)


def words_to_number(phrase: str) -> float | None:
    total = 0
    for tok in re.split(r"[\s-]+", phrase.strip().lower()):
        if tok in _UNITS:
            total += _UNITS[tok]
        elif tok in _TENS:
            total += _TENS[tok]
        elif tok == "hundred":
            total = (total or 1) * 100
        else:
            return None
    return float(total)


_DECIMAL_COMMA = re.compile(r"\d+,\d{1,2}")


def _numberish(s: str) -> float | None:
    """"12,5" is twelve and a half; "1,500" is fifteen hundred."""
    s = s.strip().lower()
    s = s.replace(",", ".") if _DECIMAL_COMMA.fullmatch(s) else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return words_to_number(s)


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

@dataclass
class Claim:
    kind: str          # "percent" | "ratio"
    value: float       # percent 0-100, or multiplier
    raw: str           # the text that triggered it
    subject: str       # best-effort clause after the number ("people drink beer")
    quantifier: str = "exact"  # "exact" | "vague" (most / almost everyone / few)

    @property
    def key(self) -> str:
        return hashlib.sha1(f"{self.kind}|{round(self.value, 1)}|{self.subject.lower()[:40]}".encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


_NUM = r"(?:\d+(?:[.,]\d+)?|" + _NUMBER_WORD + r"(?:[\s-]+" + _NUMBER_WORD + r")*)"
_SUBJECT_STOP = re.compile(r"[.!?;,]|\b(?:but|because|so|which|and|then|therefore)\b", re.IGNORECASE)

# Each pattern matches only the numeric core; the subject is whatever follows it up to a clause boundary.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("percent", re.compile(rf"\b(?P<num>{_NUM})\s*(?:%|percent\b|per cent\b|pct\b)", re.IGNORECASE)),
    ("percent", re.compile(r"(?P<num>\d+(?:[.,]\d+)?)\s*%")),
    ("in", re.compile(rf"\b(?P<a>{_NUM})\s+(?:in|out of)\s+(?:every\s+)?(?P<b>{_NUM})\b", re.IGNORECASE)),
    # "3 times more likely", and any comparative: older, longer, cheaper, faster.
    # The exclusions are words that merely end in -er ("3 times over", "per").
    ("ratio", re.compile(
        rf"\b(?P<num>{_NUM})\s*(?:x|times)\s+"
        r"(?!over\b|per\b|under\b|after\b|ever\b|never\b|other\b|either\b|whether\b|together\b"
        r"|however\b|rather\b|later\b|earlier\b)"
        r"(?:\w+er\b|more|less|as|the)\b", re.IGNORECASE)),
    ("twice", re.compile(r"\b(?P<word>twice|double|triple)\s+(?:as|the|more|likely)\b", re.IGNORECASE)),
]

_VAGUE: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\b(?:almost|nearly|basically|practically|virtually)\s+(?P<noun>everyone|everybody|all|every)\b", re.IGNORECASE), 95),
    (re.compile(r"\b(?:the\s+)?(?:vast\s+)?majority\s+of\b", re.IGNORECASE), 65),
    (re.compile(r"\bmost\s+(?P<noun>people|folks|guys|humans|americans|adults|men|women|kids|users)\b", re.IGNORECASE), 65),
    (re.compile(r"\bmost\s+of\b", re.IGNORECASE), 65),
    (re.compile(r"\b(?:about\s+|roughly\s+|around\s+)?half\s+(?:of|the)\b", re.IGNORECASE), 50),
    (re.compile(r"\b(?:a|one)[\s-]third\s+of\b", re.IGNORECASE), 33),
    (re.compile(r"\btwo[\s-]thirds\s+of\b", re.IGNORECASE), 67),
    (re.compile(r"\b(?:a|one)[\s-]quarter\s+of\b", re.IGNORECASE), 25),
    (re.compile(r"\bthree[\s-]quarters\s+of\b", re.IGNORECASE), 75),
    (re.compile(r"\b(?:hardly|barely)\s+(?P<noun>anyone|anybody)\b", re.IGNORECASE), 3),
    (re.compile(r"\b(?P<noun>nobody|no one)\b", re.IGNORECASE), 1),
]


def _tail(text: str, pos: int) -> str:
    """The clause following position ``pos``, cut at the next boundary."""
    rest = text[pos:]
    cut = _SUBJECT_STOP.search(rest)
    return rest[:cut.start()] if cut else rest


def _clean_subject(rest: str) -> str:
    rest = re.sub(r"^\s*(?:of\s+)?(?:all\s+|the\s+)?", "", rest, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", rest).strip(" ,.-")[:80]


# An answer is per claim, and nobody reads twenty rebuttals. A pasted article
# is otherwise thousands of claims, each costing a match, a render and (with
# --llm) a paid call.
MAX_CLAIMS = 20


def extract_claims(text: str, limit: int = MAX_CLAIMS) -> list[Claim]:
    claims: list[tuple[int, Claim]] = []
    # Which characters an earlier pattern has already claimed. A scan of the
    # spans recorded so far would be linear in the claims found, making the
    # whole extraction quadratic in the length of the text.
    covered = bytearray(len(text))

    def taken(m: re.Match) -> bool:
        return any(covered[m.start():m.end()])

    def add(m: re.Match, kind: str, value: float, quantifier: str = "exact", noun: str = "") -> None:
        tail = _tail(text, m.end())
        subject = _clean_subject(tail)
        if noun:
            subject = f"{noun} {subject}".strip()
        raw = re.sub(r"\s+", " ", text[m.start():m.end() + len(tail)]).strip(" ,.-")
        claims.append((m.start(), Claim(kind, value, raw, subject, quantifier)))
        covered[m.start():m.end()] = b"\x01" * (m.end() - m.start())

    for kind, pat in _PATTERNS:
        if len(claims) >= limit:
            break
        for m in pat.finditer(text):
            if len(claims) >= limit:
                break
            if taken(m):
                continue
            if kind == "percent":
                v = _numberish(m.group("num"))
                if v is not None and 0 <= v <= 100:
                    add(m, "percent", v)
            elif kind == "in":
                a, b = _numberish(m.group("a")), _numberish(m.group("b"))
                if a is not None and b is not None and 0 < a <= b:
                    add(m, "percent", round(100 * a / b, 1))
            elif kind == "ratio":
                v = _numberish(m.group("num"))
                if v is not None and v > 1:
                    add(m, "ratio", v)
            elif kind == "twice":
                add(m, "ratio", float({"twice": 2, "double": 2, "triple": 3}[m.group("word").lower()]))

    for pat, value in _VAGUE:
        if len(claims) >= limit:
            break
        for m in pat.finditer(text):
            if len(claims) >= limit:
                break
            if taken(m):
                continue
            noun = m.groupdict().get("noun") or ""
            add(m, "percent", float(value), quantifier="vague", noun=noun.lower())

    claims.sort(key=lambda c: c[0])
    return [c for _, c in claims]


# ---------------------------------------------------------------------------
# Matching & rendering
# ---------------------------------------------------------------------------

@dataclass
class Fact:
    id: str
    kind: str
    value: float
    statement: str
    short: str
    source: str
    year: int
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Counterpoint:
    claim: Claim
    fact: Fact
    lines: list[str]
    gap: float
    fallacy: str

    def to_dict(self) -> dict:
        return {"claim": self.claim.to_dict(), "fact": self.fact.to_dict(), "lines": self.lines,
                "gap": self.gap, "fallacy": self.fallacy}


class CounterpointEngine:
    def __init__(self, facts_path: Path | None = None, seed: int | None = None,
                 packs: set[str] | None = None, voice: str = DEFAULT_VOICE):
        if facts_path is not None:  # an explicit file wins, for tests and custom sets
            raw = json.loads(Path(facts_path).read_text(encoding="utf-8"))["facts"]
        else:
            raw = load_facts(packs)
        self.facts = [Fact(**{k: v for k, v in f.items() if k != "pack"}) for f in raw]
        self.packs = set(packs or ())
        self.voice = load_voice(voice)
        self.rng = random.Random(seed)
        self._recent: list[str] = []

    # -- matching -----------------------------------------------------------
    def _distance(self, claim: Claim, fact: Fact) -> float:
        if claim.kind != fact.kind:
            return float("inf")
        if claim.kind == "percent":
            return abs(claim.value - fact.value)
        # ratios: compare on a log scale so 2x vs 2.5x is "close" and 2x vs 300x is not
        import math
        return abs(math.log(claim.value) - math.log(fact.value)) * 10

    def match(self, claim: Claim, k: int = 3, exclude: set[str] | None = None) -> list[tuple[Fact, float]]:
        tol = 6.0 if claim.quantifier == "exact" else 12.0
        if claim.kind == "ratio":
            tol = 5.0
        scored = [(f, self._distance(claim, f)) for f in self.facts if not exclude or f.id not in exclude]
        scored = [s for s in scored if s[1] != float("inf")]
        scored.sort(key=lambda s: (s[1] > tol, s[0].id in self._recent, s[1] + self.rng.random() * 0.5))
        return scored[:k]

    # -- rendering ------------------------------------------------------------
    def _fmt(self, v: float) -> str:
        return f"{v:g}"

    def _render(self, claim: Claim, fact: Fact, gap: float) -> list[str]:
        subject = claim.subject or "your claim"
        subject_or_that = f"'{subject}'" if claim.subject else "that"
        ctx = {
            "fact": fact.statement,
            "fact_lc": fact.statement[0].lower() + fact.statement[1:],
            "short": fact.short,
            "subject": subject,
            "subject_or_that": subject_or_that,
            "gap": self._fmt(round(gap, 1)) + (" point" if round(gap, 1) == 1 else " points"),
            "claim_v": self._fmt(claim.value),
            "fact_v": self._fmt(fact.value),
        }
        if claim.kind == "ratio":
            pool = list(self.voice["ratio"])
        else:
            pool = list(self.voice["percent"])
            # Lines calling the two figures the same are only usable when they
            # nearly are. Each voice marks its own rather than the engine
            # guessing from the wording.
            if gap <= CLOSE_ENOUGH:
                pool += list(self.voice.get("percent_close") or [])
        self.rng.shuffle(pool)
        lines = [t.format(**ctx) for t in pool[:2]]
        lines.append(f"Source: {fact.source}, {fact.year}.")
        return lines

    def _fallacy(self, claim: Claim) -> str:
        key = "fallacy_ratio" if claim.kind == "ratio" else ("fallacy_vague" if claim.quantifier == "vague" else "fallacy_percent")
        return self.rng.choice(self.voice[key]).format(v=self._fmt(claim.value))

    def respond(self, text: str, per_claim: int = 2, seen: set[str] | None = None) -> list[Counterpoint]:
        """Return counterpoints for every new claim in ``text``.

        ``seen`` is a set of claim keys already answered (for live listening);
        it is updated in place.
        """
        out: list[Counterpoint] = []
        for claim in extract_claims(text):
            if seen is not None:
                if claim.key in seen:
                    continue
                seen.add(claim.key)
            used: set[str] = set()
            for fact, gap in self.match(claim, k=per_claim, exclude=used):
                used.add(fact.id)
                self._recent = (self._recent + [fact.id])[-12:]
                out.append(Counterpoint(claim, fact, self._render(claim, fact, gap), round(gap, 2), self._fallacy(claim)))
        return out

    def spurious_pair(self) -> dict:
        """Two unrelated facts with near-identical values, for the 'random correlation' button."""
        percents = [f for f in self.facts if f.kind == "percent"]
        best: list[tuple[float, Fact, Fact]] = []
        for i, a in enumerate(percents):
            for b in percents[i + 1:]:
                if "health" in a.tags or "health" in b.tags:
                    continue
                if set(a.tags) & set(b.tags) - {"us"}:
                    continue
                gap = abs(a.value - b.value)
                if gap <= 2.5:
                    best.append((gap, a, b))
        if not best:
            if len(percents) < 2:
                return {"a": None, "b": None, "gap": None,
                        "line": "Not enough percentages loaded to find a coincidence."}
            best = [(abs(percents[0].value - percents[1].value), percents[0], percents[1])]
        gap, a, b = self.rng.choice(best)
        return {
            "a": a.to_dict(), "b": b.to_dict(), "gap": round(gap, 2),
            "line": f"{a.statement}. {b.statement}. Gap: {gap:g} points. Clearly one causes the other.",
        }
