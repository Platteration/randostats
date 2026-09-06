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
    assert r.json() == {"format": "whatsapp", "parsed": 2, "added": 2, "total": 2, "contacts": ["Alex"]}
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
