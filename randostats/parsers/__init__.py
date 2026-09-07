"""Parsers that turn message exports into ``Message`` objects.

Supported formats:

* ``whatsapp``  - WhatsApp "Export chat" .txt files (with or without media)
* ``csv``       - generic CSV with contact/sender/direction/timestamp/text columns
* ``json``      - generic JSON list of message objects (same fields as CSV)
* ``imessage``  - macOS ``chat.db`` SQLite database (Messages.app)
* ``smsbackup`` - "SMS Backup & Restore" XML exports on Android
* ``telegram``  - Telegram Desktop JSON export (``result.json``, or its zip)
* ``discord``   - Discord data package (your own messages only)
* ``meta``      - Instagram and Facebook Messenger downloads

``detect_format`` sniffs the file so the UI can auto-pick one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from ..models import Message
from . import archive, discord, generic, imessage, meta, smsbackup, telegram, whatsapp

Parser = Callable[[bytes, str], Iterable[Message]]

PARSERS: dict[str, Parser] = {
    "whatsapp": whatsapp.parse,
    "csv": generic.parse_csv,
    "json": generic.parse_json,
    "imessage": imessage.parse,
    "smsbackup": smsbackup.parse,
    "telegram": telegram.parse,
    "discord": discord.parse,
    "meta": meta.parse,
}


def _sniff_json(head: bytes) -> str:
    """Tell the JSON exports apart by the keys only one of them uses."""
    probe = head[:8192].decode("utf-8", errors="replace")
    if '"text_entities"' in probe or '"from_id"' in probe or '"personal_information"' in probe:
        return "telegram"
    if '"sender_name"' in probe or '"participants"' in probe:
        return "meta"
    if '"Contents"' in probe and '"Timestamp"' in probe:
        return "discord"
    return "json"


def detect_format(filename: str, data: bytes) -> str | None:
    """Best-effort guess at the export format from the filename and contents.

    Pass the whole file for archives: a zip's index lives at the end, so a
    prefix cannot say what is inside it.
    """
    name = Path(filename).name.lower()
    if data.startswith(b"SQLite format 3"):
        return "imessage"
    if archive.is_zip(data):
        inside = " ".join(archive.names(data)).lower()
        if "result.json" in inside or "chats" in inside and "telegram" in name:
            return "telegram"
        if "message_1.json" in inside or "/inbox/" in inside:
            return "meta"
        if "channel.json" in inside or "/messages/c" in inside:
            return "discord"
        return None
    stripped = data.lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<smses"):
        return "smsbackup"
    if stripped.startswith(b"[") or stripped.startswith(b"{"):
        return _sniff_json(stripped)
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".txt") or whatsapp.looks_like_whatsapp(data[:4096]):
        return "whatsapp"
    return None


def parse(fmt: str, data: bytes, self_name: str) -> list[Message]:
    """Parse ``data`` in format ``fmt``; ``self_name`` identifies the user's own messages."""
    try:
        parser = PARSERS[fmt]
    except KeyError as exc:
        raise ValueError(f"unknown format {fmt!r}; expected one of {sorted(PARSERS)}") from exc
    return list(parser(data, self_name))
