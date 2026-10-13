"""Sign in with Google: the other way in, for a server beyond loopback (randostats/google.py,
and where api.py, auth.py, security.py and cli.py let it in).

Google's half is tests/fake_google.py: the consent screen as a function, and the token
endpoint handed to create_app(fetch=...). Nothing points the app at another host.
"""

from __future__ import annotations

import base64
import http.client
import http.server
import json
import logging
import re
import ssl
import subprocess
import threading
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import fake_google
import pytest
from fake_google import OWNER, STRANGER, FakeGoogle
from fastapi.testclient import TestClient

from randostats import api, auth, google
from randostats.api import create_app

PASSWORD = "correct horse battery"
LOCAL = "http://localhost"
HTML = {"accept": "text/html,application/xhtml+xml"}
STATIC = Path(__file__).resolve().parent.parent / "randostats" / "static"
ENV = {google.APP_URL: "https://stats.example.com/", google.CLIENT_ID: fake_google.CLIENT_ID,
       google.CLIENT_SECRET: fake_google.CLIENT_SECRET, google.ALLOWED: OWNER["sub"]}
# The owner's account as a Google Workspace one: the ID token names its domain (hd).
WORKSPACE_OWNER = {**OWNER, "hd": "example.com"}
# A Google account someone made with the owner's outside address, having once read its
# mail (a lapsed domain bought again, a former employer, a shared alias): Google marks
# the address verified, and names no Workspace domain.
IMPOSTOR = {"sub": "999999999999999999999", "email": "owner@example.com", "email_verified": True}


@pytest.fixture(autouse=True)
def nothing_from_the_environment(monkeypatch):
    for name in ("RANDOSTATS_PASSWORD", google.APP_URL, *google.TOUCHED):
        monkeypatch.delenv(name, raising=False)


def app(tmp_path, *, config=None, fake=None, password=None, base_url=LOCAL, **kwargs):
    config = config or fake_google.config()
    fake = fake if fake is not None else FakeGoogle()
    client = TestClient(create_app(tmp_path / "g.db", use_llm=False, password=password, google_config=config,
                                   fetch=fake, **kwargs), base_url=base_url)
    client.fake = fake
    return client


@pytest.fixture
def signin(tmp_path):
    with app(tmp_path) as c:
        yield c


def set_cookies(response) -> dict[str, str]:
    """name -> the whole Set-Cookie line, for every cookie a response sets."""
    return {line.split("=", 1)[0]: line for line in response.headers.get_list("set-cookie")}


def begin(client, return_to=None, **headers):
    r = client.get(google.START, params={} if return_to is None else {"return": return_to},
                   headers=headers, follow_redirects=False)
    assert r.status_code == 302, r.text
    line = set_cookies(r)[google.FLOW_COOKIE]
    return r.headers["location"], line.split("=", 1)[1].split(";", 1)[0]


def come_back(client, location, cookie, account=OWNER, claims=None, cookie_name=google.FLOW_COOKIE, **headers):
    """Google sends the browser back with the code and the state."""
    back = urlsplit(fake_google.authorize(location, account, claims))
    # UTF-8, as a browser sends a header with a non-ASCII byte in it.
    cookies = {"cookie": f"{cookie_name}={cookie}".encode("utf-8")} if cookie is not None else {}
    return client.get(f"{back.path}?{back.query}", headers={**cookies, **headers}, follow_redirects=False)


def sign_in_with_google(client, account=OWNER, claims=None, return_to=None):
    location, cookie = begin(client, return_to)
    return come_back(client, location, cookie, account, claims)


def failed(r) -> bool:
    """Sent to the sign-in page to say so, the sign-in cookie cleared, no session."""
    cookies = set_cookies(r)
    return (r.status_code == 302 and r.headers["location"] == "/login?signin=failed"
            and "max-age=0" in cookies.get(google.FLOW_COOKIE, "").lower() and auth.COOKIE not in cookies)


def session(r) -> str:
    return set_cookies(r)[auth.COOKIE].split("=", 1)[1].split(";", 1)[0]


# -- configuration -------------------------------------------------------------------

def test_nothing_asked_for_is_nothing_set_up():
    assert google.read_config({}) is None
    assert google.read_config({google.APP_URL: "https://stats.example.com/"}) is None, \
        "the site's address alone asks for nothing"


def test_a_whole_set_up_reads_as_one():
    config = google.read_config({**ENV, google.ALLOWED: f" {OWNER['sub']}, Owner@Example.com\n42 "})
    assert config.app_url == "https://stats.example.com/"
    assert config.redirect_uri == "https://stats.example.com/api/auth/google/callback"
    assert config.subs == {OWNER["sub"], "42"} and config.emails == {"owner@example.com"}
    assert fake_google.CLIENT_SECRET not in repr(config), "the secret stays out of logs and tracebacks"


@pytest.mark.parametrize("missing", [google.APP_URL, *google.TOUCHED])
def test_half_a_set_up_is_refused_naming_what_is_missing(missing):
    env = {**ENV, missing: ""}
    with pytest.raises(ValueError) as refused:
        google.read_config(env)
    assert f"missing {missing}" in str(refused.value)
    assert fake_google.CLIENT_SECRET not in str(refused.value)


@pytest.mark.parametrize("with_address", [False, True])
@pytest.mark.parametrize("alone", ["RANDOSTATS_GOOGLE_CLIENT_ID", "RANDOSTATS_GOOGLE_CLIENT_SECRET",
                                   "RANDOSTATS_GOOGLE_ALLOWED"])
def test_any_one_of_the_three_asks_for_sign_in_with_google(alone, with_address):
    """Set any one of them, with the site's address or without, and the server does not
    start without the others: it never runs without the sign-in that was asked for."""
    env = {alone: ENV[alone], **({"RANDOSTATS_APP_URL": ENV[google.APP_URL]} if with_address else {})}
    with pytest.raises(ValueError) as refused:
        google.read_config(env)
    message = str(refused.value)
    for name in ("RANDOSTATS_GOOGLE_CLIENT_ID", "RANDOSTATS_GOOGLE_CLIENT_SECRET", "RANDOSTATS_GOOGLE_ALLOWED"):
        assert (f"missing {name}" in message) is (name != alone), (name, message)
    assert ("missing RANDOSTATS_APP_URL" in message) is (not with_address), message


