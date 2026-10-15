"""Forged receipts must not survive ledger verification, the resolver or the offline replay
(Codex M1 review #1; prompt §4.1–4.2; contracts §A2).

The reproductions use the ledger's internal write path (as a buggy trusted component would) and a
raw SQLite connection (as a third-party tamper would). Agents have no access to either."""
import json
import sqlite3

import pytest

from admission_helpers import DOMAIN, dim, sha
from zkqec.admission import LedgerError
from zkqec.admission.ledger import event_hash
from zkqec.admission.service import _stamp
from zkqec.contracts import canonical_json, content_hash

CODE = sha("code:forgery")


def _open_payload(env, issuer="scripted", version="1.0", role="verifier", eid=None):
    return {"execution_id": eid or sha(f"forged:{issuer}"), "role": role, "issuer": issuer,
            "issuer_version": version, "domain": DOMAIN, "inputs": {"seed": env.seed}}


def _receipt_payload(ex, k=999):
    r = _stamp(ex, dim(CODE, k))
    return {"receipt_id": content_hash(r), "receipt": r.model_dump(mode="json")}


def _internal_append(env, kind, payload):
    with env.adm.ledger.transaction():
        env.adm.ledger.append(kind, payload, writer=env.adm._writer)


def test_public_append_is_refused_outside_admission(env):
    with pytest.raises(LedgerError, match="admission"):
        with env.adm.ledger.transaction():
            env.adm.ledger.append("execution_opened", _open_payload(env, issuer="forger"))
    assert "execution_opened" not in env.event_kinds()


def test_unregistered_issuer_and_role_are_rejected_at_apply(env):
    for kw in ({"issuer": "forger"}, {"version": "9.9"}, {"role": "identity"}):
        with pytest.raises(LedgerError, match="registry manifest"):
            _internal_append(env, "execution_opened", _open_payload(env, **kw))
    assert "execution_opened" not in env.event_kinds()


def test_receipt_of_an_open_execution_carries_no_authority(env):
    _internal_append(env, "execution_opened", _open_payload(env))
    ex = env.adm.ledger.execution(sha("forged:scripted"))
    _internal_append(env, "receipt_admitted", _receipt_payload(ex))
    prop = dim(CODE, 999).proposition
    assert env.resolver().status(prop).status == "unknown"        # no admitted closure
    problems = " | ".join(env.adm.verify().problems)
    assert "open" in problems and "without an admitted closure" in problems


def test_closure_must_equal_the_executions_receipts_and_nothing_follows_it(env):
    rep = env.admit(dim(CODE, 2))
    ex = env.adm.ledger.execution(rep.execution_id)
    with pytest.raises(LedgerError, match="after its closure"):
        _internal_append(env, "receipt_admitted", _receipt_payload(ex))
    # a closure naming records that are not exactly the execution's receipts is refused
    _internal_append(env, "execution_opened", _open_payload(env, eid=sha("e2")))
    ex2 = env.adm.ledger.execution(sha("e2"))
    _internal_append(env, "receipt_admitted", _receipt_payload(ex2, k=5))
    for records in ([], [sha("other")]):
        with pytest.raises(LedgerError, match="exactly"):
            _internal_append(env, "execution_closed", {
                "execution_id": sha("e2"), "outcome": "admitted", "completion": None,
                "records": records, "detail": {}})
    with pytest.raises(LedgerError, match="exactly"):
        _internal_append(env, "execution_closed", {
            "execution_id": sha("e2"), "outcome": "crashed", "completion": "crashed",
            "records": [], "detail": {}})
    # the receipt input hashes must be the execution's inputs
    _internal_append(env, "execution_opened", _open_payload(env, eid=sha("e3")))
    ex3 = dict(env.adm.ledger.execution(sha("e3")))
    ex3["inputs"] = {"seed": sha("not-an-input")}
    with pytest.raises(LedgerError, match="input"):
        _internal_append(env, "receipt_admitted", _receipt_payload(ex3))


def _raw_append(env, kind, payload):
    conn = sqlite3.connect(env.root / "ledger.db", isolation_level=None)
    seq, prev = conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    payload = json.loads(canonical_json(payload))
    conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                 (seq + 1, prev, event_hash(prev, kind, payload), kind,
                  canonical_json(payload).decode(), "now"))
    conn.close()


def test_raw_chain_extensions_with_forged_receipts_fail_verification(env):
    rep = env.admit(dim(CODE, 2))
    ex = env.adm.ledger.execution(rep.execution_id)
    _raw_append(env, "receipt_admitted", _receipt_payload(ex))       # extra receipt after closure
    with pytest.raises(LedgerError):
        env.adm.rebuild()                       # re-applying the chain enforces the invariants
    report = env.adm.verify()
    assert not report.ok and any("cannot be re-applied" in p for p in report.problems)


def test_raw_unregistered_execution_fails_verification(env):
    _raw_append(env, "execution_opened", _open_payload(env, issuer="forger"))
    with pytest.raises(LedgerError):
        env.adm.rebuild()
    report = env.adm.verify()
    assert not report.ok and any("registry manifest" in p for p in report.problems)


def test_consistent_forged_closure_is_caught_by_replay(env):
    from zkqec.report.bundle import replay_executions
    _internal_append(env, "execution_opened", _open_payload(env))
    ex = env.adm.ledger.execution(sha("forged:scripted"))
    payload = _receipt_payload(ex)
    _internal_append(env, "receipt_admitted", payload)
    _internal_append(env, "execution_closed", {
        "execution_id": ex["execution_id"], "outcome": "admitted", "completion": None,
        "records": [payload["receipt_id"]], "detail": {}})
    assert env.adm.verify().ok                      # internally consistent: chain and apply rules
    problems, replayed, _ = replay_executions(env.adm, env.adm.registry, env.store)
    assert replayed == 1 and any("different receipts" in p for p in problems)
