# randostats — security audit (2026-09-11)

A dedicated security pass, separate from and later than the review in `REVIEW.md`. Specialist reviewers read the repository through 3 independent lenses (L1, L2, L3), each required to *demonstrate* a finding rather than argue for it.

**8 findings** — 3 medium, 4 low, 1 info. Every one was reproduced with command output rather than argued from reading.

## Status

Every finding below was fixed on `claude/repo-review-security-baiyud` in bd8f236. The findings are kept as written so the reasoning behind each change stays with it.

### Corrections after an adversarial re-read of that commit

A second pass read bd8f236 against its own claims. Five corrections, all now made in the tree:

- **"each with a regression test that was checked by reverting the fix" was not true of six call sites.** The five `known_contact` guards and the `/api/wrapped` short-circuit could all be deleted with the suite still green, because the test asserted only `status_code == 200` — which is what the *unguarded* code returned too, after walking the corpus and keeping the answer under the invented name. `test_a_name_the_store_does_not_hold_takes_no_memo_slot` now asserts the property itself, on `app.state.derived._values`.
- **L2-1's header half does not hold on every browser, and its rounding half was not a bound.** Per the Fetch standard a cross-origin `no-cors` GET carries no `Origin`, so on a browser without Fetch Metadata (Firefox < 90, Safari < 16.4) the request arrives unlabelled and `refuse()` treats it as first-party — which is what keeps `curl` working and cannot be tightened without breaking it. And `round(gap_hours, 1)` over `le=24*365` still admitted 87,600 distinct keys against a 256-entry memo. The honest case is now bounded instead of the labelled one: `gap_hours` is snapped to the five gaps the interface offers (`GAP_CHOICES`), so no caller can mint a key at all, whatever headers they send. README no longer claims the server "refuses anything another site sends it". What is *not* closed, and is now recorded next to `MAX_ROWS`: `limit` is clamped rather than enumerated, so cycling it still mints one key per value (measured: 60 values across `/api/stats/words` and `/api/stats/contacts` = 120 keys), which across views exceeds the 256-entry memo. Closing that means computing each view once at the cap and slicing, which changes what several views return and is not a security fix to smuggle in.
- **The pre-fix 500 did not echo the exception text.** The commit message said it did; L3-3 below has it right. With `debug` false — FastAPI's default — Starlette answers a crash with the fixed string `Internal Server Error`, so the gap was the missing headers and nothing else. The body assertion in `test_a_500_carries_them_too` is forward-looking: it pins *this* app's handler, which does have the exception in reach, and the raising route now carries a caller-supplied string so the assertion has something to bite on.
- **The directory half of the store fix did not do what it said.** A umask only clears bits, so `mkdir(mode=0o700)` is already a subset of 0700 and the chmod after it could only add owner bits back; and `mkdir(parents=True, mode=...)` does not apply the mode to intermediate parents at all, leaving `--db a/b/c/randostats.db` with `a` and `b` at the umask default. `_make_private_dirs` now creates each missing level itself and chmods it, so every directory this process creates is 0700 under every umask; a directory that was already there is still left alone. The test asserts the intermediates, under two umasks that mkdir alone cannot satisfy.
- **Two smaller behaviour changes the commit did not mention.** `?contact=` (empty) had flipped from "everyone" to "nobody" on `/api/stats/emoji` and `/api/stats/misspellings`, since `""` is not a name the store holds; it is now decided in one place — an empty filter is no filter, as an omitted one is — and `stats.timing`, the one function that spelled it `contact is None`, now agrees. And every call refused past the LLM ceiling wrote a warning, trading a bounded bill for an unbounded log; the refusal is now logged once per window.

These were deliberately left for a decision rather than guessed at:

- L2-2 — with --allow-host, the cross-site check measures same-origin-ness against the very Host the allow-list accepted, so a DNS-rebinding attacker is genuinely same-origin. Closing it needs a mandatory token and a startup refusal that breaks the README's own documented example. The documentation half is done.
- The database path is still relative to the working directory. Moving it under XDG_DATA_HOME closes a symlink-preplant variant but orphans every existing user's database.

## Findings

### L1-1 · medium — WhatsApp _LINE regex backtracks quadratically on a single long line: a few-KB crafted .txt pegs a CPU core for minutes to hours (ReDoS on import)

`randostats/parsers/whatsapp.py`:25 · CWE-1333 · reproduced

**Who.** Whoever authored an export file the victim imports. The app exists to import chat exports other people wrote; a crafted 'WhatsApp export' .txt is the exact untrusted-file threat SECURITY.md is written for. No special format field is needed — detect_format picks whatsapp for any .txt filename (parsers/__init__.py:75) or any blob whose head matches _LINE.

**How.** 1) Craft a .txt whose first line is a normal WhatsApp line (so it auto-detects as whatsapp) followed by ONE enormous line with no newline that starts with a valid date+time prefix and then a long run of spaces ending in a non-colon char, e.g. `1/1/23, 1:00 PM - Alex: hi\n1/1/23, 1:11 PM ` + (' '*400000) + 'x'. 2) Victim imports it (POST /api/import, or `randostats import`). 3) api.py:269 runs `parsers.parse` in the threadpool, which calls whatsapp.parse; line 143 builds `opening = [_LINE.match(_clean_line(line)) for line in head]` and immediately runs _LINE.match on that one giant line. 4) The match never returns in any reasonable time.

**Why it matters.** A single small upload consumes a CPU core effectively forever (quadratic in line length). The parse runs in the Starlette threadpool holding the GIL, so a handful of such uploads exhaust the pool and all cores and freeze the whole local server; the browser tab that submitted the import hangs. Additionally, detect_format runs looks_like_whatsapp(data[:4096]) on the event-loop thread (api.py:256, before the threadpool hop), so even auto-detecting a non-.txt blob with a pathological ~4KB first line stalls the event loop ~15s. This is distinct from REVIEW.md MISS-2, which measured only strptime cost (~385us/line, bounded per line) and mitigated it with a line-count cap (MAX_UNPARSEABLE) and format-pair caching — neither helps here: the cost is in the regex match itself, on ONE line, before any strptime runs and before any line-count bound can engage.

**Evidence.**

The pattern (whatsapp.py:25-35) places several adjacent whitespace-matching quantifiers next to a whitespace-accepting sender group: `...(?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?)\]?\s*[-–]?\s*(?P<sender>[^:]{1,80}?):\s ...`. When the overall match fails (no `:` within the 80-char sender window) the engine tries every way of distributing a long space run across the trailing `\s*` groups and the `[^:]{1,80}?` sender → quadratic backtracking.

