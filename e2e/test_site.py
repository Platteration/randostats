"""The website, driven in Chromium under the policy the server sends.

Both front ends' main flows run against `randostats serve` with every response
checked against the one policy in randostats/api.py (which tests/test_website.py
holds equal to README.md). A source the policy lacks shows up here as a
securitypolicyviolation, a console error or a request that failed; a request to
any other origin, or a status nobody asked for, fails the walk too.

The site is served at the root of its own origin, which is the only way this app
is deployed (README, "Deploy"): every address the front end writes is
root-absolute, so the walk runs at the root rather than under a sub-path.
"""

from __future__ import annotations

import http.client
import re
from urllib.parse import urlparse

from conftest import PASSWORD, REPO, Watch, new_context
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from randostats import api

PNG = b"\x89PNG\r\n\x1a\n"


def _features(header: str) -> dict[str, str]:
    return dict(item.strip().split("=", 1) for item in header.split(","))


def test_the_desktop_app_runs_under_the_policy(browser, server, tmp_path):
    watch = Watch(server.base)
    context = new_context(browser, permissions=["microphone", "clipboard-read", "clipboard-write"])
    page = context.new_page()
    watch.attach(page)

    page.goto(server.base + "/")
    page.wait_for_selector("#tab-import.active")  # nothing imported yet
    assert not page.is_visible("#guard-note"), "the safety net stands down once the page starts"

    # The first-run Overview, and its sample button: the main flow for a new visitor.
    page.click('.tabs button[data-tab="overview"]')
    page.wait_for_selector(".overview-welcome")
    page.click(".overview-actions .primary")
    page.wait_for_selector("#tab-people.active #people-chart svg path.bar")

    # The charts' per-bar timing went from a style="" attribute to the CSSOM: still there.
    delays = page.eval_on_selector_all("#people-chart path.bar", "ps => ps.map(p => p.style.animationDelay)")
    assert "0ms" in delays and "18ms" in delays, delays

    page.hover("#people-chart .hit >> nth=0")
    page.wait_for_selector("#tooltip:not([hidden]) b")
    page.click("#people-chart .hit >> nth=0")
    page.wait_for_selector("#drawer:not([hidden]) .msg")
    page.fill("#drawer-q", "the")
    page.wait_for_timeout(400)
    page.keyboard.press("Escape")
    page.click('[data-toggle-table="people-chart"]')
    page.wait_for_selector("#people-chart table.data")
    page.click('[data-toggle-table="people-chart"]')
    page.wait_for_selector("#people-chart svg")
    page.focus("#people-chart svg")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    page.wait_for_selector("#drawer:not([hidden]) .msg")
    page.keyboard.press("Escape")

    page.click('.tabs button[data-tab="convo"]')
    page.wait_for_selector("#open-chart svg")
    page.select_option("#convo-gap", "1")
    page.wait_for_selector("#convo-table table.data")

    page.click('.tabs button[data-tab="timing"]')
    page.wait_for_selector("#heatmap svg rect.clickable")
    # The swatches beside each name went from style="" to a class per hue. The
    # table they sit in is drawn after a second request, after the heatmap.
    page.wait_for_selector("#peaks table.data i.sw")
    swatches = page.eval_on_selector_all(
        "#peaks i.sw", "is => is.map(i => [i.className, getComputedStyle(i).backgroundColor])")
    assert swatches and all(re.fullmatch(r"sw hue[0-8]", c) for c, _ in swatches), swatches
    assert all(colour not in ("", "rgba(0, 0, 0, 0)") for _, colour in swatches), swatches
    assert len({colour for _, colour in swatches}) > 1, "each person keeps a hue of their own"
    person = page.eval_on_selector_all("#timing-contact option", "os => os.map(o => o.value)")[1]
    page.select_option("#timing-contact", person)
    page.wait_for_selector("#heatmap svg rect.clickable")
    page.click("#heatmap rect.clickable >> nth=0")
    page.wait_for_selector("#drawer:not([hidden]) .msg")
    page.keyboard.press("Escape")
    page.click("#month-chart .hit")
    page.wait_for_selector("#drawer:not([hidden])")
    page.keyboard.press("Escape")

    page.click('.tabs button[data-tab="spelling"]')
    page.wait_for_selector("#spell-kpis .kpi")
    page.click('.tabs button[data-tab="words"]')
    page.wait_for_selector("#tone-words tr[data-word]")
    page.click("#tone-words tr[data-word] >> nth=0")
    page.wait_for_selector("#drawer:not([hidden])")
    page.keyboard.press("Escape")

    # Wrapped: the card goes through a blob: image and a canvas to a PNG.
    page.click('.tabs button[data-tab="wrapped"]')
    page.wait_for_selector("#wrapped-card svg")
    with page.expect_download() as download:
        page.click("#wrapped-png")
    saved = tmp_path / "wrapped.png"
    download.value.save_as(saved)
    assert saved.read_bytes()[:8] == PNG and saved.stat().st_size > 10_000

    # Counterpoint: a claim, the random pair, a pack, a voice, and the microphone.
    page.click('.tabs button[data-tab="counter"]')
    page.wait_for_selector("#pack-chips .chip")
    page.fill("#counter-text", "seventy percent of people drink beer, and studies show 1 in 5 adults have a tattoo")
    page.click("#counter-go")
    page.wait_for_selector("#counter-results .cp .punch")
    page.click("#counter-random")
    page.wait_for_selector("#counter-results .cp >> nth=1")
    page.click("#pack-chips .chip:not([disabled]) >> nth=0")
    page.wait_for_timeout(300)
    page.click("#voice-chips .chip >> nth=1")
    page.wait_for_selector("#voice-chips .chip.on >> nth=0")
    page.click("#counter-listen")
    page.wait_for_timeout(1000)
    if page.inner_text("#counter-listen").startswith("■"):
        page.click("#counter-listen")
    # Headless Chromium's speech recognition never reports back, allowed or not, so
    # Listen cannot show whether the policy lets it hear. Ask for the microphone
    # itself, which is what it listens through: refused under microphone=().
    assert page.evaluate("navigator.mediaDevices.getUserMedia({audio: true})"
                         ".then(s => { s.getTracks().forEach(t => t.stop()); return 'allowed'; }, e => e.name)") == "allowed"

    # The Overview with data, and a shortcut out of it.
    page.click('.tabs button[data-tab="overview"]')
    page.wait_for_selector(".insight-card")
    page.click(".insight-card >> nth=0")
    page.wait_for_timeout(300)

    # An import from a file, then the one destructive button, confirmed.
    page.click('.tabs button[data-tab="import"]')
    page.fill("#self-name", "Sam")
    page.set_input_files("#file", str(REPO / "samples" / "sample_whatsapp_alex.txt"))
    page.click('#import-form button[type="submit"]')
    page.wait_for_selector("#import-result:has-text('Parsed')")
    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#clear-all")
    page.wait_for_selector("#import-result:has-text('Cleared')")
    assert page.is_hidden("#sign-out"), "no password, nothing to sign out of"

    # The Permissions-Policy, read back from the browser: every name it gives is
    # one Chromium knows, and exactly the ones given (self) are allowed.
    features = _features(api.PERMISSIONS)
    known = set(page.evaluate("document.featurePolicy.features()"))
    assert set(features) <= known, set(features) - known
    allowed = page.evaluate("names => names.filter(n => document.featurePolicy.allowsFeature(n))", list(features))
    assert set(allowed) == {name for name, value in features.items() if value == "(self)"}, allowed

    watch.check(page)
    context.close()


