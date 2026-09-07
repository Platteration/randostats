"""Instagram and Facebook Messenger export parser.

Both products write ``messages/inbox/<thread>/message_1.json``::

    {"participants": [{"name": "Alex"}, {"name": "Sam"}],
     "messages": [{"sender_name": "Alex", "timestamp_ms": 1704229200000, "content": "hey"}],
     "title": "Alex"}

Meta writes UTF-8 bytes escaped as Latin-1, so "café" arrives as "cafÃ©".
``mojibake`` puts that back together.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable

from ..models import Message
from . import archive


def mojibake(text: str) -> str:
    """Undo Meta's Latin-1-over-UTF-8 encoding, leaving already-correct text alone."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _timestamp(ms) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        return None


def _thread(payload: dict, self_name: str) -> Iterable[Message]:
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        return
    participants = [mojibake(str(p.get("name", ""))) for p in payload.get("participants", []) if isinstance(p, dict)]
    others = [p for p in participants if p.strip().lower() != self_name.strip().lower()]
    title = mojibake(str(payload.get("title", ""))) if payload.get("title") else ""
    contact = title or (others[0] if len(others) == 1 else "Group chat")
    source = "instagram" if "instagram" in str(payload.get("thread_path", "")).lower() else "messenger"

    for entry in payload["messages"]:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        ts = _timestamp(entry.get("timestamp_ms"))
        if not content or ts is None:
            continue
        sender = mojibake(str(entry.get("sender_name", "Unknown")))
        is_me = sender.strip().lower() == self_name.strip().lower()
        yield Message(contact=contact, sender=self_name if is_me else sender,
                      direction="sent" if is_me else "received", timestamp=ts,
                      text=mojibake(str(content)), source=source)


def parse(data: bytes, self_name: str) -> Iterable[Message]:
    if archive.is_zip(data):
        seen: set[str] = set()
        for name, payload in archive.json_files(data, contains="message_"):
            if name in seen:
                continue
            seen.add(name)
            yield from _thread(payload, self_name)
        return
    yield from _thread(json.loads(data.decode("utf-8-sig", errors="replace")), self_name)
