"""Print what the app is built from pinned at its declared floors, for the audit job.

`pip-audit -r <(echo ".[llm]")` audits the newest versions the ranges in
pyproject.toml resolve to on the day, which says nothing about the floors: an
environment that already holds a floor keeps it through `pip install .`. So the
audit job also audits this: every [project] dependency and every `llm` extra
requirement pinned with `==` at its `>=` floor, one per line, for `pip-audit -r`
to resolve everything else around. A version between the floor and the newest
release is audited by neither.

A requirement with no floor, or in a form this does not read, is an error rather
than a line left out, and so is an empty list: an audit of nothing passes.
Standard library only, and no tomllib, because the suite that tests this still
runs on 3.10.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

# The extras the app is built from. `dev` is the test runner and the linter.
EXTRAS = ("llm",)

# name, optional [extras], a `>=` floor, and at most one `<` ceiling after it.
REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?)"
    r">=(?P<floor>\d+(?:\.\d+)*)(?:,<\d+(?:\.\d+)*)?$"
)


def _table(text: str, name: str) -> str:
    match = re.search(rf"^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not match:
        raise ValueError(f"pyproject.toml has no [{name}] table")
    return match.group(1)


def _array(table: str, key: str) -> list[str]:
    """The strings of one `key = [...]` array, which may span lines. A comment or
    anything but quoted strings inside it fails the match, and so the run."""
    match = re.search(rf'^{re.escape(key)} = \[((?:\s*"[^"\n]*"\s*,?)*)\s*\]$', table, re.M)
    if not match:
        raise ValueError(f"no readable `{key} = [...]` array")
    return re.findall(r'"([^"\n]*)"', match.group(1))


def floors(text: str, extras: tuple[str, ...] = EXTRAS) -> list[str]:
    requirements = _array(_table(text, "project"), "dependencies")
    if not requirements:
        raise ValueError("[project] declares no dependencies")
    optional = _table(text, "project.optional-dependencies")
    for extra in extras:
        requirements += _array(optional, extra)
    pinned = []
    for requirement in requirements:
        match = REQUIREMENT.match(requirement)
        if not match:
            raise ValueError(f"{requirement!r} does not declare a floor this can read")
        pinned.append(f"{match['name']}=={match['floor']}")
    return pinned


def main() -> int:
    try:
        pinned = floors(PYPROJECT.read_text(encoding="utf-8"))
    except ValueError as err:
        print(f"floors.py: {err}", file=sys.stderr)
        return 1
    print("\n".join(pinned))
    return 0


if __name__ == "__main__":
    sys.exit(main())
