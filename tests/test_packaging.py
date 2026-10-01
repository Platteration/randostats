"""The data files the app reads at run time have to be in the built package.

Nothing else notices when they are not: `pip install -e .` (what the README
and CI use) reads them straight out of the checkout, so a glob in pyproject
that misses a directory only shows up as a crash on somebody's wheel install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "randostats"
# Everything loaded from disk while the app runs: the front end, the core
# facts, and every fact pack and punchline voice.
RUNTIME_DATA = ("static/*", "counterpoint/*.json", "counterpoint/packs/*.json", "counterpoint/voices/*.json")


def package_data_patterns() -> list[str]:
    tomllib = pytest.importorskip("tomllib", reason="reading pyproject.toml needs Python 3.11+")
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["setuptools"]["package-data"]["randostats"]


def test_every_runtime_data_file_is_covered_by_a_package_data_glob():
    """`counterpoint/*.json` does not descend into `counterpoint/packs/`, and
    there is no MANIFEST.in, so the packs and voices were left out of the
    wheel. The engine then raised "no punchline voices found" at import time
    and `randostats serve` died before it printed anything."""
    needed = {p.relative_to(PKG) for pattern in RUNTIME_DATA for p in PKG.glob(pattern) if p.is_file()}
    assert needed, "no data files found at all; this test is checking nothing"
    # Same globbing setuptools does, relative to the package directory.
    shipped = {p.relative_to(PKG) for pattern in package_data_patterns() for p in PKG.glob(pattern) if p.is_file()}
    missing = sorted(str(p) for p in needed - shipped)
    assert not missing, f"read at run time but not packaged, so a non-editable install would crash: {missing}"


def test_the_packs_and_voices_that_have_to_ship_are_really_there():
    """Keeps the check above honest: an empty directory would satisfy it."""
    from randostats.counterpoint import packs

    assert list(packs.PACK_DIR.glob("*.json")), "no fact packs on disk"
    assert list(packs.VOICE_DIR.glob("*.json")), "no punchline voices on disk"
    assert packs.DEFAULT_VOICE in {v["id"] for v in packs.list_voices()}
