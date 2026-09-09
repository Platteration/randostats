"""Guards for the things an imported file could otherwise do to the app."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from randostats.api import create_app
from randostats.parsers import archive

APP_JS = Path(__file__).resolve().parent.parent / "randostats" / "static" / "app.js"


# The app only answers to the names it is meant to be reached by, so a test
# client has to use one of them (the default "testserver" is not one).
LOCAL = "http://localhost"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "sec.db", use_llm=False), base_url=LOCAL) as c:
        yield c


def test_security_headers_are_sent(client):
    headers = client.get("/").headers
    csp = headers["content-security-policy"]
    assert "script-src 'self'" in csp and "object-src 'none'" in csp
    assert "'unsafe-eval'" not in csp
    # the Wrapped card rasterises through a blob URL, so images need blob:
    assert "img-src 'self' data: blob:" in csp
    assert headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/status").headers["content-security-policy"] == csp


def test_markup_in_a_contact_name_survives_as_text(client):
    evil = '<img src=x onerror="alert(1)">'
    rows = [{"contact": evil, "sender": evil, "direction": "received",
             "timestamp": "2024-01-01T10:00:00", "text": "hi"}]
    client.post("/api/import", files={"file": ("m.json", json.dumps(rows).encode())},
                data={"self_name": "Sam", "fmt": "json"})
    # The API stores and returns it verbatim; escaping is the renderer's job.
    assert client.get("/api/stats/contacts").json()[0]["contact"] == evil


# Where a string becomes markup. Anything else (textContent, an SVG <text>
# node, a drawer title) is safe by construction and not checked here.
HTML_SINKS = (".innerHTML", "insertAdjacentHTML", "hover(", "showTip(")
# A value read off a row of API data: r.contact, p.contact, w.word, r[key], m.sender...
ROW_VALUE = re.compile(r"\b[a-z]{1,4}(\.[a-zA-Z_]\w*|\[[^\]]+\])")
# Escapes it, or turns it into a number. Either way it cannot carry markup.
LAUNDERED = ("esc(", "dot(", "fmt(", "pct(", "mins(", "compact(", ".toFixed(", "Math.round(", "hueOf(")


def test_frontend_escapes_every_imported_value_it_renders():
    """A bare imported value interpolated into HTML is how the XSS got in once.

    Contact names, senders and message text all come from files other people
    wrote, so every one of them must pass through esc() (or a numeric
    formatter) before it reaches innerHTML or a tooltip.
    """
    offenders = []
    for number, line in enumerate(APP_JS.read_text().splitlines(), start=1):
        if not any(sink in line for sink in HTML_SINKS):
            continue
        for expression in re.findall(r"\$\{([^{}]*)\}", line):
            if ROW_VALUE.search(expression) and not any(safe in expression for safe in LAUNDERED):
                offenders.append(f"line {number}: ${{{expression}}}")
    assert not offenders, ("data reaching HTML without esc() or a numeric formatter:\n  "
                           + "\n  ".join(offenders))


def test_oversized_upload_is_refused(client, monkeypatch):
    import randostats.api as api

    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 1024)
    payload = json.dumps([{"contact": "A", "text": "x" * 4000, "timestamp": "2024-01-01"}]).encode()
    r = client.post("/api/import", files={"file": ("big.json", payload)},
                    data={"self_name": "Sam", "fmt": "json"})
    assert r.status_code == 413 and "limit" in r.json()["detail"]


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return buf.getvalue()


def test_archive_refuses_to_unpack_too_much(monkeypatch):
    data = _zip({"messages/inbox/a/message_1.json": json.dumps({"messages": [], "participants": []}) * 50})
    monkeypatch.setattr(archive, "MAX_TOTAL_BYTES", 64)
    with pytest.raises(archive.ArchiveTooLarge, match="unpacks to"):
        list(archive.json_files(data))


def test_archive_refuses_too_many_members(monkeypatch):
    data = _zip({f"messages/inbox/a{i}/message_1.json": "{}" for i in range(20)})
    monkeypatch.setattr(archive, "MAX_MEMBERS", 5)
    with pytest.raises(archive.ArchiveTooLarge, match="more than the 5 allowed"):
        list(archive.json_files(data))


def test_a_compressed_bomb_is_refused_before_it_is_read(monkeypatch):
    """A gigabyte of zeros compresses to almost nothing; the header says so first."""
    bomb = _zip({"messages/inbox/a/message_1.json": "0" * (4 * 1024 * 1024)})
    assert len(bomb) < 64 * 1024  # tiny on disk, large on unpack
    monkeypatch.setattr(archive, "MAX_TOTAL_BYTES", 1024 * 1024)
    with pytest.raises(archive.ArchiveTooLarge):
        list(archive.json_files(bomb))


def test_import_reports_an_oversized_archive_as_413(client, monkeypatch):
    monkeypatch.setattr(archive, "MAX_TOTAL_BYTES", 64)
    data = _zip({"messages/inbox/a/message_1.json": json.dumps(
        {"participants": [{"name": "Alex"}], "messages": [
            {"sender_name": "Alex", "timestamp_ms": 1704229200000, "content": "hi"}]})})
    r = client.post("/api/import", files={"file": ("x.zip", data)}, data={"self_name": "Sam", "fmt": "meta"})
    assert r.status_code == 413


def test_store_survives_concurrent_use():
    """The API serves from a thread pool and shares one connection."""
    import tempfile
    import threading
    from datetime import datetime, timedelta

    from randostats.models import Message
    from randostats.store import Store

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "concurrent.db")
        base = datetime(2024, 1, 1)
        errors: list[Exception] = []

        def writer(worker: int):
            try:
                for i in range(40):
                    store.add_messages([Message(contact=f"C{worker}", sender="Sam", direction="sent",
                                                timestamp=base + timedelta(minutes=i), text=f"m{i}")])
                    store.all_messages()
                    store.count()
            except Exception as exc:  # noqa: BLE001 - the point is to catch anything
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(w,)) for w in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert store.count() == 8 * 40
        store.close()


def test_reimporting_after_correcting_your_own_name_does_not_double_the_database():
    """The app tells you to fix your name and import again; that used to give
    you two copies of every message you had sent."""
    import json
    import tempfile

    from randostats import parsers
    from randostats.store import Store

    rows = [{"contact": "Alex", "direction": "sent", "timestamp": "2024-01-01T10:00:00", "text": "hi"},
            {"contact": "Alex", "sender": "Alex", "direction": "received", "timestamp": "2024-01-01T10:01:00", "text": "hey"}]
    data = json.dumps(rows).encode()
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "name.db")
        assert store.add_messages(parsers.parse("json", data, "Samm")) == 2
        assert store.add_messages(parsers.parse("json", data, "Sam")) == 0
        assert store.count() == 2
        store.close()


def test_two_people_saying_the_same_thing_at_once_are_still_two_messages():
    import tempfile
    from datetime import datetime

    from randostats.models import Message
    from randostats.store import Store

    moment = datetime(2024, 2, 1, 12, 0, 0)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "group.db")
        added = store.add_messages([Message("Trip", "Alex", "received", moment, "ok"),
                                    Message("Trip", "Priya", "received", moment, "ok")])
        assert added == 2
        store.close()


def test_an_old_database_is_deduplicated_when_it_is_opened():
    import sqlite3
    import tempfile

    from randostats.store import Store

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE messages (id INTEGER PRIMARY KEY, contact TEXT NOT NULL, sender TEXT NOT NULL,
              direction TEXT NOT NULL CHECK (direction IN ('sent','received')), ts TEXT NOT NULL,
              text TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'unknown',
              UNIQUE (contact, sender, ts, text));
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO messages (contact, sender, direction, ts, text) VALUES
              ('Alex','Sam','sent','2024-01-02 21:16:00','hi'),
              ('Alex','Samm','sent','2024-01-02 21:16:00','hi'),
              ('Alex','Alex','received','2024-01-02 21:15:00','hey');""")
        conn.commit()
        conn.close()
        store = Store(path)
        assert store.count() == 2, "the duplicate pair should collapse on open"
        store.close()


