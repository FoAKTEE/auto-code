"""Bundle, CAS and index publication durability (appendix §L publication order; Codex M1 #16).

Every published file is written to a temporary file in the destination directory, fsynced, renamed
and the directory fsynced — never ``shutil.move``d from another filesystem — before a reference to
it is returned."""
import json
import os
from pathlib import Path

import pytest

import zkqec.report.bundle as bundle_mod
import zkqec.store.durable as durable


@pytest.fixture
def trace(monkeypatch):
    calls = []
    real_fd, real_dir = durable._fsync_fd, durable.fsync_dir

    def fd(f):
        calls.append(("file", os.readlink(f"/proc/self/fd/{f}")))
        real_fd(f)

    def d(p):
        calls.append(("dir", str(p)))
        real_dir(p)
    monkeypatch.setattr(durable, "_fsync_fd", fd)
    monkeypatch.setattr(durable, "fsync_dir", d)

    def no_move(*a, **k):
        raise AssertionError("shutil.move used for publication")
    monkeypatch.setattr(bundle_mod.shutil, "move", no_move)
    return calls


def _published(calls, dest: Path) -> bool:
    """A file fsync of a temp in dest's directory, followed by an fsync of that directory."""
    for i, (kind, what) in enumerate(calls):
        if kind == "file" and Path(what).parent == dest.parent and what != str(dest):
            return ("dir", str(dest.parent)) in calls[i + 1:]
    return False


def test_cas_put_publishes_through_destination_temp(tmp_path, trace):
    src = tmp_path / "elsewhere" / "x.tar.zst"
    src.parent.mkdir()
    src.write_bytes(b"closure bytes")
    sha = bundle_mod._cas_put(src, {"kind": "test"}, tmp_path / "archive")
    dest = tmp_path / "archive" / "cas" / f"{sha}.tar.zst"
    assert dest.read_bytes() == b"closure bytes"
    assert _published(trace, dest)
    side = dest.with_name(f"{sha}.json")
    assert json.loads(side.read_text())["sha256"] == sha and _published(trace, side)
    # the created ancestors' entries are synced too
    assert ("dir", str(tmp_path / "archive")) in trace and ("dir", str(tmp_path)) in trace


def test_tar_zst_publishes_durably(tmp_path, trace):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    out = tmp_path / "out" / "b.tar.zst"
    out.parent.mkdir()
    bundle_mod._tar_zst(src, out, "x")
    assert out.is_file() and _published(trace, out)
    assert not any(p.name.endswith(".tmp") for p in out.parent.iterdir())


def test_seal_scans_every_bundle_input_for_secrets(tmp_path, monkeypatch):
    """Codex M1 review #8: sealing refuses secret-bearing inputs instead of copying them."""
    from zkqec.admission import Admission, VerifierRegistry
    from zkqec.store import ArtifactStore, SecretDetected
    run = tmp_path / "run"
    store = ArtifactStore(run / "store")
    Admission(run / "ledger.db", store, VerifierRegistry()).close()
    calls = run / "calls"
    calls.mkdir()
    (calls / "x.json").write_text('{"api_key": "opaque-secret-123"}')
    monkeypatch.setattr(bundle_mod, "ensure_closures", lambda *a, **k: {})
    archive = tmp_path / "archive"
    with pytest.raises(SecretDetected, match="calls/x.json"):
        bundle_mod.seal(run, "c", run / "ledger.db", run / "store", "# r\n", {}, archive=archive,
                        extra_dirs={"calls": calls}, commit="0" * 40)
    assert not (archive / "bundles").exists() or not any((archive / "bundles").rglob("*.tar.zst"))
