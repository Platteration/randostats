from datetime import datetime

import pytest

from randostats import parsers
from randostats.parsers import generic, smsbackup, whatsapp

WA_US = """1/2/24, 9:15 PM - Alex: hey are you around
1/2/24, 9:16 PM - Sam: yeah what's up
this is a continuation line
1/2/24, 9:17 PM - Alex: <Media omitted>
"""
WA_INTL = """[14/03/2024, 23:59:01] Alex: late one
[15/03/2024, 00:01:12] Sam: very
"""


def test_whatsapp_us_format_and_continuation():
    msgs = list(whatsapp.parse(WA_US.encode(), "Sam"))
    assert [m.direction for m in msgs] == ["received", "sent", "received"]
    assert msgs[1].text == "yeah what's up\nthis is a continuation line"
    assert msgs[0].timestamp == datetime(2024, 1, 2, 21, 15)
    assert {m.contact for m in msgs} == {"Alex"}


def test_whatsapp_bracketed_day_first():
    msgs = list(whatsapp.parse(WA_INTL.encode(), "Sam"))
    assert msgs[0].timestamp == datetime(2024, 3, 14, 23, 59, 1)
    assert msgs[1].direction == "sent"


def test_whatsapp_group_detection():
    text = "1/2/24, 9:15 PM - Alex: hi\n1/2/24, 9:16 PM - Priya: hello\n1/2/24, 9:17 PM - Sam: hey all\n"
    msgs = list(whatsapp.parse(text.encode(), "Sam"))
    assert msgs[0].contact == "Group chat"
    assert list(whatsapp.parse(text.encode(), "Sam", contact="Trip planning"))[0].contact == "Trip planning"


def test_csv_and_json_generic():
    csv = "contact,sender,direction,timestamp,text\nAlex,Alex,received,2024-01-02T21:15:00,hi\nAlex,Sam,sent,1704230220,hello\n"
    msgs = list(generic.parse_csv(csv.encode(), "Sam"))
    assert len(msgs) == 2 and msgs[1].direction == "sent" and msgs[1].timestamp.year == 2024
    js = b'[{"chat": "Alex", "from": "Sam", "is_from_me": true, "date": "2024-01-02 21:15", "body": "yo"}]'
    (m,) = generic.parse_json(js, "Sam")
    assert m.direction == "sent" and m.contact == "Alex" and m.text == "yo"


def test_smsbackup_xml():
    xml = b"""<?xml version='1.0'?><smses count="2">
    <sms address="+15551234567" date="1704230100000" type="1" body="hi" contact_name="Alex"/>
    <sms address="+15551234567" date="1704230200000" type="2" body="hello" contact_name="Alex"/>
    <mms address="+15551234567" date="1704230300000" msg_box="1" contact_name="Alex"><parts><part ct="text/plain" text="pic caption"/></parts></mms>
    </smses>"""
    msgs = list(smsbackup.parse(xml, "Sam"))
    assert [m.direction for m in msgs] == ["received", "sent", "received"]
    assert msgs[2].text == "pic caption"


def test_detect_format():
    assert parsers.detect_format("chat.db", b"SQLite format 3\x00") == "imessage"
    assert parsers.detect_format("sms.xml", b"<?xml version='1.0'?>") == "smsbackup"
    assert parsers.detect_format("x.json", b"[{}]") == "json"
    assert parsers.detect_format("x.csv", b"a,b") == "csv"
    assert parsers.detect_format("WhatsApp Chat with Alex.txt", WA_US.encode()) == "whatsapp"


import inspect
import io
import json as _json
import zipfile

from randostats.parsers import discord, meta, telegram

TELEGRAM = {
    "personal_information": {"user_id": 111, "first_name": "Sam"},
    "chats": {"list": [
        {"name": "Alex", "type": "personal_chat", "messages": [
            {"type": "message", "date": "2024-01-02T21:15:00", "from": "Alex", "from_id": "user222", "text": "hey"},
            {"type": "message", "date": "2024-01-02T21:16:00", "from": "Sam", "from_id": "user111",
             "text": ["look at ", {"type": "link", "text": "example.com"}, " please"]},
            {"type": "service", "date": "2024-01-02T21:17:00", "action": "phone_call"},
        ]},
        {"name": "Some Channel", "type": "public_channel", "messages": [
            {"type": "message", "date": "2024-01-02T21:18:00", "from": "Bot", "text": "ad"}]},
    ]},
}


def test_telegram_json_flattens_entities_and_skips_service():
    msgs = list(telegram.parse(_json.dumps(TELEGRAM).encode(), "Sam"))
    assert [m.direction for m in msgs] == ["received", "sent"]
    assert msgs[1].text == "look at example.com please"
    assert msgs[0].contact == "Alex" and msgs[0].timestamp == datetime(2024, 1, 2, 21, 15)
    assert all(m.contact != "Some Channel" for m in msgs)  # channels are broadcasts, not chats


