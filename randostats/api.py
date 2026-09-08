"""FastAPI application: import endpoints, stats endpoints, and the counterpoint engine."""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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

# Live-listening sessions are only there to stop the same spoken claim being
# answered twice. Ids come from us, and old ones fall off the end.
MAX_SESSIONS = 256


class PackConfig(BaseModel):
    """Which fact packs are loaded and which voice writes the punchlines."""

    packs: list[str] | None = None
    voice: str | None = None


class CounterRequest(BaseModel):
    text: str
    session: str | None = None
    per_claim: int = 2
    llm: bool = True


def create_app(db_path: Path | str = DEFAULT_DB, use_llm: bool | None = None) -> FastAPI:
    app = FastAPI(title="randostats", version="0.1.0")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response
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
    derived: dict[tuple, object] = {}

    def messages():
        return messages_cache(state["version"])

    def remember(name: str, compute, **params):
        key = (state["version"], name, tuple(sorted(params.items())))
        if key not in derived:
            if len(derived) > 256:  # a very long session with many filters
                derived.clear()
            derived[key] = compute()
        return derived[key]

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
            raise HTTPException(413, f"file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit; "
                                     "raise RANDOSTATS_MAX_UPLOAD_MB if you really need to")
        data = await file.read()
        if not data:
            raise HTTPException(400, "empty file")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit; "
                                     "raise RANDOSTATS_MAX_UPLOAD_MB if you really need to")
        if fmt == "auto":
            fmt = parsers.detect_format(file.filename or "", data) or ""
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
        return remember("contacts", lambda: stats.contact_frequency(messages(), limit=limit), limit=limit)

    @app.get("/api/stats/timing")
    def timing(contact: str | None = None):
        return remember("timing", lambda: stats.timing(messages(), contact=contact), contact=contact)

    @app.get("/api/stats/timing/contacts")
    def timing_contacts(limit: int = 20):
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
    def conversations(gap_hours: float = 6.0, limit: int | None = None):
        rows = remember("health", lambda: stats.conversation_health(messages(), gap_hours=gap_hours), gap=gap_hours)
        # The summary always covers everyone; `limit` only trims what is charted.
        return {"gap_hours": gap_hours, "summary": stats.conversation_summary(rows), "rows": rows[:limit] if limit else rows}

    @app.get("/api/stats/members")
    def members(contact: str):
        return remember("members", lambda: stats.group_members(messages(), contact), contact=contact)

    @app.get("/api/stats/misspellings")
    def misspellings(direction: str = "sent", limit: int = 50, contact: str | None = None):
        if direction not in ("sent", "received"):
            raise HTTPException(400, "direction must be sent or received")
        return remember("misspellings", lambda: stats.misspellings(messages(), speller(), direction=direction,
                                                                   limit=limit, contact=contact),
                        direction=direction, limit=limit, contact=contact)

    @app.get("/api/stats/emoji")
    def emoji(limit: int = 30, contact: str | None = None):
        return remember("emoji", lambda: stats.emoji_stats(messages(), limit=limit, contact=contact),
                        limit=limit, contact=contact)

    @app.get("/api/stats/tone")
    def tone(contact: str | None = None):
        return remember("tone", lambda: stats.tone(messages(), contact=contact), contact=contact)

    @app.get("/api/stats/words")
    def words(direction: str | None = None, limit: int = 50):
        return remember("words", lambda: stats.word_frequency(messages(), direction=direction, limit=limit),
                        direction=direction, limit=limit)

    @app.get("/api/wrapped")
    def wrapped(year: int | None = None):
        msgs = messages()
        available = stats.years(msgs)
        if year is None and available:
            year = available[-1]
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
            # claims should not take three times as long to answer.
            groups = list(by_claim.items())
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
    return app


app = create_app()
