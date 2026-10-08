"""The password gate: a server beyond loopback asks for RANDOSTATS_PASSWORD on every
request, and refuses to start without one (randostats/auth.py, api.py, cli.py)."""

from __future__ import annotations

import hmac
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from randostats import api, auth
from randostats.api import create_app

PASSWORD = "correct horse battery"
LOCAL = "http://localhost"
HTML = {"accept": "text/html,application/xhtml+xml"}


@pytest.fixture(autouse=True)
def no_password_from_the_environment(monkeypatch):
    monkeypatch.delenv("RANDOSTATS_PASSWORD", raising=False)


@pytest.fixture
def gated(tmp_path):
    with TestClient(create_app(tmp_path / "gate.db", use_llm=False, password=PASSWORD), base_url=LOCAL) as c:
        yield c


def sign_in(client, password=PASSWORD, **body):
    return client.post("/api/login", json={"password": password, **body})


# -- the gate on its own ---------------------------------------------------------

def test_a_password_has_to_be_long_enough():
    assert auth.password_problem("") and auth.password_problem(None)
    assert "12" in auth.password_problem("short")
    assert auth.password_problem("x" * 11) and auth.password_problem("x" * 12) is None
    with pytest.raises(ValueError):
        auth.Gate("x" * 11)


def test_the_password_is_compared_as_two_digests_in_constant_time(monkeypatch):
    """The comparison is hmac.compare_digest over two digests of the same length, so
    neither the length of a guess nor how much of it was right moves the time it
    takes. A plain `==` on the strings stops at the first differing character."""
    gate = auth.Gate(PASSWORD)
    seen = []
    real = hmac.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    assert gate.password_matches(PASSWORD)
    assert not gate.password_matches("c")
    assert not gate.password_matches(PASSWORD + "x" * 500)
    assert not gate.password_matches(None) and not gate.password_matches(123)
    assert len(seen) == 3, "every string guess goes through compare_digest"
    assert all(len(a) == len(b) == 32 and a != PASSWORD.encode() for a, b in seen), "digests, not the strings"


def test_a_session_is_signed_by_this_process_and_ends_when_revoked():
    gate = auth.Gate(PASSWORD)
    token = gate.issue()
    assert gate.valid(token)
    sid, _, signature = token.partition(".")
    assert not gate.valid(f"{sid}.{'0' * len(signature)}"), "a forged signature"
    assert not gate.valid(sid), "an id without its signature"
    assert not gate.valid(auth.Gate(PASSWORD).issue()), "another process's session, same password"
    assert not gate.valid(token + "é") and not gate.valid("x" * 500) and not gate.valid(None)
    gate.revoke(token)
    assert not gate.valid(token), "signing out ends the session itself, not just the cookie"


def test_a_session_runs_out(monkeypatch):
    now = [1_000_000.0]
    gate = auth.Gate(PASSWORD, clock=lambda: now[0])
    token = gate.issue()
    now[0] += auth.SESSION_DAYS * 86400 - 1
    assert gate.valid(token)
    now[0] += 2
    assert not gate.valid(token)


def test_the_session_table_is_bounded(monkeypatch):
    monkeypatch.setattr(auth, "MAX_SESSIONS", 3)
    gate = auth.Gate(PASSWORD)
    tokens = [gate.issue() for _ in range(5)]
    assert [gate.valid(t) for t in tokens] == [False, False, True, True, True], "the oldest go first"


def test_guesses_are_counted_per_client_before_the_password_is_checked(monkeypatch):
    now = [5_000.0]
    gate = auth.Gate(PASSWORD, clock=lambda: now[0])
    for _ in range(auth.MAX_ATTEMPTS):
        assert gate.admit("10.0.0.9") == 0
    wait = gate.admit("10.0.0.9")
    assert 0 < wait <= auth.WINDOW_SECONDS
    assert gate.admit("10.0.0.10") == 0, "another client has a count of its own"
    now[0] += auth.WINDOW_SECONDS
    assert gate.admit("10.0.0.9") == 0, "the window passes"
    gate.forget("10.0.0.9")
    monkeypatch.setattr(auth, "MAX_CLIENTS", 2)
    assert gate.admit("10.0.0.11") == 0 and gate.admit("10.0.0.12") == 0
    assert gate.admit("10.0.0.13") > 0, "a full table refuses a newcomer rather than forget a count"
    assert gate.admit("10.0.0.11") == 0, "and still counts the clients it has"


@pytest.mark.parametrize("value", ["/", "/m", "/#people", "/?x=1", "/m?next=/", "/.//x"])
def test_next_keeps_a_path_on_this_site(value):
    assert auth.local_path(value) == value


