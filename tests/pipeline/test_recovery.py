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


def test_closed_execution_is_never_rerun_for_a_fresh_job(pipe):
    p_sha = _steane(pipe.store)
    inputs, key = {"presentation": p_sha}, f"l1:{p_sha}"
    first = pipe._run("l1.css_algebra", inputs, key)
    other = JobBook(pipe.adm.ledger.conn, "recovery", profile="other")   # same ledger, new namespace
    other.set_caps({"cpu_seconds": 10_000})
    pipe.book = other
    rep = pipe._run("l1.css_algebra", inputs, key)
    assert rep.replayed and rep.records == first.records
    rows = other.conn.execute("SELECT state FROM ops_jobs WHERE campaign=?",
                              (other.campaign,)).fetchall()
    assert rows == [("settled",)] and other.totals()["cpu_seconds"]["spent"] == 0
