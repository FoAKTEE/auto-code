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


# --- §I mode 1: a task that differs from the frozen task aborts with an integrity record ----------
def _frozen(tmp_path, task):
    from zkqec.admission import Admission
    from zkqec.pipeline import build_registry
    from zkqec.store import ArtifactStore
    store = ArtifactStore(tmp_path / "store")
    adm = Admission(tmp_path / "ledger.db", store, build_registry(store))
    adm.freeze_campaign("c1", namespace="c1", config={}, tasks=[task.model_dump(mode="json")],
                        caps={}, labels=[], domain="0" * 64, manifest=store.put_json({}))
    return adm


def _incidents(adm):
    return [e.payload["category"] for e in adm.ledger.events() if e.kind == "incident_opened"]


def test_a_task_differing_from_the_frozen_task_aborts_with_an_integrity_record(tmp_path):
    from zkqec.contracts import StructuralTargets, TaskSA
    task = TaskSA(task_id="sa-1", targets=StructuralTargets(n_max=7, k_min=1, d_min=5))
    adm = _frozen(tmp_path, task)
    try:
        freeze.check_task(adm, "c1", task)                      # unchanged: proceeds
        assert _incidents(adm) == []
        weaker = TaskSA(task_id="sa-1", targets=StructuralTargets(n_max=7, k_min=1, d_min=3))
        unknown = TaskSA(task_id="sa-2", targets=task.targets)
        for presented in (weaker, unknown):
            with pytest.raises(freeze.TaskMutated):
                freeze.check_task(adm, "c1", presented)
        assert _incidents(adm) == ["task_mutated", "task_mutated"]
    finally:
        adm.close()


def test_campaign_aborts_when_its_task_is_mutated_mid_run(tmp_path):
    """A task changed in memory after the freeze (here by the provider double, as a buggy component
    could) is caught before the proposal is evaluated: typed abort, incident, no evaluation."""
    import sqlite3
    from test_campaign import GOOD, ReplayProviderDouble, cfg
    from zkqec.orchestrator.campaign import run_campaign
    config = cfg()
    task = config.tasks[0]

    class MutatingDouble(ReplayProviderDouble):
        def call(self, **kw):
            object.__setattr__(task.targets, "d_min", 5)        # the chain(3,7) code has d = 6
            return super().call(**kw)
    with pytest.raises(freeze.TaskMutated):
        run_campaign(config, MutatingDouble([GOOD]), tmp_path, require_clean=False,
                     seal_bundle=False)
    conn = sqlite3.connect(tmp_path / "t1" / "ledger.db")
    kinds = [k for k, in conn.execute("SELECT kind FROM events ORDER BY seq")]
    cats = [json.loads(p)["category"] for p, in conn.execute(
        "SELECT payload FROM events WHERE kind='incident_opened'")]
    conn.close()
    assert cats == ["task_mutated"] and "evaluation_recorded" not in kinds
    assert not any(k == "execution_opened" for k in kinds)        # nothing was verified


def test_boundary_check_records_an_incident_before_the_abort(repo, tmp_path):
    from zkqec.contracts import StructuralTargets, TaskSA
    adm = _frozen(tmp_path, TaskSA(task_id="sa-1", targets=StructuralTargets(n_max=7, k_min=1,
                                                                           d_min=3)))
    try:
        check = freeze.boundary_check(freeze.EvaluatorGuard(repo, check_imports=False), adm)
        check("verifier l1")
        (repo / "src/zkqec/a.py").write_text("A = 3\n")
        with pytest.raises(freeze.EvaluatorChanged):
            check("verifier l2")
        assert _incidents(adm) == ["evaluator_changed"]
    finally:
        adm.close()


# --- Codex M1 milestone review #14: the evaluator runs from a read-only snapshot (§O) --------------
def _snapshot_of(repo, tmp_path):
    """A snapshot directory (no git) holding the committed evaluator files and their marker."""
    import shutil
    snap = tmp_path / "snap"
    shutil.copytree(repo, snap, ignore=shutil.ignore_patterns(".git"))
    commit = git(repo, "rev-parse", "HEAD")
    fp = freeze.commit_fingerprint(repo, commit)
    (snap / freeze.SNAPSHOT_MARKER).write_text(json.dumps(
        {"kind": freeze.SNAPSHOT_KIND, "commit": commit, "fingerprint": fp["sha256"],
         "evaluator_closure": "e" * 64}))
    return snap, commit


def test_guard_in_a_snapshot_checks_the_marker_not_git(repo, tmp_path):
    snap, commit = _snapshot_of(repo, tmp_path)
    guard = freeze.EvaluatorGuard(snap, commit=commit, check_imports=False)   # no git repo here
    assert guard.manifest()["snapshot"]["commit"] == commit
    guard.check("provider dispatch")
    with pytest.raises(freeze.EvaluatorChanged, match="snapshot"):
        freeze.EvaluatorGuard(snap, commit="0" * 40, check_imports=False)
    (snap / "src/zkqec/a.py").write_text("A = 9\n")
    with pytest.raises(freeze.EvaluatorChanged):
        guard.check("verifier l2")
    with pytest.raises(freeze.EvaluatorChanged, match="snapshot"):
        freeze.EvaluatorGuard(snap, commit=commit, check_imports=False)


def test_every_boundary_check_asserts_the_module_origin(tmp_path):
    import sys
    import types
    guard = freeze.EvaluatorGuard(freeze.REPO)               # this process imports zkqec from REPO
    guard.check("start")
    rogue = types.ModuleType("zkqec._rogue_for_test")
    rogue.__file__ = str(tmp_path / "rogue.py")
    sys.modules[rogue.__name__] = rogue
    try:
        with pytest.raises(freeze.EvaluatorChanged, match="outside"):
            guard.check("controls")
    finally:
        del sys.modules[rogue.__name__]
    guard.check("confirmation")


def test_the_import_guard_refuses_zkqec_modules_from_outside_the_snapshot(tmp_path):
    outside = tmp_path / "outside"
    (outside / "zkqec").mkdir(parents=True)
    (outside / "zkqec" / "__init__.py").write_text("")
    (outside / "zkqec" / "extra.py").write_text("X = 1\n")
    finder = freeze.SnapshotImportGuard(freeze.REPO / "src")
    with pytest.raises(ImportError, match="non-snapshot"):
        finder.find_spec("zkqec.extra", [str(outside / "zkqec")])
    assert finder.find_spec("zkqec.pipeline", [str(freeze.REPO / "src" / "zkqec")]) is not None
    assert finder.find_spec("json", None) is None              # not ours to judge


def test_runtime_facts_pin_python_cli_broker_and_dependencies():
    facts = freeze.runtime_facts()
    py = facts["python"]
    assert py["version"].startswith("3.10") and len(py["executable_sha256"]) == 64
    assert set(facts["claude_cli"]) >= {"path", "sha256"}
    assert set(facts["broker"]) >= {"mitmdump", "mitmproxy_version", "addon_sha256",
                                    "policy_module_sha256", "policy_sha256", "prices_sha256"}
    assert facts["dependencies"]["lock_sha256"] and facts["dependencies"]["distributions"]
