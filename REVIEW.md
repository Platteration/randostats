# randostats — security & upgrade review (2026-09-09)

Two independent reviewers read every first-party file in this repository; a third then re-read each security or bug claim against the code and tried to refute it. Only claims that survived that check are listed as findings; the ones that did not are recorded at the end so they are not re-raised.

## Status — what has been fixed

All of the following are fixed on `claude/repo-review-security-baiyud`, each with a regression test that was checked by reverting the fix.

**First pass** — every critical and high finding, plus the medium ones that were quick:

`SEC-1`, `SEC-2`, `BUG-1`, `MISS-1`

**Second pass** — the remaining medium findings and the low-severity ones that were trivial or small:

`SEC-4`, `SEC-6`, `MISS-2`, `SEC-3`, `SEC-5`, `BUG-2`, `BUG-3`, `BUG-4`, `MISS-3`, `MISS-4`, `MISS-5`

Deliberately not done: `SUP-1`, `CI-1`. Each was either already covered by an earlier pass, or judged churn or too risky to make without a device or a measurement. The reasoning is in the commit that touched it.

An independent reviewer then read each commit and tried to find what was wrong with it, and a second reviewer tried to refute every objection raised. What survived that was fixed in a follow-up commit.

Repository hardening applied here as well: every GitHub Action is pinned to a commit rather than a floating tag, each workflow declares a least-privilege `permissions` block, and a Dependabot config, a licence and a security policy are in place.

The rest of this document is the review as written. Fixed items are left in place so the reasoning behind each change stays with it.

## Summary

randostats is a local-only Python 3 app (FastAPI + SQLite + a hand-drawn SVG front end with no build step) that imports message exports from eight sources, computes people/timing/spelling/tone/Wrapped statistics, and runs a 'counterpoint engine' that answers any statistic in an argument with a sourced, equally-sized, unrelated one, optionally rephrased by Claude. It is unusually mature for a side project: 17 commits, four of them review-driven fix rounds, 100+ focused tests including a regex walk that fails on XSS regressions, timestamp normalisation pinned across all parsers, cache invalidation tests, zip-bomb caps, and a CLAUDE.md that records real bugs. The headline problems are release hygiene rather than code: the package-data glob omits counterpoint/packs and counterpoint/voices so any non-editable install cannot start, there is no lockfile, LICENSE, Dependabot, lint, type-check or browser test, and the CI matrix stops at Python 3.12 with unpinned actions and no permissions block. The Anthropic integration is current (claude-opus-5, server-side fallbacks, structured parse) but calls the API with the SDK's 10-minute default timeout inside a request. Product-wise the biggest gaps are contact merging across sources, selective deletion/import history, a global date filter, and reply-time distributions; code-wise the 265-line create_app closure with import-time side effects, the security-critical esc() buried 700 lines down in app.js, and the untested iMessage parser are the items to fix first.

## Attack surface

randostats is a single-user FastAPI/uvicorn server bound to 127.0.0.1:8765 by default (`randostats serve`, cli.py:19) with no authentication of any kind; a `--host` flag allows binding to any interface. It exposes a multipart upload endpoint (POST /api/import) that parses WhatsApp text, iMessage SQLite, SMS Backup XML, Telegram/Discord/Meta JSON and zip archives written by other people, a DELETE /api/messages endpoint, JSON POST endpoints for the counterpoint engine and pack/voice settings, and GET endpoints that return aggregate statistics and, via /api/messages, the full text of every stored message. Everything is persisted unencrypted in a local SQLite file (data/randostats.db). The front end is a static, framework-free page that renders imported contact names, senders and message bodies into innerHTML, and it can capture microphone audio through the browser's Web Speech API and send claim text to the Anthropic API when started with --llm. The realistic adversaries are therefore: crafted export files (stored XSS, XML/zip bombs), any website the user visits while the server is running (CSRF against the multipart endpoint, DNS rebinding against the unauthenticated JSON API), and anyone on the LAN if the user binds to 0.0.0.0.

## Already done well

- Every API response carries a strict CSP (script-src 'self', object-src 'none', base-uri 'none', frame-ancestors 'none'), X-Content-Type-Options and Referrer-Policy via middleware (randostats/api.py:28-62), so even a missed escape cannot execute script.
- All imported values reaching innerHTML or tooltips go through esc() (randostats/static/app.js:726), including LLM output (app.js:738,742) and the Wrapped SVG text nodes (app.js:628); a regression test walks app.js for unescaped row values (tests/test_security.py:56-71).
- SQL is fully parameterised; the only f-string in a query is the constant IDENTITY column list (randostats/store.py:24,69-70,89). A process-wide lock serialises the shared sqlite connection and there is a concurrency test (store.py:56, tests/test_security.py:124-155).
- Uploads are size-capped both on the declared multipart size and after read (api.py:32,132-141); zip archives are capped on member count, declared total and per-member size before any member is read, so a compressed bomb fails fast (randostats/parsers/archive.py:14-57) with tests (tests/test_security.py:92-121).
- iMessage databases are written to a 0600 tempfile, opened read-only via a `file:...?mode=ro` URI and unlinked in a finally block (randostats/parsers/imessage.py:63-74).
- Counterpoint session ids are server-issued, bounded to 256 entries and unknown ids are ignored, so a client cannot grow the map (api.py:36,260-283; tests/test_security.py:220-233).
- The Claude path is well contained: the model only chooses among facts the engine already matched, the returned fact_id is validated against that list, refusals and unparseable output fall back to the rule-based line, every exception becomes None, and the credential probe uses real SDK attributes (auth_headers, credentials) (randostats/counterpoint/llm.py:60-91,124-135). The request shape (claude-opus-5, beta.messages.parse with output_format=Model, server-side-fallback-2026-07-01 + fallbacks='default') matches current SDK usage.
- Pydantic request models live at module scope, request query parameters for direction are validated, and search limit/offset are clamped (api.py:39-50,202-206).
- CPU-heavy parsing and inserts run off the event loop via run_in_threadpool, and derived stats are cached per import version with invalidation tests (api.py:146-163; tests/test_api.py:147-187).
- Exports' timestamps are normalised in one module with a cross-parser test pinning all six sources to one instant (randostats/parsers/timestamps.py; tests/test_timestamps.py).
- The database, data/ directory and .env are gitignored (.gitignore:4-5,10); the sample data is synthetic (samples/make_sample.py).

## Findings (18)

| # | Severity | Category | Title | Where | Effort | Status |
|---|---|---|---|---|---|---|
| SEC-1 | Medium | security | No Host-header validation: DNS rebinding lets a web page read (and delete) the entire message database | `randostats/api.py:56` | trivial | confirmed |
| SEC-2 | Medium | security | CSRF on the multipart import endpoint: any website can inject messages and overwrite your name | `randostats/api.py:128` | small | confirmed |
| BUG-1 | Medium | bug | Fact packs and voices are not packaged: a non-editable install (`pip install .`/wheel) crashes at startup | `pyproject.toml:28` | trivial | confirmed |
| MISS-1 | Medium | security | POST /api/counterpoint is quadratic in the number of claims and fans out one paid Claude call per claim, with no length cap | `randostats/counterpoint/engine.py:143` | small | found by second reviewer |
| SEC-3 | Low | security | XML entity-expansion guard is bypassed by a UTF-16 document (billion laughs still reachable) | `randostats/parsers/smsbackup.py:31` | trivial | confirmed, severity lowered |
| SEC-4 | Low | security | Binding to a non-loopback interface exposes the whole database and the Anthropic credential to the network with no auth | `randostats/cli.py:19` | small | confirmed |
| SEC-5 | Low | privacy | "Listen" streams microphone audio to the browser vendor's cloud, contradicting the local-only promise and without disclosure | `randostats/static/app.js:803` | trivial | confirmed |
| SEC-6 | Low | security | XSS regression test only inspects the line that contains the sink, so most multi-line templates are never checked | `tests/test_security.py:64` | small | confirmed |
| BUG-2 | Low | bug | create_app() runs at import time, so `serve --db X` still creates ./data/randostats.db and builds everything twice | `randostats/api.py:321` | trivial | confirmed |
| BUG-3 | Low | bug | /api/stats/conversations accepts inf/nan for gap_hours: inf returns a 500, nan silently produces wrong numbers | `randostats/api.py:209` | trivial | confirmed |
| BUG-4 | Low | reliability | remember() can raise KeyError when an import clears the cache between store and return | `randostats/api.py:95` | trivial | confirmed |
| CI-1 | Low | ci-cd | GitHub Actions are tag-pinned rather than SHA-pinned and the workflow has no permissions block | `.github/workflows/tests.yml:21` | trivial | confirmed |
| SUP-1 | Low | supply-chain | No lockfile or constraints, unpinned dependency floors, no Dependabot, and no LICENSE/SECURITY.md | `pyproject.toml:6` | small | confirmed |
| MISS-2 | Low | security | WhatsApp import burns ~15 s of CPU per megabyte of crafted text (48 strptime attempts per unparseable line) | `randostats/parsers/whatsapp.py:61` | small | found by second reviewer |
| MISS-3 | Low | security | The upload size cap is only checked after the entire body has been received and spooled to disk | `randostats/api.py:131` | trivial | found by second reviewer |
| MISS-4 | Low | security | Archive caps are generous enough to be a memory bomb, and the auto-detect path applies none of them | `randostats/parsers/archive.py:14` | trivial | found by second reviewer |
| MISS-5 | Low | bug | Speller memoisation caches are unbounded and live for the process lifetime | `randostats/stats.py:275` | trivial | found by second reviewer |
| SEC-7 | Info | security | Claim text is interpolated into the Claude prompt without delimiting, so a spoken or typed instruction can steer the punchline | `randostats/counterpoint/llm.py:108` | trivial | confirmed |

