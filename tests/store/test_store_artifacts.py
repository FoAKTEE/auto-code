"""Content-addressed store: layout, integrity, unsafe paths, caps, publication order (§L, §M)."""
import hashlib
import os

import pytest

from zkqec.contracts import content_hash
from zkqec.store import ArtifactNotFound, ArtifactStore, ArtifactTooLarge, IntegrityError
from zkqec.store import artifacts as artifacts_mod


def test_roundtrip_and_sharded_layout(tmp_path):
    store = ArtifactStore(tmp_path)
    obj = {"b": [1, 2], "a": "x"}
    sha = store.put_json(obj)
    assert sha == content_hash(obj)
    assert store.path_for(sha) == tmp_path / sha[:2] / sha[2:4] / sha
    assert store.path_for(sha).is_file() and store.exists(sha)
    assert store.get_json(sha) == obj
    raw = store.put_bytes(b"\x00binary\xff", "npy")
    assert raw == hashlib.sha256(b"\x00binary\xff").hexdigest()
    assert store.get_bytes(raw) == b"\x00binary\xff"
    assert store.put_json(obj) == sha                          # idempotent re-publish
    assert sorted(store.iter_shas()) == sorted({sha, raw})


def test_missing_and_malformed_addresses(tmp_path):
    store = ArtifactStore(tmp_path)
    assert not store.exists("0" * 64)
    with pytest.raises(ArtifactNotFound):
        store.get_bytes("0" * 64)
    with pytest.raises(ValueError):
        store.get_bytes("../../etc/passwd")


def test_tampered_file_is_detected(tmp_path):
    store = ArtifactStore(tmp_path)
    sha = store.put_json({"d": 3})
    path = store.path_for(sha)
    os.chmod(path, 0o644)
    path.write_bytes(b'{"d":5}')
    with pytest.raises(IntegrityError):
        store.get_bytes(sha)
    with pytest.raises(IntegrityError):
        store.verify(sha)
    with pytest.raises(IntegrityError):                       # never silently overwritten
        store.put_json({"d": 3})


def test_symlinked_artifact_and_shard_are_rejected(tmp_path):
    store = ArtifactStore(tmp_path / "s")
    sha = store.put_json({"v": 1})
    path = store.path_for(sha)
    copy = tmp_path / "elsewhere"
    copy.write_bytes(path.read_bytes())                       # same, valid bytes
    path.unlink()
    path.symlink_to(copy)
    with pytest.raises(IntegrityError):
        store.get_bytes(sha)
    assert not store.exists(sha)
    # a symlinked shard directory is refused as well
    other = store.put_json({"v": 2})
    shard = store.path_for(other).parent
    real = tmp_path / "realshard"
    shard.rename(real)
    shard.symlink_to(real, target_is_directory=True)
    with pytest.raises(IntegrityError):
        store.get_bytes(other)


def test_non_regular_file_is_rejected_without_blocking(tmp_path):
    store = ArtifactStore(tmp_path)
    sha = store.put_json({"v": 1})
    path = store.path_for(sha)
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(IntegrityError):
        store.get_bytes(sha)


def test_byte_caps(tmp_path):
    store = ArtifactStore(tmp_path, max_bytes=64)
    sha = store.put_bytes(b"x" * 40, "text")
    with pytest.raises(ArtifactTooLarge):
        store.get_bytes(sha, max_bytes=10)
    with pytest.raises(ArtifactTooLarge):
        store.put_bytes(b"y" * 65, "text")


def test_publication_order_is_write_fsync_rename_fsyncdir(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    calls = []
    for name in ("_write_all", "_fsync_file", "_replace", "_fsync_dir"):
        real = getattr(artifacts_mod, name)

        def wrapped(*a, _real=real, _name=name):
            calls.append((_name, a))
            return _real(*a)
        monkeypatch.setattr(artifacts_mod, name, wrapped)
    sha = store.put_json({"order": True})
    dest = store.path_for(sha)
    steps = [c[0] for c in calls]
    i = steps.index("_write_all")
    assert steps[i:i + 4] == ["_write_all", "_fsync_file", "_replace", "_fsync_dir"]
    src, dst = calls[i + 2][1]
    assert os.path.dirname(src) == str(dest.parent) and dst == str(dest)  # temp in dest dir
    assert calls[i + 3][1][0] == str(dest.parent)


@pytest.mark.parametrize("step", ["_write_all", "_fsync_file", "_replace"])
def test_crash_before_rename_publishes_nothing(tmp_path, monkeypatch, step):
    store = ArtifactStore(tmp_path)
    obj = {"crash": step}
    sha = content_hash(obj)

    def boom(*a):
        raise OSError(f"injected crash at {step}")
    monkeypatch.setattr(artifacts_mod, step, boom)
    with pytest.raises(OSError):
        store.put_json(obj)
    assert not store.exists(sha)
    shard = store.path_for(sha).parent
    assert not any(p.name.startswith(".tmp-") for p in shard.iterdir())   # temp cleaned up
    monkeypatch.undo()
    assert store.put_json(obj) == sha and store.exists(sha)


def test_crash_after_rename_needs_ensure_durable(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    obj = {"crash": "dir-fsync"}
    store.path_for(content_hash(obj)).parent.mkdir(parents=True)   # shards exist already

    def boom(*a):
        raise OSError("injected crash at _fsync_dir")
    monkeypatch.setattr(artifacts_mod, "_fsync_dir", boom)
    with pytest.raises(OSError):
        store.put_json(obj)
    sha = content_hash(obj)
    assert store.exists(sha)                       # renamed, but durability not established
    with pytest.raises(OSError):
        store.ensure_durable(sha)                  # admission calls this before any commit
    monkeypatch.undo()
    store.ensure_durable(sha)
