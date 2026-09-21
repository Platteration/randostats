"""The conventions shared by every platteration repository (see CONVENTIONS.md), pinned
so that a session cannot quietly re-decide them: the Python counterpart of the npm
repositories' test/conventions.mjs, with the same two hashes. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Update these when the shared file changes — in every repository, in one pass.
EDITORCONFIG_SHA = "85bccbd23a9070becfe1dc0dbb9ad7305fb2bb98f92f54cb9856d7d6eca4ebfe"
CONVENTIONS_SHA = "4e87fb42960f7716e9538c486a28984aa05e00f56894629da50a7a0940d23988"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def has(path: str) -> bool:
    return (ROOT / path).exists()


def sha(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def table(name: str) -> str:
    """The raw text of one pyproject.toml table. CI still tests 3.10, which has no
    tomllib, and every key pinned here is one line."""
    match = re.search(rf"^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", read("pyproject.toml"), re.M | re.S)
    assert match, f"pyproject.toml has a [{name}] table"
    return match.group(1)


def line(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.M) is not None


def test_editorconfig_and_conventions_are_the_shared_copies():
    assert sha(".editorconfig") == EDITORCONFIG_SHA, ".editorconfig differs from the shared copy"
    assert sha("CONVENTIONS.md") == CONVENTIONS_SHA, "CONVENTIONS.md differs from the shared copy"


def test_python_is_pinned_once_and_pyproject_agrees():
    assert read(".python-version").strip() == "3.12"
    assert line(table("project"), r'^requires-python = ">=3\.10"$'), 'requires-python is ">=3.10"'
    assert line(table("tool.ruff"), r'^target-version = "py310"$'), "ruff targets the same floor"


def test_the_check_set():
    """ruff and pytest are what CI runs, so the dev extra installs both."""
    dev = re.search(r"^dev = \[(.*?)\]$", table("project.optional-dependencies"), re.M)
    assert dev, "a dev extra"
    for tool in ("pytest", "ruff"):
        assert re.search(rf'"{tool}[">=<~]', dev.group(1)), f"the dev extra installs {tool}"
    assert line(table("tool.pytest.ini_options"), r'^testpaths = \["tests"\]$')


def test_the_ci_workflow_shape():
    assert has(".github/workflows/ci.yml"), ".github/workflows/ci.yml exists"
    ci = read(".github/workflows/ci.yml")
    assert line(ci, r"^name: CI$")
    assert "workflow_dispatch" in ci
    assert line(ci, r"^concurrency:")
    assert "timeout-minutes:" in ci
    assert "python-version-file: .python-version" in ci
    assert line(ci, r"^permissions:\n\s+contents: read")
    assert not re.search(r"uses: [^@\n]+@v\d", ci), "actions are pinned to a commit SHA, not a tag"
    assert '"3.10"' in ci and '"3.12"' in ci, "CI tests 3.10 and 3.12"
    assert line(ci, r"^\s+- run: ruff check \.$"), "CI runs ruff"
    assert line(ci, r"^\s+- run: pytest\b"), "CI runs pytest"
    assert ci.index("run: ruff check .") < ci.index("run: pytest"), "lint before the suite"


def test_the_documents():
    for f in ("README.md", "LICENSE", "SECURITY.md", "CLAUDE.md", "CONVENTIONS.md", ".gitignore", ".github/dependabot.yml"):
        assert has(f), f"{f} exists"
    assert read("LICENSE").split("\n")[0].strip() == "MIT License"
    project = table("project")
    assert line(project, r'^license = "MIT"$')
    assert line(project, r'^license-files = \["LICENSE"\]$')
    setuptools = re.search(r"setuptools>=(\d+)", table("build-system"))
    assert setuptools and int(setuptools.group(1)) >= 77, "the PEP 639 license fields need setuptools 77"
    assert line(read("CLAUDE.md"), r"^## Conventions$"), "CLAUDE.md has a Conventions section"
    dependabot = read(".github/dependabot.yml")
    assert "package-ecosystem: pip" in dependabot
    assert "package-ecosystem: github-actions" in dependabot
    assert line(dependabot, r"^\s+groups:")


def test_gitignore_ends_with_the_secrets_block():
    tail = "\n".join(read(".gitignore").rstrip().split("\n")[-3:])
    assert tail == "# Secrets: ignore every .env variant, but keep the documented template.\n.env*\n!.env.example"
    assert line(read(".gitignore"), r"^\.claude/settings\.local\.json$")


def test_the_claude_session_hook():
    settings = json.loads(read(".claude/settings.json"))
    assert settings.get("hooks", {}).get("SessionStart"), "SessionStart hook declared"
    assert has(".claude/hooks/session-start.sh")
