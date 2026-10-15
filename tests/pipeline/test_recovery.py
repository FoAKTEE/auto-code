"""Recovery never re-runs verification without a new reservation and a fenced settlement
(Codex M1 review #2; appendix §L "replay and recovery")."""
import pytest

from zkqec.admission import Admission
from zkqec.grammar import GrammarCaps
from zkqec.grammar.matrices import from_dense
from zkqec.kernel.codes import steane
from zkqec.orchestrator.jobs import JobBook
from zkqec.pipeline import Pipeline, build_registry, evaluator_domain
from zkqec.store import ArtifactStore


@pytest.fixture
def pipe(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    reg = build_registry(store)
    adm = Admission(tmp_path / "ledger.db", store, reg)
    book = JobBook(adm.ledger.conn, "recovery")
    book.set_caps({"cpu_seconds": 10_000})
    p = Pipeline(adm, store, book, evaluator_domain(reg), caps=GrammarCaps())
    yield p
    adm.close()


def _steane(store):
    c = steane()
    return store.put_json(from_dense(c.hx, c.hz).model_dump(mode="json"))


def _jobs(pipe):
    return pipe.book.conn.execute("SELECT job_id, state, retry_of FROM ops_jobs WHERE campaign=? "
                                  "ORDER BY created_at", (pipe.book.campaign,)).fetchall()


def test_settled_job_without_closure_retries_under_a_new_fenced_job(pipe, monkeypatch):
    p_sha = _steane(pipe.store)
    inputs, key = {"presentation": p_sha}, f"l1:{p_sha}"

    def crash(job_id):
        raise RuntimeError("injected crash inside the settlement")
    monkeypatch.setattr(pipe.book, "settle", crash)
    with pytest.raises(RuntimeError):
        pipe._run("l1.css_algebra", inputs, key)
    monkeypatch.undo()
    [(job, state, _)] = _jobs(pipe)
    assert state == "dispatched"                         # the closing transaction rolled back
    assert pipe.book.recover() == [job]
    pipe.book.settle(job)                                 # uncertain: full reservation retained
    spent_before = pipe.book.totals()["cpu_seconds"]["spent"]
    assert spent_before == pipe.cpu_reservation

    rep = pipe._run("l1.css_algebra", inputs, key)
    assert rep.outcome == "admitted" and not rep.replayed
    rows = _jobs(pipe)
    assert len(rows) == 2 and rows[1][2] == job            # a new job, retry_of the first
    assert all(r[1] == "settled" for r in rows)            # fenced settlement of the retry
    assert pipe.book.totals()["cpu_seconds"]["spent"] > spent_before
    # the first execution is closed (abandoned), the retry's execution is admitted
    closures = pipe.adm.ledger.conn.execute(
        "SELECT outcome FROM execution_closures ORDER BY event_seq").fetchall()
    assert [c[0] for c in closures] == ["crashed", "admitted"]
    assert pipe.adm.verify().ok

    again = pipe._run("l1.css_algebra", inputs, key)       # settled + closed: read-only
    assert again.replayed and again.records == rep.records and len(_jobs(pipe)) == 2


def _other_profile(pipe, profile="other"):
    """A second profile of the same campaign sharing the ledger and the store (§N namespace)."""
    book = JobBook(pipe.adm.ledger.conn, "recovery", profile=profile)
    book.set_caps({"cpu_seconds": 10_000})
    return Pipeline(pipe.adm, pipe.store, book, pipe.domain, caps=GrammarCaps())


def test_profiles_sharing_a_ledger_each_run_and_pay_for_their_own_executions(pipe):
    """Codex M1 milestone review B4: a closure from another namespace is never reused (no zero-cost
    cancel + replay); each profile runs, is charged and gets its own execution and receipts."""
    p_sha = _steane(pipe.store)
    inputs, key = {"presentation": p_sha}, f"l1:{p_sha}"
    first = pipe._run("l1.css_algebra", inputs, key)
    other = _other_profile(pipe)
    rep = other._run("l1.css_algebra", inputs, key)
    assert rep.outcome == "admitted" and not rep.replayed
    assert rep.execution_id != first.execution_id and set(rep.records).isdisjoint(first.records)
    rows = other.book.conn.execute("SELECT state, outcome FROM ops_jobs WHERE campaign=?",
                                   (other.book.campaign,)).fetchall()
    assert rows == [("settled", "completed")]
    assert other.book.totals()["cpu_seconds"]["spent"] > 0
    assert pipe.book.totals()["cpu_seconds"]["spent"] > 0
    namespaces = [ns for ns, in pipe.adm.ledger.conn.execute(
        "SELECT namespace FROM executions ORDER BY event_seq")]
    assert namespaces == [pipe.book.namespace, other.book.namespace]
    again = other._run("l1.css_algebra", inputs, key)          # within a namespace: read only
    assert again.replayed and again.records == rep.records
    assert pipe.adm.verify().ok


def test_evidence_of_another_profile_never_authorizes(pipe, monkeypatch):
    """Profile B cannot satisfy a task with profile A's distance evidence: its own L2 failed."""
    from test_ceilings import TASK, chain
    assert pipe.evaluate(chain(), TASK).satisfies == "verified"
    import zkqec.verify.distance as dist

    def broken(*a, **k):
        raise RuntimeError("B's L2 fails")
    monkeypatch.setattr(dist, "sector_distance", broken)
    other = _other_profile(pipe)
    ev = other.evaluate(chain(), TASK)
    assert ev.satisfies == "unknown" and ev.facts["d_both"] == [None, None]
    kinds = [k for k, in other.book.conn.execute(
        "SELECT idem_key FROM ops_jobs WHERE campaign=? AND outcome != 'cancelled'",
        (other.book.campaign,))]
    assert {k.split(":")[0] for k in kinds} == {"l1.css_algebra", "identity.code_sha",
                                                  "l2.distance"}       # B ran each of its own
