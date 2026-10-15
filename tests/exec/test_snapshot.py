"""``zkqec campaign run`` re-executes from the committed evaluator closure, extracted read-only, in an
isolated interpreter whose ``sys.path`` is the snapshot ``src`` plus the locked site-packages only
(contracts §O; Codex M1 milestone review #14).

The tests build a throwaway git repository from this working tree's evaluator files and replace the
archive's ``ensure_closures`` with one that builds only the evaluator closure (``git archive`` of the
commit into the CAS through the bundle module's own helpers): the runtime and wheelhouse closures
need the network and are not used by the launch."""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import zkqec.exec.snapshot as snapshot
import zkqec.report.bundle as bundle_mod
from zkqec import cli
from zkqec.orchestrator import freeze

REPO = freeze.REPO


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture(scope="module")
def evaluator_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("snapshot-repo") / "repo"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")
    for rel in ("src/zkqec", "roles", "configs"):
        shutil.copytree(REPO / rel, repo / rel, ignore=ignore)
    for rel in ("pyproject.toml", "requirements.lock"):
        shutil.copy2(REPO / rel, repo / rel)
    git(repo.parent, "init", "-q", str(repo))
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "evaluator")
    return repo


@pytest.fixture
def env(evaluator_repo, tmp_path, monkeypatch):
    archive = tmp_path / "archive"

    def evaluator_only(archive_dir, *, commit=None):
        tar_bytes = subprocess.run(["git", "-C", str(evaluator_repo), "archive", "--format=tar",
                                    commit, "src", "pyproject.toml", "requirements.lock"],
                                   check=True, capture_output=True).stdout
        work = tmp_path / f"ev-{commit[:8]}"
        if not work.exists():
            (work / "tree").mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as t:
                t.extractall(work / "tree", filter="data")
            bundle_mod._tar_zst(work / "tree", work / "ev.tar.zst", "evaluator")
        sha = bundle_mod._cas_put(work / "ev.tar.zst", {"kind": "evaluator", "git_commit": commit},
                                  Path(archive_dir))
        return {"evaluator": sha, "runtime": "0" * 64, "wheelhouse": "1" * 64,
                "git_commit": commit, "lock_sha256": "2" * 64}
    monkeypatch.setattr(snapshot, "ensure_closures", evaluator_only)
    monkeypatch.setattr(snapshot, "REPO", evaluator_repo)
    yield {"repo": evaluator_repo, "archive": archive, "commit": git(evaluator_repo, "rev-parse",
                                                                     "HEAD")}
    for p in sorted(archive.rglob("*"), reverse=True):        # read-only snapshot: allow cleanup
        if not p.is_symlink():
            os.chmod(p, 0o700)


def run_inner(root, entry, *, cwd=None, extra_env=None):
    cmd = snapshot.command(root, entry=entry)
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env, timeout=120)


def test_the_snapshot_is_the_committed_closure_and_read_only(env):
    root = snapshot.prepare(archive=env["archive"])
    info = json.loads((root / freeze.SNAPSHOT_MARKER).read_text())
    assert info["commit"] == env["commit"] and info["kind"] == freeze.SNAPSHOT_KIND
    assert freeze.worktree_fingerprint(root)["sha256"] == info["fingerprint"] == \
        freeze.commit_fingerprint(env["repo"], env["commit"])["sha256"]
    assert (root / "roles" / "designer" / "output.schema.json").is_file()
    for p in [root, *root.rglob("*")]:
        if p.is_symlink():
            continue
        assert not os.access(p, os.W_OK), p
        assert not stat.S_IMODE(p.stat().st_mode) & 0o222, p
    assert snapshot.prepare(archive=env["archive"]) == root          # reused after verification


def test_campaign_run_executes_from_the_snapshot(env, capfd):
    cfg = env["repo"] / "configs" / "campaigns" / "m1-live.json"
    rc = cli.main(["campaign", "run", str(cfg), "--dry-run", "--archive", str(env["archive"])])
    out = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert rc == 0
    root = Path(out["snapshot_root"])
    src = root / "src"
    assert root.is_relative_to(env["archive"]) and out["snapshot"]["commit"] == env["commit"]
    assert out["modules"] and all(Path(f).is_relative_to(src / "zkqec")
                                  for f in out["modules"].values())
    assert str(REPO / "src") not in out["sys_path"] and str(env["repo"] / "src") not in out["sys_path"]
    assert out["sys_path"][0] == str(src)
    assert out["runtime"]["python"]["flags"] == {"isolated": 1, "no_site": 1,
                                                 "ignore_environment": 1}
    assert out["config"]["campaign_id"]


