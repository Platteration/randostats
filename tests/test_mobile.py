"""The phone app at /m.

It shares the counterpoint API with the desktop and nothing else. These guard
the parts that are easy to break silently: the routes, the manifest, and the
service worker's promise never to cache an answer or a setting.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from randostats.api import create_app

STATIC = Path(__file__).resolve().parent.parent / "randostats" / "static"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "m.db", use_llm=False)) as c:
        yield c


def test_the_phone_page_is_served(client):
    r = client.get("/m")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "/static/m.js" in r.text and 'rel="manifest"' in r.text


def test_the_phone_page_has_no_inline_script(client):
    """script-src 'self' means an inline script silently does nothing."""
    assert not re.search(r"<script(?![^>]*\ssrc=)", client.get("/m").text)


def test_the_manifest_is_served_as_a_manifest(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    # With the global nosniff header, a text/plain manifest is simply refused.
    assert r.headers["content-type"].startswith("application/manifest+json")
    manifest = json.loads(r.text)
    assert manifest["start_url"] == "/m" and manifest["scope"] == "/m"
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]


def test_the_apple_touch_icon_exists(client):
    """iOS ignores manifest icons and wants this one by name."""
    assert 'rel="apple-touch-icon"' in client.get("/m").text
    assert client.get("/static/icon-180.png").status_code == 200


def test_the_service_worker_is_served_from_the_root(client):
    """A worker's scope defaults to its own directory, so one under /static
    could never control /m. That is why this odd little route exists."""
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


def test_the_service_worker_never_caches_the_api():
    """Caching /api/counterpoint/packs would have the phone insisting on a
    fact count and a voice the desktop had already changed."""
    source = (STATIC / "m-sw.js").read_text()
    shell = re.search(r"const SHELL = \[(.*?)\]", source, re.S).group(1)
    assert "/api" not in shell
    assert 'url.pathname.startsWith("/api/")' in source
    assert 'request.method !== "GET"' in source


def test_the_worker_scope_cannot_reach_the_desktop_app(client):
    """Scope is a string prefix, not a path segment: a future /messages route
    would fall inside "/m" without anyone noticing."""
    paths = {route.path for route in client.app.routes if route.path.startswith("/m")}
    assert paths == {"/m", "/manifest.webmanifest"}


def test_the_desktop_app_is_untouched(client):
    r = client.get("/")
    assert r.status_code == 200 and "/static/app.js" in r.text


def test_the_phone_app_only_talks_to_the_counterpoint_api():
    """It never sees a message, so it must never ask for one."""
    source = (STATIC / "m.js").read_text()
    called = set(re.findall(r'api\("(/api/[^"?]+)', source))
    assert called <= {"/api/counterpoint", "/api/counterpoint/random", "/api/counterpoint/packs"}, called
    assert "/api/messages" not in source and "/api/stats" not in source


def test_speech_is_never_offered_on_feature_detection_alone():
    """On an installed iOS PWA the API is present, start() resolves, and no
    result ever arrives. Detection is not evidence, so the verdict is stored."""
    source = (STATIC / "m.js").read_text()
    assert "localStorage.getItem(SPEECH_KEY)" in source
    assert 'localStorage.setItem(SPEECH_KEY, "yes")' in source
    assert 'localStorage.setItem(SPEECH_KEY, "no")' in source
    # the probe lives in the sheet, not on the compose bar
    assert "#speech-try" in source
    assert 'rec.onresult' in source and "heard = true" in source


def test_the_share_image_is_ready_before_the_tap():
    """navigator.share has to be called straight out of the gesture; awaiting
    a canvas first is how iOS comes to reject it."""
    source = (STATIC / "m.js").read_text()
    share = source[source.index("function shareCard"):]
    call = share[:share.index("copyCard")]
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", call, flags=re.S)  # the prose explains the rule
    assert "await" not in code, "shareCard must not await before navigator.share"
    assert "state.shareFile" in call
    assert "renderShareImage(card)" in source[:source.index("function shareCard")]
