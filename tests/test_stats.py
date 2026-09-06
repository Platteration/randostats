from datetime import datetime, timedelta

import pytest

from randostats import stats
from randostats.models import Message


def msg(contact, direction, ts, text, sender=None):
    return Message(contact=contact, sender=sender or (("Me" if direction == "sent" else contact)), direction=direction, timestamp=ts, text=text)


@pytest.fixture
def messages():
    t0 = datetime(2024, 1, 1, 9, 0)  # a Monday
    return [
        msg("Alex", "received", t0, "hey are you around"),
        msg("Alex", "sent", t0 + timedelta(minutes=5), "yeah definately"),
        msg("Alex", "received", t0 + timedelta(minutes=6), "cool"),
        msg("Priya", "sent", t0 + timedelta(days=1, hours=13), "I recieved the invite tommorow"),
        msg("Priya", "received", t0 + timedelta(days=1, hours=13, minutes=30), "great, seperate cars tomorrow?"),
        msg("Priya", "sent", t0 + timedelta(days=1, hours=14), "https://example.com/wierdurl lol"),
    ]


def test_overview_and_contacts(messages):
    ov = stats.overview(messages)
    assert ov["total"] == 6 and ov["sent"] == 3 and ov["contacts"] == 2 and ov["days"] == 2
    rows = stats.contact_frequency(messages)
    assert [r["contact"] for r in rows] == ["Alex", "Priya"]
    assert rows[0]["sent"] == 1 and rows[0]["received"] == 2 and rows[0]["is_group"] is False


def test_timing(messages):
    t = stats.timing(messages)
    assert t["peak_weekday"] in ("Mon", "Tue")
    assert sum(sum(r) for r in t["heatmap"]) == 6
    assert t["by_hour"][9]["received"] == 2
    assert t["reply_latency"]["you_median_minutes"] == 17.5
    assert t["reply_latency"]["them_median_minutes"] == 15.5
    per_contact = stats.timing(messages, contact="Priya")
    assert per_contact["peak_hour"] == 22 or per_contact["peak_hour"] in (22, 23)


def test_misspellings_only_sent_and_ignores_urls_slang(messages):
    speller = stats.Speller()
    res = stats.misspellings(messages, speller, direction="sent")
    words = {w["word"]: w for w in res["words"]}
    assert set(words) == {"definately", "recieved", "tommorow"}
    assert words["definately"]["suggestion"] == "definitely"
    assert "wierdurl" not in words and "lol" not in words
    received = stats.misspellings(messages, speller, direction="received")
    assert [w["word"] for w in received["words"]] == ["seperate"]
    assert received["by_sender"][0]["sender"] == "Priya"


def test_word_frequency(messages):
    top = stats.word_frequency(messages, limit=5)
    assert top[0]["word"] in {"invite", "cars", "recieved", "tommorow"} or top
    assert all(w["word"] not in stats.STOPWORDS for w in top)
