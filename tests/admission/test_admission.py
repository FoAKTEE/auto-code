"""Receipts only via admission; issuer from the execution record; crashes; stale artifacts;
durable publication before reference; idempotency; identity records (§A2, §L, §M)."""
import pytest
from pydantic import ValidationError

from admission_helpers import DOMAIN, css, lower, prop, sha, vr
from zkqec.admission import AdmissionRefused, ArtifactInput, DuplicateCompletion, RegistryError
from zkqec.admission.service import _revalidate_results
from zkqec.contracts import VerifierResult
from zkqec.store import artifacts as artifacts_mod

CODE = sha("code:adm")


def receipts(env):
    return env.adm.ledger.receipt_records()


def test_receipt_fields_come_from_the_execution_record(env):
    report = env.admit(lower(CODE, "Z", 3))
    assert report.outcome == "admitted" and len(report.records) == 1
    (rec,) = receipts(env)
    r = rec.receipt
    assert (r.issuer, r.issuer_version, r.domain) == ("scripted", "1.0", DOMAIN)
    assert r.execution_id == report.execution_id and r.input_hashes == (env.seed,)
    kinds = env.event_kinds()
    assert kinds.index("execution_opened") < kinds.index("receipt_admitted") \
        < kinds.index("execution_closed")
    # the verifier saw hash-verified input bytes, not a path it could be fooled by
    (call,) = env.script.calls
    assert isinstance(call["seed"], ArtifactInput) and call["seed"].sha == env.seed
    assert call["seed"].json() == {"seed": "input"}


def test_a_verifier_result_cannot_name_its_issuer(env):
    with pytest.raises(ValidationError):
        VerifierResult(proposition=prop("css_valid", "presentation", sha("p")), status="verified",
                       evidence_kind="exact_algebra", method="m", completion="completed",
                       issuer="trusted-verifier")
    forged = VerifierResult.model_construct(**lower(CODE, "Z", 3).__dict__, issuer="evil",
                                            issuer_version="9", execution_id="x")
    env.admit(forged)
    (rec,) = receipts(env)
    assert rec.receipt.issuer == "scripted" and rec.receipt.issuer_version == "1.0"


def test_model_construct_cannot_smuggle_authority(env):
    bad = VerifierResult.model_construct(**{**lower(CODE, "Z", 9).__dict__,
                                            "evidence_kind": "agent_assertion"})
    report = env.admit(bad)
    assert report.outcome == "crashed" and report.detail["error_type"] == "malformed_result"
    assert receipts(env) == []
    assert env.resolver().status(lower(CODE, "Z", 9).proposition).status == "unknown"


def test_only_registered_issuers_and_frozen_registry(env):
    with pytest.raises(RegistryError):
        env.adm.run_verifier("not-registered", {"x": env.seed}, DOMAIN)
    with pytest.raises(RegistryError):
        env.adm.registry.register("late", "1", lambda **kw: [])
    with pytest.raises(RegistryError):          # identity fn cannot be used as a verifier
        env.adm.run_verifier("table_identity", {"x": env.seed}, DOMAIN)
    assert not hasattr(env.adm, "record_identity")


def test_crash_yields_crashed_closure_and_unknown(env):
    def explode(**inputs):
        raise RuntimeError("boom with Authorization: Bearer SYNTHETICtokenVALUE123")
    env.script.action = explode
    report = env.admit()
    assert report.outcome == "crashed"
    closure = env.adm.ledger.closure(report.execution_id)
    assert closure["completion"] == "crashed" and closure["outcome"] == "crashed"
    assert "SYNTHETICtokenVALUE123" not in closure["detail"]["message"]
    assert "receipt_admitted" not in env.event_kinds()
    assert env.resolver().status(lower(CODE, "Z", 3).proposition).status == "unknown"
    env.script.action = lambda **kw: "not a list"
    assert env.admit().outcome == "crashed"


