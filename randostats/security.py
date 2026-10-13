"""Browser boundary and admission limits, applied before multipart parsing.

Host checks stop browser DNS rebinding; they are not authentication for a
caller who can set arbitrary headers. Beyond loopback that is
RANDOSTATS_PASSWORD's job (auth.py).

The boundary has two halves, and api.py runs them on either side of the
password gate. ``RequestBoundary.screen`` decides from the headers alone (the
Host, where a browser says the request came from, and the declared length), so
it runs first, in api.py's guard, where its refusal carries the site's headers
like every other answer. Admission, which reads the body, runs after the gate
(``screened=True``): staging a body is disk and an import slot, and a client
without a session gets neither. On its own, as the tests drive it, the class
does both.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import tempfile
from urllib.parse import urlsplit

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# What a browser says about a request the page on this server made itself
# ("same-origin"), or one the person made by typing, a bookmark or a home-screen
# icon ("none"). "same-site" is another port or a subdomain: another site here.
FIRST_PARTY = frozenset({"same-origin", "none"})
# Every body but an import is a short sentence or a list of pack names.
MAX_BODY = 1024 * 1024
MiB = 1024 * 1024


def upload_too_large(limit: int) -> str:
    """What an import past the limit is told; the front end shows it as it is."""
    return (f"file is larger than the {max(1, limit // MiB)} MB limit; "
            "raise RANDOSTATS_MAX_UPLOAD_MB if you really need to")


def loopback_bind(host: str) -> bool:
    """Whether binding ``host`` keeps the server on this machine.

    "localhost" and the loopback addresses (all of 127.0.0.0/8, and ::1) do. ""
    "0.0.0.0" and "::" are every interface, and any other name is whatever it
    resolves to, which is not something to take on trust.
    """
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def allowed_hostname(value: str) -> str:
    """Configuration accepts exact hostnames or IPs, never wildcards/URLs."""
    host = value.lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if not host or len(host) > 253 or any(not (c.isascii() and (c.isalnum() or c in "-._")) for c in host):
            raise ValueError("allow-host must be an exact hostname or IP address") from None
        return host


def _authority(value: str):
    if not value or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError("invalid authority")
    parsed = urlsplit("//" + value)
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError("invalid authority")
    return allowed_hostname(parsed.hostname), parsed.port


def _first_party_only(scope) -> bool:
    """Whether only this server's own page may make this request.

    The API is called by nothing but the front end, same-origin; a cross-site
    read cannot see the answer but still makes this machine walk the archive. A
    method that changes something is never a link. A page or a file fetched
    with GET is: a link from another site to this one still opens it.
    """
    path = scope.get("path", "")
    return path == "/api" or path.startswith("/api/") or scope.get("method", "GET") not in SAFE_METHODS


# A body that stops arriving holds a staging file and an import slot, so it is not
# waited on for ever: `idle_timeout` seconds without a chunk, or `body_deadline`
# seconds in all, and it is a 408. Parameters, so the tests can drive them small
# against a receive that stalls rather than wait the real limits out (RS-R7).
IDLE_TIMEOUT = 30.0
BODY_DEADLINE = 300.0


class RequestBoundary:
    def __init__(self, app, allowed_hosts=LOCAL_HOSTS, upload_limit=lambda: 256 * 1024 * 1024,
                 max_uploads=2, overhead=MiB, screened=False,
                 idle_timeout=IDLE_TIMEOUT, body_deadline=BODY_DEADLINE):
        self.app = app
        self.hosts = frozenset(allowed_hostname(host) for host in allowed_hosts)
        self.upload_limit = upload_limit
        self.max_uploads = max_uploads
        # The boundary lines and part headers around an imported file.
        self.overhead = overhead
        # The headers were already screened, ahead of the password gate.
        self.screened = screened
        # The longest a stalled or trickled body is staged before a 408.
        self.idle_timeout = idle_timeout
        self.body_deadline = body_deadline
        self.active_uploads = 0

    @staticmethod
    def _headers(scope) -> dict[bytes, list[str]]:
        headers: dict[bytes, list[str]] = {}
        for key, value in scope.get("headers", []):
            headers.setdefault(key.lower(), []).append(value.decode("latin1"))
        return headers

    def _limit(self, scope) -> tuple[int, str]:
        """How many bytes this request may send, and what to say past that."""
        if scope.get("path") == "/api/import":
            limit = self.upload_limit()
            return limit + self.overhead, upload_too_large(limit)
        return MAX_BODY, f"request body is larger than the {MAX_BODY // MiB} MB limit"

    def screen(self, scope) -> tuple[int, str] | None:
        """Why the headers alone say no, as (status, detail), or None."""
        headers = self._headers(scope)
        try:
            host_values = headers.get(b"host", [])
            if len(host_values) != 1:
                raise ValueError("one Host required")
            host, port = _authority(host_values[0])
            if host not in self.hosts:
                raise ValueError("unapproved Host")
        except ValueError:
            # A name that resolves to this machine is not a name we serve.
            return 403, "invalid host header; use the local URL, or pass --allow-host to serve this name"
        if _first_party_only(scope):
            try:
                origin_values = headers.get(b"origin", [])
                if len(origin_values) > 1:
                    raise ValueError("multiple Origins")
                if origin_values:
                    origin = urlsplit(origin_values[0])
                    if origin.scheme != scope.get("scheme", "http") or origin.path or origin.query or origin.fragment:
                        raise ValueError("cross-origin request")
                    origin_host, origin_port = _authority(origin.netloc)
                    default_port = 443 if origin.scheme == "https" else 80
                    if (host, port or default_port) != (origin_host, origin_port or default_port):
                        raise ValueError("cross-origin request")
                # A request that names no site at all is treated as first-party, which
                # keeps curl and local scripts working; see README, "Running it".
                if any(v.lower() not in FIRST_PARTY for v in headers.get(b"sec-fetch-site", [])):
                    raise ValueError("cross-site request")
            except ValueError:
                return 403, "cross-site request refused"
        lengths = headers.get(b"content-length", [])
        if lengths:
            if len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isascii() or not lengths[0].isdigit():
                return 400, "invalid Content-Length"
            limit, too_large = self._limit(scope)
            if int(lengths[0]) > limit:
                return 413, too_large
        return None

    @staticmethod
    async def reject(send, status, detail):
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if not self.screened:
            problem = self.screen(scope)
            if problem is not None:
                return await self.reject(send, *problem)

        importing = scope.get("path") == "/api/import"
        limit, too_large = self._limit(scope)
        lengths = self._headers(scope).get(b"content-length", [])
        if importing and self.active_uploads >= self.max_uploads:
            return await self.reject(send, 503, "another import is in progress; try again when it finishes")
        # No await between check and increment: atomic within the ASGI loop.
        if importing:
            self.active_uploads += 1
        try:
            # Stage in a private, disk-backed tempfile *before* FastAPI's
            # multipart dependency runs. Framework body-parsing error handlers
            # cannot turn a receive-limit exception into a misleading 400.
            with tempfile.TemporaryFile() as body:
                total = 0
                more = True
                deadline = asyncio.get_running_loop().time() + self.body_deadline
                while more:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        return await self.reject(send, 408, "request body timed out")
                    try:
                        message = await asyncio.wait_for(receive(), timeout=min(self.idle_timeout, remaining))
                    except asyncio.TimeoutError:
                        return await self.reject(send, 408, "request body timed out")
                    if message["type"] == "http.disconnect":
                        # The client went away before the body arrived. api.py wraps
                        # this in a BaseHTTPMiddleware, whose call_next raises
                        # RuntimeError("No response returned.") - logged as a traceback
                        # for every aborted upload - when the inner app returns without
                        # a response (RS-R6). Answer instead: the bytes go nowhere, but
                        # the middleware sees a response and logs nothing.
                        return await self.reject(send, 400, "request body was cut off before it arrived")
                    chunk = message.get("body", b"")
                    total += len(chunk)
                    if total > limit:
                        return await self.reject(send, 413, too_large)
                    body.write(chunk)
                    more = message.get("more_body", False)
                if lengths and int(lengths[0]) != total:
                    return await self.reject(send, 400, "body size does not match Content-Length")
                body.seek(0)
                replayed = False

                async def replay():
                    nonlocal replayed
                    # After the final staged chunk, preserve ASGI's blocking
                    # disconnect receive rather than returning endless empty
                    # requests to response/disconnect listeners.
                    if replayed:
                        return await receive()
                    chunk = body.read(64 * 1024)
                    more_body = body.tell() < total
                    replayed = not more_body
                    return {"type": "http.request", "body": chunk, "more_body": more_body}

                await self.app(scope, replay, send)
        finally:
            if importing:
                self.active_uploads -= 1
