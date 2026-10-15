"""Job book: reservations, dispatch intent, fencing, settlement, crash recovery (appendix §K, §L)."""
import hashlib

import pytest

from zkqec.admission import Admission, VerifierRegistry
from zkqec.admission.ledger import Ledger
from zkqec.contracts import Proposition, Reservation, VerifierResult
from zkqec.orchestrator.jobs import (BudgetExceeded, DuplicateCompletion, IllegalTransition, JobBook,
                                     StaleCompletion)
from zkqec.store import ArtifactStore

R = Reservation(usd=1.0, requests=2, input_tokens=1000, output_tokens=500, wall_seconds=60,
                cpu_seconds=0)
CAPS = {"usd": 2.5, "requests": 10, "input_tokens": 10_000, "output_tokens": 10_000,
        "wall_seconds": 1000, "cpu_seconds": 1000}


@pytest.fixture
def book(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    b = JobBook(led.conn, "camp")
    b.set_caps(CAPS)
    yield b
    led.close()


def test_reserve_is_idempotent_and_budget_checked(book):
    a = book.reserve("propose", "k1", R)
    assert book.reserve("propose", "k1", R) == a
    book.reserve("propose", "k2", R)
    with pytest.raises(BudgetExceeded):          # 3 x $1 > $2.5 (open reservations count)
        book.reserve("propose", "k3", R)


def test_caps_are_frozen(book):
    with pytest.raises(Exception):
        book.conn.execute("UPDATE ops_caps SET cap=100 WHERE unit='usd'")


def test_happy_path_charges_actual_usage(book):
    j = book.reserve("propose", "k", R)
    g = book.dispatch(j)
    book.complete(j, g, "completed", actual={"usd": 0.25, "requests": 1})
    charged = book.settle(j)
    assert charged["usd"] == 0.25 and book.totals()["usd"]["spent"] == 0.25
    assert book.audit() == []


def test_stale_and_duplicate_completions_rejected(book):
    j = book.reserve("propose", "k", R)
    g1 = book.dispatch(j)
    book.expire(j, g1, killed=False)             # lease expired; old execution uncertain
    with pytest.raises(StaleCompletion):
        book.complete(j, g1, "completed", actual={"usd": 0.1})
    j2 = book.reserve("propose", "k-retry", R, retry_of=j)   # retry = new job, own reservation
    g2 = book.dispatch(j2)
    book.complete(j2, g2, "completed", actual={"usd": 0.1})
    with pytest.raises(DuplicateCompletion):
        book.complete(j2, g2, "completed", actual={"usd": 0.1})
    with pytest.raises(StaleCompletion):
        book.complete(j2, g2 + 1, "completed")


def test_uncertain_keeps_full_reservation(book):
    j = book.reserve("propose", "k", R)
    g = book.dispatch(j)
    book.complete(j, g, "uncertain")
    assert book.settle(j)["usd"] == 1.0


def test_crash_after_dispatch_recovers_as_uncertain(tmp_path):
    led = Ledger(tmp_path / "l.db")
    b = JobBook(led.conn, "c"); b.set_caps(CAPS)
    j = b.reserve("propose", "k", R); b.dispatch(j)
    led.close()                                   # process "crashes" with the CLI possibly running
    led2 = Ledger(tmp_path / "l.db")
    b2 = JobBook(led2.conn, "c")
    assert b2.recover() == [j] and b2.state(j) == "uncertain"
    assert b2.settle(j)["usd"] == 1.0             # a kill never releases an unresolved charge
    assert b2.totals()["usd"]["spent"] == 1.0 and b2.audit() == []
    led2.close()


def test_illegal_transitions(book):
    j = book.reserve("verify", "k", R)
    with pytest.raises(IllegalTransition):
        book.settle(j)                            # reserved -> settled only via cancel
    book.cancel(j)
    assert book.totals()["usd"]["spent"] == 0.0
    with pytest.raises(IllegalTransition):
        book.dispatch(j)


def test_overrun_charged_and_flagged(book):
    j = book.reserve("propose", "k", R)
    g = book.dispatch(j)
    book.complete(j, g, "completed", actual={"usd": 1.7})
    assert book.settle(j)["usd"] == 1.7
    assert book.conn.execute("SELECT overrun FROM ops_jobs WHERE job_id=?", (j,)).fetchone()[0] == 1


def test_ops_log_is_append_only(book):
    book.reserve("propose", "k", R)
    with pytest.raises(Exception):
        book.conn.execute("DELETE FROM ops_log")


# --- atomic settlement with admission ------------------------------------------------------------

SUBJ = hashlib.sha256(b"code").hexdigest()
DOMAIN = hashlib.sha256(b"domain").hexdigest()


def _verifier(**inputs):
    return [VerifierResult(proposition=Proposition(predicate="dimension_eq", subject_type="code",
                                                   subject=SUBJ, args={"k": 2}),
                           status="verified", evidence_kind="exact_algebra", method="t",
                           completion="completed")]


def _env(tmp_path, fn=_verifier):
    store = ArtifactStore(tmp_path / "store")
    reg = VerifierRegistry(); reg.register("v", "1.0", fn)
    adm = Admission(tmp_path / "ledger.db", store, reg)
    book = JobBook(adm.ledger.conn, "c"); book.set_caps(CAPS)
    return adm, book, store.put_json({"x": 1})


def test_settlement_commits_atomically_with_admission(tmp_path):
    adm, book, seed = _env(tmp_path)
    j = book.reserve("verify", "v1", R); g = book.dispatch(j)

    def on_close(outcome):
        book.complete(j, g, "completed" if outcome == "admitted" else "failed", actual={"cpu_seconds": 1})
        book.settle(j)
    rep = adm.run_verifier("v", {"seed": seed}, DOMAIN, on_close=on_close)
    assert rep.outcome == "admitted" and book.state(j) == "settled"
    adm.close()


def test_failing_settlement_rolls_back_the_admission(tmp_path):
    adm, book, seed = _env(tmp_path)
    j = book.reserve("verify", "v1", R); g = book.dispatch(j)

    def on_close(outcome):
        book.complete(j, g + 7, "completed")       # stale generation -> raises inside the tx
    with pytest.raises(StaleCompletion):
        adm.run_verifier("v", {"seed": seed}, DOMAIN, on_close=on_close)
    assert not list(adm.ledger.conn.execute("SELECT 1 FROM receipts")) if \
        adm.ledger.conn.execute("SELECT name FROM sqlite_master WHERE name='receipts'").fetchone() else True
    assert book.state(j) == "dispatched"          # nothing settled; recovery will mark uncertain
    adm.close()
