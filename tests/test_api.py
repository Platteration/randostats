import json

import pytest
from fastapi.testclient import TestClient

from randostats.api import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "t.db", use_llm=False)
    with TestClient(app) as c:
        yield c


WA = "1/2/24, 9:15 PM - Alex: hey are you around\n1/2/24, 9:16 PM - Sam: yeah definately\n"


def test_import_then_stats(client):
    r = client.post("/api/import", files={"file": ("chat.txt", WA.encode())}, data={"self_name": "Sam"})
    assert r.status_code == 200, r.text
    assert r.json() == {"format": "whatsapp", "parsed": 2, "added": 2, "total": 2, "contacts": ["Alex"], "note": None}
    # re-import is idempotent
    assert client.post("/api/import", files={"file": ("chat.txt", WA.encode())}, data={"self_name": "Sam"}).json()["added"] == 0
    assert client.get("/api/stats/overview").json()["total"] == 2
    assert client.get("/api/stats/contacts").json()[0]["contact"] == "Alex"
    assert client.get("/api/stats/timing").json()["peak_hour"] == 21
    ms = client.get("/api/stats/misspellings").json()
    assert ms["words"][0]["word"] == "definately"
    assert client.get("/api/status").json()["self_name"] == "Sam"
    client.delete("/api/messages")
    assert client.get("/api/stats/overview").json()["total"] == 0


def test_import_json_multi_contact(client):
    rows = [{"contact": "Alex", "sender": "Sam", "direction": "sent", "timestamp": "2024-01-01T10:00:00", "text": "hi"},
            {"contact": "Priya", "sender": "Priya", "direction": "received", "timestamp": "2024-01-01T11:00:00", "text": "yo"}]
    r = client.post("/api/import", files={"file": ("m.json", json.dumps(rows).encode())}, data={"self_name": "Sam", "fmt": "json"})
    assert r.json()["contacts"] == ["Alex", "Priya"]


def test_import_errors(client):
    assert client.post("/api/import", files={"file": ("x.bin", b"\x00\x01")}, data={"self_name": "Sam"}).status_code == 400
    assert client.post("/api/import", files={"file": ("x.txt", b"no messages here")}, data={"self_name": "Sam", "fmt": "whatsapp"}).status_code == 400


def test_counterpoint_endpoints(client):
    r = client.post("/api/counterpoint", json={"text": "seventy percent of people drink beer"})
    body = r.json()
    assert body["results"] and body["results"][0]["claim"]["value"] == 70
    assert "llm" not in body
    sid = client.post("/api/counterpoint/session").json()["session"]
    first = client.post("/api/counterpoint", json={"text": "70% of people drink beer", "session": sid}).json()
    second = client.post("/api/counterpoint", json={"text": "70% of people drink beer", "session": sid}).json()
    assert first["results"] and second["results"] == []
    assert client.get("/api/counterpoint/random").json()["gap"] <= 2.5
    assert len(client.get("/api/counterpoint/facts").json()) > 50


def test_index_served(client):
    assert "randostats" in client.get("/").text


def test_conversation_and_member_endpoints(client):
    rows = [{"contact": "Alex", "sender": "Alex", "direction": "received", "timestamp": "2024-01-01T10:00:00", "text": "hi"},
            {"contact": "Alex", "sender": "Sam", "direction": "sent", "timestamp": "2024-01-01T10:05:00", "text": "hey"},
            {"contact": "Alex", "sender": "Sam", "direction": "sent", "timestamp": "2024-01-03T10:00:00", "text": "still there?"}]
    client.post("/api/import", files={"file": ("m.json", json.dumps(rows).encode())}, data={"self_name": "Sam", "fmt": "json"})
    body = client.get("/api/stats/conversations?gap_hours=6").json()
    assert body["summary"]["conversations"] == 2
    assert body["rows"][0]["contact"] == "Alex"
    # a display limit must not change the headline numbers
    limited = client.get("/api/stats/conversations?gap_hours=6&limit=1").json()
    assert limited["summary"] == body["summary"] and len(limited["rows"]) == 1
    members = client.get("/api/stats/members?contact=Alex").json()
    assert {m["sender"] for m in members} == {"Alex", "Sam"}