@pytest.mark.parametrize("value, root", [
    ("https://stats.example.com", "https://stats.example.com/"),
    ("https://Stats.Example.com/", "https://stats.example.com/"),
    ("https://stats.example.com:8443/", "https://stats.example.com:8443/"),
    ("http://localhost:8765", "http://localhost:8765/"),
    ("http://127.0.0.1:8765/", "http://127.0.0.1:8765/"),
    ("http://[::1]:8765/", "http://[::1]:8765/"),
])
def test_the_site_address_is_its_root(value, root):
    assert google.parse_app_url(value) == root


@pytest.mark.parametrize("value", [
    "http://stats.example.com/", "http://192.168.1.20/", "ftp://stats.example.com/", "stats.example.com",
    "https://stats.example.com/randostats/", "https://stats.example.com/?x=1", "https://stats.example.com/#x",
    "https://stats.example.com/?", "https://user:pw@stats.example.com/", "https://*.example.com/",
    "https://stats.example.com:99999/", "https://stats example.com/", "https:///", "https://stats.example.com\\/",
    "https://stätš.example.com/",
])
def test_the_site_address_is_not_anything_else(value):
    assert google.parse_app_url(value) is None
    with pytest.raises(ValueError) as refused:
        google.read_config({**ENV, google.APP_URL: value})
    assert f"malformed {google.APP_URL}" in str(refused.value)


@pytest.mark.parametrize("name, value", [
    (google.CLIENT_ID, "123-abc.example.com"),
    (google.CLIENT_ID, "apps.googleusercontent.com"),
    (google.CLIENT_SECRET, "short"),
    (google.CLIENT_SECRET, "has a space in it"),
    (google.ALLOWED, "owner@"),
    (google.ALLOWED, f"{OWNER['sub']}, not an id!"),
    (google.ALLOWED, ", ,"),
])
def test_a_malformed_value_is_refused_by_name(name, value):
    with pytest.raises(ValueError) as refused:
        google.read_config({**ENV, name: value})
    assert f"malformed {name}" in str(refused.value)
    if name == google.CLIENT_SECRET:
        assert value not in str(refused.value), "the secret is not repeated back"


def test_the_site_address_has_to_be_a_name_this_server_answers_to(tmp_path):
    with pytest.raises(ValueError, match="--allow-host stats.example.com"):
        create_app(tmp_path / "x.db", use_llm=False, google_config=fake_google.config("https://stats.example.com/"))
    create_app(tmp_path / "y.db", use_llm=False, google_config=fake_google.config("https://stats.example.com/"),
               allowed_hosts=["localhost", "stats.example.com"])


def test_the_factory_reads_sign_in_with_google_from_the_environment(tmp_path, monkeypatch):
    """`uvicorn --factory randostats.api:create_app` passes no arguments."""
    for name, value in {**ENV, google.APP_URL: "http://localhost/"}.items():
        monkeypatch.setenv(name, value)
    with TestClient(create_app(tmp_path / "env.db", use_llm=False), base_url=LOCAL) as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/config").json()["auth"] == {"google": True, "password": False}
    monkeypatch.setenv(google.CLIENT_SECRET, "")
    with pytest.raises(ValueError, match=f"missing {google.CLIENT_SECRET}"):
        create_app(tmp_path / "half.db", use_llm=False)


# -- what the page is told -------------------------------------------------------------

def test_with_no_gate_there_is_no_sign_in_to_offer(tmp_path):
    with TestClient(create_app(tmp_path / "open.db", use_llm=False), base_url=LOCAL) as c:
        assert c.get("/api/config").status_code == 404
        assert c.get(google.START, follow_redirects=False).status_code == 404
        assert c.get(google.CALLBACK, follow_redirects=False).status_code == 404
        assert c.get("/api/status").json()["user"] is None


def test_the_sign_in_page_is_told_which_ways_in_there_are(tmp_path):
    with TestClient(create_app(tmp_path / "pw.db", use_llm=False, password=PASSWORD), base_url=LOCAL) as c:
        assert c.get("/api/config").json() == {"auth": {"google": False, "password": True}, "shop": {"products": []}}
        assert c.get(google.START, follow_redirects=False).status_code == 404, "not set up, not there"
    with app(tmp_path) as c:
        assert c.get("/api/config").json() == {"auth": {"google": True, "password": False}, "shop": {"products": []}}
        assert c.get("/login", headers=HTML).status_code == 200
        assert c.post("/api/login", json={"password": PASSWORD}).status_code in (404, 405), \
            "with no password there is no password to try"
    with app(tmp_path, password=PASSWORD) as c:
        assert c.get("/api/config").json()["auth"] == {"google": True, "password": True}


def test_with_only_google_every_page_and_the_api_still_ask(signin):
    r = signin.get("/", headers=HTML, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2F"
    for path in ("/api/status", "/api/stats/overview", "/api/messages"):
        assert signin.get(path).status_code == 401, path
    for path in ("/login", "/api/config", "/static/login.js", "/robots.txt"):
        assert signin.get(path).status_code == 200, path


# -- starting ---------------------------------------------------------------------------

def test_start_sends_the_browser_to_google_with_pkce_state_and_nonce(signin):
    r = signin.get(google.START, follow_redirects=False)
    assert r.status_code == 302
    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == google.AUTHORIZE_URL
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert params["response_type"] == "code" and params["scope"] == "openid email"
    assert params["client_id"] == fake_google.CLIENT_ID
    assert params["redirect_uri"] == "http://localhost/api/auth/google/callback"
    assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) == 43
    assert len(params["state"]) >= 43 and len(params["nonce"]) >= 43 and params["state"] != params["nonce"]
    line = set_cookies(r)[google.FLOW_COOKIE]
    flags = {part.strip().lower() for part in line.split(";")[1:]}
    assert {"httponly", "secure", "path=/", "samesite=lax", "max-age=600"} <= flags, "ten minutes"
    assert not any(flag.startswith("domain") for flag in flags), "__Host-: no Domain"
    assert r.headers["referrer-policy"] == "no-referrer", "nothing of this site goes to Google in a Referer"
    other = urlsplit(begin(signin)[0]).query
    assert parse_qs(other)["state"] != [params["state"]] and parse_qs(other)["nonce"] != [params["nonce"]]