def test_telegram_identifies_you_by_id_even_under_another_name():
    msgs = list(telegram.parse(_json.dumps(TELEGRAM).encode(), "Someone Else"))
    assert [m.direction for m in msgs] == ["received", "sent"]


def test_telegram_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("DataExport/result.json", _json.dumps(TELEGRAM))
    data = buf.getvalue()
    assert parsers.detect_format("telegram.zip", data) == "telegram"
    assert len(list(telegram.parse(data, "Sam"))) == 2


DISCORD_ROWS = [{"ID": "1", "Timestamp": "2024-01-02 21:15:00", "Contents": "hello there", "Attachments": ""},
                {"ID": "2", "Timestamp": "2024-01-02 21:16:00", "Contents": "", "Attachments": "img.png"}]


def test_discord_package_names_the_channel_and_marks_everything_sent():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("messages/c123/channel.json", _json.dumps({"id": "123", "type": 1, "recipients": [{"username": "alex"}]}))
        zf.writestr("messages/c123/messages.json", _json.dumps(DISCORD_ROWS))
    data = buf.getvalue()
    assert parsers.detect_format("package.zip", data) == "discord"
    msgs = list(discord.parse(data, "Sam"))
    assert len(msgs) == 1  # the attachment-only row has no text
    assert msgs[0].contact == "alex" and msgs[0].direction == "sent" and msgs[0].sender == "Sam"


META_THREAD = {
    "participants": [{"name": "Alex"}, {"name": "Sam"}],
    "title": "Alex",
    "thread_path": "inbox/alex_123",
    "messages": [{"sender_name": "Alex", "timestamp_ms": 1704229200000, "content": "cafÃ© later?"},
                 {"sender_name": "Sam", "timestamp_ms": 1704229260000, "content": "yes"},
                 {"sender_name": "Alex", "timestamp_ms": 1704229320000, "photos": [{"uri": "x.jpg"}]}],
}


def test_meta_export_fixes_encoding_and_skips_media_only():
    msgs = list(meta.parse(_json.dumps(META_THREAD).encode(), "Sam"))
    assert [m.direction for m in msgs] == ["received", "sent"]
    assert msgs[0].text == "café later?"  # Latin-1 over UTF-8, put back together
    assert msgs[0].contact == "Alex"


def test_meta_zip_detected_and_parsed():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("messages/inbox/alex_123/message_1.json", _json.dumps(META_THREAD))
    data = buf.getvalue()
    assert parsers.detect_format("instagram.zip", data) == "meta"
    assert len(list(meta.parse(data, "Sam"))) == 2


def test_json_exports_are_told_apart():
    assert parsers.detect_format("x.json", _json.dumps(TELEGRAM).encode()) == "telegram"
    assert parsers.detect_format("x.json", _json.dumps(META_THREAD).encode()) == "meta"
    assert parsers.detect_format("x.json", _json.dumps(DISCORD_ROWS).encode()) == "discord"
    assert parsers.detect_format("x.json", b'[{"contact":"A","text":"hi","timestamp":"2024-01-01"}]') == "json"


def test_mojibake_leaves_clean_text_alone():
    assert meta.mojibake("café") == "café"
    assert meta.mojibake("plain ascii") == "plain ascii"


# --- findings from a review of the parsers; each of these was wrong before ---

def test_ios_bidi_marks_do_not_hide_who_you_are():
    """iOS wraps names in U+202A/U+202C, which str.strip() leaves in place, so
    a one-to-one chat was filed as a group and the contact came out as you."""
    export = "‪1/2/24, 9:15 PM‬ - ‪Alex‬: hey\n1/2/24, 9:16 PM - Sam: hi\n"
    msgs = list(whatsapp.parse(export.encode(), "Sam"))
    assert [m.contact for m in msgs] == ["Alex", "Alex"]
    assert [m.direction for m in msgs] == ["received", "sent"]
    assert msgs[0].sender == "Alex"


def test_system_notices_are_skipped_not_glued_to_the_message_above():
    export = ("1/2/24, 9:15 PM - Alex: hey\n"
              "1/2/24, 9:16 PM - Messages are end-to-end encrypted\n"
              "1/2/24, 9:17 PM - Alex: still here\n")
    assert [m.text for m in whatsapp.parse(export.encode(), "Sam")] == ["hey", "still here"]
    # a genuine wrapped line is still joined to its message
    wrapped = "1/2/24, 9:15 PM - Alex: first line\nsecond line\n"
    assert list(whatsapp.parse(wrapped.encode(), "Sam"))[0].text == "first line\nsecond line"


def test_a_numbered_mailbox_column_means_what_sms_means_by_it():
    sms = b"contact,type,timestamp,text\nAlex,1,2024-01-01T10:00:00,in\nAlex,2,2024-01-01T10:01:00,out\n"
    assert [m.direction for m in generic.parse_csv(sms, "Sam")] == ["received", "sent"]
    # is_from_me keeps its own meaning: 1 is you
    mine = b"contact,is_from_me,timestamp,text\nAlex,1,2024-01-01T10:00:00,a\nAlex,0,2024-01-01T10:01:00,b\n"
    assert [m.direction for m in generic.parse_csv(mine, "Sam")] == ["sent", "received"]
    worded = b"contact,direction,timestamp,text\nAlex,received,2024-01-01T10:00:00,a\n"
    assert [m.direction for m in generic.parse_csv(worded, "Sam")] == ["received"]


