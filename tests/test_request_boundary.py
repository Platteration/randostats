"""Stdlib regressions run without installed FastAPI/pytest dependencies."""
import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from randostats.security import LOCAL_HOSTS, RequestBoundary, allowed_hostname, loopback_bind
from randostats.store import Store


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, headers=None, chunks=(), boundary=None, path="/api/import"):
        called = []

        async def app(scope, receive, send):
            called.append(True)
            size = 0
            while True:
                message = await receive()
                size += len(message.get("body", b""))
                if not message.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": str(size).encode()})

        guard = boundary or RequestBoundary(app, upload_limit=lambda: 8)
        messages = [{"type": "http.request", "body": part, "more_body": i < len(chunks) - 1}
                    for i, part in enumerate(chunks)] or [{"type": "http.request", "body": b""}]
        sent = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        await guard({"type": "http", "scheme": "http", "path": path,
                     "headers": headers or [(b"host", b"localhost:8765")]}, receive, send)
        return sent[0]["status"], called, b"".join(m.get("body", b"") for m in sent)

    async def test_local_and_same_origin(self):
        status, called, _ = await self.request([(b"host", b"localhost:8765"), (b"origin", b"http://localhost:8765")])
        self.assertEqual(status, 200)
        self.assertTrue(called)

    async def test_rebinding_and_cross_origin_rejected_before_app(self):
        for headers in [[(b"host", b"attacker.invalid")],
                        [(b"host", b"localhost:8765"), (b"origin", b"http://evil.invalid")],
                        [(b"host", b"localhost:8765"), (b"origin", b"null")],
                        [(b"host", b"localhost"), (b"sec-fetch-site", b"cross-site")],
                        [(b"host", b"localhost"), (b"host", b"localhost")]]:
            status, called, _ = await self.request(headers)
            self.assertEqual(status, 403)
            self.assertFalse(called)

    async def test_declared_and_chunked_oversize_never_reach_parser(self):
        for headers, chunks in [([(b"host", b"localhost"), (b"content-length", b"1048585")], ()),
                                ([(b"host", b"localhost")], (b"x" * 524288, b"y" * 524300))]:
            status, called, _ = await self.request(headers, chunks)
            self.assertEqual(status, 413)
            self.assertFalse(called)

    async def test_short_declared_body_rejected(self):
        status, called, _ = await self.request([(b"host", b"localhost"), (b"content-length", b"3")], (b"x",))
        self.assertEqual(status, 400)
        self.assertFalse(called)

    async def test_chunks_replay_without_losing_bytes(self):
        status, called, body = await self.request(chunks=(b"x" * 70000, b"y" * 80000))
        self.assertEqual(status, 200)
        self.assertTrue(called)
        self.assertEqual(body, b"150000")

    async def test_replay_waits_for_real_disconnect_after_body(self):
        disconnect = asyncio.Event()
        receives = 0

        async def receive():
            nonlocal receives
            receives += 1
            if receives == 1:
                return {"type": "http.request", "body": b"complete", "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def app(scope, replay, send):
            self.assertEqual((await replay())["body"], b"complete")
            waiting = asyncio.create_task(replay())
            await asyncio.sleep(0)
            self.assertFalse(waiting.done())
            disconnect.set()
            self.assertEqual((await waiting)["type"], "http.disconnect")

        async def send(message):
            pass

        await RequestBoundary(app)({"type": "http", "scheme": "http", "path": "/",
                                    "headers": [(b"host", b"localhost")]}, receive, send)

    async def test_concurrent_admission_is_released_on_failure(self):
        entered = asyncio.Event()
        leave = asyncio.Event()

        async def app(scope, receive, send):
            entered.set()
            await leave.wait()
            raise RuntimeError("parser failed")

        guard = RequestBoundary(app, max_uploads=1)
        first = asyncio.create_task(self.request(boundary=guard))
        await entered.wait()
        status, _, _ = await self.request(boundary=guard)
        self.assertEqual(status, 503)
        leave.set()
        with self.assertRaises(RuntimeError):
            await first
        self.assertEqual(guard.active_uploads, 0)

    async def test_explicit_lan_host(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
        guard = RequestBoundary(app, allowed_hosts=(*LOCAL_HOSTS, "192.168.1.20"))
        status, _, _ = await self.request([(b"host", b"192.168.1.20:8765")], boundary=guard)
        self.assertEqual(status, 200)


class StorePermissionsTests(unittest.TestCase):
    def test_private_creation_and_existing_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "archive"
            previous = os.umask(0o022)
            try:
                db = Store(parent / "messages.db")
                db.conn.execute("PRAGMA journal_mode=WAL")
                db.set_setting("example", "nonsecret")
                self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
                for suffix in ("", "-wal", "-shm"):
                    self.assertEqual(stat.S_IMODE(Path(str(db.path) + suffix).stat().st_mode), 0o600)
                db.close()
                (parent / "messages.db").chmod(0o644)
                db = Store(parent / "messages.db")
                self.assertEqual(stat.S_IMODE(db.path.stat().st_mode), 0o600)
                self.assertEqual(db.get_setting("example"), "nonsecret")
                db.close()
            finally:
                os.umask(previous)

    def test_explicit_hosts_cannot_be_urls_or_wildcards(self):
        self.assertTrue(loopback_bind("::1"))
        self.assertFalse(loopback_bind("0.0.0.0"))
        for value in ("*", "*.example.com", "http://example.com", "example.com:80"):
            with self.assertRaises(ValueError):
                allowed_hostname(value)


if __name__ == "__main__":
    unittest.main()
