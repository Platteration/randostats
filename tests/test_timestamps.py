"""Every export must land on the same wall-clock hour.

Exports disagree about what a timestamp means: some write local time, some
write UTC. Left alone, the same 7pm message shows up at 7pm from WhatsApp and
11pm from Messenger, and every hour-of-day chart in the app is wrong.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import pytest

from randostats import parsers

pytestmark = pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs a POSIX timezone")


@pytest.fixture
def new_york():
    """Run the test on a machine four hours behind UTC in July."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    yield
    if before is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = before
    time.tzset()


# One instant: 19:00 in New York on 1 July 2024, which is 23:00 UTC.
LOCAL = datetime(2024, 7, 1, 19, 0)
EPOCH_MS = 1_719_874_800_000


def _one(msgs):
    msgs = list(msgs)
    assert len(msgs) == 1, msgs
    return msgs[0]


def test_every_source_agrees_on_the_hour(new_york):
    whatsapp = _one(parsers.whatsapp.parse(b"7/1/24, 7:00 PM - Alex: hi\n", "Sam"))

    sms = _one(parsers.smsbackup.parse(
        f'<?xml version="1.0"?><smses><sms address="1" date="{EPOCH_MS}" type="1" '
        f'body="hi" contact_name="Alex"/></smses>'.encode(), "Sam"))

    meta = _one(parsers.meta.parse(json.dumps({
        "participants": [{"name": "Alex"}], "title": "Alex",
        "messages": [{"sender_name": "Alex", "timestamp_ms": EPOCH_MS, "content": "hi"}]}).encode(), "Sam"))

    telegram = _one(parsers.telegram.parse(json.dumps({"chats": {"list": [{
        "name": "Alex", "type": "personal_chat", "messages": [
            {"type": "message", "date": "2024-07-01T19:00:00", "date_unixtime": str(EPOCH_MS // 1000),
             "from": "Alex", "text": "hi"}]}]}}).encode(), "Sam"))

    discord = _one(parsers.discord.parse(json.dumps(
        [{"ID": "1", "Timestamp": "2024-07-01T23:00:00+00:00", "Contents": "hi"}]).encode(), "Sam"))

    generic = _one(parsers.generic.parse_json(json.dumps(
        [{"contact": "Alex", "direction": "received", "timestamp": EPOCH_MS // 1000, "text": "hi"}]).encode(), "Sam"))

    found = {"whatsapp": whatsapp.timestamp, "sms": sms.timestamp, "meta": meta.timestamp,
             "telegram": telegram.timestamp, "discord": discord.timestamp, "generic": generic.timestamp}
    assert set(found.values()) == {LOCAL}, f"sources disagree on the hour: {found}"


def test_iso_with_an_offset_moves_to_local_time(new_york):
    from randostats.parsers.timestamps import from_iso

    assert from_iso("2024-07-01T23:00:00Z") == LOCAL
    assert from_iso("2024-07-01T23:00:00+00:00") == LOCAL
    assert from_iso("2024-07-02T08:00:00+09:00") == LOCAL  # Tokyo evening, New York morning
    # a naive string is already the wall clock the sender saw, so it is kept
    assert from_iso("2024-07-01T19:00:00") == LOCAL
    assert from_iso("not a date") is None


def test_apple_reference_date(new_york):
    from randostats.parsers.timestamps import from_apple

    # 1 July 2024 23:00 UTC counted from 2001-01-01
    assert from_apple(EPOCH_MS // 1000 - 978_307_200) == LOCAL


def test_telegram_prefers_its_wall_clock_over_the_utc_field(new_york):
    """Telegram writes both; `date` is what the exporter actually saw."""
    payload = json.dumps({"chats": {"list": [{"name": "Alex", "type": "personal_chat", "messages": [
        {"type": "message", "date": "2024-07-01T19:00:00", "date_unixtime": "1", "from": "Alex", "text": "hi"}]}]}})
    assert _one(parsers.telegram.parse(payload.encode(), "Sam")).timestamp == LOCAL


def test_a_bad_timestamp_drops_the_message_rather_than_guessing():
    payload = json.dumps([{"contact": "Alex", "direction": "received", "timestamp": "whenever", "text": "hi"}])
    assert list(parsers.generic.parse_json(payload.encode(), "Sam")) == []
