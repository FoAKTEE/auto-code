"""Evaluator freeze by content (prompt §4.3; appendix §O; Codex M1 review #14).

The evaluator fingerprint is the sha256 of every file under ``src/zkqec`` (bytecode caches
excluded), ``roles/`` and ``configs/broker/``. At campaign start it must equal the fingerprint of the
recorded git commit (no edited, deleted or untracked evaluator file), and the imported ``zkqec``
package must be the fingerprinted tree. :meth:`EvaluatorGuard.check` recomputes it at every boundary
(provider dispatch, verifier job, controls, confirmation) and re-asserts that every loaded ``zkqec``
module comes from that tree; any difference is a typed abort (:class:`EvaluatorChanged`).

**Snapshot mode (§O: "imported from a read-only snapshot with a controlled sys.path").** ``zkqec
campaign run`` (:mod:`zkqec.exec.snapshot`) extracts the committed evaluator closure into a read-only
directory carrying a :data:`SNAPSHOT_MARKER` (commit, closure hash, fingerprint) and re-executes the
CLI there in an isolated interpreter (``-I -S``; ``sys.path`` = snapshot ``src`` + stdlib + the
locked site-packages). Its bootstrap calls :func:`enter_snapshot`, which verifies the fingerprint,
refuses any other ``sys.path`` entry providing ``zkqec``, and installs :class:`SnapshotImportGuard`
(a ``zkqec`` module whose file is outside the snapshot cannot be imported). A guard over a snapshot
compares the marker, not git (the snapshot is not a checkout), and must be the active snapshot.
:func:`runtime_facts` pins the effective interpreter, dependencies, Claude CLI binary and broker; the
guard's :meth:`~EvaluatorGuard.manifest` (frozen into the campaign manifest) carries them.

The frozen *task* is checked the same way (§I mode 1): :func:`check_task` compares the task about to
be dispatched or evaluated with the task frozen under its id in the ledger's ``campaign_frozen``
event; any difference opens a ``task_mutated`` incident and is a typed abort (:class:`TaskMutated`).
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from ..contracts import content_hash

REPO = Path(__file__).resolve().parents[3]
FINGERPRINT_PATHS = ("src/zkqec", "roles", "configs/broker")
FINGERPRINT_KIND = "zkqec-evaluator-fingerprint-v1"
SNAPSHOT_MARKER = "ZKQEC_SNAPSHOT.json"
SNAPSHOT_KIND = "zkqec-evaluator-snapshot-v1"
_ACTIVE: dict[str, Any] = {}            # the snapshot this process entered (enter_snapshot)


class EvaluatorChanged(RuntimeError):
    """The evaluator differs from the frozen fingerprint or from the recorded commit."""


class TaskMutated(RuntimeError):
    """A task presented for dispatch or evaluation differs from the campaign's frozen task."""


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


# --- snapshot origin -----------------------------------------------------------------------------
def snapshot_info(root: str | os.PathLike = REPO) -> dict[str, Any] | None:
    """The marker of the evaluator snapshot at ``root``, or ``None`` for a repository checkout."""
    marker = Path(root) / SNAPSHOT_MARKER
    if not marker.is_file():
        return None
    info = json.loads(marker.read_text())
    if not isinstance(info, dict) or info.get("kind") != SNAPSHOT_KIND:
        raise EvaluatorChanged(f"{marker} is not a {SNAPSHOT_KIND} marker")
    return info


def active_snapshot() -> dict[str, Any] | None:
    """``{"root": ..., **marker}`` of the snapshot this process runs from, or ``None``."""
    return dict(_ACTIVE) if _ACTIVE else None


def _within(path: str | None, top: Path) -> bool:
    return path is not None and Path(path).resolve().is_relative_to(top)


def check_origin(root: str | os.PathLike | None = None) -> None:
    """Every loaded ``zkqec`` module must come from ``root``/src/zkqec (default: the active
    snapshot, else this checkout); in a snapshot the import guard must still be installed."""
    top = Path(root if root is not None else _ACTIVE.get("root", REPO)).resolve()
    src = top / "src" / "zkqec"
    stray = sorted(name for name, mod in list(sys.modules.items())
                   if (name == "zkqec" or name.startswith("zkqec."))
                   and not _within(getattr(mod, "__file__", None), src))
    if stray:
        raise EvaluatorChanged(f"imported zkqec modules outside {src}: {stray[:5]}")
    if _ACTIVE and not any(isinstance(f, SnapshotImportGuard) for f in sys.meta_path):
        raise EvaluatorChanged("the snapshot import guard was removed from sys.meta_path")