def test_a_working_tree_modified_after_launch_does_not_change_the_executed_code(env):
    root = snapshot.prepare(archive=env["archive"])
    init = env["repo"] / "src" / "zkqec" / "__init__.py"
    original = init.read_text()
    try:
        init.write_text(original.replace('__version__ = "0.0.1"', '__version__ = "tampered"'))
        (env["repo"] / "src" / "zkqec" / "injected.py").write_text("X = 1\n")
        probe = ("import json, zkqec, importlib.util as u; "
                 "print(json.dumps({'version': zkqec.__version__, 'file': zkqec.__file__, "
                 "'injected': u.find_spec('zkqec.injected') is not None}))")
        res = run_inner(root, probe, cwd=env["repo"] / "src",
                        extra_env={"PYTHONPATH": str(env["repo"] / "src")})
        assert res.returncode == 0, res.stderr
        seen = json.loads(res.stdout.strip().splitlines()[-1])
        assert seen == {"version": "0.0.1", "file": str(root / "src" / "zkqec" / "__init__.py"),
                        "injected": False}
    finally:
        init.write_text(original)
        (env["repo"] / "src" / "zkqec" / "injected.py").unlink(missing_ok=True)


def test_a_non_snapshot_import_is_refused(env, tmp_path):
    root = snapshot.prepare(archive=env["archive"])
    outside = tmp_path / "outside"
    shutil.copytree(env["repo"] / "src", outside)
    (outside / "zkqec" / "extra.py").write_text("X = 1\n")
    probe = f"""
import importlib.util, json, sys, zkqec
from zkqec.orchestrator import freeze
out = {{}}
zkqec.__path__.append({str(outside / 'zkqec')!r})
try:
    import zkqec.extra
    out["extended_path"] = "imported"
except ImportError as e:
    out["extended_path"] = "refused" if "non-snapshot" in str(e) else str(e)
sys.path.insert(0, {str(outside)!r})
for name in [m for m in sys.modules if m == "zkqec" or m.startswith("zkqec.")]:
    sys.modules.pop(name)
try:
    import zkqec as again
    out["reimport"] = again.__file__
except ImportError as e:
    out["reimport"] = "refused" if "non-snapshot" in str(e) else str(e)
spec = importlib.util.spec_from_file_location("zkqec.rogue", {str(outside / 'zkqec' / 'extra.py')!r})
sys.modules["zkqec.rogue"] = importlib.util.module_from_spec(spec)
try:
    freeze.check_origin()
    out["boundary"] = "passed"
except freeze.EvaluatorChanged:
    out["boundary"] = "refused"
print(json.dumps(out))
"""
    res = run_inner(root, probe)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip().splitlines()[-1]) == {
        "extended_path": "refused", "reimport": "refused", "boundary": "refused"}


def test_a_dirty_evaluator_tree_is_refused_before_launch(env):
    pol = env["repo"] / "configs" / "broker" / "policy.json"
    original = pol.read_text()
    try:
        pol.write_text(original + "\n")
        with pytest.raises(snapshot.SnapshotError, match="dirty"):
            snapshot.prepare(archive=env["archive"])
    finally:
        pol.write_text(original)


def test_a_tampered_snapshot_is_refused(env):
    root = snapshot.prepare(archive=env["archive"])
    target = root / "src" / "zkqec" / "pipeline.py"
    os.chmod(target.parent, 0o755)
    os.chmod(target, 0o644)
    target.write_text(target.read_text() + "\n# tampered\n")
    with pytest.raises(snapshot.SnapshotError, match="fingerprint"):
        snapshot.prepare(archive=env["archive"])
    res = run_inner(root, "print('ran')")
    assert res.returncode != 0 and "ran" not in res.stdout
    assert "EvaluatorChanged" in res.stderr
