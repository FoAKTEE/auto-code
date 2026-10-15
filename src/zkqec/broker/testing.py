"""Offline harness for the broker: a local TLS fake upstream (HTTP/1.1 + WebSocket) and a stdlib
client that speaks CONNECT through the host bridge. Used by tests and by ``qualify-host`` for the
synthetic (credential-free, network-free) broker checks. Not used on any production path.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC11B65"


def make_test_pki(directory: str | os.PathLike[str]) -> dict[str, str]:
    """A throwaway CA and a ``localhost`` server certificate (openssl CLI)."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    ext = d / "srv.ext"
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
                   "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
    run = lambda *a: subprocess.run(a, check=True, capture_output=True, cwd=d)  # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem",
        "-days", "2", "-subj", "/CN=zkqec-test-ca", "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr",
        "-subj", "/CN=localhost")
    run("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
        "-out", "srv.pem", "-days", "2", "-extfile", "srv.ext")
    return {"ca": str(d / "ca.pem"), "cert": str(d / "srv.pem"), "key": str(d / "srv.key")}


@dataclass
class Seen:
    kind: str
    method: str | None = None
    path: str | None = None
    size: int = 0


@dataclass
class FakeUpstream:
    """TLS server on 127.0.0.1 answering like a model API: SSE with usage for ``POST /v1/messages``,
    a ``response.completed`` frame (with usage) for every WebSocket text frame, JSON 200 otherwise."""
    cert: str
    key: str
    seen: list[Seen] = field(default_factory=list)
    port: int = 0

    def start(self) -> "FakeUpstream":
        ready = threading.Event()

        def _run() -> None:
            self.loop = asyncio.new_event_loop()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.cert, self.key)
            self.server = self.loop.run_until_complete(
                asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=ctx))
            self.port = self.server.sockets[0].getsockname()[1]
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()
        ready.wait(10)
        return self

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.server.close)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)

    async def _handle(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await r.readuntil(b"\r\n\r\n")
                lines = head.decode("latin-1").split("\r\n")
                method, path, _ = lines[0].split(" ", 2)
                hdrs = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
                n = int(hdrs.get("content-length", "0"))
                body = await r.readexactly(n) if n else b""
                if hdrs.get("upgrade", "").lower() == "websocket":
                    self.seen.append(Seen("ws_upgrade", method, path))
                    accept = base64.b64encode(hashlib.sha1(hdrs["sec-websocket-key"].encode() + WS_GUID).digest())
                    w.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
                    await w.drain()
                    await self._ws_loop(r, w)
                    return
                self.seen.append(Seen("http", method, path, len(body)))
                if method == "POST" and path.split("?")[0] == "/v1/messages":
                    events = [
                        {"type": "message_start", "message": {"usage": {"input_tokens": 11, "output_tokens": 1,
                                                                        "cache_creation_input_tokens": 0}}},
                        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}},
                    ]
                    payload = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
                    ctype = b"text/event-stream"
                else:
                    payload, ctype = b'{"ok":true}', b"application/json"
                w.write(b"HTTP/1.1 200 OK\r\nContent-Type: " + ctype + b"\r\nContent-Length: "
                        + str(len(payload)).encode() + b"\r\n\r\n" + payload)
                await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ValueError, ssl.SSLError):
            pass
        finally:
            w.close()

    async def _ws_loop(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        while True:
            opcode, payload = await _ws_read(r)
            if opcode == 8:
                w.write(_ws_frame(8, b"", mask=False))
                await w.drain()
                return
            if opcode == 1:
                self.seen.append(Seen("ws_frame", size=len(payload)))
                reply = {"type": "response.completed",
                         "response": {"usage": {"input_tokens": len(payload), "output_tokens": 3}}}
                w.write(_ws_frame(1, json.dumps(reply).encode(), mask=False))
                await w.drain()


async def _ws_read(r: asyncio.StreamReader) -> tuple[int, bytes]:
    b0, b1 = await r.readexactly(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", await r.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await r.readexactly(8))[0]
    mask = await r.readexactly(4) if b1 & 0x80 else None
    data = await r.readexactly(n)
    if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return b0 & 0x0F, data


def _ws_frame(opcode: int, payload: bytes, mask: bool = True) -> bytes:
    head = bytes([0x80 | opcode])
    n = len(payload)
    mbit = 0x80 if mask else 0
    if n < 126:
        head += bytes([mbit | n])
    elif n < 65536:
        head += bytes([mbit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([mbit | 127]) + struct.pack(">Q", n)
    if not mask:
        return head + payload
    key = os.urandom(4)
    return head + key + bytes(c ^ key[i % 4] for i, c in enumerate(payload))


# --------------------------------------------------------------------------------------------------
# Client through the bridge
# --------------------------------------------------------------------------------------------------
class ProxyClient:
    """CONNECT via the host bridge's Unix socket, then TLS with the broker CA."""

    def __init__(self, bridge_socket: str, broker_ca: str, host: str, port: int, timeout: float = 15) -> None:
        self.host, self.port = host, port
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(timeout)
        raw.connect(bridge_socket)
        raw.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        head = _read_head(raw)
        self.connect_status = int(head.split(b" ", 2)[1])
        if self.connect_status != 200:
            raw.close()
            self.sock = None
            return
        ctx = ssl.create_default_context(cafile=broker_ca)
        ctx.set_alpn_protocols(["http/1.1"])
        self.sock = ctx.wrap_socket(raw, server_hostname=host)

    def request(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        assert self.sock is not None
        hdr = {"Host": f"{self.host}:{self.port}", "Content-Length": str(len(body)),
               "Content-Type": "application/json", **(headers or {})}
        req = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdr.items()) + "\r\n"
        self.sock.sendall(req.encode() + body)
        head = _read_head(self.sock)
        status = int(head.split(b" ", 2)[1])
        length = 0
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        data = b""
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                break
            data += chunk
        return status, data

    def websocket(self, path: str) -> int:
        assert self.sock is not None
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        return int(_read_head(self.sock).split(b" ", 2)[1])

    def ws_send(self, text: str) -> None:
        assert self.sock is not None
        self.sock.sendall(_ws_frame(1, text.encode()))

    def ws_recv(self) -> tuple[int, bytes] | None:
        assert self.sock is not None
        try:
            b0, b1 = _recv_exact(self.sock, 2)
        except (OSError, EOFError):
            return None
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", _recv_exact(self.sock, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _recv_exact(self.sock, 8))[0]
        return b0 & 0x0F, _recv_exact(self.sock, n)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass


def _recv_exact(s: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def _read_head(s: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = s.recv(1)
        if not chunk:
            raise EOFError("connection closed before head")
        data += chunk
        if len(data) > 16384:
            raise ValueError("head too large")
    return data


def local_policy_overrides(port: int) -> dict:
    """Policy providers that point at a FakeUpstream on localhost:<port>."""
    return {"providers": {
        "claude": {"hosts": ["localhost"], "connect_ports": [port],
                   "endpoints": [{"host": "localhost", "method": "POST", "path": "^/v1/messages$",
                                  "kind": "model", "body": "anthropic_messages"}],
                   "quiet_block": [{"host": "localhost", "method": "GET", "path": "^/v1/mcp_servers$"}],
                   "allowed_tools": ["StructuredOutput", "DeferredToolPlaceholder"]},
        "codex": {"hosts": ["localhost"], "connect_ports": [port],
                  "endpoints": [{"host": "localhost", "method": "GET", "path": "^/backend-api/codex/responses$",
                                 "kind": "websocket", "frames": "openai_responses_ws"}],
                  "quiet_block": [], "allowed_tools": [], "ws_client_frame_types": ["response.create"]},
    }}
