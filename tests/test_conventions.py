"""The conventions shared by every platteration repository (see CONVENTIONS.md), pinned
so that a session cannot quietly re-decide them: the Python counterpart of the npm
repositories' test/conventions.mjs, with the same hashes and the same reading of the
workflows. A check that only this repository needs goes in its own tests; the audit
job's own steps are pinned here because this is the one Python repository
(CONVENTIONS.md). Standard library only, and it runs without pytest as well
(`python tests/test_conventions.py`), which is how CI runs it a second time: a pytest
setting such as `addopts = "--collect-only"` collects this file and runs none of it,
and no check inside it can see that from under pytest.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Update these when a shared text changes — in every repository, in one pass.
EDITORCONFIG_SHA = "85bccbd23a9070becfe1dc0dbb9ad7305fb2bb98f92f54cb9856d7d6eca4ebfe"
CONVENTIONS_SHA = "6afaa593e5df37dfdfa0b4a53dcc52d8aa59cf9636297ab457551cc7677968fe"
REVIEW_STATUS_SHA = "5a22e3f5833fa7576edd0ca0b11c8da94e5bcae33a5d13bad6b845c8edb78989"
# CI's weekly run (CONVENTIONS.md, "CI"). Not at the top of the hour, which GitHub's
# documentation names as a high-load time, when a scheduled run can be delayed or dropped.
SCHEDULE = "17 6 * * 1"
CI = ".github/workflows/ci.yml"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def has(path: str) -> bool:
    return (ROOT / path).exists()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tables() -> list[str]:
    return re.findall(r"^\[([^\]\n]+)\]\s*$", read("pyproject.toml"), re.M)


def table(name: str) -> str:
    """The raw text of one pyproject.toml table. CI still tests 3.10, which has no
    tomllib, and every key pinned here is one line."""
    match = re.search(rf"^\[{re.escape(name)}\]\n(.*?)(?=^\[|\Z)", read("pyproject.toml"), re.M | re.S)
    assert match, f"pyproject.toml has a [{name}] table"
    return match.group(1)


def keys_of(name: str) -> list[str]:
    """The keys one pyproject.toml table sets, in order."""
    return re.findall(r'^([\w.-]+|"[^"\n]*")\s*=', table(name), re.M)


def line(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.M) is not None


def review_status(review: str) -> str:
    """REVIEW.md's shared block: from the line `### Status of the shared items` up to,
    not including, the line `### A hardened workflow to copy`, each of which it has once."""
    lines = review.split("\n")
    starts = [i for i, text in enumerate(lines) if text.startswith("### Status of the shared items")]
    ends = [i for i, text in enumerate(lines) if text == "### A hardened workflow to copy"]
    assert len(starts) == 1 and len(ends) == 1 and starts[0] < ends[0], (
        'REVIEW.md has the shared status block once, before "### A hardened workflow to copy"'
    )
    return "\n".join(lines[starts[0] : ends[0]]) + "\n"


def test_editorconfig_conventions_and_review_status_are_the_shared_copies():
    assert sha((ROOT / ".editorconfig").read_bytes()) == EDITORCONFIG_SHA, ".editorconfig differs from the shared copy"
    assert sha((ROOT / "CONVENTIONS.md").read_bytes()) == CONVENTIONS_SHA, "CONVENTIONS.md differs from the shared copy"
    assert sha(review_status(read("REVIEW.md")).encode("utf-8")) == REVIEW_STATUS_SHA, (
        'the "Status of the shared items" block of REVIEW.md differs from the shared copy'
    )


def test_python_is_pinned_once_and_pyproject_agrees():
    assert read(".python-version").strip() == "3.12"
    assert line(table("project"), r'^requires-python = ">=3\.10"$'), 'requires-python is ">=3.10"'
    assert line(table("tool.ruff"), r'^target-version = "py310"$'), "ruff targets the same floor"


def test_the_check_set():
    """ruff and pytest are what CI runs, so the dev extra installs both, and nothing but
    pyproject.toml configures them: a setting that narrows what either reads makes the
    step pass having checked less, as dropping "F" from the rule set would."""
    dev = re.search(r"^dev = \[(.*?)\]$", table("project.optional-dependencies"), re.M)
    assert dev, "a dev extra"
    for tool in ("pytest", "ruff"):
        assert re.search(rf'"{tool}[">=<~]', dev.group(1)), f"the dev extra installs {tool}"
    assert line(table("tool.pytest.ini_options"), r'^testpaths = \["tests"\]$')
    # `addopts = "--collect-only"` makes `pytest -q` run nothing, and pass; `python_files`
    # or an `--ignore` can leave this file out. Only testpaths, then.
    assert keys_of("tool.pytest.ini_options") == ["testpaths"], "pytest's configuration is testpaths and nothing else"
    # Each of these is read before, or instead of, pyproject.toml.
    for f in ("pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "ruff.toml", ".ruff.toml"):
        assert not has(f), f"no {f}: pyproject.toml is the one configuration"
    # The rule set is spelled out because the pip ruff enables some 400 rules when
    # none is selected. Pinned here as well: without this, dropping "F" from the list
    # lets pyflakes stop gating CI while `ruff check .` stays green; and an exclude, an
    # ignore or a per-file ignore of a whole rule family does the same.
    assert line(table("tool.ruff.lint"), r'^select = \["E4", "E7", "E9", "F"\]$'), "the pinned rule set"
    ruff = [t for t in tables() if t == "tool.ruff" or t.startswith("tool.ruff.")]
    assert set(ruff) <= {"tool.ruff", "tool.ruff.lint", "tool.ruff.lint.per-file-ignores"}, f"no ruff table but those three: {ruff}"
    assert set(keys_of("tool.ruff")) <= {"target-version", "line-length"}, "[tool.ruff] sets the target and the line length, nothing that narrows what is checked"
    assert keys_of("tool.ruff.lint") == ["select"], "[tool.ruff.lint] sets the rule set, nothing that narrows it"
    if "tool.ruff.lint.per-file-ignores" in ruff:
        for entry in [x for x in table("tool.ruff.lint.per-file-ignores").split("\n") if x.strip() and not x.lstrip().startswith("#")]:
            assert re.fullmatch(r'"[\w./-]+" = \["[A-Z]+\d+"(, "[A-Z]+\d+")*\]', entry), (
                f"a per-file ignore names one file and whole rule codes, one line each: {entry}"
            )


# Enough YAML for the workflows here: the block YAML they are written in, and nothing else.


KEY = re.compile(r"[A-Za-z_][\w.-]*")


def parse_workflow(text: str, file: str) -> tuple[dict, list[str], list[tuple[str, int]]]:
    """A workflow file read as the block YAML these files are written in, and nothing
    else: block mappings with plain keys, block lists indented under their key, scalars
    (plain, quoted, or a `|`/`>` block) and one-line flow lists of scalars. Every other
    spelling YAML allows (a flow mapping `{ ... }`, a quoted or complex key, an anchor,
    alias, tag or merge key, a list at its key's own indent, a plain value carried onto
    another line, a tab, a second document) fails with its line: each check reads the
    tree, and a spelling it does not read is one it cannot check. Scalars stay strings,
    and an empty value is None. Returns the tree, the lines, and every key with the
    index of its line. The same reader as test/conventions.mjs's readWorkflow."""
    lines = text.split("\n")
    keys: list[tuple[str, int]] = []

    def fail(n: int, why: str):
        line = lines[n] if n < len(lines) else ""
        raise AssertionError(f"{file}:{n + 1}: {why}, which the conventions test does not read: {json.dumps(line)}")

    def blank(line: str) -> bool:
        return re.fullmatch(r"\s*(#.*)?", line) is not None

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    for n, line in enumerate(lines):
        if "\t" in line:
            fail(n, "a tab")
        if re.match(r"(---|\.\.\.)(\s|$)|%", line):
            fail(n, "a document marker or directive")

    at = 0

    def skip() -> None:
        nonlocal at
        while at < len(lines) and blank(lines[at]):
            at += 1

    def is_item(text: str) -> bool:
        return text == "-" or text.startswith("- ")

    def unescape(body: str, n: int) -> str:
        table = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}

        def one(m: re.Match) -> str:
            if m.group(1) not in table:
                fail(n, f"the escape \\{m.group(1)}")
            return table[m.group(1)]

        return re.sub(r"\\(.)", one, body)

    def trailing(rest: str, n: int, what: str) -> None:
        if not re.fullmatch(r"(\s+#.*)?", rest):
            fail(n, f"text after {what}")

    def scalar(text: str, n: int):
        m = re.fullmatch(r'"((?:[^"\\]|\\.)*)"(.*)', text)
        if m:
            trailing(m.group(2), n, "a quoted value")
            return unescape(m.group(1), n)
        m = re.fullmatch(r"'((?:[^']|'')*)'(.*)", text)
        if m:
            trailing(m.group(2), n, "a quoted value")
            return m.group(1).replace("''", "'")
        if re.match(r"[\"']", text):
            fail(n, "a quoted value that does not close on its line")
        if text.startswith("["):
            return flow(text, n)
        if re.match(r"[{&*!|>%@`,\]}]|[-?:](\s|$)", text):
            fail(n, "a flow mapping, anchor, alias, tag or indicator")
        value = re.sub(r"\s+#.*$", "", text, count=1).rstrip()
        if re.search(r":(\s|$)", value):
            fail(n, "a colon and a space inside a plain value")
        return value

    def flow(text: str, n: int) -> list:
        out: list = []
        rest = text[1:].lstrip()
        if rest.startswith("]"):
            trailing(rest[1:], n, "a list")
            return out
        while True:
            m = (
                re.match(r'"((?:[^"\\]|\\.)*)"', rest)
                or re.match(r"'((?:[^']|'')*)'", rest)
                or re.match(r"([^\s,\[\]{}#'\"&*!|>%@`?:-][^,\[\]{}#]*?)(?=\s*[,\]])", rest)
            )
            if not m or re.search(r":(\s|$)", m.group(1)):
                fail(n, "a list item that is not a plain or quoted value")
            if m.group(0).startswith('"'):
                out.append(unescape(m.group(1), n))
            elif m.group(0).startswith("'"):
                out.append(m.group(1).replace("''", "'"))
            else:
                out.append(m.group(1))
            rest = rest[len(m.group(0)) :].lstrip()
            if rest.startswith(","):
                rest = rest[1:].lstrip()
                continue
            if not rest.startswith("]"):
                fail(n, "a list that does not close on its line")
            trailing(rest[1:], n, "a list")
            return out

    def block(ind: int) -> str:
        nonlocal at
        body: list[str] = []
        content = -1
        while at < len(lines):
            line = lines[at]
            if line.strip() == "":
                body.append("")
                at += 1
                continue
            i = indent(line)
            if content == -1:
                if i <= ind:
                    break
                content = i
            if i < content:
                break
            body.append(line[content:])
            at += 1
        while body and body[-1] == "":
            body.pop()
        return "\n".join(body) + "\n" if body else ""

    def value(rest: str, ind: int, n: int):
        nonlocal at
        at = n + 1
        if rest == "" or rest.startswith("#"):
            skip()
            if at < len(lines) and indent(lines[at]) == ind and is_item(lines[at][ind:]):
                fail(at, "a list at its key's own indent")
            if at < len(lines) and indent(lines[at]) > ind:
                return node(indent(lines[at]))
            return None
        if re.match(r"[|>]", rest):
            if not re.fullmatch(r"[|>][+-]?(\s+#.*)?", rest):
                fail(n, "a block value header")
            return block(ind)
        out = scalar(rest, n)
        skip()
        if at < len(lines) and indent(lines[at]) > ind:
            fail(at, "a value carried onto the next line")
        return out

    def entry(out: dict, text: str, ind: int, n: int) -> None:
        m = re.fullmatch(r"(\S+?):(?:\s+(.*))?", text) or re.fullmatch(r"(\S+?):", text)
        if not m or not KEY.fullmatch(m.group(1)):
            fail(n, "a key that is not a plain name")
        if m.group(1) in out:
            fail(n, f"the key {m.group(1)} twice")
        keys.append((m.group(1), n))
        out[m.group(1)] = value((m.group(2) or "").strip(), ind, n)

    def mapping(ind: int, out: dict | None = None) -> dict:
        out = {} if out is None else out
        while True:
            skip()
            if at >= len(lines) or indent(lines[at]) < ind:
                return out
            if indent(lines[at]) > ind:
                fail(at, "a line indented under nothing")
            if is_item(lines[at][ind:]):
                fail(at, "a list item among keys")
            entry(out, lines[at][ind:], ind, at)

    def sequence(ind: int) -> list:
        out: list = []
        while True:
            skip()
            if at >= len(lines) or indent(lines[at]) < ind:
                return out
            if indent(lines[at]) > ind:
                fail(at, "a line indented under nothing")
            text = lines[at][ind:]
            if not is_item(text):
                fail(at, "a key among list items")
            item = text[2:].strip()
            n = at
            if item == "" or item.startswith("#"):
                fail(n, "a list item on the lines below its dash")
            if re.match(r"[^\s\"'\[{]\S*?:(\s|$)", item):
                first: dict = {}
                entry(first, item, ind + 2, n)
                out.append(mapping(ind + 2, first))
            else:
                out.append(value(item, ind, n))

    def node(ind: int):
        return sequence(ind) if is_item(lines[at][ind:]) else mapping(ind)

    skip()
    if at < len(lines) and indent(lines[at]) != 0:
        fail(at, "a document that does not start at the margin")
    tree = mapping(0)
    skip()
    if at < len(lines):
        fail(at, "a line after the document")
    return tree, lines, keys