Measured raw match time on `"1/1/23, 1:11 PM " + " "*N + "x"` (redos_grow.py, signal-alarmed): N=400 -> 0.34s, N=800 -> 0.98s (2.9x), N=1600 -> 3.86s (3.9x), N=3200 -> 12.96s, N=6400 -> >20s. Ratio ~3-4x per doubling => ~O(n^2).

End-to-end through the real path (redos_parse.py): payload = one normal line + one line with 4000 trailing spaces (total 4044 bytes) -> detect_format('chat.txt', payload) == 'whatsapp' -> parsers.parse(...) took 15.11s to return 1 message. Extrapolating quadratically: ~40KB single line ~= 25 min; ~400KB ~= tens of hours; the upload cap is 256MB.

**Fix.** Remove the ambiguous adjacent whitespace quantifiers. Options, cheapest first: (a) On Python 3.11+ make the whitespace runs possessive/atomic so they cannot be re-partitioned on failure, e.g. `\s*+` or `(?>\s*)` for the `\s*` groups between the time and the sender, and consider `(?>[^:]{1,80}?)` for the sender; (b) cap the length of any single line handed to _LINE.match (a real WhatsApp line is short — refuse or skip lines longer than, say, a few thousand chars before matching, both in parse() and in looks_like_whatsapp); (c) tighten the pattern so only one `\s+` separates the time from the sender. Add a mutation test that feeds a single ~50KB line beginning with a valid stamp and asserts parse() returns within a small time budget.


### L2-1 · medium — The cross-site guard exempts every GET, and every memoised statistics view keys its cache on a caller-supplied parameter, so any web page the user visits can peg the local server

`randostats/api.py`:149 · CWE-400 · reproduced

**Who.** Any web page the user opens in a browser while `randostats serve` is running. No DNS rebinding, no LAN access, no credential and no interaction beyond the tab being open: the page targets http://127.0.0.1:8765 directly, which is a Host the server serves by design.

**How.** 1. The user has randostats running on the default 127.0.0.1:8765 with their messages imported. 2. They open any page (an ad frame is enough) that runs `for (let i=0;i<5000;i++) fetch('http://127.0.0.1:8765/api/stats/conversations?gap_hours='+(6+i*1e-4), {mode:'no-cors'}).catch(()=>{})`. 3. `refuse()` checks the Host header, which is 127.0.0.1 and therefore allowed, and then skips the Sec-Fetch-Site/Origin check entirely because `request.method not in SAFE_METHODS` is false for GET. 4. Each request reaches `conversations()`, whose memo key is `(version,'health',('gap',gap_hours))`. Because gap_hours is caller-chosen and only bounded by `Query(gt=0, le=8760)`, every request is a fresh key, so `stats.conversation_health()` re-sorts and re-walks the entire message corpus. 5. The handler is a sync `def`, so each one occupies an anyio worker thread holding the GIL. 6. After 256 distinct keys `Derived.get_or_compute` calls `self._values.clear()`, which also throws away the views the user's own tabs had cached. The same works with `/api/stats/words?limit=N`, `/api/stats/contacts?limit=N`, `/api/stats/misspellings?contact=<any string>` and `/api/wrapped?year=N`.

**Why it matters.** Denial of service of the whole app plus sustained 100% CPU on the user's machine, from a page that spends ~10 ms doing it. Measured on a 120,000-message store: one cross-site GET with a fresh gap_hours costs 0.774 s of server CPU against 0.002 s when memoised (a ~390x amplification of the attacker's cost), and 150 fire-and-forget requests issued in 0.01 s made the next `/api/status` take 93.8 s. The attacker cannot read any response (there are no CORS headers, so the same-origin policy still hides the data), so this is availability and battery only - but it is unattributable, survives closing the tab until the queue drains, and can be held indefinitely while the tab is open.

**Evidence.**

randostats/api.py:149-157 - the guard only runs for non-safe methods:
    if request.method not in SAFE_METHODS:
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        if (site is not None and site not in ("same-origin", "none")) or \
                (origin is not None and _netloc(origin) != _netloc(host)):
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
randostats/api.py:51 - SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
randostats/api.py:212-213 - the memo key is built from the caller's own query parameters:
    def remember(name: str, compute, **params):
        return derived.get_or_compute((state["version"], name, tuple(sorted(params.items()))), compute)
randostats/api.py:323-331 - gap_hours is a float the caller picks, bounded only by Query(gt=0, le=24*365):
    rows = remember("health", lambda: stats.conversation_health(messages(), gap_hours=gap_hours), gap=gap_hours)
randostats/api.py:109-111 - Derived clears everything once 256 keys are in it.

Measured against a live `randostats serve --port 8766` with 120,000 messages imported, every request carrying Origin: https://evil.example and Sec-Fetch-Site: cross-site:

  cross-site DELETE /api/messages                 -> 403  (writes are guarded)
  cross-site GET    /api/messages?limit=500       -> 200, 58526 bytes  (reads are not)

  baseline  /api/status  : 0.012 s
  one cross-site GET to  /api/stats/conversations?gap_hours=<new>: 0.774 s
  the same one again (memoised)                                 : 0.002 s

  fired 150 fire-and-forget cross-site GETs in 0.01 s
  /api/status while the page is 'open': ['93.82', '2.70', '0.42', '0.06']

Cache eviction of the user's own views, same server:
  user's own Conversations view, cold : 0.016 s
  user's own Conversations view, warm : 0.002 s
  Derived.LIMIT = 256 -> one clear() wipes every memoised view
  after 276 cross-site GETs with fresh keys:
  user's own Conversations view again : 0.350 s  (was warm)

Other fresh-key endpoints, cold, same store: /api/stats/words?limit=77 0.340 s, /api/stats/contacts?limit=88 0.331 s.

