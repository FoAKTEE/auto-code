"""Durable file publication outside the artifact store (appendix §L publication order).

Every helper publishes by: temporary file *in the destination directory* → write → fsync the file →
``os.replace`` → fsync the directory. Nothing is ``shutil.move``d across filesystems (a
cross-filesystem move copies without fsync). A caller may return a reference to the destination only
after the helper returns.

Directories (Codex M1 milestone review B7): :func:`makedirs_durable` creates what is missing and then
fsyncs **every** directory from the leaf up to a known durable root — the caller's ``root``, or by
default the mount point of the leaf's filesystem, whose own entry needs no fsync — whether or not a
directory was created by this call. A retry after a crash between any ``mkdir`` and any ancestor
fsync therefore re-establishes every entry on the chain, including when everything already exists.
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


def mount_point(path: str | os.PathLike) -> Path:
    """The mount point of the filesystem holding the existing directory ``path``."""
    p = Path(path).absolute()
    while not os.path.ismount(p):
        p = p.parent
    return p


def _durable_root(path: Path, root: str | os.PathLike | None) -> Path | None:
    if root is None:
        return None
    root = Path(root).absolute()
    if root != path and root not in path.parents:
        raise ValueError(f"durable root {root} is not an ancestor of {path}")
    if not root.is_dir():
        raise FileNotFoundError(f"durable root {root} does not exist")
    return root


def makedirs_durable(path: str | os.PathLike, *, root: str | os.PathLike | None = None) -> Path:
    """Create ``path`` and its missing ancestors, then fsync every directory from ``path`` up to
    and including the durable root (``root``, an existing ancestor known to be durable, or the
    mount point), so every entry on the chain is durable — also on a retry where all exist."""
    path = Path(path).absolute()
    stop = _durable_root(path, root)
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
    stop = stop or mount_point(path)
    d = path
    while True:
        fsync_dir(d)                        # makes d's entries (children) durable
        if d == stop or d.parent == d:
            break
        d = d.parent
    return path


@contextmanager
def atomic_writer(dest: str | os.PathLike, *,
                  root: str | os.PathLike | None = None) -> Iterator[BinaryIO]:
    """Yield a binary file object; on clean exit publish it durably at ``dest`` (its directory
    chain re-synced up to ``root`` / the mount point first)."""
    dest = Path(dest)
    makedirs_durable(dest.parent, root=root)
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


def atomic_write_bytes(dest: str | os.PathLike, data: bytes, *,
                       root: str | os.PathLike | None = None) -> Path:
    with atomic_writer(dest, root=root) as f:
        f.write(data)
    return Path(dest)


def atomic_copy(src: str | os.PathLike, dest: str | os.PathLike, *,
                root: str | os.PathLike | None = None) -> Path:
    """Copy ``src`` (possibly on another filesystem) to ``dest`` with the publication order."""
    with open(src, "rb") as fin, atomic_writer(dest, root=root) as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)
    return Path(dest)
