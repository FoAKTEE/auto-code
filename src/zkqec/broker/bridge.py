"""Host side of the sandbox egress bridge (contracts §J1).

Inside the sandbox, ``socat`` listens on ``127.0.0.1:<port>`` (the CLI's ``HTTPS_PROXY``) and forwards
each connection to a Unix socket bind-mounted read-only from the job directory. This module listens on
that Unix socket on the host and forwards to the broker's loopback port.

The bridge accepts exactly one proxy request per connection, and only ``CONNECT host:port``. It strips
any client-supplied ``Proxy-Authorization`` header and injects the job's token, so the broker can tie
the connection to the job's grant while the sandbox never sees the token. After the CONNECT head the
bytes are spliced unchanged (TLS to the broker's per-campaign CA).
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import socket
import threading
from dataclasses import dataclass, field

HEAD_LIMIT = 16 * 1024
_CONNECT = re.compile(rb"^CONNECT ([A-Za-z0-9.-]{1,253}):([0-9]{1,5}) HTTP/1\.[01]$")


@dataclass
class BridgeStats:
    connections: int = 0
    connects_forwarded: int = 0
    rejected: int = 0
    reasons: list[str] = field(default_factory=list)


def rewrite_connect_head(head: bytes, token: str) -> bytes:
    """Validate a CONNECT request head and return it with the job token injected.

    Raises ValueError for anything that is not a single well-formed CONNECT.
    """
    if not head.endswith(b"\r\n\r\n"):
        raise ValueError("incomplete_head")
    lines = head[:-4].split(b"\r\n")
    if not _CONNECT.match(lines[0]):
        raise ValueError("not_connect")
    kept = [lines[0]]
    for line in lines[1:]:
        name = line.split(b":", 1)[0].strip().lower()
        if b":" not in line:
            raise ValueError("bad_header")
        if name in (b"proxy-authorization", b"proxy-connection"):
            continue
        kept.append(line)
    cred = base64.b64encode(b"zkqec:" + token.encode()).decode()
    kept.append(b"Proxy-Authorization: Basic " + cred.encode())
    return b"\r\n".join(kept) + b"\r\n\r\n"


class Bridge:
    """Unix-socket → broker TCP bridge running its own asyncio loop in a daemon thread."""

    def __init__(self, socket_path: str, broker_port: int, token: str, *, broker_host: str = "127.0.0.1",
                 max_connections: int = 64) -> None:
        self.socket_path = socket_path
        self.broker = (broker_host, broker_port)
        self._token = token
        self._sem_n = max_connections
        self.stats = BridgeStats()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.AbstractServer | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    # -- lifecycle -------------------------------------------------------------------------------
    def start(self, timeout: float = 5.0) -> "Bridge":
        self._thread = threading.Thread(target=self._run, name="zkqec-bridge", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("bridge did not start")
        if self._error is not None:
            raise RuntimeError(f"bridge failed: {self._error}")
        return self

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            if os.path.lexists(self.socket_path):
                os.unlink(self.socket_path)
            self._sem = asyncio.Semaphore(self._sem_n)
            old = os.umask(0o077)
            try:
                self._server = loop.run_until_complete(
                    asyncio.start_unix_server(self._handle, path=self.socket_path, limit=HEAD_LIMIT))
            finally:
                os.umask(old)
        except BaseException as exc:  # noqa: BLE001
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def stop(self) -> None:
        loop = self._loop
        if loop is None:
            return

        async def _close() -> None:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
            for task in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
                task.cancel()

        try:
            fut = asyncio.run_coroutine_threadsafe(_close(), loop)
            fut.result(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        self._token = ""

    def __enter__(self) -> "Bridge":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- per connection --------------------------------------------------------------------------
    async def _reject(self, writer: asyncio.StreamWriter, reason: str) -> None:
        self.stats.rejected += 1
        self.stats.reasons.append(reason)
        try:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except OSError:
            pass
        writer.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.stats.connections += 1
        async with self._sem:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, OSError):
                await self._reject(writer, "bad_head")
                return
            try:
                new_head = rewrite_connect_head(head, self._token)
            except ValueError as exc:
                await self._reject(writer, str(exc))
                return
            try:
                up_r, up_w = await asyncio.open_connection(*self.broker)
            except OSError:
                await self._reject(writer, "broker_unreachable")
                return
            self.stats.connects_forwarded += 1
            up_w.write(new_head)
            await up_w.drain()
            await asyncio.gather(self._pipe(reader, up_w), self._pipe(up_r, writer), return_exceptions=True)
            for w in (up_w, writer):
                try:
                    w.close()
                except OSError:
                    pass

    @staticmethod
    async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await src.read(65536)
                if not chunk:
                    break
                dst.write(chunk)
                await dst.drain()
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                if dst.can_write_eof():
                    dst.write_eof()
            except (OSError, RuntimeError):
                pass


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