class SnapshotImportGuard:
    """``sys.meta_path`` finder: a ``zkqec`` module may be imported only from the snapshot src."""

    def __init__(self, src: str | os.PathLike):
        self.src = Path(src).resolve()

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "zkqec" and not fullname.startswith("zkqec."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None:
            return None
        origins = [spec.origin] if spec.has_location else list(spec.submodule_search_locations
                                                                 or [])
        if not origins or not all(_within(o, self.src / "zkqec") for o in origins):
            raise ImportError(f"non-snapshot import of {fullname} refused: {origins[:1]} is outside "
                              f"{self.src}")
        return spec

    def invalidate_caches(self) -> None:
        pass


def enter_snapshot(root: str | os.PathLike) -> dict[str, Any]:
    """Called by the snapshot bootstrap before anything else of zkqec is imported: verify the
    snapshot against its marker, refuse any other source of ``zkqec`` on ``sys.path``, install the
    import guard and record the snapshot as this process's evaluator."""
    root = Path(root).resolve()
    info = snapshot_info(root)
    if info is None:
        raise EvaluatorChanged(f"{root} is not an evaluator snapshot")
    src = root / "src"
    if Path(__file__).resolve() != src / "zkqec" / "orchestrator" / "freeze.py":
        raise EvaluatorChanged(f"zkqec is loaded from {Path(__file__).parents[1]}, not {src}")
    fp = worktree_fingerprint(root)
    if fp["sha256"] != info.get("fingerprint"):
        raise EvaluatorChanged(f"snapshot {root} differs from its recorded fingerprint")
    others = [p for p in sys.path if p and Path(p).resolve() != src and (Path(p) / "zkqec").exists()]
    if others:
        raise EvaluatorChanged(f"sys.path provides zkqec outside the snapshot: {others[:3]}")
    if not any(isinstance(f, SnapshotImportGuard) for f in sys.meta_path):
        sys.meta_path.insert(0, SnapshotImportGuard(src))
    _ACTIVE.clear()
    _ACTIVE.update({"root": str(root), **info})
    check_origin(root)
    return active_snapshot()


# --- effective runtime ------------------------------------------------------------------------------
_HASHES: dict[tuple, str] = {}


def _sha256_file(path: str | os.PathLike) -> str | None:
    """sha256 of a file (cached per path, size and mtime), ``None`` if it does not exist."""
    try:
        real = os.path.realpath(path)
        st = os.stat(real)
    except OSError:
        return None
    key = (real, st.st_size, st.st_mtime_ns)
    if key not in _HASHES:
        h = hashlib.sha256()
        with open(real, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _HASHES[key] = h.hexdigest()
    return _HASHES[key]


_VERSIONS: dict[tuple, str | None] = {}


def runtime_facts() -> dict[str, Any]:
    """The effective runtime of this process, frozen into the campaign manifest (§O): interpreter
    (version, executable and its sha256, isolation flags, ``sys.path``), installed distributions
    and the lock, the Claude CLI binary (path, resolved path, sha256), and the broker (mitmdump,
    mitmproxy version, addon/policy-module/base-policy/prices sha256)."""
    import importlib.metadata as md

    from ..broker import launcher
    from ..sandbox.profiles import CLAUDE_LINK
    root = Path(_ACTIVE.get("root", REPO))
    exe = sys.executable
    python = {"version": sys.version, "implementation": sys.implementation.name,
              "executable": exe, "executable_real_path": os.path.realpath(exe),
              "executable_sha256": _sha256_file(exe),
              "flags": {"isolated": sys.flags.isolated, "no_site": sys.flags.no_site,
                        "ignore_environment": sys.flags.ignore_environment},
              "sys_path": list(sys.path)}
    dists = sorted({f"{d.metadata['Name']}=={d.version}" for d in md.distributions()
                    if d.metadata["Name"]})
    mitmdump = Path(launcher.MITMDUMP)
    vkey = (os.path.realpath(mitmdump), _sha256_file(mitmdump))
    if vkey not in _VERSIONS:
        _VERSIONS[vkey] = launcher.mitmproxy_version(mitmdump)
    return {
        "python": python,
        "dependencies": {"lock_sha256": _sha256_file(root / "requirements.lock"),
                         "distributions": dists,
                         "site_packages": [p for p in sys.path if p.endswith("site-packages")]},
        "claude_cli": {"path": str(CLAUDE_LINK), "real_path": os.path.realpath(CLAUDE_LINK),
                       "sha256": _sha256_file(CLAUDE_LINK)},
        "broker": {"mitmdump": str(mitmdump), "mitmdump_sha256": _sha256_file(mitmdump),
                   "mitmproxy_version": _VERSIONS[vkey],
                   "addon_sha256": _sha256_file(launcher.ADDON),
                   "policy_module_sha256": _sha256_file(launcher.POLICY_MODULE),
                   "policy_sha256": _sha256_file(launcher.BASE_POLICY),
                   "prices_sha256": _sha256_file(launcher.PRICES)},
    }


# --- the guard --------------------------------------------------------------------------------------
class EvaluatorGuard:
    """Freeze the evaluator fingerprint at campaign start and re-check it at every boundary.

    Over a snapshot (a :data:`SNAPSHOT_MARKER` at ``repo``) the start check compares the marker's
    commit and fingerprint (no git); in a process that entered a snapshot, a guard over the running
    evaluator (``check_imports=True``) must be over that snapshot. A fingerprint-only guard
    (``check_imports=False``) watches another tree, e.g. the evaluator-tamper control's scratch copy."""

    def __init__(self, repo: Path = REPO, *, commit: str | None = None,
                 check_imports: bool = True):
        self.repo = Path(repo).resolve()
        active = _ACTIVE.get("root")
        if check_imports and active is not None and self.repo != Path(active):
            raise EvaluatorChanged(f"guard over {self.repo}, but the evaluator runs from the "
                                   f"snapshot {active}")
        self.snapshot = snapshot_info(self.repo)
        self.frozen = worktree_fingerprint(self.repo)
        self.commit = commit
        if self.snapshot is not None:
            if self.frozen["sha256"] != self.snapshot.get("fingerprint"):
                raise EvaluatorChanged(f"snapshot {self.repo} differs from its recorded fingerprint")
            if commit is not None and commit != self.snapshot.get("commit"):
                raise EvaluatorChanged(f"the snapshot holds commit {self.snapshot.get('commit')}, "
                                       f"not the recorded commit {commit}")
        elif commit is not None:
            recorded = commit_fingerprint(self.repo, commit)
            if recorded["sha256"] != self.frozen["sha256"]:
                raise EvaluatorChanged(f"evaluator differs from the recorded commit {commit}: "
                                       f"{diff(self.frozen, recorded)}")
        self.check_imports = check_imports
        if check_imports:
            check_origin(self.repo)

    def manifest(self) -> dict[str, Any]:
        return {**self.frozen, "commit": self.commit, "snapshot": self.snapshot,
                "runtime": runtime_facts()}

    def check(self, boundary: str) -> None:
        now = worktree_fingerprint(self.repo)
        if now["sha256"] != self.frozen["sha256"]:
            raise EvaluatorChanged(f"{boundary}: evaluator changed since the freeze: "
                                   f"{diff(self.frozen, now)}")
        if self.snapshot is not None and snapshot_info(self.repo) != self.snapshot:
            raise EvaluatorChanged(f"{boundary}: the snapshot marker changed")
        if self.check_imports:
            try:
                check_origin(self.repo)
            except EvaluatorChanged as exc:
                raise EvaluatorChanged(f"{boundary}: {exc}") from None


def boundary_check(guard: EvaluatorGuard, adm) -> Callable[[str], None]:
    """The campaign's check at every dispatch boundary: on any evaluator change, record an
    ``evaluator_changed`` incident in the ledger, then abort (:class:`EvaluatorChanged`)."""
    def check(boundary: str) -> None:
        try:
            guard.check(boundary)
        except EvaluatorChanged as exc:
            adm.open_incident("evaluator_changed", {"boundary": boundary[:100],
                                                    "detail": str(exc)[:400]})
            raise
    return check


def check_task(adm, campaign_id: str, task) -> None:
    """Abort with an integrity record unless ``task`` equals (canonical JSON) the task frozen under
    its id in the campaign's ``campaign_frozen`` event (§I mode 1: task mutated mid-campaign)."""
    camp = adm.ledger.campaign(campaign_id)
    frozen = {t["task_id"]: t for t in (camp or {}).get("tasks", [])}.get(task.task_id)
    presented = content_hash(task.model_dump(mode="json"))
    if frozen is not None and content_hash(frozen) == presented:
        return
    adm.open_incident("task_mutated", {
        "campaign_id": campaign_id, "task_id": str(task.task_id)[:64],
        "frozen_sha256": None if frozen is None else content_hash(frozen),
        "presented_sha256": presented})
    raise TaskMutated(f"task {str(task.task_id)[:64]!r} differs from the task frozen in campaign "
                      f"{campaign_id}")
