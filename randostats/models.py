"""Core data types shared by parsers, the store, and the stats engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Message:
    """One message in a conversation.

    ``contact`` is the *other* party (or the group name), ``direction`` is
    "sent" when the user wrote it and "received" otherwise. ``sender`` keeps
    the raw author name so group chats can still be broken down per person.
    """

    contact: str
    sender: str
    direction: str  # "sent" | "received"
    timestamp: datetime
    text: str
    source: str = "unknown"

    def __post_init__(self) -> None:
        if self.direction not in ("sent", "received"):
            raise ValueError(f"direction must be 'sent' or 'received', got {self.direction!r}")