def test_the_phone_app_runs_under_the_policy_and_offline(browser, server, tmp_path):
    watch = Watch(server.base)
    context = new_context(browser, permissions=["microphone", "clipboard-read", "clipboard-write"])
    page = context.new_page()
    watch.attach(page)

    page.goto(server.base + "/m")
    page.wait_for_selector("#examples .chip")
    # Headless Chromium fetches a manifest only when asked, so ask: manifest-src is
    # measured here and nowhere else.
    manifest = context.new_cdp_session(page).send("Page.getAppManifest")
    assert manifest.get("url", "").endswith("/manifest.webmanifest") and '"start_url": "/m"' in manifest.get("data", "")
    assert not manifest.get("errors"), manifest.get("errors")

    page.click("#examples .chip >> nth=0")
    page.wait_for_selector("#cards article .punch")
    # The share image is rendered while the card is read, and Share before it is
    # ready copies the text instead; so Share until the image is what comes back.
    for _ in range(30):
        try:
            with page.expect_download(timeout=1000) as download:
                page.click('#cards [data-act="share"]')
            break
        except PlaywrightTimeout:
            continue
    else:
        raise AssertionError("Share never handed back the card as an image")
    saved = tmp_path / "card.png"
    download.value.save_as(saved)
    assert saved.read_bytes()[:8] == PNG
    page.click('#cards [data-act="copy"]')
    page.wait_for_selector("#hint:has-text('Copied')")
    page.click("#random")
    page.wait_for_selector("#cards article")
    page.fill("#claim", "most people agree and crime went up 40%")
    page.click("#go")
    page.wait_for_selector("#cards article")
    if page.is_visible("#next"):
        page.click("#next")
    page.click("#settings-open")
    page.wait_for_selector("#packs .chip")
    page.click("#packs .chip:not([disabled]) >> nth=0")
    page.wait_for_timeout(300)
    page.click("#voices .chip >> nth=1")
    page.wait_for_timeout(300)
    if page.is_visible("#speech-try"):
        page.click("#speech-try")
        page.wait_for_timeout(1500)
    page.keyboard.press("Escape")
    worker = page.evaluate("navigator.serviceWorker.ready.then(r => r.active && r.active.scriptURL)")
    assert worker == server.base + "/sw.js"
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    watch.check(page)

    # The server goes away: the installed shell still opens, safety net included.
    server.stop()
    offline = context.new_page()
    offline.goto(server.base + "/m")
    offline.wait_for_selector("#examples .chip")
    assert offline.evaluate("typeof window.RandoGuard") == "object", "guard.js is part of the cached shell"
    offline.wait_for_timeout(300)
    assert not offline.is_visible("#guard-note")
    context.close()


