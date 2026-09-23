"""What the audit job in .github/workflows/ci.yml installs and audits, beyond the step
lines tests/test_conventions.py pins. Standard library only, and offline: whether a
hash is right is pip's to say at install time, and whether a version has an advisory
is the job's."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / ".github" / "audit"


def pinned(text: str) -> dict[str, tuple[str, list[str]]]:
    """name -> (version, hashes) for each requirement of a pip-compile file. Anything
    that is not `name==version` followed by its hashes fails here."""
    entries: dict[str, tuple[str, list[str]]] = {}
    for logical in text.replace("\\\n", " ").split("\n"):
        tokens = logical.split("#", 1)[0].split()
        if not tokens:
            continue
        requirement = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\d[^\s;]*)", tokens[0])
        assert requirement, f"{tokens[0]!r} is pinned with == and nothing else"
        hashes = [t for t in tokens[1:] if re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", t)]
        assert hashes and len(hashes) == len(tokens) - 1, f"{tokens[0]} carries its hashes and nothing else"
        name = requirement[1].lower().replace("_", "-").replace(".", "-")
        assert name not in entries, f"{name} is listed once"
        entries[name] = (requirement[2], hashes)
    return entries


def test_pip_audit_and_everything_it_runs_on_are_pinned_with_hashes():
    """`pip install --require-hashes` refuses a file with an unhashed line, but only in
    CI; this says so before a push. pip is in the file too (--allow-unsafe), since
    pip-audit runs on it."""
    wanted = re.findall(r"^pip-audit==(\d+\.\d+\.\d+)$", (AUDIT / "requirements.in").read_text(), re.M)
    assert len(wanted) == 1, "requirements.in pins pip-audit, once"
    requirements = (AUDIT / "requirements.txt").read_text()
    assert "pip-compile --allow-unsafe --generate-hashes --strip-extras requirements.in" in requirements
    entries = pinned(requirements)
    assert entries["pip-audit"][0] == wanted[0], "requirements.txt was compiled from requirements.in"
    assert "pip" in entries, "pip is pinned as well"


def test_the_pinned_parser_refuses_what_is_not_pinned():
    good = "a==1.0 \\\n    --hash=sha256:" + "0" * 64 + "\n    # via b\n"
    assert pinned(good) == {"a": ("1.0", ["--hash=sha256:" + "0" * 64])}
    for bad in ("a==1.0\n", "a>=1.0 \\\n    --hash=sha256:" + "0" * 64 + "\n",
                "--index-url https://example.invalid/simple\n" + good,
                good + good):
        try:
            pinned(bad)
        except AssertionError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_dependabot_moves_the_audit_pins():
    """Neither the root pip entry nor the github-actions one reads a `run:` line."""
    dependabot = (ROOT / ".github" / "dependabot.yml").read_text()
    assert re.search(r"^  - package-ecosystem: pip\n    directory: /\.github/audit$", dependabot, re.M)
