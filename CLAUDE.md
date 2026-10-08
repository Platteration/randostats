# randostats

Read AGENTS.md first. It holds the working rules every coding agent follows in this repository; this file adds the notes specific to this project.

Message statistics plus a counterpoint engine that answers a statistic with a
real, sourced one of the same size and no relevance. See README.md for what it
does and how to run it. This file is the working context: conventions, and the
things that have bitten already.

## Shape

`parsers/` turn exports into `Message` objects. `stats.py` is pure functions
over `list[Message]`. `store.py` is SQLite. `api.py` wires them to HTTP.
`static/` is the whole front end: no build step, no framework, hand-drawn SVG.
`counterpoint/` holds the engine plus its content as JSON on disk.

## Project conventions

- **Stats stay pure.** Every function in `stats.py` takes messages and returns
  JSON-serialisable data, so tests and endpoints share them. Filtering and
  caching belong in `api.py`.
- **Content is data.** Facts and punchline voices are JSON in
  `counterpoint/packs/` and `counterpoint/voices/`. Adding either means adding
  a file, never editing the engine.
- **Charts are built by hand** in `static/app.js` (`hbars`, `columns`, `line`,
  `heatmap`, `dumbbell`, `diverging`). Each takes an optional `onClick` and
  registers itself with `keyboardNav`. Follow the existing marks: 2px surface
  gaps, rounded data ends, hairline grid, legend for two or more series.
- **Every chart needs a table twin** (`container._table`) and must survive
  both themes.

## Things that have already gone wrong

- **Untrusted input.** Contact names, senders and message text come from files
  other people wrote. Everything reaching `innerHTML` or a tooltip goes
  through `esc()`. `tests/test_security.py` walks `app.js` and fails on a
  regression; do not weaken it. Nothing writes a `style=""` attribute either:
  the policy refuses inline styles, so a chart sets its declarations through
  the CSSOM (`el()` does it for a `style` key) or a class (`dot()`'s `hueN`).
- **A request model defined inside `create_app` breaks FastAPI.** Pydantic
  models for request bodies must be at module scope, or the endpoint 422s on
  every call. This has happened twice (`CounterRequest`, `PackConfig`).
- **Timestamps.** Exports disagree: some write local wall clock, some write
  UTC. `parsers/timestamps.py` is the only place that decides; everything ends
  up local and naive. `tests/test_timestamps.py` pins all six parsers to the
  same instant.
- **Caching must invalidate.** `api.py` caches derived stats per import
  version. `bump()` clears both the message cache and `derived`. Stale numbers
  are worse than slow ones, and there is a test.
- **Measure before optimising.** A compiled regex replacing twelve substring
  checks in `is_media_placeholder` was three times *slower*. The comment in
  the code says so, so nobody tries it again.

## The counterpoint engine

A realistic probe once showed it confidently wrong: "crime went up 40%" answered
as a share, "I'm 100% sure" answered as a statistic, famous myths answered with
parallels. `tests/test_verdicts.py` pins each case. When changing the engine:

- **Share vs change.** A percentage *of* something and a percentage *change*
  are different kinds. Changes match ratio facts, never shares.
- **Verdicts before parallels.** `analyse()` returns myths, fact-checks and
  appeals first. A myth silences parallels in its sentence only.
- **A fact-check must be right or absent.** `_TOPICS` is hand-written; the
  first matching topic decides, and a framing or population mismatch returns
  nothing rather than trying a looser topic. There are tests for each refusal.
- **Myths test themselves.** Each carries `examples` that must hit it, and a
  list of ordinary sentences must hit none. Loosen a pattern, run the tests.

## The phone app

`/m` is a second front end (`static/m.{html,css,js}`, `m-sw.js`, `m.webmanifest`)
sharing only the counterpoint API. Deliberately standalone rather than importing
from `app.js`, which is one closed IIFE. Consequences worth knowing:

- `tests/test_security.py` parameterises its escaping lint over **both** front
  ends. Each duplicates `esc()`; neither may skip it. The desktop modules
  (`overview.js`, `ui-state.js`) and the site's own scripts (`login.js`,
  `guard.js`) are in the same `FRONT_ENDS` list; a new static module goes there
  too, and in CI's `node --check` list, or neither sees it
  (`tests/test_static_shell.py` fails until it is in both).
- That lint follows multi-line template literals. It used to check only the line
  the sink was on, which let an unescaped value in a card template pass.
