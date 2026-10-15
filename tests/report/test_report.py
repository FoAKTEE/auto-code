"""Templated report + linter (appendix §A5)."""
import json
from pathlib import Path

import numpy as np
import pytest

from zkqec.admission import Admission
from zkqec.contracts import Proposal, StructuralTargets, TaskSA
from zkqec.grammar import GrammarCaps
from zkqec.kernel.codes import bicycle_chain
from zkqec.orchestrator.jobs import JobBook
from zkqec.pipeline import Pipeline, build_registry, evaluator_domain
from zkqec.report.render import COMMENTARY_MARKER, CampaignRecord, EvaluationRef, lint, render
from zkqec.store import ArtifactStore


def sparse(a):
    a = np.asarray(a)
    return {"shape": list(a.shape), "entries": [[int(i), int(j)] for i, j in zip(*np.nonzero(a))]}


@pytest.fixture
def env(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    reg = build_registry(store)
    adm = Admission(tmp_path / "ledger.db", store, reg)
    book = JobBook(adm.ledger.conn, "rep"); book.set_caps({"cpu_seconds": 1e4})
    dom = evaluator_domain(reg)
    pipe = Pipeline(adm, store, book, dom, caps=GrammarCaps())
    c = bicycle_chain(3, 7)
    task = TaskSA(task_id="sa-fix", targets=StructuralTargets(n_max=50, k_min=6, d_min=6, w_max=8, deg_max=8))
    ev = pipe.evaluate(Proposal(family="explicit_sparse", params={"hx": sparse(c.hx), "hz": sparse(c.hz)}), task)
    camp = CampaignRecord(campaign_id="t", manifest={"m": 1}, tasks=(task,),
                          evaluations=(EvaluationRef(task_id="sa-fix", candidate=ev.candidate, source="fixture"),),
                          budget_totals=book.totals(), labels=("single-implementation", "non-evaluation"))
    yield adm, dom, camp
    adm.close()


def test_report_renders_and_lints_clean(env):
    adm, dom, camp = env
    md = render(adm, dom, camp)
    assert "**verified**" in md and lint(md, adm, dom, camp) == []


def test_hand_edit_of_authoritative_section_fails_lint(env):
    adm, dom, camp = env
    md = render(adm, dom, camp).replace("**verified**", "**verified (and novel)**")
    assert lint(md, adm, dom, camp)


def test_commentary_is_allowed_only_after_marker(env):
    adm, dom, camp = env
    md = render(adm, dom, camp, commentary="The code looks elegant.")
    assert COMMENTARY_MARKER in md and lint(md, adm, dom, camp) == []
    assert lint(md + f"\n{COMMENTARY_MARKER}\nmore\n", adm, dom, camp)


def test_report_cites_receipts(env):
    adm, dom, camp = env
    md = render(adm, dom, camp)
    assert "distance_lower_bound" in md and "l2.distance 1.1.0" in md
