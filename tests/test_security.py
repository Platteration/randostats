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

STATIC = Path(__file__).resolve().parent.parent / "randostats" / "static"
APP_JS = STATIC / "app.js"
# Every front end that puts somebody else's words on screen, not just the first.
FRONT_ENDS = (APP_JS, STATIC / "m.js")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "sec.db", use_llm=False)) as c:
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


@pytest.mark.parametrize("front_end", FRONT_ENDS, ids=lambda p: p.name)
def test_frontend_escapes_every_imported_value_it_renders(front_end):
    """A bare imported value interpolated into HTML is how the XSS got in once.

    Contact names, senders, message text and fact statements all come from
    somewhere we do not control, so every one of them must pass through esc()
    (or a numeric formatter) before it reaches innerHTML or a tooltip. Each
    front end duplicates esc(), so each is checked.
    """
    offenders = []
    lines = front_end.read_text().splitlines()
    for number, line in enumerate(lines, start=1):
        if not any(sink in line for sink in HTML_SINKS):
            continue
        # A card template runs over many lines. Checking only the line the sink
        # sits on would miss every interpolation below it, which is most of them.
        block, index = line, number
        while block.count("`") % 2 and index < len(lines):
            block += "\n" + lines[index]
            index += 1
        for expression in re.findall(r"\$\{([^{}]*)\}", block):
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
