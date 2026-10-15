"""Evaluator freeze by content (prompt §4.3; appendix §O).

The evaluator fingerprint is the sha256 of every file under ``src/zkqec`` (bytecode caches
excluded), ``roles/`` and ``configs/broker/``. At campaign start it must equal the same fingerprint
computed from the recorded git commit (no edited, deleted or untracked evaluator file), and the
imported ``zkqec`` package must be the fingerprinted tree. :meth:`EvaluatorGuard.check` recomputes
it before every provider dispatch and every verifier batch; any difference is a typed abort
(:class:`EvaluatorChanged`). This is the M1 control: importing the evaluator from an extracted,
read-only snapshot with a controlled ``sys.path`` (§O) is an M2 residual.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..contracts import content_hash

REPO = Path(__file__).resolve().parents[3]
FINGERPRINT_PATHS = ("src/zkqec", "roles", "configs/broker")
FINGERPRINT_KIND = "zkqec-evaluator-fingerprint-v1"


class EvaluatorChanged(RuntimeError):
    """The evaluator differs from the frozen fingerprint or from the recorded commit."""


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    return "__pycache__" in parts or rel.endswith((".pyc", ".pyo"))


def _pack(files: dict[str, str]) -> dict[str, Any]:
    files = dict(sorted(files.items()))
    return {"kind": FINGERPRINT_KIND, "paths": list(FINGERPRINT_PATHS), "files": files,
            "sha256": content_hash({"kind": FINGERPRINT_KIND, "files": files})}


def worktree_fingerprint(repo: Path = REPO) -> dict[str, Any]:
    """sha256 of every evaluator file in the working tree (symlinks and specials are refused)."""
    repo = Path(repo)
    files: dict[str, str] = {}
    for top in FINGERPRINT_PATHS:
        root = repo / top
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(repo).as_posix()
            if _excluded(rel) or p.is_dir():
                continue
            if p.is_symlink() or not p.is_file():
                raise EvaluatorChanged(f"evaluator path {rel} is not a regular file")
            files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return _pack(files)


def commit_fingerprint(repo: Path, commit: str) -> dict[str, Any]:
    """The same fingerprint over the blobs recorded at ``commit``."""
    listing = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", commit, "--",
                              *FINGERPRINT_PATHS], check=True, capture_output=True).stdout
    entries = []
    for item in listing.split(b"\0"):
        if not item:
            continue
        meta, path = item.split(b"\t", 1)
        mode, kind, blob = meta.decode().split()
        rel = path.decode()
        if kind == "blob" and not _excluded(rel):
            entries.append((rel, blob, mode))
    files: dict[str, str] = {}
    if entries:
        out = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                             input=b"".join(f"{b}\n".encode() for _, b, _ in entries),
                             check=True, capture_output=True).stdout
        pos = 0
        for rel, blob, mode in entries:
            header_end = out.index(b"\n", pos)
            _, _, size = out[pos:header_end].decode().split()
            start = header_end + 1
            data = out[start:start + int(size)]
            pos = start + int(size) + 1
            if mode == "120000":
                raise EvaluatorChanged(f"evaluator path {rel} is a symlink at {commit}")
            files[rel] = hashlib.sha256(data).hexdigest()
    return _pack(files)


def diff(a: dict[str, Any], b: dict[str, Any], limit: int = 10) -> list[str]:
    fa, fb = a["files"], b["files"]
    out = [f"changed {k}" for k in sorted(set(fa) & set(fb)) if fa[k] != fb[k]]
    out += [f"only in first: {k}" for k in sorted(set(fa) - set(fb))]
    out += [f"only in second: {k}" for k in sorted(set(fb) - set(fa))]
    return out[:limit] or ([] if a["sha256"] == b["sha256"] else ["fingerprint differs"])


class EvaluatorGuard:
    """Freeze the evaluator fingerprint at campaign start and re-check it at every boundary."""

    def __init__(self, repo: Path = REPO, *, commit: str | None = None,
                 check_imports: bool = True):
        self.repo = Path(repo).resolve()
        self.frozen = worktree_fingerprint(self.repo)
        self.commit = commit
        if commit is not None:
            recorded = commit_fingerprint(self.repo, commit)
            if recorded["sha256"] != self.frozen["sha256"]:
                raise EvaluatorChanged(f"evaluator differs from the recorded commit {commit}: "
                                       f"{diff(self.frozen, recorded)}")
        if check_imports:
            src = (self.repo / "src" / "zkqec").resolve()
            stray = sorted(name for name, mod in list(sys.modules.items())
                           if (name == "zkqec" or name.startswith("zkqec."))
                           and getattr(mod, "__file__", None)
                           and not Path(mod.__file__).resolve().is_relative_to(src))
            if stray:
                raise EvaluatorChanged(f"imported zkqec modules outside {src}: {stray[:5]}")

    def manifest(self) -> dict[str, Any]:
        return {**self.frozen, "commit": self.commit}

    def check(self, boundary: str) -> None:
        now = worktree_fingerprint(self.repo)
        if now["sha256"] != self.frozen["sha256"]:
            raise EvaluatorChanged(f"{boundary}: evaluator changed since the freeze: "
                                   f"{diff(self.frozen, now)}")