def test_a_sign_in_started_under_another_name_starts_again_at_the_site_s_own(tmp_path):
    """The sign-in cookie is set for the name the browser used, and Google sends the
    browser back to RANDOSTATS_APP_URL: started under any other name the server answers
    to, the sign-in could only fail. So it goes to the sign-in page at the site's own
    address, with where it was going, and starts nothing."""
    with app(tmp_path, config=fake_google.config("http://127.0.0.1:8765/")) as c:  # opened as localhost
        r = c.get(google.START, params={"return": "/m?x=1"}, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "http://127.0.0.1:8765/login?next=%2Fm%3Fx%3D1&signin=here"
        assert google.FLOW_COOKIE not in set_cookies(r) and c.fake.calls == []
        for attack in ("//evil.example", "https://evil.example/", None):
            r = c.get(google.START, params={} if attack is None else {"return": attack}, follow_redirects=False)
            assert r.headers["location"] == "http://127.0.0.1:8765/login?next=%2F&signin=here", attack
        for host in ("127.0.0.1", "127.0.0.1:8766", "localhost:8765"):
            r = c.get(google.START, headers={"host": host}, follow_redirects=False)
            assert r.headers["location"].endswith("signin=here"), host
        r = c.get(google.START, params={"return": "/m"}, headers={"host": "127.0.0.1:8765"}, follow_redirects=False)
        assert r.headers["location"].startswith(google.AUTHORIZE_URL + "?") and google.FLOW_COOKIE in set_cookies(r)


@pytest.mark.parametrize("app_url, host, same", [
    ("https://stats.example.com/", "stats.example.com", True),
    ("https://stats.example.com/", "Stats.Example.COM", True),
    ("https://stats.example.com/", "stats.example.com:443", True),
    ("https://stats.example.com:443/", "stats.example.com", True),
    ("https://stats.example.com/", "stats.example.com:8443", False),
    ("https://stats.example.com:8443/", "stats.example.com", False),
    ("https://stats.example.com/", "laptop.lan", False),
    ("https://stats.example.com/", "stats.example.com.lan", False),
    ("https://stats.example.com/", "", False),
    ("https://stats.example.com/", "stats.example.com:https", False),
    ("http://127.0.0.1:8765/", "127.0.0.1:8765", True),
    ("http://127.0.0.1:8765/", "localhost:8765", False),
    ("http://127.0.0.1:8765/", "127.0.0.1", False),
    ("http://localhost/", "localhost", True),
    ("http://localhost/", "localhost:80", True),
    ("http://localhost/", "localhost:443", False),
    ("http://[::1]:8765/", "[::1]:8765", True),
    ("http://[::1]:8765/", "[::1]:8766", False),
])
def test_the_site_s_own_name_and_port(app_url, host, same):
    assert fake_google.config(app_url).answers_at(host) is same


def test_start_is_this_site_s_own(signin):
    """A link from another site does not start a sign-in: it is under /api/."""
    r = signin.get(google.START, headers={"sec-fetch-site": "cross-site"}, follow_redirects=False)
    assert r.status_code == 403 and google.FLOW_COOKIE not in set_cookies(r)


# -- coming back --------------------------------------------------------------------------

def test_a_listed_account_is_signed_in_and_sent_back(signin, caplog):
    location, cookie = begin(signin, "/m?x=1#people")
    r = come_back(signin, location, cookie)
    assert r.status_code == 302 and r.headers["location"] == "/m?x=1#people"
    cookies = set_cookies(r)
    assert "max-age=0" in cookies[google.FLOW_COOKIE].lower(), "the sign-in cookie is spent"
    line = cookies[auth.COOKIE].lower()
    assert "httponly" in line and "samesite=lax" in line and f"max-age={auth.SESSION_DAYS * 86400}" in line
    assert "secure" not in line, "plain http on localhost: a Secure session cookie would not come back"
    assert len(signin.fake.calls) == 1
    form = signin.fake.calls[0]["form"]
    assert signin.fake.calls[0]["url"] == google.TOKEN_URL and signin.fake.calls[0]["timeout"] == 10
    assert form["redirect_uri"] == "http://localhost/api/auth/google/callback" and form["code_verifier"]
    signin.cookies.set(auth.COOKIE, session(r))
    status = signin.get("/api/status").json()
    assert status["auth"] is True and status["user"] == {"email": OWNER["email"]}
    assert signin.get("/", headers=HTML).status_code == 200
    assert signin.post("/api/logout").status_code == 200
    assert signin.get("/api/status").status_code == 401, "Sign out ends a Google session like any other"
    assert not caplog.records


def test_over_https_the_session_is_secure(tmp_path):
    with app(tmp_path, config=fake_google.config("https://localhost/"), base_url="https://localhost") as c:
        r = sign_in_with_google(c)
        assert "; secure" in set_cookies(r)[auth.COOKIE].lower()
        assert c.fake.calls[0]["form"]["redirect_uri"] == "https://localhost/api/auth/google/callback"


def test_behind_a_proxy_uvicorn_does_not_believe_the_session_is_secure_all_the_same(tmp_path):
    """A TLS terminator whose X-Forwarded-Proto uvicorn does not trust hands the request on
    as plain http, while the browser is on https: RANDOSTATS_APP_URL says so, and the
    session cookie is Secure for it."""
    with app(tmp_path, config=fake_google.config("https://localhost/"), base_url="http://localhost") as c:
        r = sign_in_with_google(c)
        assert "; secure" in set_cookies(r)[auth.COOKIE].lower()


def test_each_sign_in_is_a_new_session(signin):
    first, second = session(sign_in_with_google(signin)), session(sign_in_with_google(signin))
    assert first != second


def test_an_address_on_the_list_lets_in_only_an_account_google_speaks_for(tmp_path, caplog):
    """Google marks an address verified once a mail sent to it has been read, so whoever
    could once read the owner's mail can make a Google account with the owner's address.
    Listed by the address, only an account Google is the authority for comes in: one of
    the Google Workspace domain the address is at (hd), or a Gmail address."""
    caplog.set_level(logging.WARNING, logger="randostats")
    listed = ("owner@example.com", "someone@gmail.com", "old@googlemail.com")
    with app(tmp_path, config=fake_google.config(subs=(), emails=listed)) as c:
        r = sign_in_with_google(c, WORKSPACE_OWNER)
        assert r.status_code == 302 and r.headers["location"] == "/"
        c.cookies.set(auth.COOKIE, session(r))
        assert c.get("/api/status").json()["user"] == {"email": "Owner@Example.com"}
        c.cookies.clear()
        assert failed(sign_in_with_google(c, IMPOSTOR)), "the owner's address on an account Google does not speak for"
        assert failed(sign_in_with_google(c, OWNER)), "no Workspace domain: Google does not speak for example.com"
        assert failed(sign_in_with_google(c, {**IMPOSTOR, "hd": "other.example"})), "another Workspace's account"
        for gmail in ({"sub": "5", "email": "Someone@Gmail.com", "email_verified": True},
                      {"sub": "6", "email": "old@googlemail.com", "email_verified": True}):
            assert auth.COOKIE in set_cookies(sign_in_with_google(c, gmail)), gmail
        assert failed(sign_in_with_google(c, {**WORKSPACE_OWNER, "email_verified": False})), \
            "an address Google has not verified is not the owner's"
        assert failed(sign_in_with_google(c, {**WORKSPACE_OWNER, "email_verified": "true"})), "true, the JSON value"
        assert failed(sign_in_with_google(c, STRANGER))
    assert f"the Google account {IMPOSTOR['sub']} is not in {google.ALLOWED}: its address is" in caplog.text
    assert "owner@example.com" not in caplog.text.lower(), "the address is not logged"


@pytest.mark.parametrize("identity, allowed", [
    (google.Identity(OWNER["sub"]), True),
    (google.Identity(OWNER["sub"] + "1"), False),  # the owner's id is a prefix of it
    (google.Identity(OWNER["sub"][:-1]), False),  # it is a prefix of the owner's id
    (google.Identity("1" + OWNER["sub"]), False),
    (google.Identity("7", "owner@example.com", "example.com"), True),
    (google.Identity("7", "OWNER@EXAMPLE.COM", "example.com"), True),
    (google.Identity("7", "owner@example.com"), False),  # no Workspace domain
    (google.Identity("7", "owner@example.com", "example.org"), False),  # another one's
    (google.Identity("7", "owner@example.com", "mail.example.com"), False),
    (google.Identity("7", "owner@other.example", "other.example"), False),  # the owner's name, another domain
    (google.Identity("7", "other@example.com", "example.com"), False),  # the owner's domain, another name
    (google.Identity("7", None, "example.com"), False),
    (google.Identity("7", "boss@gmail.com"), True),
    (google.Identity("7", "boss@googlemail.com"), False),  # listed at gmail.com only
    (google.Identity("7", "boss@gmail.co"), False),
    (google.Identity("7", "other@gmail.com"), False),
    (google.Identity("7", "boss@other.example", "other.example"), False),
])
def test_who_the_list_lets_in(identity, allowed):
    """Exactly the listed ids, and exactly the listed addresses where Google speaks for them."""
    config = fake_google.config(subs=(OWNER["sub"],), emails=("owner@example.com", "boss@gmail.com"))
    assert config.allows(identity) is allowed
    assert (config.refusal(identity) is None) is allowed


def test_the_workspace_domain_is_read_as_google_writes_it():
    good = {"iss": "accounts.google.com", "aud": "c", "sub": "1", "nonce": "n", "exp": NOW + 9, "iat": NOW}

    def read(**extra):
        return google.check_claims({**good, **extra}, client_id="c", nonce="n", now=NOW)
    assert read(hd="Example.COM", email="a@example.com", email_verified=True).domain == "example.com"
    assert read(hd="example.com").domain == "example.com", "read whether or not there is an address"
    for hd in (None, "", ["example.com"], 7, True):
        assert read(hd=hd, email="a@example.com", email_verified=True).domain is None, hd
    assert read(email="a@example.com", email_verified=True).domain is None


def test_an_unverified_email_is_never_shown(signin):
    r = sign_in_with_google(signin, {**OWNER, "email_verified": False})
    signin.cookies.set(auth.COOKIE, session(r))
    assert signin.get("/api/status").json()["user"] == {}, "signed in with Google, no address to show"


def test_an_account_not_on_the_list_is_turned_away_by_its_id(signin, caplog):
    caplog.set_level(logging.WARNING, logger="randostats")
    assert failed(sign_in_with_google(signin, STRANGER))
    logged = caplog.text
    assert STRANGER["sub"] in logged and google.ALLOWED in logged
    assert STRANGER["email"] not in logged, "the email is not logged"


@pytest.mark.parametrize("value, back", [
    ("/", "/"), ("/m", "/m"), ("/m?x=1", "/m?x=1"), ("/#people", "/#people"), ("/ü", "/%C3%BC"),
    ("/%2F%2Fevil.example", "/%2F%2Fevil.example"),
])
def test_the_return_path_stays_a_path_here(value, back):
    assert google.return_path(value) == back


@pytest.mark.parametrize("value", [
    None, "", "m", "//evil.example", "/\\evil.example", "https://evil.example/", "/\t/evil.example",
    "/\n/evil.example", "/api/status", "/api", "javascript:alert(1)", "\\\\evil.example", "/x\x7f",
])
def test_the_return_path_cannot_leave_the_site(value):
    assert google.return_path(value) == "/"


def test_a_return_path_that_would_drop_the_cookie_comes_back_to_the_root(signin):
    """Percent-encoded, a path of non-ASCII letters grows nine times over; a browser
    drops a cookie over 4096 bytes, and the sign-in with it."""
    assert google.return_path("/" + "é" * 170) == "/" + "%C3%A9" * 170
    assert google.return_path("/" + "é" * 171) == "/"
    for value in ("/" + "é" * 170, "/" + "x" * 1023, "/" + "😀" * 2000):
        r = signin.get(google.START, params={"return": value}, follow_redirects=False)
        line = set_cookies(r)[google.FLOW_COOKIE]
        assert len(line.split(";", 1)[0]) < 2000, len(line)


@pytest.mark.parametrize("attack", ["//evil.example/x", "/\\evil.example", "https://evil.example/", "%2F%2Fevil"])
def test_the_whole_flow_never_sends_anyone_off_the_site(signin, attack):
    r = sign_in_with_google(signin, return_to=attack)
    location = r.headers["location"]
    assert r.status_code == 302 and location.startswith("/") and not location.startswith("//")
    assert "\\" not in location and "evil.example" not in urlsplit(location).netloc


# -- what is turned away ------------------------------------------------------------------

def test_without_the_sign_in_cookie_nothing_is_asked_of_google(signin):
    location, _ = begin(signin)
    assert failed(come_back(signin, location, None))
    assert signin.fake.calls == []


def test_a_state_that_is_not_this_sign_in_s_is_refused(signin):
    location, cookie = begin(signin)
    other, _ = begin(signin)
    assert failed(come_back(signin, other, cookie)), "a code meant for another sign-in"
    back = urlsplit(fake_google.authorize(location, OWNER))
    query = {k: v[0] for k, v in parse_qs(back.query).items()}
    for state in ("", query["state"][:-1], query["state"] + "x", "é"):
        r = signin.get(back.path + "?" + urlencode({**query, "state": state}),
                       headers={"cookie": f"{google.FLOW_COOKIE}={cookie}"}, follow_redirects=False)
        assert failed(r), state
    r = signin.get(back.path + "?" + urlencode({"code": query["code"]}),
                   headers={"cookie": f"{google.FLOW_COOKIE}={cookie}"}, follow_redirects=False)
    assert failed(r), "no state at all"
    assert signin.fake.calls == []


def test_google_s_error_is_refused_and_not_repeated(signin, caplog):
    caplog.set_level(logging.WARNING, logger="randostats")
    location, cookie = begin(signin)
    back = urlsplit(fake_google.authorize(location, OWNER))
    query = {k: v[0] for k, v in parse_qs(back.query).items()}
    canary = "access_denied CANARY-<script>"
    r = signin.get(back.path + "?" + urlencode({**query, "error": canary}),
                   headers={"cookie": f"{google.FLOW_COOKIE}={cookie}"}, follow_redirects=False)
    assert failed(r) and signin.fake.calls == []
    assert "CANARY" not in caplog.text and "CANARY" not in r.text


def test_a_sign_in_cookie_this_process_did_not_sign_is_refused(signin, tmp_path):
    location, cookie = begin(signin)
    payload, mac = cookie.split(".")
    flow = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    forged = base64.urlsafe_b64encode(json.dumps({**flow, "r": "//evil.example"}).encode()).rstrip(b"=").decode()
    for value in (f"{forged}.{mac}", payload, f"{payload}.", f"{payload}.{mac[:-1]}{'B' if mac.endswith('A') else 'A'}", f"{payload}\xe9.{mac}"):
        assert failed(come_back(signin, location, value)), value
    with app(tmp_path / "other") as other:
        location2, cookie2 = begin(other)
    assert failed(come_back(signin, location2, cookie2)), "another process's sign-in, same client"
    assert signin.fake.calls == []


def test_a_sign_in_cookie_runs_out():
    """Ten minutes, by the clock of the process that signed it."""
    now = [1_000.0]
    flow = google.GoogleSignIn(fake_google.config(), fetch=FakeGoogle(), clock=lambda: now[0])
    location, cookie = flow.start("/m")
    state = parse_qs(urlsplit(location).query)["state"][0]
    now[0] += 599
    assert flow.check(cookie, {"state": state}).back == "/m"
    now[0] += 1
    with pytest.raises(google.Refused, match="expired"):
        flow.check(cookie, {"state": state})


def test_only_the_cookie_s_own_name_is_read():
    """A cookie named with a raw 0xA0 or 0x85 byte in front is another cookie to a
    browser, one a sibling subdomain or a plain-http answer may set (Chromium stores
    and sends it, measured with a Set-Cookie carrying the byte). Starlette decodes the
    header as Latin-1 and its parser strips both as whitespace; this one does not.
    The test client re-encodes a header as UTF-8 and cannot send the raw byte, so
    the walk (e2e/test_site.py) sends it to the running server over a socket."""
    from starlette.requests import cookie_parser

    for byte in ("\xa0", "\x85"):
        header = f"{byte}{google.FLOW_COOKIE}=v"
        assert cookie_parser(header) == {google.FLOW_COOKIE: "v"}, "what the name would be read as"
        assert google.read_cookie(header, google.FLOW_COOKIE) is None, repr(byte)
    assert google.read_cookie(f"a=1; \t{google.FLOW_COOKIE}=v ;b=2", google.FLOW_COOKIE) == "v"
    assert google.read_cookie(f"x{google.FLOW_COOKIE}=v", google.FLOW_COOKIE) is None
    assert google.read_cookie(f"{google.FLOW_COOKIE}=first; {google.FLOW_COOKIE}=second",
                              google.FLOW_COOKIE) == "first"


@pytest.mark.parametrize("answer", [
    (400, b'{"error": "invalid_grant"}'), (500, b""), (302, b""), (200, b"not json"), (200, b"[]"),
    (200, b'{"access_token": "x"}'), (200, b'{"id_token": 42}'), (200, b'{"id_token": "a.b"}'),
    (200, b'{"id_token": "a.!!!.c"}'), (200, b'{"id_token": "a.bm90IGpzb24.c"}'),
    (200, b"\xff\xfe"), (200, b"{" + b" " * google.RESPONSE_CAP + b"}"),
])
def test_an_answer_from_the_token_endpoint_that_is_not_a_token_is_refused(signin, answer):
    signin.fake.answer = answer
    assert failed(sign_in_with_google(signin))
    assert len(signin.fake.calls) == 1


def test_a_token_is_read_only_from_a_whole_answer(tmp_path):
    """A token with an error status, or past the size an answer may have, is not taken."""
    def answered(status, padding=b""):
        fake = FakeGoogle()

        def fetch(url, body, timeout):
            _, answer = fake(url, body, timeout)
            return status, answer + padding
        return fetch

    for i, (status, padding) in enumerate(((500, b""), (201, b""), (200, b" " * (64 * 1024)))):
        with app(tmp_path / str(i), fake=answered(status, padding)) as c:
            assert failed(sign_in_with_google(c)), (status, len(padding))
    with app(tmp_path / "whole", fake=answered(200, b" " * 100)) as c:
        assert auth.COOKIE in set_cookies(sign_in_with_google(c)), "the control: the same answer, whole"


@pytest.mark.parametrize("error", [urllib.error.URLError("down"), TimeoutError("slow"), ssl.SSLError("bad"),
                                   ConnectionResetError(), http.client.IncompleteRead(b"")])
def test_a_token_endpoint_that_cannot_be_reached_is_a_refusal_not_a_crash(signin, error):
    signin.fake.raises = error
    assert failed(sign_in_with_google(signin))


def test_a_code_is_traded_once(signin):
    location, cookie = begin(signin)
    back = urlsplit(fake_google.authorize(location, OWNER))
    url = f"{back.path}?{back.query}"
    assert signin.get(url, headers={"cookie": f"{google.FLOW_COOKIE}={cookie}"},
                      follow_redirects=False).status_code == 302
    assert failed(signin.get(url, headers={"cookie": f"{google.FLOW_COOKIE}={cookie}"}, follow_redirects=False))


def test_no_code_is_no_exchange(signin):
    location, cookie = begin(signin)
    state = parse_qs(urlsplit(location).query)["state"][0]
    for query in ({"state": state}, {"state": state, "code": ""}):
        r = signin.get(google.CALLBACK + "?" + urlencode(query),
                       headers={"cookie": f"{google.FLOW_COOKIE}={cookie}"}, follow_redirects=False)
        assert failed(r), query
    assert signin.fake.calls == []


NOW = 1_800_000_000


@pytest.mark.parametrize("claims, reason", [
    ({"iss": "https://evil.example"}, "iss"), ({"iss": None}, "iss"), ({"iss": ["accounts.google.com"]}, "iss"),
    ({"iss": "https://evil.accounts.google.com"}, "iss"), ({"iss": "evilaccounts.google.com"}, "iss"),
    ({"iss": "https://accounts.google.com/"}, "iss"), ({"iss": "https://accounts.google.com.evil.example"}, "iss"),
    ({"aud": "someone-else.apps.googleusercontent.com"}, "aud"), ({"aud": [fake_google.CLIENT_ID]}, "aud"),
    ({"aud": None}, "aud"),
    ({"azp": "someone-else.apps.googleusercontent.com"}, "azp"), ({"azp": ""}, "azp"),
    ({"exp": NOW - 61}, "exp"), ({"exp": None}, "exp"), ({"exp": "9999999999"}, "exp"), ({"exp": True}, "exp"),
    ({"exp": float("inf")}, "exp"), ({"exp": 10 ** 400}, "exp"),
    ({"iat": NOW + 61}, "iat"), ({"iat": None}, "iat"), ({"iat": float("nan")}, "iat"), ({"iat": True}, "iat"),
    ({"nonce": "another"}, "nonce"), ({"nonce": None}, "nonce"), ({"nonce": 7}, "nonce"),
    ({"sub": None}, "sub"), ({"sub": ""}, "sub"), ({"sub": "a b"}, "sub"), ({"sub": "x" * 256}, "sub"),
    ({"sub": 123}, "sub"),
])
def test_every_claim_is_checked(claims, reason):
    good = {"iss": "https://accounts.google.com", "aud": fake_google.CLIENT_ID, "azp": fake_google.CLIENT_ID,
            "sub": OWNER["sub"], "iat": NOW, "exp": NOW + 3600, "nonce": "the-nonce"}
    assert google.check_claims(good, client_id=fake_google.CLIENT_ID, nonce="the-nonce", now=NOW).sub == OWNER["sub"]
    bad = {**good, **claims}
    with pytest.raises(google.Refused) as refused:
        google.check_claims(bad, client_id=fake_google.CLIENT_ID, nonce="the-nonce", now=NOW)
    assert refused.value.reason == reason


def test_the_clock_is_allowed_a_minute_either_way():
    good = {"iss": "accounts.google.com", "aud": fake_google.CLIENT_ID, "sub": "1", "nonce": "n"}
    for exp, iat in ((NOW - 59, NOW - 3600), (NOW + 3600, NOW + 59)):
        assert google.check_claims({**good, "exp": exp, "iat": iat}, client_id=fake_google.CLIENT_ID,
                                   nonce="n", now=NOW).sub == "1", "no azp at all is fine too"
    for missing in ("iss", "aud", "sub", "nonce"):
        with pytest.raises(google.Refused):
            google.check_claims({k: v for k, v in {**good, "exp": NOW + 9, "iat": NOW}.items() if k != missing},
                                client_id=fake_google.CLIENT_ID, nonce="n", now=NOW)
    with pytest.raises(google.Refused):
        google.check_claims([good], client_id=fake_google.CLIENT_ID, nonce="n", now=NOW)


@pytest.mark.parametrize("claims", [{"aud": "someone-else.apps.googleusercontent.com"}, {"nonce": "replayed"},
                                    {"exp": 1}, {"iss": "https://evil.example"}, {"azp": "x"}])
def test_a_bad_claim_through_the_whole_flow_opens_nothing(signin, claims):
    assert failed(sign_in_with_google(signin, claims=claims))


def test_the_email_is_kept_only_when_google_says_it_is_verified():
    good = {"iss": "accounts.google.com", "aud": "c", "sub": "1", "nonce": "n", "exp": NOW + 9, "iat": NOW}
    def read(**extra):
        return google.check_claims({**good, **extra}, client_id="c", nonce="n", now=NOW).email
    assert read(email="a@b.example", email_verified=True) == "a@b.example"
    for extra in ({"email": "a@b.example"}, {"email": "a@b.example", "email_verified": False},
                  {"email": "a@b.example", "email_verified": "true"}, {"email": "a@b.example", "email_verified": 1},
                  {"email": "a b@c.example", "email_verified": True}, {"email": "a@\nb", "email_verified": True},
                  {"email": ["a@b.example"], "email_verified": True}):
        assert read(**extra) is None, extra


# -- from another site, and how often ---------------------------------------------------------

def test_google_may_send_the_browser_back_from_another_site(signin):
    """The callback is the one /api/ address a cross-site navigation may reach:
    Google's redirect says Sec-Fetch-Site: cross-site. Only GET."""
    location, cookie = begin(signin)
    r = come_back(signin, location, cookie, **{"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate"})
    assert r.status_code == 302 and auth.COOKIE in set_cookies(r)
    for method in ("POST", "PUT", "DELETE", "HEAD"):
        r = signin.request(method, google.CALLBACK, headers={"sec-fetch-site": "cross-site"}, follow_redirects=False)
        assert r.status_code == 403, method
    assert signin.get("/api/config", headers={"sec-fetch-site": "cross-site"}).status_code == 403, \
        "only the callback"


def test_exchanges_count_against_the_guess_limit(signin):
    """Each exchange is a call to Google for whoever holds a fresh sign-in cookie,
    which anyone can get; so they are counted like password guesses."""
    for _ in range(auth.MAX_ATTEMPTS):
        assert failed(sign_in_with_google(signin, STRANGER))
    calls = len(signin.fake.calls)
    assert failed(sign_in_with_google(signin, OWNER)), "the limit holds for the owner's client too"
    assert len(signin.fake.calls) == calls, "and nothing more is asked of Google"


def test_a_callback_that_never_gets_to_google_is_not_counted(signin):
    location, _ = begin(signin)
    for _ in range(auth.MAX_ATTEMPTS * 3):
        assert failed(come_back(signin, location, None))
    assert sign_in_with_google(signin).status_code == 302
    assert auth.COOKIE in set_cookies(sign_in_with_google(signin))


def test_a_good_sign_in_clears_the_count(signin):
    for _ in range(auth.MAX_ATTEMPTS - 1):
        assert failed(sign_in_with_google(signin, STRANGER))
    assert auth.COOKIE in set_cookies(sign_in_with_google(signin))
    for _ in range(auth.MAX_ATTEMPTS - 1):
        assert failed(sign_in_with_google(signin, STRANGER))
    assert auth.COOKIE in set_cookies(sign_in_with_google(signin)), "the count started again"


# -- the gate, the password and Google together --------------------------------------------------

def test_a_gate_with_no_password_matches_no_password():
    gate = auth.Gate(None)
    assert not gate.has_password and not gate.password_matches("") and not gate.password_matches(None)
    assert gate.valid(gate.issue({"email": "a@b"})) and gate.account(gate.issue({})) == {}
    assert auth.Gate(PASSWORD).account(auth.Gate(PASSWORD).issue()) is None


def test_both_ways_in_open_the_same_app(tmp_path):
    with app(tmp_path, password=PASSWORD) as c:
        r = c.post("/api/login", json={"password": PASSWORD})
        assert r.status_code == 200
        assert c.get("/api/status").json()["user"] is None, "a password session names nobody"
        c.cookies.clear()
        c.cookies.set(auth.COOKIE, session(sign_in_with_google(c)))
        assert c.get("/api/status").json()["user"] == {"email": OWNER["email"]}


def test_a_connection_beyond_loopback_is_served_with_google_alone(tmp_path):
    lan = "https://stats.example.com"
    with app(tmp_path, config=fake_google.config("https://stats.example.com/"), base_url=lan,
             allowed_hosts=["stats.example.com"]) as c:
        assert c.get("/api/status").status_code == 401, "asked to sign in, not refused outright"
        c.cookies.set(auth.COOKIE, session(sign_in_with_google(c)))
        assert c.get("/api/status").status_code == 200


# -- the command line ---------------------------------------------------------------------------

def _serve(monkeypatch, tmp_path, *args):
    import uvicorn

    from randostats import cli

    served = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))
    status = cli.main(["--db", str(tmp_path / "cli.db"), "serve", *args])
    return status, served


