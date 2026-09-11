"""Guards for the things an imported file could otherwise do to the app."""

from __future__ import annotations

import io
import json
import os
import re
import stat
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from randostats.api import create_app
from randostats.models import Message
from randostats.parsers import archive
from randostats.store import Store

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
# The last four are helpers that either escape what they are handed (kpi,
# highlight) or build their markup from a template this same scan covers
# (sparkline, toneTable).
LAUNDERED = ("esc(", "dot(", "fmt(", "pct(", "mins(", "compact(", ".toFixed(", "Math.round(", "hueOf(",
             "highlight(", "kpi(", "sparkline(", "toneTable(")
# A template literal that opens a tag is markup, wherever it is later put.
MARKUP = re.compile(r"<[a-zA-Z/]")
# `cond ? "num" : ""` writes one of two literals; the row only picks which.
LITERAL_CHOICE = re.compile(r"""\?\s*(['"][^'"]*['"])\s*:\s*(['"][^'"]*['"])\s*$""")
INTERPOLATION = re.compile(r"\$\{([^{}]*)\}")


# A "/" opens a regex only where a value cannot already have ended; after
# one of these words it can. (Without this, `.replace(/[&<>"\']/g, ...)` in
# esc() reads as a division and leaves a quote hanging.)
BEFORE_A_REGEX = {"return", "typeof", "instanceof", "in", "of", "new", "delete",
                  "void", "case", "do", "else", "yield", "await"}


def _starts_a_regex(text: str, i: int) -> bool:
    end = i
    while end > 0 and text[end - 1] in " \t\r\n":
        end -= 1
    if end == 0:
        return True
    if not (text[end - 1].isalnum() or text[end - 1] in "_$"):
        return text[end - 1] not in ")]}'\"`"
    start = end
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "_$"):
        start -= 1
    return text[start:end] in BEFORE_A_REGEX


def _closing_quote(text: str, i: int) -> int:
    """One past the end of the string opened at ``i``, or the line it ran off."""
    quote, j = text[i], i + 1
    while j < len(text):
        if text[j] == "\\":
            j += 2
        elif text[j] == quote:
            return j + 1
        elif text[j] == "\n":
            return j  # an unterminated string is a syntax error, not the rest of the file
        else:
            j += 1
    return len(text)


def _closing_slash(text: str, i: int) -> int:
    """One past the end of the regex literal opened at ``i``."""
    j, in_class = i + 1, False
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "\n":
            return j
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            return j + 1
        j += 1
    return len(text)


def _scan(text: str) -> tuple[list[tuple[int, str]], list[int], list[int]]:
    """Walk the file once. Returns (literals, quoted, unterminated).

    ``literals`` is (offset, source) for every template literal at any depth,
    ``quoted`` the offset of every backtick that is only a character - one
    inside a string, a comment or a regex literal - and ``unterminated`` the
    offset of any template whose end was never found.

    The first version of this paired backticks blindly and skipped nothing,
    on the argument that telling strings and comments apart needs a real
    lexer and getting it wrong silently shrinks the scan. Both halves of that
    are true, and the conclusion was still wrong: a single backtick in a
    comment or a string desynced the pairing and dropped every template after
    it just as silently, offenders and all. So this is the lexer, small as it
    is, and ``quoted`` and ``unterminated`` are what it has to show for
    itself - a test below reads them back and fails if the scan ever covers
    less of app.js than it does now.
    """
    literals: list[tuple[int, str]] = []
    quoted: list[int] = []
    stack = ["code"]  # "code" | "brace" | "subst" | "template"
    opens: list[int] = []
    i, n = 0, len(text)

    def skip(end: int) -> int:
        quoted.extend(k for k in range(i, min(end, n)) if text[k] == "`")
        return end

    while i < n:
        ch = text[i]
        if stack[-1] == "template":
            if ch == "\\":
                i += 2
            elif ch == "`":
                start = opens.pop()
                stack.pop()
                literals.append((start, text[start:i + 1]))
                i += 1
            elif text[i:i + 2] == "${":
                stack.append("subst")
                i += 2
            else:
                i += 1
            continue
        if text[i:i + 2] == "//":
            end = text.find("\n", i)
            i = skip(n if end < 0 else end)
        elif text[i:i + 2] == "/*":
            end = text.find("*/", i + 2)
            i = skip(n if end < 0 else end + 2)
        elif ch in "'\"":
            i = skip(_closing_quote(text, i))
        elif ch == "/" and _starts_a_regex(text, i):
            i = skip(_closing_slash(text, i))
        elif ch == "`":
            opens.append(i)
            stack.append("template")
            i += 1
        elif ch == "{":
            stack.append("brace")
            i += 1
        elif ch == "}" and len(stack) > 1:
            stack.pop()  # the brace it was opened by, or the ${...} it closes
            i += 1
        else:
            i += 1
    return literals, quoted, opens


