"""SQLite persistence for imported messages.

One table, ``messages``, with a uniqueness constraint on
(contact, sender, timestamp, text) so re-importing the same export is a no-op.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import Message

DEFAULT_DB = Path(os.environ.get("RANDOSTATS_DB", "data/randostats.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY,
    contact   TEXT NOT NULL,
    sender    TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('sent', 'received')),
    ts        TEXT NOT NULL,
    text      TEXT NOT NULL,
    source    TEXT NOT NULL DEFAULT 'unknown',
    UNIQUE (contact, sender, ts, text)
);
CREATE INDEX IF NOT EXISTS idx_messages_contact ON messages (contact);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages (ts);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    # -- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # -- messages ---------------------------------------------------------
    def add_messages(self, messages: Iterable[Message]) -> int:
        """Insert messages, returning how many were new."""
        rows = [(m.contact, m.sender, m.direction, m.timestamp.isoformat(sep=" "), m.text, m.source) for m in messages]
        before = self.count()
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO messages (contact, sender, direction, ts, text, source) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return self.count() - before

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def all_messages(self, contact: str | None = None) -> list[Message]:
        sql = "SELECT contact, sender, direction, ts, text, source FROM messages"
        params: tuple = ()
        if contact:
            sql += " WHERE contact = ?"
            params = (contact,)
        sql += " ORDER BY ts"
        return [
            Message(contact=r["contact"], sender=r["sender"], direction=r["direction"],
                    timestamp=datetime.fromisoformat(r["ts"]), text=r["text"], source=r["source"])
            for r in self.conn.execute(sql, params)
        ]

    def contacts(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT contact FROM messages ORDER BY contact")]

    def clear(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM messages")

    def close(self) -> None:
        self.conn.close()
