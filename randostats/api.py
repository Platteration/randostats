"""FastAPI application: import endpoints, stats endpoints, and the counterpoint engine."""

from __future__ import annotations

import os
import uuid
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import parsers, stats
from .counterpoint import Claim, CounterpointEngine, packs as cp_packs
from .counterpoint import llm
from .store import DEFAULT_DB, Store

STATIC = Path(__file__).with_name("static")


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

    @lru_cache(maxsize=1)
    def messages_cache(version: int):  # version busts the cache after an import
        return store.all_messages()

    state = {"version": 0}

    def messages():
        return messages_cache(state["version"])

    def bump():
        state["version"] += 1

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
        data = await file.read()
        if not data:
            raise HTTPException(400, "empty file")
        if fmt == "auto":
            fmt = parsers.detect_format(file.filename or "", data) or ""
            if not fmt:
                raise HTTPException(400, "could not work out the export format; pick one from the list")
        try:
            if fmt == "whatsapp" and contact:
                msgs = list(parsers.whatsapp.parse(data, self_name, contact=contact))
            else:
                msgs = parsers.parse(fmt, data, self_name)
        except Exception as exc:  # parser errors are user-facing
            raise HTTPException(400, f"could not parse as {fmt}: {exc}") from exc
        if not msgs:
            raise HTTPException(400, f"parsed as {fmt} but found no messages; check the format and your name")
        added = store.add_messages(msgs)
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
        return stats.overview(messages())

    @app.get("/api/stats/contacts")
    def contacts(limit: int | None = None):
        return stats.contact_frequency(messages(), limit=limit)

    @app.get("/api/stats/timing")
    def timing(contact: str | None = None):
        return stats.timing(messages(), contact=contact)

    @app.get("/api/stats/timing/contacts")
    def timing_contacts(limit: int = 20):
        return stats.contact_peaks(messages(), limit=limit)

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
        rows = stats.conversation_health(messages(), gap_hours=gap_hours)
        # The summary always covers everyone; `limit` only trims what is charted.
        return {"gap_hours": gap_hours, "summary": stats.conversation_summary(rows), "rows": rows[:limit] if limit else rows}

    @app.get("/api/stats/members")
    def members(contact: str):
        return stats.group_members(messages(), contact)

    @app.get("/api/stats/misspellings")
    def misspellings(direction: str = "sent", limit: int = 50, contact: str | None = None):
        if direction not in ("sent", "received"):
            raise HTTPException(400, "direction must be sent or received")
        return stats.misspellings(messages(), speller(), direction=direction, limit=limit, contact=contact)

    @app.get("/api/stats/emoji")
    def emoji(limit: int = 30, contact: str | None = None):
        return stats.emoji_stats(messages(), limit=limit, contact=contact)

    @app.get("/api/stats/tone")
    def tone(contact: str | None = None):
        return stats.tone(messages(), contact=contact)

    @app.get("/api/stats/words")
    def words(direction: str | None = None, limit: int = 50):
        return stats.word_frequency(messages(), direction=direction, limit=limit)

    @app.get("/api/wrapped")
    def wrapped(year: int | None = None):
        msgs = messages()
        available = stats.years(msgs)
        if year is None and available:
            year = available[-1]
        card = stats.wrapped(msgs, year=year, speller=speller())
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
        seen = None
        session = req.session
        if session:
            seen = sessions.setdefault(session, set())
        results = cp["engine"].respond(req.text, per_claim=max(1, min(req.per_claim, 5)), seen=seen)
        payload = {"session": session, "results": [r.to_dict() for r in results]}
        if results and llm_on and req.llm:
            by_claim: dict[str, list] = {}
            for r in results:
                by_claim.setdefault(r.claim.key, []).append(r)
            payload["llm"] = {key: llm.sharpen(group[0].claim.raw, group) for key, group in by_claim.items()}
        return payload

    @app.post("/api/counterpoint/session")
    def new_session():
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
