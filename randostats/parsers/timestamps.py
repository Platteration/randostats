"""One place that decides what a timestamp means.

Exports disagree: WhatsApp and Telegram write the local wall-clock time the
sender saw, while iMessage, Android SMS, Discord and Meta write an instant in
UTC. Mixing the two puts the same 7pm message at 7pm from one app and 11pm
from another, which quietly ruins every hour-of-day chart.

Everything is normalised to **local wall-clock time on this machine**, stored
naive. That is what "when do you message people" means to a person, and it
matches what the chat app itself showed them.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Seconds between the Unix epoch and Apple's 2001-01-01 reference date.
APPLE_EPOCH_OFFSET = 978_307_200


def from_unix(seconds: float) -> datetime | None:
    """A UTC instant in seconds since 1970, as local wall-clock time."""
    try:
        return datetime.fromtimestamp(seconds)
    except (OverflowError, OSError, ValueError):
        return None


def from_unix_ms(milliseconds: float) -> datetime | None:
    return from_unix(milliseconds / 1000)


def from_apple(seconds: float) -> datetime | None:
    """Apple's Core Data reference date, used by the Messages database."""
    return from_unix(seconds + APPLE_EPOCH_OFFSET)


def from_iso(text: str) -> datetime | None:
    """An ISO 8601 string. One carrying an offset is moved to local time; a
    naive one is already local wall-clock and is kept as written."""
    try:
        parsed = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def as_local(moment: datetime) -> datetime:
    """Drop an aware datetime into local wall-clock time."""
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


__all__ = ["from_unix", "from_unix_ms", "from_apple", "from_iso", "as_local", "timezone"]