### SEC-1 · No Host-header validation: DNS rebinding lets a web page read (and delete) the entire message database

**Severity:** Medium · **Category:** security · **Effort:** trivial · **Where:** `randostats/api.py:56`

The API has no authentication and does not check the Host header. A malicious page served from attacker.example can rebind that name to 127.0.0.1 after the initial load; subsequent same-origin fetches then hit http://attacker.example:8765/api/messages?limit=500 and the server answers because nothing distinguishes the request from the legitimate front end. That endpoint returns the full text, sender and timestamp of every stored message, and DELETE /api/messages wipes them. The absence of CORS headers does not help here because after rebinding the request is same-origin. Chrome's newer Local Network Access prompt mitigates this in some browsers, but Firefox and older browsers do not.

Evidence:

```
api.py:56-62:
    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
(no host check anywhere; cli.py:19: s.add_argument("--host", default="127.0.0.1"))
api.py:197-206: @app.get("/api/messages") ... returns stats.search(...) with full message text
```

**Recommendation.** Add Starlette's TrustedHostMiddleware so only the loopback names are served: `from fastapi.middleware.trustedhost import TrustedHostMiddleware; app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])`, extending the list with the value of --host when it is not loopback. Optionally also reject requests whose Sec-Fetch-Site header is `cross-site`.

### SEC-2 · CSRF on the multipart import endpoint: any website can inject messages and overwrite your name

**Severity:** Medium · **Category:** security · **Effort:** small · **Where:** `randostats/api.py:128`

POST /api/import accepts multipart/form-data, which browsers send cross-origin from an ordinary <form enctype="multipart/form-data"> (or a hidden iframe) without a CORS preflight. The JSON endpoints are protected because FastAPI only parses a body declared as application/json, and DELETE needs a preflight, but the import handler has no Origin or Sec-Fetch-Site check. While the app is running, a page the user visits can silently import arbitrary contacts and message text into the statistics, overwrite the stored self_name (api.py:162), invalidate the cache, and, combined with SEC-3, crash the server. The imported text is escaped on render, so this is data pollution rather than XSS.

Evidence:

```
api.py:128-130:
    @app.post("/api/import")
    async def import_file(file: UploadFile = File(...), self_name: str = Form(...), fmt: str = Form("auto"),
                          contact: str | None = Form(None)):
api.py:162: store.set_setting("self_name", self_name)
```

**Recommendation.** Reject cross-site requests on every mutating route: in the existing middleware, for non-GET requests return 403 when `request.headers.get("sec-fetch-site") == "cross-site"` or when an Origin header is present and its host is not the request's own host. Alternatively require a custom header (e.g. `X-Randostats: 1`) on all fetch() calls in app.js and on the import FormData request; a custom header forces a preflight, which the missing CORS policy then denies.

### BUG-1 · Fact packs and voices are not packaged: a non-editable install (`pip install .`/wheel) crashes at startup

**Severity:** Medium · **Category:** bug · **Effort:** trivial · **Where:** `pyproject.toml:28`

package-data only lists `static/*` and `counterpoint/*.json`; the `*` glob does not descend into `counterpoint/packs/` or `counterpoint/voices/`, and there is no MANIFEST.in or VCS plugin for include-package-data to pick them up. Building with setuptools from a copy of the repo confirms the wheel would contain facts.json and static/ but no packs or voices JSON. At runtime `_voices()` then raises RuntimeError("no punchline voices found") while `create_app()` builds the engine, and because api.py calls create_app() at import time, `randostats serve` (and any `from randostats.api import ...`) dies immediately. The README's `pip install -e .` hides this because editable installs read the files from the checkout; CI also uses -e.

Evidence:

```
pyproject.toml:28-29:
[tool.setuptools.package-data]
randostats = ["static/*", "counterpoint/*.json"]
setuptools build_py data_files for this project: static/app.css, static/app.js, static/index.html, counterpoint/facts.json -- packs json shipped: False, voices json shipped: False
packs.py:60-61: if not voices: raise RuntimeError(f"no punchline voices found in {VOICE_DIR}")
api.py:67: return CounterpointEngine(packs=enabled, voice=store.get_setting("voice", cp_packs.DEFAULT_VOICE))
```

**Recommendation.** Change the entry to `randostats = ["static/*", "counterpoint/*.json", "counterpoint/packs/*.json", "counterpoint/voices/*.json"]` (or `counterpoint/**/*.json` with setuptools>=62), and add a CI job that runs `pip install .` (non-editable) followed by `randostats counter "70% of people"` so the packaged artifact is exercised.

### MISS-1 · POST /api/counterpoint is quadratic in the number of claims and fans out one paid Claude call per claim, with no length cap

**Severity:** Medium · **Category:** security · **Effort:** small · **Where:** `randostats/counterpoint/engine.py:143`

`CounterRequest.text` has no maximum length and `extract_claims` has no ceiling on how many claims it returns. Its overlap check `taken()` rescans every span recorded so far for every regex match, so the cost grows with the square of the number of claims: I measured 0.61 s for 1,000 claims, 2.5 s for 2,000, 10.5 s for 4,000 and 45.3 s for 8,000 (111 KB of text), i.e. roughly an hour of pegged CPU for a megabyte. Because the handler is a sync `def`, that runs in the threadpool holding the GIL and the whole local server stalls. With `--llm` it is also a money bug: api.py:265-274 groups the results by claim key and calls `llm.sharpen` once per group through `pool.map`, so a text with N distinct claims issues N Claude requests at the owner's expense with no cap. This is reachable by accident (pasting a long article into the Counterpoint box) as well as by a DNS-rebinding page (SEC-1) or any LAN client when `--host` is widened, and it is the one place where an outside request can spend the owner's API budget.

Evidence:

```
randostats/counterpoint/engine.py:143-153 `def taken(m: re.Match) -> bool:\n        return any(not (m.end() <= s or m.start() >= e) for s, e in spans)` with `spans.append(...)` in `add`; engine.py:284-301 `def respond(self, text: str, per_claim: int = 2, seen=None)` loops over every claim with no limit; randostats/api.py:46-50 `class CounterRequest(BaseModel):\n    text: str` (no max_length); randostats/api.py:265-274 `groups = list(by_claim.items())\n            with ThreadPoolExecutor(max_workers=min(4, len(groups))) as pool:\n                sharpened = pool.map(lambda g: (g[0], llm.sharpen(...)), groups)`. Measured: extract_claims on 1000/2000/4000/8000 claims -> 0.611/2.47/10.53/45.35 s.
```

**Recommendation.** Bound the input and the fan-out: `text: str = Field(max_length=4000)` on CounterRequest, stop `extract_claims` after N claims (say 20) and cap the number of LLM groups per request (say 4). Independently, make the overlap check O(1) per match with a `bytearray(len(text))` coverage map instead of the linear `spans` scan, which removes the quadratic term for legitimate long inputs.

### SEC-3 · XML entity-expansion guard is bypassed by a UTF-16 document (billion laughs still reachable)

**Severity:** Low (reported as medium, adjusted after review) · **Category:** security · **Effort:** trivial · **Where:** `randostats/parsers/smsbackup.py:31`