def _template_literals(text: str) -> list[tuple[int, str]]:
    """(offset, source) for every template literal in the file."""
    return _scan(text)[0]


def _unlaundered(expression: str) -> bool:
    if any(safe in expression for safe in LAUNDERED) or LITERAL_CHOICE.search(expression):
        return False
    return bool(ROW_VALUE.search(expression))


def unescaped_values(text: str) -> list[str]:
    """Every interpolation that could put an imported string into markup.

    Two passes, because a table's rows are written on the continuation lines
    of a template whose innerHTML is on the line above, and the counterpoint
    renderer builds its markup into a variable that is assigned somewhere
    else entirely. Scanning only the line the sink is on saw neither.
    """
    line_of = {}
    line = 1
    for index, ch in enumerate(text):
        line_of[index] = line
        if ch == "\n":
            line += 1
    offenders = []
    for start, body in _template_literals(text):  # any template that is markup
        if not MARKUP.search(body):
            continue
        for match in INTERPOLATION.finditer(body):
            if _unlaundered(match.group(1)):
                offenders.append(f"line {line_of[start + match.start()]}: ${{{match.group(1)}}}")
    for number, source in enumerate(text.splitlines(), start=1):  # and any sink line
        if any(sink in source for sink in HTML_SINKS):
            for expression in INTERPOLATION.findall(source):
                if _unlaundered(expression):
                    offenders.append(f"line {number}: ${{{expression}}}")
    return sorted(set(offenders))


def test_frontend_escapes_every_imported_value_it_renders():
    """A bare imported value interpolated into HTML is how the XSS got in once.

    Contact names, senders and message text all come from files other people
    wrote, so every one of them must pass through esc() (or a numeric
    formatter) before it reaches innerHTML or a tooltip.
    """
    offenders = unescaped_values(APP_JS.read_text())
    assert not offenders, ("data reaching HTML without esc() or a numeric formatter:\n  "
                           + "\n  ".join(offenders))


def test_the_escaping_scan_reaches_inside_multiline_templates():
    """The guard above is only worth having if it sees the whole template.

    Every table in the app is written on the continuation lines of a template
    literal whose innerHTML assignment is on the line before, so a scan that
    only read the line carrying the sink read almost none of the app.
    """
    table = ('$("#peaks").innerHTML = `<table><tbody>` +\n'
             '  rows.map(r => `<tr><td>${esc(r.contact)}</td><td>${r.sender}</td></tr>`).join("");\n')
    assert unescaped_values(table) == ["line 2: ${r.sender}"]
    # a value built into a variable first, and assigned somewhere else
    detached = ('const html = groups.map(g => `<div class="cp">${g.punchline}</div>`).join("");\n'
                'box.innerHTML = html;\n')
    assert unescaped_values(detached) == ["line 1: ${g.punchline}"]
    # and it still passes what is laundered, or is a literal either way
    assert unescaped_values('el.innerHTML = `<b>${esc(r.contact)}</b>${r.is_you ? " (you)" : ""}`;\n') == []


