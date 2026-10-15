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


# --- Codex M1 review #5: partial usage must not refund; NaN must not disable caps -------------------
def test_partial_usage_does_not_refund_unmeasured_units(book):
    j = book.reserve("propose", "k", R)
    g = book.dispatch(j)
    book.complete(j, g, "failed", actual={"requests": 1})
    charged = book.settle(j)
    assert charged["usd"] == 1.0                      # unmeasured: the reservation is retained
    assert charged["requests"] == 1.0 and charged["output_tokens"] == 500


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, True, "0.5", None])
def test_non_finite_or_untyped_usage_is_rejected(book, bad):
    from zkqec.orchestrator.jobs import JobError
    j = book.reserve("propose", "k", R)
    g = book.dispatch(j)
    with pytest.raises(JobError):
        book.complete(j, g, "completed", actual={"usd": bad})
    assert book.state(j) == "dispatched"
    book.complete(j, g, "completed", actual={"usd": 0.5})
    book.settle(j)
    assert book.totals()["usd"]["spent"] == 0.5


def test_nan_cannot_open_room_beyond_the_cap(book):
    j = book.reserve("propose", "k", R)
    g = book.dispatch(j)
    with pytest.raises(Exception):
        book.complete(j, g, "completed", actual={"usd": float("nan")})
    book.complete(j, g, "uncertain")
    book.settle(j)
    book.reserve("propose", "k2", R)
    with pytest.raises(BudgetExceeded):
        book.reserve("propose", "k3", R)              # 1 spent + 1 open + 1 > 2.5


def test_audit_flags_non_finite_charges(book):
    j = book.reserve("propose", "k", R)
    g = book.dispatch(j)
    book.complete(j, g, "completed", actual={"usd": 0.5})
    book.settle(j)
    book.conn.execute("UPDATE ops_jobs SET actual = ? WHERE job_id = ?",
                      ('{"usd": NaN}', j))
    assert any("finite" in p for p in book.audit())


# --- Codex M1 review #6: ownership and idempotency are namespace-scoped ----------------------------
def test_a_job_is_invisible_to_other_campaigns(tmp_path):
    from zkqec.orchestrator.jobs import JobError
    led = Ledger(tmp_path / "l.db")
    a = JobBook(led.conn, "camp-a"); a.set_caps(CAPS)
    b = JobBook(led.conn, "camp-b"); b.set_caps({"usd": 0.0})
    ja = a.reserve("propose", "same-key", R)
    with pytest.raises(BudgetExceeded):             # B cannot ride on A's reservation
        b.reserve("propose", "same-key", R)
    for call in (lambda: b.dispatch(ja), lambda: b.state(ja), lambda: b.cancel(ja)):
        with pytest.raises(JobError):
            call()
    ga = a.dispatch(ja)
    for call in (lambda: b.complete(ja, ga, "completed", actual={"usd": 0.0}),
                 lambda: b.expire(ja, ga, killed=True), lambda: b.settle(ja)):
        with pytest.raises(JobError):
            call()
    assert a.state(ja) == "dispatched" and b.recover() == []
    c = JobBook(led.conn, "camp-c"); c.set_caps(CAPS)
    assert c.reserve("propose", "same-key", R) != ja      # same key, independent namespace
    led.close()


def test_namespace_includes_arm_split_and_profile(tmp_path):
    led = Ledger(tmp_path / "l.db")
    x = JobBook(led.conn, "camp", arm="zero", split="dev", profile="K0.P-min.G-open")
    y = JobBook(led.conn, "camp", arm="b1", split="dev", profile="K0.P-min.G-open")
    x.set_caps(CAPS); y.set_caps(CAPS)
    assert x.reserve("propose", "k", R) != y.reserve("propose", "k", R)
    assert x.namespace != y.namespace and x.totals()["usd"]["open"] == 1.0
    led.close()


def test_idempotent_reserve_rejects_a_different_request(book):
    from zkqec.orchestrator.jobs import JobError
    j = book.reserve("propose", "k", R, input_sha="a" * 64)
    assert book.reserve("propose", "k", R, input_sha="a" * 64) == j
    other = Reservation(usd=0.5, requests=1, input_tokens=1, output_tokens=1, wall_seconds=1,
                        cpu_seconds=0)
    for kw in ({"kind": "verify"}, {"input_sha": "b" * 64}, {"reservation": other}):
        args = {"kind": "propose", "reservation": R, "input_sha": "a" * 64, **kw}
        with pytest.raises(JobError, match="idempotency"):
            book.reserve(args["kind"], "k", args["reservation"], input_sha=args["input_sha"])


def test_retry_ownership_is_checked(tmp_path):
    from zkqec.orchestrator.jobs import JobError
    led = Ledger(tmp_path / "l.db")
    a = JobBook(led.conn, "camp-a"); a.set_caps(CAPS)
    b = JobBook(led.conn, "camp-b"); b.set_caps(CAPS)
    ja = a.reserve("propose", "k", R)
    with pytest.raises(JobError):
        b.reserve("propose", "k-retry", R, retry_of=ja)       # another campaign's job
    with pytest.raises(JobError):
        a.reserve("verify", "k-retry", R, retry_of=ja)        # a different kind
    with pytest.raises(JobError):
        a.reserve("propose", "k-retry", R, retry_of=ja)       # still reserved: nothing to retry
    a.cancel(ja)
    assert a.reserve("propose", "k-retry", R, retry_of=ja)
    led.close()
