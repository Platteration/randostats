from datetime import datetime

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
