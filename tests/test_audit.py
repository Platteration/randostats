"""What the audit job in .github/workflows/ci.yml installs and audits, beyond the step
lines tests/test_conventions.py pins. Offline: whether a hash is right is pip's to say
at install time, and whether a version has an advisory, or the floors install
together, is the job's."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / ".github" / "audit"


def _load_floors():
    spec = importlib.util.spec_from_file_location("audit_floors", AUDIT / "floors.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


floors_py = _load_floors()

# The first release clear of every advisory pip-audit 2.10.1 reported against the
# floors pyproject.toml declared until 2026-09-23 (python-multipart 0.0.9, starlette
# 0.36.3 through fastapi 0.110, and pydantic 2.0.2, the oldest 2.x that fastapi
# accepted): seven for python-multipart, fixed by 0.0.31; seven for starlette, by
# 1.3.1; PYSEC-2026-1812 for pydantic, by 2.4.0.
FIRST_FIXED = {"python-multipart": (0, 0, 31), "starlette": (1, 3, 1), "pydantic": (2, 4, 0)}

SAMPLE = """[project]
name = "sample"
dependencies = [
    "a>=1.2",
    "b[extra]>=0.0.9,<2",
]

[project.optional-dependencies]
llm = ["c>=3"]
dev = ["pytest>=8", "ruff"]

[tool.sample]
"""


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


def test_the_floors_are_what_the_app_is_built_from():
    """Every [project] dependency and the llm extra, in order, and not the dev extra,
    which is the test runner and the linter. Run as CI runs it."""
    run = subprocess.run([sys.executable, str(AUDIT / "floors.py")], capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 0, run.stderr
    pins = run.stdout.split()
    assert pins == floors_py.floors((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert [pin.split("==")[0] for pin in pins] == [
        "fastapi", "starlette", "uvicorn[standard]", "python-multipart", "pyspellchecker", "pydantic", "anthropic",
    ]


def test_no_floor_admits_a_release_with_a_known_advisory():
    pins = dict(pin.split("==") for pin in floors_py.floors((ROOT / "pyproject.toml").read_text(encoding="utf-8")))
    for name, fixed in FIRST_FIXED.items():
        floor = tuple(int(part) for part in pins[name].split("."))
        assert floor + (0,) * (3 - len(floor)) >= fixed, f"{name}>={pins[name]} admits a release with an advisory"


def test_floors_pins_each_form_it_reads():
    assert floors_py.floors(SAMPLE) == ["a==1.2", "b[extra]==0.0.9", "c==3"]


@pytest.mark.parametrize("broken", [
    SAMPLE.replace('"a>=1.2"', '"a"'),                                  # no floor to pin
    SAMPLE.replace('"a>=1.2"', '"a~=1.2"'),
    SAMPLE.replace('"a>=1.2"', '"a>=1.2; python_version < \'3.11\'"'),
    SAMPLE.replace('llm = ["c>=3"]', 'llm = ["c"]'),                     # the llm extra is audited too
    SAMPLE.replace('    "a>=1.2",\n', '    # a comment\n    "a>=1.2",\n'),  # the array no longer reads
    SAMPLE.replace('    "a>=1.2",\n    "b[extra]>=0.0.9,<2",\n', ""),  # nothing to audit
    SAMPLE.replace('llm = ["c>=3"]\n', ""),
], ids=["no-floor", "compatible-release", "marker", "llm-no-floor", "comment", "empty", "no-llm-extra"])
def test_floors_refuses_rather_than_leaves_a_requirement_out(broken):
    assert broken != SAMPLE
    with pytest.raises(ValueError):
        floors_py.floors(broken)


def test_a_failing_run_prints_no_list(tmp_path, monkeypatch, capsys):
    """The job hands stdout to pip-audit, which passes an empty list and audits a
    partial one without saying so."""
    pyproject = tmp_path / "pyproject.toml"
    monkeypatch.setattr(floors_py, "PYPROJECT", pyproject)
    pyproject.write_text(SAMPLE.replace('"b[extra]>=0.0.9,<2"', '"b[extra]"'))
    assert floors_py.main() == 1
    out, err = capsys.readouterr()
    assert out == "" and "b[extra]" in err
    pyproject.write_text(SAMPLE)
    assert floors_py.main() == 0
    assert capsys.readouterr().out == "a==1.2\nb[extra]==0.0.9\nc==3\n"
