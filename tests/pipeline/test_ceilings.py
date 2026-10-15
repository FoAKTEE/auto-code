"""Verifier work runs under enforceable, charged ceilings; identity work is a charged job
(Codex M1 review #13; appendix §K, §L)."""
import json
import time

import numpy as np
import pytest

from zkqec.admission import Admission
from zkqec.contracts import Proposal, StructuralTargets, TaskSA
from zkqec.grammar import GrammarCaps
from zkqec.kernel.codes import bicycle_chain
from zkqec.orchestrator.jobs import JobBook
from zkqec.pipeline import Pipeline, build_registry, evaluator_domain
from zkqec.store import ArtifactStore


def sparse(a):
    a = np.asarray(a)
    return {"shape": list(a.shape), "entries": [[int(i), int(j)] for i, j in zip(*np.nonzero(a))]}


@pytest.fixture
def pipe(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    reg = build_registry(store)
    adm = Admission(tmp_path / "ledger.db", store, reg)
    book = JobBook(adm.ledger.conn, "ceil")
    book.set_caps({"cpu_seconds": 10_000, "wall_seconds": 10_000})
    p = Pipeline(adm, store, book, evaluator_domain(reg), caps=GrammarCaps())
    yield p
    adm.close()


TASK = TaskSA(task_id="sa", targets=StructuralTargets(n_max=50, k_min=6, d_min=6, w_max=8, deg_max=8))


def chain():
    c = bicycle_chain(3, 7)
    return Proposal(family="explicit_sparse", params={"hx": sparse(c.hx), "hz": sparse(c.hz)})


def jobs(pipe):
    return [(k, json.loads(r), json.loads(a), o) for k, r, a, o in pipe.book.conn.execute(
        "SELECT idem_key, reservation, actual, outcome FROM ops_jobs WHERE campaign=? "
        "ORDER BY created_at", (pipe.book.campaign,))]


def test_verifier_jobs_reserve_and_charge_wall_and_cpu(pipe):
    assert pipe.evaluate(chain(), TASK).satisfies == "verified"
    rows = jobs(pipe)
    assert rows and all(r["wall_seconds"] == pipe.wall_reservation > 0 for _, r, _, _ in rows)
    for _, r, charged, _ in rows:
        assert 0 < charged["wall_seconds"] <= r["wall_seconds"]     # measured, not the reservation
        assert 0 <= charged["cpu_seconds"] <= r["cpu_seconds"]


def test_identity_computation_is_a_charged_job(pipe):
    pipe.evaluate(chain(), TASK)
    keys = [k for k, *_ in jobs(pipe)]
    assert any(k.startswith("identity.code_sha:") for k in keys), keys


def test_wall_ceiling_is_enforced_and_yields_no_bound(pipe, monkeypatch):
    import zkqec.verify.distance as dist

    def slow(*a, **k):
        time.sleep(30)
        raise AssertionError("the ceiling did not interrupt the verifier")
    monkeypatch.setattr(dist, "sector_distance", slow)
    pipe.wall_reservation = 1
    t0 = time.monotonic()
    ev = pipe.evaluate(chain(), TASK)
    assert time.monotonic() - t0 < 10
    assert ev.satisfies == "unknown" and ev.facts["d_both"] == [None, None]
    l2 = [r for r in jobs(pipe) if r[0].startswith("l2.distance:")]
    assert len(l2) == 1 and l2[0][3] == "failed" and l2[0][2]["wall_seconds"] >= 1
    closure = pipe.adm.ledger.conn.execute(
        "SELECT c.outcome, c.detail FROM execution_closures c JOIN executions e USING "
        "(execution_id) WHERE e.issuer = 'l2.distance'").fetchone()
    assert closure[0] == "crashed" and json.loads(closure[1])["error_type"] == "wall_timeout"
