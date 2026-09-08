"""SQLite persistence for imported messages.

One table, ``messages``, with a uniqueness constraint on
(contact, sender, timestamp, text) so re-importing the same export is a no-op.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import Message

DEFAULT_DB = Path(os.environ.get("RANDOSTATS_DB", "data/randostats.db"))

# What makes a message the same message on a second import. Deliberately not
# the stored `sender`: for messages you sent that holds the name you typed on
# the import form, so correcting a misspelling of your own name and importing
# again - exactly what the app tells you to do - used to double the database.
IDENTITY = "contact, direction, ts, text, CASE WHEN direction = 'sent' THEN '' ELSE sender END"

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
        # The API serves requests from a thread pool and shares one connection.
        # sqlite3.threadsafety is 3 on most builds, which makes that safe, but
        # it depends on how SQLite was compiled, so serialise here regardless.
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(_SCHEMA)
            self._ensure_identity_index()

    def _ensure_identity_index(self) -> None:
        """Add the identity index, collapsing any duplicates an older build left."""
        existing = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_messages_identity'").fetchone()
        if existing:
            return
        with self.conn:
            self.conn.execute(
                f"DELETE FROM messages WHERE id NOT IN (SELECT MIN(id) FROM messages GROUP BY {IDENTITY})")
            self.conn.execute(f"CREATE UNIQUE INDEX idx_messages_identity ON messages ({IDENTITY})")

    # -- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # -- messages ---------------------------------------------------------
    def add_messages(self, messages: Iterable[Message]) -> int:
        """Insert messages, returning how many were new."""
        rows = [(m.contact, m.sender, m.direction, m.timestamp.isoformat(sep=" "), m.text, m.source) for m in messages]
        with self.lock, self.conn:
            before = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            self.conn.executemany(
                "INSERT OR IGNORE INTO messages (contact, sender, direction, ts, text, source) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            after = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return after - before

    def count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def all_messages(self, contact: str | None = None) -> list[Message]:
        sql = "SELECT contact, sender, direction, ts, text, source FROM messages"
        params: tuple = ()
        if contact:
            sql += " WHERE contact = ?"
            params = (contact,)
        sql += " ORDER BY ts"
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [
            Message(contact=r["contact"], sender=r["sender"], direction=r["direction"],
                    timestamp=datetime.fromisoformat(r["ts"]), text=r["text"], source=r["source"])
            for r in rows
        ]

    def contacts(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.conn.execute("SELECT DISTINCT contact FROM messages ORDER BY contact")]

    def clear(self) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM messages")

    def close(self) -> None:
        self.conn.close()
