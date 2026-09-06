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