def _google_env(monkeypatch, **overrides):
    for name, value in {**ENV, **overrides}.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("args", [
    ("--host", "0.0.0.0", "--allow-host", "stats.example.com"),
    ("--host", "127.0.0.1", "--allow-host", "stats.example.com"),
])
def test_serve_takes_sign_in_with_google_in_place_of_a_password(tmp_path, monkeypatch, args):
    _google_env(monkeypatch)
    status, served = _serve(monkeypatch, tmp_path, *args)
    assert status == 0
    with TestClient(served["app"], base_url="https://stats.example.com") as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/config").json()["auth"] == {"google": True, "password": False}


def test_serve_on_loopback_with_google_asks_for_it(tmp_path, monkeypatch):
    _google_env(monkeypatch, **{google.APP_URL: "http://localhost:8765"})
    status, served = _serve(monkeypatch, tmp_path)
    assert status == 0
    with TestClient(served["app"], base_url=LOCAL) as c:
        assert c.get("/api/status").status_code == 401


@pytest.mark.parametrize("missing", [google.APP_URL, *google.TOUCHED])
def test_serve_refuses_half_a_set_up(tmp_path, monkeypatch, capsys, missing):
    _google_env(monkeypatch, **{missing: ""})
    status, served = _serve(monkeypatch, tmp_path, "--allow-host", "stats.example.com")
    assert status == 2 and served == {}
    err = capsys.readouterr().err
    assert f"missing {missing}" in err and fake_google.CLIENT_SECRET not in err


