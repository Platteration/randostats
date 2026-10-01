"""Hear a statistic, answer with an equally true and equally irrelevant one.

The engine does three things:

1. ``extract_claims`` pulls quantitative claims out of free text. It
   understands "70%", "seventy percent", "1 in 5", "three out of four",
   "most people", "half of", "X times more likely", ranges ("15 to 30
   times") and changes ("up 40%", "20% less likely").
2. ``CounterpointEngine.match`` finds real, sourced facts of the same
   magnitude from ``facts.json``.
3. ``CounterpointEngine.analyse`` answers in order of usefulness: a known
   myth is called a myth, a claim the facts cover is checked against the
   real figure, an appeal with no number in it gets asked for one, and
   only then come the absurd parallels, each with a note on the actual
   logical gap.

A **share** ("70% of people") and a **change** ("up 40%") are different
claims. A change is a multiplier on an unstated baseline, so it is matched
against ratio facts, never against shares.

Everything here is deterministic given a seed, and needs no network.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .packs import DEFAULT_VOICE, load_facts, load_myths, load_voice

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
    kind: str          # "percent" | "ratio" | "change"
    value: float       # percent 0-100, multiplier, or signed percentage change
    raw: str           # the text that triggered it
    subject: str       # best-effort clause after the number ("people drink beer")
    quantifier: str = "exact"  # "exact" | "vague" (most / almost everyone / few)
    core: str = ""     # just the quantity as written: "70%", "most people"
    start: int = 0     # offset in the text, so a myth can claim its sentence
    context: str = ""  # the whole clause, for finding what the claim is about
    band: tuple[float, float] | None = None  # the range a vague word honestly covers

    @property
    def key(self) -> str:
        return hashlib.sha1(f"{self.kind}|{round(self.value, 1)}|{self.subject.lower()[:40]}".encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("context")
        d["key"] = self.key
        return d


_NUM = r"(?:\d+(?:[.,]\d+)?|" + _NUMBER_WORD + r"(?:[\s-]+" + _NUMBER_WORD + r")*)"
# A number, or a range of two ("15 to 30", "20-30%"). A range is read at its midpoint.
_SPAN = rf"(?P<num>{_NUM})(?:\s*%?\s*(?:to|-|–|—)\s*(?P<hi>{_NUM}))?"
_PCT = r"\s*(?:%|percent\b|per cent\b|pct\b)"
_HEDGE = r"(?:(?:about|around|roughly|nearly|almost|over|some|more than|at least)\s+)?"
_SUBJECT_STOP = re.compile(r"[.!?;,]|\b(?:but|because|so|which|and|then|therefore)\b", re.IGNORECASE)

# Verbs of change. "cut" is left out: "cut 20% of staff" is a share.
_RISE = r"up|rose|risen|rises|rising|jumped|jumps|climbed|climbs|grew|grown|grows|soared|surged|spiked|increased|increases|increase|raised|raises|boosted|boosts"
_FALL = r"down|fell|fallen|falls|falling|dropped|drops|plunged|plummeted|slumped|declined|declines|decreased|decreases|decrease|reduced|reduces|lowered|lowers|shrank|shrunk"
_FALL_WORD = re.compile(rf"^(?:{_FALL}|less|fewer|lower|smaller|cheaper|slower|decrease|drop|fall|reduction|decline|cut|loss)$", re.IGNORECASE)

# Each pattern matches only the numeric core; the subject is whatever follows it up to a clause boundary.
# Order matters: a span taken by an earlier pattern is not read again by a later one.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # "from 20% to 30%" is a change of +50%, not two shares.
    ("from", re.compile(rf"\bfrom\s+(?P<a>{_NUM}){_PCT}?\s+to\s+(?P<b>{_NUM}){_PCT}", re.IGNORECASE)),
    # "up 40%", "fell by 20%", "increases your risk of cancer by 20%". Filler
    # words are only allowed before "by", so "up to 40%" stays a share.
    ("change", re.compile(
        rf"\b(?P<verb>{_RISE}|{_FALL})(?:(?:\s+[\w'’]+){{0,6}}?\s+by)?\s+{_HEDGE}{_SPAN}{_PCT}", re.IGNORECASE)),
    # "20% less likely", "a 30% increase".
    ("change", re.compile(
        rf"\b{_SPAN}{_PCT}\s+(?P<verb>more|less|fewer|higher|lower|greater|bigger|smaller|cheaper|faster|slower"
        r"|increase|decrease|rise|drop|fall|jump|reduction|decline|boost|cut|gain|loss)\b", re.IGNORECASE)),
    ("percent", re.compile(rf"\b{_SPAN}{_PCT}", re.IGNORECASE)),
    ("percent", re.compile(r"(?P<num>\d+(?:[.,]\d+)?)(?:\s*%?\s*(?:to|-|–|—)\s*(?P<hi>\d+(?:[.,]\d+)?))?\s*%")),
    ("in", re.compile(rf"\b(?P<a>{_NUM})\s+(?:in|out of)\s+(?:every\s+)?(?P<b>{_NUM})\b", re.IGNORECASE)),
    # "3 times more likely", and any comparative: older, longer, cheaper, faster.
    # The exclusions are words that merely end in -er ("3 times over", "per").
    ("ratio", re.compile(
        rf"\b{_SPAN}\s*(?:x|times)\s+"
        r"(?!over\b|per\b|under\b|after\b|ever\b|never\b|other\b|either\b|whether\b|together\b"
        r"|however\b|rather\b|later\b|earlier\b)"
        r"(?:\w+er\b|more|less|as|the)\b", re.IGNORECASE)),
    # "increases your risk by 15 to 30 times", "by a factor of 3", "a threefold rise".
    ("ratio", re.compile(rf"\bby\s+{_HEDGE}{_SPAN}\s*(?:x|times|fold)\b", re.IGNORECASE)),
    ("ratio", re.compile(rf"\bby\s+a\s+factor\s+of\s+{_HEDGE}{_SPAN}", re.IGNORECASE)),
    ("ratio", re.compile(rf"\b{_SPAN}[\s-]?fold\b", re.IGNORECASE)),
    ("twice", re.compile(r"\b(?P<word>twice|double|triple)\s+(?:as|the|more|likely)\b", re.IGNORECASE)),
]

# "I'm 100% sure" and "90 percent certain" are about the speaker, not the world.
_CONFIDENCE = re.compile(
    r"\s*(?:sure|certain|confident|positive|convinced|serious|honest|correct|right|committed|behind)\b",
    re.IGNORECASE)

# (pattern, point value, the band the word honestly covers)
_VAGUE: list[tuple[re.Pattern, float, tuple[float, float]]] = [
    (re.compile(r"\b(?:almost|nearly|basically|practically|virtually)\s+(?P<noun>everyone|everybody|all|every)\b", re.IGNORECASE), 95, (85, 100)),
    (re.compile(r"\b(?:the\s+)?(?:vast\s+)?majority\s+of\b", re.IGNORECASE), 65, (50.5, 100)),
    (re.compile(r"\bmost\s+(?P<noun>people|folks|guys|humans|americans|adults|men|women|kids|users)\b", re.IGNORECASE), 65, (50.5, 100)),
    (re.compile(r"\bmost\s+of\b", re.IGNORECASE), 65, (50.5, 100)),
    (re.compile(r"\b(?:about\s+|roughly\s+|around\s+)?half\s+(?:of|the)\b", re.IGNORECASE), 50, (42, 58)),
    (re.compile(r"\b(?:a|one)[\s-]third\s+of\b", re.IGNORECASE), 33, (28, 38)),
    (re.compile(r"\btwo[\s-]thirds\s+of\b", re.IGNORECASE), 67, (61, 72)),
    (re.compile(r"\b(?:a|one)[\s-]quarter\s+of\b", re.IGNORECASE), 25, (20, 30)),
    (re.compile(r"\bthree[\s-]quarters\s+of\b", re.IGNORECASE), 75, (70, 80)),
    (re.compile(r"\b(?:hardly|barely)\s+(?P<noun>anyone|anybody)\b", re.IGNORECASE), 3, (0, 5)),
    (re.compile(r"\b(?P<noun>nobody|no one)\b", re.IGNORECASE), 1, (0, 1)),
]


def _tail(text: str, pos: int) -> str:
    """The clause following position ``pos``, cut at the next boundary."""
    rest = text[pos:]
    cut = _SUBJECT_STOP.search(rest)
    return rest[:cut.start()] if cut else rest


def _head(text: str, pos: int, stops: list[int] | None = None) -> str:
    """The clause leading up to position ``pos``, from the last boundary.

    ``stops`` is the end of every boundary in the whole of ``text``, found once
    by the caller: scanning the prefix again for every claim made extraction
    quadratic in the length of the text. A boundary that ends before ``pos`` is
    the same match in the prefix as in the whole text. The prefix can have one
    more, ending exactly at ``pos`` where its ``\\b`` meets the cut ("and" in
    "andy"), and no boundary is longer than 16 characters, so only those last
    few are scanned again as the prefix itself would see them.
    """
    if stops is None:
        stops = [m.end() for m in _SUBJECT_STOP.finditer(text)]
    i = bisect.bisect_left(stops, pos) - 1
    last = stops[i] if i >= 0 else 0
    for m in _SUBJECT_STOP.finditer(text, max(last, pos - 16), pos):
        last = m.end()
    return text[last:pos]


def _clean_subject(rest: str) -> str:
    rest = re.sub(r"^\s*(?:of\s+)?(?:all\s+|the\s+)?", "", rest, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", rest).strip(" ,.-")[:80]


def _span_value(m: re.Match) -> float | None:
    """The number a match stands for: the midpoint, if it was a range."""
    low = _numberish(m.group("num"))
    high = m.groupdict().get("hi")
    if low is None or high is None:
        return low
    high = _numberish(high)
    return None if high is None else round((low + high) / 2, 2)


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
    stops = [m.end() for m in _SUBJECT_STOP.finditer(text)]

    def cover(m: re.Match) -> None:
        covered[m.start():m.end()] = b"\x01" * (m.end() - m.start())

    # The whole reach of each numeric claim. "0.1% of people control most of
    # the wealth" is one claim; the "most of" inside it is not a second.
    reaches: list[tuple[int, int]] = []

    def taken(m: re.Match) -> bool:
        return any(covered[m.start():m.end()])

    def add(m: re.Match, kind: str, value: float, quantifier: str = "exact", noun: str = "",
            band: tuple[float, float] | None = None) -> None:
        tail = _tail(text, m.end())
        subject = _clean_subject(tail)
        if noun:
            subject = f"{noun} {subject}".strip()
        raw = re.sub(r"\s+", " ", text[m.start():m.end() + len(tail)]).strip(" ,.-")
        context = re.sub(r"\s+", " ", _head(text, m.start(), stops) + text[m.start():m.end() + len(tail)]).strip(" ,.-")
        core = re.sub(r"\s+", " ", m.group(0)).strip()
        if kind == "change":
            raw = context  # "up 40%" means nothing without what went up
        claims.append((m.start(), Claim(kind, value, raw, subject, quantifier, core, m.start(), context, band)))
        cover(m)
        if quantifier == "exact":
            reaches.append((m.start(), m.end() + len(tail)))

    for kind, pat in _PATTERNS:
        if len(claims) >= limit:
            break
        for m in pat.finditer(text):
            if len(claims) >= limit:
                break
            if taken(m):
                continue
            if kind == "from":
                a, b = _numberish(m.group("a")), _numberish(m.group("b"))
                if a and b is not None and a != b:
                    add(m, "change", round(100 * (b - a) / a, 1))
            elif kind == "change":
                v = _span_value(m)
                if v:
                    add(m, "change", -v if _FALL_WORD.match(m.group("verb")) else v)
            elif kind == "percent":
                v = _span_value(m)
                if v is None or not 0 <= v <= 100:
                    continue
                after = text[m.end():]
                # A bare 100% is an intensifier ("I'm 100% with you") unless it is a share of something.
                if _CONFIDENCE.match(after) or (v >= 100 and not re.match(r"\s*of\b", after, re.IGNORECASE)):
                    cover(m)
                    continue
                add(m, "percent", v)
            elif kind == "in":
                a, b = _numberish(m.group("a")), _numberish(m.group("b"))
                if a is not None and b is not None and 0 < a <= b:
                    add(m, "percent", round(100 * a / b, 1))
            elif kind == "ratio":
                v = _span_value(m)
                if v is not None and v > 1:
                    add(m, "ratio", v)
            elif kind == "twice":
                add(m, "ratio", float({"twice": 2, "double": 2, "triple": 3}[m.group("word").lower()]))

    for pat, value, band in _VAGUE:
        if len(claims) >= limit:
            break
        for m in pat.finditer(text):
            if len(claims) >= limit:
                break
            if taken(m) or any(s < m.start() < e for s, e in reaches):
                continue
            noun = m.groupdict().get("noun") or ""
            add(m, "percent", float(value), quantifier="vague", noun=noun.lower(), band=band)

    claims.sort(key=lambda c: c[0])
    return [c for _, c in claims]



# ---------------------------------------------------------------------------
# Fact-checking: what a claim is about
# ---------------------------------------------------------------------------

# Which facts can check a claim on which subject. Hand-written on purpose: a
# wrong fact-check is far worse than none, so a topic only lists facts that
# measure the same thing the words describe. Where framing differs ("have a
# credit card" vs "credit card interest"), the narrower pattern comes first.
# Patterns are matched against the claim's whole clause, lower-cased.
_TOPICS: list[tuple[str, list[str]]] = [
    (r"\b(?:clean|safe) (?:drinking )?water\b", ["no-safe-water"]),
    (r"(?=.*\b(?:earth|planet|globe|surface)\b)(?=.*\b(?:water|oceans?|seas?)\b)", ["earth-water"]),
    (r"(?=.*\bbod(?:y|ies)\b)(?=.*\bwater\b)", ["body-water"]),
    (r"\bfresh ?water\b", ["fresh-water"]),
    (r"\b(?:beer|wine|alcohol|booze|liquor|spirits)\b|\bdrinks? alcohol", ["alcohol-us"]),
    (r"\bbinge", ["binge-us"]),
    (r"\bcoffee\b", ["coffee-us"]),
    (r"\b(?:sodas?|fizzy drinks?|soft drinks?|sugary drinks?|sugar-sweetened)\b", ["sugary-drink-us"]),
    (r"\b(?:sodium|too much salt)\b", ["sodium-us"]),
    (r"\b(?:vegetables|veggies)\b", ["veg-us"]),
    (r"\bfruits?\b", ["fruit-us"]),
    (r"\bfast[\s-]?food\b", ["fast-food-us"]),
    (r"\bpizza\b", ["pizza-us"]),
    (r"\bobes(?:e|ity)\b", ["obese-us"]),
    (r"\boverweight\b", ["overweight-us"]),
    (r"\b(?:high blood pressure|hypertension)\b", ["hypertension-us"]),
    (r"\bdiabet", ["diabetes-us"]),
    (r"\bmental(?:ly)? ill", ["mental-us"]),
    (r"\bchronic pain\b", ["chronic-pain-us"]),
    (r"\bsleep\w*\b.*\b(?:less than|under|fewer than)\b", ["sleep-us"]),
    (r"\b(?:smok\w*|cigarettes?)\b", ["smoke-us"]),
    (r"\btattoo", ["tattoo-us"]),
    (r"\b(?:wear|need|have)\s+(?:eye)?glasses\b", ["glasses-us"]),
    (r"\b(?:own|have|has|keep)\s+(?:a\s+)?pets?\b|\bpet owners?\b", ["pets-us"]),
    (r"\b(?:own|have|has)\s+(?:a\s+|at least one\s+)?(?:cars?|vehicles?)\b", ["car-us"]),
    (r"\bhomeowners?\b|\bown (?:a |their |his |her )?(?:home|house)s?\b", ["homeowner-us"]),
    (r"\b(?:own|have|has)\s+(?:a\s+)?(?:tvs?|televisions?)\b", ["tv-us"]),
    (r"\bsmartphones?\b", ["smartphone-us"]),
    (r"\b(?:use|uses|on)\s+(?:the\s+)?internet\b|\bgo(?:es)? online\b", ["internet-us", "internet-world"]),
    (r"\bsocial media\b", ["social-world"]),
    (r"\bfacebook\b", ["facebook-us"]),
    (r"\byoutube\b", ["youtube-us"]),
    (r"\bmarried\b", ["married-us"]),
    (r"\bchristians?\b", ["christian-us"]),
    (r"\bread\w*\b.*\bbooks?\b", ["book-us", "no-book-us"]),
    (r"\bpassports?\b", ["passport-us"]),
    (r"\bleft[\s-]?hand\w*|\blefties\b", ["left-handed"]),
    (r"\bright[\s-]?hand\w*", ["right-handed"]),
    (r"(?=.*\bcolou?r[\s-]?blind)(?=.*\bwomen\b)", ["colorblind-women"]),
    (r"(?=.*\bcolou?r[\s-]?blind)(?=.*\bmen\b)", ["colorblind-men"]),
    (r"\b(?:red hair|redheads?|gingers?)\b", ["red-hair"]),
    (r"\btwins\b", ["twins"]),
    (r"\bautis", ["autism"]),
    (r"\bspeak english\b|\benglish speakers?\b", ["english"]),
    (r"\bbreast cancer\b", ["breast-cancer"]),
    (r"\bdisab", ["disability"]),
    (r"\b(?:hunger|hungry|starv\w*)\b", ["hunger"]),
    (r"\b(?:migrants?|immigrants?)\b", ["migrants"]),
    (r"\blives? in (?:a )?(?:cities|city|urban)|\burban\b", ["urban-world", "urban-us"]),
    (r"\bforests?\b", ["forest"]),
    (r"\boxygen\b", ["oxygen"]),
    (r"\bnitrogen\b", ["nitrogen"]),
    (r"\bextinct\b", ["extinct"]),
    (r"\bchimp", ["dna-chimp"]),
    (r"\bdark matter\b", ["dark-matter"]),
    (r"\bdark energy\b", ["dark-energy"]),
    (r"(?=.*\$?\b400\b)(?=.*\bemergenc)", ["mn-400"]),
    (r"\bcredit cards?\b.*\b(?:interest|apr|rate)\b", ["mn-apr"]),
    (r"\b(?:have|has|own|use)\s+(?:a\s+)?credit cards?\b", ["mn-card"]),
    (r"\bretirement sav", ["mn-retirement"]),
    (r"\bfree throws?\b", ["sp-ft"]),
    (r"\bpenalt(?:y|ies)\b", ["sp-pen"]),
    (r"\b(?:three|3)[\s-]point|\bthrees\b", ["sp-3pt"]),
    (r"\bfield goals?\b", ["sp-fg"]),
    (r"\bfirst serves?\b", ["sp-serve"]),
    (r"\bprisoners?\b|\bprison population\b|\bincarcerat", ["prisoners-us"]),
    (r"\b(?:hispanic|latino)", ["hispanic-us"]),
]
_TOPIC_PATTERNS = [(re.compile(p), ids) for p, ids in _TOPICS]

_NEGATION = re.compile(
    r"\b(?:not|no|never|none|without|lacks?|lacking|cannot|can'?t|don'?t|doesn'?t|didn'?t|won'?t"
    r"|haven'?t|hasn'?t|isn'?t|aren'?t)\b|n['’]t\b", re.IGNORECASE)
_AMERICAN = re.compile(r"\bamericans?\b|\bamerica\b|\bUSA?\b|\bU\.S\.", re.IGNORECASE)
_WORLD = re.compile(r"\b(?:world|global(?:ly)?|worldwide|earth|planet|humanity|mankind)\b", re.IGNORECASE)
# Anyone the facts do not measure: checking them against Americans would be a lie.
_ELSEWHERE = re.compile(
    r"\b(?:brits?|british|uk|britain|england|english people|scots?|scottish|welsh|irish|ireland|canad\w*"
    r"|australi\w*|new zealand\w*|kiwis|europe\w*|eu|french|france|germans?|germany|spain|spanish|itali\w*"
    r"|dutch|swed\w*|norw\w*|danes|danish|finn\w*|japan\w*|chin\w*|indians?|india|mexic\w*|brazil\w*"
    r"|russian?s?|africa\w*|asia\w*|londoners?|parisians?)\b", re.IGNORECASE)


def _negated(text: str) -> bool:
    return bool(_NEGATION.search(text))


# Appeals with no number in them. Each gets a question rather than a parallel.
_APPEALS: list[tuple[str, re.Pattern, str]] = [
    ("proof", re.compile(
        r"\b(?:it'?s|it is|it has been|that'?s|this is)\s+(?:been\s+)?(?:scientifically\s+|clinically\s+)?proven\b"
        r"|\b(?:a\s+)?(?:known|proven|scientific)\s+fact\b|\bthe science is settled\b", re.IGNORECASE),
     "Proven how?"),
    ("authority", re.compile(
        r"\b(?:studies|a study|research|science|scientists|experts|doctors|economists|statistics)\s+"
        r"(?:have\s+|has\s+)?(?:show|shows|showed|shown|say|says|said|prove|proves|proved|proven|found|finds"
        r"|agree|agrees|suggest|suggests|confirm|confirms)\b", re.IGNORECASE), "Which study?"),
    ("popularity", re.compile(
        r"\b(?:everyone|everybody)\s+knows\b|\bcommon\s+(?:knowledge|sense)\b|\bno\s+one\s+(?:disputes|disagrees)\b",
        re.IGNORECASE), "Everyone who?"),
    ("count", re.compile(
        r"\b(?:hundreds|thousands|millions|billions|tens of thousands)\s+of\s+"
        r"(?:people|americans|users|kids|children|lives|deaths|cases|women|men|families|dollars)\b",
        re.IGNORECASE), "Out of how many?"),
]


def _sentence_at(text: str, pos: int) -> str:
    start = max(text.rfind(c, 0, pos) for c in ".!?\n") + 1
    ends = [i for i in (text.find(c, pos) for c in ".!?\n") if i != -1]
    return re.sub(r"\s+", " ", text[start:min(ends) if ends else len(text)]).strip()[:160]

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
    proper: bool = False  # starts with a proper noun, so it keeps its capital mid-sentence

    @property
    def statement_lc(self) -> str:
        """The statement as it reads after "By that math, ": "the Sun", but "Russia" and "NBA"."""
        first = self.statement.split(" ", 1)[0]
        if self.proper or (len(first) > 1 and first.isupper()):
            return self.statement
        return self.statement[0].lower() + self.statement[1:]

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


def _multiplier(claim: Claim) -> float:
    """A change as the factor it implies, folded above 1: "20% less" is 1.25x
    in the other direction, which is the size of claim it is."""
    if claim.kind != "change":
        return claim.value
    factor = max(1 + claim.value / 100, 0.01)
    return max(factor, 1 / factor)


class CounterpointEngine:
    def __init__(self, facts_path: Path | None = None, seed: int | None = None,
                 packs: set[str] | None = None, voice: str = DEFAULT_VOICE):
        if facts_path is not None:  # an explicit file wins, for tests and custom sets
            raw = json.loads(Path(facts_path).read_text(encoding="utf-8"))["facts"]
        else:
            raw = load_facts(packs)
        self.facts = [Fact(**{k: v for k, v in f.items() if k != "pack"}) for f in raw]
        self._by_id = {f.id: f for f in self.facts}
        self.myths = [{**m, "_patterns": [re.compile(p, re.IGNORECASE) for p in m["patterns"]]}
                      for m in load_myths(packs)]
        self.packs = set(packs or ())
        self.voice = load_voice(voice)
        self.rng = random.Random(seed)
        self._recent: list[str] = []

    # -- matching -----------------------------------------------------------
    def _distance(self, claim: Claim, fact: Fact) -> float:
        if claim.kind == "percent":
            return abs(claim.value - fact.value) if fact.kind == "percent" else float("inf")
        if fact.kind != "ratio":
            return float("inf")
        # ratios: compare on a log scale so 2x vs 2.5x is "close" and 2x vs 300x is not
        return abs(math.log(_multiplier(claim)) - math.log(fact.value)) * 10

    def match(self, claim: Claim, k: int = 3, exclude: set[str] | None = None) -> list[tuple[Fact, float]]:
        tol = 6.0 if claim.quantifier == "exact" else 12.0
        if claim.kind in ("ratio", "change"):
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
            "fact_lc": fact.statement_lc,
            "short": fact.short,
            "subject": subject,
            "subject_or_that": subject_or_that,
            "gap": self._fmt(round(gap, 1)) + (" point" if round(gap, 1) == 1 else " points"),
            "claim_v": self._fmt(claim.value),
            "fact_v": self._fmt(fact.value),
        }
        if claim.kind in ("ratio", "change"):
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
        key = {"ratio": "fallacy_ratio", "change": "fallacy_change"}.get(claim.kind) or (
            "fallacy_vague" if claim.quantifier == "vague" else "fallacy_percent")
        return self._line(key, v=self._fmt(abs(claim.value)),
                          direction="fall" if claim.value < 0 else "rise")

    def _line(self, key: str, **values) -> str:
        return self.rng.choice(self.voice[key]).format(**values)

    # -- answering before the parallels ----------------------------------------
    def myths_in(self, text: str) -> list[tuple[dict, tuple[int, int]]]:
        """Known myths, each with the sentence it was found in."""
        hits = []
        for sentence in re.finditer(r"[^.!?\n]+[.!?]*", text):
            for myth in self.myths:
                if all(p.search(sentence.group(0)) for p in myth["_patterns"]):
                    hits.append((myth, sentence.span()))
                    break  # one myth per sentence: two cards for one sentence is noise
        return hits

    def myth_verdict(self, myth: dict, said: str) -> dict:
        return {"kind": "myth", "key": f"myth:{myth['id']}", "claim": said, "title": "That's a myth.",
                "line": f"{myth['truth']}.", "source": myth["source"], "year": myth.get("year"),
                "note": self._line("fallacy_myth"), "myth": myth["claim"]}

    def check(self, claim: Claim) -> dict | None:
        """The real figure for what the claim is about, when the facts have it.

        Only a percentage can be checked, and only against a fact on the same
        subject, for the same population, framed the same way round. When any
        of that is in doubt this returns None: silence beats a wrong verdict.
        """
        if claim.kind != "percent":
            return None
        about = (claim.context or claim.subject)
        lowered = about.lower()
        negated = _negated(about)
        for pattern, ids in _TOPIC_PATTERNS:
            if not pattern.search(lowered):
                continue
            candidates = [self._by_id[i] for i in ids
                          if i in self._by_id and self._by_id[i].kind == "percent"
                          and _negated(self._by_id[i].statement) == negated]
            fact = self._same_population(about, candidates)
            if fact:
                return self._verdict(claim, fact)
            return None  # the topic was right but no fact fits: do not try a looser one
        return None

    @staticmethod
    def _same_population(about: str, facts: list[Fact]) -> Fact | None:
        if _ELSEWHERE.search(about):
            return None
        american = [f for f in facts if "us" in f.tags or f.id.endswith("-us")]
        world = [f for f in facts if f not in american]
        if _AMERICAN.search(about):
            return american[0] if american else None
        if _WORLD.search(about):
            return world[0] if world else None
        return (world or american or [None])[0]

    def _verdict(self, claim: Claim, fact: Fact) -> dict:
        delta = round(claim.value - fact.value, 1)
        gap = abs(delta)
        said = self._fmt(claim.value)
        if claim.quantifier == "vague" and claim.band:
            low, high = claim.band
            fits = low <= fact.value <= high
            word = re.sub(r"\s+(?:of|the)$", "", claim.core, flags=re.IGNORECASE)
            word = word[:1].lower() + word[1:]  # "Most Americans" at the start of a sentence
            verdict = "fair" if fits else "off"
            title = "Fair enough." if fits else "Not really."
            judged = "holds up" if fits else ("is too low" if fact.value > high else "is too high")
            line = f"{fact.statement}, so “{word}” {judged}."
        else:
            direction = "high" if delta > 0 else "low"
            points = f"{self._fmt(gap)} point{'' if gap == 1 else 's'} {direction}"
            if gap <= CLOSE_ENOUGH:
                verdict, title, line = "fair", "That checks out.", f"{fact.statement}. You said {said}%, which is fair."
            elif gap <= 10:
                verdict, title, line = "close", f"Close, but {direction}.", f"{fact.statement}. You said {said}%: {points}."
            else:
                verdict, title, line = "off", "Not quite.", f"{fact.statement}. You said {said}%: {points}."
        return {"kind": "check", "key": claim.key, "claim": claim.raw, "title": title, "line": line,
                "source": fact.source, "year": fact.year, "verdict": verdict, "delta": delta,
                "said": claim.value, "real": fact.value, "fact": fact.to_dict(), "note": self._fallacy(claim)}

    def appeals(self, text: str) -> list[dict]:
        """"Studies show", "everyone knows", "millions of people": no number to
        check, but always something worth asking."""
        out, sentences = [], set()
        for kind, pattern, title in _APPEALS:
            m = pattern.search(text)
            if m and _sentence_at(text, m.start()) not in sentences:
                sentences.add(_sentence_at(text, m.start()))
                out.append({"kind": "appeal", "key": f"appeal:{kind}", "claim": _sentence_at(text, m.start()),
                            "title": title, "line": self._line(f"appeal_{kind}"), "source": None, "year": None,
                            "note": None, "start": m.start()})
        return out

    def analyse(self, text: str, per_claim: int = 2,
                seen: set[str] | None = None) -> tuple[list[dict], list[Counterpoint]]:
        """Verdicts first, then the parallels.

        Verdicts are, in order: myths, fact-checks, and appeals to authority or
        popularity. A claim inside a sentence that holds a myth gets no parallel:
        the answer to a myth is that it is false, not an equally irrelevant number.
        ``seen`` works as in ``respond``, for verdicts too.
        """
        def fresh(key: str) -> bool:
            if seen is None:
                return True
            if key in seen:
                return False
            seen.add(key)
            return True

        myths = self.myths_in(text)
        mythic = [span for _, span in myths]
        in_myth = lambda pos: any(a <= pos < b for a, b in mythic)  # noqa: E731

        verdicts = [self.myth_verdict(myth, text[a:b].strip()) for myth, (a, b) in myths
                    if fresh(f"myth:{myth['id']}")]
        results: list[Counterpoint] = []
        for claim in extract_claims(text):
            if in_myth(claim.start) or not fresh(claim.key):
                continue
            checked = self.check(claim)
            if checked:
                verdicts.append(checked)
            used: set[str] = set()
            for fact, gap in self.match(claim, k=per_claim, exclude=used):
                if checked and fact.id == checked["fact"]["id"]:
                    continue  # the real figure is not also its own absurd parallel
                used.add(fact.id)
                self._recent = (self._recent + [fact.id])[-12:]
                results.append(Counterpoint(claim, fact, self._render(claim, fact, gap), round(gap, 2), self._fallacy(claim)))
        for appeal in self.appeals(text):
            start = appeal.pop("start")
            if not in_myth(start) and fresh(appeal["key"]):
                verdicts.append(appeal)
        return verdicts, results

    def respond(self, text: str, per_claim: int = 2, seen: set[str] | None = None) -> list[Counterpoint]:
        """Return counterpoints for every new claim in ``text``.

        ``seen`` is a set of claim keys already answered (for live listening);
        it is updated in place.
        """
        return self.analyse(text, per_claim=per_claim, seen=seen)[1]

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
