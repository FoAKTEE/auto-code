"""Directory publication re-establishes durability from the leaf up to a known durable root, whether
or not a directory was just created (appendix §L; Codex M1 milestone review B7).

A small model tracks durability: ``os.mkdir(d)`` makes the entry of ``d`` in its parent volatile, and
``os.fsync`` of a directory makes every entry in it durable. A crash is an exception injected at the
k-th ``mkdir`` or ``fsync``; the directories already created stay (the running filesystem keeps them)
but their entries stay volatile until a retry syncs them."""
import os
from pathlib import Path

import pytest

import zkqec.store.durable as durable


class Disk:
    """Records mkdir/fsync, tracks volatile directory entries, and crashes on request."""

    def __init__(self, monkeypatch):
        self.volatile: set[Path] = set()
        self.synced: list[Path] = []
        self.calls: list[tuple[str, Path]] = []
        self.crash_at: tuple[str, int] | None = None
        self.count = {"mkdir": 0, "fsync": 0}
        self._mkdir, self._fsync = os.mkdir, os.fsync
        monkeypatch.setattr(os, "mkdir", self.mkdir)
        monkeypatch.setattr(os, "fsync", self.fsync)

    def _step(self, op: str, path: Path) -> None:
        self.calls.append((op, path))
        n, self.count[op] = self.count[op], self.count[op] + 1
        if self.crash_at == (op, n):
            raise OSError(f"injected crash at {op} #{n} ({path})")

    def mkdir(self, path, mode=0o777, *a, **k):
        path = Path(path).absolute()
        self._step("mkdir", path)
        self._mkdir(path, mode, *a, **k)
        self.volatile.add(path)

    def fsync(self, fd):
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        self._step("fsync", path)
        self._fsync(fd)
        if path.is_dir():
            self.synced.append(path)
            self.volatile -= {p for p in self.volatile if p.parent == path}


def chain(leaf: Path, root: Path) -> list[Path]:
    out = [leaf]
    while out[-1] != root:
        out.append(out[-1].parent)
    return out


def mount_point(p: Path) -> Path:
    while not os.path.ismount(p):
        p = p.parent
    return p


def _count(tmp_path, monkeypatch, op):
    """How many ``op`` steps a clean publication of the 4-level chain takes."""
    disk = Disk(monkeypatch)
    durable.makedirs_durable(tmp_path / "probe" / "a" / "b" / "c")
    return sum(1 for o, _ in disk.calls if o == op)


@pytest.mark.parametrize("op", ["mkdir", "fsync"])
def test_crash_at_every_step_then_retry_leaves_every_entry_durable(tmp_path, monkeypatch, op):
    steps = _count(tmp_path, monkeypatch, op)
    monkeypatch.undo()
    assert steps >= 4
    for k in range(steps):
        leaf = tmp_path / f"{op}{k}" / "archive" / "bundles" / "campaign"
        disk = Disk(monkeypatch)
        disk.crash_at = (op, k)
        with pytest.raises(OSError, match="injected crash"):
            durable.makedirs_durable(leaf)
        disk.crash_at = None
        disk.synced.clear()
        durable.makedirs_durable(leaf)                       # the retry
        assert leaf.is_dir()
        assert not disk.volatile, f"crash at {op} #{k}: volatile entries {sorted(disk.volatile)}"
        assert set(chain(leaf, mount_point(leaf))) <= set(disk.synced)
        monkeypatch.undo()


def test_retry_with_the_whole_chain_already_present_syncs_every_ancestor(tmp_path, monkeypatch):
    """Codex's reproduction: after a crash between multilevel mkdir and the parent fsyncs, retry
    found ``missing == []`` and synced only the immediate parent."""
    leaf = tmp_path / "review-new" / "archive" / "bundles" / "campaign"
    disk = Disk(monkeypatch)
    disk.crash_at = ("fsync", 0)                             # every mkdir done, no fsync yet
    with pytest.raises(OSError):
        durable.makedirs_durable(leaf)
    assert leaf.is_dir() and len(disk.volatile) == 4
    disk.crash_at = None
    durable.makedirs_durable(leaf)
    assert not disk.volatile
    for d in (leaf, leaf.parent, leaf.parent.parent, tmp_path / "review-new", tmp_path):
        assert d in disk.synced


def test_every_publication_resyncs_the_chain_up_to_an_explicit_root(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir()
    dest = root / "cas" / "ab" / "x.json"
    durable.atomic_write_bytes(dest, b"1", root=root)
    disk = Disk(monkeypatch)
    durable.atomic_write_bytes(dest, b"2", root=root)        # everything exists already
    assert dest.read_bytes() == b"2"
    dirs = [p for p in disk.synced if p.is_dir()]
    assert set(dirs) == {dest.parent, root / "cas", root}    # nothing above the durable root
    files = [p for o, p in disk.calls if o == "fsync" and not p.is_dir()]
    assert len(files) == 1 and files[0].parent == dest.parent


def test_default_root_is_the_mount_point(tmp_path, monkeypatch):
    disk = Disk(monkeypatch)
    durable.atomic_write_bytes(tmp_path / "a" / "b.bin", b"x")
    assert set(chain(tmp_path / "a", mount_point(tmp_path))) <= set(disk.synced)


def test_root_must_be_an_existing_ancestor(tmp_path):
    with pytest.raises(ValueError):
        durable.makedirs_durable(tmp_path / "x", root=tmp_path / "elsewhere")
    with pytest.raises(FileNotFoundError):
        durable.makedirs_durable(tmp_path / "missing" / "x", root=tmp_path / "missing")
