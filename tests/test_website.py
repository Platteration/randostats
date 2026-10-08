"""The website: one policy, written once in randostats/api.py and printed in README.md,
on every response the app gives; the files that are the site and the ones that are
not; robots.txt and security.txt; the safety net in every page. The browser half of
this is e2e/ (`pytest -q e2e`), which drives both front ends under the policy."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from randostats import api
from randostats.api import create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "randostats" / "static"
README = ROOT / "README.md"
LOCAL = "http://localhost"
HTML = {"accept": "text/html,application/xhtml+xml"}


@pytest.fixture(autouse=True)
def no_password_from_the_environment(monkeypatch):
    monkeypatch.delenv("RANDOSTATS_PASSWORD", raising=False)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """One app for the module: nothing here writes, and each app warms a dictionary."""
    with pytest.MonkeyPatch.context() as env:
        env.delenv("RANDOSTATS_PASSWORD", raising=False)
        app = create_app(tmp_path_factory.mktemp("web") / "web.db", use_llm=False)
    with TestClient(app, base_url=LOCAL, raise_server_exceptions=False) as c:
        yield c


def deploy_section() -> str:
    text = README.read_text(encoding="utf-8")
    assert text.count("\n### Deploy\n") == 1, "README.md has one Deploy section"
    return text.split("\n### Deploy\n", 1)[1].split("\n## ", 1)[0]


def readme_policy() -> dict[str, str]:
    """The header block README.md prints under Deploy, as name -> value."""
    blocks = re.findall(r"```http\n(.*?)\n```", deploy_section(), re.S)
    assert len(blocks) == 1, "the Deploy section prints the headers once, in one ```http block"
    headers: dict[str, str] = {}
    for line in blocks[0].splitlines():
        name, sep, value = line.partition(": ")
        assert sep and name not in headers, f"one `Name: value` per line, each name once: {line!r}"
        headers[name] = value
    return headers


def policy(response) -> dict[str, str | None]:
    names = ["Content-Security-Policy", *api.HEADERS, "Strict-Transport-Security"]
    return {name: response.headers.get(name) for name in names}


PLAIN = {"Content-Security-Policy": api.CSP, **api.HEADERS, "Strict-Transport-Security": None}


# -- one policy, the same everywhere it is written ----------------------------------

def test_the_policy_is_the_same_in_every_place_it_is_written():
    """The code that sets it and the README that tells a deployer what to expect.
    A header changed in one and not the other fails here."""
    assert readme_policy() == {"Content-Security-Policy": api.CSP, **api.HEADERS}
    deploy = deploy_section()
    assert api.CSP_HTTPS == api.CSP + "; upgrade-insecure-requests"
    assert "`; upgrade-insecure-requests`" in deploy
    assert f"`Strict-Transport-Security: {api.HSTS}`" in deploy


def test_the_policy_is_strict():
    directives = dict(d.strip().split(" ", 1) for d in api.CSP.split(";"))
    assert directives["default-src"] == "'none'"
    for name in ("script-src", "style-src"):
        assert directives[name] == "'self'", name
    assert "'unsafe-inline'" not in api.CSP and "'unsafe-eval'" not in api.CSP
    assert "*" not in api.CSP and "data:" not in api.CSP and "http" not in api.CSP
    assert directives["connect-src"] == "'self'", "the API and nothing else"
    for name in ("base-uri", "form-action", "object-src", "frame-ancestors"):
        assert directives[name] == "'none'", name
    features = dict(item.strip().split("=", 1) for item in api.PERMISSIONS.split(","))
    assert {name for name, value in features.items() if value != "()"} == {"microphone", "clipboard-write"}
    assert features["microphone"] == features["clipboard-write"] == "(self)"
    assert "camera" in features and "geolocation" in features and "payment" in features
    assert api.HSTS == "max-age=31536000; includeSubDomains"


# -- on every response --------------------------------------------------------------

def _requests(client):
    """One of every kind of answer the app gives, with the status it has to give."""
    yield client.get("/"), 200
    yield client.head("/"), 200
    yield client.get("/m"), 200
    yield client.get("/static/app.js"), 200
    yield client.get("/static/app.css"), 200
    yield client.get("/sw.js"), 200
    yield client.get("/manifest.webmanifest"), 200
    yield client.get("/robots.txt"), 200
    yield client.get("/.well-known/security.txt"), 200
    yield client.get("/samples/sample_messages.json"), 200
    yield client.get("/api/status"), 200
    yield client.post("/api/counterpoint", json={"text": "70% of people"}), 200
    yield client.get("/no/such/page", headers=HTML), 404
    yield client.get("/static/no-such-file.js"), 404
    yield client.get("/api/no/such/route"), 404
    yield client.post("/"), 405
    yield client.post("/api/counterpoint", json={"nope": 1}), 422
    yield client.get("/api/status", headers={"host": "attacker.example"}), 400
    yield client.post("/api/counterpoint", json={"text": "x"}, headers={"sec-fetch-site": "cross-site"}), 403
    yield client.post("/api/counterpoint", content=b"x" * (api.MAX_JSON_BYTES + 1),
                      headers={"content-type": "application/json"}), 413


def test_every_response_carries_the_policy(client):
    seen = 0
    for response, status in _requests(client):
        where = f"{response.request.method} {response.request.url.path} -> {response.status_code}"
        assert response.status_code == status, where
        assert policy(response) == PLAIN, where
        seen += 1
    assert seen == 20


@pytest.mark.parametrize("path", ["/", "/m", "/sw.js", "/manifest.webmanifest", "/robots.txt",
                                  "/.well-known/security.txt", "/samples/sample_messages.json",
                                  "/static/app.css"])
def test_pages_and_site_files_answer_head(client, path):
    """As a web server's files do: link checkers and uptime monitors ask with HEAD."""
    r = client.head(path)
    assert r.status_code == 200 and r.content == b"", path
    assert policy(r) == PLAIN, path


