"""Evaluator freeze by content: a fingerprint over every zkqec source file, roles/ and
configs/broker/, equal to the recorded git commit at start and re-checked at every dispatch
boundary (Codex M1 review #14; prompt §4.3; appendix §O)."""
import json
import subprocess

import pytest

from zkqec.orchestrator import freeze


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    for rel, text in {"src/zkqec/a.py": "A = 1\n", "src/zkqec/kernel/k.py": "K = 2\n",
                      "roles/designer/P-min.md": "contract\n",
                      "configs/broker/policy.json": "{}\n", "configs/campaigns/x.json": "{}\n",
                      "README.md": "not evaluator\n"}.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    git(r.parent, "init", "-q", str(r))
    git(r, "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return r


def test_fingerprint_covers_sources_roles_and_broker_config(repo):
    fp = freeze.worktree_fingerprint(repo)
    assert sorted(fp["files"]) == ["configs/broker/policy.json", "roles/designer/P-min.md",
                                   "src/zkqec/a.py", "src/zkqec/kernel/k.py"]
    assert fp == freeze.commit_fingerprint(repo, git(repo, "rev-parse", "HEAD"))
    (repo / "src/zkqec/__pycache__").mkdir()
    (repo / "src/zkqec/__pycache__/a.cpython-310.pyc").write_bytes(b"\0")
    assert freeze.worktree_fingerprint(repo) == fp                  # bytecode caches excluded


@pytest.mark.parametrize("change", [
    lambda r: (r / "src/zkqec/a.py").write_text("A = 2\n"),                  # edited source
    lambda r: (r / "src/zkqec/new.py").write_text("X = 1\n"),                # untracked module
    lambda r: (r / "roles/designer/P-min.md").write_text("changed\n"),       # role contract
    lambda r: (r / "configs/broker/policy.json").write_text('{"a": 1}\n'),   # broker policy
    lambda r: (r / "src/zkqec/kernel/k.py").unlink(),                        # deleted file
])
def test_any_change_is_a_typed_abort(repo, change):
    guard = freeze.EvaluatorGuard(repo, commit=git(repo, "rev-parse", "HEAD"),
                                  check_imports=False)
    guard.check("start")
    change(repo)
    with pytest.raises(freeze.EvaluatorChanged):
        guard.check("provider dispatch")
    with pytest.raises(freeze.EvaluatorChanged, match="recorded commit"):
        freeze.EvaluatorGuard(repo, commit=git(repo, "rev-parse", "HEAD"), check_imports=False)


def test_unrelated_files_do_not_trip_the_guard(repo):
    guard = freeze.EvaluatorGuard(repo, commit=git(repo, "rev-parse", "HEAD"), check_imports=False)
    (repo / "README.md").write_text("edited\n")
    (repo / "configs/campaigns/x.json").write_text('{"b": 2}\n')
    guard.check("provider dispatch")


def test_the_imported_zkqec_package_must_be_the_fingerprinted_tree(repo):
    with pytest.raises(freeze.EvaluatorChanged, match="imported"):
        freeze.EvaluatorGuard(repo, commit=git(repo, "rev-parse", "HEAD"))


def test_campaign_aborts_when_the_evaluator_changes_mid_run(tmp_path, monkeypatch):
    from test_campaign import GOOD, ReplayProviderDouble, cfg
    from zkqec.orchestrator.campaign import run_campaign
    real = freeze.worktree_fingerprint
    calls = {"n": 0}

    def drifting(repo):
        calls["n"] += 1
        fp = real(repo)
        if calls["n"] > 2:                                  # changes after the first dispatch
            fp = {**fp, "sha256": "0" * 64}
        return fp
    monkeypatch.setattr(freeze, "worktree_fingerprint", drifting)
    prov = ReplayProviderDouble(["not json", GOOD, GOOD])
    with pytest.raises(freeze.EvaluatorChanged):
        run_campaign(cfg(), prov, tmp_path, require_clean=False, seal_bundle=False)
    assert len(prov.requests) <= 1
    import sqlite3
    conn = sqlite3.connect(tmp_path / "t1" / "ledger.db")
    cats = [json.loads(p)["category"] for p, in conn.execute(
        "SELECT payload FROM events WHERE kind='incident_opened'")]
    assert "evaluator_changed" in cats
    manifest = json.loads((tmp_path / "t1" / "manifest.json").read_text())
    assert "src/zkqec/pipeline.py" in manifest["evaluator_fingerprint"]["files"]
    assert "roles/designer/output.schema.json" in manifest["evaluator_fingerprint"]["files"]
