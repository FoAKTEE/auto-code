"""Append-only hash-chained ledger, guarded projections, verify and rebuild (prompt §4.2; §L)."""
import hashlib
import json
import sqlite3

import pytest

from admission_helpers import DOMAIN, css, dim, lower, make_env, sha, upper
from zkqec.admission import GENESIS, Admission, Ledger, LedgerBusy, RegistryMismatch, VerifierRegistry
from zkqec.contracts import canonical_json

CODE = sha("code:ledger")


def raw(env):
    """A raw connection that bypasses admission (what a buggy or hostile writer would use)."""
    return sqlite3.connect(env.root / "ledger.db", isolation_level=None)


def populate(env):
    p = env.presentation("P")
    env.admit(css(p), lower(CODE, "Z", 3), upper(CODE, "Z", 5), dim(CODE, 1))
    env.identity(p, CODE)
    rid = env.resolver().status(lower(CODE, "Z", 3).proposition).premises[0]
    env.adm.revoke(rid, "test revocation")
    env.adm.open_incident("orchestrator_note", {"what": "test"})
    return p


def test_chain_hash_formula_and_genesis(env):
    populate(env)
    events = list(env.adm.ledger.events())
    assert events[0].kind == "ledger_opened" and events[0].prev_hash == GENESIS
    prev = GENESIS
    for ev in events:
        expect = hashlib.sha256(bytes.fromhex(prev) + canonical_json(
            {"kind": ev.kind, "payload": ev.payload})).hexdigest()
        assert ev.prev_hash == prev and ev.hash == expect
        prev = ev.hash
    assert env.adm.verify().ok


def test_update_and_delete_on_events_abort(env):
    populate(env)
    conn = raw(env)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE events SET payload = '{}' WHERE seq = 2")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM events WHERE seq = 2")
    with pytest.raises(sqlite3.DatabaseError, match="hash chain"):
        conn.execute("INSERT INTO events VALUES (999, ?, ?, 'revoked', '{}', 'now')",
                     ("0" * 64, "1" * 64))
    conn.close()
    assert env.adm.verify().ok


def test_receipt_rows_cannot_be_inserted_outside_admission(env):
    populate(env)
    conn = raw(env)
    row = conn.execute("SELECT * FROM receipts LIMIT 1").fetchone()
    forged = (sha("forged"), *row[1:])
    with pytest.raises(sqlite3.DatabaseError, match="only by the admission apply path"):
        conn.execute(f"INSERT INTO receipts VALUES ({','.join('?' * len(row))})", forged)
    # even naming a real receipt event through the guard table, the receipt id must match it
    conn.execute("INSERT INTO apply_guard (id, seq) VALUES (1, ?)", (row[1],))
    with pytest.raises(sqlite3.DatabaseError, match="only by the admission apply path"):
        conn.execute(f"INSERT INTO receipts VALUES ({','.join('?' * len(row))})",
                     (sha("forged2"), *row[1:]))
    conn.execute("DELETE FROM apply_guard")
    for stmt in ("UPDATE receipts SET status = 'verified'", "DELETE FROM receipts",
                 "UPDATE revocations SET reason = 'x'", "DELETE FROM executions",
                 "DELETE FROM identities", "DELETE FROM artifact_refs"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(stmt)
    with pytest.raises(sqlite3.DatabaseError, match="only by the admission apply path"):
        conn.execute("INSERT INTO identities VALUES (?,?,?,?,?,?,?,?)",
                     (sha("i"), 1, "e", DOMAIN, sha("p"), sha("c"), "x", "1"))
    conn.close()
    assert env.adm.verify().ok


def test_payload_tamper_with_dropped_triggers_is_detected(env):
    populate(env)
    conn = raw(env)
    conn.execute("DROP TRIGGER events_no_update")
    seq, text = conn.execute("SELECT seq, payload FROM events WHERE kind = 'receipt_admitted' "
                             "LIMIT 1").fetchone()
    payload = json.loads(text)
    payload["receipt"]["status"] = "refuted"
    conn.execute("UPDATE events SET payload = ? WHERE seq = ?",
                 (canonical_json(payload).decode(), seq))
    conn.close()
    report = env.adm.verify()
    assert not report.ok
    joined = " | ".join(report.problems)
    assert "missing trigger events_no_update" in joined
    assert f"event {seq}: hash mismatch" in joined


def test_truncation_and_projection_tamper_are_detected_and_rebuild_repairs(env):
    populate(env)
    before = env.adm.ledger.snapshot()
    conn = raw(env)
    conn.execute("DROP TRIGGER revocations_no_delete")
    conn.execute("DELETE FROM revocations")                 # hide a revocation
    conn.close()
    report = env.adm.verify()
    assert not report.ok and any("projection revocations" in p for p in report.problems)
    env.adm.rebuild()
    assert env.adm.ledger.snapshot() == before
    assert env.adm.verify().ok                               # rebuild recreated the triggers too
    conn = raw(env)
    conn.execute("DROP TRIGGER events_no_delete")
    conn.execute("DELETE FROM events WHERE seq = (SELECT MAX(seq) FROM events)")
    conn.close()
    assert not env.adm.verify().ok                           # projections no longer match events


def test_rebuild_reproduces_projections(env):
    populate(env)
    before = env.adm.ledger.snapshot()
    assert before["receipts"] and before["identities"] and before["revocations"]
    env.adm.rebuild()
    assert env.adm.ledger.snapshot() == before
    assert env.adm.verify().ok


def test_verify_checks_referenced_artifacts(env):
    populate(env)
    path = env.store.path_for(env.seed)
    path.chmod(0o644)
    path.write_bytes(b"tampered")
    report = env.adm.verify()
    assert not report.ok
    assert any(env.seed in p and "IntegrityError" in p for p in report.problems)
    path.unlink()
    assert any("ArtifactNotFound" in p for p in env.adm.verify().problems)


def test_single_writer_lock(env):
    with pytest.raises(LedgerBusy):
        Ledger(env.root / "ledger.db")


def test_reopen_requires_the_same_frozen_registry(tmp_path):
    env = make_env(tmp_path)
    env.adm.close()
    reg = VerifierRegistry()
    reg.register("scripted", "2.0", env.script)              # different version
    reg.register_identity("table_identity", "1.0", env.ids)
    with pytest.raises(RegistryMismatch):
        Admission(tmp_path / "ledger.db", env.store, reg)
    same = VerifierRegistry()
    same.register("scripted", "1.0", env.script)
    same.register_identity("table_identity", "1.0", env.ids)
    Admission(tmp_path / "ledger.db", env.store, same).close()
