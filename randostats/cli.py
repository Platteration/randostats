"""Command line entry point: ``randostats serve`` and ``randostats import``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import google, parsers
from .auth import password_problem
from .store import DEFAULT_DB, Store
from .security import LOCAL_HOSTS, allowed_hostname, loopback_bind


# The three spellings of "every interface": uvicorn hands each straight to bind().
BIND_ALL = ("0.0.0.0", "::", "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="randostats", description="Message statistics and a live counterpoint engine.")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1",
                   help="interface to bind (default: %(default)s). Anything but loopback needs "
                        "RANDOSTATS_PASSWORD (at least 12 characters) or sign-in with Google "
                        "(RANDOSTATS_GOOGLE_*) set in the environment: whoever reaches the port and can "
                        "sign in can read, and delete, every message you have imported.")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--llm", action="store_true", help="let Claude phrase the rebuttals (needs an Anthropic credential)")
    s.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="also answer requests whose Host header is NAME (repeatable): an exact host name or "
                        "address, never a wildcard, a URL or a port. Only loopback names are served by default, "
                        "so a page on the internet cannot point a name it owns at this port; a bind to every "
                        "interface needs at least one. NAME becomes a name this server trusts completely: the "
                        "cross-site check measures 'another site' against the names served, so any page that can "
                        "make a browser resolve NAME to this machine can read and delete everything, exactly as "
                        "the front end can.")

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

        # Binding to a name of your own means browsing to it, so it has to be
        # served; 0.0.0.0 and :: are binds, not names, and say nothing about
        # what a browser will ask for. Each name is an exact one: no wildcard,
        # URL or port (security.allowed_hostname).
        names = [*args.allow_host]
        if args.host not in (*LOCAL_HOSTS, *BIND_ALL):
            names.append(args.host)
        hosts = [*LOCAL_HOSTS]
        for name in names:
            try:
                hosts.append(allowed_hostname(name))
            except ValueError:
                print(f"error: {name!r} is not an exact host name or address. A name to serve is one\n"
                      "       name: not a wildcard, a URL or a port.", file=sys.stderr)
                return 2
        # The password comes from the environment, not an argument, so that it
        # is not in the shell history or in every `ps` listing on the machine.
        password = os.environ.get("RANDOSTATS_PASSWORD") or None
        problem = password and password_problem(password)
        if problem:
            print(f"error: {problem}.", file=sys.stderr)
            return 2
        # Sign-in with Google, the other way in (google.py). Set up halfway it is an
        # error, never a gate that quietly opens for nobody or is not there at all.
        try:
            google_config = google.read_config(os.environ)
        except ValueError as exc:
            print(f"error: {exc}.", file=sys.stderr)
            return 2
        if google_config is not None and google_config.hostname not in hosts:
            # Google sends the browser back to that name, and the Host check would
            # refuse it: the sign-in could never finish.
            print(f"error: {google.APP_URL} names {google_config.hostname}, which this server does not\n"
                  f"       answer to, so Google's answer would be refused. Serve it with\n"
                  f"       --allow-host {google_config.hostname}.", file=sys.stderr)
            return 2
        gated = password is not None or google_config is not None
        # "" is a bind-all, exactly as the line above says: uvicorn hands it
        # straight to bind(), and bind(("", port)) is every interface. Filing
        # it with the loopback names here let the widest bind of the three be
        # the one that said nothing.
        # A name other than loopback is one something forwards to this server - a
        # reverse proxy, a tunnel, a container port map - so the server is reached
        # from beyond this machine even when it binds 127.0.0.1 (`--allow-host
        # stats.example.com`, the README's own recipe). `_beyond_loopback` in the app
        # cannot see a proxy, so without a password that recipe served everything
        # unauthenticated (RS-R10). Refuse it here, where the served names are known.
        proxied = [name for name in names if not loopback_bind(allowed_hostname(name))]
        if proxied and not gated and loopback_bind(args.host):
            print(f"error: --allow-host {proxied[0]} is a name something forwards to this server, so it is\n"
                  "       reachable from beyond this machine, and nothing would ask anyone to sign in:\n"
                  "       anyone who reaches it could read, search and export every message you have\n"
                  "       imported, and delete the lot. Set RANDOSTATS_PASSWORD (at least 12 characters)\n"
                  "       or sign-in with Google (README, \"Deploy\") in the environment, or serve only\n"
                  "       loopback names.", file=sys.stderr)
            return 2
        if not loopback_bind(args.host):
            if not gated:
                # Everything imported, readable and deletable by whoever reaches the
                # port: not something to start by accident, or with a warning.
                print(f"error: serving on {args.host or '0.0.0.0'} reaches beyond this machine, and nothing\n"
                      "       would ask anyone to sign in: anyone who can reach the port could read, search\n"
                      "       and export every message you have imported, and delete the lot. Set\n"
                      "       RANDOSTATS_PASSWORD (at least 12 characters) or sign-in with Google (README,\n"
                      "       \"Deploy\") in the environment, or bind 127.0.0.1 (the default).", file=sys.stderr)
                return 2
            if not names:
                # Every interface, and no name a browser elsewhere would use: the
                # Host check would refuse every client this bind exists to reach.
                print(f"error: serving on {args.host or '0.0.0.0'} reaches beyond this machine, but only the\n"
                      "       loopback names are served, so every other client would be refused. Name the\n"
                      "       host or address they browse to with --allow-host (repeatable), for example\n"
                      "       --allow-host laptop.lan.", file=sys.stderr)
                return 2
            # Say what that still costs before it is served.
            print(f"warning: serving on {args.host or '0.0.0.0'}, which is not just this machine.\n"
                  "         Anyone who can reach this port and sign in can read, search and export\n"
                  "         every message you have imported, and delete the lot. Over plain HTTP the\n"
                  "         password and the session cookie cross the network readable: put HTTPS\n"
                  "         in front (README, \"Deploy\").", file=sys.stderr)
            if args.llm:
                # The warning above is about the data. --llm also hands out a
                # credential that costs money, which is a separate decision.
                print("         With --llm, they can also make this server call the Anthropic API on your\n"
                      "         credential. RANDOSTATS_LLM_CALLS_PER_HOUR caps how much of that it will do.",
                      file=sys.stderr)
        uvicorn.run(create_app(args.db, use_llm=args.llm, allowed_hosts=hosts, password=password,
                               google_config=google_config),
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
