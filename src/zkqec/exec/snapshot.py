"""Run ``zkqec`` from the committed evaluator closure (contracts §O; Codex M1 milestone review #14).

``zkqec campaign run`` (:mod:`zkqec.cli`) never executes the working tree. It

1. refuses a dirty evaluator tree (``src``, ``roles``, ``configs``, ``pyproject.toml``,
   ``requirements.lock``), then takes ``HEAD``;
2. obtains the commit's evaluator closure from the archive CAS through
   :func:`zkqec.report.bundle.ensure_closures` (the same closure every sealed bundle embeds), checks
   its content address and extracts it; ``roles/`` and ``configs/`` of the same commit are added
   from ``git archive`` when the closure does not carry them; the tree's fingerprint must equal the
   commit's (:func:`zkqec.orchestrator.freeze.commit_fingerprint`);
3. writes the :data:`~zkqec.orchestrator.freeze.SNAPSHOT_MARKER`, links the locked broker runtime
   (``.venv-broker``, outside the fingerprint), makes every file and directory read-only and
   publishes the snapshot at ``ARCHIVE/snapshots/<commit>-<closure>/evaluator`` (an existing one is
   reused only after its fingerprint verifies);
4. re-executes the same command there with this interpreter (the ``.venv`` python, a link to the
   archived runtime) in isolated mode, ``-I -S``: ``sys.path`` is the snapshot ``src``, the
   interpreter's stdlib and the locked site-packages only (no ``.pth`` processing, so the editable
   install's repository ``src`` never appears). The bootstrap calls
   :func:`~zkqec.orchestrator.freeze.enter_snapshot` before anything else of ``zkqec`` runs.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
from pathlib import Path
from typing import Sequence

from ..orchestrator import freeze
from ..report.bundle import ARCHIVE, _sha_file, _untar_zst, ensure_closures
from ..store.durable import fsync_dir, makedirs_durable

REPO = freeze.REPO
TREE_PATHS = ("src", "roles", "configs", "pyproject.toml", "requirements.lock")
DATA_PATHS = ("roles", "configs")          # committed data the evaluator closure may not carry
BROKER_VENV = ".venv-broker"

BOOTSTRAP = """\
import sys
_i = sys.argv.index("--")
_root, _site, _args = sys.argv[1], sys.argv[2:_i], sys.argv[_i + 1:]
sys.path[:] = [_root + "/src"] + [p for p in sys.path if p and not p.endswith("site-packages")] \\
    + _site