BACKTICK_HIDING_PLACES = [
    ("a comment", "// a backtick ` in a comment\n"),
    ("an apostrophe", "// what it's for: a backtick `\n"),
    ("a string", 'const tip = "press ` to search";\n'),
    ("a regex", "const fence = /`+/g;\n"),
    ("a block comment", "/* a backtick ` here */\n"),
]


@pytest.mark.parametrize("where,prelude", BACKTICK_HIDING_PLACES, ids=[w for w, _ in BACKTICK_HIDING_PLACES])
def test_a_backtick_that_is_not_a_delimiter_does_not_shrink_the_scan(where, prelude):
    """One stray backtick used to switch the whole scan off, quietly.

    Backticks were paired from the outside in with nothing skipped, so a
    backtick in a comment or a string opened a pseudo-template that ran to
    the next real one and every literal after it was read inside out. On the
    sample below the offender simply stopped being reported; on app.js the
    markup-bearing templates found dropped from 53 to 23 with no failure
    anywhere. That is the same silent-coverage failure the line-scoped scan
    was replaced for, so it has to be the scan that copes, not the file.
    """
    table = ('$("#peaks").innerHTML = `<table><tbody>` +\n'
             '  rows.map(r => `<tr><td>${esc(r.contact)}</td><td>${r.sender}</td></tr>`).join("");\n')
    assert unescaped_values(table) == ["line 2: ${r.sender}"]
    assert prelude.count("`") == 1, f"one backtick, hiding in {where}"
    assert unescaped_values(prelude + table) == ["line 3: ${r.sender}"]


def test_the_escaping_scan_still_covers_the_whole_of_app_js():
    """Coverage, not just cleanliness.

    The scan above passing means nothing if the scan has quietly stopped
    reading most of the file, and every way it can go wrong ends in reading
    less. So check its own accounting against a fact it does not produce: the
    number of backticks in the file. Every one of them has to be either a
    delimiter of a literal the scan found, or a character the scan decided
    was inside a string, a comment or a regex - never simply lost.
    """
    text = APP_JS.read_text()
    literals, quoted, unterminated = _scan(text)
    assert not unterminated, f"template literals whose end was never found, at {unterminated}"
    assert 2 * len(literals) + len(quoted) == text.count("`"), "backticks the scan cannot account for"
    assert not quoted, ("app.js has always spelt its backticks as template delimiters and nothing "
                        "else. One that is not is fine, but check the scan still reads past it "
                        f"before allowing it here: {quoted}")
    markup = [body for _, body in literals if MARKUP.search(body)]
    assert len(markup) >= 50, (f"only {len(markup)} of {len(literals)} templates in app.js were read as "
                               "markup; the scan has shrunk, or the app has")


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


def test_a_small_archive_cannot_claim_to_unpack_into_hundreds_of_megabytes():
    """The ceiling alone let a two-megabyte upload declare half a gigabyte of
    content, because repetitive JSON deflates a few hundred times over. What
    separates a bomb from a big export is how far it expands, not how much it
    claims: a real export is prose and deflates five to fifteen times."""
    bomb = _zip({"messages/inbox/a/message_1.json": "0" * (40 * 1024 * 1024)})
    assert len(bomb) < 1024 * 1024, "40 MB of one character is nothing on disk"
    with pytest.raises(archive.ArchiveTooLarge, match="for an archive this size"):
        list(archive.json_files(bomb))
    # ... while an archive that expands the way real text does is read
    honest = _zip({"messages/inbox/a/message_1.json": json.dumps(
        {"participants": [{"name": "Alex"}],
         "messages": [{"sender_name": "Alex", "timestamp_ms": 1704229200000 + i, "content": f"message {i}"}
                      for i in range(20_000)]})})
    unpacked = sum(i.file_size for i in zipfile.ZipFile(io.BytesIO(honest)).infolist())
    assert unpacked > len(honest) * 5, "the sample has to actually compress to be worth testing"
    assert len(list(archive.json_files(honest))) == 1


