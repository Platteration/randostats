import json

import pytest
from fastapi.testclient import TestClient

from randostats.api import create_app


# The app only answers to the names it is meant to be reached by, so a test
# client has to use one of them (the default "testserver" is not one).
LOCAL = "http://localhost"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "t.db", use_llm=False)
    with TestClient(app, base_url=LOCAL) as c:
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
    with TestClient(create_app(db, use_llm=False), base_url=LOCAL) as first:
        first.post("/api/counterpoint/packs", json={"packs": ["money"], "voice": "victorian"})
    with TestClient(create_app(db, use_llm=False), base_url=LOCAL) as second:
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


def test_stats_are_computed_once_per_import(client, monkeypatch):
    """Aggregates take seconds on a real archive, so each view is computed once."""
    from randostats import stats as stats_module

    rows = [{"contact": "Alex", "sender": "Sam", "direction": "sent",
             "timestamp": "2024-01-01T10:00:00", "text": "hello"}]
    client.post("/api/import", files={"file": ("m.json", json.dumps(rows).encode())},
                data={"self_name": "Sam", "fmt": "json"})

    calls = []
    real = stats_module.tone
    monkeypatch.setattr(stats_module, "tone", lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    first = client.get("/api/stats/tone").json()
    second = client.get("/api/stats/tone").json()
    assert first == second and len(calls) == 1

    # a different filter is a different view, so it is computed
    client.get("/api/stats/tone?contact=Alex")
    assert len(calls) == 2


def test_importing_invalidates_cached_stats(client):
    """Stale aggregates after an import would be worse than slow ones."""
    first = [{"contact": "Alex", "sender": "Sam", "direction": "sent",
              "timestamp": "2024-01-01T10:00:00", "text": "hello"}]
    client.post("/api/import", files={"file": ("a.json", json.dumps(first).encode())},
                data={"self_name": "Sam", "fmt": "json"})
    assert client.get("/api/stats/overview").json()["total"] == 1
    assert len(client.get("/api/stats/contacts").json()) == 1

    more = [{"contact": "Priya", "sender": "Priya", "direction": "received",
             "timestamp": "2024-01-02T10:00:00", "text": "hi"}]
    client.post("/api/import", files={"file": ("b.json", json.dumps(more).encode())},
                data={"self_name": "Sam", "fmt": "json"})
    assert client.get("/api/stats/overview").json()["total"] == 2
    assert len(client.get("/api/stats/contacts").json()) == 2

    client.delete("/api/messages")
    assert client.get("/api/stats/overview").json()["total"] == 0
    assert client.get("/api/stats/contacts").json() == []


def test_cli_counter_uses_the_configured_packs_and_voice(tmp_path, capsys):
    """The terminal and the browser must not contradict each other."""
    from randostats.cli import main
    from randostats.store import Store

    db = tmp_path / "cli.db"
    store = Store(db)
    store.set_setting("voice", "announcer")
    store.set_setting("packs", "sports")
    store.close()

    assert main(["--db", str(db), "counter", "78 percent of people drink beer"]) == 0
    out = capsys.readouterr().out
    assert "!" in out, "the announcer voice should be shouting"
    assert "free throws" in out, "the sports pack should be loaded"


def test_cli_import_fails_loudly_on_a_file_it_cannot_read(tmp_path, capsys):
    from randostats.cli import main

    empty = tmp_path / "nothing.txt"
    empty.write_text("this is not a chat export at all\n")
    assert main(["--db", str(tmp_path / "x.db"), "import", str(empty), "--me", "Sam", "--format", "whatsapp"]) == 1
    assert "no messages" in capsys.readouterr().err


def test_counterpoint_refuses_more_text_than_a_claim_needs(client):
    """An unbounded body is the cheapest way to spend the owner's CPU (and,
    with --llm, their API budget) from outside."""
    from randostats.api import MAX_TEXT_CHARS

    assert client.post("/api/counterpoint", json={"text": "70% of people. " * 5000}).status_code == 422
    ok = client.post("/api/counterpoint", json={"text": "70% of people. ".ljust(MAX_TEXT_CHARS)})
    assert ok.status_code == 200 and ok.json()["results"]
