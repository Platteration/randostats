# randostats

Statistics on the messages you send and receive, plus a **counterpoint engine**
that listens to an argument and answers any statistic with a real one of the
same magnitude that has nothing to do with it.

> "Seventy percent of people drink beer, so it's fine."
> "Counterpoint: about 71% of Earth's surface is covered by water. By the same
> logic, drinking beer is caused by the ocean. Source: USGS, 2019."

Everything runs locally. Your messages go into a SQLite file on your machine
and never leave it. It is a website as well as a local app: `randostats serve`
is the whole host, setting its own security headers, and served beyond your own
machine it asks for a password on every request (see [Deploy](#deploy)).

Two things do leave, both opt-in and both named where they happen: the
Counterpoint tab's **Listen** button uses your browser's speech recognition,
which in Chrome and Edge means the audio is transcribed by the browser vendor
rather than on your device, and starting with `--llm` sends the claim being
answered to the optional service described further down this page. Neither one
touches your imported messages.

## What it does

| Tab | What you see |
|---|---|
| **Overview** | A quick read of your archive: headline counts, top contact, reply rhythm, who starts conversations, peak time, and shortcuts into deeper views. |
| **People** | Messages per person, split into sent and received, share you wrote, messages per day, who writes the longest messages. |
| **Conversations** | Who opens conversations and who gets the last word, double texts, longest silences, a reply-time dumbbell of you against them, and a per-member breakdown inside group chats. The silence gap that separates one conversation from the next is yours to set. |
| **Timing** | Hour-of-day and day-of-week columns, a weekday × hour heatmap, volume per month, median reply time (yours vs theirs), and each person's favourite time to talk. Filter by person. |
| **Spelling** | Your most frequent misspellings with suggested corrections and an example, misspellings per 1,000 words, who you misspell things to, and (flipped) who misspells the most at you. Text-speak like "lol" and "gonna" is ignored, as are URLs and names. |
| **Words & tone** | Most used words, emoji counts and favourites per person, and warm-minus-cold tone words by month and by person. Tone is a word count, not a mood reading: it cannot see sarcasm or "not great", and the app says so. |
| **Wrapped** | The year on one 1080 × 1350 card: total messages, who you talk to most, busiest hour, reply times, after-midnight share, longest streak, your word, your emoji, your worst typo. Downloads as a 2× PNG. |
| **Counterpoint** | Type or *listen* (microphone, in Chrome/Edge/Safari; the audio is transcribed by the browser, which is a cloud service in Chrome and Edge). A known myth is called a myth, with the source. A claim the facts cover is checked against the real figure ("close, but high: it's 62%, Gallup 2023"). "Studies show" and "everyone knows" get asked for their evidence. Then every claim like "70%", "seventy percent", "1 in 5", "most people", "up 40%", "15 to 30 times more likely" gets sourced facts of the same size, a punchline, and a one-line note on the actual logical gap. There's also a "random spurious correlation" button. |
| **Import** | WhatsApp exports, iMessage `chat.db`, Android "SMS Backup & Restore" XML, Telegram JSON, Instagram and Messenger downloads, Discord packages, or generic CSV/JSON. Zips are read in place, and the format is detected for you. |

Every chart has a **Table** toggle and hover tooltips, and follows the viewer's
light or dark theme. **Click any bar, heatmap cell, word, or point on a line**
to open the messages behind that number, filtered and searchable. Each person
keeps the same colour everywhere, assigned once from overall volume so
filtering never repaints the survivors.

## Counterpoint with Claude (optional)

The rule-based engine works offline and always runs. If you want sharper
phrasing, install the extra and start with `--llm`:

```bash
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=...        # or `ant auth login`
randostats serve --llm
```

The option only appears when a credential is actually found, and it starts
unticked: the claim is a thing that leaves your machine, so it is something you
turn on rather than something you remember to turn off. If a request fails, is
refused, or returns something unusable, the rule-based punchline stays on
screen rather than the answer disappearing.

The same fallback bounds the bill. No more than
`RANDOSTATS_LLM_CALLS_PER_HOUR` (200) requests an hour are sent, after which
the rule-based punchline is simply what you get.

Claude only *chooses among and rephrases* the facts the engine already matched
from `randostats/counterpoint/facts.json`. It is never asked to invent
statistics, so every number shown still traces to a listed source. Requests use
`claude-opus-5` with refusal fallbacks enabled; set `RANDOSTATS_MODEL` to change
the model.

## Timestamps

Exports disagree about what a timestamp means. WhatsApp and Telegram write the
wall-clock time the sender saw; iMessage, Android SMS, Discord and Meta write
an instant in UTC. Everything is normalised to local wall-clock time on the
machine doing the import, so the same 7pm message reads as 7pm whichever app
it came from. That is what "when do you talk to people" means, and it matches
what the chat app showed at the time.

If you imported before this was fixed, re-import: the old rows hold UTC and
will sit a few hours off in the hour-of-day charts.

## Accessibility

Every chart is one tab stop. Arrow keys walk its marks, Home and End jump to
either end, and Enter opens the messages behind the focused mark. The focused
mark is outlined and its values are announced in a live region, so the chart
reads the same by keyboard as by mouse. Escape closes the drill-down and
returns focus where it started.

Each chart also has a table view, colour is never the only channel (a legend
is always present for two or more series), animation respects
`prefers-reduced-motion`, and the palette is validated for contrast and
colour-vision deficiency in both light and dark themes.

## Performance

Measured on a generated archive of 250,000 messages across 40 people and nine
years, which is larger than most real ones:

| Step | Cold | Repeat view |
|---|---|---|
| Import and index | 5.7s | — |
| Slowest view (tone words) | 1.9s | 3ms |
| Misspellings | 1.7s | 3ms |
| Everything else | under 1.6s | 3ms |

Aggregates are pure functions of the imported messages, so each view is
computed once per import and cached until the next one. Importing or clearing
messages invalidates the cache; there is a test for that, because stale
numbers would be worse than slow ones.

## The phone app

`/m` is a separate, phone-shaped front end for the counterpoint half alone. It
needs none of your messages — only something someone said — so it is useful ten
seconds after you open it. Install it to the home screen and the shell works
offline; the answers still come from the app on your machine.

A phone is another machine, and the server answers only to loopback names
until told otherwise, so open `/m` from a phone by serving under a name the
phone can reach, with a password, for example `RANDOSTATS_PASSWORD='…'
randostats serve --host 0.0.0.0 --allow-host laptop.local`; the phone signs in
once. Read what that costs under [Running it](#running-it) and
[Deploy](#deploy) first: the name you add is one the server trusts with every
message you imported, and over plain HTTP the password crosses the network
readable.

Type or paste the statistic, or use your keyboard's dictation key. **Hands-free
listening is deliberately not offered up front.** On iOS the Speech Recognition
API is present inside an installed web app, reports success, and then silently
never returns a result — so the app only reveals the microphone after a probe
you start from the settings sheet has actually produced a transcript, and it
remembers the verdict.

Each answer can be shared as an image. The picture is rendered while you are
reading the card, not when you tap Share, because iOS only accepts a share that
is raised straight from the tap.

## What the engine answers, in order

1. **Myths.** `counterpoint/myths.json` lists popular "facts" that are false,
   each with the truth and a source. A sentence holding one gets "That's a
   myth." and no parallel: the answer to a myth is no, not an equally
   irrelevant number. Each myth carries example phrasings the tests check it
   catches, and the tests also check it ignores ordinary sentences.
2. **Fact-checks.** When a percentage is about something the facts measure
   (drinking, obesity, tattoos, the Earth's water...), it is checked against the
   real figure: fair, close, or off, and by how much. `_TOPICS` in `engine.py`
   maps subjects to facts by hand, and a check is skipped whenever the framing
   ("don't drink") or the population ("Brits") differs. A wrong verdict is
   worse than none.
3. **Appeals with no number.** "Studies show", "it's proven", "everyone
   knows", "millions of people" each get a question.
4. **Parallels**, as before.

A **share** ("70% of people") and a **change** ("up 40%", "20% less likely")
are different claims. A change is a multiplier on a baseline nobody stated, so
it is matched against ratio facts and gets its own fallacy note. "I'm 100%
sure" is not a statistic and is ignored.

## Fact packs and voices

`randostats/counterpoint/packs/*.json` holds themed fact packs (Sports, Money,
Deep time) on top of the core set, and `randostats/counterpoint/voices/*.json`
holds the sentence templates that phrase the punchlines: House, Deadpan
professor, Sports announcer, Victorian gentleman. Toggle both from chips on the
Counterpoint tab; the choice is stored in the database and survives a restart.

Adding either means dropping a JSON file in the right directory. A pack needs
an `id`, a `name`, and a list of facts with a source and year; it may also
carry `myths`. A fact whose statement starts with a proper noun ("Russia is…")
sets `"proper": true` so it keeps its capital mid-sentence. A voice needs
template lists for `percent` and `ratio` plus the `fallacy_*` and `appeal_*`
lists; any list you leave out falls back to the house lines. Placeholders available to a
template are `{fact}`, `{fact_lc}`, `{short}`, `{subject}`, `{subject_or_that}`,
`{gap}`, `{claim_v}` and `{fact_v}`; fact statements carry no trailing full
stop, so templates supply their own punctuation.

`RANDOSTATS_LOCKED=sports,money` marks packs unavailable. That is the seam a
paid unlock would use; everything shipped here is available.

## The fact database

`randostats/counterpoint/facts.json` holds about a hundred sourced statistics
(USGS, NASA, CDC, WHO, Pew, Gallup, Census...). Each has a value, a plain
statement, a short noun-phrase form for generated sentences, a source, and a
year. Values are approximate and dated; if you add facts, keep the source and
year and run the tests, which check the file is well formed.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python samples/make_sample.py        # optional: fake data to play with
randostats serve                     # http://127.0.0.1:8765
```

Open the app, go to **Overview** or **Import**, and either click **Load sample data** or import
your own export (see the help panel on the Import tab for how to export from each app).

It answers only to `localhost`, `127.0.0.1` and `[::1]`, and refuses reads and
writes that a browser tells it came from another site — anything carrying
`Sec-Fetch-Site: cross-site`, or an `Origin` that is not the name it was asked
under. A request that says nothing about where it came from is answered: that
is what keeps `curl` and `randostats import` working, and it is also what an
older browser sends, since Firefox before 90 and Safari before 16.4 attach no
`Sec-Fetch-Site` and a `no-cors` GET carries no `Origin` on any browser at
all. So that check is a latch rather than a wall. What does not depend on it
is that every memoised view is keyed on a value this app chose rather than on
whatever arrived — one of the five conversation gaps in the menu, a row count
clamped to 200, a name the store actually holds — so the cheap trick of
varying a parameter to make the machine walk your whole history again has
nothing left to vary but the row count. On loopback there is no login, so
whatever reaches the port can read every message you imported, and a page on
the internet can point a name it owns at 127.0.0.1 and try. Bound to anything
wider, it does not start without a password (see [Deploy](#deploy)). To reach
it under another name, say which, and set one:

```bash
RANDOSTATS_PASSWORD='at least twelve characters' randostats serve --host 0.0.0.0 --allow-host laptop.lan
```

Those are not two independent guards. "Another site" is measured against the
names this server answers to, so a name you add with `--allow-host` is a name
it trusts completely: any page that can make your browser resolve that name to
this machine — a hostile router, DNS on the local network, or plain DNS
rebinding for `'*'` — can then read and delete everything, exactly as the
front end can. Add a name only on a network where you would accept that.

You can also import from the terminal:

```bash
randostats import "WhatsApp Chat with Alex.txt" --me "Your Name"
randostats import ~/Library/Messages/chat.db --me "Me"
randostats import telegram-export.zip --me "Your Name"
randostats counter "seventy percent of people drink beer"
```

Where to find each export:

| Source | Where |
|---|---|
| WhatsApp | a chat → ⋮ → More → Export chat → Without media |
| iMessage | `~/Library/Messages/chat.db`, with Full Disk Access granted |
| Android SMS | the "SMS Backup & Restore" app's XML file |
| Telegram | Desktop → Settings → Advanced → Export Telegram data, JSON |
| Instagram / Messenger | request your information in JSON, then import the zip |
| Discord | Settings → Data & Privacy → Request all of my data |

Discord only exports what you wrote, so an import from it shows nothing as
received. The app says so when it notices, rather than letting you read a
half-empty chart as a fact about your friends.

The database lives at `data/randostats.db` by default; override with `--db` or
`RANDOSTATS_DB`.

### Deploy

randostats is a website served by its own server. The browser is the GUI; the
server parses, stores and counts, and it is also the host: it sets every
response header itself, answers a wrong address with a page of its own, and
refuses every file that is not part of the site. There is no `_headers`,
`.htaccess` or web-server configuration to keep in step with it.

**Beyond this machine, a password.** Bound to loopback (the default), nothing
asks for one. Bound to anything else (`--host 0.0.0.0`, a LAN address, a name),
`randostats serve` will not start unless `RANDOSTATS_PASSWORD` is set in its
environment, at least 12 characters: an environment variable, so it is in
neither your shell history nor `ps`. Set it as well whenever something else on
this machine publishes the port (a reverse proxy, a tunnel, a container's port
mapping), since the server sees those connections arrive on loopback. With a
password set, every page sends you to `/login` first, and the session then lasts
30 days in an HttpOnly, SameSite=Lax cookie, Secure over HTTPS. **Sign out** in
the header ends that session wherever a copy of the cookie went; restarting the
server ends them all, and is the way to change the password. The password is
compared as two fixed-length HMAC digests in constant time. After eight wrong
guesses in a minute a client waits the minute out; the client is the address
that connected, or behind a reverse proxy on this machine the address the proxy
writes in `X-Forwarded-For` (uvicorn believes that header only from 127.0.0.1 and
::1, or the addresses in `FORWARDED_ALLOW_IPS`), so set it there rather than let
the client's own value through. What is the same for every visitor (`/static/`,
the manifest, the service worker, `robots.txt`, `security.txt`) is served
without a session; the two pages, the sample and the API are not. Run with
`uvicorn --factory randostats.api:create_app` instead, the app reads
`RANDOSTATS_PASSWORD` itself, and without one refuses every connection that
arrives on an address other than loopback.

**HTTPS in front.** Over plain HTTP beyond loopback the password and the session
cookie cross the network readable. Put a TLS terminator in front, keep
randostats on 127.0.0.1 behind it, pass the original `Host` through (and name it
with `--allow-host`), and let the response headers through untouched rather
than add a policy of its own. With nginx, for example:

```nginx
server {
    listen 80;
    server_name stats.example.com;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    server_name stats.example.com;
    ssl_certificate     /etc/letsencrypt/live/stats.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/stats.example.com/privkey.pem;
    server_tokens off;
    client_max_body_size 257m;  # RANDOSTATS_MAX_UPLOAD_MB (256) and the form around it
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

```bash
RANDOSTATS_PASSWORD='…' randostats serve --allow-host stats.example.com
```

**One origin of its own.** Serve it at the root of a name of its own
(`stats.example.com`), not under a path of a site that hosts other things.
Every address the front end writes is root-absolute, so it does not run under a
sub-path; and the origin is what a browser isolates. Cookies, storage and
service workers are per origin, and a page from another app on the same origin
is same-origin with this one, so the cross-site checks cannot tell its requests
from yours.

**Response headers.** Every response (pages, files, JSON, refusals, 404s and
500s) carries these, set in `randostats/api.py` and nowhere else.
`tests/test_website.py` holds this block equal to the code, and the browser walk
(`e2e/`) holds every response it sees to it:

```http
Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; manifest-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: accelerometer=(), attribution-reporting=(), autoplay=(), browsing-topics=(), camera=(), clipboard-read=(), clipboard-write=(self), compute-pressure=(), display-capture=(), encrypted-media=(), fullscreen=(), gamepad=(), geolocation=(), gyroscope=(), hid=(), identity-credentials-get=(), idle-detection=(), interest-cohort=(), join-ad-interest-group=(), local-fonts=(), magnetometer=(), microphone=(self), midi=(), otp-credentials=(), payment=(), picture-in-picture=(), publickey-credentials-create=(), publickey-credentials-get=(), run-ad-auction=(), screen-wake-lock=(), serial=(), storage-access=(), usb=(), window-management=(), xr-spatial-tracking=()
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
```

Over HTTPS, meaning the request's own scheme or `X-Forwarded-Proto: https` from a
proxy uvicorn believes, the policy also ends `; upgrade-insecure-requests` and
`Strict-Transport-Security: max-age=31536000; includeSubDomains` is added. Over plain HTTP the first would
send every script and stylesheet to an `https://` that is not listening (measured
from a LAN address: the sign-in page loaded with no stylesheet and no script), and
the second is ignored.

| Directive | Why |
| --- | --- |
| `default-src 'none'` | Anything not named below is refused: fonts, frames, media, plugins and every other origin. |
| `script-src 'self'`, `style-src 'self'` | The app's own files only. Nothing inline: the charts used to write `style=""` attributes and now set the same declarations through the CSSOM or a class. |
| `img-src 'self' blob:` | The icons `/m` links, and the two share images (the Wrapped PNG and the phone's card), drawn through `blob:` URLs. No `data:`. |
| `connect-src 'self'` | The API. Nothing else is fetched. |
| `manifest-src 'self'`, `worker-src 'self'` | `/m`'s manifest and its service worker. The worker would run under `script-src 'self'` without the second (CSP falls back to it); it is stated so that a change to `script-src` cannot change what may run as a worker. |
| `base-uri 'none'`, `object-src 'none'`, `form-action 'none'` | No `<base>`, no plugins, and no native form submission: the import form and the sign-in form are submitted by script. |
| `frame-ancestors 'none'`, `X-Frame-Options: DENY` | Nothing may frame it (clickjacking); the second for browsers before CSP 2. |
| `Referrer-Policy: no-referrer` | A URL here can carry a contact's name or the sign-in page's `next=`. |
| `Permissions-Policy` | Every feature off but the microphone (Listen, and the phone's hands-free probe) and clipboard-write (the phone's Copy). The walk reads it back from Chromium. |
| `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy` | `same-origin`: no other window keeps a handle on this one, and no other site embeds its responses. |

Measured and not adopted: `require-trusted-types-for 'script'`. Every chart,
table and card is built as escaped markup assigned to `innerHTML` (the escaping
lint in `tests/test_security.py` holds each one), which Trusted Types refuses as
a string, so the first render fails under it. Adopting it means a policy at each
of those sinks, where a pass-through one would add nothing, or building them all
with DOM calls.

**Caching.** `Cache-Control: no-store` on every `/api/` answer, since each is one
person's messages and changes with every import; `no-cache` on everything else,
since no file name carries a version: an unchanged file costs a 304.

**A safety net.** `static/guard.js` loads first on every page that has a script.
When one of the page's scripts does not load, or throws before the page has
started, it puts a note with a Reload button at the top of the page instead of
leaving controls that do nothing; an error after the start gets a note that can
be dismissed. With JavaScript off, each page's `<noscript>` note says so.

**Not the site.** Only `/`, `/m`, `/login`, `/static/`, `/sw.js`,
`/manifest.webmanifest`, `/samples/sample_messages.json`, `/robots.txt`,
`/.well-known/security.txt` and the API answer. FastAPI's `/docs`, `/redoc` and
`/openapi.json` are off, nothing else under `samples/` is served, and any other
address is a 404, which in the browser is a page in the app's look with no
script. `robots.txt` asks every crawler to stay out; `security.txt` points to
the private report form [`SECURITY.md`](SECURITY.md) names, and its `Expires`
(2027-10-08) has to be renewed before then: `tests/test_website.py` fails once it
has passed.

**Launch checklist**, with `SITE` your HTTPS name:

```sh
curl -sI http://SITE/ | head -1                  # a 301 to https (the proxy)
curl -sI https://SITE/robots.txt | grep -i -E 'content-security|strict-transport|nosniff|referrer|permissions|cache-control'
curl -sI https://SITE/ | head -1                 # a 303 to /login
curl -s  https://SITE/api/status                 # {"detail":"sign in first"}
curl -sI https://SITE/.git/config | head -1      # a 404
```

Then sign in, load the sample, open every tab and the phone page, and check
that the browser console shows no `Content Security Policy` line.

## Development

```bash
ruff check .                         # pyflakes and the pycodestyle errors
pytest -q                            # parsers, stats, counterpoint, API, security, timestamps, the website
pip install -e ".[e2e]"              # Playwright, for the browser walk
python -m playwright install chromium
pytest -q e2e                        # both front ends driven in Chromium under the policy the server sends
```

The browser walk starts `randostats serve` itself and fails on any Content
Security Policy violation, page error, console error, failed request, request to
another origin, or response whose headers are not the policy; it also stops the
server to open the phone app from its offline cache, and checks the safety net,
the 404 page, the files that must not be served and the sign-in flow.

CI runs both on Python 3.10 and 3.12, with and without the `llm` extra, and
the browser walk after them, then installs the built package and runs it from outside the checkout, which
is the only way to tell that the data files ship. A separate job runs
`pip-audit`, pinned with its dependencies by hash, over the declared
dependencies and the `llm` extra twice: at the newest versions the ranges in
`pyproject.toml` resolve to on the day, and at the declared floors
(`.github/audit/floors.py`), which `pip install .` keeps in an environment
that already has them. A version between a floor and the newest release is
audited by neither.

For contributor setup, frontend checks, and project conventions, see
[`CONTRIBUTING.md`](CONTRIBUTING.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md).

The packaging job builds a wheel and a source distribution with `python -m build`,
installs the wheel, runs it from outside the checkout, and keeps both files as a
workflow artifact of that run. Publishing remains a deliberate manual step.

## Project layout

```
randostats/
  parsers/        whatsapp, imessage, smsbackup, telegram, discord, meta, generic csv/json, archive helpers
  lexicon.py      tone word lists and the emoji pattern
  stats.py        overview, frequency, timing, reply latency, conversation health, words,
                  misspellings, emoji, tone, search, wrapped
  store.py        SQLite persistence (idempotent imports, settings)
  auth.py         the password gate: RANDOSTATS_PASSWORD, sessions, the guess limit
  counterpoint/   facts.json, myths.json, packs/, voices/, engine.py, packs.py, llm.py (optional Claude)
  api.py          FastAPI routes, and the host: the response headers, the 404 page, sign-in
  static/         desktop shell, shared frontend core, overview, hand-drawn SVG charts,
                  drill-down drawer, Wrapped card, Web Speech API listening, phone PWA,
                  the safety net (guard.js), sign-in and 404 pages, robots.txt, security.txt
samples/          make_sample.py generates fake exports
e2e/              the browser walk (Playwright): the site under its own policy
```
