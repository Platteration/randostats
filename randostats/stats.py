"""Statistics over a list of ``Message`` objects.

Every function is pure: it takes messages and returns JSON-serialisable
dicts, so the API layer and the tests can share them.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median
from typing import Iterable

from .models import Message

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*[A-Za-z]|[A-Za-z]")
_URL = re.compile(r"https?://\S+|www\.\S+")
_MENTION = re.compile(r"[@#]\w+")
_MEDIA_PLACEHOLDERS = ("<media omitted>", "image omitted", "video omitted", "audio omitted", "sticker omitted",
                       "gif omitted", "document omitted", "‎image omitted", "this message was deleted",
                       "you deleted this message", "<attached:")

# Text-speak and interjections that a dictionary calls misspellings but a
# human does not. Anything here is never reported.
SLANG = set("""
lol lmao lmfao rofl omg omfg wtf brb btw idk idc ikr imo imho tbh smh fyi ttyl thx ty np nvm
ok okay okie kk yea yeah yep yup nah nope ya yall y'all gonna wanna gotta kinda sorta dunno lemme
gimme ain't cuz coz cos bc pls plz thru tho ur u r ya'll rn asap omw ily ilu jk fr ngl irl dm dms
pic pics vid vids app apps lil bro sis fam bae bff bestie dude yo hey heya hiya sup wassup whats
hmm hmmm mmm mm umm uh uhh ugh ooh oooh ahh ahhh aww awww ew eww eh meh huh hah haha hahaha hehe
lolol lool xd xoxo xo omgg okk okkk yess yesss noo nooo sooo soo ok ok okey wtv whatev totes obvi
prob probs def defo deffo tmr tmrw tmw tonite gr8 l8r b4 w/ w/o vs etc ppl msg msgs txt txts
selfie selfies emoji emojis meme memes vibe vibes vibin lit fam goat sus cap bussin bet
covid wifi iphone android whatsapp facebook instagram tiktok snapchat youtube netflix uber
venmo paypal zoom spotify google gmail amazon reddit twitter discord
""".split())


def _sanitise(text: str) -> str:
    text = _URL.sub(" ", text)
    text = _MENTION.sub(" ", text)
    return text


def is_media_placeholder(text: str) -> bool:
    t = text.strip().lower()
    return any(p in t for p in _MEDIA_PLACEHOLDERS)


def words_of(text: str) -> list[str]:
    return [w for w in _WORD.findall(_sanitise(text))]


# ---------------------------------------------------------------------------
# Overview & per-contact frequency
# ---------------------------------------------------------------------------

def overview(messages: list[Message]) -> dict:
    if not messages:
        return {"total": 0, "sent": 0, "received": 0, "contacts": 0, "first": None, "last": None, "days": 0}
    sent = sum(1 for m in messages if m.direction == "sent")
    first = min(m.timestamp for m in messages)
    last = max(m.timestamp for m in messages)
    days = (last.date() - first.date()).days + 1
    return {
        "total": len(messages),
        "sent": sent,
        "received": len(messages) - sent,
        "contacts": len({m.contact for m in messages}),
        "first": first.isoformat(),
        "last": last.isoformat(),
        "days": days,
        "per_day": round(len(messages) / days, 2),
    }


def contact_frequency(messages: list[Message], limit: int | None = None) -> list[dict]:
    """Messages per contact, split by direction, sorted by total desc."""
    by_contact: dict[str, dict] = defaultdict(lambda: {"sent": 0, "received": 0, "words_sent": 0, "words_received": 0,
                                                       "first": None, "last": None, "senders": Counter()})
    for m in messages:
        c = by_contact[m.contact]
        c[m.direction] += 1
        c["words_" + m.direction] += len(words_of(m.text))
        c["first"] = m.timestamp if c["first"] is None or m.timestamp < c["first"] else c["first"]
        c["last"] = m.timestamp if c["last"] is None or m.timestamp > c["last"] else c["last"]
        if m.direction == "received":
            c["senders"][m.sender] += 1
    rows = []
    for name, c in by_contact.items():
        total = c["sent"] + c["received"]
        days = (c["last"].date() - c["first"].date()).days + 1
        rows.append({
            "contact": name,
            "total": total,
            "sent": c["sent"],
            "received": c["received"],
            "sent_share": round(c["sent"] / total, 3) if total else 0,
            "avg_words_sent": round(c["words_sent"] / c["sent"], 1) if c["sent"] else 0,
            "avg_words_received": round(c["words_received"] / c["received"], 1) if c["received"] else 0,
            "first": c["first"].isoformat(),
            "last": c["last"].isoformat(),
            "active_days": days,
            "per_day": round(total / days, 2),
            "is_group": len(c["senders"]) > 1,
            "members": [s for s, _ in c["senders"].most_common()],
        })
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows[:limit] if limit else rows


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

def timing(messages: list[Message], contact: str | None = None) -> dict:
    """When messages happen: hour-of-day, weekday, a 7x24 heatmap, monthly volume, and reply latency."""
    msgs = [m for m in messages if contact is None or m.contact == contact]
    if not msgs:
        return {"by_hour": [], "by_weekday": [], "heatmap": [], "by_month": [], "busiest_day": None,
                "reply_latency": None, "peak_hour": None, "peak_weekday": None}

    hour = {"sent": [0] * 24, "received": [0] * 24}
    weekday = {"sent": [0] * 7, "received": [0] * 7}
    heat = [[0] * 24 for _ in range(7)]
    month: Counter = Counter()
    day: Counter = Counter()
    for m in msgs:
        h, wd = m.timestamp.hour, m.timestamp.weekday()
        hour[m.direction][h] += 1
        weekday[m.direction][wd] += 1
        heat[wd][h] += 1
        month[m.timestamp.strftime("%Y-%m")] += 1
        day[m.timestamp.date().isoformat()] += 1

    totals_h = [hour["sent"][i] + hour["received"][i] for i in range(24)]
    totals_w = [weekday["sent"][i] + weekday["received"][i] for i in range(7)]
    busiest = day.most_common(1)[0] if day else None
    return {
        "by_hour": [{"hour": i, "sent": hour["sent"][i], "received": hour["received"][i]} for i in range(24)],
        "by_weekday": [{"weekday": WEEKDAYS[i], "sent": weekday["sent"][i], "received": weekday["received"][i]} for i in range(7)],
        "heatmap": heat,
        "by_month": [{"month": k, "count": v} for k, v in sorted(month.items())],
        "busiest_day": {"date": busiest[0], "count": busiest[1]} if busiest else None,
        "peak_hour": max(range(24), key=lambda i: totals_h[i]),
        "peak_weekday": WEEKDAYS[max(range(7), key=lambda i: totals_w[i])],
        "reply_latency": reply_latency(msgs),
    }


def reply_latency(messages: list[Message], max_gap_hours: float = 12.0) -> dict | None:
    """Median minutes between a received message and the user's next reply, and vice versa.

    Gaps longer than ``max_gap_hours`` are treated as a new conversation, not a reply.
    """
    by_contact: dict[str, list[Message]] = defaultdict(list)
    for m in messages:
        by_contact[m.contact].append(m)
    mine: list[float] = []
    theirs: list[float] = []
    cutoff = timedelta(hours=max_gap_hours)
    for msgs in by_contact.values():
        msgs.sort(key=lambda m: m.timestamp)
        for prev, cur in zip(msgs, msgs[1:]):
            if prev.direction == cur.direction:
                continue
            gap = cur.timestamp - prev.timestamp
            if gap > cutoff or gap.total_seconds() < 0:
                continue
            (mine if cur.direction == "sent" else theirs).append(gap.total_seconds() / 60)
    if not mine and not theirs:
        return None
    return {
        "you_median_minutes": round(median(mine), 1) if mine else None,
        "them_median_minutes": round(median(theirs), 1) if theirs else None,
        "you_samples": len(mine),
        "them_samples": len(theirs),
    }


def contact_peaks(messages: list[Message], limit: int = 20) -> list[dict]:
    """For each contact, the hour and weekday they talk to you most."""
    out = []
    for row in contact_frequency(messages, limit=limit):
        t = timing(messages, contact=row["contact"])
        out.append({"contact": row["contact"], "total": row["total"], "peak_hour": t["peak_hour"],
                    "peak_weekday": t["peak_weekday"], "by_hour": [h["sent"] + h["received"] for h in t["by_hour"]]})
    return out


# ---------------------------------------------------------------------------
# Words & misspellings
# ---------------------------------------------------------------------------

STOPWORDS = set("""
the a an and or but if then so of to in on at for with from by as is are was were be been being am
i me my mine you your yours he him his she her hers it its we us our ours they them their theirs
this that these those there here what which who whom whose when where why how not no yes do does
did doing have has had having will would can could should shall may might must just very too also
about into over under again out up down off than because while until after before all any some
more most such only own same other few both each every either neither much many get got go going
went come came like im ive id ill dont didnt cant wont isnt arent wasnt werent thats whats
""".split())


def word_frequency(messages: list[Message], direction: str | None = None, limit: int = 50, min_len: int = 3) -> list[dict]:
    counts: Counter = Counter()
    for m in messages:
        if direction and m.direction != direction:
            continue
        if is_media_placeholder(m.text):
            continue
        for w in words_of(m.text):
            lw = w.lower().replace("’", "'")
            if len(lw) >= min_len and lw not in STOPWORDS:
                counts[lw] += 1
    return [{"word": w, "count": c} for w, c in counts.most_common(limit)]


class Speller:
    """Thin wrapper around pyspellchecker so the dictionary loads once."""

    def __init__(self, language: str = "en"):
        from spellchecker import SpellChecker

        self.checker = SpellChecker(language=language)
        self.checker.word_frequency.load_words(SLANG)
        self._cache: dict[str, bool] = {}
        self._suggest_cache: dict[str, str | None] = {}

    def is_misspelled(self, word: str) -> bool:
        lw = word.lower().replace("’", "'")
        if lw in self._cache:
            return self._cache[lw]
        bad = lw not in self.checker and lw.rstrip("'s") not in self.checker
        self._cache[lw] = bad
        return bad

    def suggest(self, word: str) -> str | None:
        lw = word.lower()
        if lw not in self._suggest_cache:  # correction() is slow; it runs once per distinct word
            corr = self.checker.correction(lw)
            self._suggest_cache[lw] = corr if corr and corr != lw else None
        return self._suggest_cache[lw]


def _looks_like_name(word: str, position: int) -> bool:
    """Capitalised mid-sentence words are probably names; skip them."""
    return position > 0 and word[0].isupper()


def _is_noise(word: str) -> bool:
    lw = word.lower()
    if len(lw) < 3 or lw in SLANG:
        return True
    if re.fullmatch(r"(.)\1{2,}", lw):  # "aaa", "zzz"
        return True
    if re.search(r"(ha){2,}|(he){2,}|(lo)+l|o{3,}|a{3,}|e{3,}|y{3,}|z{2,}|m{3,}", lw):  # stretched words
        return True
    if not lw.isalpha() and "'" not in lw:
        return True
    return False


def misspellings(messages: list[Message], speller: Speller | None = None, direction: str = "sent",
                 limit: int = 50, contact: str | None = None) -> dict:
    """Misspelled words in ``direction`` messages, most frequent first, with contact and per-person breakdowns."""
    speller = speller or Speller()
    counts: Counter = Counter()
    examples: dict[str, str] = {}
    by_contact: dict[str, Counter] = defaultdict(Counter)
    by_sender: dict[str, Counter] = defaultdict(Counter)
    words_total = 0
    for m in messages:
        if m.direction != direction or (contact and m.contact != contact) or is_media_placeholder(m.text):
            continue
        words = words_of(m.text)
        words_total += len(words)
        for i, w in enumerate(words):
            if _is_noise(w) or _looks_like_name(w, i):
                continue
            if speller.is_misspelled(w):
                lw = w.lower()
                counts[lw] += 1
                examples.setdefault(lw, m.text[:140])
                by_contact[m.contact][lw] += 1
                by_sender[m.sender][lw] += 1
    top = counts.most_common(limit)
    rows = [{"word": w, "count": c, "suggestion": speller.suggest(w), "example": examples[w]} for w, c in top]
    total_bad = sum(counts.values())
    return {
        "direction": direction,
        "words_checked": words_total,
        "misspelled_total": total_bad,
        "rate_per_1000": round(1000 * total_bad / words_total, 2) if words_total else 0,
        "unique": len(counts),
        "words": rows,
        "by_contact": sorted(
            ({"contact": c, "misspelled": sum(v.values()), "top": [w for w, _ in v.most_common(5)]} for c, v in by_contact.items()),
            key=lambda r: r["misspelled"], reverse=True)[:limit],
        "by_sender": sorted(
            ({"sender": s, "misspelled": sum(v.values()), "top": [w for w, _ in v.most_common(5)]} for s, v in by_sender.items()),
            key=lambda r: r["misspelled"], reverse=True)[:limit],
    }


# ---------------------------------------------------------------------------
# Conversation shape: who opens, who has the last word, who double-texts
# ---------------------------------------------------------------------------

def split_conversations(messages: list[Message], gap_hours: float = 6.0) -> list[list[Message]]:
    """Split one contact's messages into runs separated by more than ``gap_hours`` of silence."""
    gap = timedelta(hours=gap_hours)
    convs: list[list[Message]] = []
    current: list[Message] = []
    for m in sorted(messages, key=lambda m: m.timestamp):
        if current and m.timestamp - current[-1].timestamp > gap:
            convs.append(current)
            current = []
        current.append(m)
    if current:
        convs.append(current)
    return convs


