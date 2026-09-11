"""FastAPI application: import endpoints, stats endpoints, and the counterpoint engine."""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import parsers, stats
from .counterpoint import Claim, CounterpointEngine, packs as cp_packs
from .counterpoint import llm
from .store import DEFAULT_DB, Store

STATIC = Path(__file__).with_name("static")

# Imported files are other people's data, and a contact name can hold markup.
# The front end escapes everything it renders; this header is the second line,
# so an injected tag cannot run script even if an escape is ever missed.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

MAX_UPLOAD_BYTES = int(os.environ.get("RANDOSTATS_MAX_UPLOAD_MB", "256")) * 1024 * 1024

# Nothing but an import sends a body at all, and those bodies are one short
# sentence or a list of pack names.
MAX_JSON_BYTES = 1024 * 1024
# Boundary lines and part headers around the file itself.
MULTIPART_OVERHEAD = 8 * 1024

# Live-listening sessions are only there to stop the same spoken claim being
# answered twice. Ids come from us, and old ones fall off the end.
MAX_SESSIONS = 256

# Nothing here asks for a password: whatever reaches the port can read every
# message and delete the lot. A page on the internet can point a name it owns
# at 127.0.0.1 (DNS rebinding) and then talk to this server *same-origin*, so
# CORS never comes into it. Two checks stop that: the Host header has to be a
# name we agreed to serve, and a mutating request has to come from us.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# A pasted article holds thousands of claims, and each one costs CPU here and,
# with --llm, a paid API call. Bound the text, and the fan-out (see
# extract_claims, which bounds the claims).
MAX_TEXT_CHARS = 4000
MAX_LLM_GROUPS = 4

# Every aggregate view is memoised on the parameters the *caller* chose (see
# `remember`), so a parameter the caller can vary freely is a parameter that
# can mint an unbounded number of distinct keys - each one a full walk of the
# corpus, and every 256 of them clearing the memo the user's own tabs were
# using. The front end asks for fifteen to thirty rows and one of five gap
# values; bound what reaches the key to roughly that, the way /api/messages
# already bounds its own limit.
#
# This one is a clamp, not a set, so it is the weakest of the three: cycling
# `limit` still mints one key per value, up to MAX_ROWS for a view (measured:
# 60 values across /api/stats/words and /api/stats/contacts = 120 keys), which
# combined across views is more than Derived.LIMIT holds. Closing it properly
# means computing each view once at the cap and slicing the answer down, which
# changes what several of them return - worth doing, but not something to
# smuggle into a security fix. The gap and the contact name, the two the audit
# measured, are sets and cannot be cycled at all.
MAX_ROWS = 200
# Conversation gaps are chosen from a five-entry select, and these are the five.
# Rounding the caller's float was not a bound: a tenth of an hour across
# `le=24*365` is 87,600 distinct values against a 256-entry memo, so cycling
# the gap still cleared it on every request. Snapping to a set the app decides
# the size of means a fresh key cannot be minted at all - and unlike the
# cross-site check, that holds whatever headers a request carries or omits.
GAP_CHOICES = (1.0, 3.0, 6.0, 12.0, 24.0)


def _rows(limit: int | None) -> int | None:
    """A row count that came from outside, bounded to something sane."""
    return None if limit is None else max(1, min(limit, MAX_ROWS))


def _gap(hours: float) -> float:
    """The gap this app offers that is nearest the one that was asked for."""
    return min(GAP_CHOICES, key=lambda choice: (abs(choice - hours), choice))


def _contact(contact: str | None) -> str | None:
    """An empty ``?contact=`` means everyone, exactly as an omitted one does.

    Every optional filter in stats.py is spelled ``if contact and ...``, so an
    empty string has always meant "no filter" down there; `stats.timing` now
    agrees with the rest. Deciding it here as well keeps the answer from
    depending on which endpoint was asked, and keeps ``?contact=`` from being
    a second memo key for the view that no contact at all already has.
    ``/api/stats/members`` is the exception on purpose: it is a breakdown of
    one conversation, and the empty string names no conversation, so it goes
    through `known_contact` and comes back empty.
    """
    return contact or None


