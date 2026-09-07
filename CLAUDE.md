# randostats

Message statistics plus a counterpoint engine that answers a statistic with a
real, sourced one of the same size and no relevance. See README.md for what it
does and how to run it. This file is the working context: conventions, and the
things that have bitten already.

## Shape

`parsers/` turn exports into `Message` objects. `stats.py` is pure functions
over `list[Message]`. `store.py` is SQLite. `api.py` wires them to HTTP.
`static/` is the whole front end: no build step, no framework, hand-drawn SVG.
`counterpoint/` holds the engine plus its content as JSON on disk.

## Conventions

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
  regression; do not weaken it.
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

## Verifying a change

`pytest -q` covers parsers, stats, engine, API, security and timestamps. For
anything visual, run the app and look at it:

```bash
randostats serve            # then load sample data on the Import tab
python samples/make_sample.py
```

Chromium and Playwright are available in the cloud sandbox; screenshot the
tabs in both colour schemes rather than assuming a chart renders.
