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
from typing import Iterable

from ..models import Message

# Timestamp, then " - " or "] ", then "Sender: text".
_LINE = re.compile(
    r"""^‎?\[?
        (?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+
        (?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?)
        \]?\s*[-–]?\s*
        (?P<sender>[^:]{1,80}?):\s
        (?P<text>.*)$""",
    re.VERBOSE,
)

_DATE_FORMATS = (
    "%m/%d/%y", "%m/%d/%Y", "%d/%m/%y", "%d/%m/%Y",
    "%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d",
)
_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p", "%I:%M%p", "%I:%M:%S%p")


def looks_like_whatsapp(head: bytes) -> bool:
    text = head.decode("utf-8", errors="replace")
    return any(_LINE.match(line.strip()) for line in text.splitlines()[:20])


def _parse_timestamp(date: str, time: str, day_first: bool | None) -> datetime | None:
    time = time.replace("a.m.", "AM").replace("p.m.", "PM").replace(" ", " ").strip()
    candidates = list(_DATE_FORMATS)
    if day_first is True:
        candidates.sort(key=lambda f: 0 if f.startswith("%d") else 1)
    elif day_first is False:
        candidates.sort(key=lambda f: 0 if f.startswith("%m") else 1)
    for dfmt in candidates:
        for tfmt in _TIME_FORMATS:
            try:
                return datetime.strptime(f"{date} {time}", f"{dfmt} {tfmt}")
            except ValueError:
                continue
    return None


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
    lines = text.splitlines()
    matches = [(_LINE.match(line.strip()), line) for line in lines]
    day_first = _guess_day_first([m.group("date") for m, _ in matches if m][:500])

    # The contact is whoever isn't the user; for a group, the export has no
    # name, so we fall back to the caller-provided name or a generic label.
    senders: list[str] = []
    for m, _ in matches:
        if m and m.group("sender") not in senders:
            senders.append(m.group("sender"))
    others = [s for s in senders if s.strip().lower() != self_name.strip().lower()]
    if contact is None:
        contact = others[0] if len(others) == 1 else ("Group chat" if others else self_name)

    current: dict | None = None
    for m, raw in matches:
        if m:
            if current:
                yield _finish(current, contact)
            ts = _parse_timestamp(m.group("date"), m.group("time"), day_first)
            if ts is None:
                current = None
                continue
            sender = m.group("sender").strip().lstrip("‪").rstrip("‬")
            current = {"sender": sender, "ts": ts, "text": [m.group("text")], "self": sender.lower() == self_name.strip().lower()}
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
