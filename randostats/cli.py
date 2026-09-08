"""Command line entry point: ``randostats serve`` and ``randostats import``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import parsers
from .store import DEFAULT_DB, Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="randostats", description="Message statistics and a live counterpoint engine.")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--llm", action="store_true", help="let Claude phrase the rebuttals (needs an Anthropic credential)")

    i = sub.add_parser("import", help="import an export file from the terminal")
    i.add_argument("file", type=Path)
    i.add_argument("--me", required=True, help="your name exactly as it appears in the export")
    i.add_argument("--format", default="auto", choices=["auto", *sorted(parsers.PARSERS)])
    i.add_argument("--contact", help="override the contact name (WhatsApp exports of group chats)")

    c = sub.add_parser("counter", help="print counterpoints for a sentence")
    c.add_argument("text", nargs="+")

    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(args.db, use_llm=args.llm), host=args.host, port=args.port)
        return 0

    if args.cmd == "import":
        data = args.file.read_bytes()
        fmt = args.format
        if fmt == "auto":
            fmt = parsers.detect_format(args.file.name, data) or ""
            if not fmt:
                print("could not detect format; pass --format", file=sys.stderr)
                return 2
        if fmt == "whatsapp" and args.contact:
            msgs = list(parsers.whatsapp.parse(data, args.me, contact=args.contact))
        else:
            msgs = parsers.parse(fmt, data, args.me)
        if not msgs:
            print(f"parsed as {fmt} but found no messages; check the format and your name", file=sys.stderr)
            return 1
        store = Store(args.db)
        added = store.add_messages(msgs)
        store.set_setting("self_name", args.me)
        print(f"{fmt}: parsed {len(msgs)} messages, {added} new, {store.count()} total")
        return 0

    if args.cmd == "counter":
        from .counterpoint import CounterpointEngine
        from .counterpoint.packs import DEFAULT_VOICE

        # Answer in whatever voice and packs the app is configured with, so
        # the terminal and the browser do not contradict each other.
        store = Store(args.db)
        engine = CounterpointEngine(
            packs={p for p in (store.get_setting("packs", "") or "").split(",") if p},
            voice=store.get_setting("voice", DEFAULT_VOICE))
        results = engine.respond(" ".join(args.text))
        if not results:
            print("No quantitative claim found.")
            return 1
        for r in results:
            print(f"Claim: {r.claim.raw}")
            for line in r.lines:
                print(f"  {line}")
            print(f"  Logic gap: {r.fallacy}\n")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