def test_serve_refuses_a_site_address_it_would_not_answer_to(tmp_path, monkeypatch, capsys):
    _google_env(monkeypatch)
    status, served = _serve(monkeypatch, tmp_path)
    assert status == 2 and served == {}
    assert "--allow-host stats.example.com" in capsys.readouterr().err


def test_serve_beyond_loopback_with_neither_still_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(google.APP_URL, "https://stats.example.com/")  # alone, it asks for nothing
    status, served = _serve(monkeypatch, tmp_path, "--host", "0.0.0.0", "--allow-host", "stats.example.com")
    assert status == 2 and served == {}
    err = capsys.readouterr().err
    assert "RANDOSTATS_PASSWORD" in err and "sign-in with Google" in err


# -- the token endpoint is reached over verified TLS ------------------------------------------------

class _Token(http.server.BaseHTTPRequestHandler):
    seen: list = []
    answer = (200, b'{"id_token": "x"}', {})

    def do_POST(self):
        self.seen.append((self.path, self.rfile.read(int(self.headers["content-length"]))))
        status, body, headers = self.answer
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def token_server(monkeypatch):
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    servers = []

    def serve(answer, context=None):
        handler = type("Token", (_Token,), {"seen": [], "answer": answer})
        server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        if context is not None:
            server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server, handler

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def test_the_exchange_follows_no_redirect(token_server):
    server, handler = token_server((302, b"", {"location": "/elsewhere"}))
    status, body = google.post_form(f"http://127.0.0.1:{server.server_port}/token", b"a=1", 5)
    assert status == 302 and body == b""
    assert handler.seen == [("/token", b"a=1")], "one request, to where it was sent"