from zkqec.orchestrator import freeze as _freeze
_freeze.enter_snapshot(_root)
del _i, _freeze
"""
CLI_ENTRY = "from zkqec.cli import main\nsys.exit(main(_args))\n"


class SnapshotError(RuntimeError):
    """The evaluator snapshot cannot be built, verified or entered."""


def _git(repo: Path, *args: str, binary: bool = False):
    out = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return out.stdout if binary else out.stdout.decode().strip()


def _read_only(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                os.chmod(p, 0o444)
        for name in dirnames:
            p = os.path.join(dirpath, name)
            if not os.path.islink(p):
                os.chmod(p, 0o555)
    os.chmod(root, 0o555)


def _stamp(root: Path, when: int) -> None:
    """Set every entry's mtime to the commit time. The closure tar stores mtime 0, and mitmproxy
    loads a script only once its mtime exceeds 0: a zero-mtime broker addon never loads (m1-live-4)."""
    for p in [root, *root.rglob("*")]:
        os.utime(p, (when, when), follow_symlinks=False)


def _verify(root: Path, commit: str, expected: dict) -> Path:
    """The snapshot's marker names ``commit`` and its files carry the commit's fingerprint."""
    info = freeze.snapshot_info(root)
    if info is None or info.get("commit") != commit:
        raise SnapshotError(f"{root} is not the snapshot of {commit}")
    if any(p.lstat().st_mtime <= 0 for p in [root, *root.rglob("*")]):
        raise SnapshotError(f"snapshot {root} has zero mtimes (built before the commit-time stamp)")
    fp = freeze.worktree_fingerprint(root)["sha256"]
    if not fp == info.get("fingerprint") == expected["sha256"]:
        raise SnapshotError(f"snapshot {root} differs from its recorded fingerprint")
    return root


def prepare(*, archive: str | os.PathLike | None = None, repo: str | os.PathLike | None = None,
            commit: str | None = None) -> Path:
    """Build (or reuse, after verification) the read-only snapshot of ``commit`` (default
    ``HEAD`` of a clean ``repo``) and return its root."""
    repo = Path(repo or REPO).resolve()
    archive = Path(archive or ARCHIVE).absolute()
    dirty = _git(repo, "status", "--porcelain", "--", *TREE_PATHS)
    if dirty:
        raise SnapshotError(f"the evaluator tree is dirty; commit it first: {dirty.splitlines()[:5]}")
    commit = commit or _git(repo, "rev-parse", "HEAD")
    expected = freeze.commit_fingerprint(repo, commit)
    closure = ensure_closures(archive, commit=commit)["evaluator"]
    cas = archive / "cas" / f"{closure}.tar.zst"
    if _sha_file(cas) != closure:
        raise SnapshotError(f"evaluator closure {cas} does not match its content address")
    parent = makedirs_durable(archive / "snapshots")
    final = parent / f"{commit}-{closure[:16]}"
    if final.exists():
        return _verify(final / "evaluator", commit, expected)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=parent))
    try:
        _untar_zst(cas, tmp)                          # -> tmp/evaluator/{src,pyproject.toml,...}
        root = tmp / "evaluator"
        missing = [p for p in DATA_PATHS if not (root / p).exists()]
        if missing:
            data = _git(repo, "archive", "--format=tar", commit, *missing, binary=True)
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                t.extractall(root, filter="data")
        fp = freeze.worktree_fingerprint(root)
        if fp["sha256"] != expected["sha256"]:
            raise SnapshotError(f"extracted evaluator differs from commit {commit}: "
                                f"{freeze.diff(fp, expected)}")
        (root / freeze.SNAPSHOT_MARKER).write_text(json.dumps(
            {"kind": freeze.SNAPSHOT_KIND, "commit": commit, "evaluator_closure": closure,
             "fingerprint": fp["sha256"], "repository": str(repo)}, indent=2, sort_keys=True))
        if (repo / BROKER_VENV).is_dir():
            os.symlink((repo / BROKER_VENV).resolve(), root / BROKER_VENV)   # runtime, not evaluator code
        _stamp(root, int(_git(repo, "show", "-s", "--format=%ct", commit)))
        _read_only(root)
        try:
            os.rename(tmp, final)
        except OSError:
            if not final.exists():
                raise
            return _verify(final / "evaluator", commit, expected)    # a concurrent launch won
        fsync_dir(parent)
        tmp = None
        return _verify(final / "evaluator", commit, expected)
    finally:
        if tmp is not None and tmp.exists():
            for p in [tmp, *tmp.rglob("*")]:
                if not p.is_symlink():
                    os.chmod(p, 0o700)
            shutil.rmtree(tmp, ignore_errors=True)


def site_packages() -> list[str]:
    """The locked dependency directories of this interpreter's environment."""
    paths = sysconfig.get_paths()
    return list(dict.fromkeys(p for p in (paths["purelib"], paths["platlib"]) if p))


def command(root: str | os.PathLike, argv: Sequence[str] = (), *, entry: str | None = None,
            python: str | None = None) -> list[str]:
    """The isolated command that runs ``zkqec <argv>`` (or ``entry`` code) from the snapshot."""
    return [python or sys.executable, "-I", "-S", "-c", BOOTSTRAP + (entry or CLI_ENTRY),
            str(root), *site_packages(), "--", *argv]


def launch(argv: Sequence[str], *, archive: str | os.PathLike | None = None) -> int:
    """Prepare the snapshot of ``HEAD`` and run ``zkqec <argv>`` from it; return its exit code.
    An explicit ``archive`` is also the inner process's archive (``ZKQEC_ARCHIVE``: sealing)."""
    root = prepare(archive=archive)
    env = dict(os.environ)
    if archive is not None:
        env["ZKQEC_ARCHIVE"] = str(Path(archive).absolute())
    return subprocess.run(command(root, argv), env=env).returncode