def test_a_crash_carries_the_policy_too(tmp_path):
    app = create_app(tmp_path / "boom.db", use_llm=False)

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("boom")

    with TestClient(app, base_url=LOCAL, raise_server_exceptions=False) as c:
        r = c.get("/api/boom")
        assert r.status_code == 500 and policy(r) == PLAIN
        assert r.headers["cache-control"] == "no-store"


def test_over_https_the_policy_upgrades_and_hsts_is_sent(tmp_path):
    """The scheme is the request's own, or X-Forwarded-Proto from a proxy uvicorn
    believes (it rewrites the scope before the app sees it)."""
    with TestClient(create_app(tmp_path / "tls.db", use_llm=False), base_url="https://localhost") as c:
        for path in ("/", "/api/status", "/static/app.css", "/no/such/page"):
            r = c.get(path, headers=HTML)
            assert r.headers["content-security-policy"] == api.CSP_HTTPS, path
            assert r.headers["strict-transport-security"] == api.HSTS, path
            assert r.headers["x-frame-options"] == "DENY", path


def test_the_api_is_never_stored_and_everything_else_is_revalidated(client):
    """API answers are one person's messages and change with each import; no file
    name carries a version, so a file is revalidated on every use."""
    for response, _ in _requests(client):
        path = response.request.url.path
        want = "no-store" if path.startswith("/api/") else "no-cache"
        assert response.headers.get("cache-control") == want, path
    etag = client.get("/static/app.js").headers["etag"]
    assert client.get("/static/app.js", headers={"if-none-match": etag}).status_code == 304, \
        "an unchanged file costs a 304"


# -- the site, and what is not the site -----------------------------------------------

@pytest.mark.parametrize("path", [
    "/README.md", "/pyproject.toml", "/CLAUDE.md", "/.git/config", "/.git/HEAD", "/.env",
    "/randostats/api.py", "/api.py", "/tests/test_api.py", "/e2e/conftest.py",
    "/samples/make_sample.py", "/samples/make_icons.py", "/samples/sample_whatsapp_alex.txt",
    "/samples/", "/static/", "/static/%2e%2e/api.py", "/static/..%2fapi.py",
    "/static/%2e%2e/%2e%2e/pyproject.toml", "/docs", "/redoc", "/openapi.json",
])
def test_the_repository_is_not_the_site(client, path):
    r = client.get(path, headers=HTML)
    assert r.status_code == 404, (path, r.status_code)
    for leak in ("def create_app", "[project]", "[core]", "ref: refs/", "import ", "sample_messages"):
        assert leak not in r.text, (path, leak)