def test_the_exchange_reads_no_more_than_the_cap(token_server):
    server, _ = token_server((200, b"x" * (3 * 64 * 1024), {}))
    status, body = google.post_form(f"http://127.0.0.1:{server.server_port}/token", b"", 5)
    assert status == 200 and len(body) == 64 * 1024 + 1, "64 KiB, and one byte to tell a longer answer by"


def _certificate(tmp_path, name):
    key, cert = tmp_path / f"{name}.key", tmp_path / f"{name}.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                    "-days", "1", "-subj", f"/CN={name}", "-addext", f"subjectAltName=DNS:{name}"],
                   check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return cert, context


def test_the_exchange_verifies_the_certificate_and_the_name(token_server, tmp_path, monkeypatch):
    """What lets the ID token's signature go unchecked (OIDC Core 3.1.3.7 item 6) is
    that the token came straight from Google over TLS the client validated. A server
    whose certificate the system does not trust, or that is not the name asked for,
    is never read from."""
    cert, context = _certificate(tmp_path, "localhost")
    server, handler = token_server((200, b'{"id_token": "x"}', {}), context)
    url = f"https://localhost:{server.server_port}/token"
    with pytest.raises(urllib.error.URLError) as refused:
        google.post_form(url, b"", 5)
    assert isinstance(refused.value.reason, ssl.SSLCertVerificationError)
    assert "certificate verify failed" in str(refused.value.reason)
    # Trusted, the same server answers: the refusal above was the verification.
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    assert google.post_form(url, b"", 5) == (200, b'{"id_token": "x"}')
    # Trusted, but not the name it was reached by.
    with pytest.raises(urllib.error.URLError) as refused:
        google.post_form(f"https://127.0.0.1:{server.server_port}/token", b"", 5)
    assert "match" in str(refused.value.reason) or "mismatch" in str(refused.value.reason)
    assert len(handler.seen) == 1


