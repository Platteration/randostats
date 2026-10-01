"""WhatsApp "Export chat" text parser.

WhatsApp exports vary by platform and locale. Lines look like one of:

    12/31/23, 11:59 PM - Alice: happy new year
    [31/12/2023, 23:59:01] Alice: happy new year
    31.12.23, 23:59 - Alice: happy new year
    2023-12-31, 23:59 - Alice: happy new year

Continuation lines (no timestamp) belong to the previous message. System
lines (no "Name: " part) such as "Messages are end-to-end encrypted" are
skipped. Media placeholders are kept as text so counts stay honest.
"""

from __future__ import annotations

import re
from datetime import datetime
from itertools import chain, islice
from typing import Iterable, Iterator

from ..models import Message

# Timestamp, then " - " or "] ", then "Sender: text".
#
# Every run of whitespace here belongs to exactly one construct, and the
# sender cannot start with whitespace. That is not style: the earlier pattern
# put three whitespace quantifiers in a row (one inside `time`, two in the
# separator) next to a sender that also accepts spaces, so a line that never
# reaches its ":" made the engine try every way of dividing the spaces between
# them. A 4 KB upload of one such line cost 22 s of CPU, and the cost is
# quadratic in the length of the line, which no line- or file-count bound can
# reach. Pinning each `\s*` to the thing that must follow it - a "]", a dash,
# or a non-space sender - leaves exactly one way to divide them, and the same
# line is now linear (400 KB in 0.014 s).
_LINE = re.compile(
    r"""^‎?\[?
        (?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+
        (?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap]\.?[Mm]\.?)?)
        (?:\s*\])?(?:\s*[-–])?\s*
        (?P<sender>[^:\s][^:]{0,79}?):\s
        (?P<text>.*)$""",
    re.VERBOSE,
)

# iOS wraps timestamps and names in bidirectional control characters, which
# str.strip() does not remove. Left in, the user's own name never matches and
# a one-to-one chat is filed as a group.
_BIDI = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069\u061c]")

# A line that opens with a timestamp but carries no "Name: " is a system
# notice ("Messages are end-to-end encrypted", "You were added"). It is not a
# continuation of the message above it.
_TIMESTAMPED = re.compile(r"^\[?\d{1,4}[./-]\d{1,2}[./-]\d{1,4},?\s+\d{1,2}:\d{2}")


def _clean_line(raw: str) -> str:
    return _BIDI.sub("", raw).strip()


# A line can match _LINE and still carry a date no format parses
# ("99/99/9999, 99:99 - a: x"), and each of those costs a failed strptime for
# every date and time format there is. A crafted file of nothing else used to
# cost 23 seconds of CPU per megabyte, so stop reading one that never parses.
# Counting only *consecutive* failures would not help: one parseable line
# every thousand resets it and the cost comes straight back.
MAX_UNPARSEABLE = 1000

# How much of the file is read before the first message comes out. One export
# is one conversation written in one date format, so which way round the dates
# are and who speaks in it are both settled within the opening lines; reading
# the whole file to answer them is what stopped the give-up above from
# bounding anything at all. It built a Match object per line for the whole
# upload - 8 s and 1.2 GB for 64 MB of it - before looking at a single one,
# and the give-up cannot fire until that is done. A group whose third
# participant says nothing in the first five thousand lines is filed under
# the name of the second, which is the price of the bound.
HEAD_LINES = 5000

# What str.splitlines() splits on. Splitting the whole file at once is a list
# of millions of strings; this yields them one at a time instead, so a file
# that is going to be refused is never fully materialised.
_LINE_BREAK = re.compile("\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]")

_DATE_FORMATS = (
    "%m/%d/%y", "%m/%d/%Y", "%d/%m/%y", "%d/%m/%Y",
    "%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d",
)
_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p", "%I:%M%p", "%I:%M:%S%p")


def looks_like_whatsapp(head: bytes) -> bool:
    text = head.decode("utf-8", errors="replace")
    return any(_LINE.match(_clean_line(line)) for line in text.splitlines()[:20])