def test_a_huge_index_is_refused_before_the_archive_is_opened(monkeypatch):
    """Sniffing the format calls names() on the raw upload, and ZipFile reads a
    header for every member as it opens: the member cap was only consulted
    afterwards, once that index was already in memory."""
    data = _zip({f"messages/inbox/a{i}/message_1.json": "{}" for i in range(20)})
    assert archive.declared_members(data) == 20
    monkeypatch.setattr(archive, "MAX_MEMBERS", 5)
    with pytest.raises(archive.ArchiveTooLarge, match="more than the 5 allowed"):
        archive.names(data)
    with pytest.raises(archive.ArchiveTooLarge, match="more than the 5 allowed"):
        list(archive.json_files(data))


def _lie_about_the_member_count(data: bytes, count: int) -> bytes:
    """Rewrite both entry-count fields in the end-of-central-directory record.

    Two bytes each, and nothing reads them back: CPython's zipfile walks the
    central directory by size and never consults the count at all.
    """
    end = data.rfind(b"PK\x05\x06")
    packed = count.to_bytes(2, "little")
    return data[:end + 8] + packed + packed + data[end + 12:]


def test_a_lie_about_the_member_count_does_not_buy_the_walk(monkeypatch):
    """The count is the uploader's word, so the cap cannot rest on it.

    zipfile reads *size_cd* bytes of central directory and steps through them
    46 bytes at a time; the count in the end record is never read. Two edited
    bytes therefore used to restore the whole cost the cap was added to stop.
    Bound the walk on the size, which cannot be shrunk without shrinking the
    walk, and check the real number again once the index exists.
    """
    honest = _zip({f"messages/inbox/a{i}/message_1.json": "{}" for i in range(60)})
    liar = _lie_about_the_member_count(honest, 1)
    assert archive.declared_members(liar) == 1, "the archive now claims to hold one file"
    assert len(zipfile.ZipFile(io.BytesIO(liar)).namelist()) == 60, "and zipfile enumerates all sixty"

    monkeypatch.setattr(archive, "MAX_MEMBERS", 5)
    with pytest.raises(archive.ArchiveTooLarge, match="more than the 5 allowed"):
        archive.names(liar)
    with pytest.raises(archive.ArchiveTooLarge, match="more than the 5 allowed"):
        list(archive.json_files(liar))


def test_the_index_cap_really_does_refuse_before_the_archive_is_opened(monkeypatch):
    """Asserted, rather than claimed in a docstring: nothing may open it."""
    liar = _lie_about_the_member_count(
        _zip({f"messages/inbox/a{i}/message_1.json": "{}" for i in range(60)}), 1)

    def refuse_to_open(*args, **kwargs):
        raise AssertionError("the archive was opened before the cap was checked")

    monkeypatch.setattr(archive, "MAX_MEMBERS", 5)
    monkeypatch.setattr(zipfile, "ZipFile", refuse_to_open)
    with pytest.raises(archive.ArchiveTooLarge, match="index describes up to"):
        archive.names(liar)


def test_an_unknown_member_count_is_not_an_unlimited_one(monkeypatch):
    """65,535 members park 0xFFFF in the end record, which is also Zip64's
    "look in the other record" sentinel. With no other record the count is
    simply unknown - and an unknown count used to mean no cap at all, so an
    archive of exactly 65,535 members walked through untouched."""
    data = _lie_about_the_member_count(_zip({f"n{i}.txt": "{}" for i in range(20)}), 0xFFFF)
    assert archive.declared_members(data) is None, "the count says nothing"

    monkeypatch.setattr(archive, "MAX_MEMBERS", 5)
    assert archive.index_bytes(data) <= archive.MAX_MEMBERS * archive.BYTES_PER_MEMBER, \
        "small enough that only the count after opening can catch it"
    with pytest.raises(archive.ArchiveTooLarge, match="more than the 5 allowed"):
        archive.names(data)