def test_the_safety_net(browser, server):
    """A script that never arrives, or throws before the page has started, is a note
    with a Reload button, not a page of dead controls; JavaScript off is the
    <noscript> note; an error after the start is a note that can be dismissed."""
    context = new_context(browser)
    page = context.new_page()

    page.route("**/static/app.js", lambda route: route.abort())
    page.goto(server.base + "/")
    page.wait_for_selector("#guard-note:has-text('did not start') button")
    page.unroute("**/static/app.js")

    page.route("**/static/app.js", lambda route: route.fulfill(
        status=200, content_type="text/javascript", body='throw new Error("a broken build");'))
    page.goto(server.base + "/")
    page.wait_for_selector("#guard-note:has-text('did not start')")
    page.unroute("**/static/app.js")

    # A script that arrives empty throws nothing: only the check at load sees that
    # the page never said it started.
    page.route("**/static/app.js", lambda route: route.fulfill(status=200, content_type="text/javascript", body=""))
    page.goto(server.base + "/")
    page.wait_for_selector("#guard-note:has-text('did not start')")
    page.unroute("**/static/app.js")

    page.route("**/static/m.js", lambda route: route.fulfill(status=404, body=""))
    page.goto(server.base + "/m")
    page.wait_for_selector("#guard-note:has-text('did not start')")
    page.unroute("**/static/m.js")

    page.goto(server.base + "/")
    page.wait_for_selector("#tab-import.active")
    assert not page.is_visible("#guard-note")
    page.evaluate("setTimeout(() => { throw new Error('after the start'); })")
    page.wait_for_selector("#guard-note:has-text('Something went wrong') #guard-dismiss")
    page.click("#guard-dismiss")
    assert not page.is_visible("#guard-note")
    context.close()

    off = browser.new_context(java_script_enabled=False)
    page = off.new_page()
    for path, words in (("/", "randostats needs JavaScript"), ("/m", "Counterpoint needs JavaScript")):
        page.goto(server.base + path)
        assert page.is_visible(".guard-note"), path
        assert words in page.inner_text(".guard-note"), path
    off.close()


def test_a_browser_that_keeps_no_site_data_still_starts_both_pages(browser, server):
    """A browser told to keep no site data throws on any localStorage access. The
    phone page reads one there as it starts (whether hands-free worked before); it
    has to start anyway, and the safety net must not report a failure over
    controls that work."""
    watch = Watch(server.base)
    context = new_context(browser)
    context.add_init_script("Object.defineProperty(window, 'localStorage', "
                            "{get() { throw new DOMException('The operation is insecure.', 'SecurityError'); }});")
    page = context.new_page()
    watch.attach(page)
    for path, ready in (("/m", "#examples .chip"), ("/", "#tab-import.active")):
        page.goto(server.base + path)
        page.wait_for_selector(ready)
        page.wait_for_timeout(300)
        assert not page.is_visible("#guard-note"), path
    page.goto(server.base + "/m")
    page.click("#examples .chip >> nth=0")
    page.wait_for_selector("#cards article .punch")
    watch.check(page)
    context.close()


