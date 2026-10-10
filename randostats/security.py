"""Browser boundary and admission limits, applied before multipart parsing.

Host checks stop browser DNS rebinding; they are not authentication for a
caller who can set arbitrary headers. LAN sharing still needs a trusted
network or an authenticated reverse proxy.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import tempfile
from urllib.parse import urlsplit

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def loopback_bind(host: str) -> bool:
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


class RequestBoundary:
    def __init__(self, app, allowed_hosts=LOCAL_HOSTS, upload_limit=lambda: 256 * 1024 * 1024,
                 max_uploads=2):
        self.app = app
        self.hosts = {allowed_hostname(host) for host in allowed_hosts}
        self.upload_limit = upload_limit
        self.max_uploads = max_uploads
        self.active_uploads = 0

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
        headers = {}
        for key, value in scope.get("headers", []):
            key = key.lower()
            headers.setdefault(key, []).append(value.decode("latin1"))
        try:
            host_values = headers.get(b"host", [])
            if len(host_values) != 1:
                raise ValueError("one Host required")
            host, port = _authority(host_values[0])
            if host not in self.hosts:
                raise ValueError("unapproved Host")
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
            if any(v.lower() == "cross-site" for v in headers.get(b"sec-fetch-site", [])):
                raise ValueError("cross-site request")
        except ValueError:
            return await self.reject(send, 403, "Host or Origin is not allowed; use the local URL or configure --allow-host for intentional sharing")

        importing = scope.get("path") == "/api/import"
        limit = self.upload_limit() + 1024 * 1024 if importing else 1024 * 1024
        lengths = headers.get(b"content-length", [])
        if lengths:
            if len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isascii() or not lengths[0].isdigit():
                return await self.reject(send, 400, "invalid Content-Length")
            if int(lengths[0]) > limit:
                return await self.reject(send, 413, "request exceeds the upload/body limit")
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
                deadline = asyncio.get_running_loop().time() + 300
                while more:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        return await self.reject(send, 408, "request body timed out")
                    try:
                        message = await asyncio.wait_for(receive(), timeout=min(30, remaining))
                    except asyncio.TimeoutError:
                        return await self.reject(send, 408, "request body timed out")
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    total += len(chunk)
                    if total > limit:
                        return await self.reject(send, 413, "request exceeds the upload/body limit")
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
