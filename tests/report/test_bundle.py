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
from zkqec.orchestrator.jobs import JobBook
from zkqec.pipeline import Pipeline, build_registry, evaluator_domain
from zkqec.report.bundle import ARCHIVE, audit_bundle, seal
from zkqec.report.render import CampaignRecord, EvaluationRef, render
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
    book = JobBook(adm.ledger.conn, "bundle-test"); book.set_caps({"cpu_seconds": 1e4})
    dom = evaluator_domain(reg)
    pipe = Pipeline(adm, store, book, dom, caps=GrammarCaps())
    task = TaskSA(task_id="sa-fix", targets=StructuralTargets(n_max=50, k_min=6, d_min=6, w_max=8, deg_max=8))
    c = bicycle_chain(3, 7)
    ev = pipe.evaluate(Proposal(family="explicit_sparse", params={"hx": sparse(c.hx), "hz": sparse(c.hz)}), task)
    camp = CampaignRecord(campaign_id="bundle-test", manifest={"domain": dom}, tasks=(task,),
                          evaluations=(EvaluationRef(task_id="sa-fix", candidate=ev.candidate, source="fixture"),),
                          budget_totals=book.totals(), labels=("single-implementation", "non-evaluation"))
    md = render(adm, dom, camp)
    adm.close()
    entry = seal(run, "bundle-test", run / "ledger.db", run / "store", md, camp.model_dump(mode="json"))
    result = audit_bundle(Path(entry["path"]), workdir=tmp_path / "audit")
    assert result["ok"], result
    assert result["replayed_executions"] >= 2 and result["offline"]
    # tamper: a modified report must fail the offline audit
    tampered = tmp_path / "tampered"
    entry2 = seal(run, "bundle-test-tampered", run / "ledger.db", run / "store",
                  md.replace("**verified**", "**verified (novel)**"), camp.model_dump(mode="json"))
    assert not audit_bundle(Path(entry2["path"]), workdir=tampered)["ok"]
