"""CI's browser install, asked of the pinned Playwright itself.

The check job installs Chromium with `python -m playwright install --with-deps
chromium` on the runner it names, and Playwright has a Chromium build for some Ubuntu
releases and not others: Playwright 1.56.0, the e2e extra's pin, has none for Ubuntu
26.04, which `ubuntu-latest` becomes from 2026-10-19, so the install would fail there.
So every job names its release, the one image the shared conventions name (RUNNER in
tests/test_conventions.py, moved in every repository at once), and this asks the
installed Playwright, told the check job's platform (PLAYWRIGHT_HOST_PLATFORM_OVERRIDE),
whether it would download a Chromium there: the question CONVENTIONS.md has each
repository ask before the runner moves. Here rather than in tests/, because it needs the
e2e extra, which CI installs for this step.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from importlib.metadata import version

from conftest import REPO

CI = REPO / ".github" / "workflows" / "ci.yml"


def _jobs(text: str) -> dict[str, list[str]]:
    """Each job of the workflow, by name, with its lines."""
    jobs: dict[str, list[str]] = {}
    current = None
    within = False
    for line in text.splitlines():
        if not line.startswith(" ") and line.strip():
            within = line.rstrip() == "jobs:"
            current = None
            continue
        named = re.fullmatch(r"  ([\w-]+):\s*", line) if within else None
        if named:
            current = jobs.setdefault(named.group(1), [])
        elif current is not None:
            current.append(line)
    return jobs


def _chromium_download(release: str) -> str | None:
    """Where the installed Playwright would download Chromium from on that Ubuntu
    release, or None when it has no build for it."""
    env = {**os.environ, "PLAYWRIGHT_HOST_PLATFORM_OVERRIDE": f"ubuntu{release}-x64"}
    out = subprocess.run([sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"], env=env,
                         capture_output=True, text=True, timeout=120, check=True).stdout
    first = out.split("browser: ")[1]
    assert first.startswith("chromium version"), out
    found = re.search(r"Download url:\s+(\S+)", first)
    return found.group(1) if found else None


def test_the_installed_playwright_is_the_pinned_one():
    pin = re.search(r'e2e = \["playwright==([\w.]+)"\]', (REPO / "pyproject.toml").read_text())
    assert pin and version("playwright") == pin.group(1), "the walk runs on the Playwright pyproject.toml pins"


def test_the_job_that_installs_the_browser_names_a_release_playwright_has_one_for():
    jobs = _jobs(CI.read_text())
    assert set(jobs) == {"check", "audit", "packaged"}, sorted(jobs)
    installing = [name for name, lines in jobs.items()
                  if any(re.match(r"\s+- run: .*playwright install", line) for line in lines)]
    assert installing == ["check"], installing
    runs_on = [m.group(1) for m in (re.fullmatch(r"    runs-on:\s*(\S+)\s*", line) for line in jobs["check"]) if m]
    assert len(runs_on) == 1, runs_on
    release = re.fullmatch(r"ubuntu-(\d\d\.\d\d)", runs_on[0])
    assert release, f"the check job names an Ubuntu release, not one that moves under it: {runs_on[0]}"
    assert _chromium_download(release.group(1)), \
        f"Playwright {version('playwright')} has no Chromium build for Ubuntu {release.group(1)}"


def test_the_reason_ci_yml_gives_for_naming_the_release_holds():
    """ci.yml says why the check job does not run on ubuntu-latest; while that is true,
    it names this Playwright. Once a raised Playwright has a build for 26.04, this fails
    and the comment changes with it; the runner itself moves with the conventions test's
    RUNNER, in every job of every repository at once."""
    assert f"Playwright {version('playwright')} has no Chromium build for 26.04" in CI.read_text()
    assert _chromium_download("26.04") is None