def test_a_row_naming_nobody_is_unknown_not_the_word_none():
    (m,) = generic.parse_csv(b"timestamp,text\n2024-01-01T10:00:00,hello\n", "Sam")
    assert m.contact == "Unknown" and m.sender == "Unknown"


def test_declared_entities_are_refused_before_they_expand():
    """A few hundred bytes of nested entities expand to fill memory."""
    bomb = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "AAAAAAAAAA">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><smses><sms body="&b;"/></smses>')
    with pytest.raises(ValueError, match="entities"):
        list(smsbackup.parse(bomb, "Sam"))
    plain = (b'<?xml version="1.0"?><smses><sms address="1" date="1704229200000" type="1" '
             b'body="hi" contact_name="Alex"/></smses>')
    assert len(list(smsbackup.parse(plain, "Sam"))) == 1


def test_a_byte_order_mark_does_not_hide_the_format():
    payload = _json.dumps(TELEGRAM).encode()
    assert parsers.detect_format("result.json", payload) == "telegram"
    assert parsers.detect_format("result.json", b"\xef\xbb\xbf" + payload) == "telegram"
    csv = b"contact,sender,direction,timestamp,text\nA,A,received,2024-01-01T10:00:00,hi\n"
    assert parsers.detect_format("x.csv", b"\xef\xbb\xbf" + csv) == "csv"


def test_whatsapp_gives_up_on_a_file_of_timestamps_it_cannot_read():
    """Every line matching the shape and none of them carrying a readable date
    used to cost forty-eight failed strptime calls a line, for the length of
    the file: 23 seconds of CPU per megabyte of it."""
    junk = ("99/99/9999, 99:99 - a: x\n" * (whatsapp.MAX_UNPARSEABLE + 5)).encode()
    with pytest.raises(ValueError, match="not a WhatsApp export"):
        list(whatsapp.parse(junk, "Sam"))
    # one real line every so often must not reset the budget and buy more work
    mixed = (("99/99/9999, 99:99 - a: x\n" * 100 + "1/2/24, 9:15 PM - Alex: hi\n")
             * (whatsapp.MAX_UNPARSEABLE // 100 + 1)).encode()
    with pytest.raises(ValueError, match="not a WhatsApp export"):
        list(whatsapp.parse(mixed, "Sam"))


def test_whatsapp_tolerates_the_odd_unreadable_line():
    export = "99/99/9999, 99:99 - a: nonsense\n1/2/24, 9:15 PM - Alex: hey\n"
    assert [m.text for m in whatsapp.parse(export.encode(), "Sam")] == ["hey"]


def test_whatsapp_reuses_the_format_that_worked():
    """One export is written in one format; finding it again per line was the
    other half of the cost above."""
    assert whatsapp._parse_timestamp("14/03/2024", "23:59", None) == (
        datetime(2024, 3, 14, 23, 59), ("%d/%m/%Y", "%H:%M"))
    known = ("%d/%m/%Y", "%H:%M")
    assert whatsapp._parse_timestamp("15/03/2024", "00:01", None, known)[0] == datetime(2024, 3, 15, 0, 1)
    # a line the remembered pair cannot read still gets the full search
    assert whatsapp._parse_timestamp("2024-03-16", "00:02", None, known)[0] == datetime(2024, 3, 16, 0, 2)


def test_smsbackup_refuses_a_utf16_document():
    """The entity guard reads bytes, and expat picks its encoding off the byte
    order mark: in UTF-16 a declaration carries no "<!ENTITY" bytes at all."""
    doc = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE smses [<!ENTITY a "aaaaaaaaaa">'
           '<!ENTITY b "&a;&a;&a;&a;&a;">]><smses><sms address="1" date="1700000000000" '
           'type="1" body="&b;"/></smses>')
    utf16 = doc.encode("utf-16")
    assert b"<!ENTITY" not in utf16  # which is why the old guard missed it
    with pytest.raises(ValueError, match="UTF-16"):
        list(smsbackup.parse(utf16, "Sam"))
    assert b"<!ENTITY" in doc.encode("utf-8")  # and the byte guard still catches this one
    with pytest.raises(ValueError, match="entities"):
        list(smsbackup.parse(doc.encode("utf-8"), "Sam"))


def test_telegram_reads_an_archive_one_document_at_a_time():
    """Holding every document at once meant a small archive of large exports
    was in memory twice: as parsed JSON and as the messages built from it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(3):
            zf.writestr(f"DataExport{i}/result.json", _json.dumps(TELEGRAM))
    seen = list(telegram._payloads(buf.getvalue()))
    assert len(seen) == 3 and all(isinstance(d, dict) for d in seen)
    assert inspect.isgenerator(telegram._payloads(buf.getvalue()))
