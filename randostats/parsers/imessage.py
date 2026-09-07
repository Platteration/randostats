"""macOS Messages.app ``chat.db`` parser.

Reads the SQLite file directly. The ``date`` column is nanoseconds (or
seconds on very old macOS) since 2001-01-01. Messages with an empty ``text``
but an ``attributedBody`` are decoded loosely from the typedstream blob.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ..models import Message
from .timestamps import from_apple

_QUERY = """
SELECT m.ROWID, m.date, m.is_from_me, m.text, m.attributedBody,
       h.id AS handle, c.chat_identifier, c.display_name
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
LEFT JOIN chat c ON c.ROWID = cmj.chat_id
ORDER BY m.date
"""


def _apple_date(raw: int | float | None) -> datetime | None:
    if raw is None:
        return None
    raw = float(raw)
    if raw > 1e12:  # nanoseconds on modern macOS
        raw /= 1e9
    return from_apple(raw)


def _decode_attributed_body(blob: bytes | None) -> str:
    """Pull the plain string out of an NSAttributedString typedstream blob."""
    if not blob:
        return ""
    idx = blob.find(b"NSString")
    if idx == -1:
        return ""
    rest = blob[idx + 8:]
    # Skip the class-info bytes, then a length prefix (1 byte, or 0x81 + 2 bytes).
    rest = rest[5:] if len(rest) > 5 else rest
    if not rest:
        return ""
    if rest[0] == 0x81:
        length = int.from_bytes(rest[1:3], "little")
        rest = rest[3:]
    else:
        length = rest[0]
        rest = rest[1:]
    text = rest[:length].decode("utf-8", errors="ignore")
    return re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)


def parse(data: bytes, self_name: str) -> Iterable[Message]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(data)
        path = Path(tmp.name)
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(_QUERY).fetchall()
        finally:
            conn.close()
    finally:
        path.unlink(missing_ok=True)

    seen: set[int] = set()
    for row in rows:
        if row["ROWID"] in seen:
            continue
        seen.add(row["ROWID"])
        ts = _apple_date(row["date"])
        text = row["text"] or _decode_attributed_body(row["attributedBody"])
        if ts is None or not text:
            continue
        handle = row["handle"] or "Unknown"
        contact = row["display_name"] or row["chat_identifier"] or handle
        is_me = bool(row["is_from_me"])
        yield Message(
            contact=str(contact),
            sender=self_name if is_me else str(handle),
            direction="sent" if is_me else "received",
            timestamp=ts,
            text=str(text),
            source="imessage",
        )
