# randostats architecture

randostats is deliberately small: a FastAPI/SQLite backend serves a no-build-step HTML/CSS/JavaScript frontend. The goal is to keep that simplicity without letting a single file become the only place new behavior can live.

## Backend

- `randostats/api.py` owns HTTP routes and request/response validation, and is the website's host: it sets the one response-header policy on every response, serves the 404 page, `robots.txt` and `security.txt`, and refuses every path that is not part of the site.
- `randostats/auth.py` is the password gate (`RANDOSTATS_PASSWORD`): sessions, the constant-time comparison, the guess limit, and `local_path` for the sign-in page's `next=`.
- `randostats/store.py` owns SQLite persistence, settings, and import identity.
- `randostats/stats.py` contains pure-ish statistical transforms over stored messages.
- `randostats/parsers/` turns exports into the shared message model. Timestamp meaning is centralized in the timestamp parser helpers.
- `randostats/counterpoint/` owns claim extraction, fact packs, voices, and the optional LLM rephrasing layer.

Keep parsing, persistence, statistics, and HTTP concerns separate. If a feature can be tested without FastAPI, prefer putting the logic below `api.py`.

## Desktop frontend

The desktop shell is `randostats/static/index.html`.

The long-lived chart implementation remains in `app.js` for now. New standalone surfaces should not automatically be added there.

- `app.js`: existing charts, drill-down, tab mechanics, import flow, Counterpoint desktop UI.
- `ui-state.js`: shared browser core exposed as `window.RandoCore` — query helpers, API requests, DOM construction, formatting, tab navigation, and loading/empty/error states.
- `overview.js`: high-level dashboard and deterministic insight summaries; it consumes `RandoCore` rather than defining its own API or DOM helpers.
- `app.css`: established design tokens and chart/application styles.
- `spruce.css`: additive layout/polish layer for newer surfaces. Prefer moving stable rules into `app.css` only when there is a reason to touch the base stylesheet.

### Frontend rule of thumb

A new feature belongs in its own file when it has its own data-loading lifecycle or can render independently of the hand-drawn chart primitives. Reuse `RandoCore` for request, DOM, navigation, and view-state behavior instead of inventing parallel helpers.

The next safe extraction target is code in `app.js` that is both widely reused and behavior-neutral — shared request/state helpers first, then self-contained view domains. Do not split chart primitives merely to make the file count look better; split at boundaries that reduce coupling.

Imported data is untrusted. Prefer `textContent` and DOM construction. If HTML strings are unavoidable, escape every imported value before it reaches `innerHTML`.

## Site pages

`guard.js` is the safety net every scripted page loads first; each page's own script ends with `window.RandoGuard.started()`. `login.html`/`login.js` is the sign-in page (only served when a password is set) and `404.html` the not-found page, which has no script. None of them depends on the desktop or phone modules.

## Phone frontend

`m.html`, `m.css`, `m.js`, and `m-sw.js` form the installable `/m` Counterpoint PWA. It intentionally has a separate interaction model from the analytics UI. Do not make the desktop application a dependency of the phone shell.

## Tests and CI

`pytest` covers parsers, statistics, persistence, API behavior, Counterpoint logic, the website's headers and files, the password gate, and regression cases. CI also asks Node to parse every JavaScript entry point, and runs `pytest -q e2e`, which drives both front ends and the sign-in page in Chromium under the policy the server sends. `tests/test_static_shell.py` guards the no-build-step asset graph and the module boundary so an HTML reference or shared-core dependency cannot silently regress.

Pushing a `v*` tag runs `.github/workflows/release.yml`. The workflow builds both Python distributions, smoke-tests the wheel in a fresh virtual environment, and uploads the artifacts. It intentionally stops short of publishing them.

For bug fixes, reproduce the bug in a test first when practical. For frontend-only bugs that are difficult to drive without a browser, keep changes narrow and add a static regression assertion when it can actually catch the failure mode.
