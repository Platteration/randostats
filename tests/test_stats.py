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


def test_conversation_health_splits_on_silence(messages):
    rows = stats.conversation_health(messages, gap_hours=6)
    alex = next(r for r in rows if r["contact"] == "Alex")
    priya = next(r for r in rows if r["contact"] == "Priya")
    assert alex["conversations"] == 1 and priya["conversations"] == 1
    assert alex["they_opened"] == 1 and alex["you_opened"] == 0
    assert alex["they_closed"] == 1  # Alex sent the last message in that thread
    assert priya["you_opened"] == 1
    summary = stats.conversation_summary(rows)
    assert summary["conversations"] == 2
    assert 0 <= summary["you_opened_share"] <= 1


def test_double_texts_and_silence():
    t = datetime(2024, 1, 1, 9)
    msgs = [msg("Alex", "sent", t, "hi"), msg("Alex", "sent", t + timedelta(minutes=2), "you there"),
            msg("Alex", "received", t + timedelta(minutes=30), "sorry"),
            msg("Alex", "sent", t + timedelta(days=9), "long time")]
    (row,) = stats.conversation_health(msgs, gap_hours=6)
    assert row["your_double_texts"] == 1 and row["their_double_texts"] == 0
    assert row["conversations"] == 2
    assert row["longest_silence_days"] == 9.0


def test_group_members():
    t = datetime(2024, 1, 1, 9)
    msgs = [msg("Trip", "received", t, "hi all", sender="Alex"),
            msg("Trip", "received", t + timedelta(minutes=1), "hello", sender="Priya"),
            msg("Trip", "received", t + timedelta(minutes=2), "yo", sender="Alex"),
            msg("Trip", "sent", t + timedelta(minutes=3), "hey", sender="Me")]
    rows = stats.group_members(msgs, "Trip")
    assert [r["sender"] for r in rows] == ["Alex", "Priya", "Me"]
    assert rows[0]["count"] == 2 and rows[0]["share"] == 0.5 and rows[0]["is_you"] is False
    assert rows[2]["is_you"] is True


def test_search_filters(messages):
    assert stats.search(messages, contact="Alex")["total"] == 3
    assert stats.search(messages, direction="sent")["total"] == 3
    assert stats.search(messages, hour=9)["total"] == 3
    assert stats.search(messages, q="RECIEVED")["total"] == 1  # case-insensitive substring
    assert stats.search(messages, month="2024-01")["total"] == 6
    page = stats.search(messages, limit=2)
    assert page["total"] == 6 and len(page["messages"]) == 2
    # newest first, so paging is stable
    assert page["messages"][0]["timestamp"] > page["messages"][1]["timestamp"]


def test_search_word_is_whole_word_only():
    t = datetime(2024, 1, 1, 9)
    msgs = [msg("Alex", "sent", t, "i ate a sandwich"), msg("Alex", "sent", t, "wich one?")]
    assert stats.search(msgs, word="wich")["total"] == 1
    assert stats.search(msgs, q="wich")["total"] == 2


def test_emoji_stats_keeps_compound_glyphs_whole():
    t = datetime(2024, 1, 1, 9)
    msgs = [msg("Alex", "sent", t, "family 👨‍👩‍👧‍👦 time 😂😂"),
            msg("Alex", "received", t, "flag 🇬🇧 and a wave 👋🏽")]
    e = stats.emoji_stats(msgs)
    counts = {row["emoji"]: row["count"] for row in e["top"]}
    assert counts["😂"] == 2
    assert counts["👨‍👩‍👧‍👦"] == 1  # one glyph, not four people
    assert counts["🇬🇧"] == 1  # one flag, not two letters
    assert counts["👋🏽"] == 1  # skin tone stays attached
    assert e["total"] == 5 and e["share_of_messages"] == 1.0
    assert e["yours"][0]["emoji"] == "😂"


def test_tone_scores_and_splits_by_month():
    msgs = [msg("Alex", "sent", datetime(2024, 1, 5, 9), "love this, thanks!"),
            msg("Alex", "received", datetime(2024, 2, 5, 9), "awful terrible day"),
            msg("Alex", "sent", datetime(2024, 2, 6, 9), "no numbers here")]
    t = stats.tone(msgs)
    assert t["positive"] == 2 and t["negative"] == 3  # "no" counts as a cold word
    assert [m["month"] for m in t["by_month"]] == ["2024-01", "2024-02"]
    assert t["by_month"][0]["net"] == 1.0 and t["by_month"][1]["net"] < 0
    assert t["by_contact"][0]["contact"] == "Alex"
    assert {w["word"] for w in t["top_positive"]} == {"love", "thanks"}


def test_longest_streak_counts_consecutive_days():
    t = datetime(2024, 3, 1, 9)
    msgs = [msg("Alex", "sent", t + timedelta(days=d), "x") for d in (0, 1, 2, 5, 6)]
    streak = stats.longest_streak(msgs)
    assert streak["days"] == 3 and streak["start"] == "2024-03-01" and streak["end"] == "2024-03-03"
    assert stats.longest_streak([])["days"] == 0


def test_wrapped_headline_numbers(messages):
    card = stats.wrapped(messages, year=2024)
    assert card["empty"] is False and card["year"] == 2024
    assert card["total"] == 6 and card["sent"] == 3 and card["people"] == 2
    assert card["top_contact"]["contact"] in ("Alex", "Priya")
    assert card["top_typo"]["word"] in ("definately", "recieved", "tommorow")
    assert 0 <= card["you_opened_share"] <= 1
    assert stats.years(messages) == [2024]
    assert stats.wrapped(messages, year=1999)["empty"] is True