def test_a_counterpoint_session_id_has_to_be_one_we_issued(client):
    """Otherwise any client could grow the session map without limit."""
    from randostats.api import MAX_SESSIONS

    app_sessions = None
    made_up = client.post("/api/counterpoint", json={"text": "70% of people", "session": "not-ours"}).json()
    assert made_up["results"], "an unknown id must not break the request"
    again = client.post("/api/counterpoint", json={"text": "70% of people", "session": "not-ours"}).json()
    assert again["results"], "and it must not silently start deduplicating either"

    issued = client.post("/api/counterpoint/session").json()["session"]
    first = client.post("/api/counterpoint", json={"text": "70% of people", "session": issued}).json()
    second = client.post("/api/counterpoint", json={"text": "70% of people", "session": issued}).json()
    assert first["results"] and second["results"] == []
    assert MAX_SESSIONS > 0


# -- who is allowed to talk to us -------------------------------------------
# There is no login: whatever reaches the port reads every message. Both of
# these came from a page on the internet being able to reach 127.0.0.1.

def _rows() -> bytes:
    return json.dumps([{"contact": "Alex", "sender": "Alex", "direction": "received",
                        "timestamp": "2024-01-01T10:00:00", "text": "hi"}]).encode()


def _import(client, **headers):
    return client.post("/api/import", files={"file": ("m.json", _rows())},
                       data={"self_name": "Sam", "fmt": "json"}, headers=headers)


