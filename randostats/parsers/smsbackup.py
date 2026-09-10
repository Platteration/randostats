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


_UTF16_BOM = (b"\xff\xfe", b"\xfe\xff")
_UTF32_BOM = (b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")


def decode(data: bytes) -> str:
    """The document as characters, in the encoding the parser will read it in.

    The entity check below has to read what expat reads, and expat does not
    read bytes the way a byte scan does: it takes its encoding from a
    byte-order mark or, with no mark at all, from where the NUL bytes fall in
    the first four. A document entity can only begin with an ASCII character
    (XML 1.0, appendix F), so a UTF-16 document always pairs one of its
    bytes with a NUL and is auto-detected from that alone - and in UTF-16 the string
    "<!ENTITY" shares no bytes at all with its UTF-8 spelling. A guard that
    only knew about the mark was reading a different document than the parser.

    NUL is not a legal XML character in any encoding expat accepts, so a NUL
    anywhere in the first four bytes means UTF-16 or UTF-32 and nothing else.
    No export of this format is either, so those are refused rather than
    decoded. What comes back is handed to the parser as *text*, which pins it
    to the encoding decided here: expat parses a str as UTF-8 and ignores any
    encoding the declaration names, so it cannot pick a second one.
    """
    if data[:2] in _UTF16_BOM or data[:4] in _UTF32_BOM or b"\x00" in data[:4]:
        raise ValueError("this XML is UTF-16 or UTF-32; re-export it as UTF-8")
    return data.decode("utf-8-sig", errors="replace")


def parse(data: bytes, self_name: str) -> Iterable[Message]:
    # ElementTree expands internal entities, so a few hundred bytes of nested
    # declarations can inflate into a great deal of memory. (libexpat has
    # capped the amplification factor since 2.4, but the runtime's copy may be
    # older than that.) No real backup declares any, so refuse the file.
    text = decode(data)
    if "<!ENTITY" in text:
        raise ValueError("this XML declares entities, which this importer will not expand")
    root = ET.fromstring(text)
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