def _runs(conv: list[Message]) -> list[tuple[str, int]]:
    """Consecutive same-direction stretches inside a conversation."""
    out: list[tuple[str, int]] = []
    for m in conv:
        if out and out[-1][0] == m.direction:
            out[-1] = (m.direction, out[-1][1] + 1)
        else:
            out.append((m.direction, 1))
    return out


def conversation_health(messages: list[Message], gap_hours: float = 6.0, limit: int | None = None) -> list[dict]:
    """Per contact: conversation count, who opens, who gets the last word, double-texts, silences."""
    by_contact: dict[str, list[Message]] = defaultdict(list)
    for m in messages:
        by_contact[m.contact].append(m)

    rows = []
    for contact, msgs in by_contact.items():
        convs = split_conversations(msgs, gap_hours)
        opened = Counter(c[0].direction for c in convs)
        closed = Counter(c[-1].direction for c in convs)
        doubles = Counter()
        for conv in convs:
            for direction, length in _runs(conv):
                if length >= 2:
                    doubles[direction] += 1
        ordered = sorted(msgs, key=lambda m: m.timestamp)
        silence = max((b.timestamp - a.timestamp for a, b in zip(ordered, ordered[1:])), default=timedelta(0))
        latency = reply_latency(msgs) or {}
        total = len(convs)
        rows.append({
            "contact": contact,
            "messages": len(msgs),
            "conversations": total,
            "you_opened": opened["sent"],
            "they_opened": opened["received"],
            "you_opened_share": round(opened["sent"] / total, 3) if total else 0,
            "you_closed": closed["sent"],
            "they_closed": closed["received"],
            "you_closed_share": round(closed["sent"] / total, 3) if total else 0,
            "your_double_texts": doubles["sent"],
            "their_double_texts": doubles["received"],
            "avg_conversation": round(len(msgs) / total, 1) if total else 0,
            "longest_silence_days": round(silence.total_seconds() / 86400, 1),
            "you_reply_median": latency.get("you_median_minutes"),
            "them_reply_median": latency.get("them_median_minutes"),
        })
    rows.sort(key=lambda r: r["conversations"], reverse=True)
    return rows[:limit] if limit else rows