@pytest.mark.parametrize("value", [
    None, 7, "", "m", "https://evil.example/", "//evil.example", "///evil.example", "/\\evil.example",
    "/\\/evil.example", "\\\\evil.example", "/\t/evil.example", "/\n/evil.example", " //evil.example",
    "javascript:alert(1)", "/api/status", "/api", "/" + "a" * 3000,
])
def test_next_cannot_leave_the_site(value):
    """An open redirect through the sign-in page is a phishing link that starts on
    the owner's own server."""
    assert auth.local_path(value) == "/"


# -- the gate in the app ---------------------------------------------------------

def test_without_a_password_nothing_asks_for_one(tmp_path):
    with TestClient(create_app(tmp_path / "open.db", use_llm=False), base_url=LOCAL) as c:
        assert c.get("/", headers=HTML).status_code == 200
        assert c.get("/api/status").json()["auth"] is False
        assert c.get("/login", headers=HTML).status_code == 404
        assert c.post("/api/login", json={"password": PASSWORD}).status_code in (404, 405)


def test_with_a_password_every_page_and_the_api_ask_for_it(gated):
    r = gated.get("/", headers=HTML, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2F"
    r = gated.get("/m?x=1", headers=HTML, follow_redirects=False)
    assert r.headers["location"] == "/login?next=%2Fm%3Fx%3D1"
    for path in ("/api/status", "/api/stats/overview", "/api/messages", "/samples/sample_messages.json",
                 "/no/such/page", "/api/wrapped"):
        assert gated.get(path).status_code == 401, path
    assert gated.delete("/api/messages").status_code == 401
    assert gated.post("/api/counterpoint", json={"text": "70% of people"}).status_code == 401
    assert gated.get("/api/status", headers=HTML).status_code == 401, "the API never redirects"
    # What is the same for every visitor needs no session.
    for path in ("/login", "/static/app.css", "/static/login.js", "/static/guard.js", "/sw.js",
                 "/manifest.webmanifest", "/robots.txt", "/.well-known/security.txt"):
        assert gated.get(path).status_code == 200, path


def test_a_refusal_carries_the_policy(gated):
    for r in (gated.get("/api/status"), gated.get("/", headers=HTML, follow_redirects=False)):
        assert r.headers["content-security-policy"] == api.CSP
        assert r.headers["x-frame-options"] == "DENY"


def test_signing_in_sets_a_cookie_that_opens_everything(gated):
    assert sign_in(gated, "not the password").status_code == 401
    r = sign_in(gated, next="/m")
    assert r.status_code == 200 and r.json() == {"next": "/m"}
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{auth.COOKIE}=")
    flags = {part.strip().split("=")[0].lower() for part in cookie.split(";")[1:]}
    assert {"httponly", "path", "samesite", "max-age"} <= flags
    assert "samesite=lax" in cookie.lower() and f"max-age={auth.SESSION_DAYS * 86400}" in cookie.lower()
    assert "secure" not in flags, "plain http: a Secure cookie would never be sent back"
    assert gated.get("/", headers=HTML).status_code == 200
    assert gated.get("/api/status").json()["auth"] is True


def test_over_https_the_cookie_is_secure(tmp_path):
    with TestClient(create_app(tmp_path / "tls.db", use_llm=False, password=PASSWORD),
                    base_url="https://localhost") as c:
        cookie = sign_in(c).headers["set-cookie"].lower()
        assert "; secure" in cookie and "httponly" in cookie


def test_next_comes_back_only_as_a_path_here(gated):
    assert sign_in(gated, next="//evil.example/x").json() == {"next": "/"}
    assert sign_in(gated, next="/\t/evil.example").json() == {"next": "/"}
    assert sign_in(gated).json() == {"next": "/"}


def test_a_forged_or_signed_out_cookie_opens_nothing(gated):
    token = sign_in(gated).cookies[auth.COOKIE]
    gated.cookies.clear()
    gated.cookies.set(auth.COOKIE, token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert gated.get("/api/status").status_code == 401
    gated.cookies.set(auth.COOKIE, token)
    assert gated.get("/api/status").status_code == 200
    r = gated.post("/api/logout")
    assert r.status_code == 200 and auth.COOKIE in r.headers["set-cookie"]
    assert "max-age=0" in r.headers["set-cookie"].lower()
    # The browser drops the cookie; a copy kept anywhere else is dead as well.
    gated.cookies.set(auth.COOKIE, token)
    assert gated.get("/api/status").status_code == 401


def test_guessing_is_cut_off_and_the_right_password_does_not_get_through_it(gated):
    for _ in range(auth.MAX_ATTEMPTS):
        assert sign_in(gated, "wrong password!").status_code == 401
    r = sign_in(gated)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert auth.COOKIE not in r.headers.get("set-cookie", ""), "a locked-out client learns nothing"


def test_signing_in_from_another_site_is_refused(gated):
    """Login CSRF: a page elsewhere posting the form. The cross-site check that keeps
    other sites off the API keeps them off this route too."""
    r = gated.post("/api/login", json={"password": PASSWORD}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and "set-cookie" not in r.headers
    r = gated.post("/api/login", json={"password": PASSWORD}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_the_factory_reads_the_password_from_the_environment(tmp_path, monkeypatch):
    """`uvicorn --factory randostats.api:create_app` passes no arguments."""
    monkeypatch.setenv("RANDOSTATS_PASSWORD", PASSWORD)
    with TestClient(create_app(tmp_path / "env.db", use_llm=False), base_url=LOCAL) as c:
        assert c.get("/api/status").status_code == 401
    monkeypatch.setenv("RANDOSTATS_PASSWORD", "too short")
    with pytest.raises(ValueError):
        create_app(tmp_path / "short.db", use_llm=False)


# -- beyond loopback -------------------------------------------------------------

def test_a_connection_beyond_loopback_needs_a_password(tmp_path):
    """The server bound wide by hand (`uvicorn --factory ... --host 0.0.0.0`) has no
    CLI to refuse it. uvicorn reports the address each connection reached; one that
    is not loopback is refused until a password is set."""
    lan = "http://192.168.1.20"
    with TestClient(create_app(tmp_path / "lan.db", use_llm=False, allowed_hosts=["192.168.1.20"]),
                    base_url=lan) as c:
        r = c.get("/api/status")
        assert r.status_code == 403 and "RANDOSTATS_PASSWORD" in r.json()["detail"]
        assert c.get("/", headers=HTML).status_code == 403
    with TestClient(create_app(tmp_path / "lan2.db", use_llm=False, allowed_hosts=["192.168.1.20"],
                               password=PASSWORD), base_url=lan) as c:
        assert c.get("/api/status").status_code == 401
        sign_in(c)
        assert c.get("/api/status").status_code == 200
    with TestClient(create_app(tmp_path / "lo.db", use_llm=False), base_url="http://127.0.0.1") as c:
        assert c.get("/api/status").status_code == 200


@pytest.mark.parametrize("address, beyond", [
    ("127.0.0.1", False), ("127.8.9.10", False), ("::1", False), ("::ffff:127.0.0.1", False),
    ("192.168.1.20", True), ("10.0.0.1", True), ("::ffff:192.168.1.20", True), ("fe80::1%eth0", True),
    ("2001:db8::1", True), ("localhost", False), ("testserver", False), ("/run/randostats.sock", False),
])
def test_what_counts_as_beyond_loopback(address, beyond):
    request = SimpleNamespace(scope={"server": (address, 8765)})
    assert api._beyond_loopback(request) is beyond
    assert api._beyond_loopback(SimpleNamespace(scope={})) is False


# -- the command line ------------------------------------------------------------

def _serve(monkeypatch, tmp_path, *args):
    import uvicorn

    from randostats import cli

    served = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))
    status = cli.main(["--db", str(tmp_path / "cli.db"), "serve", *args])
    return status, served


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "192.168.1.20", "laptop.lan", "127.0.0.1.nip.io"])
def test_serve_will_not_start_beyond_loopback_without_a_password(tmp_path, monkeypatch, capsys, host):
    status, served = _serve(monkeypatch, tmp_path, "--host", host)
    assert status == 2 and served == {}
    assert "RANDOSTATS_PASSWORD" in capsys.readouterr().err


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_serve_on_loopback_needs_no_password(tmp_path, monkeypatch, host):
    status, served = _serve(monkeypatch, tmp_path, "--host", host)
    assert status == 0 and served["host"] == host
    with TestClient(served["app"], base_url=LOCAL) as c:
        assert c.get("/api/status").json()["auth"] is False


def test_serve_with_a_password_asks_for_it_wherever_it_binds(tmp_path, monkeypatch):
    monkeypatch.setenv("RANDOSTATS_PASSWORD", PASSWORD)
    for host in ("127.0.0.1", "0.0.0.0"):
        status, served = _serve(monkeypatch, tmp_path, "--host", host)
        assert status == 0
        with TestClient(served["app"], base_url=LOCAL) as c:
            assert c.get("/api/status").status_code == 401, host


def test_serve_refuses_a_short_password_anywhere(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RANDOSTATS_PASSWORD", "hunter2")
    status, served = _serve(monkeypatch, tmp_path)
    assert status == 2 and served == {}
    assert "at least 12" in capsys.readouterr().err
