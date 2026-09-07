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
| **Conversations** | Who opens conversations and who gets the last word, double texts, longest silences, a reply-time dumbbell of you against them, and a per-member breakdown inside group chats. The silence gap that separates one conversation from the next is yours to set. |
| **Timing** | Hour-of-day and day-of-week columns, a weekday × hour heatmap, volume per month, median reply time (yours vs theirs), and each person's favourite time to talk. Filter by person. |
| **Spelling** | Your most frequent misspellings with suggested corrections and an example, misspellings per 1,000 words, who you misspell things to, and (flipped) who misspells the most at you. Text-speak like "lol" and "gonna" is ignored, as are URLs and names. |
| **Words & tone** | Most used words, emoji counts and favourites per person, and warm-minus-cold tone words by month and by person. Tone is a word count, not a mood reading: it cannot see sarcasm or "not great", and the app says so. |
| **Wrapped** | The year on one 1080 × 1350 card: total messages, who you talk to most, busiest hour, reply times, after-midnight share, longest streak, your word, your emoji, your worst typo. Downloads as a 2× PNG. |
| **Counterpoint** | Type or *listen* (microphone, in Chrome/Edge/Safari). Every claim like "70%", "seventy percent", "1 in 5", "three out of four", "most people", or "3 times more likely" gets one or more sourced facts of the same size, a punchline, and a one-line note on the actual logical gap. There's also a "random spurious correlation" button. |
| **Import** | WhatsApp exports, iMessage `chat.db`, Android "SMS Backup & Restore" XML, Telegram JSON, Instagram and Messenger downloads, Discord packages, or generic CSV/JSON. Zips are read in place, and the format is detected for you. |

Every chart has a **Table** toggle and hover tooltips, and follows the viewer's
light or dark theme. **Click any bar, heatmap cell, word, or point on a line**
to open the messages behind that number, filtered and searchable. Each person
keeps the same colour everywhere, assigned once from overall volume so
filtering never repaints the survivors.

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

## Fact packs and voices

`randostats/counterpoint/packs/*.json` holds themed fact packs (Sports, Money,
Deep time) on top of the core set, and `randostats/counterpoint/voices/*.json`
holds the sentence templates that phrase the punchlines: House, Deadpan
professor, Sports announcer, Victorian gentleman. Toggle both from chips on the
Counterpoint tab; the choice is stored in the database and survives a restart.

Adding either means dropping a JSON file in the right directory. A pack needs
an `id`, a `name`, and a list of facts with a source and year. A voice needs
template lists for `percent` and `ratio` plus the three `fallacy_*` lists; any
list you leave out falls back to the house lines. Placeholders available to a
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

## Development

```bash
pytest            # parsers, stats, counterpoint, API
```

Layout:

```
randostats/
  parsers/        whatsapp, imessage, smsbackup, telegram, discord, meta, generic csv/json, archive helpers
  lexicon.py      tone word lists and the emoji pattern
  stats.py        overview, frequency, timing, reply latency, conversation health, words,
                  misspellings, emoji, tone, search, wrapped
  store.py        SQLite persistence (idempotent imports, settings)
  counterpoint/   facts.json, packs/, voices/, engine.py, packs.py, llm.py (optional Claude)
  api.py          FastAPI routes
  static/         single-page front end, hand-drawn SVG charts, drill-down drawer,
                  Wrapped card, Web Speech API listening
samples/          make_sample.py generates fake exports
```
