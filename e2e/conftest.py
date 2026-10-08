"""The website walk's harness: `randostats serve` started for real, on a free port
of this machine, and a Chromium that records everything a policy problem looks like.

Run with `pytest -q e2e` after `pip install -e ".[e2e]"` and, once,
`python -m playwright install chromium`. It is not part of `pytest -q`, which reads
`tests/` only: a browser is not a unit test. CI runs it in the check job.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from randostats import api

REPO = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery staple"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Server:
    base: str
    process: subprocess.Popen
    log: Path

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


def start(tmp_path: Path, password: str | None = None) -> Server:
    """`randostats serve` on loopback, with a database of its own."""
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RANDOSTATS_")}
    if password:
        env["RANDOSTATS_PASSWORD"] = password
    log = tmp_path / f"server-{port}.log"
    with log.open("wb") as out:
        process = subprocess.Popen(
            [sys.executable, "-m", "randostats.cli", "--db", str(tmp_path / f"e2e-{port}.db"),
             "serve", "--port", str(port)],
            cwd=REPO, env=env, stdout=out, stderr=subprocess.STDOUT)
    server = Server(f"http://127.0.0.1:{port}", process, log)
    deadline = time.monotonic() + 30
    while True:
        try:
            # Public whether or not a password is set.
            urllib.request.urlopen(server.base + "/robots.txt", timeout=2)
            return server
        except (urllib.error.URLError, ConnectionError, OSError):
            if process.poll() is not None or time.monotonic() > deadline:
                server.stop()
                raise RuntimeError(f"the server did not start:\n{log.read_text(errors='replace')}")
            time.sleep(0.1)


# Every securitypolicyviolation the page sees, kept where the test can read it.
RECORD_VIOLATIONS = """
window.__violations = [];
document.addEventListener("securitypolicyviolation", (e) => {
  window.__violations.push({directive: e.violatedDirective, blocked: e.blockedURI, source: e.sourceFile, line: e.lineNumber});
});
"""


@dataclass
class Watch:
    """What a page did that the website must never do.

    Fails on any CSP violation (the event and the console line Chromium writes
    for it), any page error or console error, any request that left this
    origin, any request that failed, any response of this origin whose
    headers are not the policy, and any status of 400 or more the test did not
    ask for by name.
    """

    base: str
    expected: set = field(default_factory=set)  # (status, path) pairs a test asks for
    problems: list = field(default_factory=list)
    responses: int = 0

    def attach(self, page) -> None:
        page.on("console", self._console)
        page.on("pageerror", lambda error: self.problems.append(("pageerror", str(error))))
        page.on("request", self._request)
        page.on("requestfailed", self._failed)
        page.on("response", self._response)

    def _path(self, url: str) -> str:
        return url[len(self.base):].split("?", 1)[0].split("#", 1)[0] or "/"

    def _console(self, message) -> None:
        text = message.text
        if message.type == "error" and text.startswith("Failed to load resource"):
            # Chromium's own line for a status the response check below judges.
            url = (message.location or {}).get("url", "")
            if url.startswith(self.base) and any(path == self._path(url) for _, path in self.expected):
                return
        if message.type == "error" or "Content Security Policy" in text or "Permissions-Policy" in text:
            self.problems.append((f"console.{message.type}", text))

    def _request(self, request) -> None:
        url = request.url
        if not url.startswith((self.base + "/", "blob:", "data:")):
            self.problems.append(("request outside the site", url))

    def _failed(self, request) -> None:
        failure = request.failure or ""
        # A download navigates to a blob: and is aborted once saved; nothing failed.
        if request.url.startswith("blob:") and "ERR_ABORTED" in failure:
            return
        self.problems.append(("request failed", request.url, failure))

    def _response(self, response) -> None:
        url = response.url
        if not url.startswith(self.base + "/"):
            return
        self.responses += 1
        path = self._path(url)
        if response.status >= 400 and (response.status, path) not in self.expected:
            self.problems.append(("status", response.status, url))
        headers = response.headers
        want = {"content-security-policy": api.CSP, **{k.lower(): v for k, v in api.HEADERS.items()}}
        for name, value in want.items():
            if headers.get(name) != value:
                self.problems.append(("header", name, headers.get(name), url))
        if "strict-transport-security" in headers:
            self.problems.append(("HSTS over plain http", url))
        cache = headers.get("cache-control")
        if cache != ("no-store" if path.startswith("/api/") else "no-cache"):
            self.problems.append(("cache-control", cache, url))

    def drain(self, page) -> None:
        for violation in page.evaluate("window.__violations.splice(0)"):
            self.problems.append(("csp violation", violation))

    def check(self, *pages) -> None:
        for page in pages:
            if not page.is_closed():
                self.drain(page)
        assert self.responses, "the walk saw no response from the server at all"
        assert not self.problems, "\n".join(map(str, self.problems))


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        yield chromium
        chromium.close()


@pytest.fixture
def server(tmp_path):
    running = start(tmp_path)
    yield running
    running.stop()


@pytest.fixture
def gated(tmp_path):
    running = start(tmp_path, PASSWORD)
    yield running
    running.stop()


def new_context(browser, **options):
    context = browser.new_context(accept_downloads=True, **options)
    context.add_init_script(RECORD_VIOLATIONS)
    return context
