"""Generic CSV and JSON parsers.

Both accept rows/objects with these keys (case-insensitive, aliases allowed):

* ``contact``   (aliases: chat, conversation, thread, name, to/from fallback)
* ``sender``    (aliases: author, from)      - optional, defaults to contact or self
* ``direction`` (aliases: type, is_from_me)  - "sent"/"received", "out"/"in", true/false
* ``timestamp`` (aliases: date, time, datetime, ts)  - ISO 8601 or unix seconds/millis
* ``text``      (aliases: body, message, content)
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Iterable

from ..models import Message
from .timestamps import from_iso, from_unix

_ALIASES = {
    "contact": ("contact", "chat", "conversation", "thread", "name", "chat_name"),
    "sender": ("sender", "author", "from"),
    "direction": ("direction", "type", "is_from_me", "isfromme", "outgoing", "sent"),
    "timestamp": ("timestamp", "date", "time", "datetime", "ts", "date_sent", "created_at"),
    "text": ("text", "body", "message", "content", "msg"),
}


def _pick_alias(row: dict[str, Any], key: str) -> tuple[str | None, Any]:
    """The column that supplied the value, and the value. The column matters:
    ``type=1`` means received in an SMS export but sent in an is_from_me one."""
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for alias in _ALIASES[key]:
        if alias in lowered and lowered[alias] not in (None, ""):
            return alias, lowered[alias]
    return None, None


def _pick(row: dict[str, Any], key: str) -> Any:
    return _pick_alias(row, key)[1]


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e11:  # milliseconds
            v /= 1000.0
        return from_unix(v)
    s = str(value).strip()
    if s.replace(".", "", 1).isdigit():
        return parse_timestamp(float(s))
    parsed = from_iso(s)
    if parsed is not None:
        return parsed
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%m/%d/%y %H:%M", "%d/%m/%Y %H:%M",
                "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%b %d, %Y %I:%M %p", "%B %d, %Y at %I:%M %p"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# SMS and MMS exports number the mailbox rather than describing it.
_MAILBOX_COLUMNS = ("type", "msg_box")
_MAILBOX = {"1": "received", "2": "sent"}


def _direction(alias: str | None, value: Any, sender: str | None, self_name: str) -> str:
    if isinstance(value, bool):
        return "sent" if value else "received"
    if value is not None:
        s = str(value).strip().lower()
        if alias in _MAILBOX_COLUMNS and s in _MAILBOX:
            return _MAILBOX[s]
        if s in ("sent", "out", "outgoing", "true", "1", "yes", "me"):
            return "sent"
        if s in ("received", "in", "incoming", "inbound", "false", "0", "no", "them"):
            return "received"
    if sender and sender.strip().lower() == self_name.strip().lower():
        return "sent"
    return "received"


def _row_to_message(row: dict[str, Any], self_name: str, source: str) -> Message | None:
    text = _pick(row, "text")
    ts = parse_timestamp(_pick(row, "timestamp"))
    if text is None or ts is None:
        return None
    sender = _pick(row, "sender")
    alias, raw_direction = _pick_alias(row, "direction")
    direction = _direction(alias, raw_direction, sender, self_name)
    contact = _pick(row, "contact")
    if contact is None:
        # With no contact column the other party is the best we have; a row
        # that names nobody is "Unknown", never the string "None".
        contact = sender if (direction == "received" and sender) else "Unknown"
    if sender is None:
        sender = self_name if direction == "sent" else str(contact)
    return Message(contact=str(contact), sender=str(sender), direction=direction, timestamp=ts, text=str(text), source=source)


def parse_csv(data: bytes, self_name: str) -> Iterable[Message]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig", errors="replace")))
    for row in reader:
        msg = _row_to_message(row, self_name, "csv")
        if msg:
            yield msg


def parse_json(data: bytes, self_name: str) -> Iterable[Message]:
    payload = json.loads(data.decode("utf-8-sig", errors="replace"))
    if isinstance(payload, dict):
        # Accept {"messages": [...]} or {"contact name": [...]} shapes.
        if "messages" in payload and isinstance(payload["messages"], list):
            rows = payload["messages"]
        else:
            rows = []
            for contact, items in payload.items():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            rows.append({"contact": contact, **item})
    else:
        rows = payload
    for row in rows:
        if isinstance(row, dict):
            msg = _row_to_message(row, self_name, "json")
            if msg:
                yield msg
