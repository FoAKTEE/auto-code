"""Durable file publication outside the artifact store (appendix §L publication order).

Every helper publishes by: temporary file *in the destination directory* → write → fsync the file →
``os.replace`` → fsync the directory, and creates missing directories with an fsync of each created
directory's parent. Nothing is ``shutil.move``d across filesystems (a cross-filesystem move copies
without fsync). A caller may return a reference to the destination only after the helper returns.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


def _fsync_fd(fd: int) -> None:
    os.fsync(fd)


def fsync_dir(path: str | os.PathLike) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def makedirs_durable(path: str | os.PathLike) -> Path:
    """Create ``path`` and missing ancestors; fsync the parent of every directory on the chain.

    The parents are synced even when the directories already exist, so a retry after a crash
    between an earlier ``mkdir`` and its parent fsync re-establishes durability.
    """
    path = Path(path).absolute()
    missing = []
    p = path
    while not p.exists():
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            pass
    if not path.is_dir():
        raise NotADirectoryError(str(path))
    chain = [path, *path.parents]
    stop = len(missing) + 1                 # sync up to the first pre-existing ancestor
    for d in chain[:stop]:
        fsync_dir(d.parent if d.parent != d else d)
    return path


@contextmanager
def atomic_writer(dest: str | os.PathLike) -> Iterator[BinaryIO]:
    """Yield a binary file object; on clean exit publish it durably at ``dest``."""
    dest = Path(dest)
    makedirs_durable(dest.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    renamed = False
    try:
        with os.fdopen(fd, "wb") as f:
            yield f
            f.flush()
            _fsync_fd(f.fileno())
        os.replace(tmp, dest)
        renamed = True
        fsync_dir(dest.parent)
    finally:
        if not renamed:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def atomic_write_bytes(dest: str | os.PathLike, data: bytes) -> Path:
    with atomic_writer(dest) as f:
        f.write(data)
    return Path(dest)


def atomic_copy(src: str | os.PathLike, dest: str | os.PathLike) -> Path:
    """Copy ``src`` (possibly on another filesystem) to ``dest`` with the publication order."""
    with open(src, "rb") as fin, atomic_writer(dest) as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)
    return Path(dest)