**Fix.** Two independent changes, both small. (1) Extend the refusal to reads of the JSON API: in `refuse()`, before the `request.method not in SAFE_METHODS` branch, refuse any request whose `sec-fetch-site` is not in ("same-origin", "none") when `request.url.path.startswith("/api/")`, whatever the method. A cross-site GET to /api/* is never something the real front end makes and its body is unreadable to the calling page anyway, so nothing legitimate is lost; keep `/`, `/static/*` and `/samples/*` outside the rule so a bookmark or a typed URL still opens the app (those are `sec-fetch-site: none` anyway, and a cross-site top-level navigation to `/` is harmless). (2) Stop letting the caller choose the memo key: quantise `gap_hours` to one decimal (`gap_hours = round(gap_hours, 1)`) before it reaches `remember`, clamp every `limit` to the handful of values the UI uses (e.g. `min(limit, 200)`) exactly as `/api/messages` already clamps, and for the `contact`-keyed views fall through to an empty result without computing when the name is not in `store.contacts()`. Either change alone removes the amplification; both together also bound the honest case.


### L3-1 · medium — The message database and its directory are created world-readable (0644/0755) at a path relative to the working directory

`randostats/store.py`:50 · CWE-732 · reproduced

**Who.** Any other local account on the machine the app runs on — a shared Linux workstation, a family/office desktop, a home server or NAS the user also runs this on, or any service account in a container image. They need no network access and no credentials, only the ability to traverse the user's home directory (0755 on Debian and Ubuntu before 21.04, and on most self-built /home layouts).

**How.** 1. The user follows the README quick start and runs `randostats serve` (or `randostats import`), which creates `<cwd>/data/randostats.db`. 2. `Store.__init__` calls `self.path.parent.mkdir(parents=True, exist_ok=True)` with the default mode, so `data/` is 0755, and `sqlite3.connect` creates the file with SQLite's default 0644 — neither is narrowed and no umask is set. 3. Any other local user opens the file directly with sqlite3 and reads the `messages` table: every imported message body, contact name, sender and timestamp, from every source the user imported (WhatsApp, iMessage, SMS, Telegram, Instagram, Messenger, Discord). A separate, weaker variant: because DEFAULT_DB is the *relative* path `data/randostats.db`, resolved against whatever directory the command was run from, a local attacker who can write to that directory can pre-create `data` as a symlink; `mkdir(exist_ok=True)` silently accepts the existing entry and the whole archive is then written inside a directory the attacker owns.

**Why it matters.** Full disclosure of the user's entire private message history — and their correspondents' messages, who never consented to anything here — to any other account on the same machine. The README's promise is confidentiality ("Your messages go into a SQLite file on your machine and never leave it"), and the app already treats exactly this data as 0600 when it is transient (parsers/imessage.py:63 writes the uploaded chat.db to a NamedTemporaryFile, which is 0600), so the permanent copy being 0644 is an inconsistency rather than a deliberate choice. On a single-user laptop with a 0700 home directory (Fedora/RHEL, Ubuntu 21.04+) the directory mode masks this, which is why this is medium and not high.

**Evidence.**

randostats/store.py:18 `DEFAULT_DB = Path(os.environ.get("RANDOSTATS_DB", "data/randostats.db"))`
randostats/store.py:49-51:
            if str(self.path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path), check_same_thread=False)

Measured modes after a real import (umask 022):
  .../permtest            0o755
  .../permtest/sub        0o755
  .../permtest/sub/randostats.db  0o644

Same after running the app over HTTP and importing 20,000 messages:
  drwxr-xr-x 2 root root  4096 data
  -rw-r--r-- 1 root root 32768 data/r.db

Cross-user read, as uid 65534 (nobody), of a database created by root in a 0755 directory:
  euid 65534
  rows 9
  sample [('Alex', 'Sam', 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'), ('Alex', 'Sam', 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx')]

Symlinked data directory, planted by nobody in a world-writable cwd:
  lrwxrwxrwx 1 nobody nogroup data -> /var/tmp/attacker
  json: parsed 20000 messages, 9 new, 9 total
  /var/tmp/attacker: -rw-r--r-- 1 root root 32768 randostats.db

**Fix.** In `Store.__init__`, create both the directory and the file privately, and repair an existing one: `self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)` (mkdir's mode argument is masked by the umask, so follow it with `os.chmod(self.path.parent, 0o700)` for the directory this process created), then after `sqlite3.connect` succeeds, `os.chmod(self.path, 0o600)` guarded by `try/except OSError` so a database on a filesystem without POSIX modes still works. Do the same for the `-journal`/`-wal` siblings by setting `PRAGMA journal_mode` explicitly or simply chmod-ing any sibling that exists. Separately, resolve DEFAULT_DB against a fixed user directory rather than the process's working directory — `Path(os.environ.get("RANDOSTATS_DB") or (Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "randostats/randostats.db"))` — so the location does not depend on where the command was typed and cannot be pre-empted by a symlink in a shared directory; note the move in the README next to the existing `--db`/`RANDOSTATS_DB` paragraph. A regression test asserting `stat.S_IMODE(os.stat(db).st_mode) == 0o600` after `Store(tmp_path / 'x.db')` pins it.


### L2-2 · low — Allow-listing a Host name with --allow-host silently disables the cross-site guard for that name as well, because same-origin is judged against the Host header the rebinding attacker controls

`randostats/api.py`:155 · CWE-346 · reproduced

**Who.** A web page, plus the ability to answer DNS for one allow-listed name. With `--allow-host laptop.lan` that is anyone who can answer `.lan` on the user's network (mDNS/DNS spoofing, a hostile router, a captive portal). With `--allow-host '*'` it is any page on the internet, using ordinary DNS rebinding on a name the attacker owns.

**How.** 1. The user follows the README's own example, `randostats serve --host 0.0.0.0 --allow-host laptop.lan` (or the CLI help's `--allow-host '*'`). 2. The attacker gets the user's browser to load a page whose host resolves to the randostats machine - for `*`, this is the textbook rebinding flow (serve the page from attacker.example with a 1-second TTL, then re-answer attacker.example as 127.0.0.1). 3. Every subsequent request the page makes is, to the browser, same-origin: Host: <name>, Origin: http://<name>, Sec-Fetch-Site: same-origin. 4. `refuse()` passes the Host check because the name is allow-listed, then passes the cross-site check because `_netloc(origin) == _netloc(host)` - both sides are the attacker's own name - and `site == "same-origin"`. 5. The page reads and writes everything, and because it is same-origin it can also read the responses.

**Why it matters.** Complete loss of the app's only access control for the widened name: `GET /api/messages?limit=500` returns the full text, sender and timestamp of every imported message and the page can read it; `DELETE /api/messages` wipes the database; `POST /api/import` injects data and overwrites `self_name`; `POST /api/counterpoint/packs` rewrites persisted settings; and with `--llm` the attacker can spend the owner's Anthropic budget (see L2-4). The README presents these as two independent defences - "It answers only to `localhost`, `127.0.0.1` and `[::1]`, and refuses anything that posts to it from another site" - and the CLI help for `--allow-host` says only that `*` turns off "the check", singular. In fact the second guard is derived from the first: once the Host allow-list accepts a name, the cross-site guard, which measures same-origin-ness against that very Host, accepts everything from it. REVIEW.md lists SEC-1 (Host validation), SEC-2 (cross-site refusal) and SEC-4 (non-loopback bind) as fixed; this is the interaction the three fixes create together, and SEC-4's stronger recommendation - refuse a non-loopback posture unless `RANDOSTATS_TOKEN` is set and enforced with `secrets.compare_digest` - was not implemented, only its "at minimum, print a warning" alternative.

**Evidence.**

randostats/api.py:139-157:
    hosts = {_hostname(h) for h in (LOOPBACK_HOSTS if allowed_hosts is None else allowed_hosts)}
    any_host = "*" in hosts
    ...
        if not any_host and _hostname(host) not in hosts:
            return JSONResponse({"detail": "invalid host header; ..."}, status_code=400)
        if request.method not in SAFE_METHODS:
            site = request.headers.get("sec-fetch-site")
            origin = request.headers.get("origin")
            if (site is not None and site not in ("same-origin", "none")) or \
                    (origin is not None and _netloc(origin) != _netloc(host)):
randostats/cli.py:25-28 - "Only loopback names are served by default, so a page on the internet cannot point a name it owns at this port. Pass '*' to turn the check off."
README.md:55-57 - "It answers only to `localhost`, `127.0.0.1` and `[::1]`, and refuses anything that posts to it from another site."

Run against two live servers:

  # .venv/bin/python -m randostats.cli --db .../lan.db serve --port 8768 --allow-host laptop.lan
  GET    /api/messages (Host+Origin laptop.lan, sec-fetch-site same-origin) -> 200
  DELETE /api/messages (Host+Origin laptop.lan, sec-fetch-site same-origin) -> 200
  GET    /api/messages (Host attacker.example)                              -> 400

  # .venv/bin/python -m randostats.cli --db .../star.db serve --port 8767 --allow-host '*'
  GET    /api/messages            (Host+Origin attacker.example, same-origin) -> 200
  DELETE /api/messages            (Host+Origin attacker.example, same-origin) -> 200
  POST   /api/counterpoint/packs  (Host+Origin attacker.example, same-origin) -> 200

**Fix.** A custom-header or token-in-the-page scheme does not help here - a rebinding attacker is same-origin and can set any header and read any body - so the fix has to be a secret the page cannot obtain by fetching it. In `create_app`, when `allowed_hosts` contains anything outside `LOOPBACK_HOSTS` (including `*`), require `os.environ['RANDOSTATS_TOKEN']` and enforce it in `refuse()` on every request whose `_hostname(host)` is not loopback: accept it from an `Authorization: Bearer` header or a `?token=` query parameter compared with `secrets.compare_digest`, and have `cli.py` refuse to start (rather than warn) if the variable is unset - this is SEC-4's first recommendation, which was never implemented. Reject `--allow-host '*'` outright unless the token is set. Independently, fix the documentation so it does not read as two guards: the `--allow-host` help should say "NAME becomes a name this server trusts completely: any page that can make your browser resolve NAME to this machine can read and delete everything, exactly as the front end can", and the README's "refuses anything that posts to it from another site" should say "...from a site other than the names it serves".


### L2-3 · low — The message database and its directory are created world-readable, while the app treats the same data as 0600 everywhere else

`randostats/store.py`:50 · CWE-732 · reproduced

**Who.** Any other account on the same machine: a second user on a shared workstation or family laptop, another service account in a container or on a dev box, or any process running as a different unprivileged user. They need no access to the port and are stopped by neither the Host allow-list nor the cross-site guard, which are the app's only two access controls.

**How.** 1. The user runs `randostats serve` and imports their WhatsApp/iMessage/SMS exports. 2. `Store.__init__` does `self.path.parent.mkdir(parents=True, exist_ok=True)` and `sqlite3.connect(str(self.path))`, both of which take the process umask (022 on a default Linux/macOS install). 3. The attacker runs `cp ~victim/data/randostats.db /tmp && sqlite3 /tmp/randostats.db 'select * from messages'`.

**Why it matters.** The complete plaintext of every message the user ever imported - contact, sender, direction, timestamp and body - plus the `settings` table, readable by any local account. The app already treats this data as needing 0600 when it is transient: `parsers/imessage.py:63-66` writes the uploaded copy of `chat.db` through `tempfile.NamedTemporaryFile`, which is 0600, opens it `mode=ro` and unlinks it in a `finally`. The permanent store, into which every import from every source accumulates, is 0644. README.md:11-12 says "Your messages go into a SQLite file on your machine and never leave it", which a reader can reasonably take as a confidentiality claim.

**Evidence.**

randostats/store.py:47-51:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
No chmod, no umask, no mode= anywhere in the file (grep for 'chmod|0o6|0o7|umask' in randostats/ returns nothing).

After `randostats --db .../probe.db serve` and one import, on a default umask of 0022:

  $ umask
  0022
  $ stat -c '%a %n' probe.db
  644 probe.db
  $ ls -la /home/user/randostats/data
  drwxr-xr-x  2 root root 4096 Sep 10 03:34 .

Contrast randostats/parsers/imessage.py:63-66, which does get this right for the temporary copy:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(data)
        path = Path(tmp.name)

**Fix.** In `Store.__init__`, after the mkdir and connect, narrow both: `self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)` and, because mkdir's mode is ignored for a directory that already exists, follow it with `os.chmod(self.path.parent, 0o700)` guarded by a try/except OSError (a shared or network path may refuse). Then `os.chmod(self.path, 0o600)` immediately after `sqlite3.connect` - sqlite creates the file on first write, so do it after `executescript(_SCHEMA)` - and chmod the `-wal`/`-shm` siblings too if WAL is ever enabled. Add a test that asserts `stat.S_IMODE(os.stat(db).st_mode) == 0o600` under a deliberately loose umask (`os.umask(0)`), which is the only way this stays fixed.


### L2-4 · low — Nothing bounds how many paid Claude calls a caller can trigger, and the non-loopback warning tells the user what is exposed but not what can be spent

`randostats/api.py`:384 · CWE-770 · reproduced

**Who.** Anyone who can reach the port once the user has widened it - `--host 0.0.0.0` on a coffee-shop or office network - or a rebinding page once a name has been allow-listed (L2-2). Requires the owner to have started with `--llm` and a working Anthropic credential.

**How.** 1. The owner runs `randostats serve --host 0.0.0.0 --allow-host laptop.lan --llm`, reads the printed warning ('anyone who can reach this port can read, search and export every message you have imported, and delete the lot') and accepts it as a data-exposure trade. 2. The attacker POSTs `/api/counterpoint` with a 4000-character body containing four or more distinct quantitative claims. 3. `counterpoint()` groups the results by claim key, takes the first MAX_LLM_GROUPS=4 groups and calls `llm.sharpen` once per group through a ThreadPoolExecutor - four billed `beta.messages.parse` requests to claude-opus-5 on the owner's credential. 4. There is no per-caller counter, no daily cap, no global limiter and no cooldown anywhere in the process, so the attacker simply repeats. 5. Because `counterpoint` is a sync `def` and `llm.sharpen` uses the SDK's default 10-minute timeout, each in-flight request also holds an anyio worker thread, so roughly forty concurrent requests block every other sync endpoint in the app.

**Why it matters.** Unbounded spend on the owner's Anthropic account at 4 requests per HTTP request, plus a second denial-of-service channel through threadpool exhaustion. The per-request caps that REVIEW.md's MISS-1 added (MAX_TEXT_CHARS, MAX_LLM_GROUPS, MAX_CLAIMS) bound one request's fan-out but say nothing about how many requests may arrive, which is the quantity that actually costs money. The CLI warning enumerates read / search / export / delete and stops there, so a user weighing `--host 0.0.0.0` is not told that the same decision hands out their API budget.

**Evidence.**

randostats/api.py:377-396:
    @app.post("/api/counterpoint")
    def counterpoint(req: CounterRequest):
        ...
        if results and llm_on and req.llm:
            ...
            groups = list(by_claim.items())[:MAX_LLM_GROUPS]
            with ThreadPoolExecutor(max_workers=min(4, len(groups))) as pool:
                sharpened = pool.map(lambda g: (g[0], llm.sharpen(g[1][0].claim.raw, g[1])), groups)
randostats/api.py:56-57: MAX_TEXT_CHARS = 4000 / MAX_LLM_GROUPS = 4  (per request, not per caller)
randostats/cli.py:58-61: the warning names read / search / export / delete and nothing about cost.
randostats/counterpoint/llm.py:110-119: beta.messages.parse with no `timeout=`, so the SDK's 10-minute default applies inside the request.
A grep of randostats/ for 'limiter|ratelimit|rate_limit|throttle|slowapi' returns nothing.

Measured with llm.sharpen stubbed out and counted (no real API calls made):
  MAX_TEXT_CHARS = 4000  MAX_LLM_GROUPS = 4
  Claude calls made by 10 requests: 40 -> 4 per request
  any per-caller / global cap on how many requests?  none in api.py: True

**Fix.** Three small changes. (1) Bound the spend, not just the fan-out: keep a process-wide counter of llm.sharpen calls in a rolling window (e.g. `RANDOSTATS_LLM_CALLS_PER_HOUR`, default a couple of hundred) and fall through to the rule-based answer - which is already the documented behaviour for every other failure - once it is exceeded; a `threading.Lock` and a deque of timestamps is enough, since there is exactly one user. (2) Pass an explicit `timeout=20` to `beta.messages.parse` in `llm.py:110`, so a stalled call releases its anyio worker thread in seconds rather than ten minutes. (3) Extend the cli.py warning so it names the cost as well as the data: add a line, printed only when `args.llm` is set and the bind is not loopback, saying that anyone who reaches the port can also make this server call the Anthropic API on the owner's credential.


### L3-2 · low — The Counterpoint tab tells the user "Nothing else here leaves your machine" while --llm sends the transcribed claim to Anthropic

`randostats/static/index.html`:137 · CWE-451 · reproduced

**Who.** No attacker is needed: the defect is that the interface misstates where data goes, so the user (and the third parties whose speech the microphone picks up) cannot give informed consent. The party who ends up with data they were told would not leave is the LLM vendor.

**How.** 1. The user starts the server with `--llm` (or `RANDOSTATS_LLM=1`) with an Anthropic credential present, as the README documents. 2. `/api/status` returns `llm: true`, so app.js unhides `#llm-label`, whose checkbox `#counter-llm` is `checked` in the markup (index.html:132) — sharpening is on by default. 3. The user opens the Counterpoint tab and reads the always-visible privacy note, which explains the speech-to-cloud caveat and then states flatly "Nothing else here leaves your machine." 4. The user presses Listen. For every final speech result app.js calls `counter(t, {live: true})` (app.js:811), which POSTs the transcript with `llm: $("#counter-llm").checked` — true. 5. api.py:392-394 groups the extracted claims and calls `llm.sharpen(claim.raw, ...)`, which interpolates the claim text into a prompt and sends it to the Anthropic API. `claim.raw` is not just the number: engine.py:160 takes `text[m.start() : m.end() + len(tail)]`, where `tail` runs to the next clause boundary, so the surrounding words spoken in the room travel with it.

**Why it matters.** Speech captured from people in the room who never touched the app — the note itself says "including whoever else is talking" — is forwarded to a third-party API, under a sentence promising the opposite. The README (lines 14-19) does disclose this correctly, so the shipped interface contradicts the project's own documentation at the exact screen where the microphone is turned on. No data is stolen and no boundary is crossed by an attacker; the defect is that the consent the UI obtains is based on a false statement.

**Evidence.**

randostats/static/index.html:135-137:
        <p class="muted" id="listen-privacy">Listening uses your browser's own speech recognition. In Chrome and
          Edge that sends the audio — including whoever else is talking — to the browser vendor to be transcribed.
          Safari does it on the device. Nothing else here leaves your machine.</p>
randostats/static/index.html:132: <label class="muted" id="llm-label" hidden><input type="checkbox" id="counter-llm" checked> sharpen with Claude</label>
randostats/static/app.js:811: if (e.results[i].isFinal) { finalText += t + " "; counter(t, { live: true }).catch(() => {}); }
randostats/static/app.js:754-755: api("/api/counterpoint", {..., body: JSON.stringify({ text, session: ..., llm: $("#counter-llm").checked })})
randostats/counterpoint/llm.py:108: prompt = f"Claim made in the argument: \"{claim_text}\"\n\nVerified facts to choose from:\n{facts}"

Observed, with the SDK call stubbed to record its arguments and use_llm=True:
  GET /api/status -> {..., 'llm': True}
  status 200 llm in payload: True
  --- what was handed to the Anthropic SDK ---
  model: claude-opus-5
  user content: Claim made in the argument: "70% of people on that ward relapse"

                Verified facts to choose from:
                - id=glacier-water: About 69% of the world's fresh water is locked in glaciers and ice caps ...
The words "on that ward relapse" are the spoken clause that followed the number; they were carried out of the machine along with it.

**Fix.** Make the sentence conditional on the server's own `llm` flag instead of asserting an absolute. Replace the trailing clause in `#listen-privacy` with a span the front end fills in, e.g. give it `<span id="listen-privacy-llm"></span>` and set it in `refresh()` alongside the existing `$("#llm-label").hidden = !st.llm`: when `st.llm` is false, "Nothing else here leaves your machine."; when it is true, "With --llm on, the claim itself — including the words spoken around the number — is also sent to Anthropic to be rephrased; untick \"sharpen with Claude\" to keep it local." Consider also defaulting `#counter-llm` to unchecked so the cloud round trip is opted into rather than out of, at least for the live-listening path, and add a test that fails if the literal string "Nothing else here leaves your machine" appears unconditionally in index.html.


### L3-3 · info — Security headers are not applied to 5xx responses, because the middleware sits inside Starlette's ServerErrorMiddleware

`randostats/api.py`:172 · CWE-693 · reproduced

**Who.** None reachable today. Recorded because the lens asks specifically whether the headers apply to every response, and the answer is no.

**How.** Starlette builds its stack as `[ServerErrorMiddleware] + user_middleware + [ExceptionMiddleware] + router` (starlette/applications.py:74-78), so `@app.middleware("http")` — which is user middleware — is *inside* the 500 handler. Any exception that escapes a route handler is turned into a response by ServerErrorMiddleware after the header-setting code has already been unwound, so that response carries no Content-Security-Policy, no X-Content-Type-Options and no Referrer-Policy. HTTPException-derived responses (400/403/413/422) are produced by ExceptionMiddleware, which is inside the header middleware, and those are correctly decorated — I verified 400, 403, 404 and 413 all carry the full set.

**Why it matters.** Nothing today. The only body reachable this way is Starlette's fixed `Internal Server Error` string served as text/plain, which carries no attacker-controlled bytes, and I could not find any route in the shipped API that produces a 500: I probed `/api/stats/members?contact=zzz`, `/api/wrapped?year=0`, `/api/stats/misspellings?limit=-5`, `/api/stats/conversations?gap_hours=0.0000001`, `/api/stats/timing/contacts?limit=-1`, `/api/stats/emoji?limit=999999999` and `/api/messages?offset=99999999999999999999` and every one returned 200. It matters only as a latent gap: a future handler that renders any part of a user string into an error page would do so without the CSP the project relies on as its second line of defence.

**Evidence.**

randostats/api.py:172-179:
    @app.middleware("http")
    async def security_guard(request, call_next):
        refusal = refuse(request)
        response = refusal if refusal is not None else await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        ...
starlette/applications.py:74-78:
    middleware = [Middleware(ServerErrorMiddleware, handler=error_handler, debug=debug)]
    ...
    middleware += self.user_middleware
    middleware.append(Middleware(ExceptionMiddleware, handlers=exception_handlers, debug=debug))

Measured, with a route added that raises RuntimeError:
  status 500
  csp present: False
  headers: {'content-length': '21', 'content-type': 'text/plain; charset=utf-8'}

For contrast, the refusals the middleware itself returns do carry them:
  $ curl -sD- -H 'Host: evil.com' http://127.0.0.1:8799/api/status
  HTTP/1.1 400 Bad Request
  content-security-policy: default-src 'self'; script-src 'self'; ...
  x-content-type-options: nosniff
  referrer-policy: no-referrer

**Fix.** Register the headers as an exception-handler-independent layer rather than as HTTP middleware: pass a 500 handler that returns a JSONResponse with the same three headers (`app.add_exception_handler(Exception, handler)` installs it as ServerErrorMiddleware's handler, which *is* outside the user middleware), or factor the three `setdefault` lines into a `_decorate(response)` helper and call it from both the middleware and that handler. A test asserting `client.get('/boom').headers['content-security-policy']` on an app with a deliberately raising route pins it.


## Checked and sound

What the reviewers tried and could not break. Recorded so it is not re-raised, and so a future change that undoes one of these is recognisable as a regression.

- Zip handling: archive members are only ever read into memory via zf.read(name), never extracted to a path, so member-name path traversal (../../etc/passwd) and symlink members are inert. Nested archives are not recursed (json_files only reads .json members and json.loads them; a nested .zip member is skipped by suffix).
- Zip bombs: declared_members + index_bytes (size_cd//46) cap the enumeration before open, _refuse_what_was_opened re-checks the exact count after open, and json_files bounds total uncompressed bytes (min(512MB, max(32MB, len*50))) and per-member size (64MB) before any member is read; zipfile's ZipExtFile stops at the declared file_size, so an understated size cannot bomb. Confirmed the member-count lie (EOCD vs central directory) is caught by the post-open exact count.
- smsbackup XML: the UTF-16/UTF-32 bypass is closed — decode() refuses any doc with a BOM or a NUL in the first 4 bytes (every UTF-16 XML doc has a NUL there because the first char is ASCII), and passes a str to expat which then parses it as UTF-8 and ignores any encoding declaration. The `<!ENTITY` substring check on the decoded text blocks internal-entity billion-laughs; `<!ENTITY` is the only valid spelling (case-sensitive, no interior whitespace, covers parameter entities `<!ENTITY %`). External entities are not loaded by the stdlib parser.
- Front-end DOM escaping / the XSS scanner: manually traced every innerHTML/insertAdjacentHTML/hover/showTip sink; all imported values (contact, sender, text, word, emoji, member names, pack/voice fields, LLM punchline/logic_gap, wrapped SVG text) pass through esc() or a numeric formatter. The scanner's INTERPOLATION regex (\$\{[^{}]*\}) does have a blind spot for interpolations containing nested braces, but I enumerated all 7 such cases in markup templates (lines 139,182,347,386,567,628,741) and each resolves to esc()/fmt()/a literal-choice or a hardcoded value — the scanner also captures the nested template literals independently, so no real unescaped sink slips through.
- highlight() and stats.search build regexes from user/imported words but escape all metacharacters first (JS: /[.*+?^${}()|[\]\\]/g; Python: re.escape then a benign apostrophe char-class), so no regex injection and no ReDoS (the resulting pattern is a fixed literal with fixed lookarounds); $1 in the <mark> replacement is taken from already-esc()'d text.
- Counterpoint extract_claims: CounterRequest.text is Field(max_length=4000) so input is bounded; worst-case pathological 4000-char inputs (number-word runs, hyphenated runs, percent/times bait) measured at <=0.9s each and produce no catastrophic blowup. The quadratic taken() rescan and unbounded LLM fan-out from REVIEW MISS-1 are mitigated (covered() bytearray, MAX_CLAIMS=20, MAX_LLM_GROUPS=4).
- No SQL injection: all store queries are parameterised; the only interpolated SQL is the constant IDENTITY column expression, not user data.
- No CSV/TSV formula-injection surface: the app imports CSV but never exports any spreadsheet-openable format (the only export is the Wrapped PNG), so =cmd() payloads in a contact name never reach a formula sink.
- No prototype-pollution path: this is Python server-side (dict keys are values, never attribute assignments) and the front end never assigns imported strings as object keys except state.hues[contact] whose values are fixed CSS-var strings; whitelists (packs/voices/fmt) use set membership or try/except KeyError, not bare TABLE[user_key].
- imessage parser: the uploaded blob is written to a 0600 NamedTemporaryFile, opened file:...?mode=ro (read-only, so no triggers fire on the fixed SELECT), and unlinked in finally; _decode_attributed_body's length-prefixed slice cannot overread.
- Host allow-list: probed a running server with Host values attacker.example, 127.0.0.1.nip.io, localhost. (trailing dot), evil.com@localhost, LOCALHOST, localhost:8766, 127.0.0.1:8766 and [::1]:8766. Only the intended loopback spellings are served; everything else is a 400. The check also covers the mounts and the docs, not just the API: GET /, /static/app.js, /samples/sample_messages.json and /openapi.json with Host: attacker.example are all 400, so the @app.middleware("http") registration really does wrap the StaticFiles mounts.
- One Host-parsing quirk found and judged unreachable, so not reported: _netloc() strips a scheme by splitting on "//" (so --allow-host can be given a URL), which means Host: evil.com//localhost is read as "localhost" and served - verified, 200. No browser can emit it: a URL's authority cannot contain a slash, so the Host header a browser derives from http://evil.com//localhost:8765/ is "evil.com". A non-browser client can already just send Host: localhost, so the quirk grants nothing the threat model does not already grant.
- Cross-site writes: POST /api/import, DELETE /api/messages and POST /api/counterpoint/packs are all 403 with sec-fetch-site: cross-site, with sec-fetch-site: same-site (a different port on the same host, which the allow-list correctly treats as hostile), and with a bare cross-origin Origin header and no Sec-Fetch-* at all (the older-browser case). Sec-Fetch-* is a forbidden header name, so page script cannot forge "same-origin"; and the check reads the raw header value case-sensitively, which fails closed rather than open.
- Tried to dodge the upload size cap by routing around the middleware's `request.url.path == "/api/import"` test: POST /api/import/ (trailing slash) falls into the stricter `elif` branch and is refused at the 1 MB JSON limit rather than the 256 MB upload limit, and //api/import and /API/import are 404 plus the same 1 MB refusal. No path spelling reaches the handler with the looser cap.
- Walked every GET route looking for a state change a cross-site page could cause: /api/status, /api/stats/* , /api/messages, /api/wrapped, /api/counterpoint/{random,facts,packs} and the two static mounts. None writes to the store, the settings table or the session map; the only mutations a GET causes are filling the Derived memo (finding L2-1) and advancing the engine's shared random.Random. Every mutating operation is POST or DELETE and therefore behind the cross-site guard.
- Static serving is not traversable: /samples/../pyproject.toml, /samples/%2e%2e/pyproject.toml, /samples/..%2fpyproject.toml, /static/../../pyproject.toml and /static/%2e%2e%2f%2e%2e%2fpyproject.toml (all with curl --path-as-is) are 404.
- Sessions: the counterpoint session id is server-issued (uuid4), carries no authority at all - it keys a set of already-answered claim hashes used only to stop a spoken claim being answered twice - is bounded at MAX_SESSIONS=256 with insertion-order eviction, and an id the server did not issue is ignored rather than created, so a client cannot grow the map. There are no cookies, no localStorage/sessionStorage/IndexedDB use and no bearer tokens anywhere in the front end, so there is nothing to steal, fixate or compare non-constant-time.
- Every caller-supplied identifier is resolved through a real allow-list before use: fmt via PARSERS[fmt] with KeyError -> 400 (parsers/__init__.py:81-87), packs and voice against the ids from list_packs()/list_voices() with a 400 on anything unknown (api.py:420-431), direction against a two-value tuple, gap_hours and limit through Query(gt=..., le=...), and search limit/offset clamped to 1..500 / >=0. Python dicts carry no prototype chain, so the __proto__/constructor style of allow-list bypass that CLAUDE.md warns about in the JS repositories does not apply here.
- Secrets: git grep for sk-ant / ANTHROPIC_API_KEY / api_key= across all 24 commits of history turns up only documentation and the SDK attribute probe - no key material was ever committed. The Anthropic credential is only ever touched by the SDK inside _get_client(); llm.sharpen swallows every exception into a log.warning and returns None, so no exception text reaches an HTTP response; /api/status exposes only the boolean llm flag; FastAPI is not in debug mode, so a 500 is a bare "Internal Server Error" with no traceback; and index.html loads nothing but /static/app.css and /static/app.js, so there is no third-party script that could read anything.
- No message content leaves the machine. The only outbound network call in the whole app is llm.sharpen, whose prompt is the claim the user typed or spoke plus statements already in facts.json - /api/wrapped builds a Claim from the user's own percentage but answers it with cp["engine"].match locally rather than calling Claude. The Wrapped PNG is serialised, rasterised and downloaded entirely inside the page. Responses carry Referrer-Policy: no-referrer, so no query string leaks onward, and the CSP's connect-src 'self' pins the front end's own fetches.
- Derived cache keys are distinct per endpoint (overview / contacts / timing / peaks / health / members / misspellings / emoji / tone / words / wrapped), each combined with the import version and the handler's own parameters, so no view can be served another view's answer and an import invalidates all of them (bump() advances state["version"], which also misses the maxsize=1 messages_cache).
- A chunked request (Transfer-Encoding: chunked) declares no content-length, so the middleware's pre-read refusal does not fire and a multipart body is spooled to disk before the handler's own len(data) check. Not reported as a finding: a browser cannot send a cross-site streaming POST past the 403, so this only reaches someone who can already talk to the port directly, which is exactly the exposure the CLI warning already spells out.
- The iMessage importer, the one place that handles an uploaded database file, is sound on privilege: the copy is written through tempfile.NamedTemporaryFile (mode 0600), opened read-only via a file:...?mode=ro URI so nothing in the uploaded database can be written back, and unlinked in a finally.
- Host-header validation (the DNS-rebinding fix from SEC-1) holds. Probed `evil.com`, `evil.com:8799`, `localhost.evil.com`, `127.0.0.1.nip.io:8799` and a raw HTTP/1.0 request with no Host header at all — every one is refused with 400, while `127.0.0.1:8799`, `localhost:8799`, `LOCALHOST:8799` and `[::1]:8799` answer 200. `_hostname()` handles the bracketed IPv6 form and the bare `::1` correctly (count(':')==1 is the only case it strips a port from), so it fails closed rather than open on anything I could throw at it.
- The cross-site write guard (SEC-2) is stricter than the review describes: `sec-fetch-site: same-site` is refused as well as `cross-site`, and an `Origin` whose netloc differs from the Host is refused independently, so a cross-origin multipart form POST is blocked twice over. Measured: cross-site 403, same-site 403, Origin: http://evil.com 403, DELETE /api/messages with cross-site 403, same-origin 200.
- `--host 0.0.0.0` does not widen the Host allow-list (cli.py:50 excludes binds from names), so even with the widest bind a LAN client reaching the port by IP gets a 400 unless the user also passes `--allow-host`. That is a real defence the CLI help does not advertise.
- Static file serving hands out nothing outside its two directories. `/static/../pyproject.toml`, `/static/..%2fpyproject.toml`, `/static/%2e%2e/%2e%2e/pyproject.toml`, `/samples/../README.md`, `/samples/../randostats/api.py` and a `%00` suffix all return 404; Starlette 1.6's `lookup_path` rejects absolute paths and requires `os.path.commonpath(realpath(joined), realpath(directory)) == directory` with `follow_symlink=False`, so a planted symlink inside `static/` or `samples/` would not escape either. Directory listings are off (`html=False`), so `/static/` and `/samples/` are 404.
- The `/samples` mount does reach outside the package — `Path(__file__).resolve().parent.parent / "samples"`, i.e. the parent of the installed package directory, which for a non-editable install is site-packages. I checked this venv: no `samples` directory exists there, so nothing is exposed, and in a source checkout it is the repo's own synthetic sample files. No attack path today, but the path escapes the package boundary for no reason and would publish someone else's `samples/` if one ever appeared alongside; `importlib.resources` or a path under `randostats/` would be the safer spelling.
- The CSP and the other two headers are present on every response I could produce short of a 500: `/`, `/static/app.js`, `/samples/sample_messages.json`, `/api/status`, `/api/messages`, a 404, the 400 Host refusal, the 403 cross-site refusal and the 413 oversize refusal all carry the identical policy. `script-src 'self'` with no `'unsafe-eval'`, `object-src 'none'`, `base-uri 'none'`, `form-action 'self'` and `frame-ancestors 'none'` are all present, and `style-src 'unsafe-inline'` is genuinely needed (inline style attributes on every chart mark) rather than laziness.
- FastAPI's `/docs`, `/redoc` and `/openapi.json` are exposed unauthenticated. I checked whether this contradicts the local-only promise: the Swagger page references https://cdn.jsdelivr.net (script + stylesheet) and https://fastapi.tiangolo.com (favicon), all three of which the app's own CSP blocks before the request is made — `script-src 'self'`, `style-src 'self' 'unsafe-inline'`, `img-src 'self' data: blob:`. Nothing leaves, and the schema discloses no route that /static/app.js does not already spell out, so it is not a finding; closing them (`docs_url=None`) would still be tidier.
- `X-Content-Type-Options: nosniff` blocks the cross-origin type-confusion reads that the missing CORS policy would otherwise leave open: `/api/messages` is served as application/json, so a cross-site `<link rel=stylesheet>` or `<script src>` pointed at it is refused by the browser rather than parsed. No CORS middleware is installed anywhere, so a cross-origin `fetch` gets a response the page cannot read.
- No subprocess use of any kind. Grepping the whole package for `subprocess`, `os.system`, `popen` and `shell=True` returns nothing; the only process the app starts is the daemon speller-warmup thread (api.py:198).
- Temporary files are handled correctly. The only explicit one is the uploaded iMessage database (parsers/imessage.py:63), written with `NamedTemporaryFile` (0600 via mkstemp), opened through a `file:...?mode=ro` URI so SQLite cannot write back or create journal siblings, and unlinked in a `finally`. The tempfile name is random hex so it cannot inject query parameters into that URI. Starlette's multipart spool (`SpooledTemporaryFile(max_size=1MB)`, formparsers.py:230) rolls over to a 0600 mkstemp file and is closed and deleted when the request ends; I uploaded a 6 MB import and found no leftovers in /tmp afterwards.
- The XML entity fix (SEC-3) is complete, not partial. `smsbackup.decode` now refuses UTF-16/UTF-32 by BOM *and* by a NUL anywhere in the first four bytes — which is what expat's own encoding auto-detection keys on — and hands expat a `str`, pinning the encoding so the declaration cannot name a second one; the `<!ENTITY` check then runs on the same characters the parser will see. External entities and external DTD retrieval are not reachable through `xml.etree.ElementTree` regardless.
- The upload size cap works on any request that declares its length: a POST declaring Content-Length 2,000,000 to a JSON route is refused 413 by the middleware before the body is read. The acknowledged gap is a request that declares nothing (chunked transfer-encoding), where Starlette spools the whole multipart body to disk before the handler's `len(data)` check runs — the code comment at api.py:158-162 says as much. I did not raise it as a finding because the cross-site guard blocks every browser-driven path to it and the only remaining caller is a local process that can exhaust the same disk directly; if it is worth closing, Starlette 1.6 has `max_body_size` on the application object (applications.py:75, `RequestBodyLimitMiddleware`), which enforces it at the ASGI layer for chunked bodies too.
- Pack and voice loading reads only `randostats/counterpoint/{facts.json,packs/*.json,voices/*.json}` via fixed globs; the pack ids and voice id a client can POST are validated against the loaded set before being stored (api.py:422-431) and are only ever used as plain dict keys, so no path or attribute is built from them.
- No browser-platform escape hatches in the front end: no service worker, no localStorage/sessionStorage/cookies, no postMessage, no iframes, no `eval`/`new Function`, no `window.open`, and app.css contains no `url()` or `@import`. The one `a.href` is a blob URL for the Wrapped PNG download. The hash-fragment router (`location.hash.slice(1)` into a `querySelector`) is attacker-influenceable by link, but the worst it produces is a thrown invalid-selector DOMException caught by `refresh()`'s `.catch`, or a truthy `render["toString"]` lookup that resolves to a harmless native function — no sink.
- No credential ever reaches a response. `llm.sharpen` swallows every exception and returns None (llm.py:120-122), logging the exception server-side only, and `llm.available()` does the same at startup, so an Anthropic SDK error message cannot be echoed to an HTTP client. The API key is never read by randostats itself — it stays inside `anthropic.Anthropic()`.
- The README's disclosure is accurate where the UI's is not: lines 11-19 name both outbound paths (browser speech recognition and, with --llm, the claim text) and correctly state that neither touches the imported messages. I traced the LLM path and confirmed only `claim.raw` and the already-matched local facts are sent — no message bodies, contacts, senders or statistics leave the process.