def _parse_timestamp(date: str, time: str, day_first: bool | None,
                     known: tuple[str, str] | None = None) -> tuple[datetime | None, tuple[str, str] | None]:
    """The moment, and the pair of formats that read it.

    One export is written in one format, so pass the pair that worked last
    time back in as ``known`` and the rest of the file costs one attempt a
    line instead of up to forty-eight.
    """
    time = time.replace("a.m.", "AM").replace("p.m.", "PM").replace(" ", " ").strip()
    stamp = f"{date} {time}"
    if known is not None:
        try:
            return datetime.strptime(stamp, f"{known[0]} {known[1]}"), known
        except ValueError:
            pass
    candidates = list(_DATE_FORMATS)
    if day_first is True:
        candidates.sort(key=lambda f: 0 if f.startswith("%d") else 1)
    elif day_first is False:
        candidates.sort(key=lambda f: 0 if f.startswith("%m") else 1)
    for dfmt in candidates:
        for tfmt in _TIME_FORMATS:
            try:
                return datetime.strptime(stamp, f"{dfmt} {tfmt}"), (dfmt, tfmt)
            except ValueError:
                continue
    return None, known


def _iter_lines(text: str) -> Iterator[str]:
    """``text.splitlines()``, one line at a time and without the list."""
    start = 0
    for brk in _LINE_BREAK.finditer(text):
        yield text[start:brk.start()]
        start = brk.end()
    if start < len(text):
        yield text[start:]


def _guess_day_first(dates: list[str]) -> bool | None:
    """If any first field exceeds 12 the export is day-first; any second field >12 means month-first."""
    for d in dates:
        parts = re.split(r"[./-]", d)
        if len(parts) != 3 or len(parts[0]) == 4:
            continue
        a, b = int(parts[0]), int(parts[1])
        if a > 12:
            return True
        if b > 12:
            return False
    return None


def parse(data: bytes, self_name: str, contact: str | None = None) -> Iterable[Message]:
    text = data.decode("utf-8-sig", errors="replace")
    rest = _iter_lines(text)
    head = list(islice(rest, HEAD_LINES))
    opening = [m for m in (_LINE.match(_clean_line(line)) for line in head) if m]
    day_first = _guess_day_first([m.group("date") for m in opening][:500])

    # The contact is whoever isn't the user; for a group, the export has no
    # name, so we fall back to the caller-provided name or a generic label.
    # There are only three answers - nobody else, exactly one other, or more
    # than one - so stop looking at two, and a file of nothing but distinct
    # senders cannot make the roster itself expensive to build.
    self_key = _BIDI.sub("", self_name).strip().lower()
    others: list[str] = []
    for m in opening:
        sender = m.group("sender")
        if sender.strip().lower() != self_key and sender not in others:
            others.append(sender)
            if len(others) > 1:
                break
    if contact is None:
        contact = others[0] if len(others) == 1 else ("Group chat" if others else self_name)

    current: dict | None = None
    known: tuple[str, str] | None = None
    unparseable = 0
    for raw in chain(head, rest):
        m = _LINE.match(_clean_line(raw))
        if m:
            if current:
                yield _finish(current, contact)
            ts, known = _parse_timestamp(m.group("date"), m.group("time"), day_first, known)
            if ts is None:
                unparseable += 1
                if unparseable > MAX_UNPARSEABLE:
                    raise ValueError(f"gave up after {MAX_UNPARSEABLE} lines whose timestamp no known "
                                     "format could read; this is not a WhatsApp export")
                current = None
                continue
            sender = m.group("sender").strip()
            current = {"sender": sender, "ts": ts, "text": [m.group("text")], "self": sender.lower() == self_key}
        elif _TIMESTAMPED.match(_clean_line(raw)):
            # A system notice. End the message above it and keep neither.
            if current:
                yield _finish(current, contact)
            current = None
        elif current is not None and raw.strip():
            current["text"].append(raw)
    if current:
        yield _finish(current, contact)


def _finish(cur: dict, contact: str) -> Message:
    return Message(
        contact=contact,
        sender=cur["sender"],
        direction="sent" if cur["self"] else "received",
        timestamp=cur["ts"],
        text="\n".join(cur["text"]).strip(),
        source="whatsapp",
    )