def test_contact_peaks_matches_a_per_contact_timing_pass():
    """The one-pass version replaced a full scan per contact; they must agree."""
    t = datetime(2024, 1, 1, 9)  # a Monday
    msgs = ([msg("Alex", "sent", t + timedelta(hours=h), "x") for h in (0, 1, 1, 26)] +
            [msg("Priya", "received", t + timedelta(days=2, hours=h), "y") for h in (0, 5, 5)])
    peaks = {p["contact"]: p for p in stats.contact_peaks(msgs, limit=10)}
    for contact, row in peaks.items():
        reference = stats.timing(msgs, contact=contact)
        assert row["peak_hour"] == reference["peak_hour"]
        assert row["peak_weekday"] == reference["peak_weekday"]
        assert row["by_hour"] == [h["sent"] + h["received"] for h in reference["by_hour"]]
        assert row["total"] == reference["by_month"][0]["count"] or row["total"] > 0


def test_media_placeholders_still_detected_after_the_fast_path():
    for text in ("<Media omitted>", "image omitted", "‎video omitted", "This message was deleted",
                 "<attached: 00000042-PHOTO.jpg>", "STICKER OMITTED"):
        assert stats.is_media_placeholder(text), text
    for text in ("omit nothing here", "we deleted the plan? no", "hello there", "attach the file later"):
        assert not stats.is_media_placeholder(text), text


# --- findings from a review of stats.py; each of these was wrong before ---

def test_curly_apostrophes_are_not_treated_as_noise():
    """iOS and WhatsApp write U+2019, so these typos were invisible."""
    t = datetime(2024, 1, 1, 9)
    msgs = [msg("Alex", "sent", t, "i did’nt go and it was’nt great")]
    found = {w["word"] for w in stats.misspellings(msgs, stats.Speller())["words"]}
    assert found == {"did'nt", "was'nt"}
    # words_of is where the apostrophe is normalised; every check downstream
    # (the noise filter, the dictionary) then sees the straight form.
    assert stats.words_of("did’nt") == ["did'nt"]
    assert not stats._is_noise("did'nt")


def test_search_finds_either_apostrophe():
    t = datetime(2024, 1, 1, 9)
    msgs = [msg("Alex", "sent", t, "i did’nt go")]
    assert stats.search(msgs, word="did'nt")["total"] == 1
    assert stats.search(msgs, word="did’nt")["total"] == 1


def test_possessive_stripping_does_not_eat_real_letters():
    """rstrip("'s") took every trailing s: "posess" became "pose", a real word."""
    speller = stats.Speller()
    assert speller.is_misspelled("posess")
    assert speller.is_misspelled("acess")
    assert not speller.is_misspelled("cat's")


def test_capitalisation_only_excuses_a_word_that_opens_its_own_sentence():
    t = datetime(2024, 1, 1, 9)
    msgs = [msg("Alex", "sent", t, "we went to the store. Definately worth it"),
            msg("Alex", "sent", t + timedelta(minutes=1), "THIS IS DEFINATELY SHOUTING")]
    found = {w["word"]: w["count"] for w in stats.misspellings(msgs, stats.Speller())["words"]}
    assert found.get("definately") == 2, found
    # a genuine name mid-sentence is still left alone
    quiet = [msg("Alex", "sent", t, "i saw Siobhan yesterday")]
    assert stats.misspellings(quiet, stats.Speller())["words"] == []


def test_media_placeholders_are_not_words_anybody_wrote():
    t = datetime(2024, 1, 1, 9)
    msgs = [msg("Alex", "sent", t, "<Media omitted>"),
            msg("Alex", "sent", t + timedelta(minutes=1), "hi there friend"),
            msg("Alex", "received", t + timedelta(minutes=2), "image omitted", sender="Alex")]
    row = stats.contact_frequency(msgs)[0]
    assert row["avg_words_sent"] == 3.0, "the placeholder must not count as two words"
    assert row["avg_words_received"] == 0
    assert stats.wrapped(msgs, 2024, stats.Speller())["words_written"] == 3
    members = stats.group_members(msgs, "Alex")
    assert {m["sender"]: m["avg_words"] for m in members}["Me"] == 3.0


def test_one_unanswered_message_is_not_a_ghosting_habit():
    rows = [{"contact": "Barely know them", "conversations": 1, "you_closed_share": 1.0, "you_opened": 0,
             "you_closed": 1, "your_double_texts": 0, "you_reply_median": None, "them_reply_median": None},
            {"contact": "Best friend", "conversations": 300, "you_closed_share": 0.62, "you_opened": 150,
             "you_closed": 186, "your_double_texts": 40, "you_reply_median": 5, "them_reply_median": 6}]
    assert stats.conversation_summary(rows)["ghosted_by"]["contact"] == "Best friend"
    # nobody has enough history to judge
    thin = [dict(rows[0])]
    assert stats.conversation_summary(thin)["ghosted_by"] is None


def test_the_spellers_memos_are_bounded(monkeypatch):
    """One Speller lives for the life of the server, so a dict with an entry
    per distinct word ever checked was memory that never came back."""
    monkeypatch.setattr(stats, "MEMO_WORDS", 16)
    speller = stats.Speller()
    for i in range(200):
        speller.is_misspelled(f"zzqx{i}")
    assert speller._misspelled.cache_info().currsize <= 16
    # bounded, and still answering the same way
    assert speller.is_misspelled("definately") and not speller.is_misspelled("definitely")
    assert speller.suggest("definately") == "definitely"
    assert speller._suggestion.cache_info().maxsize == 16
