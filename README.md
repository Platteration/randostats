# randostats

Statistics on the messages you send and receive, plus a **counterpoint engine**
that listens to an argument and answers any statistic with a real one of the
same magnitude that has nothing to do with it.

> "Seventy percent of people drink beer, so it's fine."
> "Counterpoint: about 71% of Earth's surface is covered by water. By the same
> logic, drinking beer is caused by the ocean. Source: USGS, 2019."

Everything runs locally. Your messages go into a SQLite file on your machine
and never leave it.

## What it does

| Tab | What you see |
|---|---|
| **People** | Messages per person, split into sent and received, share you wrote, messages per day, who writes the longest messages. |
| **Timing** | Hour-of-day and day-of-week columns, a weekday × hour heatmap, volume per month, median reply time (yours vs theirs), and each person's favourite time to talk. Filter by person. |
| **Spelling** | Your most frequent misspellings with suggested corrections and an example, misspellings per 1,000 words, who you misspell things to, and (flipped) who misspells the most at you. Text-speak like "lol" and "gonna" is ignored, as are URLs and names. |
| **Words** | Most used words, sent or received. |
| **Counterpoint** | Type or *listen* (microphone, in Chrome/Edge/Safari). Every claim like "70%", "seventy percent", "1 in 5", "three out of four", "most people", or "3 times more likely" gets one or more sourced facts of the same size, a punchline, and a one-line note on the actual logical gap. There's also a "random spurious correlation" button. |
| **Import** | WhatsApp exports, iMessage `chat.db`, Android "SMS Backup & Restore" XML, or generic CSV/JSON. |

Every chart has a **Table** toggle and hover tooltips, and follows the viewer's
light or dark theme.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python samples/make_sample.py        # optional: fake data to play with
randostats serve                     # http://127.0.0.1:8765
```

Open the app, go to **Import**, and either click **Load sample data** or import
your own export (see the help panel on that tab for how to export from each app).

You can also import from the terminal:

```bash
randostats import "WhatsApp Chat with Alex.txt" --me "Your Name"
randostats import ~/Library/Messages/chat.db --me "Me"
randostats counter "seventy percent of people drink beer"
```

The database lives at `data/randostats.db` by default; override with `--db` or
`RANDOSTATS_DB`.

## Counterpoint with Claude (optional)

The rule-based engine works offline and always runs. If you want sharper
phrasing, install the extra and start with `--llm`:

```bash
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=...        # or `ant auth login`
randostats serve --llm
```

Claude only *chooses among and rephrases* the facts the engine already matched
from `randostats/counterpoint/facts.json`. It is never asked to invent
statistics, so every number shown still traces to a listed source. Requests use
`claude-opus-5` with refusal fallbacks enabled; set `RANDOSTATS_MODEL` to change
the model.

## The fact database

`randostats/counterpoint/facts.json` holds about a hundred sourced statistics
(USGS, NASA, CDC, WHO, Pew, Gallup, Census...). Each has a value, a plain
statement, a short noun-phrase form for generated sentences, a source, and a
year. Values are approximate and dated; if you add facts, keep the source and
year and run the tests, which check the file is well formed.

## Development

```bash
pytest            # parsers, stats, counterpoint, API
```

Layout:

```
randostats/
  parsers/        whatsapp, imessage (chat.db), smsbackup (xml), generic csv/json
  stats.py        overview, per-contact frequency, timing, reply latency, words, misspellings
  store.py        SQLite persistence (idempotent imports)
  counterpoint/   facts.json, engine.py (claims + matching + punchlines), llm.py (optional Claude)
  api.py          FastAPI routes
  static/         single-page front end, hand-drawn SVG charts, Web Speech API listening
samples/          make_sample.py generates fake exports
```