def conversation_summary(rows: list[dict]) -> dict:
    """Totals across every contact, for the tiles above the tables."""
    if not rows:
        return {"conversations": 0, "you_opened_share": None, "you_closed_share": None,
                "you_reply_median": None, "them_reply_median": None, "double_texts": 0, "ghosted_by": None}
    total = sum(r["conversations"] for r in rows) or 1
    you_replies = [r["you_reply_median"] for r in rows if r["you_reply_median"] is not None]
    them_replies = [r["them_reply_median"] for r in rows if r["them_reply_median"] is not None]
    # The person most likely to leave your message unanswered at the end of a conversation.
    ghost = max(rows, key=lambda r: (r["you_closed_share"], r["conversations"]))
    return {
        "conversations": sum(r["conversations"] for r in rows),
        "you_opened_share": round(sum(r["you_opened"] for r in rows) / total, 3),
        "you_closed_share": round(sum(r["you_closed"] for r in rows) / total, 3),
        "you_reply_median": round(median(you_replies), 1) if you_replies else None,
        "them_reply_median": round(median(them_replies), 1) if them_replies else None,
        "double_texts": sum(r["your_double_texts"] for r in rows),
        "ghosted_by": {"contact": ghost["contact"], "share": ghost["you_closed_share"]} if ghost["conversations"] else None,
    }


def group_members(messages: list[Message], contact: str) -> list[dict]:
    """Per-person breakdown inside one conversation, which is what makes a group chat readable."""
    msgs = [m for m in messages if m.contact == contact]
    if not msgs:
        return []
    by_sender: dict[str, list[Message]] = defaultdict(list)
    for m in msgs:
        by_sender[m.sender].append(m)
    rows = []
    for sender, items in by_sender.items():
        hours = Counter(m.timestamp.hour for m in items)
        words = sum(len(words_of(m.text)) for m in items)
        rows.append({
            "sender": sender,
            "count": len(items),
            "share": round(len(items) / len(msgs), 3),
            "avg_words": round(words / len(items), 1),
            "peak_hour": hours.most_common(1)[0][0],
            "first": min(m.timestamp for m in items).isoformat(),
            "last": max(m.timestamp for m in items).isoformat(),
            "is_you": items[0].direction == "sent",
        })
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows
