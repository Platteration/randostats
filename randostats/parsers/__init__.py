"""Parsers that turn message exports into ``Message`` objects.

Supported formats:

* ``whatsapp``  - WhatsApp "Export chat" .txt files (with or without media)
* ``csv``       - generic CSV with contact/sender/direction/timestamp/text columns
* ``json``      - generic JSON list of message objects (same fields as CSV)
* ``imessage``  - macOS ``chat.db`` SQLite database (Messages.app)
* ``smsbackup`` - "SMS Backup & Restore" XML exports on Android

``detect_format`` sniffs the file so the UI can auto-pick one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from ..models import Message
from . import generic, imessage, smsbackup, whatsapp

Parser = Callable[[bytes, str], Iterable[Message]]

PARSERS: dict[str, Parser] = {
    "whatsapp": whatsapp.parse,
    "csv": generic.parse_csv,
    "json": generic.parse_json,
    "imessage": imessage.parse,
    "smsbackup": smsbackup.parse,
}


def detect_format(filename: str, head: bytes) -> str | None:
    """Best-effort guess at the export format from the filename and first bytes."""
    name = Path(filename).name.lower()
    if head.startswith(b"SQLite format 3"):
        return "imessage"
    stripped = head.lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<smses"):
        return "smsbackup"
    if stripped.startswith(b"[") or stripped.startswith(b"{"):
        return "json"
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".txt") or whatsapp.looks_like_whatsapp(head):
        return "whatsapp"
    return None


def parse(fmt: str, data: bytes, self_name: str) -> list[Message]:
    """Parse ``data`` in format ``fmt``; ``self_name`` identifies the user's own messages."""
    try:
        parser = PARSERS[fmt]
    except KeyError as exc:
        raise ValueError(f"unknown format {fmt!r}; expected one of {sorted(PARSERS)}") from exc
    return list(parser(data, self_name))