def _too_large(limit: int) -> str:
    megabytes = max(1, limit // (1024 * 1024))
    return (f"file is larger than the {megabytes} MB limit; "
            "raise RANDOSTATS_MAX_UPLOAD_MB if you really need to")


def _declared_length(request) -> int:
    """What the request says it is about to send, or 0 when it does not say."""
    value = request.headers.get("content-length", "")
    return int(value) if value.isdigit() else 0


def _netloc(value: str) -> str:
    """``host[:port]`` out of a Host or Origin header, lowercased."""
    value = value.strip().lower()
    if "//" in value:  # an Origin carries a scheme
        value = value.split("//", 1)[1]
    return value.split("/", 1)[0]


def _hostname(value: str) -> str:
    """The name on its own: no scheme, no port, no IPv6 brackets."""
    netloc = _netloc(value)
    if netloc.startswith("["):  # [::1]:8765
        return netloc[1:].partition("]")[0]
    return netloc.rpartition(":")[0] if netloc.count(":") == 1 else netloc


class Derived:
    """Per-import memo for the aggregate views.

    Stats handlers are sync, so Starlette runs them on worker threads, while
    an import clears this from the event loop the moment it finishes. A value
    is therefore returned from the local name: storing it and then reading it
    back out of the dict is a KeyError waiting for that clear to land in
    between, which is a 500 on the refresh every import triggers.
    """

    LIMIT = 256  # a very long session with many filters

    def __init__(self) -> None:
        self._values: dict[tuple, object] = {}

    def get_or_compute(self, key: tuple, compute):
        try:
            return self._values[key]
        except KeyError:
            pass
        value = compute()
        if len(self._values) > self.LIMIT:
            self._values.clear()
        self._values[key] = value
        return value

    def clear(self) -> None:
        self._values.clear()


class PackConfig(BaseModel):
    """Which fact packs are loaded and which voice writes the punchlines."""

    packs: list[str] | None = None
    voice: str | None = None


class CounterRequest(BaseModel):
    # Long text is not a feature here: one sentence is the whole idea, and an
    # unbounded one is the cheapest way to spend the owner's CPU and API budget.
    text: str = Field(max_length=MAX_TEXT_CHARS)
    session: str | None = None
    per_claim: int = 2
    llm: bool = True


def create_app(db_path: Path | str = DEFAULT_DB, use_llm: bool | None = None,
               allowed_hosts: Sequence[str] | None = None) -> FastAPI:
    app = FastAPI(title="randostats", version="0.1.0")
    # Host names this server answers to. "*" turns the check off for someone
    # who knows what they are doing (see `randostats serve --allow-host`).
    hosts = {_hostname(h) for h in (LOOPBACK_HOSTS if allowed_hosts is None else allowed_hosts)}
    any_host = "*" in hosts

    def refuse(request) -> JSONResponse | None:
        """Why this request must not be answered at all, if it must not."""
        host = request.headers.get("host", "")
        if not any_host and _hostname(host) not in hosts:
            # A name that resolves to this machine is not a name we serve.
            return JSONResponse({"detail": "invalid host header; pass --allow-host to serve this name"},
                                status_code=400)
        # multipart/form-data needs no CORS preflight, so /api/import is
        # reachable from any page in the browser unless the browser's own
        # account of where the request came from is checked.
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        cross_site = (site is not None and site not in ("same-origin", "none")) or \
                     (origin is not None and _netloc(origin) != _netloc(host))
        # A request that names no site at all is treated as first-party, which
        # is what keeps curl, `randostats import` and any other local script
        # working. A browser is not obliged to name one: Fetch attaches an
        # Origin to a GET only when the response tainting is cors, so a
        # cross-origin `fetch(url, {mode: "no-cors"})` carries none, and a
        # browser without Fetch Metadata (Firefox < 90, Safari < 16.4) sends
        # no Sec-Fetch-Site either. On those the check below never fires. It
        # is therefore the cheap half of the defence, not the whole of it:
        # what bounds the work a stranger can ask for is that every expensive
        # view keys its memo on a value this app chose (MAX_ROWS,
        # GAP_CHOICES, known_contact), which no header can route around.
        # A cross-site *read* of the JSON API is not something the front end
        # ever makes, and the page that makes it cannot read the reply either
        # - there are no CORS headers. It can still make this machine do the
        # work, and an aggregate view is seconds of CPU on a real corpus, so
        # a tab left open on any web page could hold a core indefinitely.
        # Refuse those whatever the method. "/", "/static/*" and "/samples/*"
        # stay open, so a bookmark or a typed URL still opens the app.
        if cross_site and request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        if request.method not in SAFE_METHODS:
            if cross_site:
                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
            # Starlette spools a multipart body to a temporary file before the
            # handler is ever called, so a check inside the handler bounds what
            # the parser sees, not what the machine writes. Refuse on the
            # length the request declares; the handler still checks what
            # actually arrived, for a request that declares nothing.
            if request.url.path == "/api/import":
                if _declared_length(request) > MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD:
                    return JSONResponse({"detail": _too_large(MAX_UPLOAD_BYTES)}, status_code=413)
            elif _declared_length(request) > MAX_JSON_BYTES:
                megabytes = MAX_JSON_BYTES // (1024 * 1024)
                return JSONResponse({"detail": f"request body is larger than the {megabytes} MB limit"},
                                    status_code=413)
        return None

    def decorate(response):
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.middleware("http")
    async def security_guard(request, call_next):
        refusal = refuse(request)
        return decorate(refusal if refusal is not None else await call_next(request))

    # Starlette builds its stack as [ServerErrorMiddleware] + user middleware +
    # [ExceptionMiddleware] + router, so the middleware above is *inside* the
    # 500 handler: an exception that escapes a route is turned into a response
    # after it has unwound, and that response carried none of these headers.
    # Nothing in the shipped API renders a user string into a 500 today; the
    # headers are the second line of defence, and the point of a second line
    # is that it is already there when the first one gives way.
    @app.exception_handler(Exception)
    def unhandled(request, exc):
        return decorate(JSONResponse({"detail": "internal server error"}, status_code=500))
    store = Store(db_path)

    def build_engine() -> CounterpointEngine:
        enabled = {p for p in (store.get_setting("packs", "") or "").split(",") if p}
        return CounterpointEngine(packs=enabled, voice=store.get_setting("voice", cp_packs.DEFAULT_VOICE))

    cp = {"engine": build_engine()}
    sessions: dict[str, set[str]] = {}
    if use_llm is None:
        use_llm = os.environ.get("RANDOSTATS_LLM", "").lower() in ("1", "true", "yes", "on")
    llm_on = bool(use_llm and llm.available())

    @lru_cache(maxsize=1)
    def speller() -> stats.Speller:
        return stats.Speller()

    # Building the dictionary takes over a second, and it is the only cold
    # path a user waits on. Do it while they are still choosing a file.
    threading.Thread(target=speller, name="speller-warmup", daemon=True).start()

    @lru_cache(maxsize=1)
    def messages_cache(version: int):  # version busts the cache after an import
        return store.all_messages()

    state = {"version": 0}
    # Aggregates are pure functions of the imported messages, and a quarter of a
    # million of them take seconds to walk. Compute each view once per import.
    derived = Derived()

    def messages():
        return messages_cache(state["version"])

    def remember(name: str, compute, **params):
        return derived.get_or_compute((state["version"], name, tuple(sorted(params.items()))), compute)

    def known_contact(contact: str | None) -> bool:
        """Is this a name the store actually holds? None means everyone.

        A name it does not hold is answered with the empty view whatever the
        name is, so letting one through would walk the whole corpus and take
        a memo slot for a string the caller invented. The set of real names
        is itself one memo entry per import.
        """
        if contact is None:
            return True
        return contact in remember("contact_names", lambda: frozenset(m.contact for m in messages()))

    def bump():
        state["version"] += 1
        derived.clear()

    # -- static ---------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    samples_dir = Path(__file__).resolve().parent.parent / "samples"
    if samples_dir.is_dir():
        app.mount("/samples", StaticFiles(directory=samples_dir), name="samples")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    # -- import ---------------------------------------------------------------
    @app.get("/api/status")
    def status():
        return {
            "messages": store.count(),
            "contacts": len(store.contacts()),
            "self_name": store.get_setting("self_name", ""),
            "formats": sorted(parsers.PARSERS),
            "llm": llm_on,
        }

    @app.post("/api/import")
    async def import_file(file: UploadFile = File(...), self_name: str = Form(...), fmt: str = Form("auto"),
                          contact: str | None = Form(None)):
        # The multipart header usually declares the size; refuse before reading.
        declared = getattr(file, "size", None)
        if declared is not None and declared > MAX_UPLOAD_BYTES:
            raise HTTPException(413, _too_large(MAX_UPLOAD_BYTES))
        data = await file.read()
        if not data:
            raise HTTPException(400, "empty file")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, _too_large(MAX_UPLOAD_BYTES))
        if fmt == "auto":
            try:
                # Sniffing a zip opens it, which an archive with millions of
                # members makes expensive; that refusal is a 413 like any other.
                fmt = parsers.detect_format(file.filename or "", data) or ""
            except parsers.archive.ArchiveTooLarge as exc:
                raise HTTPException(413, str(exc)) from exc
            if not fmt:
                raise HTTPException(400, "could not work out the export format; pick one from the list")
        def read_and_store() -> list:
            if fmt == "whatsapp" and contact:
                return list(parsers.whatsapp.parse(data, self_name, contact=contact))
            return parsers.parse(fmt, data, self_name)

        try:
            # Parsing and inserting a large export takes seconds of CPU; off
            # the event loop it does not stall every other request.
            msgs = await run_in_threadpool(read_and_store)
        except parsers.archive.ArchiveTooLarge as exc:
            raise HTTPException(413, str(exc)) from exc
        except Exception as exc:  # parser errors are user-facing
            raise HTTPException(400, f"could not parse as {fmt}: {exc}") from exc
        if not msgs:
            raise HTTPException(400, f"parsed as {fmt} but found no messages; check the format and your name")
        added = await run_in_threadpool(store.add_messages, msgs)
        store.set_setting("self_name", self_name)
        bump()
        directions = {m.direction for m in msgs}
        note = None
        if directions == {"sent"}:
            note = ("This export only contains messages you sent, so anything about what you received will be empty. "
                    "Discord exports are like this by design.")
        elif directions == {"received"}:
            note = "Nothing in this file was recognised as yours. Check that your name matches the export exactly."
        return {"format": fmt, "parsed": len(msgs), "added": added, "total": store.count(),
                "contacts": sorted({m.contact for m in msgs}), "note": note}

    @app.delete("/api/messages")
    def clear():
        store.clear()
        bump()
        return {"messages": 0}

    # -- stats ----------------------------------------------------------------
    @app.get("/api/stats/overview")
    def overview():
        return remember("overview", lambda: stats.overview(messages()))

    @app.get("/api/stats/contacts")
    def contacts(limit: int | None = None):
        limit = _rows(limit)
        return remember("contacts", lambda: stats.contact_frequency(messages(), limit=limit), limit=limit)

    @app.get("/api/stats/timing")
    def timing(contact: str | None = None):
        contact = _contact(contact)
        if not known_contact(contact):
            return stats.timing([], contact=contact)
        return remember("timing", lambda: stats.timing(messages(), contact=contact), contact=contact)

    @app.get("/api/stats/timing/contacts")
    def timing_contacts(limit: int = 20):
        limit = _rows(limit)
        return remember("peaks", lambda: stats.contact_peaks(messages(), limit=limit), limit=limit)

    @app.get("/api/messages")
    def search_messages(q: str | None = None, word: str | None = None, contact: str | None = None,
                        sender: str | None = None, direction: str | None = None, hour: int | None = None,
                        weekday: int | None = None, month: str | None = None, date: str | None = None,
                        limit: int = 100, offset: int = 0):
        if direction and direction not in ("sent", "received"):
            raise HTTPException(400, "direction must be sent or received")
        return stats.search(messages(), q=q, word=word, contact=contact, sender=sender, direction=direction,
                            hour=hour, weekday=weekday, month=month, date=date,
                            limit=max(1, min(limit, 500)), offset=max(0, offset))

    @app.get("/api/stats/conversations")
    def conversations(gap_hours: float = Query(6.0, gt=0, le=24 * 365),
                      limit: int | None = Query(None, ge=1, le=1000)):
        # An unbounded float here reached timedelta(hours=inf), which is an
        # OverflowError and a 500; "nan" is a ValueError one line later, and
        # both are unencodable in the JSON response besides - so the bounds on
        # Query stay. Snapping is the other half: bounded or not, a float the
        # caller picks freely is an unbounded supply of memo keys, and there
        # are exactly five gaps this app knows how to be asked about. The
        # answer echoes the gap actually used, so a caller who asked for
        # something finer can see they did not get it.
        gap_hours = _gap(gap_hours)
        rows = remember("health", lambda: stats.conversation_health(messages(), gap_hours=gap_hours), gap=gap_hours)
        # The summary always covers everyone; `limit` only trims what is charted.
        return {"gap_hours": gap_hours, "summary": stats.conversation_summary(rows), "rows": rows[:limit] if limit else rows}

    @app.get("/api/stats/members")
    def members(contact: str):
        if not known_contact(contact):
            return stats.group_members([], contact)
        return remember("members", lambda: stats.group_members(messages(), contact), contact=contact)

    @app.get("/api/stats/misspellings")
    def misspellings(direction: str = "sent", limit: int = 50, contact: str | None = None):
        if direction not in ("sent", "received"):
            raise HTTPException(400, "direction must be sent or received")
        limit = _rows(limit)
        contact = _contact(contact)
        if not known_contact(contact):
            return stats.misspellings([], speller(), direction=direction, limit=limit, contact=contact)
        return remember("misspellings", lambda: stats.misspellings(messages(), speller(), direction=direction,
                                                                   limit=limit, contact=contact),
                        direction=direction, limit=limit, contact=contact)

    @app.get("/api/stats/emoji")
    def emoji(limit: int = 30, contact: str | None = None):
        limit = _rows(limit)
        contact = _contact(contact)
        if not known_contact(contact):
            return stats.emoji_stats([], limit=limit, contact=contact)
        return remember("emoji", lambda: stats.emoji_stats(messages(), limit=limit, contact=contact),
                        limit=limit, contact=contact)

    @app.get("/api/stats/tone")
    def tone(contact: str | None = None):
        contact = _contact(contact)
        if not known_contact(contact):
            return stats.tone([], contact=contact)
        return remember("tone", lambda: stats.tone(messages(), contact=contact), contact=contact)

    @app.get("/api/stats/words")
    def words(direction: str | None = None, limit: int = 50):
        # Unvalidated, this was the third free-form memo key: any string at
        # all came back as an empty view and kept a slot. Its siblings above
        # already refuse anything but the two directions.
        if direction and direction not in ("sent", "received"):
            raise HTTPException(400, "direction must be sent or received")
        limit = _rows(limit)
        return remember("words", lambda: stats.word_frequency(messages(), direction=direction, limit=limit),
                        direction=direction, limit=limit)

    @app.get("/api/wrapped")
    def wrapped(year: int | None = None):
        msgs = messages()
        available = stats.years(msgs)
        if year is None and available:
            year = available[-1]
        if year not in available:
            # A year with nothing in it is the empty card whatever the number,
            # so it neither walks the corpus nor takes a memo slot of its own.
            return {"years": available, "card": stats.wrapped([], year=year, speller=speller())}
        card = dict(remember("wrapped", lambda: stats.wrapped(msgs, year=year, speller=speller()), year=year))
        # Tie the two halves of the app together: answer one of your own
        # percentages with a real statistic of the same size.
        if not card.get("empty"):
            claim = Claim("percent", round(card["sent_share"] * 100, 1), f"{card['sent_share']:.0%} of these messages", "you wrote them")
            match = cp["engine"].match(claim, k=1)
            if match:
                fact, gap = match[0]
                card["counterpoint"] = {"statement": fact.statement, "source": fact.source, "year": fact.year, "gap": round(gap, 1)}
        return {"years": available, "card": card}

    # -- counterpoint ---------------------------------------------------------
    @app.post("/api/counterpoint")
    def counterpoint(req: CounterRequest):
        # Only ids we issued are honoured, so a client cannot grow this map.
        session = req.session
        seen = sessions.get(session) if session else None
        results = cp["engine"].respond(req.text, per_claim=max(1, min(req.per_claim, 5)), seen=seen)
        payload = {"session": session, "results": [r.to_dict() for r in results]}
        if results and llm_on and req.llm:
            by_claim: dict[str, list] = {}
            for r in results:
                by_claim.setdefault(r.claim.key, []).append(r)
            # One round trip per claim, run together: a sentence with three
            # claims should not take three times as long to answer. Capped, so
            # one request cannot fan out into an unbounded number of paid
            # calls; the rule-based answer still covers the claims past it.
            groups = list(by_claim.items())[:MAX_LLM_GROUPS]
            with ThreadPoolExecutor(max_workers=min(4, len(groups))) as pool:
                sharpened = pool.map(lambda g: (g[0], llm.sharpen(g[1][0].claim.raw, g[1])), groups)
                payload["llm"] = {key: value for key, value in sharpened if value}
        return payload

    @app.post("/api/counterpoint/session")
    def new_session():
        while len(sessions) >= MAX_SESSIONS:
            sessions.pop(next(iter(sessions)))  # dicts keep insertion order
        sid = uuid.uuid4().hex[:12]
        sessions[sid] = set()
        return {"session": sid}

    @app.get("/api/counterpoint/random")
    def random_pair():
        return cp["engine"].spurious_pair()

    @app.get("/api/counterpoint/facts")
    def facts():
        return [f.to_dict() for f in cp["engine"].facts]

    @app.get("/api/counterpoint/packs")
    def counterpoint_packs():
        enabled = {p for p in (store.get_setting("packs", "") or "").split(",") if p}
        return {"packs": cp_packs.list_packs(enabled), "voices": cp_packs.list_voices(),
                "voice": cp["engine"].voice["id"], "facts": len(cp["engine"].facts)}

    @app.post("/api/counterpoint/packs")
    def set_counterpoint_packs(config: PackConfig):
        known = {p["id"] for p in cp_packs.list_packs()}
        if config.packs is not None:
            unknown = set(config.packs) - known
            if unknown:
                raise HTTPException(400, f"unknown pack(s): {', '.join(sorted(unknown))}")
            store.set_setting("packs", ",".join(sorted(set(config.packs) - {"core"})))
        if config.voice is not None:
            if config.voice not in {v["id"] for v in cp_packs.list_voices()}:
                raise HTTPException(400, f"unknown voice: {config.voice}")
            store.set_setting("voice", config.voice)
        cp["engine"] = build_engine()
        sessions.clear()  # old answers were written in the old voice
        enabled = {p for p in (store.get_setting("packs", "") or "").split(",") if p}
        return {"packs": cp_packs.list_packs(enabled), "voices": cp_packs.list_voices(),
                "voice": cp["engine"].voice["id"], "facts": len(cp["engine"].facts)}

    app.state.store = store
    # The memo is the thing a caller must not be able to fill or evict, so the
    # tests assert on it directly rather than on a status code that looks the
    # same either way.
    app.state.derived = derived
    return app


# No module-level ``app = create_app()``: importing this module would then open
# a second store (in the default location, whatever --db said) and warm a second
# dictionary. For ``uvicorn`` directly, use the factory:
#
#     uvicorn --factory randostats.api:create_app