def test_only_google_s_own_addresses_are_used():
    """No configuration names another host: the addresses are constants."""
    source = (Path(google.__file__)).read_text()
    assert re.findall(r'"(https://[^"]+)"', source) == [google.AUTHORIZE_URL, google.TOKEN_URL,
                                                       "https://accounts.google.com"]
    assert "os.environ" not in source and "getenv" not in source

    class Recording(dict):
        read: list = []

        def get(self, key, default=None):
            self.read.append(key)
            return super().get(key, default)

    environ = Recording(ENV)
    google.read_config(environ)
    assert environ.read == [google.APP_URL, *google.TOUCHED], "read_config reads its four variables, no other"


# -- the phone's service worker never sees the sign-in ---------------------------------------------

def test_the_service_worker_cannot_answer_the_sign_in():
    """The worker is registered for /m: Google's redirect back to /api/auth/google/callback
    and the start of a sign-in are navigations outside its scope, and its fetch handler
    returns early for /api/ besides."""
    source = (STATIC / "m.js").read_text()
    assert re.findall(r'serviceWorker\.register\("([^"]+)", \{ scope: "([^"]+)" \}\)', source) == [("/sw.js", "/m")]
    for path in (google.START, google.CALLBACK, "/login"):
        assert not path.startswith("/m"), path
    assert 'url.pathname.startsWith("/api/")) return' in (STATIC / "m-sw.js").read_text()


def test_the_pages_say_who_is_signed_in_only_as_text():
    login = (STATIC / "login.js").read_text()
    assert "/api/config" in login and "google.hidden = false" in login
    assert "ways.google === true" in login and "Sign-in didn't complete." in login
    assert '"here"' in login and "it is the one Google sends you back to." in login
    assert "history.replaceState" in login
    app_js = (STATIC / "app.js").read_text()
    assert '$("#signed-in").textContent = who;' in app_js
    assert 'id="google-start" href="/api/auth/google/start"' in (STATIC / "login.html").read_text()
    assert api.PUBLIC_PATHS >= {"/api/config", google.START, google.CALLBACK}
