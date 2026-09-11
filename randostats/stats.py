"""Statistics over a list of ``Message`` objects.

Every function is pure: it takes messages and returns JSON-serialisable
dicts, so the API layer and the tests can share them.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from functools import lru_cache
from datetime import timedelta
from statistics import median
from typing import Iterable

from .models import Message

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Below this many conversations, a share is noise rather than a habit.
MIN_CONVERSATIONS_TO_JUDGE = 5

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
    if "http" in text or "www." in text:
        text = _URL.sub(" ", text)
    if "@" in text or "#" in text:
        text = _MENTION.sub(" ", text)
    return text


# Fragments of every placeholder above, in the two cases exports actually use
# ("<Media omitted>", "STICKER OMITTED"). Nearly every real message fails all
# of them, so the lowercase copy and the full scan below are skipped. A
# compiled case-insensitive alternation was tried here and measured three
# times slower than this.
_MEDIA_HINTS = ("mitted", "MITTED", "elete", "ELETE", "ttach", "TTACH")


def is_media_placeholder(text: str) -> bool:
    if not any(hint in text for hint in _MEDIA_HINTS):
        return False
    t = text.strip().lower()
    return any(p in t for p in _MEDIA_PLACEHOLDERS)


def words_of(text: str) -> list[str]:
    """Words, with the typographic apostrophe folded to the straight one.

    iOS and WhatsApp write U+2019, and every check downstream (the noise
    filter, the dictionary, the stopword list) expects "'". Normalising once
    here keeps "did’nt" visible instead of silently dropping it as noise.
    """
    return [w.replace("\u2019", "'") for w in _WORD.findall(_sanitise(text))]


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
                                                       "counted_sent": 0, "counted_received": 0,
                                                       "first": None, "last": None, "senders": Counter()})
    for m in messages:
        c = by_contact[m.contact]
        c[m.direction] += 1
        if not is_media_placeholder(m.text):
            c["words_" + m.direction] += len(words_of(m.text))
            c["counted_" + m.direction] += 1
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
            "avg_words_sent": round(c["words_sent"] / c["counted_sent"], 1) if c["counted_sent"] else 0,
            "avg_words_received": round(c["words_received"] / c["counted_received"], 1) if c["counted_received"] else 0,
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
    """When messages happen: hour-of-day, weekday, a 7x24 heatmap, monthly volume, and reply latency.

    An empty ``contact`` means everyone, as it does in every other filter here
    (``search``, ``emoji_stats``, ``misspellings``, ``tone`` all spell it
    ``if contact and ...``). Spelled ``contact is None``, this one alone read
    ``?contact=`` as "nobody", so the same query answered two different ways
    depending on which endpoint it was sent to.
    """
    msgs = [m for m in messages if not contact or m.contact == contact]
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
    """For each contact, the hour and weekday they talk to you most.

    One pass over the messages, bucketed per contact. Calling timing() per
    contact instead costs a full scan each time.
    """
    hours: dict[str, list[int]] = defaultdict(lambda: [0] * 24)
    weekdays: dict[str, list[int]] = defaultdict(lambda: [0] * 7)
    totals: Counter = Counter()
    for m in messages:
        contact = m.contact
        hours[contact][m.timestamp.hour] += 1
        weekdays[contact][m.timestamp.weekday()] += 1
        totals[contact] += 1

    out = []
    for contact, total in totals.most_common(limit):
        by_hour = hours[contact]
        by_weekday = weekdays[contact]
        out.append({
            "contact": contact,
            "total": total,
            "peak_hour": max(range(24), key=lambda i: by_hour[i]),
            "peak_weekday": WEEKDAYS[max(range(7), key=lambda i: by_weekday[i])],
            "by_hour": by_hour,
        })
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


# How many distinct words the word-by-word memos below will hold. One Speller
# lives for the life of the server, so unbounded dicts there were one entry
# per distinct word ever checked, never returned.
MEMO_WORDS = 200_000


class Speller:
    """Thin wrapper around pyspellchecker so the dictionary loads once."""

    def __init__(self, language: str = "en"):
        from spellchecker import SpellChecker

        self.checker = SpellChecker(language=language)
        self.checker.word_frequency.load_words(SLANG)
        # Bounded like _is_noise: an import full of distinct junk should not
        # be able to grow the resident set without limit.
        self._misspelled = lru_cache(maxsize=MEMO_WORDS)(self._check)
        self._suggestion = lru_cache(maxsize=MEMO_WORDS)(self._correct)

    def _check(self, lw: str) -> bool:
        return lw not in self.checker and lw.removesuffix("'s") not in self.checker

    def _correct(self, lw: str) -> str | None:  # correction() is slow; it runs once per distinct word
        corr = self.checker.correction(lw)
        return corr if corr and corr != lw else None

    def is_misspelled(self, word: str) -> bool:
        return self._misspelled(word.lower().replace("’", "'"))

    def suggest(self, word: str) -> str | None:
        return self._suggestion(word.lower())


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SENTENCE_END = re.compile(r"[.!?]\s").search


def _looks_like_name(word: str, position: int) -> bool:
    """Capitalised mid-sentence words are probably names; skip them.

    ``position`` is the index within its own sentence, not the message: a word
    opening a second sentence is capitalised by grammar, not because it is a
    name, and used to escape the spell check entirely.
    """
    return position > 0 and word[0].isupper()


_REPEATED = re.compile(r"(.)\1{2,}")
_STRETCHED = re.compile(r"(ha){2,}|(he){2,}|(lo)+l|o{3,}|a{3,}|e{3,}|y{3,}|z{2,}|m{3,}")


@lru_cache(maxsize=MEMO_WORDS)
def _is_noise(word: str) -> bool:
    """Laughter, keysmashes and text-speak, which no dictionary should judge."""
    lw = word.lower()
    if len(lw) < 3 or lw in SLANG:
        return True
    if _REPEATED.fullmatch(lw):  # "aaa", "zzz"
        return True
    if _STRETCHED.search(lw):  # "hahaha", "noooo"
        return True
    return not lw.isalpha() and "'" not in lw


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
        # Sentence by sentence, so capitalisation is only read as a name where
        # grammar did not force it. A shouted message carries no such signal.
        shouted = m.text.isupper()
        # Most messages are one sentence with no terminal punctuation at all,
        # so the split is skipped unless there is something to split.
        text = m.text
        sentences = _SENTENCE_SPLIT.split(text) if _SENTENCE_END(text) else (text,)
        for sentence in sentences:
            words = words_of(sentence)
            words_total += len(words)
            for i, w in enumerate(words):
                if _is_noise(w) or (not shouted and _looks_like_name(w, i)):
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
    # The person most likely to leave your message unanswered at the end of a
    # conversation. A single unanswered message is not a habit, so contacts
    # with too little history cannot win this.
    frequent = [r for r in rows if r["conversations"] >= MIN_CONVERSATIONS_TO_JUDGE]
    ghost = max(frequent, key=lambda r: (r["you_closed_share"], r["conversations"])) if frequent else None
    return {
        "conversations": sum(r["conversations"] for r in rows),
        "you_opened_share": round(sum(r["you_opened"] for r in rows) / total, 3),
        "you_closed_share": round(sum(r["you_closed"] for r in rows) / total, 3),
        "you_reply_median": round(median(you_replies), 1) if you_replies else None,
        "them_reply_median": round(median(them_replies), 1) if them_replies else None,
        "double_texts": sum(r["your_double_texts"] for r in rows),
        "ghosted_by": {"contact": ghost["contact"], "share": ghost["you_closed_share"]} if ghost else None,
    }


def group_members(messages: list[Message], contact: str) -> list[dict]:
    """Per-person breakdown inside one conversation, which is what makes a group chat readable.

    ``contact`` names one conversation; unlike the filters elsewhere in this
    module there is no "everyone" to fall back on, so an empty name matches
    nothing and the answer is the empty list.
    """
    msgs = [m for m in messages if m.contact == contact]
    if not msgs:
        return []
    by_sender: dict[str, list[Message]] = defaultdict(list)
    for m in msgs:
        by_sender[m.sender].append(m)
    rows = []
    for sender, items in by_sender.items():
        hours = Counter(m.timestamp.hour for m in items)
        spoken = [m for m in items if not is_media_placeholder(m.text)]
        words = sum(len(words_of(m.text)) for m in spoken)
        rows.append({
            "sender": sender,
            "count": len(items),
            "share": round(len(items) / len(msgs), 3),
            "avg_words": round(words / len(spoken), 1) if spoken else 0,
            "peak_hour": hours.most_common(1)[0][0],
            "first": min(m.timestamp for m in items).isoformat(),
            "last": max(m.timestamp for m in items).isoformat(),
            "is_you": items[0].direction == "sent",
        })
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Drill-down: the messages behind a number
# ---------------------------------------------------------------------------

def search(messages: list[Message], q: str | None = None, word: str | None = None, contact: str | None = None,
           sender: str | None = None, direction: str | None = None, hour: int | None = None,
           weekday: int | None = None, month: str | None = None, date: str | None = None,
           limit: int = 100, offset: int = 0) -> dict:
    """Filter messages down to the ones behind a chart mark.

    ``q`` is a substring; ``word`` matches whole words only, so clicking the
    misspelling "wich" doesn't drag in "sandwich".
    """
    # Words are stored with a straight apostrophe but the message may hold the
    # typographic one, so clicking "did'nt" must still find "did’nt".
    escaped = re.escape(word.replace("\u2019", "'")).replace("'", "['\u2019]") if word else ""
    pattern = re.compile(rf"(?<![A-Za-z'\u2019]){escaped}(?![A-Za-z'\u2019])", re.IGNORECASE) if word else None
    needle = q.lower() if q else None
    hits = []
    for m in messages:
        if contact and m.contact != contact:
            continue
        if sender and m.sender != sender:
            continue
        if direction and m.direction != direction:
            continue
        if hour is not None and m.timestamp.hour != hour:
            continue
        if weekday is not None and m.timestamp.weekday() != weekday:
            continue
        if month and m.timestamp.strftime("%Y-%m") != month:
            continue
        if date and m.timestamp.date().isoformat() != date:
            continue
        if needle and needle not in m.text.lower():
            continue
        if pattern and not pattern.search(m.text):
            continue
        hits.append(m)
    hits.sort(key=lambda m: m.timestamp, reverse=True)
    page = hits[offset:offset + limit]
    return {
        "total": len(hits),
        "offset": offset,
        "limit": limit,
        "messages": [{"contact": m.contact, "sender": m.sender, "direction": m.direction,
                      "timestamp": m.timestamp.isoformat(sep=" "), "text": m.text} for m in page],
    }


# ---------------------------------------------------------------------------
# Emoji and tone
# ---------------------------------------------------------------------------

def emoji_stats(messages: list[Message], limit: int = 30, contact: str | None = None) -> dict:
    """Emoji counts overall, per direction, and per person."""
    from .lexicon import EMOJI

    counts: dict[str, Counter] = {"sent": Counter(), "received": Counter()}
    by_contact: dict[str, Counter] = defaultdict(Counter)
    carriers = 0
    considered = 0
    for m in messages:
        if contact and m.contact != contact:
            continue
        considered += 1
        found = EMOJI.findall(m.text)
        if not found:
            continue
        carriers += 1
        counts[m.direction].update(found)
        by_contact[m.contact].update(found)
    total = sum(c.total() for c in counts.values())
    combined = counts["sent"] + counts["received"]
    return {
        "total": total,
        "unique": len(combined),
        "messages_with_emoji": carriers,
        "share_of_messages": round(carriers / considered, 3) if considered else 0,
        "top": [{"emoji": e, "count": c, "sent": counts["sent"][e], "received": counts["received"][e]}
                for e, c in combined.most_common(limit)],
        "yours": [{"emoji": e, "count": c} for e, c in counts["sent"].most_common(5)],
        "theirs": [{"emoji": e, "count": c} for e, c in counts["received"].most_common(5)],
        "by_contact": sorted(
            ({"contact": name, "count": c.total(), "top": [e for e, _ in c.most_common(5)]} for name, c in by_contact.items()),
            key=lambda r: r["count"], reverse=True)[:limit],
    }


def tone(messages: list[Message], contact: str | None = None) -> dict:
    """Counts of positive and negative tone words, by month and by person.

    Net is (positive - negative) / (positive + negative), so it runs -1 to 1.
    It is a word count. It cannot see sarcasm, negation, or context.
    """
    from .lexicon import NEGATIVE, POSITIVE

    months: dict[str, Counter] = defaultdict(Counter)
    people: dict[str, Counter] = defaultdict(Counter)
    hits: dict[str, Counter] = {"positive": Counter(), "negative": Counter()}
    totals = Counter()
    for m in messages:
        if contact and m.contact != contact:
            continue
        if is_media_placeholder(m.text):
            continue
        pos = neg = 0
        for w in words_of(m.text):
            lw = w.lower()
            if lw in POSITIVE:
                pos += 1
                hits["positive"][lw] += 1
            elif lw in NEGATIVE:
                neg += 1
                hits["negative"][lw] += 1
        if not (pos or neg):
            continue
        key = m.timestamp.strftime("%Y-%m")
        months[key]["positive"] += pos
        months[key]["negative"] += neg
        people[m.contact]["positive"] += pos
        people[m.contact]["negative"] += neg
        people[m.contact][m.direction] += pos + neg
        totals["positive"] += pos
        totals["negative"] += neg

    def net(c: Counter) -> float:
        span = c["positive"] + c["negative"]
        return round((c["positive"] - c["negative"]) / span, 3) if span else 0.0

    return {
        "positive": totals["positive"],
        "negative": totals["negative"],
        "net": net(totals),
        "by_month": [{"month": k, "positive": v["positive"], "negative": v["negative"], "net": net(v)}
                     for k, v in sorted(months.items())],
        "by_contact": sorted(
            ({"contact": name, "positive": v["positive"], "negative": v["negative"], "net": net(v),
              "words": v["positive"] + v["negative"]} for name, v in people.items()),
            key=lambda r: r["words"], reverse=True),
        "top_positive": [{"word": w, "count": c} for w, c in hits["positive"].most_common(15)],
        "top_negative": [{"word": w, "count": c} for w, c in hits["negative"].most_common(15)],
    }


# ---------------------------------------------------------------------------
# Wrapped: the year in one card
# ---------------------------------------------------------------------------

def longest_streak(messages: list[Message]) -> dict:
    """Longest run of consecutive days with at least one message."""
    days = sorted({m.timestamp.date() for m in messages})
    if not days:
        return {"days": 0, "start": None, "end": None}
    best = run = 1
    best_end = run_start = days[0]
    best_start = days[0]
    for prev, cur in zip(days, days[1:]):
        if (cur - prev).days == 1:
            run += 1
        else:
            run, run_start = 1, cur
        if run > best:
            best, best_start, best_end = run, run_start, cur
    return {"days": best, "start": best_start.isoformat(), "end": best_end.isoformat()}


def years(messages: list[Message]) -> list[int]:
    return sorted({m.timestamp.year for m in messages})


def wrapped(messages: list[Message], year: int | None = None, speller: "Speller | None" = None) -> dict:
    """The handful of numbers worth putting on a card."""
    msgs = [m for m in messages if year is None or m.timestamp.year == year]
    if not msgs:
        return {"year": year, "empty": True}

    ov = overview(msgs)
    contacts = contact_frequency(msgs)
    t = timing(msgs)
    health = conversation_summary(conversation_health(msgs))
    words = word_frequency(msgs, direction="sent", limit=1)
    em = emoji_stats(msgs, limit=1)
    typos = misspellings(msgs, speller, direction="sent", limit=1)
    tn = tone(msgs)
    night = sum(1 for m in msgs if 0 <= m.timestamp.hour < 5)

    return {
        "year": year,
        "empty": False,
        "total": ov["total"],
        "sent": ov["sent"],
        "received": ov["received"],
        "sent_share": round(ov["sent"] / ov["total"], 3) if ov["total"] else 0,
        "per_day": ov["per_day"],
        "words_written": sum(len(words_of(m.text)) for m in msgs
                             if m.direction == "sent" and not is_media_placeholder(m.text)),
        "people": ov["contacts"],
        "top_contact": {"contact": contacts[0]["contact"], "total": contacts[0]["total"],
                        "sent": contacts[0]["sent"], "received": contacts[0]["received"]} if contacts else None,
        "runner_up": {"contact": contacts[1]["contact"], "total": contacts[1]["total"]} if len(contacts) > 1 else None,
        "peak_hour": t["peak_hour"],
        "peak_weekday": t["peak_weekday"],
        "busiest_day": t["busiest_day"],
        "night_messages": night,
        "night_share": round(night / ov["total"], 3) if ov["total"] else 0,
        "reply_median": (t["reply_latency"] or {}).get("you_median_minutes"),
        "their_reply_median": (t["reply_latency"] or {}).get("them_median_minutes"),
        "conversations": health["conversations"],
        "you_opened_share": health["you_opened_share"],
        "streak": longest_streak(msgs),
        "top_word": words[0] if words else None,
        "top_emoji": em["yours"][0] if em["yours"] else (em["top"][0] if em["top"] else None),
        "top_typo": typos["words"][0] if typos["words"] else None,
        "typo_rate": typos["rate_per_1000"],
        "tone_net": tn["net"],
    }
