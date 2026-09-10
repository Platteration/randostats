"""Parser for Android "SMS Backup & Restore" XML files.

Elements look like ``<sms address="+15551234567" date="1700000000000" type="1"
body="hi" contact_name="Alice" />`` where ``type`` 1 is received and 2 is sent.
MMS entries carry their text in nested ``<part ct="text/plain" text="..."/>``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Iterable

from ..models import Message
from .timestamps import from_unix_ms


def _ts(ms: str | None) -> datetime | None:
    if not ms:
        return None
    try:
        return from_unix_ms(int(ms))
    except ValueError:
        return None


def parse(data: bytes, self_name: str) -> Iterable[Message]:
    # ElementTree expands internal entities, so a few hundred bytes of nested
    # declarations can inflate into a great deal of memory. (libexpat has
    # capped the amplification factor since 2.4, but the runtime's copy may be
    # older than that.) No real backup declares any, so refuse the file.
    #
    # The check is on bytes, and expat picks its encoding up from a
    # byte-order mark: in UTF-16 a declaration holds no "<!ENTITY" bytes at
    # all, so the guard used to miss it entirely. No export of this format is
    # UTF-16 or UTF-32, so those are refused before the check that follows.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or data[:4] == b"\x00\x00\xfe\xff":
        raise ValueError("this XML is UTF-16 or UTF-32; re-export it as UTF-8")
    if b"<!ENTITY" in data:
        raise ValueError("this XML declares entities, which this importer will not expand")
    root = ET.fromstring(data)
    for el in root.iter():
        if el.tag == "sms":
            ts = _ts(el.get("date"))
            body = el.get("body") or ""
            if ts is None or not body:
                continue
            sent = el.get("type") == "2"
            contact = el.get("contact_name") or el.get("address") or "Unknown"
            if contact == "(Unknown)":
                contact = el.get("address") or "Unknown"
            yield Message(contact=contact, sender=self_name if sent else contact,
                          direction="sent" if sent else "received", timestamp=ts, text=body, source="sms")
        elif el.tag == "mms":
            ts = _ts(el.get("date"))
            texts = [p.get("text") or "" for p in el.iter("part") if (p.get("ct") or "").startswith("text/plain")]
            body = "\n".join(t for t in texts if t).strip()
            if ts is None or not body:
                continue
            sent = el.get("msg_box") == "2"
            contact = el.get("contact_name") or el.get("address") or "Unknown"
            if contact == "(Unknown)":
                contact = el.get("address") or "Unknown"
            yield Message(contact=contact, sender=self_name if sent else contact,
                          direction="sent" if sent else "received", timestamp=ts, text=body, source="mms")