def test_message_search_endpoint(client):
    rows = [{"contact": "Alex", "sender": "Sam", "direction": "sent", "timestamp": "2024-01-01T10:00:00", "text": "a sandwich"},
            {"contact": "Alex", "sender": "Alex", "direction": "received", "timestamp": "2024-01-01T11:00:00", "text": "wich one"}]
    client.post("/api/import", files={"file": ("m.json", json.dumps(rows).encode())}, data={"self_name": "Sam", "fmt": "json"})
    assert client.get("/api/messages?word=wich").json()["total"] == 1
    assert client.get("/api/messages?q=wich").json()["total"] == 2
    assert client.get("/api/messages?hour=10").json()["messages"][0]["text"] == "a sandwich"
    assert client.get("/api/messages?direction=nonsense").status_code == 400


def test_wrapped_endpoint(client):
    client.post("/api/import", files={"file": ("chat.txt", WA.encode())}, data={"self_name": "Sam"})
    body = client.get("/api/wrapped").json()
    assert body["years"] == [2024]
    card = body["card"]
    assert card["total"] == 2 and card["year"] == 2024
    # the kicker quotes a real sourced fact of the same size as your own share
    assert "counterpoint" in card and card["counterpoint"]["source"]
    assert client.get("/api/wrapped?year=1999").json()["card"]["empty"] is True


def test_pack_and_voice_config(client):
    cfg = client.get("/api/counterpoint/packs").json()
    assert cfg["voice"] == "house"
    base_facts = cfg["facts"]
    assert any(p["id"] == "core" and p["always_on"] for p in cfg["packs"])

    updated = client.post("/api/counterpoint/packs", json={"packs": ["sports"], "voice": "announcer"}).json()
    assert updated["facts"] > base_facts and updated["voice"] == "announcer"
    assert {p["id"] for p in updated["packs"] if p["enabled"]} == {"core", "sports"}

    # the running engine actually changed
    body = client.post("/api/counterpoint", json={"text": "78% of people drink beer"}).json()
    assert any("!" in r["lines"][0] for r in body["results"])

    assert client.post("/api/counterpoint/packs", json={"packs": ["nope"]}).status_code == 400
    assert client.post("/api/counterpoint/packs", json={"voice": "nope"}).status_code == 400


def test_pack_choice_survives_a_restart(tmp_path):
    from randostats.api import create_app

    db = tmp_path / "p.db"
    with TestClient(create_app(db, use_llm=False)) as first:
        first.post("/api/counterpoint/packs", json={"packs": ["money"], "voice": "victorian"})
    with TestClient(create_app(db, use_llm=False)) as second:
        cfg = second.get("/api/counterpoint/packs").json()
        assert cfg["voice"] == "victorian"
        assert {p["id"] for p in cfg["packs"] if p["enabled"]} == {"core", "money"}


def test_import_flags_a_one_sided_export(client):
    rows = [{"contact": "alex", "sender": "Sam", "direction": "sent", "timestamp": "2024-01-01T10:00:00", "text": "only mine"}]
    body = client.post("/api/import", files={"file": ("m.json", json.dumps(rows).encode())},
                       data={"self_name": "Sam", "fmt": "json"}).json()
    assert "only contains messages you sent" in body["note"]


def test_import_detects_a_telegram_export(client):
    payload = {"personal_information": {"user_id": 7, "first_name": "Sam"},
               "chats": {"list": [{"name": "Alex", "type": "personal_chat", "messages": [
                   {"type": "message", "date": "2024-01-02T21:15:00", "from": "Alex", "from_id": "user9", "text": "hey"},
                   {"type": "message", "date": "2024-01-02T21:16:00", "from": "Sam", "from_id": "user7", "text": "hi"}]}]}}
    body = client.post("/api/import", files={"file": ("result.json", json.dumps(payload).encode())},
                       data={"self_name": "Sam"}).json()
    assert body["format"] == "telegram" and body["parsed"] == 2 and body["note"] is None
