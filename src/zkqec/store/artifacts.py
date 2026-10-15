"""Content-addressed artifact store (contracts §L publication order, §M sanitize-before-hash).

Layout: ``root/ab/cd/<sha256>`` where ``ab``/``cd`` are the first two byte pairs of the hash.

Publication order (§L): write a temporary file *in the destination directory* → fsync the file →
``os.replace`` onto the final name → fsync the directory, then fsync every ancestor directory up to
the store root (a retry after a crash between a shard ``mkdir`` and its parent fsync must
re-establish that entry too). A failure at any step leaves no file under the final name (or, after
the rename, a complete file whose durability :meth:`ensure_durable` re-establishes through the whole
chain file → shard → shard → root); re-publishing an existing artifact does the same. The admission
service calls :meth:`ensure_durable` on every artifact an event references before it commits, so no
event can reference an artifact that is not durably published.

Reads verify the content hash, refuse symlinks and non-regular files (artifact or shard directory),
and are capped in bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from zkqec.contracts import canonical_json

from .durable import makedirs_durable
from .sanitize import DEFAULT_SANITIZER, SANITIZER_VERSION, Sanitizer, SecretDetected

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
REDACTION_SCHEMA = "zkqec.redaction/1"


class StoreError(Exception):
    """Base class for artifact-store failures."""


class ArtifactNotFound(StoreError, KeyError):
    """No artifact with this hash is published."""


class IntegrityError(StoreError):
    """Stored bytes do not hash to their address, or the path is unsafe (symlink, non-regular)."""


class ArtifactTooLarge(StoreError):
    """An artifact exceeds the byte cap."""


@dataclass(frozen=True)
class Ingested:
    """Result of an ingest: the address of the (sanitized) bytes and its redaction record."""
    sha: str
    size: int
    redaction: str | None = None
    redactions: dict[str, int] = field(default_factory=dict)


# --- publication steps (module-level so crash tests can inject a failure between them) ----------
def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _fsync_file(fd: int) -> None:
    os.fsync(fd)


def _replace(src: str, dst: str) -> None:
    os.replace(src, dst)


def _fsync_dir(path: str) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def check_sha(sha: str) -> str:
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise ValueError(f"not a sha256 hex digest: {sha!r}")
    return sha


class ArtifactStore:
    """Sharded, content-addressed, sanitize-before-hash artifact store."""

    def __init__(self, root: str | os.PathLike, *, max_bytes: int = DEFAULT_MAX_BYTES,
                 sanitizer: Sanitizer = DEFAULT_SANITIZER):
        self.root = Path(root)
        makedirs_durable(self.root)
        self.max_bytes = int(max_bytes)
        self.sanitizer = sanitizer

    # -- addressing ------------------------------------------------------------------------------
    def path_for(self, sha: str) -> Path:
        check_sha(sha)
        return self.root / sha[:2] / sha[2:4] / sha

    def _shard_dirs(self, sha: str) -> tuple[Path, Path]:
        first = self.root / sha[:2]
        return first, first / sha[2:4]

    def _check_shards(self, sha: str, *, create: bool) -> Path:
        for d in self._shard_dirs(sha):
            try:
                st = os.lstat(d)
            except FileNotFoundError:
                if not create:
                    raise ArtifactNotFound(sha) from None
                try:
                    os.mkdir(d)
                    _fsync_dir(str(d.parent))
                    continue
                except FileExistsError:
                    st = os.lstat(d)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise IntegrityError(f"shard path {d} is not a real directory")
        return self._shard_dirs(sha)[1]

    # -- writes ----------------------------------------------------------------------------------
    def put_json(self, obj: Any) -> str:
        return self.ingest_json(obj).sha

    def put_bytes(self, data: bytes, kind: str) -> str:
        return self.ingest_bytes(data, kind).sha

    def ingest_json(self, obj: Any) -> Ingested:
        clean, counts = self.sanitizer.sanitize_obj(obj)
        if self.sanitizer.detect_obj(clean):
            raise SecretDetected("residual secret after sanitizing (sanitizer not idempotent)")
        return self._ingest(canonical_json(clean), "json", counts)

    def ingest_bytes(self, data: bytes, kind: str) -> Ingested:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("put_bytes needs bytes")
        if not isinstance(kind, str) or not kind:
            raise ValueError("put_bytes needs a non-empty kind")
        clean, counts = self.sanitizer.sanitize_bytes(bytes(data), kind)
        if self.sanitizer.sanitize_bytes(clean, kind)[1]:
            raise SecretDetected("residual secret after sanitizing (sanitizer not idempotent)")
        return self._ingest(clean, kind, counts)

    def _ingest(self, data: bytes, kind: str, counts) -> Ingested:
        if len(data) > self.max_bytes:
            raise ArtifactTooLarge(f"{len(data)} bytes exceeds cap {self.max_bytes}")
        sha = hashlib.sha256(data).hexdigest()
        self._publish(sha, data)
        if not counts:
            return Ingested(sha, len(data))
        record = {"schema": REDACTION_SCHEMA, "artifact": sha, "kind": kind,
                  "sanitizer": SANITIZER_VERSION, "rules": dict(sorted(counts.items())),
                  "total": sum(counts.values())}
        rec_bytes = canonical_json(record)
        rec_sha = hashlib.sha256(rec_bytes).hexdigest()
        self._publish(rec_sha, rec_bytes)
        self._index_redaction(sha, rec_sha)
        return Ingested(sha, len(data), rec_sha, dict(record["rules"]))

    def _publish(self, sha: str, data: bytes) -> None:
        """Durable publication: temp file in the destination dir → fsync → rename → fsync dir."""
        dest_dir = self._check_shards(sha, create=True)
        dest = dest_dir / sha
        if os.path.lexists(dest):
            # idempotent re-publish; a corrupt or unsafe file is an error, and durability is
            # re-established (the earlier publication may have crashed before its fsyncs)
            self.ensure_durable(sha)
            return
        self._atomic_write(dest_dir, dest, data)
        self._sync_ancestors(sha)

    def _sync_ancestors(self, sha: str) -> None:
        first, _ = self._shard_dirs(sha)
        _fsync_dir(str(first))
        _fsync_dir(str(self.root))

    def _atomic_write(self, dest_dir: Path, dest: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=dest_dir)
        renamed = False
        try:
            try:
                _write_all(fd, data)
                _fsync_file(fd)
            finally:
                os.close(fd)
            _replace(tmp, str(dest))
            renamed = True
            _fsync_dir(str(dest_dir))
        finally:
            if not renamed:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass

    def _index_redaction(self, sha: str, rec_sha: str) -> None:
        """Sibling index ``<sha>.redactions``: sorted redaction-record hashes for the artifact."""
        dest_dir = self._check_shards(sha, create=True)
        index = dest_dir / f"{sha}.redactions"
        known = set(self._read_index(index))
        if rec_sha in known:
            return
        known.add(rec_sha)
        self._atomic_write(dest_dir, index, ("\n".join(sorted(known)) + "\n").encode("ascii"))

    @staticmethod
    def _read_index(index: Path) -> list[str]:
        try:
            st = os.lstat(index)
        except FileNotFoundError:
            return []
        if not stat.S_ISREG(st.st_mode):
            raise IntegrityError(f"redaction index {index} is not a regular file")
        lines = index.read_text("ascii").split()
        return [check_sha(x) for x in lines]

    # -- reads -----------------------------------------------------------------------------------
    def exists(self, sha: str) -> bool:
        """True iff a regular file is published under this hash (content not re-hashed)."""
        path = self.path_for(sha)
        try:
            self._check_shards(sha, create=False)
            st = os.lstat(path)
        except (ArtifactNotFound, IntegrityError, FileNotFoundError):
            return False
        return stat.S_ISREG(st.st_mode)

    def get_bytes(self, sha: str, *, max_bytes: int | None = None) -> bytes:
        cap = self.max_bytes if max_bytes is None else min(int(max_bytes), self.max_bytes)
        path = self.path_for(sha)
        self._check_shards(sha, create=False)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise ArtifactNotFound(sha) from None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise IntegrityError(f"artifact {sha} is not a regular file")
        if st.st_size > cap:
            raise ArtifactTooLarge(f"artifact {sha} has {st.st_size} bytes > cap {cap}")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            fst = os.fstat(fd)
            if not stat.S_ISREG(fst.st_mode) or (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
                raise IntegrityError(f"artifact {sha} changed while opening")
            chunks, total = [], 0
            while True:
                chunk = os.read(fd, min(1 << 20, cap + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > cap:
                    raise ArtifactTooLarge(f"artifact {sha} exceeds cap {cap}")
        finally:
            os.close(fd)
        data = b"".join(chunks)
        if hashlib.sha256(data).hexdigest() != sha:
            raise IntegrityError(f"artifact {sha} does not match its hash")
        return data

    def get_json(self, sha: str, *, max_bytes: int | None = None) -> Any:
        def _reject(token):
            raise IntegrityError(f"non-finite number {token} in JSON artifact")
        return json.loads(self.get_bytes(sha, max_bytes=max_bytes).decode("utf-8"),
                          parse_constant=_reject)

    def verify(self, sha: str) -> None:
        """Raise unless the artifact exists, is a regular file, and hashes to its address."""
        self.get_bytes(sha)

    def ensure_durable(self, sha: str) -> None:
        """Verify the artifact, then fsync it, its shard directories and the store root (§L)."""
        self.verify(sha)
        path = self.path_for(sha)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            _fsync_file(fd)
        finally:
            os.close(fd)
        _fsync_dir(str(self._shard_dirs(sha)[1]))
        self._sync_ancestors(sha)

    def redactions(self, sha: str) -> list[dict]:
        """Redaction records filed against an artifact (rule names and counts, never secrets)."""
        dest_dir = self._check_shards(sha, create=False)
        return [self.get_json(r) for r in self._read_index(dest_dir / f"{sha}.redactions")]

    def iter_shas(self) -> Iterator[str]:
        for first in sorted(self.root.iterdir()):
            if not (len(first.name) == 2 and first.is_dir() and not first.is_symlink()):
                continue
            for second in sorted(first.iterdir()):
                if not (len(second.name) == 2 and second.is_dir() and not second.is_symlink()):
                    continue
                for f in sorted(second.iterdir()):
                    if _SHA_RE.match(f.name):
                        yield f.name