def test_artifact_changed_between_run_and_commit_is_refused(env):
    def tamper(**inputs):
        path = env.store.path_for(env.seed)
        path.chmod(0o644)
        path.write_bytes(b'{"seed":"changed"}')
        return [lower(CODE, "Z", 3)]
    env.script.action = tamper
    with pytest.raises(AdmissionRefused) as info:
        env.admit()
    assert "receipt_admitted" not in env.event_kinds()
    rep = info.value.report
    assert rep.outcome == "refused" and rep.incidents
    assert env.adm.ledger.closure(rep.execution_id)["outcome"] == "refused"
    assert env.adm.ledger.conn.execute("SELECT category FROM incidents").fetchone()[0] \
        == "artifact_integrity"


def test_unpublished_witness_is_never_referenced(env, monkeypatch):
    def crash(*a):
        raise OSError("injected crash before rename")
    monkeypatch.setattr(artifacts_mod, "_replace", crash)
    witness_obj = {"logical": [1, 0, 1]}
    with pytest.raises(OSError):
        env.store.put_json(witness_obj)
    monkeypatch.undo()
    from zkqec.contracts import content_hash
    wsha = content_hash(witness_obj)
    with pytest.raises(AdmissionRefused, match="not published"):
        env.admit(vr("distance_upper_bound", "code", CODE, sector="Z", d=3, witnesses=[wsha]))
    assert "receipt_admitted" not in env.event_kinds()


def test_dir_fsync_failure_blocks_admission_until_durable(env, monkeypatch):
    witness_obj = {"logical": [0, 1, 1]}
    from zkqec.contracts import content_hash
    wsha = content_hash(witness_obj)
    env.store.path_for(wsha).parent.mkdir(parents=True, exist_ok=True)

    real = artifacts_mod._fsync_dir
    witness_dir = str(env.store.path_for(wsha).parent)

    def crash(path):
        if path == witness_dir:
            raise OSError("injected crash at directory fsync")
        return real(path)
    monkeypatch.setattr(artifacts_mod, "_fsync_dir", crash)
    with pytest.raises(OSError):
        env.store.put_json(witness_obj)
    assert env.store.exists(wsha)                         # renamed but not durably published
    with pytest.raises(AdmissionRefused, match="durable"):
        env.admit(vr("distance_upper_bound", "code", CODE, sector="Z", d=3, witnesses=[wsha]))
    assert "receipt_admitted" not in env.event_kinds()
    monkeypatch.undo()
    report = env.admit(vr("distance_upper_bound", "code", CODE, sector="Z", d=3,
                          witnesses=[wsha]))
    assert report.outcome == "admitted"


def test_idempotent_readmission_and_duplicate_completion(env):
    report = env.admit(lower(CODE, "Z", 3), lower(CODE, "Z", 3))      # duplicate in one batch
    assert len(report.records) == 1
    n_events = len(env.event_kinds())
    ex = env.adm.ledger.execution(report.execution_id)
    again = env.adm._complete(ex, [lower(CODE, "Z", 3)])               # identical: no-op
    assert again.replayed and again.records == report.records
    assert len(env.event_kinds()) == n_events
    with pytest.raises(DuplicateCompletion):
        env.adm._complete(ex, [lower(CODE, "Z", 4)])
    with pytest.raises(DuplicateCompletion):
        env.adm._close_crashed(ex, "late", "late crash")
    assert len(env.event_kinds()) == n_events


def test_idempotency_key_replays_without_rerunning(env):
    first = env.admit(lower(CODE, "X", 3), idempotency_key="job-1")
    calls = len(env.script.calls)
    second = env.admit(lower(CODE, "X", 3), idempotency_key="job-1")
    assert second.replayed and second.execution_id == first.execution_id
    assert second.records == first.records and len(env.script.calls) == calls


def test_presentation_subject_must_be_an_input(env):
    p = env.presentation("P")
    with pytest.raises(AdmissionRefused, match="not an input"):
        env.admit(css(p), inputs={"seed": env.seed})
    assert "receipt_admitted" not in env.event_kinds()


def test_unknown_completion_is_recorded_but_never_authorizes(env):
    env.admit(vr("distance_lower_bound", "code", CODE, "unknown", completion="timeout",
                 sector="Z", d=9),
              vr("distance_lower_bound", "code", CODE, "unknown", kind="solver_claim",
                 sector="Z", d=9))
    assert len(receipts(env)) == 2
    assert env.resolver().status(lower(CODE, "Z", 9).proposition).status == "unknown"