def test_a_host_header_we_do_not_serve_is_refused(client):
    """DNS rebinding: a name the attacker owns, pointed at this machine, is a
    same-origin request from the browser's point of view, so CORS never runs.
    The only thing that separates it from the real front end is the Host."""
    r = client.get("/api/messages", headers={"host": "attacker.example"})
    assert r.status_code == 400 and "host" in r.json()["detail"]
    assert client.delete("/api/messages", headers={"host": "attacker.example"}).status_code == 400
    # and the names it is actually reached by, with or without a port, still work
    for host in ("localhost", "127.0.0.1", "127.0.0.1:8765", "[::1]:8765"):
        assert client.get("/api/status", headers={"host": host}).status_code == 200, host


def test_another_name_is_served_only_when_it_is_asked_for(tmp_path):
    with TestClient(create_app(tmp_path / "named.db", use_llm=False, allowed_hosts=["stats.lan"]),
                    base_url="http://stats.lan") as c:
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/status", headers={"host": "attacker.example"}).status_code == 400
    with TestClient(create_app(tmp_path / "any.db", use_llm=False, allowed_hosts=["*"]),
                    base_url="http://whatever.example") as c:
        assert c.get("/api/status").status_code == 200


def test_the_cli_only_serves_loopback_unless_told_otherwise(tmp_path, monkeypatch):
    import uvicorn

    from randostats import cli

    served = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))

    cli.main(["--db", str(tmp_path / "cli.db"), "serve"])
    with TestClient(served["app"], base_url="http://stats.lan") as c:
        assert c.get("/api/status").status_code == 400

    cli.main(["--db", str(tmp_path / "cli.db"), "serve", "--allow-host", "stats.lan"])
    with TestClient(served["app"], base_url="http://stats.lan") as c:
        assert c.get("/api/status").status_code == 200


def test_a_cross_site_page_cannot_import_or_delete_anything(client):
    """multipart/form-data is a CORS-safelisted content type, so a plain form
    on any page reaches /api/import with no preflight to stop it."""
    assert _import(client, **{"sec-fetch-site": "cross-site"}).status_code == 403
    # a browser that sends no Sec-Fetch-Site still names the origin
    assert _import(client, origin="http://evil.example").status_code == 403
    assert client.delete("/api/messages", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/counterpoint/packs", json={"voice": "victorian"},
                       headers={"origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/status").json()["messages"] == 0, "nothing was allowed in"

    # the front end's own request, from the page this app serves, still works
    ok = _import(client, origin="http://localhost", **{"sec-fetch-site": "same-origin"})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/status").json()["messages"] == 1


def test_a_refused_request_still_carries_the_security_headers(client):
    r = _import(client, **{"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert "script-src 'self'" in r.headers["content-security-policy"]
