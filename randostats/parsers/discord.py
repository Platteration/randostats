"""Discord data package parser.

The package holds ``messages/c<channel id>/messages.json`` (your messages)
beside ``channel.json`` (who the channel was with). Discord only exports
what *you* wrote, so every message here is outgoing; the app labels the
import accordingly rather than inventing the other half of the conversation.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from ..models import Message
from . import archive
from .timestamps import from_iso


def _timestamp(raw: str | None) -> datetime | None:
    return from_iso(raw) if raw else None


def _channel_name(meta: dict | None, fallback: str) -> str:
    if not isinstance(meta, dict):
        return fallback
    recipients = meta.get("recipients")
    if isinstance(recipients, list) and recipients:
        names = [r.get("username") if isinstance(r, dict) else str(r) for r in recipients]
        named = [n for n in names if n]
        if named:
            return ", ".join(named)
    if meta.get("name"):
        return str(meta["name"])
    guild = (meta.get("guild") or {}).get("name") if isinstance(meta.get("guild"), dict) else None
    return f"{guild} #{meta.get('id')}" if guild else fallback


def parse(data: bytes, self_name: str) -> Iterable[Message]:
    if not archive.is_zip(data):
        rows = json.loads(data.decode("utf-8-sig", errors="replace"))
        yield from _rows(rows, "Discord", self_name)
        return

    channels = {name.rsplit("/", 1)[0]: doc for name, doc in archive.json_files(data, contains="channel.json")}
    for name, rows in archive.json_files(data, contains="messages.json"):
        folder = name.rsplit("/", 1)[0]
        contact = _channel_name(channels.get(folder), folder.rsplit("/", 1)[-1])
        yield from _rows(rows, contact, self_name)


def _rows(rows, contact: str, self_name: str) -> Iterable[Message]:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = row.get("Contents") or row.get("contents") or ""
        ts = _timestamp(row.get("Timestamp") or row.get("timestamp"))
        if not text or ts is None:
            continue
        yield Message(contact=contact, sender=self_name, direction="sent", timestamp=ts, text=str(text), source="discord")