The SMS importer refuses files containing the bytes `<!ENTITY` and then hands the raw bytes to xml.etree, which expands internal entities without limit. expat auto-detects UTF-16 from a byte-order mark, so an export encoded as UTF-16 contains no `<!ENTITY` byte sequence yet its entity declarations are honoured. Verified locally: for a UTF-16 document with nested entities the guard reports False and ET.fromstring expands the body to the full length. A classic billion-laughs payload (a few hundred bytes) therefore exhausts memory and kills the server. Auto-detection does not recognise UTF-16, but the format can be forced with fmt=smsbackup, including from a cross-site form (SEC-2).

Evidence:

```
smsbackup.py:31-33:
    if b"<!ENTITY" in data:
        raise ValueError("this XML declares entities, which this importer will not expand")
    root = ET.fromstring(data)
Local check: data = doc.encode("utf-16") -> (b"<!ENTITY" in data) == False; ET.fromstring(data) expanded &b; to 100 chars.
```

**Recommendation.** defusedxml is still the correct fix, but a dependency-free one is cheaper here and matches the file's existing style: decode with the encoding expat detects before the byte check (or simply reject data starting with a UTF-16 BOM, `data[:2] in (b"\\xff\\xfe", b"\\xfe\\xff")`, since no SMS Backup & Restore export is UTF-16), and note in the comment that modern expat already caps amplification, so this guard is belt-and-braces.

*Reviewer note (confirmed, severity lowered):* The bypass itself is real and I reproduced it: for a UTF-16-encoded document the byte guard does not fire and ElementTree still honours the entity declarations. But the claimed impact — 'a classic billion-laughs payload (a few hundred bytes) therefore exhausts memory and kills the server' — is false on any libexpat >= 2.4.0 (2022), which enables billion-laughs amplification protection by default (max amplification 100x, 8 MiB activation threshold). On this machine (expat 2.6.1, Python 3.11) the nested-entity payload aborts with `ParseError: limit on input amplification factor (from DTD and entities) breached` at 15 MB peak RSS, and api.py:157-158 turns that into a clean HTTP 400. What is left is (a) a real defence-in-depth hole on old runtimes (expat < 2.4, e.g. Ubuntu 20.04 era, still possible under the declared requires-python >= 3.10) and (b) a 100x expansion ceiling, so a multi-megabyte crafted file can still be inflated — which is a weaker version of the much cheaper DoS in MISS-2. That is a low, not a medium.

### SEC-4 · Binding to a non-loopback interface exposes the whole database and the Anthropic credential to the network with no auth

**Severity:** Low · **Category:** security · **Effort:** small · **Where:** `randostats/cli.py:19`

`randostats serve --host 0.0.0.0` is a documented option, but there is no authentication, token or warning. Anyone on the same network (coffee-shop Wi-Fi, a shared LAN) can then read every message via /api/messages, delete them, change settings, and, when started with --llm, make the server call the Anthropic API on the owner's key. Default binding is loopback, so this only matters when the user opts in, but the CLI gives no hint of the consequence.

Evidence:

```
cli.py:19-21:
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--llm", action="store_true", ...)
api.py: no auth dependency on any route.
```

**Recommendation.** When args.host is not a loopback address, either refuse unless `RANDOSTATS_TOKEN` is set and enforce it as a bearer token in the middleware (compare with `secrets.compare_digest`), or at minimum print a prominent warning that the private message archive will be readable by everyone on the network. Also feed the chosen host into the TrustedHostMiddleware allow-list from SEC-1.

### SEC-5 · "Listen" streams microphone audio to the browser vendor's cloud, contradicting the local-only promise and without disclosure

**Severity:** Low · **Category:** privacy · **Effort:** trivial · **Where:** `randostats/static/app.js:803`

The Counterpoint tab's Listen button uses the Web Speech API in continuous mode. In Chrome and Edge that API uploads the audio to Google's/Microsoft's speech service; the feature is explicitly meant to be used while other people are talking, so third parties' speech leaves the machine. The README states "Everything runs locally" and the import help panel says "Nothing is uploaded anywhere", and neither the UI nor the README mentions this. With --llm the transcribed claims are additionally sent to Anthropic, which the README does disclose.

Evidence:

```
app.js:803: rec = new SR(); rec.continuous = true; rec.interimResults = true;
app.js:810: if (e.results[i].isFinal) { finalText += t + " "; counter(t, { live: true })...
README.md:11-12: "Everything runs locally. Your messages go into a SQLite file on your machine and never leave it."
```

**Recommendation.** Add a one-line note next to the Listen button and in the README that speech recognition is performed by the browser vendor's cloud service in Chrome/Edge (on-device in Safari), and that with --llm claims are sent to Anthropic; consider a first-use confirm() before starting continuous recognition.

### SEC-6 · XSS regression test only inspects the line that contains the sink, so most multi-line templates are never checked

**Severity:** Low · **Category:** security · **Effort:** small · **Where:** `tests/test_security.py:64`

test_frontend_escapes_every_imported_value_it_renders is the guard CLAUDE.md says must not be weakened, but it skips any line that does not itself contain `.innerHTML`, `insertAdjacentHTML`, `hover(` or `showTip(`. Nearly every table and the whole counterpoint renderer are built on continuation lines of a template literal (app.js:512, 535, 554, 573, 596, 736-743) whose sink is on a different line, so those interpolations are not examined at all. Today they are all escaped (verified by hand), but the test would not catch a future `${r.contact}` added on any of those lines, which is exactly the regression class it exists for.

Evidence:

```
tests/test_security.py:64-66:
    for number, line in enumerate(APP_JS.read_text().splitlines(), start=1):
        if not any(sink in line for sink in HTML_SINKS):
            continue
app.js:511-512: $("#peaks").innerHTML = peaks.length ? `...` +
      peaks.map(p => `<tr><td>${dot(p.contact)}${esc(p.contact)}</td>...<td>${p.peak_weekday}</td>...`)  (line 512 is never inspected)
app.js:732-745: const html = Object.entries(groups).map(...`...${esc(llm ? llm.punchline : chosen.lines[0])}...`) ... box.innerHTML = html;
```

**Recommendation.** Make the scan template-aware: once a sink line is seen, keep scanning until the backtick-delimited template and the statement end (track an open backtick count), or simply scan every `${...}` in the file and whitelist the few known-safe non-row expressions. A more robust option is to parse app.js with acorn in the existing `node --check` CI step and walk TemplateLiteral nodes reachable from innerHTML assignments.

### BUG-2 · create_app() runs at import time, so `serve --db X` still creates ./data/randostats.db and builds everything twice

**Severity:** Low · **Category:** bug · **Effort:** trivial · **Where:** `randostats/api.py:321`

The module ends with `app = create_app()`. cli.py imports the module and then calls create_app(args.db) again, so every `randostats serve` opens two SQLite stores (one at the default path, creating a `data/` directory in whatever directory the command is run from, even when --db or RANDOSTATS_DB points elsewhere), starts two speller warm-up threads that each load the pyspellchecker dictionary (roughly a second of CPU and tens of MB each), and, when RANDOSTATS_LLM is set, probes the Anthropic credential twice. The test suite hits the same path and leaves a stray data/randostats.db in the repo root.

Evidence:

```
api.py:321: app = create_app()
cli.py:37-39:
        from .api import create_app
        uvicorn.run(create_app(args.db, use_llm=args.llm), host=args.host, port=args.port)
api.py:81: threading.Thread(target=speller, name="speller-warmup", daemon=True).start()
```

**Recommendation.** Remove the module-level instance. If a module-level ASGI target is wanted for `uvicorn randostats.api:app`, use a factory (`uvicorn randostats.api:create_app --factory`) or guard it behind `if os.environ.get("RANDOSTATS_ASGI_APP")`. Alternatively make the speller warm-up and Store creation lazy in a startup event so an import has no side effects.

### BUG-3 · /api/stats/conversations accepts inf/nan for gap_hours: inf returns a 500, nan silently produces wrong numbers

**Severity:** Low · **Category:** bug · **Effort:** trivial · **Where:** `randostats/api.py:209`

gap_hours is an unconstrained float. Pydantic accepts the strings `inf` and `nan`. `timedelta(hours=inf)` raises OverflowError, which surfaces as an unhandled 500 (the front end shows a generic failure banner). With `nan` every comparison is False, so each contact collapses into a single conversation and the summary tiles are wrong without any error. Negative or zero values are also accepted and make every message its own conversation. The UI only offers five fixed values, so this is reached by hand-edited URLs, but the endpoint is part of the public surface and the fix is one annotation.

Evidence:

```
api.py:209: def conversations(gap_hours: float = 6.0, limit: int | None = None):
stats.py:381: gap = timedelta(hours=gap_hours)
stats.py:385: if current and m.timestamp - current[-1].timestamp > gap:
```