def is_mapping(x) -> bool:
    return isinstance(x, dict)


def jobs_of(tree: dict) -> dict:
    return tree["jobs"] if is_mapping(tree.get("jobs")) else {}


def steps_of(job) -> list:
    return job["steps"] if is_mapping(job) and isinstance(job.get("steps"), list) else []


def action(step, name: str) -> bool:
    """Whether `step` uses actions/<name>, wherever in the step its `uses:` is."""
    return is_mapping(step) and isinstance(step.get("uses"), str) and step["uses"].startswith(f"actions/{name}@")


def scalars(x) -> list[str]:
    """Every scalar in a tree: where a command in any job or step would be."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    return [s for v in (x.values() if isinstance(x, dict) else x) for s in scalars(v)]


_workflows: dict = {}


def workflow(path: str):
    if path not in _workflows:
        _workflows[path] = parse_workflow(read(path), path)
    return _workflows[path]


TOP_KEYS = ["name", "on", "concurrency", "permissions", "jobs"]
JOB_KEYS = ["runs-on", "timeout-minutes", "steps", "name", "strategy"]
STEP_KEYS = ["name", "id", "run", "uses", "with", "env", "working-directory", "if"]
# Variables that change how npm, Node, Python or the shell runs a step.
TOOLCHAIN_ENV = re.compile(r"(npm_config_\w*|NODE_OPTIONS|NODE_PATH|PYTHON\w*|PYTEST_\w*|BASH_ENV|ENV|SHELL|SHELLOPTS|BASHOPTS|PATH|LD_\w+)", re.I)
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
PINNED_LINE = re.compile(r"\s*(- )?uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*")
MATRIX_PYTHON = "${{ matrix.python-version }}"


def test_the_workflows_are_block_yaml_pinned_and_let_no_step_pass_red():
    """Every workflow, every job and every step, whatever its spelling: a key the reader
    does not know, a job or step key outside these lists, or an environment variable a
    tool reads, is a way for a step to pass red that no check below would see."""
    files = sorted(p.name for p in (ROOT / ".github/workflows").iterdir() if p.suffix in (".yml", ".yaml"))
    assert files == ["ci.yml"], f"one workflow, ci.yml: {files}"
    tree, lines, keys = workflow(CI)
    assert [k for k in tree if k not in TOP_KEYS] == [], f"no top-level key but {TOP_KEYS}: an env: or a defaults: there reaches every step"
    for key, n in keys:
        if key == "uses":
            assert PINNED_LINE.fullmatch(lines[n]), f"{CI}:{n + 1}: every action is pinned to a commit SHA with its tag in a comment"
    for name, job in jobs_of(tree).items():
        assert is_mapping(job), f"{name} is a job"
        assert [k for k in job if k not in JOB_KEYS] == [], f"the {name} job has no key but {JOB_KEYS}: no if:, continue-on-error, env: or permission of its own"
        assert steps_of(job), f"the {name} job has steps"
        for i, step in enumerate(steps_of(job), 1):
            where = f"{name}, step {i}"
            assert is_mapping(step), f"{where} is a mapping"
            assert [k for k in step if k not in STEP_KEYS] == [], f"{where} has no key but {STEP_KEYS}: no continue-on-error, no shell:"
            assert ("run" in step) + ("uses" in step) == 1, f"{where} runs a command or uses an action"
            if "uses" in step:
                assert PINNED.fullmatch(str(step["uses"])), f"{where} uses an action pinned to a commit SHA"
            if "if" in step:
                assert step["if"] == "failure()" and action(step, "upload-artifact"), f"{where}: the only if: is if: failure() on an upload"
            if "env" in step:
                assert is_mapping(step["env"]), f"{where}: env is a mapping"
                assert [k for k in step["env"] if TOOLCHAIN_ENV.fullmatch(k)] == [], f"{where}: no env that npm, Node, Python or the shell reads"
            if action(step, "setup-python"):
                given = step.get("with") if is_mapping(step.get("with")) else {}
                if name == "check":
                    assert given.get("python-version") == MATRIX_PYTHON and "python-version-file" not in given, (
                        f"{where}: check sets up the matrix's Python"
                    )
                else:
                    assert given.get("python-version-file") == ".python-version" and "python-version" not in given, (
                        f"{where}: sets up Python from .python-version and names no version of its own"
                    )


def test_the_ci_workflow_triggers_concurrency_permissions_and_timeouts():
    assert has(CI), f"{CI} exists"
    tree = workflow(CI)[0]
    assert tree.get("name") == "CI"
    # Every branch, pull requests, by hand, and once a week: GitHub runs the schedule from
    # the default branch only, so an advisory or a rotting build shows there without a push.
    assert tree.get("on") == {
        "push": {"branches": ["**"]},
        "pull_request": None,
        "workflow_dispatch": None,
        "schedule": [{"cron": SCHEDULE}],
    }, tree.get("on")
    assert tree.get("concurrency") == {"group": "ci-${{ github.ref }}", "cancel-in-progress": "true"}
    # No job may name permissions of its own (the job keys above), so this is all there is.
    assert tree.get("permissions") == {"contents": "read"}, "permissions: contents: read at the top, and nothing else anywhere"
    for name, job in jobs_of(tree).items():
        assert re.fullmatch(r"[1-9]\d*", str(job.get("timeout-minutes"))), f"the {name} job states its timeout-minutes"


def test_the_check_job():
    """Checkout, the matrix's Python, the install, then ruff, pytest and this file on its
    own, each a bare step, with nothing between them: the command with `|| true` after it,
    an `if:` beside it or a step before it that rewrites what it runs is a step check does
    not have. The matrix is exactly the floor and the pinned version, with nothing
    excluded."""
    tree = workflow(CI)[0]
    job = jobs_of(tree).get("check")
    assert is_mapping(job), "ci.yml has a check job"
    assert job.get("timeout-minutes") == "20"
    matrix = job.get("strategy", {}).get("matrix") if is_mapping(job.get("strategy")) else None
    assert is_mapping(matrix) and matrix.get("python-version") == ["3.10", "3.12"], "CI tests 3.10 and 3.12"
    assert "include" not in matrix and "exclude" not in matrix, "the matrix adds and drops nothing"
    steps = steps_of(job)
    assert len(steps) >= 6, "check has its steps"
    assert action(steps[0], "checkout") and list(steps[0]) == ["uses"], "check starts with a checkout of the commit, nothing beside it"
    assert action(steps[1], "setup-python") and set(steps[1]) == {"uses", "with"}, "then sets up Python"
    assert len([s for s in steps if action(s, "setup-python")]) == 1, "check sets up Python once"
    assert list(steps[2]) == ["run"] and re.fullmatch(r'pip install -e "\.\[[^"\]]+\]"', steps[2]["run"]), "then installs the project with its extras"
    assert steps[3:6] == [{"run": "ruff check ."}, {"run": "pytest -q"}, {"run": "python tests/test_conventions.py"}], (
        "then `ruff check .`, `pytest -q` and `python tests/test_conventions.py`, each a bare step, with nothing between"
    )


def test_the_audit_job():
    """The declared dependencies are audited in a job of their own, so that an advisory,
    news about a tree that has not changed, turns audit red on the next run (on the
    default branch the weekly one at the latest) and leaves check meaning what it always
    meant: at the newest versions the ranges resolve to, and at the declared floors
    (tests/test_audit.py reads floors.py and the pinned requirements).

    The job exactly, as the npm repositories hold theirs: a line anywhere else in the file
    cannot stand in for one missing here, and a bare `pip-audit` (which audits the
    runner's own tools), `pip-audit .` (no llm extra), the dev extra, `|| true`,
    `continue-on-error`, `if:` or an unpinned install is a job this is not."""
    tree = workflow(CI)[0]
    job = jobs_of(tree).get("audit")
    assert is_mapping(job), "the audit is a job of its own"
    assert sorted(job) == ["runs-on", "steps", "timeout-minutes"], "the audit job has runs-on, timeout-minutes and steps, nothing else"
    assert re.fullmatch(r"[\w.-]+", str(job["runs-on"])), "the audit job runs on one runner"
    steps = steps_of(job)
    checkout = steps[0]["uses"] if steps and action(steps[0], "checkout") else "actions/checkout@<sha>"
    setup = steps[1]["uses"] if len(steps) > 1 and action(steps[1], "setup-python") else "actions/setup-python@<sha>"
    assert steps == [
        {"uses": checkout},
        {
            "uses": setup,
            "with": {
                "python-version-file": ".python-version",
                "cache": "pip",
                "cache-dependency-path": ".github/audit/requirements.txt\npyproject.toml\n",
            },
        },
        # Pinned with hashes, and its dependencies with it (tests/test_audit.py reads the file).
        {"run": "pip install --require-hashes -r .github/audit/requirements.txt"},
        # The runtime dependencies and the llm extra, at the newest versions they resolve to.
        {"run": 'pip-audit -r <(echo ".[llm]")'},
        # The same requirements at their floors. `&&`: pip-audit passes an empty list, and
        # nothing reads the status of a `<(...)`.
        {"run": 'python .github/audit/floors.py > "$RUNNER_TEMP/floors.txt" && pip-audit -r "$RUNNER_TEMP/floors.txt"'},
    ], "the audit job is checkout, setup-python from .python-version, the hash-pinned install and the two audits, nothing else"
    assert len([s for s in scalars(tree) if "pip-audit" in s]) == 2, "pip-audit runs in the audit job's two steps and nowhere else"


def test_the_documents():
    for f in ("README.md", "LICENSE", "SECURITY.md", "REVIEW.md", "CLAUDE.md", "CONVENTIONS.md", ".gitignore", ".github/dependabot.yml"):
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
    hook = read(".claude/hooks/session-start.sh")
    assert line(hook, r'^if \[ "\$\{CLAUDE_CODE_REMOTE:-\}" != "true" \]; then$'), "the hook is remote-only"
    # It installs into .venv, never through the pip on PATH: a PEP 668-marked Python
    # refuses a bare `pip install`, and the hook would end with no ruff and no pytest.
    assert line(hook, r"^\s*python3 -m venv \.venv$"), "the hook creates .venv when it is absent"
    assert line(hook, r'^\.venv/bin/pip install -e "\.\[dev\]"$'), "the hook installs through .venv"
    assert not line(hook, r"^\s*pip install"), "no install through the pip on PATH"
    assert subprocess.run(["bash", "-n", str(ROOT / ".claude/hooks/session-start.sh")]).returncode == 0


if __name__ == "__main__":
    # CI runs this file on its own after `pytest -q` (CONVENTIONS.md, "CI"): no pytest
    # configuration can keep it from running here.
    failures = 0
    for name, check in list(globals().items()):
        if name.startswith("test_") and callable(check):
            try:
                check()
            except Exception as error:  # every failure is reported, then counted
                failures += 1
                print(f"not ok - {name}: {type(error).__name__}: {error}")
            else:
                print(f"ok - {name}")
    raise SystemExit(1 if failures else 0)
