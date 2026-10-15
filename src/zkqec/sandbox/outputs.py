"""Output caps on the trusted side (contracts §J4, §B3/L0).

Everything a sandboxed job produced is untrusted data. It is accepted only as a **regular file**
(never a symlink, FIFO, device or directory), opened with ``O_NOFOLLOW``, read under a byte cap, and
parsed as JSON only after byte and nesting caps pass.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import dataclass, field
from typing import IO, Any, Callable

from ..broker.policy import json_depth


class OutputRejected(ValueError):
    pass


def read_regular_capped(path: str | os.PathLike[str], max_bytes: int) -> bytes:
    try:
        st = os.lstat(path)
    except FileNotFoundError as exc:
        raise OutputRejected("missing") from exc
    if not stat.S_ISREG(st.st_mode):
        raise OutputRejected("not_regular_file")
    if st.st_size > max_bytes:
        raise OutputRejected(f"too_large:{st.st_size}>{max_bytes}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OutputRejected("not_regular_file")
        chunks, n = [], 0
        while n <= max_bytes:
            b = os.read(fd, min(1 << 20, max_bytes + 1 - n))
            if not b:
                break
            chunks.append(b)
            n += len(b)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise OutputRejected("too_large")
    return data


def parse_json_capped(data: bytes | str, *, max_bytes: int, max_depth: int = 32) -> Any:
    raw = data.encode() if isinstance(data, str) else data
    if len(raw) > max_bytes:
        raise OutputRejected(f"too_large:{len(raw)}>{max_bytes}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutputRejected("not_utf8") from exc
    if json_depth(text) > max_depth:
        raise OutputRejected("too_deep")
    try:
        return json.loads(text, parse_constant=_no_constants)
    except (ValueError, RecursionError) as exc:
        raise OutputRejected("invalid_json") from exc


def _no_constants(name: str) -> Any:
    raise ValueError(name)


def load_json_file_capped(path: str | os.PathLike[str], *, max_bytes: int, max_depth: int = 32) -> Any:
    return parse_json_capped(read_regular_capped(path, max_bytes), max_bytes=max_bytes, max_depth=max_depth)


@dataclass
class CappedStream:
    """Drains a pipe into memory up to ``cap`` bytes; on overflow calls ``on_overflow`` once and keeps
    draining (discarding) so the writer never blocks."""
    stream: IO[bytes]
    cap: int
    on_overflow: Callable[[], None] | None = None
    data: bytearray = field(default_factory=bytearray)
    total: int = 0
    overflowed: bool = False

    def __post_init__(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while True:
            try:
                chunk = self.stream.read1(65536) if hasattr(self.stream, "read1") else self.stream.read(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            self.total += len(chunk)
            room = self.cap - len(self.data)
            if room > 0:
                self.data += chunk[:room]
            if self.total > self.cap and not self.overflowed:
                self.overflowed = True
                if self.on_overflow is not None:
                    self.on_overflow()

    def join(self, timeout: float | None = None) -> bytes:
        self.thread.join(timeout)
        return bytes(self.data)