def test_revoke_semantics(env):
    report = env.admit(lower(CODE, "Z", 3))
    rid = report.records[0]
    with pytest.raises(KeyError):
        env.adm.revoke(sha("nothing"), "x")
    with pytest.raises(ValueError):
        env.adm.revoke(rid, "")
    assert env.adm.revoke(rid, "bad verifier build") is True
    assert env.adm.revoke(rid, "again") is False
    assert env.resolver().status(lower(CODE, "Z", 3).proposition).status == "unknown"


def test_identity_records_come_only_from_a_registered_function(env):
    p = env.presentation("P")
    rep = env.identity(p, CODE)
    assert rep.outcome == "admitted"
    (idrec,) = env.adm.ledger.identity_records()
    assert (idrec.presentation, idrec.code, idrec.issuer) == (p, CODE, "table_identity")
    assert idrec.execution_id == rep.execution_id
    assert env.resolver().code_of(p) == (CODE, idrec.identity_id)
    env.ids.table[p] = "not-a-hash"
    assert env.adm.run_identity("table_identity", p, DOMAIN).outcome == "crashed"
    env.ids.table[p] = RuntimeError("identity crashed")
    assert env.adm.run_identity("table_identity", p, DOMAIN).outcome == "crashed"
    with pytest.raises(RegistryError):
        env.adm.run_identity("scripted", p, DOMAIN)


def test_conflicting_identities_are_quarantined(env):
    p = env.presentation("P")
    env.identity(p, CODE)
    rep = env.identity(p, sha("another-code"))
    assert rep.incidents and len(rep.quarantined) == 2
    assert env.resolver().code_of(p) is None
    assert env.adm.verify().ok


def test_revalidate_results_rejects_non_results():
    with pytest.raises(TypeError):
        _revalidate_results([{"proposition": {}}])
    with pytest.raises(TypeError):
        _revalidate_results(lower(CODE, "Z", 3))


def test_non_json_detail_is_a_malformed_result(env):
    report = env.admit(vr("css_valid", "presentation", env.presentation("P"),
                          detail={"x": float("nan")}))
    assert report.outcome == "crashed" and report.detail["error_type"] == "malformed_result"


def test_input_not_durable_opens_no_execution(env, monkeypatch):
    real = artifacts_mod._fsync_dir
    seed_dir = str(env.store.path_for(env.seed).parent)

    def crash(path):
        if path == seed_dir:
            raise OSError("injected crash at directory fsync")
        return real(path)
    monkeypatch.setattr(artifacts_mod, "_fsync_dir", crash)
    with pytest.raises(AdmissionRefused, match="durably published"):
        env.admit(lower(CODE, "Z", 3))
    assert "execution_opened" not in env.event_kinds()


def test_ledger_refuses_secret_bearing_payloads(env):
    from zkqec.store import SecretDetected
    with pytest.raises(SecretDetected):
        env.adm.open_incident("note", {"leak": "Authorization: Bearer SYNTHETICabcdefgh123"})
    with pytest.raises(SecretDetected):
        with env.adm.ledger.transaction():
            env.adm.ledger.append("incident_opened", {"incident_id": "x", "category": "c",
                                                      "detail": {"api_key": "SYNTHETICkey"}})
    assert "incident_opened" not in env.event_kinds()


def test_replaying_a_refused_execution_raises(env):
    def tamper(**inputs):
        path = env.store.path_for(env.seed)
        path.chmod(0o644)
        path.write_bytes(b"x")
        return [lower(CODE, "Z", 3)]
    env.script.action = tamper
    with pytest.raises(AdmissionRefused):
        env.admit(idempotency_key="k")
    env.store.path_for(env.seed).unlink()
    env.store.put_json({"seed": "input"})                # restore the input
    with pytest.raises(AdmissionRefused) as info:
        env.admit(idempotency_key="k")
    assert info.value.report.replayed
