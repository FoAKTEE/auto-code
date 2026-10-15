"""Sealed bundle + offline reconstruction audit (appendix §M). Slow: builds the archived runtime,
wheelhouse and evaluator closures (first run downloads the locked wheels once)."""
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from zkqec.admission import Admission
from zkqec.contracts import Proposal, StructuralTargets, TaskSA
from zkqec.grammar import GrammarCaps
from zkqec.kernel.codes import bicycle_chain
from zkqec.pipeline import Pipeline, build_registry, evaluator_domain
from zkqec.report.bundle import ARCHIVE, audit_bundle, seal
from zkqec.report.render import render
from zkqec.store import ArtifactStore


def sparse(a):
    a = np.asarray(a)
    return {"shape": list(a.shape), "entries": [[int(i), int(j)] for i, j in zip(*np.nonzero(a))]}


@pytest.mark.slow
def test_seal_and_offline_audit_roundtrip(tmp_path):
    run = tmp_path / "run"
    store = ArtifactStore(run / "store")
    reg = build_registry(store)
    adm = Admission(run / "ledger.db", store, reg)
    dom = evaluator_domain(reg)
    task = TaskSA(task_id="sa-fix", targets=StructuralTargets(n_max=50, k_min=6, d_min=6, w_max=8, deg_max=8))
    adm.freeze_campaign("bundle-test", namespace="bundle-test", config={"test": "bundle"},
                        tasks=[task.model_dump(mode="json")], caps={"cpu_seconds": 1e4},
                        labels=["single-implementation", "non-evaluation"], domain=dom,
                        manifest=store.put_json({"test": "bundle"}))
    book = adm.job_book("bundle-test"); book.set_caps({"cpu_seconds": 1e4})
    pipe = Pipeline(adm, store, book, dom, caps=GrammarCaps())
    c = bicycle_chain(3, 7)
    ev = pipe.evaluate(Proposal(family="explicit_sparse", params={"hx": sparse(c.hx), "hz": sparse(c.hz)}), task)
    adm.record_evaluation("bundle-test", "sa-fix", candidate=ev.candidate, source="fixture")
    md = render(adm, "bundle-test")
    adm.close()
    out = tmp_path / "bundles"                      # bundles embed ~130 MB of closures
    entry = seal(run, "bundle-test", run / "ledger.db", run / "store", md, out_dir=out)
    result = audit_bundle(Path(entry["path"]), workdir=tmp_path / "audit")
    assert result["ok"], result
    assert result["replayed_executions"] >= 2 and result["offline"]
    # Codex M1 #15: the bundle file alone reconstructs offline (no archive, no repo)
    alone = tmp_path / "clean-machine"
    alone.mkdir()
    copy = alone / Path(entry["path"]).name
    copy.write_bytes(Path(entry["path"]).read_bytes())
    result = audit_bundle(copy, archive=tmp_path / "no-archive", workdir=alone / "audit")
    assert result["ok"], result
    # tamper: a modified report must fail the offline audit
    tampered = tmp_path / "tampered"
    entry2 = seal(run, "bundle-test", run / "ledger.db", run / "store",
                  md.replace("**verified**", "**verified (novel)**"), out_dir=out)
    assert not audit_bundle(Path(entry2["path"]), workdir=tampered)["ok"]


@pytest.mark.slow
def test_live_like_bundle_carries_broker_evidence_and_confirms_offline(tmp_path, monkeypatch):
    """Codex M1 milestone review B6: a live-like campaign (production provider, real campaign broker
    files, a CLI stand-in) seals its broker evidence, the offline audit reconciles it from the bundle
    alone, and the confirmation supplement checks against the bundle."""
    import zkqec.orchestrator.campaign as campaign
    import zkqec.report.bundle as bundle_mod
    from test_broker_evidence import run_live_like
    orig = bundle_mod.seal
    monkeypatch.setattr(campaign, "seal", lambda *a, **k: orig(*a, out_dir=tmp_path / "bundles", **k))
    out, run, _ = run_live_like(tmp_path, monkeypatch, cid="slow-evid")
    assert out["audit"]["ok"], out["audit"]
    supp = Path(out["confirmation_supplement"]["path"])
    assert out["confirmation_supplement"]["problems"] == []
    assert bundle_mod.check_confirmation(Path(out["bundle"]["path"]), supp) == []
    assert out["confirmation"]["cpu_seconds"] > 0 and out["confirmation"]["usd"] == 0
