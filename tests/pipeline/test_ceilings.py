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
    assert closure[0] == "crashed" and json.loads(closure[1])["error_type"] == "wall_exceeded"


# --- Codex M1 milestone review B5: verifier and identity work runs in a bounded child --------------
def l2_closure(pipe):
    row = pipe.adm.ledger.conn.execute(
        "SELECT c.outcome, c.detail FROM execution_closures c JOIN executions e USING "
        "(execution_id) WHERE e.issuer = 'l2.distance'").fetchone()
    return row[0], json.loads(row[1])


def distance_receipts(pipe):
    return pipe.adm.ledger.conn.execute(
        "SELECT COUNT(*) FROM receipts WHERE predicate LIKE 'distance_%'").fetchone()[0]


def test_nested_l2_deadline_cannot_extend_the_wall_reservation(pipe, monkeypatch):
    """Codex's repro: a 1 s verifier reservation, distance_budget.max_seconds = 5 and a slowed real
    sector calculation used to finish after ~2.4 s and admit verified bounds."""
    import zkqec.verify.distance as dist
    real = dist.sector_distance

    def slowed(*a, **k):
        time.sleep(1.2)
        return real(*a, **k)
    monkeypatch.setattr(dist, "sector_distance", slowed)
    pipe.wall_reservation = 1
    pipe.distance_budget = {"max_seconds": 5}
    ev = pipe.evaluate(chain(), TASK)
    assert ev.satisfies == "unknown" and ev.facts["d_both"] == [None, None]
    assert distance_receipts(pipe) == 0
    outcome, detail = l2_closure(pipe)
    assert outcome == "crashed" and detail["error_type"] == "wall_exceeded"
    l2 = [r for r in jobs(pipe) if r[0].startswith("l2.distance:")]
    assert len(l2) == 1 and l2[0][3] == "failed" and 1 <= l2[0][2]["wall_seconds"] < 3


def _spin(seconds):
    t0 = time.process_time()
    while time.process_time() - t0 < seconds:
        pass


def test_cpu_exhaustion_is_stopped_and_charged_as_measured(pipe, monkeypatch):
    import zkqec.verify.distance as dist
    real = dist.sector_distance

    def burn(*a, **k):
        _spin(2.5)
        return real(*a, **k)
    monkeypatch.setattr(dist, "sector_distance", burn)
    pipe.cpu_reservation = 1
    t0 = time.monotonic()
    ev = pipe.evaluate(chain(), TASK)
    assert time.monotonic() - t0 < 10
    assert ev.satisfies == "unknown" and distance_receipts(pipe) == 0
    outcome, detail = l2_closure(pipe)
    assert outcome == "crashed" and detail["error_type"] == "cpu_exceeded"
    [l2] = [r for r in jobs(pipe) if r[0].startswith("l2.distance:")]
    assert l2[3] == "failed" and 0.9 <= l2[2]["cpu_seconds"] < 4      # measured, child included


def test_memory_exhaustion_is_refused_and_is_no_evidence(pipe, monkeypatch):
    import zkqec.pipeline as pl
    real = pl.certify_distance

    def hog(*a, **k):
        scratch = np.empty(600 * 2**20, dtype=np.uint8)   # beyond 16 MiB + the declared slack
        del scratch
        return real(*a, **k)
    monkeypatch.setattr(pl, "certify_distance", hog)
    pipe.distance_budget = {"max_memory_bytes": 16 * 2**20}
    ev = pipe.evaluate(chain(), TASK)
    assert ev.satisfies == "unknown" and distance_receipts(pipe) == 0
    outcome, detail = l2_closure(pipe)
    assert outcome == "crashed" and detail["error_type"] == "memory_exceeded"
    assert detail["usage"]["peak_rss_bytes"] > 0
    [l2] = [r for r in jobs(pipe) if r[0].startswith("l2.distance:")]
    assert l2[3] == "failed"


@pytest.mark.parametrize("target", ["analyze", "code_sha", "certify_distance"])
def test_a_hung_verifier_or_identity_is_killed_at_its_wall_reservation(pipe, monkeypatch, target):
    """A verifier that cannot be interrupted by an in-process SIGALRM (signal blocked, as in C code)
    is still stopped at its wall reservation: L1, identity and L2 all run in a bounded child."""
    import signal

    import zkqec.pipeline as pl

    def hung(*a, **k):
        signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGALRM])
        try:
            time.sleep(12)
        finally:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, [signal.SIGALRM])
        raise AssertionError("not stopped")
    monkeypatch.setattr(pl, target, hung)
    pipe.wall_reservation = 1
    t0 = time.monotonic()
    ev = pipe.evaluate(chain(), TASK)
    assert time.monotonic() - t0 < 8
    assert ev.satisfies == "unknown"
    detail = [json.loads(d) for d, in pipe.adm.ledger.conn.execute(
        "SELECT detail FROM execution_closures WHERE outcome = 'crashed'")]
    assert [d["error_type"] for d in detail] == ["wall_exceeded"]