def test_an_archive_with_too_many_members_is_a_413_even_when_sniffed(client, monkeypatch):
    """Nothing in here is a format worth parsing, so only the sniff ever opens
    it: without a cap on that path the answer was "could not work out the
    export format", after the whole index had been built."""
    monkeypatch.setattr(archive, "MAX_MEMBERS", 5)
    data = _zip({f"notes/a{i}.txt": "{}" for i in range(20)})
    r = client.post("/api/import", files={"file": ("x.zip", data)}, data={"self_name": "Sam"})
    assert r.status_code == 413 and "files" in r.json()["detail"]


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


# -- who is allowed to read it off the disk ----------------------------------
# The two guards below are about the port. Nothing there stops the account at
# the next desk from opening the file.

def test_the_message_database_is_not_readable_by_other_accounts(tmp_path):
    """Every message the user ever imported, and their correspondents', in one
    file the README promises stays on their machine.

    It was created at whatever the umask happened to be: 0644 in a 0755
    directory on a default install, so any other local account could copy it
    and read the lot. The app already writes the temporary copy of an
    iMessage upload 0600 (parsers/imessage.py), so the permanent one was the
    only place that did not. The umask is opened wide here deliberately -
    that is the only way this notices when the narrowing goes away.
    """
    kept = os.umask(0)
    try:
        db = tmp_path / "made-by-us" / "randostats.db"
        store = Store(db)
        store.add_messages([Message(contact="Alex", sender="Alex", direction="received",
                                    timestamp=datetime(2024, 1, 1, 10, 0), text="secret", source="json")])
        # Not "is 0600": what matters is that nobody outside this account is in it.
        assert not stat.S_IMODE(db.stat().st_mode) & 0o077, "another account can read every message"
        assert not stat.S_IMODE(db.parent.stat().st_mode) & 0o077, "...or walk in and open the file"

        # SQLite gives the rollback journal the database file's own permissions,
        # so a write in flight is covered by the same change.
        store.conn.execute("BEGIN")
        store.conn.execute("INSERT INTO settings (key, value) VALUES ('k', 'v')")
        journal = db.with_name(db.name + "-journal")
        if journal.exists():
            assert not stat.S_IMODE(journal.stat().st_mode) & 0o077, "the journal spills what the database hides"
        store.conn.execute("ROLLBACK")

        # A database from before this existed is repaired, not just a new one.
        legacy = tmp_path / "legacy.db"
        Store(legacy).close()
        os.chmod(legacy, 0o644)
        Store(legacy)
        assert not stat.S_IMODE(legacy.stat().st_mode) & 0o077, "an existing database keeps its old mode"

        # But a directory that was already there is not ours to re-mode: --db
        # can point into /tmp or a home directory.
        shared = tmp_path / "shared"
        shared.mkdir(mode=0o755)
        Store(shared / "x.db")
        assert stat.S_IMODE(shared.stat().st_mode) == 0o755, "narrowed a directory it did not create"
    finally:
        os.umask(kept)


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


def test_a_body_larger_than_the_cap_is_refused_before_it_is_parsed(client, monkeypatch):
    """Starlette spools a multipart body to a temporary file before the handler
    runs, so the in-handler check bounded the parser, not the disk. The body
    below is not valid multipart at all: reaching the handler would be a 4xx
    from the form parser, so a 413 is the middleware refusing on the declared
    length alone."""
    import randostats.api as api

    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 1024)
    r = client.post("/api/import", content=b"x" * 20_000,
                    headers={"content-type": "multipart/form-data; boundary=zzz"})
    assert r.status_code == 413 and "limit" in r.json()["detail"]

    # the JSON endpoints never need a body this size either
    big = json.dumps({"text": "70% of people", "pad": "x" * (api.MAX_JSON_BYTES + 1000)}).encode()
    r = client.post("/api/counterpoint", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and "limit" in r.json()["detail"]
    # and an ordinary request still goes through
    assert client.post("/api/counterpoint", json={"text": "70% of people"}).status_code == 200


def test_serving_beyond_loopback_says_what_that_costs(tmp_path, monkeypatch, capsys):
    """--host 0.0.0.0 is a documented option and there is no login anywhere in
    this app, so it has to say so."""
    import uvicorn

    from randostats import cli

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)

    cli.main(["--db", str(tmp_path / "cli.db"), "serve"])
    assert capsys.readouterr().err == "", "the loopback default warns about nothing"

    cli.main(["--db", str(tmp_path / "cli.db"), "serve", "--host", "0.0.0.0"])
    warning = capsys.readouterr().err
    assert "warning" in warning and "password" in warning and "delete" in warning