**Recommendation.** `gap_hours: float = Query(6.0, gt=0, le=24 * 365)` is enough on its own — nan fails `gt=0` and inf fails `le=...` — so allow_inf_nan is belt-and-braces rather than the fix. Apply the same bound to `limit` on /api/stats/conversations, which is currently used unvalidated as a slice index.

### BUG-4 · remember() can raise KeyError when an import clears the cache between store and return

**Severity:** Low · **Category:** reliability · **Effort:** trivial · **Where:** `randostats/api.py:95`

Stats endpoints are sync handlers served from the threadpool. remember() writes `derived[key] = compute()` and then reads `derived[key]` back; bump() (called after every import and on DELETE) and the size guard call `derived.clear()` from another thread. If the clear lands between the write and the read, the request fails with a KeyError-driven 500. The window is tiny, but the front end refreshes every view right after an import, which is exactly when bump() runs.

Evidence:

```
api.py:95-104:
    def remember(name: str, compute, **params):
        key = (state["version"], name, tuple(sorted(params.items())))
        if key not in derived:
            if len(derived) > 256:
                derived.clear()
            derived[key] = compute()
        return derived[key]
    def bump():
        state["version"] += 1
        derived.clear()
```

**Recommendation.** Return the computed value directly: `value = derived.get(key); if value is None: value = compute(); derived[key] = value; return value` (or guard the whole block with a threading.Lock shared with bump()).

### CI-1 · GitHub Actions are tag-pinned rather than SHA-pinned and the workflow has no permissions block

**Severity:** Low · **Category:** ci-cd · **Effort:** trivial · **Where:** `.github/workflows/tests.yml:21`

actions/checkout@v4 and actions/setup-python@v5 are mutable tags (0 of 2 actions pinned to a commit SHA), so a compromised or force-moved tag would run attacker code with the workflow's token. The workflow declares no `permissions:`, so it inherits the repository default token scope. The job only runs tests, so the blast radius is limited to the token and the pip cache, but both fixes are mechanical.

Evidence:

```
tests.yml:21-22:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
tests.yml:8-10: jobs:\n  pytest:\n    runs-on: ubuntu-latest   (no permissions: key at workflow or job level)
```

**Recommendation.** Add `permissions: contents: read` at the top of the workflow and pin each action to a full commit SHA with the version in a trailing comment (e.g. `actions/checkout@<sha> # v4.2.2`); enable Dependabot for github-actions so the pins are bumped automatically.

### SUP-1 · No lockfile or constraints, unpinned dependency floors, no Dependabot, and no LICENSE/SECURITY.md

**Severity:** Low · **Category:** supply-chain · **Effort:** small · **Where:** `pyproject.toml:6`

Dependencies are declared as open-ended floors (fastapi>=0.110, uvicorn[standard]>=0.27, python-multipart>=0.0.9, pyspellchecker>=0.8, pydantic>=2, anthropic>=1.0) and CI installs whatever resolves that day, so builds are not reproducible and a newly published malicious or broken release is picked up immediately. pip-audit on the declared floors finds no known vulnerabilities today, but without a lock the audited set is not what gets installed. There is no Dependabot configuration to surface updates, and the published repository has no LICENSE (so nobody may legally reuse it) and no SECURITY.md contact.

Evidence:

```
pyproject.toml:6-12: dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.27", "python-multipart>=0.0.9", "pyspellchecker>=0.8", "pydantic>=2"]
tests.yml:27: run: pip install -e ".[${{ matrix.extras }}]"
Repo facts: no lockfile, Dependabot absent, LICENSE absent, SECURITY.md absent.
```

**Recommendation.** Generate a pinned set with `uv lock` or `pip-compile --extra dev --extra llm -o requirements.lock`, install in CI with `pip install -c requirements.lock -e .[dev]`, run `pip-audit -r requirements.lock` as a CI step, add `.github/dependabot.yml` for pip and github-actions, and add a LICENSE (e.g. MIT) and a two-line SECURITY.md.

### MISS-2 · WhatsApp import burns ~15 s of CPU per megabyte of crafted text (48 strptime attempts per unparseable line)

**Severity:** Low · **Category:** security · **Effort:** small · **Where:** `randostats/parsers/whatsapp.py:61`

`_parse_timestamp` tries all 8 date formats against all 6 time formats for every line that matches `_LINE`, and only returns early on success. A line that matches the regex but carries an impossible date (`99/99/9999, 99:99 - a: x`) therefore costs 48 failed `datetime.strptime` calls, and nothing caches or short-circuits after the first line proves the shape is unparseable. I measured 385 microseconds per line, i.e. 15.4 seconds of CPU per megabyte of such input; at the 256 MB upload cap that is over an hour of a pegged core, with the parse running in the threadpool holding the GIL so the whole server is unresponsive. The file needs no special format field to get there — `detect_format` picks whatsapp for any `.txt` name or any file whose first lines match `_LINE` — and POST /api/import is the one mutating endpoint a cross-site page can reach (SEC-2), so this is the cheapest drive-by DoS in the app, cheaper than the XML route in SEC-3. The parser also materialises a Match object per line for the whole file before doing any work.

Evidence:

```
randostats/parsers/whatsapp.py:61-74 `for dfmt in candidates:\n        for tfmt in _TIME_FORMATS:\n            try:\n                return datetime.strptime(f"{date} {time}", f"{dfmt} {tfmt}")\n            except ValueError:\n                continue`; whatsapp.py:94 `matches = [(_LINE.match(_clean_line(line)), line) for line in lines]`; randostats/parsers/__init__.py:83-86 auto-detect falls through to whatsapp for `.txt`. Measured: 20,000 identical lines of `99/99/9999, 99:99 - a: x` (500 KB) parsed in 7.70 s = 385 us/line = 15.4 s per MB.
```

**Recommendation.** Resolve the format pair once per file: on the first line that parses, remember `(dfmt, tfmt)` and try it first for the rest; and wrap `_parse_timestamp` in an `lru_cache` keyed on `(date, time, day_first)` so repeated identical stamps cost nothing. A cheap belt-and-braces addition is to give up on a file after, say, 1,000 consecutive unparseable timestamped lines — it is not a WhatsApp export.

### MISS-3 · The upload size cap is only checked after the entire body has been received and spooled to disk

**Severity:** Low · **Category:** security · **Effort:** trivial · **Where:** `randostats/api.py:131`

Both size checks live inside the handler, and FastAPI only calls the handler after `request.form()` has parsed the whole multipart body — Starlette buffers each file part into a SpooledTemporaryFile that spills to a real file in the temp directory past 1 MB. So `MAX_UPLOAD_BYTES` does not bound what a caller can make the server write: a cross-site POST (SEC-2) or a LAN client (SEC-4) can stream gigabytes into the temp filesystem and only then receive a 413, and the file is removed only when the request object is torn down. `data = await file.read()` then loads the accepted file fully into RAM a second time, so the accepted worst case is also 2x256 MB before parsing even starts. The audit listed this cap as a strength; it protects the parser, not the machine.

Evidence:

