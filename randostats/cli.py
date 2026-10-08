"""Command line entry point: ``randostats serve`` and ``randostats import``."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path

from . import parsers
from .auth import password_problem
from .store import DEFAULT_DB, Store


def _loopback(host: str) -> bool:
    """Whether binding ``host`` keeps the server on this machine.

    "localhost" and the loopback addresses (all of 127.0.0.0/8, and ::1) do. ""
    "0.0.0.0" and "::" are every interface, and any other name is whatever it
    resolves to, which is not something to take on trust.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="randostats", description="Message statistics and a live counterpoint engine.")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1",
                   help="interface to bind (default: %(default)s). Anything but loopback needs "
                        "RANDOSTATS_PASSWORD (at least 12 characters) set in the environment: whoever "
                        "reaches the port and has it can read, and delete, every message you have imported.")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--llm", action="store_true", help="let Claude phrase the rebuttals (needs an Anthropic credential)")
    s.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="also answer requests whose Host header is NAME (repeatable). Only loopback names are "
                        "served by default, so a page on the internet cannot point a name it owns at this port. "
                        "NAME becomes a name this server trusts completely: the cross-site check measures "
                        "'another site' against the names served, so any page that can make a browser resolve "
                        "NAME to this machine can read and delete everything, exactly as the front end can. "
                        "Pass '*' to accept any name at all, which is that with nothing left to resist it.")

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
        # The password comes from the environment, not an argument, so that it
        # is not in the shell history or in every `ps` listing on the machine.
        password = os.environ.get("RANDOSTATS_PASSWORD") or None
        problem = password and password_problem(password)
        if problem:
            print(f"error: {problem}.", file=sys.stderr)
            return 2
        # "" is a bind-all, exactly as the line above says: uvicorn hands it
        # straight to bind(), and bind(("", port)) is every interface. Filing
        # it with the loopback names here let the widest bind of the three be
        # the one that said nothing.
        if not _loopback(args.host):
            if password is None:
                # Everything imported, readable and deletable by whoever reaches the
                # port: not something to start by accident, or with a warning.
                print(f"error: serving on {args.host or '0.0.0.0'} reaches beyond this machine, and nothing\n"
                      "       would ask for a password: anyone who can reach the port could read, search and\n"
                      "       export every message you have imported, and delete the lot. Set\n"
                      "       RANDOSTATS_PASSWORD (at least 12 characters) in the environment, or bind\n"
                      "       127.0.0.1 (the default).", file=sys.stderr)
                return 2
            # Say what that still costs before it is served.
            print(f"warning: serving on {args.host or '0.0.0.0'}, which is not just this machine.\n"
                  "         Anyone who can reach this port and has the password can read, search and\n"
                  "         export every message you have imported, and delete the lot. Over plain HTTP\n"
                  "         the password and the session cookie cross the network readable: put HTTPS\n"
                  "         in front (README, \"Deploy\").", file=sys.stderr)
            if args.llm:
                # The warning above is about the data. --llm also hands out a
                # credential that costs money, which is a separate decision.
                print("         With --llm, they can also make this server call the Anthropic API on your\n"
                      "         credential. RANDOSTATS_LLM_CALLS_PER_HOUR caps how much of that it will do.",
                      file=sys.stderr)
        uvicorn.run(create_app(args.db, use_llm=args.llm, allowed_hosts=hosts, password=password),
                    host=args.host, port=args.port)
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
        verdicts, results = engine.analyse(" ".join(args.text))
        if not verdicts and not results:
            print("Nothing to answer: no number, myth, or appeal found.")
            return 1
        for v in verdicts:
            source = ", ".join(str(x) for x in (v["source"], v["year"]) if x)
            print(f"Claim: {v['claim']}")
            print(f"  {v['title']} {v['line']}")
            if source:
                print(f"  Source: {source}.")
            print()
        for r in results:
            print(f"Claim: {r.claim.raw}")
            for line in r.lines:
                print(f"  {line}")
            print(f"  Logic gap: {r.fallacy}\n")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
