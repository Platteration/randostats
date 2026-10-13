"""The password gate, for a server that something other than its owner can reach.

Bound to loopback with no password set, randostats asks for nothing: only this
machine reaches the port, and the Host and cross-site checks in api.py keep a page
in the browser from borrowing it. Bound anywhere else, ``randostats serve`` will not
start without RANDOSTATS_PASSWORD; and whenever one is set, every request but the
sign-in page and the files that are the same for every visitor has to carry a
session cookie this process issued.

The shape is collectcollect's. The password is compared as two HMAC digests under a
key drawn when the process starts, with ``hmac.compare_digest``, so neither the
length of a guess nor how much of it was right changes how long the answer takes.
A session is a random id and its HMAC, kept in an HttpOnly, SameSite=Lax cookie that
is Secure whenever the request came over HTTPS, and checked the same constant-time
way before the id is looked up. Failed sign-ins are counted per client before the
password is checked, so firing guesses in parallel buys nothing.

Sessions live in this process. A restart, which is also the only way to change the
password, signs everyone out; signing out ends that one session wherever a copy of
its cookie went.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import secrets
import threading
import time
from collections import OrderedDict

# Long enough that the per-client limit below, not the alphabet, is what a guesser
# runs into. The server holds every message the owner imported.
MIN_PASSWORD = 12
# The cookie a session travels in. A name of its own: cookies are scoped to a host,
# not a port, so another app on the same machine must not be handed this one.
COOKIE = "randostats_session"
SESSION_DAYS = 30
# One person's browsers and phones. Past this the oldest session ends, so a client
# that signs in over and over cannot grow the table.
MAX_SESSIONS = 64
# Failed sign-ins one client may make in a window, and the window. The "client" is
# a bucket, not a bare address: a whole IPv6 /64 is one allocation handed to one
# subscriber, so counting per /128 gave a guesser 2**64 budgets from a single line
# (RS-R4). `_client_bucket` groups them.
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 60.0
# At most this many buckets are tracked at once, so the table cannot grow without
# bound. Past it a newcomer is still let through (the global budget below bounds a
# flood, not the table size): refusing a newcomer because the table was full let an
# attacker fill it from one /64 and lock the owner out for the window (RS-R4).
MAX_CLIENTS = 10_000
# Wrong guesses across *all* buckets in one window. The per-bucket limit does not
# see a flood spread thin across many /64s, each under MAX_ATTEMPTS; this does, and
# only this ever slows a client on its first guess. A single-user or small-team
# self-host never approaches it, and it holds a distributed guesser far below what a
# 12-character password withstands.
MAX_TOTAL_ATTEMPTS = 1_000
# A cookie longer than this was not issued here.
MAX_TOKEN = 128


def password_problem(password: str | None) -> str | None:
    """Why this cannot be the password, or None when it can."""
    if not password:
        return "RANDOSTATS_PASSWORD is empty"
    if len(password) < MIN_PASSWORD:
        return f"RANDOSTATS_PASSWORD must be at least {MIN_PASSWORD} characters (it is {len(password)})"
    return None


def local_path(value: object) -> str:
    """Where to go after signing in: ``value`` when it is a path on this site, else ``/``.

    The sign-in page hands back whatever ``?next=`` held, so this is the whole of what
    stands between that parameter and an open redirect. ``//evil.example`` and
    ``/\\evil.example`` name another host (a browser reads a backslash as a slash), and
    the URL parser drops tabs and newlines before it reads anything, so ``/\\t/evil``
    is ``//evil`` by the time it is followed. None of those, and nothing under /api/,
    which would land the visitor on a page of JSON.
    """
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 2048:
        return "/"
    if value.startswith("//") or value.startswith("/api/") or value == "/api":
        return "/"
    if any(ch == "\\" or ch < " " or ch == "\x7f" for ch in value):
        return "/"
    return value


def _client_bucket(client: str) -> str:
    """The rate-limit key for a connecting address.

    One IPv6 /64 is a single allocation, so the whole /64 is one bucket; keying on
    the full /128 let a guesser mint a fresh budget for every address in it. IPv4
    is one address per bucket, an IPv4-mapped IPv6 address is keyed by the IPv4 it
    carries, and anything that is not an address (a hostname, an empty string) is
    its own bucket as written.
    """
    try:
        ip = ipaddress.ip_address(client)
    except ValueError:
        return client
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class Gate:
    """One password, the sessions it opened, and the count of guesses against it."""

    def __init__(self, password: str, clock=time.time):
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        self._key = secrets.token_bytes(32)
        self._password = self._digest(password)
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, float] = OrderedDict()
        self._attempts: dict[str, tuple[int, float]] = {}
        # (wrong guesses this window, when the window ends), across all buckets.
        self._total: tuple[int, float] = (0, 0.0)

    def _digest(self, value: str) -> bytes:
        return hmac.new(self._key, value.encode("utf-8", "surrogatepass"), hashlib.sha256).digest()

    def password_matches(self, submitted: object) -> bool:
        if not isinstance(submitted, str):
            return False
        return hmac.compare_digest(self._digest(submitted), self._password)

    # -- sessions ------------------------------------------------------------

    def _signature(self, sid: str) -> bytes:
        return hmac.new(self._key, b"session:" + sid.encode("ascii"), hashlib.sha256).hexdigest().encode("ascii")

    def _sid(self, token: object) -> str | None:
        """The session id a cookie carries, when this process signed it."""
        if not isinstance(token, str) or len(token) > MAX_TOKEN or not token.isascii():
            return None
        sid, dot, signature = token.partition(".")
        if not dot or not sid:
            return None
        if not hmac.compare_digest(signature.encode("ascii"), self._signature(sid)):
            return None
        return sid

    def issue(self) -> str:
        """A new session, as the cookie value that carries it."""
        now = self._clock()
        with self._lock:
            for sid in [s for s, expires in self._sessions.items() if expires <= now]:
                del self._sessions[sid]
            while len(self._sessions) >= MAX_SESSIONS:
                self._sessions.popitem(last=False)
            sid = secrets.token_urlsafe(24)
            self._sessions[sid] = now + SESSION_DAYS * 86400
        return f"{sid}.{self._signature(sid).decode('ascii')}"

    def valid(self, token: object) -> bool:
        sid = self._sid(token)
        if sid is None:
            return False
        with self._lock:
            expires = self._sessions.get(sid)
        return expires is not None and expires > self._clock()

    def revoke(self, token: object) -> None:
        sid = self._sid(token)
        if sid is not None:
            with self._lock:
                self._sessions.pop(sid, None)

    # -- guesses -------------------------------------------------------------

    def admit(self, client: str) -> int:
        """Count one sign-in attempt from ``client``: 0 to go ahead, or the seconds to wait.

        The attempt is counted before the password is looked at, so a burst of
        parallel guesses is cut off at MAX_ATTEMPTS like a sequence of them. Two
        budgets apply: this bucket's own (MAX_ATTEMPTS), and the whole server's
        (MAX_TOTAL_ATTEMPTS). A newcomer is never refused merely because the table
        is full - only a bucket that has itself guessed too much, or a flood that
        has spent the global budget, waits.
        """
        now = self._clock()
        bucket = _client_bucket(client)
        with self._lock:
            for key in [k for k, (_, until) in self._attempts.items() if until <= now]:
                del self._attempts[key]
            total, total_until = self._total
            if total_until <= now:
                total, total_until = 0, now + WINDOW_SECONDS
            count, until = self._attempts.get(bucket, (0, now + WINDOW_SECONDS))
            if count >= MAX_ATTEMPTS:
                return max(1, math.ceil(until - now))
            if total >= MAX_TOTAL_ATTEMPTS:
                return max(1, math.ceil(total_until - now))
            # Record the bucket while there is room; past MAX_CLIENTS let it
            # through uncounted rather than lock out a newcomer. The global
            # budget above is what bounds a flood that overflows the table.
            if bucket in self._attempts or len(self._attempts) < MAX_CLIENTS:
                self._attempts[bucket] = (count + 1, until)
            self._total = (total + 1, total_until)
        return 0

    def forget(self, client: str) -> None:
        """A right password clears the count, so the owner's typos do not add up."""
        with self._lock:
            self._attempts.pop(_client_bucket(client), None)