- A second lint parses **every** template literal containing a tag, wherever it
  is. The sink-based one never saw markup built in a helper or hoisted into a
  variable, nor a template starting on the line after `innerHTML =`. Hoisting a
  value to quiet the lint is no longer a way round it.
- The service worker is served from `/sw.js`, not `/static/`, because scope
  defaults to the script's own directory. It must never cache `/api/` —
  `counterpoint/packs` reflects database state. Its `SHELL` holds every script
  and stylesheet `m.html` loads, `guard.js` included (`tests/test_website.py`),
  and `CACHE` changes name whenever `SHELL` does, or installed phones keep the
  old list.
- `navigator.share` must be called with no `await` in front of it, so the share
  PNG is rendered when the card is shown. There is a test for that.
- Icons are committed PNGs; regenerate with `python samples/make_icons.py`.

## The website

randostats is a website as well as a local app, and `api.py` is the host:
there is no `_headers`, `.htaccess` or proxy config holding the policy.

- **One policy, written once.** `CSP`, `HEADERS` and `PERMISSIONS` in `api.py`,
  put on every response by `decorate()` in the middleware and the 500 handler
  (refusals, 404s and HEAD included); `CSP_HTTPS` and `HSTS` only when the
  request's scheme is https. README's Deploy section prints the same block,
  and `tests/test_website.py` holds the two equal: change both or neither.
- **Every source is measured.** Each one was decided by `pytest -q e2e` (or a
  scratch walk like it) with the policy as a response header, and removing any
  of them breaks the walk but `worker-src`, which `script-src` would cover by
  fallback and is stated on purpose. A new feature that loads something new fails the walk
  until the policy names it; name it there, in README's table and in the block,
  never as `'unsafe-inline'` or a wildcard. Trusted Types was measured and is
  not adopted (README says why).
- **The gate.** `auth.py`. Loopback with no `RANDOSTATS_PASSWORD`: no login.
  `serve` beyond loopback without one exits 2; `_beyond_loopback` refuses a
  connection on a non-loopback address when the app was started some other way.
  With one: `PUBLIC_PATHS` and `/static/` need no session, everything else does
  (pages 303 to `/login?next=`, the rest 401). `local_path` is all that stands
  between `next=` and an open redirect; `tests/test_auth.py` lists the payloads.
  The sign-in route is `async` so the guess count and the comparison run with no
  await between them.
- **Pages.** Every page with a script loads `/static/guard.js` first and has a
  `<noscript>` note; its own script ends with `window.RandoGuard.started()`, or
  the guard reports a failed start on every load. So nothing on the way there
  may throw on what a browser can refuse: `m.js` reads `localStorage` inside a
  `try`, since a browser keeping no site data throws on any access, and the
  walk runs both pages with storage refused. `404.html` has no script.
  Page and site-file routes answer HEAD (`PAGE_METHODS`); FastAPI's docs routes
  are off.
- **`security.txt` expires** on the date in `static/security.txt`; the website
  test fails once it has passed. Renew it a year out.
- **No sub-path.** Every address the front end writes is root-absolute, so the
  site runs at the root of its own origin only; the walk runs there.

## Verifying a change

`ruff check .` lints; `pytest -q` covers parsers, stats, engine, API, security,
timestamps and the website's headers and files. `pytest -q e2e` (after
`pip install -e ".[e2e]"`; Chromium is preinstalled in the cloud sandbox, so
`python -m playwright install` is only for elsewhere) drives both front ends
and the sign-in page in Chromium under the policy and is part of CI's check
job. For anything visual, run the app and look at it:

```bash
randostats serve            # then load sample data on the Import tab
python samples/make_sample.py
```

Chromium and Playwright are available in the cloud sandbox; screenshot the
tabs in both colour schemes rather than assuming a chart renders.

## Conventions

This repository follows `CONVENTIONS.md`, which is identical in every platteration
repository and pinned by the conventions test (`npm run test:conventions`, or
`tests/test_conventions.py` in a Python repository): the script set (`test`,
`typecheck`, `lint`, `check`, `test:e2e`, `test:all`), Node 22 via `.nvmrc`, one
`.editorconfig`, ESLint per stack, the `ci.yml` shape, the documents every repository
carries and the README skeleton. The repository's check command (`npm run check`, or
`ruff check .` then `pytest -q` in a Python repository) is the gate before a push. To
change a convention, change it in every repository in one pass and update the hashes in
the test.
