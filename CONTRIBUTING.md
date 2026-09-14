# Contributing to randostats

Thanks for improving randostats. The project intentionally keeps its stack small: FastAPI + SQLite on the backend and plain HTML/CSS/JavaScript on the frontend.

## Set up

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

For the optional Claude integration:

```bash
pip install -e ".[dev,llm]"
```

Run the app locally with:

```bash
randostats serve
```

Then open `http://127.0.0.1:8765`. You can import your own export or use the bundled sample data from the Import screen.

## Before opening a PR

Run the same basic checks as CI:

```bash
pytest -q
node --check randostats/static/app.js
node --check randostats/static/m.js
node --check randostats/static/m-sw.js
node --check randostats/static/ui-state.js
node --check randostats/static/overview.js
```

If your change touches a parser, statistic, import identity, cache invalidation, Counterpoint matching, or another correctness-sensitive path, add a regression test that fails on the old behavior.

## Frontend conventions

Read `ARCHITECTURE.md` before adding substantial desktop UI. New independent surfaces should prefer their own module and use `window.RandoCore` for requests, DOM helpers, navigation, and loading/error states.

Imported message text, contact names, senders, and other export-derived values are untrusted. Prefer DOM construction and `textContent`. If a value must reach `innerHTML`, escape it first.

Keep accessibility parity with existing charts: keyboard users should be able to reach the same information and actions as pointer users, focus should remain visible, and reduced-motion preferences must be respected.

## Backend conventions

Keep parsing, persistence, statistics, and HTTP routing separate. Logic that can be tested without FastAPI should generally live below `api.py`.

Message timestamps are deliberately normalized in one place. Avoid adding source-specific timezone behavior outside the timestamp helpers.

## Pull requests

Keep PRs focused enough that a regression can be isolated. Describe the user-visible behavior, the failure mode being prevented, and how you verified the change. Screenshots are useful for visual changes, but tests should carry correctness where practical.