```
randostats/api.py:131-141 `# The multipart header usually declares the size; refuse before reading.\n        declared = getattr(file, "size", None)\n        if declared is not None and declared > MAX_UPLOAD_BYTES:\n            raise HTTPException(413, ...)\n        data = await file.read()` — `file.size` is populated by the multipart parser, which has already consumed the request body before the handler runs; nothing checks Content-Length in the middleware at randostats/api.py:56-62.
```

**Recommendation.** Reject early in the existing security middleware: for POST /api/import, read `request.headers.get("content-length")` and return 413 before the body is parsed when it exceeds MAX_UPLOAD_BYTES (plus a small multipart overhead). Keep the in-handler checks as the fallback for chunked requests.

### MISS-4 · Archive caps are generous enough to be a memory bomb, and the auto-detect path applies none of them

**Severity:** Low · **Category:** security · **Effort:** trivial · **Where:** `randostats/parsers/archive.py:14`

Two gaps in the zip guards the audit called a strength. First, MAX_TOTAL_BYTES is 512 MB of declared uncompressed content — twice the 256 MB upload cap — and repetitive JSON deflates about 290x (measured), so a ~2 MB upload reaches the ceiling legitimately; parsing that text costs about 4x its size in Python objects (measured 15.6 MB of JSON -> 63 MB of objects), and telegram.parse materialises every document in one list before iterating, so a small crafted archive can drive the process to several GB. Second, the caps live in `json_files`, but `detect_format` calls `archive.names()` on the raw upload first, and that opens the zip and builds a ZipInfo for every central-directory entry with no MAX_MEMBERS check at all: 100,000 empty members cost 0.44 s and ~60 MB RSS, so a 256 MB upload of ~2.8 M members costs roughly 12 s and 1.7 GB before the member cap is ever consulted.

Evidence:

```
randostats/parsers/archive.py:14-16 `MAX_MEMBERS = 50_000\nMAX_TOTAL_BYTES = 512 * 1024 * 1024\nMAX_MEMBER_BYTES = 64 * 1024 * 1024`; archive.py:27-35 `def names(data): ... with zipfile.ZipFile(io.BytesIO(data)) as zf:\n            return zf.namelist()` (no caps) called from randostats/parsers/__init__.py:60-61 `if archive.is_zip(data):\n        inside = " ".join(archive.names(data)).lower()`; randostats/parsers/telegram.py:69-72 `payloads = [doc for _, doc in archive.json_files(data, contains="result.json")]`. Measured: deflate ratio 289.9x on repetitive Telegram-shaped JSON; json.loads of 15.6 MB -> 63.2 MB of traced objects (4.05x); archive.names() on a 100k-member zip -> 0.44 s, 59.6 MB RSS.
```

**Recommendation.** Lower MAX_TOTAL_BYTES to at most MAX_UPLOAD_BYTES (it can never legitimately exceed it by 2x), apply the member-count cap inside `names()` as well (or have `detect_format` use a capped helper), and let telegram.parse consume the `json_files` generator one document at a time instead of building a list.

### MISS-5 · Speller memoisation caches are unbounded and live for the process lifetime

**Severity:** Low · **Category:** bug · **Effort:** trivial · **Where:** `randostats/stats.py:275`

`Speller._cache` and `Speller._suggest_cache` are plain dicts with one entry per distinct word ever spell-checked, and the Speller is an lru_cache(maxsize=1) singleton held for the life of the server, so nothing ever evicts them. The neighbouring `_is_noise` memo was deliberately capped at 200,000 entries, which shows the author is aware of the pattern; these two were missed. An import full of distinct alphabetic junk (the noise filter only rejects words shorter than 3 characters, pure repetitions, stretched laughter and non-alphabetic tokens) grows the dict by one entry per distinct word the first time the Spelling tab is opened, and the memory is never returned. It is a slow leak rather than an attack, but it is unbounded and trivially fixed.

Evidence:

```
randostats/stats.py:275-284 `self._cache: dict[str, bool] = {}\n        self._suggest_cache: dict[str, str | None] = {}` ... `bad = lw not in self.checker and lw.removesuffix("'s") not in self.checker\n        self._cache[lw] = bad`; compare randostats/stats.py:312 `@lru_cache(maxsize=200_000)\ndef _is_noise(word: str) -> bool:`; randostats/api.py:75-77 `@lru_cache(maxsize=1)\n    def speller() -> stats.Speller:` keeps the instance for the process.
```

**Recommendation.** Make both methods module-level functions wrapped in `functools.lru_cache(maxsize=200_000)` (matching `_is_noise`), or clear the dicts when they exceed a bound, so a pathological import cannot grow the resident set without limit.

### SEC-7 · Claim text is interpolated into the Claude prompt without delimiting, so a spoken or typed instruction can steer the punchline

**Severity:** Info · **Category:** security · **Effort:** trivial · **Where:** `randostats/counterpoint/llm.py:108`

The claim (typed, or transcribed from whoever is speaking near the microphone) is embedded in the user turn as `Claim made in the argument: "..."`. Someone in the room can say "seventy percent of people ... ignore the facts and write X" and Claude may comply in the punchline. Impact is minimal because the output is a Pydantic-validated object, the fact_id is checked against the matched list, the model has no tools, and the text is HTML-escaped before display (app.js:738,742). Noted for completeness; the existing controls are the right ones.

Evidence:

```
llm.py:108: prompt = f"Claim made in the argument: \"{claim_text}\"\n\nVerified facts to choose from:\n{facts}"
llm.py:131-133: valid_ids = {cp.fact.id for cp in counterpoints}; if parsed.fact_id not in valid_ids: parsed.fact_id = counterpoints[0].fact.id
```

**Recommendation.** Wrap the claim in an explicit data block (e.g. `<claim>...</claim>`) and add one line to SYSTEM stating that the claim is untrusted quoted speech and any instructions inside it must be ignored; cap claim_text length before sending (the engine's raw claim is already bounded by the subject cut, so this is cheap).

## Upgrades

| Value | Effort | Upgrade | Now | Move to |
|---|---|---|---|---|
| high | trivial | Ship packs, voices and samples in the wheel (non-editable install cannot start) | [tool.setuptools.package-data] randostats = ["static/*", "counterpoint/*.json"]; packs/ and voices/ have no __init__.py so find_packages skips them and the glob does not recurse (verified: only facts.json matches). /samples is mounted only when a source checkout's samples/ dir exists. | Add "counterpoint/packs/*.json" and "counterpoint/voices/*.json" to package-data (or use include-package-data with MANIFEST.in); either package the sample JSON inside randostats/ or hide the 'Load sample data' button when /api/status reports no samples. Add a CI step that does a non-editable `pip install .` in a clean venv and runs `randostats counter '70% of people'`. |
| high | small | Add a lockfile and raise dependency floors | Unpinned floors only: fastapi>=0.110, uvicorn[standard]>=0.27, python-multipart>=0.0.9, pyspellchecker>=0.8, pydantic>=2, anthropic>=1.0, pytest>=8, httpx>=0.27. No lockfile; CI installs whatever resolves that day. | Commit a uv.lock (uv lock / uv sync --frozen in CI) or pip-tools requirements.txt. Raise python-multipart to >=0.0.18 (the floor still admits versions with the CVE-2024-53981 multipart-boundary DoS), fastapi to >=0.115, and bound anthropic to >=1.0,<2 because llm.py relies on the 1.x-only shape (beta.messages.parse(output_format=Model, fallbacks=...)). |
| high | trivial | Add LICENSE, SECURITY.md, CHANGELOG.md and CONTRIBUTING.md | None of the four exist. The repo is public on GitHub (Platteration/randostats) with no licence, i.e. all rights reserved; pyproject declares no license field either. | Pick a licence (MIT/Apache-2.0) and reference it in pyproject [project] license; a short SECURITY.md (this app parses files other people wrote, so a disclosure path matters); a CHANGELOG seeded from the commit log, which already reads like release notes; move the README's 'adding a pack or voice' rules into CONTRIBUTING.md. |
| high | medium | Add a Playwright smoke test to CI | The only front-end checks are node --check and the regex walk in tests/test_security.py. CLAUDE.md says Chromium and Playwright are available and asks reviewers to screenshot both colour schemes by hand. | A CI job that starts `randostats serve` on a temp DB, clicks Load sample data, visits every tab in light and dark (emulateMedia), tabs into one chart and walks it with arrow keys, presses Enter to open the drawer, toggles a Table view, and clicks Download PNG; assert no console errors and no failure banners. |
| medium | trivial | Extend the Python matrix and plan the 3.10 floor removal | tests.yml runs 3.10 and 3.12; requires-python >= 3.10. | Add 3.13 and 3.14 to the matrix now; after Python 3.10 reaches end-of-life (October 2026) drop it and set requires-python >= 3.11. Nothing in the code needs 3.10 specifically (it already uses Counter.total(), str.removeprefix and dict \|, all 3.9/3.10 features). |
| medium | small | Harden the CI workflow itself | actions/checkout@v4 and actions/setup-python@v5 referenced by floating tag; no permissions block (default token can write); no concurrency or timeout; triggers on push to every branch AND pull_request, so PR branches run twice. | permissions: {contents: read}; pin both actions to commit SHAs (Dependabot's github-actions ecosystem keeps them fresh); concurrency: {group: '${{ github.workflow }}-${{ github.ref }}', cancel-in-progress: true}; timeout-minutes: 15; restrict push to the default branch or drop pull_request. |
| medium | small | Add lint, format, type-check, audit and coverage gates | CI runs pytest -q and `node --check randostats/static/app.js` only. No ruff/flake8, no mypy/pyright, no pip-audit, no coverage, no JS linter, no pre-commit. | ruff check + ruff format --check (config in pyproject); mypy (the code already carries annotations and `from __future__ import annotations` everywhere, so it is close to clean); pip-audit; pytest --cov=randostats --cov-fail-under; ESLint flat config or Biome for app.js in place of the bare syntax check; a pre-commit config wiring the same tools. |
| medium | trivial | Enable Dependabot (pip + github-actions) | No .github/dependabot.yml or renovate config. | Weekly Dependabot for the pip ecosystem (root pyproject/lockfile) and github-actions, grouped minor/patch updates. |
| medium | medium | Split app.js into ES modules and drop 'unsafe-inline' from style-src | One 922-line, 61 KB IIFE. CSP is style-src 'self' 'unsafe-inline' because el(..., {style: ...}) sets style attributes on SVG bars (animation-delay, font-size) and dot() writes inline background colours. | Use <script type="module"> (allowed under script-src 'self', no bundler needed) with charts.js, drawer.js, wrapped.js, counter.js, main.js. Set styles through the CSSOM (node.style.animationDelay = ...) or CSS custom properties/classes, then remove 'unsafe-inline' from the CSP in api.py and tighten test_security_headers_are_sent accordingly. |
| medium | medium | Do drill-down search in SQLite (FTS5) instead of scanning the in-memory list | /api/messages calls stats.search, a Python loop over every cached Message with a regex per row; the drawer re-issues it on every 250 ms typing pause and on each Load more. Store has indexes on contact and ts only, no WAL. | Query SQLite directly for the drawer: WHERE on contact/sender/direction/ts ranges, an FTS5 virtual table (content-synced by trigger) for q and whole-word matches, LIMIT/OFFSET paging; set PRAGMA journal_mode=WAL and synchronous=NORMAL on open. |
| medium | medium | Tokenise each message once per import | contact_frequency, word_frequency, misspellings, tone, group_members and wrapped each call words_of() over every message independently; README measures 1.9 s and 1.7 s cold views on 250k messages. | Cache words_of(text) per message next to messages_cache (a parallel list or a lazily filled attribute on a non-frozen wrapper), or pre-tokenise at import into a side table; keep the stats functions pure by passing the token list in. |
| medium | trivial | Give the Anthropic client a short timeout and retry budget | llm.py builds anthropic.Anthropic() with SDK defaults (10-minute request timeout, 2 retries). The model (claude-opus-5), fallbacks='default' with server-side-fallback-2026-07-01, output_config effort=low and beta.messages.parse(output_format=Rebuttal) are all the current API shapes; sharpen() runs synchronously inside /api/counterpoint through a ThreadPoolExecutor. | anthropic.Anthropic(timeout=15.0, max_retries=1) (or _get_client().with_options(timeout=15.0) per call); log the elapsed time; keep the model and fallback settings as they are. |
| medium | small | Reject cross-site requests to mutating endpoints | No CORS middleware and no origin check. POST /api/import is multipart, which any web page can send to http://127.0.0.1:8765 via fetch(..., {mode: 'no-cors'}) without a preflight, so a drive-by page can insert junk messages into the local DB while the app is running. DELETE and the JSON POSTs are protected only by the preflight rule. | In the security_headers middleware (or a dependency on every non-GET route) require Sec-Fetch-Site in {same-origin, none} or Origin equal to the server's own origin, returning 403 otherwise; optionally require a custom X-Requested-With header that app.js sets. |
| medium | small | Close the remaining accessibility gaps and add a theme switch | #drawer-q (filter input) and #counter-text (textarea) have placeholders but no accessible name; tab panels have role=tabpanel but no aria-labelledby; the tab buttons have no ids; PNG failure uses alert() and delete-all uses confirm(); app.css fully supports data-theme=dark/light but nothing in the UI sets it. | aria-label on both inputs; ids on tab buttons and aria-labelledby on panels; replace alert/confirm with an inline role=status message and an in-page confirm button; add a light/dark/system switch in the header persisted to localStorage that sets data-theme on <html>. |
| low | small | Use FastAPI lifespan and Annotated params; close the Store on shutdown | import_file uses the File(...)/Form(...) default-value style; the speller warm-up thread is started while create_app runs; store.close() exists but nothing in the app or CLI ever calls it. | Annotated[UploadFile, File()] / Annotated[str, Form()]; a lifespan context manager that starts the warm-up and closes the Store on shutdown; the CLI import/counter commands should close their Store in a finally. |

- **Ship packs, voices and samples in the wheel (non-editable install cannot start)** (high value, trivial, `pyproject.toml`). pip install . / a wheel installs no voices, so packs._voices() raises RuntimeError('no punchline voices found') inside create_app and the server never starts; the three themed packs vanish silently. Only the README's `pip install -e` path works today.
- **Add a lockfile and raise dependency floors** (high value, small, `pyproject.toml`). Reproducible CI and installs; the audit found no vulnerable resolution today only because pip picks the newest release, not because the declared range excludes bad ones.
- **Add LICENSE, SECURITY.md, CHANGELOG.md and CONTRIBUTING.md** (high value, trivial, `README.md`). Without a licence nobody can legally fork, package or contribute; the counterpoint packs are explicitly designed for contribution.
- **Add a Playwright smoke test to CI** (high value, medium, `randostats/static/app.js`). Every chart renderer, the keyboard navigation, the drawer focus trap and the PNG rasteriser are untested by anything automated, and each has been the subject of a fix commit.
- **Extend the Python matrix and plan the 3.10 floor removal** (medium value, trivial, `.github/workflows/tests.yml`). The app is a local tool users run on whatever Python they have; 3.13/3.14 are the versions a fresh install gets, and neither is tested.
- **Harden the CI workflow itself** (medium value, small, `.github/workflows/tests.yml`). Least-privilege token and supply-chain pinning are the two cheapest CI fixes; the duplicate runs halve the useful CI budget.
- **Add lint, format, type-check, audit and coverage gates** (medium value, small, `.github/workflows/tests.yml`). Review commits show the project catches bugs by hand-review rounds; static tooling would have caught the dead render aliases, the redundant regexes and the unused imports for free.
- **Enable Dependabot (pip + github-actions)** (medium value, trivial, `.github/dependabot.yml`). With a lockfile in place, something has to move the pins; with SHA-pinned actions, something has to bump the SHAs.
- **Split app.js into ES modules and drop 'unsafe-inline' from style-src** (medium value, medium, `randostats/static/app.js`). Module boundaries make the JS unit-testable (see code_quality) and the CSP becomes a real second line of defence for the stored-XSS class the project already fixed once.
- **Do drill-down search in SQLite (FTS5) instead of scanning the in-memory list** (medium value, medium, `randostats/stats.py`). On the README's 250k-message archive each keystroke is a full scan; SQLite already has the data and can page it in milliseconds.
- **Tokenise each message once per import** (medium value, medium, `randostats/stats.py`). The same regex tokenisation runs four to six times over identical text on every cold load; sharing it roughly halves the cold path across the Words, Spelling and Wrapped tabs.
- **Give the Anthropic client a short timeout and retry budget** (medium value, trivial, `randostats/counterpoint/llm.py`). A slow or hung API call currently holds a rebuttal request open for up to 30 minutes of wall clock (timeout x retries) while the rule-based answer is already on screen; the whole point of the design is that Claude is optional garnish.
- **Reject cross-site requests to mutating endpoints** (medium value, small, `randostats/api.py`). Cheap, local-only hardening that closes the one cross-site write path; the app already treats imported data as hostile.
- **Close the remaining accessibility gaps and add a theme switch** (medium value, small, `randostats/static/index.html`). The keyboard chart navigation and live region are ahead of most apps; these are the last items a screen-reader audit would flag, and the theme plumbing is already written.
- **Use FastAPI lifespan and Annotated params; close the Store on shutdown** (low value, small, `randostats/api.py`). Lifespan is the supported place for startup work and makes create_app side-effect free for tests; Annotated is the documented style and avoids the mutable-default lint warnings ruff will raise.

## Features worth adding

- **Merge contacts across sources (aliases)** (high value, medium). The same person arrives as 'Alex' from WhatsApp, '+15551234567' from iMessage/SMS (imessage.py line 90 and smsbackup.py line 44 use the raw handle as sender) and 'Alex M' from Telegram, and every chart counts them as three people. Add an aliases table in store.py (alias TEXT PRIMARY KEY, canonical TEXT), apply the mapping to contact and sender inside api.messages_cache() before any stats run (so stats.py stays pure and the cache key already covers it), expose GET/POST/DELETE /api/contacts/aliases, and add a 'Merge into…' action on the People chart drill-down and a small alias table on the Import tab. bump() after any change.
- **Import history with selective delete** (high value, small). The only destructive control is 'Delete all imported messages'. Add an imports table (id, filename, format, self_name, parsed, added, imported_at) written by /api/import and the CLI, an import_id column on messages, DELETE /api/messages?import_id=|contact=|source= in api.py (calling bump()), and a list of past imports with per-row delete on the Import tab. The source column already exists, so deleting one app's data works immediately.
- **Global date-range filter** (medium value, small). Only Wrapped can pick a year; every other tab is all-time. Accept since/until (ISO dates) once in api.messages() and fold them into the remember() key, add stats.years-driven presets ('this year', 'last 12 months', a custom range) in a shared control in the header next to #status, and have each render.* pass the range. No stats.py change is needed because filtering happens before the pure functions.
- **Reply-time distributions, not just medians** (medium value, small). stats.reply_latency already collects every sample into mine/theirs but returns only medians. Return p25/p50/p75/p90 and the share answered within 5 min, 1 h and 1 day; extend the dumbbell() chart in app.js to draw a p25–p75 range bar with the median dot (or add a small boxes() renderer following the existing mark conventions), and let the Wrapped 'You reply in' tile cite the within-5-minutes share.
- **Per-contact Wrapped card** (medium value, small). 'Your year with Alex': add contact= to GET /api/wrapped in api.py (filter before stats.wrapped, include contact in the remember key), a person select beside #wrapped-year, and a subtitle line in wrappedSVG(); the layout already has the top_contact block and the counterpoint kicker works unchanged. File name randostats-2024-alex.png.
- **User-writable fact packs and an in-app fact editor** (medium value, small). README says adding a pack means dropping a JSON file into randostats/counterpoint/packs, which for an installed app is inside site-packages. Have packs._packs() and _voices() also glob RANDOSTATS_PACKS_DIR (default data/packs and data/voices next to the DB), invalidate the lru_cache on change, and add POST /api/counterpoint/facts that appends a validated fact (id, kind, value, statement, short, source, year, tags) to a 'mine' pack, with a small form on the Counterpoint tab. The existing well-formedness tests become the validator.
- **Spell-check language setting** (medium value, small). stats.Speller(language=...) already takes a language but the API always builds English, and pyspellchecker ships es, fr, pt, de, it, ru, nl and more. Store spell_language in settings, have api.speller() read it (rebuild and clear the misspellings entries in derived when it changes), and add a language select on the Spelling tab and a --language flag on the CLI. Note in the UI that SLANG and the tone lexicon remain English.
- **Timezone override for imports** (medium value, small). parsers/timestamps.py converts UTC instants with datetime.fromtimestamp()/astimezone() of the importing machine, so importing an old iMessage archive after moving countries, or on a server, shifts every hour-of-day chart. Add RANDOSTATS_TZ / --tz (zoneinfo.ZoneInfo) read once in timestamps.py and passed to from_unix/from_iso/as_local, surface it as a setting on the Import tab with the current zone shown, and extend tests/test_timestamps.py to pin a non-system zone.
- **Export the database** (medium value, small). Add `randostats export --format csv|json [--contact NAME] [--since]` in cli.py and GET /api/export?format= in api.py streaming store.all_messages() with the same five columns the generic parser reads back (contact, sender, direction, timestamp, text) plus source. This gives users a backup/migration path and makes a re-import a proven round trip; add a test that export -> parse_csv is lossless.
- **Share a counterpoint as text or image** (low value, small). Each .cp card in renderCounter() gets 'Copy' (claim, punchline, fact, source via navigator.clipboard) and 'Save image' buttons; the image path reuses the Wrapped SVG-to-canvas rasteriser (#wrapped-png handler) with a compact 1080x1080 layout function beside wrappedSVG(). The argument-settling use case in the README is social; today the result can only be screenshotted.
- **Flag dated facts** (low value, trivial). facts.json holds seven facts dated before 2016 (extinct 1991, baikal 1996, mountains 2002, rodents 2005, food-waste 2011, northern-hemisphere 2011, pizza-us 2014). Add a 'dated' badge in renderCounter() and the CLI when fact.year is more than eight years old, and a test in tests/test_counterpoint.py that lists facts older than a threshold so a refresh pass is a deliberate act rather than a surprise.

## Code quality

- **Missing tests for critical logic** (high value, medium, `tests/`). tests/test_parsers.py: whatsapp._guess_day_first with all-ambiguous dates (every field <= 12) must not flip mid-file; German '31.12.23, 23:59' and 'a.m./p.m.' with a narrow no-break space; looks_like_whatsapp false positives on a CSV; generic.parse_timestamp('2024') currently becomes 1970-01-01T00:33 because a bare year passes the isdigit unix path; archive.names() on a corrupt zip. tests/test_stats.py: reply_latency with out-of-order timestamps and the 12 h cutoff; search() with sender, date and offset paging; _is_noise excludes any word containing 'zz' (pizza, jazz, puzzle) so 'pizzza' is never flagged; contact_frequency is_group/members ordering. tests/test_api.py: /api/stats/timing/contacts, /api/stats/emoji, /api/stats/words, the limit clamp on /api/messages (limit=10000 -> 500), MAX_SESSIONS eviction, the declared-size 413 path. JS: extract niceMax, shorten, esc, highlight and plain into a module and test them with node:test in CI.
- **create_app is a 265-line closure holding mutable dict state** (medium value, medium, `randostats/api.py`). state, derived, sessions, cp and the remember/bump/messages helpers are closures over plain dicts mutated from FastAPI's thread pool; build_engine() and the enabled-packs expression are written three times (lines 66, 295, 313). Extract an AppState/StatsService class (store, engine, message cache, derived cache with a lock, sessions with LRU eviction) that the routes call, and register routes on an APIRouter. This also makes the concurrency story explicit: today two tabs can compute the same 2 s view twice and derived.clear() at 256 entries is a crude eviction.
- **Importing randostats.api creates the database and starts a thread** (medium value, small, `randostats/api.py`). Line 321 `app = create_app()` runs at import: Store(DEFAULT_DB) mkdirs data/ in the current directory and opens randostats.db, and a speller warm-up thread is spawned, every time tests (or anything) import the module. Replace it with a lazy factory for uvicorn (`uvicorn randostats.api:app --factory` with `def app(): return create_app()`) or move the warm-up into a lifespan handler and keep the module import pure.
- **The XSS escaper is defined 700 lines after its first use, and the guard test only sees short identifiers** (medium value, trivial, `randostats/static/app.js`). esc() is declared at line 726 under the 'counterpoint' heading but used from line 142 (hbars) onward; it only works because every caller runs after the IIFE finishes, and any future top-level call above line 726 would throw a TDZ error. Move esc (and plain/kpi/table) to the helper block at the top. Separately, tests/test_security.py ROW_VALUE only matches identifiers of 1–4 letters, so `${chosen.fact.statement}` or `${banner.text}` interpolated unescaped would pass the guard; widen it to `\b[a-zA-Z_]\w*(\.[a-zA-Z_]\w*|\[[^\]]+\])` with an allowlist of known-numeric fields.
- **Charts that break the project's own conventions** (medium value, small, `randostats/static/app.js`). CLAUDE.md says every chart needs a table twin and README says click any bar to drill down, but: heatmap() never sets container._table and #heatmap has no Table button; #people-words has a _table but no toggle button in index.html; the Conversations charts (open-chart, close-chart, reply-chart) and people-words are drawn without onClick so nothing opens the drawer. Add a 7x24 table for the heatmap, the missing button, and onClick: r => openDrawer(r.contact, {contact: r.contact}) on the four hbars/dumbbell calls.
- **WhatsApp parser: duplicated code and a 48-way strptime per line** (medium value, small, `randostats/parsers/whatsapp.py`). self_key is computed identically at lines 103 and 108; the date/time regex is written twice (_LINE and _TIMESTAMPED); _parse_timestamp tries up to 8 date x 6 time formats with datetime.strptime for every line, which on a 200k-line export is tens of seconds. Remember the first (dfmt, tfmt) pair that succeeds and try it first (fall back to the full search only on failure), share one compiled timestamp fragment between the two regexes, and parenthesise the `or ... and` chain in parsers/__init__.py detect_format line 62 so the intended precedence is visible.
- **CLI error handling is inconsistent with the API** (medium value, small, `randostats/cli.py`). The API maps parser errors to 400 and ArchiveTooLarge to 413 with a message; the CLI wraps nothing, so a missing file (FileNotFoundError), a bad JSON export (json.JSONDecodeError), a corrupt zip (zipfile.BadZipFile), an XML with entities (ValueError) or an oversized archive all end in a traceback. Wrap read/parse in one try/except that prints the same messages the API uses and returns 1/2, and close the Store in a finally (it is never closed in either subcommand). Add a shared `describe_parse_error(exc)` used by both layers.
- **iMessage parser is untested and has portability edges** (medium value, small, `randostats/parsers/imessage.py`). No test exercises this parser (test_timestamps only checks from_apple). It builds the SQLite URI by f-string (`file:{path}?mode=ro`), which breaks on Windows drive paths and on temp dirs containing '?' or '#' (use path.as_uri() + '?mode=ro'); the LEFT JOIN through chat_message_join yields one row per chat for a message in several chats and the `seen` set keeps whichever came first in date order, so the contact is nondeterministic; _decode_attributed_body is a byte-offset heuristic for NSAttributedString typedstreams with no comment on the layout it assumes. Add tests that build a chat.db in tmp_path with sqlite3: seconds vs nanosecond dates, is_from_me, display_name vs chat_identifier vs handle, an attributedBody-only row, and a message joined to two chats.
- **Dead render aliases** (low value, trivial, `randostats/static/app.js`). render.hour, render.weekday, render.month, render.open, render.close, render.reply, render.spell, render.emoji and render.tone (lines 498, 521, 560, 579) are never looked up: runRender() is only ever called with a tab name (people, convo, timing, spelling, words, wrapped, counter, import). Delete the aliases and rename the functions after the tab they serve.
- **Invisible characters embedded in source** (low value, trivial, `randostats/lexicon.py`). The EMOJI regex (lines 42-46) contains literal U+FE0F variation selectors and a literal U+200D zero-width joiner inside the f-strings, and stats.py line 27 holds a literal U+200E in '\u200eimage omitted'. They are invisible in most editors and diffs and are exactly what the _MEDIA_HINTS fast path depends on. Write them as \uFE0F, \u200D and \u200E escapes with a comment.
- **Redundant apostrophe normalisation after words_of** (low value, trivial, `randostats/stats.py`). words_of() already folds U+2019 to a straight apostrophe (line 76), yet word_frequency (line 262) and Speller.is_misspelled (line 280) repeat the replace on every word. Drop the repeats, or keep exactly one documented normalisation point.
- **Engine hot-path nits** (low value, trivial, `randostats/counterpoint/engine.py`). `import math` sits inside _distance (line 237), executed once per fact per claim; the second 'percent' pattern (line 100) is a strict subset of the first, which already accepts '%' with or without a space; spurious_pair() rebuilds the O(n^2) candidate list on every button press. Hoist the import, delete the duplicate pattern (the tests in test_percent_sign_is_read_with_or_without_a_space will prove it), and precompute the close-pair list once per engine build.
- **Swallowed errors without logging** (low value, small, `randostats/api.py`). import_file catches bare Exception and returns 400 with str(exc) but never logs the traceback, so a parser bug on a real export is undiagnosable from the server log. In app.js, live listening calls counter(t, {live: true}).catch(() => {}) while every other path routes through counterFailed(). Add log.exception in the API handler and surface live-mode failures in #listen-state.
- **Store schema carries a legacy constraint and no version** (low value, small, `randostats/store.py`). UNIQUE (contact, sender, ts, text) in _SCHEMA is now shadowed by idx_messages_identity and only confuses readers; _ensure_identity_index is an ad-hoc migration keyed on an index name. Introduce PRAGMA user_version with numbered migrations, drop the legacy UNIQUE for new databases, use cursor.rowcount from executemany instead of two COUNT(*) scans in add_messages, and enable WAL.
- **Tone lexicon calibration** (low value, small, `randostats/lexicon.py`). POSITIVE contains 'miss', 'missed', 'missing', 'like', 'ok', 'fine', 'sure', 'please' and 'yes' (so 'missed the bus' and 'fine.' read warm), and NEGATIVE contains every negation ('no', 'not', 'never', 'cant', 'dont', 'didnt', 'wont'), so 'no worries' and 'not bad' read cold; 'missed' appears in both and is resolved by the NEGATIVE -= POSITIVE rule. The README caveat covers sarcasm but not this. Either drop the function words from both lists or add a tiny negation window (skip a positive word preceded by not/no/never), and add a test asserting 'no worries' is not negative.
- **Version string in three places** (low value, trivial, `randostats/__init__.py`). '0.1.0' is written in pyproject.toml, randostats/__init__.py __version__, and api.py FastAPI(version='0.1.0'). Make pyproject dynamic = ['version'] reading randostats.__version__ (or use importlib.metadata) and pass __version__ to FastAPI; add --version to the CLI.
- **Wrapped text fitting counts UTF-16 units while shorten() counts graphemes** (low value, trivial, `randostats/static/app.js`). fit() and fitLine() (lines 616-631) use String(s).length and s.slice(), the exact problem the graphemes() helper at line 8 was written to avoid, so a contact name with emoji is over-truncated or cut mid-glyph on the card. Route both through graphemes()/shorten().
- **Large single-purpose files and word lists as code** (low value, medium, `randostats/stats.py`). stats.py (713 lines) mixes eight concerns and app.js (922 lines) the whole UI. Split stats into a package (overview.py, timing.py, conversations.py, words.py, wrapped.py) re-exported from stats/__init__.py so the API and tests keep their imports; and, per CLAUDE.md's 'content is data' rule already applied to facts and voices, move SLANG, STOPWORDS, POSITIVE and NEGATIVE out of Python into data files under randostats/lexicon/ so language variants can be added without editing code.

## Shared across all Platteration repositories

The same gaps recur in every repository; fixing them once as a template and copying it is cheaper than fixing them fourteen times.

### CI and supply chain

1. **No workflow sets `permissions:`** (except the two Pages deploy jobs). Add `permissions: { contents: read }` at the top of every workflow so the `GITHUB_TOKEN` handed to third-party actions cannot write to the repository.
2. **No action is pinned to a commit SHA** (0 of 50 `uses:` lines across the fourteen repositories). `actions/checkout@v4` follows a movable tag; pin to the full 40-character SHA with the version in a comment, and let Dependabot bump it.
3. **No repository has Dependabot or Renovate.** Add `.github/dependabot.yml` with `npm` (or `pip`) and `github-actions` ecosystems, weekly.
4. **No CI step runs `npm audit`** (two workflows pass `--no-audit` explicitly). Add `npm audit --audit-level=high` after `npm ci`; for the Expo apps the current transitive advisories are build-time only (`uuid` via `xcode` via `@expo/config-plugins`), so gate on `high` rather than `moderate` until Expo ships the fix.
5. **`tvsham` runs `npm ci || npm install` in CI and in its Dockerfile.** The fallback silently discards the lockfile guarantee; drop it and fix the lockfile instead.
6. **`selfreportle`, `simplacad` and `phonogeometry` have no lockfile** and install Playwright ad hoc in CI. Add a `package-lock.json` (even with devDependencies only) and use `npm ci`.
7. **Enable secret scanning and push protection** in each repository's settings; nothing is committed today, and this keeps it that way.

### Repository hygiene

8. **Ten repositories have no `LICENSE`** (battleshiple, collectcollect, drawdraw, multidcheckers, multidconnect4, notenote, randostats, selfreportle, simplacad, tvsham). Without one, nobody else may legally use or contribute to the code. The siblings that have one use MIT.
9. **Only `simplacad` has a `SECURITY.md`.** Copy it to the others with a private reporting address.
10. **No repository has a `main` branch.** In all fourteen the default branch is the original `claude/...` feature branch, so branch protection, Dependabot targets and the two GitHub Pages workflows (`abientnoiser`, `chesscheatser` both trigger on `main`/`master`) all point at a branch that does not exist; those deploys have never run. Create `main` from the current branch, make it the default, and protect it.
11. **`drawdraw` is the one repository still on Expo SDK 53** (the rest are on 57). Its eight high-severity `npm audit` findings (`image-size`, `metro`) disappear with the SDK upgrade; it is also the only app not written in TypeScript and the only one pinned to Node 20 in CI.
12. **`multidcheckers` and `multidconnect4` are near-identical copies** (same branch name, same 65-file layout, same dependencies). The timeline/multiverse engine, persistence and share code should live in one shared package so fixes land in both.

### A hardened workflow to copy

```yaml
name: CI
on:
  push:
    branches: ["**"]
  pull_request:
permissions:
  contents: read
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<full-sha> # v4
      - uses: actions/setup-node@<full-sha> # v4
        with: { node-version-file: .nvmrc, cache: npm }
      - run: npm ci
      - run: npm audit --audit-level=high
      - run: npm run lint --if-present
      - run: npm run typecheck --if-present
      - run: npm test
```
