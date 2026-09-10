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
    s.add_argument("--host", default="127.0.0.1",
                   help="interface to bind (default: %(default)s). There is no login: bind anything wider "
                        "and every message you have imported is readable, and deletable, by whoever "
                        "reaches the port.")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--llm", action="store_true", help="let Claude phrase the rebuttals (needs an Anthropic credential)")
    s.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="also answer requests whose Host header is NAME (repeatable). Only loopback names are "
                        "served by default, so a page on the internet cannot point a name it owns at this port. "
                        "Pass '*' to turn the check off.")

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

        from .api import LOOPBACK_HOSTS, create_app

        # Binding to a name of your own means browsing to it, so it has to be
        # served; 0.0.0.0 and :: are binds, not names, and say nothing about
        # what a browser will ask for.
        hosts = [*LOOPBACK_HOSTS, *args.allow_host]
        if args.host not in (*LOOPBACK_HOSTS, "0.0.0.0", "::", ""):
            hosts.append(args.host)
        if args.host not in (*LOOPBACK_HOSTS, ""):
            # There is no password on any of this. Say so before it is served.
            print(f"warning: serving on {args.host}, which is not just this machine.\n"
                  "         Nothing here asks for a password: anyone who can reach this port can read,\n"
                  "         search and export every message you have imported, and delete the lot.\n"
                  "         Bind 127.0.0.1 (the default) unless you mean it.", file=sys.stderr)
        uvicorn.run(create_app(args.db, use_llm=args.llm, allowed_hosts=hosts), host=args.host, port=args.port)
        return 0

    if args.cmd == "import":
        data = args.file.read_bytes()
        fmt = args.format
        try:
            if fmt == "auto":
                fmt = parsers.detect_format(args.file.name, data) or ""
                if not fmt:
                    print("could not detect format; pass --format", file=sys.stderr)
                    return 2
            if fmt == "whatsapp" and args.contact:
                msgs = list(parsers.whatsapp.parse(data, args.me, contact=args.contact))
            else:
                msgs = parsers.parse(fmt, data, args.me)
        except ValueError as exc:
            # A refused file (entities, a zip claiming too much, a chat log
            # whose timestamps read as nothing) is the user's problem to fix,
            # not a crash to show them a traceback for.
            print(f"could not parse as {fmt}: {exc}", file=sys.stderr)
            return 1
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