@pytest.mark.parametrize("host", ["0.0.0.0", "::", ""])
def test_every_bind_all_says_it_is_a_bind_all(tmp_path, monkeypatch, capsys, host):
    """An empty host is the third spelling of "every interface".

    It is not a fourth loopback name.

    uvicorn passes --host through to bind(), and bind(("", port)) is
    INADDR_ANY, so `serve --host ""` served the unauthenticated API on the LAN
    and printed nothing. The line that builds the Host allow-list already
    counts "" as a bind rather than a name; the line that decides the warning
    filed it with 127.0.0.1 and the two disagreed.
    """
    import uvicorn

    from randostats import cli

    bound = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: bound.update(kw))
    cli.main(["--db", str(tmp_path / "cli.db"), "serve", "--host", host])
    assert bound["host"] == host, "whatever was asked for is what is bound"
    warning = capsys.readouterr().err
    assert "warning" in warning and "password" in warning and "delete" in warning
    assert "serving on , " not in warning, "an empty host still has to read as something"


def test_the_listen_button_says_where_the_audio_goes():
    """Continuous speech recognition is a cloud service in Chrome and Edge, and
    it is meant to be used while other people are talking."""
    html = (Path(__file__).resolve().parent.parent / "randostats" / "static" / "index.html").read_text()
    note = html.split('id="counter-listen"', 1)[1].split("</div>", 3)
    disclosure = " ".join(note[:3])
    assert "speech recognition" in disclosure and "browser vendor" in disclosure


def test_the_listen_note_does_not_promise_what_llm_mode_breaks():
    """The note sat above the microphone and said, flatly, that nothing else
    here leaves your machine.

    With --llm it does: the claim goes to Anthropic, and `claim.raw` carries
    the clause spoken around the number with it - the speech of people in the
    room who never touched the app. The note now comes from the server's own
    llm flag, so the promise is only made when it is true, and the box that
    sends it is off until somebody ticks it.
    """
    static = Path(__file__).resolve().parent.parent / "randostats" / "static"
    html = static.read_text() if static.is_file() else (static / "index.html").read_text()
    app_js = APP_JS.read_text()

    assert "Nothing else here leaves your machine" not in html, \
        "the absolute claim is back in the markup, where no flag can withdraw it"
    assert 'id="listen-privacy-llm"' in html, "nothing for the front end to write the honest version into"

    # The checkbox is what performs the round trip, so it starts off.
    box = html.split('id="counter-llm"', 1)[0].rsplit("<input", 1)[1] + \
        html.split('id="counter-llm"', 1)[1].split(">", 1)[0]
    assert "checked" not in box, "sending the room's speech to a third party is opt-out again"

    # And the sentence is chosen by the server's flag, not hard-coded.
    filled = app_js.split('listen-privacy-llm', 1)[1].split(";", 1)[0]
    assert "st.llm" in filled, "the note no longer depends on whether --llm is on"
    assert "Anthropic" in filled, "the --llm branch does not say where the words go"


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