def test_the_not_found_page_and_the_files_that_are_not_the_site(browser, server):
    watch = Watch(server.base, expected={(404, "/no/such/page")})
    context = new_context(browser)
    page = context.new_page()
    watch.attach(page)
    response = page.goto(server.base + "/no/such/page")
    assert response.status == 404
    assert page.inner_text("h1") == "That page isn’t here"
    assert page.evaluate("getComputedStyle(document.querySelector('.site-card')).borderRadius") != "0px", \
        "the app's stylesheet applies"
    assert page.evaluate("document.scripts.length") == 0
    page.click("a.button.primary")
    page.wait_for_selector("#tab-import.active")
    watch.check(page)
    context.close()

    # The repository around the package is not the site, and neither is anything
    # under it but static/ and the routes. Raw paths, as a client that does not
    # normalise them sends them.
    host = urlparse(server.base).netloc
    for path in ("/README.md", "/pyproject.toml", "/.git/config", "/.git/HEAD", "/.env",
                 "/randostats/api.py", "/api.py", "/tests/test_api.py", "/samples/make_sample.py",
                 "/samples/sample_whatsapp_alex.txt", "/samples/", "/static/", "/static/../api.py",
                 "/static/%2e%2e/api.py", "/static/..%2fapi.py", "/static/%2e%2e/%2e%2e/pyproject.toml",
                 "/docs", "/openapi.json", "/redoc"):
        connection = http.client.HTTPConnection(host, timeout=10)
        connection.request("GET", path, headers={"Accept": "text/html"})
        answer = connection.getresponse()
        body = answer.read().decode("utf-8", "replace")
        connection.close()
        assert answer.status == 404, (path, answer.status)
        for leak in ("def create_app", "[project]", "[core]", "ref: refs/", "import ", "# randostats\n"):
            assert leak not in body, (path, leak)
        assert answer.getheader("Content-Security-Policy") == api.CSP, path


def test_a_proxy_on_this_machine_makes_it_https(server):
    """Behind a TLS terminator on this machine, the request is https: uvicorn believes
    X-Forwarded-Proto from 127.0.0.1 and ::1, so the policy upgrades and HSTS is
    sent. Without the header, neither is."""
    host = urlparse(server.base).netloc
    for forwarded, csp, hsts in ((None, api.CSP, None), ("https", api.CSP_HTTPS, api.HSTS)):
        connection = http.client.HTTPConnection(host, timeout=10)
        connection.request("GET", "/robots.txt", headers={"X-Forwarded-Proto": forwarded} if forwarded else {})
        answer = connection.getresponse()
        answer.read()
        connection.close()
        assert answer.status == 200
        assert answer.getheader("Content-Security-Policy") == csp, forwarded
        assert answer.getheader("Strict-Transport-Security") == hsts, forwarded


def test_signing_in(browser, gated):
    """With RANDOSTATS_PASSWORD set: every page asks for it, the right one comes
    back to the page that asked, the wrong one says so, a next= that names
    another site goes nowhere, and Sign out ends the session."""
    watch = Watch(gated.base, expected={(401, "/api/login")})
    context = new_context(browser)
    page = context.new_page()
    watch.attach(page)

    page.goto(gated.base + "/m")
    assert page.url == gated.base + "/login?next=%2Fm"
    page.wait_for_selector("#sign-in")
    assert not page.is_visible("#guard-note")
    page.fill("#password", "not the password at all")
    page.click("#sign-in button")
    page.wait_for_selector("#sign-in-result:has-text('not the password')")
    page.fill("#password", PASSWORD)
    page.click("#sign-in button")
    page.wait_for_url(gated.base + "/m")
    page.wait_for_selector("#examples .chip")

    cookie = next(c for c in context.cookies() if c["name"] == "randostats_session")
    assert cookie["httpOnly"] and cookie["sameSite"] == "Lax" and cookie["path"] == "/"
    assert not cookie["secure"], "plain http on loopback: a Secure cookie would never come back"

    page.goto(gated.base + "/")
    page.wait_for_selector("#tab-import.active")
    page.wait_for_selector("#sign-out:not([hidden])")
    page.click("#sign-out")
    page.wait_for_url(gated.base + "/login")
    assert not [c for c in context.cookies() if c["name"] == "randostats_session"]
    page.goto(gated.base + "/")
    assert page.url.startswith(gated.base + "/login?next=")

    # A sign-in link that names another site comes back to this one.
    page.goto(gated.base + "/login?next=//example.com/stolen")
    page.fill("#password", PASSWORD)
    page.click("#sign-in button")
    page.wait_for_url(gated.base + "/")
    page.wait_for_selector("#tab-import.active")
    watch.check(page)
    context.close()
