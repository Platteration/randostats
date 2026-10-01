"""Telegram Desktop JSON export parser.

Telegram writes ``result.json`` holding either one chat or every chat::

    {"personal_information": {"user_id": 123, "first_name": "Sam"},
     "chats": {"list": [{"name": "Alex", "type": "personal_chat",
                         "messages": [{"type": "message", "date": "2024-01-02T21:15:00",
                                       "from": "Alex", "from_id": "user456", "text": "hey"}]}]}}

``text`` is a string, or a list mixing strings and ``{"type": ..., "text": ...}``
entities for links and formatting. Service entries (joins, calls, pins) carry
``type: "service"`` and are skipped.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from ..models import Message
from . import archive
from .timestamps import from_iso, from_unix

SKIP_CHAT_TYPES = {"public_channel", "private_channel", "public_supergroup"}


def _flatten(text) -> str:
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts = []
        for piece in text:
            if isinstance(piece, str):
                parts.append(piece)
            elif isinstance(piece, dict):
                parts.append(str(piece.get("text", "")))
        return "".join(parts)
    return ""


def _timestamp(entry: dict) -> datetime | None:
    """``date`` is the wall-clock time the exporter saw, which is what we want.
    ``date_unixtime`` is the same instant in UTC and only used as a fallback."""
    raw = entry.get("date")
    if isinstance(raw, str):
        moment = from_iso(raw)
        if moment is not None:
            return moment
    unix = entry.get("date_unixtime")
    if unix is not None:
        try:
            return from_unix(int(unix))
        except ValueError:
            return None
    return None


def _chats(payload: dict) -> list[dict]:
    chats = payload.get("chats")
    if isinstance(chats, dict) and isinstance(chats.get("list"), list):
        return chats["list"]
    if isinstance(payload.get("messages"), list):  # a single-chat export
        return [payload]
    return []


def _payloads(data: bytes) -> Iterable[object]:
    """The export documents, one at a time.

    Holding them all at once meant an archive of large documents was in memory
    twice over: as parsed JSON here and as the messages built from it.
    """
    if not archive.is_zip(data):
        yield json.loads(data.decode("utf-8-sig", errors="replace"))
        return
    found = False
    for _, doc in archive.json_files(data, contains="result.json"):
        found = True
        yield doc
    if not found:
        for _, doc in archive.json_files(data):
            yield doc


def parse(data: bytes, self_name: str) -> Iterable[Message]:
    for payload in _payloads(data):
        if not isinstance(payload, dict):
            continue
        me = payload.get("personal_information") or {}
        self_ids = {f"user{me['user_id']}"} if me.get("user_id") else set()
        self_names = {n.strip().lower() for n in
                      (self_name, f"{me.get('first_name', '')} {me.get('last_name', '')}".strip(), me.get("first_name", "")) if n and n.strip()}

        for chat in _chats(payload):
            if not isinstance(chat, dict) or chat.get("type") in SKIP_CHAT_TYPES:
                continue
            contact = chat.get("name") or chat.get("title") or "Saved messages"
            for entry in chat.get("messages") or []:
                if not isinstance(entry, dict) or entry.get("type") != "message":
                    continue
                text = _flatten(entry.get("text"))
                ts = _timestamp(entry)
                if not text or ts is None:
                    continue
                sender = str(entry.get("from") or "Unknown")
                is_me = str(entry.get("from_id") or "") in self_ids or sender.strip().lower() in self_names
                yield Message(contact=str(contact), sender=self_name if is_me else sender,
                              direction="sent" if is_me else "received", timestamp=ts, text=text, source="telegram")
