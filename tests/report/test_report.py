"""Templated report + linter (appendix §A5)."""
import json
from pathlib import Path

import numpy as np
import pytest

from zkqec.admission import Admission
from zkqec.contracts import Proposal, StructuralTargets, TaskSA
from zkqec.grammar import GrammarCaps
from zkqec.kernel.codes import bicycle_chain
from zkqec.pipeline import Pipeline, build_registry, evaluator_domain
from zkqec.report.render import COMMENTARY_MARKER, lint, render
from zkqec.store import ArtifactStore


def sparse(a):
    a = np.asarray(a)
    return {"shape": list(a.shape), "entries": [[int(i), int(j)] for i, j in zip(*np.nonzero(a))]}


def frozen_campaign(adm, store, dom, cid, tasks, caps):
    """Freeze a campaign in the ledger (the only source of report metadata)."""
    manifest = store.put_json({"campaign": cid})
    adm.freeze_campaign(cid, namespace=cid, config={"campaign": cid},
                        tasks=[t.model_dump(mode="json") for t in tasks], caps=caps,
                        labels=["single-implementation", "non-evaluation"], domain=dom,
                        manifest=manifest)
    book = adm.job_book(cid)
    book.set_caps(caps)
    return book


@pytest.fixture
def env(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    reg = build_registry(store)
    adm = Admission(tmp_path / "ledger.db", store, reg)
    dom = evaluator_domain(reg)
    c = bicycle_chain(3, 7)
    task = TaskSA(task_id="sa-fix", targets=StructuralTargets(n_max=50, k_min=6, d_min=6, w_max=8, deg_max=8))
    book = frozen_campaign(adm, store, dom, "t", [task], {"cpu_seconds": 1e4})
    pipe = Pipeline(adm, store, book, dom, caps=GrammarCaps())
    ev = pipe.evaluate(Proposal(family="explicit_sparse", params={"hx": sparse(c.hx), "hz": sparse(c.hz)}), task)
    adm.record_evaluation("t", "sa-fix", candidate=ev.candidate, source="fixture")
    yield adm, "t"
    adm.close()


def test_report_renders_and_lints_clean(env):
    adm, cid = env
    md = render(adm, cid)
    assert "**verified**" in md and lint(md, adm, cid) == []


def test_hand_edit_of_authoritative_section_fails_lint(env):
    adm, cid = env
    md = render(adm, cid).replace("**verified**", "**verified (and novel)**")
    assert lint(md, adm, cid)


def test_commentary_is_allowed_only_after_marker(env):
    adm, cid = env
    md = render(adm, cid, commentary="The code looks elegant.")
    assert COMMENTARY_MARKER in md and lint(md, adm, cid) == []
    assert lint(md + f"\n{COMMENTARY_MARKER}\nmore\n", adm, cid)


def test_report_cites_receipts(env):
    adm, cid = env
    md = render(adm, cid)
    assert "distance_lower_bound" in md and "l2.distance 1.1.0" in md