def test_a_wrong_address_is_a_page_in_the_app_s_look(client):
    r = client.get("/no/such/page", headers=HTML)
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
    assert "That page isn’t here" in r.text and 'href="/static/app.css"' in r.text
    assert "<script" not in r.text, "the 404 page works with no script at all"
    assert 'href="/"' in r.text and 'href="/m"' in r.text
    # The API, and a client that did not ask for HTML, keep FastAPI's JSON.
    assert client.get("/api/no/such/route", headers=HTML).json() == {"detail": "Not Found"}
    assert client.get("/no/such/page").json() == {"detail": "Not Found"}


def test_robots_txt_keeps_crawlers_out(client):
    r = client.get("/robots.txt")
    assert r.headers["content-type"].startswith("text/plain")
    lines = [line for line in r.text.splitlines() if line and not line.startswith("#")]
    assert lines == ["User-agent: *", "Disallow: /"]


def test_security_txt_is_current_and_says_what_security_md_says(client):
    """RFC 9116: Contact and Expires are required, Expires no more than a year out.
    This fails once Expires has passed: renew it (and this test's reading of it does
    not move)."""
    r = client.get("/.well-known/security.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    fields = dict(line.split(": ", 1) for line in r.text.splitlines() if line and not line.startswith("#"))
    assert set(fields) == {"Contact", "Expires", "Policy", "Preferred-Languages"}
    expires = datetime.strptime(fields["Expires"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert now < expires, "security.txt has expired: renew Expires"
    assert expires - now <= timedelta(days=366), "Expires is at most a year out"
    # SECURITY.md asks for private reports, so Contact is the private form, not the issues.
    assert "private vulnerability reporting" in (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert fields["Contact"] == "https://github.com/Platteration/randostats/security/advisories/new"
    assert fields["Policy"] == "https://github.com/Platteration/randostats/blob/main/SECURITY.md"
    assert fields["Preferred-Languages"] == "en"


# -- the safety net -------------------------------------------------------------------

SCRIPTED_PAGES = {"index.html": "app.js", "m.html": "m.js", "login.html": "login.js"}


@pytest.mark.parametrize("page, starter", sorted(SCRIPTED_PAGES.items()))
def test_every_page_loads_the_safety_net_first_and_says_when_javascript_is_off(page, starter):
    html = (STATIC / page).read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="([^"]+)"></script>', html)
    assert scripts and scripts[0] == "/static/guard.js", f"{page}: guard.js is the first script"
    assert html.index("/static/guard.js") < html.index("</head>"), f"{page}: in the head, before the page"
    assert not re.search(r"<script(?![^>]*\ssrc=)", html), f"{page}: no inline script"
    assert re.search(r'<noscript><div class="guard-note">[^<]*JavaScript', html), f"{page}: a noscript note"
    # The page's own script is what tells the guard it started; without the call
    # the guard reports a failure on every load.
    source = (STATIC / starter).read_text(encoding="utf-8")
    assert "window.RandoGuard.started()" in source, starter


def test_the_not_found_page_has_no_script():
    html = (STATIC / "404.html").read_text(encoding="utf-8")
    assert "<script" not in html and "<noscript" not in html


def test_the_phone_shell_caches_what_its_page_loads():
    """Offline, /m is answered from the worker's cache. A script it loads that the
    cache does not hold is missing offline, and guard.js missing is silent."""
    html = (STATIC / "m.html").read_text(encoding="utf-8")
    loaded = set(re.findall(r'<script src="([^"]+)"', html))
    loaded |= set(re.findall(r'<link rel="(?:stylesheet|manifest)" href="([^"]+)"', html))
    shell = set(re.findall(r'"(/[^"]*)"', re.search(r"const SHELL = \[(.*?)\]", (STATIC / "m-sw.js").read_text(), re.S).group(1)))
    assert loaded and loaded <= shell, loaded - shell