def test_a_cross_site_page_cannot_read_the_api_either(client):
    """Refusing only the writes left every read open to any page the user has
    in another tab.

    It cannot see the answer - there are no CORS headers - but it can make
    this machine compute it, and an aggregate view is a full walk of the
    corpus. The app is the only thing that calls /api/*, and it calls it
    same-origin, so a cross-site read is never anything but that.
    """
    cross = {"sec-fetch-site": "cross-site", "origin": "https://evil.example"}
    for path in ("/api/status", "/api/messages?limit=500", "/api/stats/overview",
                 "/api/stats/conversations?gap_hours=6", "/api/wrapped"):
        assert client.get(path, headers=cross).status_code == 403, path

    # The page itself is not the API: a bookmark or a typed URL still opens it.
    for path in ("/", "/static/app.js"):
        assert client.get(path, headers=cross).status_code == 200, path

    # And the front end's own reads are untouched.
    same = {"sec-fetch-site": "same-origin", "origin": "http://localhost"}
    assert client.get("/api/status", headers=same).status_code == 200
    assert client.get("/api/status").status_code == 200, "a client that names no site is not a cross-site one"


def test_a_caller_cannot_choose_the_memo_key(client):
    """Every aggregate view is memoised on the parameters the caller sent, and
    256 distinct keys clear the memo for everyone.

    So each of them has to come from a set this app decides the size of: the
    gap is quantised, row counts are clamped, and a name the store does not
    hold is answered without computing or remembering anything.
    """
    made = 250
    rows = [{"contact": f"person {n}", "sender": f"person {n}", "direction": "received",
             "timestamp": "2024-01-01T10:00:00", "text": f"hello {n}"} for n in range(made)]
    assert client.post("/api/import", files={"file": ("m.json", json.dumps(rows).encode())},
                       data={"self_name": "Sam", "fmt": "json"}).status_code == 200

    # A row count from outside is not a row count this app will honour, so
    # asking for more than there are cannot hand back one key per number.
    listed = client.get(f"/api/stats/contacts?limit={made * 10}").json()
    assert len(listed) < made, "a caller's own limit reached the view, and the memo key with it"

    # The gap the answer reports is the gap it used. Two a caller can tell
    # apart must not be two keys, and rounding must not reach zero.
    near = [client.get(f"/api/stats/conversations?gap_hours={g}").json()["gap_hours"]
            for g in ("6.1234", "6.1239")]
    assert near[0] == near[1], "two gaps a caller can tell apart are two memo keys"
    assert near[0] != 6.1234, "and the answer has to say which gap it actually used"
    assert client.get("/api/stats/conversations?gap_hours=0.0001").json()["gap_hours"] > 0, \
        "rounding must not round a positive gap away to nothing"

    # A direction that is not one of the two is refused, like its siblings.
    assert client.get("/api/stats/words?direction=made-up").status_code == 400

    # A name nobody has is answered with the empty view, not computed and kept.
    assert client.get("/api/stats/members?contact=person 1").json() != []
    for path in ("/api/stats/tone?contact=nobody", "/api/stats/emoji?contact=nobody",
                 "/api/stats/timing?contact=nobody", "/api/stats/misspellings?contact=nobody"):
        assert client.get(path).status_code == 200, path
    assert client.get("/api/stats/members?contact=nobody").json() == []


def test_a_refused_request_still_carries_the_security_headers(client):
    r = _import(client, **{"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert "script-src 'self'" in r.headers["content-security-policy"]


def test_a_500_carries_them_too(tmp_path):
    """Starlette's stack is [ServerErrorMiddleware] + user middleware + router,
    so the header middleware is *inside* the 500 handler and had already
    unwound by the time a crash became a response.

    Nothing in the shipped API renders a user string into a 500 today, so this
    pins the second line of defence for the handler that one day does.
    """
    app = create_app(tmp_path / "boom.db", use_llm=False)

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    with TestClient(app, base_url=LOCAL, raise_server_exceptions=False) as c:
        r = c.get("/boom")
        assert r.status_code == 500
        # The same three the middleware sets, asserted against a response that
        # did go through it rather than against a list written out twice.
        for header, value in c.get("/api/status").headers.items():
            if header in ("content-security-policy", "x-content-type-options", "referrer-policy"):
                assert r.headers.get(header) == value, header
        assert "kaboom" not in r.text, "the crash told the caller about itself"
