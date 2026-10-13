"""macOS Messages.app ``chat.db`` parser.

Reads the SQLite file directly. The ``date`` column is nanoseconds (or
seconds on very old macOS) since 2001-01-01. Messages with an empty ``text``
but an ``attributedBody`` are decoded loosely from the typedstream blob.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
import time
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

# The four relations ``_QUERY`` reads. The file is other people's data, opened
# read-only - but read-only mode stops writes, not *computation*. A ``message``
# that is really ``CREATE VIEW message AS WITH RECURSIVE ... `` makes the
# ORDER BY sort an endless stream and spill temp files until the disk is full
# (RS-R1), and a view is the uploader's to define however they like. So each
# of these must be an ordinary table before the query runs: a table's rows are
# the finite rows the file stores, which the upload cap already bounds. A
# generated column could carry a per-row expression, so those are refused too
# (SQLite forbids subqueries and recursion in one, so it cannot itself be
# unbounded, but the plain shape is the one a real chat.db has).
_REQUIRED_TABLES = ("message", "handle", "chat", "chat_message_join")

# Even with ordinary tables, the joins can fan out: ``chat_message_join`` with
# many rows per message multiplies the rows the ORDER BY must sort, so a file
# within the upload cap can still make the sort run for minutes and spill
# gigabytes of temp (two 2M-row tables fully crossed is 4e12 rows). A real
# chat.db of a million messages sorts in about a second; a file at the 256 MB
# upload cap holds at most ~4M rows and sorts in about five. This budget is
# well above that and below the attack, and a progress handler trips it so the
# query is interrupted rather than left to fill the disk.
_QUERY_BUDGET_SECONDS = 10.0


class UnexpectedSchema(ValueError):
    """The uploaded database is not a plain Messages.app ``chat.db``."""


def _assert_plain_schema(conn: sqlite3.Connection) -> None:
    kinds = dict(conn.execute(
        "SELECT name, type FROM sqlite_master WHERE name IN (?, ?, ?, ?)", _REQUIRED_TABLES).fetchall())
    for name in _REQUIRED_TABLES:
        kind = kinds.get(name)
        if kind is None:
            raise UnexpectedSchema(f"this does not look like a Messages chat.db: no {name} table")
        if kind != "table":
            raise UnexpectedSchema(f"{name} is a {kind}, not a table; a chat.db this app will read has plain tables")
        # table_xinfo's last field is the hidden flag: 2 and 3 are STORED and
        # VIRTUAL generated columns, which a plain table never has.
        if any(row[6] in (2, 3) for row in conn.execute(f"PRAGMA table_xinfo({name})")):
            raise UnexpectedSchema(f"{name} has a generated column; a chat.db this app will read has plain tables")


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
            _assert_plain_schema(conn)
            deadline = time.monotonic() + _QUERY_BUDGET_SECONDS
            # Called every ~100,000 VM steps; a non-zero return interrupts the
            # query (sqlite3.OperationalError), which the caller shows as a
            # refusal. This fires all through the sort, so a fanned-out join is
            # stopped before it fills the disk, not after.
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 100_000)
            try:
                rows = conn.execute(_QUERY).fetchall()
            except sqlite3.OperationalError as exc:
                raise UnexpectedSchema(
                    "reading this chat.db took too long and was stopped; it holds far more rows than a "
                    "real Messages database, or its tables cross-reference each other") from exc
            conn.set_progress_handler(None, 0)
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
