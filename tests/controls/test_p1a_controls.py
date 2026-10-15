"""P1a control suite (contracts §I): every injection runs through the production path and is not
authorized, every paired positive is authorized, the evidence is canonical and reproducible, and the
controls exercise their gates (disabling a gate turns its injection into a false authorization)."""
import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest

from zkqec.admission import Admission
from zkqec.admission.ledger import CONTROL_FIELDS
from zkqec.contracts import canonical_json, content_hash
from zkqec.controls import SuiteInputs, p1a, run_suite
from zkqec.pipeline import build_registry
from zkqec.store import ArtifactStore

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "roles" / "designer" / "output.schema.json").read_text())
INPUTS = SuiteInputs(json.dumps(SCHEMA, sort_keys=True, separators=(",", ":")).encode(),
                     ("explicit_sparse",))
NAMES = [c.name for c in p1a.CONTROLS]


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    return run_suite("p1a", tmp_path_factory.mktemp("controls"), INPUTS)


def by_name(ev):
    return {c["control"]: c for c in ev["controls"]}


def test_the_required_p1a_controls_are_present():
    required = {"task_mutated", "invalid_css", "incomplete_basis", "timeout_as_bound", "s_vs_sdg",
                "spoofed_issuer", "proposer_owned_fields", "evaluator_tamper",
                "duplicate_completion", "semantic_conflict", "steane_t2_d5_misreading",
                "chain_l2_declared_d6", "witness_as_lower_bound", "wrong_automorphism",
                "tampered_or_stale_artifact", "revoked_premise", "real_field_rank"}
    assert required <= set(NAMES) and len(NAMES) == len(set(NAMES))
    for c in p1a.CONTROLS:
        roles = {case.role for case in c.cases}
        assert roles == {"injection", "positive"}, c.name        # N >= 1 and M >= 1
        assert c.gate and c.sources


@pytest.mark.parametrize("name", NAMES)
def test_injections_are_not_authorized_and_positives_are(evidence, name):
    c = by_name(evidence)[name]
    for case in c["injections"]:
        assert case["expected"]["authorized"] is False
        assert case["observed"]["authorized"] is False, case
        assert case["observed"]["outcome"] == case["expected"]["outcome"], case
    for case in c["positives"]:
        assert case["expected"]["authorized"] is True
        assert case["observed"]["authorized"] is True, case
        assert case["observed"]["outcome"] == case["expected"]["outcome"], case
    assert c["counts"] == {"injections": len(c["injections"]), "false_authorizations": 0,
                           "paired_positives": len(c["positives"]), "false_rejections": 0,
                           "outcome_mismatches": 0}


def test_controls_exercise_what_they_claim(evidence):
    """Spot checks that the observed facts come from the gate under test."""
    c = by_name(evidence)

    def case(name, case_name):
        return next(x for x in c[name]["injections"] + c[name]["positives"]
                    if x["case"] == case_name)["observed"]["facts"]
    assert case("semantic_conflict", "lower5_upper3_one_execution")["quarantined"] == 2
    assert case("semantic_conflict", "lower5_upper3_one_execution")["incidents"] == \
        ["semantic_conflict"]
    assert case("timeout_as_bound", "killed_enumeration")["d_both"] == [None, None]
    assert case("steane_t2_d5_misreading", "steane_declared_d5")["d_both"] == [3, 3]
    assert case("chain_l2_declared_d6", "chain2_declared_d6")["d_both"] == [5, 5]
    assert case("witness_as_lower_bound", "weight6_witness_for_d6")["d_both"] == [1, 6]
    assert case("task_mutated", "task_file_edited")["incidents"] == ["task_mutated"]
    tamper = case("evaluator_tamper", "edited_verifier_source")
    assert tamper["incidents"] == ["evaluator_changed"] and tamper["executions"] == 0
    assert case("spoofed_issuer", "forged_receipt_chain")["resolver_claim"] == "verified"
    assert case("spoofed_issuer", "forged_receipt_chain")["replay_problems"] >= 1
    assert case("solver_claim_as_proof", "solver_claim_verified_bounds")["admission"] == "crashed"
    assert case("real_field_rank", "real_rank_dimension_claim")["k"] == 1
    truncated = case("incomplete_basis", "truncated_basis_hides_logical_5")
    assert truncated["implements_refusals"][0].startswith("incomplete basis")
    assert case("incomplete_basis", "unpaired_basis")["implements_refusals"][0].startswith(
        "invalid basis")
    assert case("presentation_weight_transport", "heavy_generators_same_code")["same_code"]
    assert case("tampered_or_stale_artifact", "stale_domain_evidence")["stale_domain_verdict"] \
        == "verified"


def test_real_field_rank_fixture_differs_from_gf2_rank():
    from zkqec.kernel import gf2
    hx, _ = p1a.fixtures.steane_checks()
    redundant = np.vstack([hx, hx[0] ^ hx[1]])
    assert gf2.rank(redundant) == 3 and np.linalg.matrix_rank(redundant.astype(float)) == 4
    assert p1a.K_REAL_FIELD == 7 - 4 - 3


def test_results_rows_match_the_ledger_contract(evidence, tmp_path):
    rows = evidence["results"]
    assert [r["control"] for r in rows] == NAMES
    assert all(set(r) == CONTROL_FIELDS for r in rows)
    store = ArtifactStore(tmp_path / "store")
    with Admission(tmp_path / "ledger.db", store, build_registry(store)) as adm:
        adm.freeze_campaign("c", namespace="c", config={}, tasks=[p1a.fixtures.STEANE_D3.model_dump(
            mode="json")], caps={}, labels=[], domain="0" * 64, manifest=store.put_json({}))
        sha = store.put_json(evidence)
        assert sha == content_hash(evidence)                   # nothing redacted: bytes reproducible
        adm.record_controls("c", "p1a", rows, evidence=sha)
        assert adm.ledger.controls("c")[0]["results"] == rows


def test_deferred_controls_are_listed_with_reasons_and_never_counted(evidence):
    deferred = {d["control"]: d for d in evidence["deferred"]}
    assert "torus_collision" in deferred and all(d["reason"] for d in deferred.values())
    assert not set(deferred) & {r["control"] for r in evidence["results"]}


def test_evidence_is_canonical_and_free_of_paths_and_times(evidence, tmp_path_factory):
    """Byte reproducibility across runs is the audit's check (test_controls_audit)."""
    data = canonical_json(evidence)
    assert json.loads(data) == evidence
    assert str(tmp_path_factory.getbasetemp()).encode() not in data
    assert b"/tmp" not in data and b"/data" not in data and b"created_at" not in data
    assert evidence["inputs"] == {"schema_sha256": INPUTS.schema_sha, "families": ["explicit_sparse"]}
    assert evidence["control_set"] == "p1a" and evidence["suite_version"] == p1a.SUITE_VERSION


def test_unknown_control_set_and_unknown_controls_are_refused(tmp_path):
    with pytest.raises(KeyError):
        run_suite("p2", tmp_path / "a", INPUTS)
    with pytest.raises(KeyError):
        run_suite("p1a", tmp_path / "b", INPUTS, only=("no_such_control",))


# --- the controls exercise their gates: disabling a gate yields a false authorization --------------
def counts(ev, name):
    return by_name(ev)[name]["counts"]


def test_task_mutated_flips_when_the_frozen_task_check_is_disabled(tmp_path, monkeypatch):
    from zkqec.orchestrator import freeze
    monkeypatch.setattr(freeze, "check_task", lambda *a, **k: None)
    ev = run_suite("p1a", tmp_path, INPUTS, only=("task_mutated",))
    edited = by_name(ev)["task_mutated"]["injections"][0]
    assert edited["case"] == "task_file_edited" and edited["observed"]["authorized"] is True
    assert counts(ev, "task_mutated")["false_authorizations"] == 1
    assert ev["results"][0]["false_authorizations"] == 1


def test_revoked_premise_flips_when_revocation_is_ignored(tmp_path, monkeypatch):
    import zkqec.admission.resolver as resolver
    real = resolver.current_receipts

    def ignore_revocation(records, domain, **kw):
        return real([dataclasses.replace(r, revoked=False) for r in records], domain, **kw)
    monkeypatch.setattr(resolver, "current_receipts", ignore_revocation)
    ev = run_suite("p1a", tmp_path, INPUTS, only=("revoked_premise",))
    flipped = [c["case"] for c in by_name(ev)["revoked_premise"]["injections"]
               if c["observed"]["authorized"]]
    assert flipped == ["revoked_distance_lower_bound"]


def test_s_vs_sdg_flips_when_signs_are_ignored(tmp_path, monkeypatch):
    from zkqec.verify.pauli import Pauli
    real = Pauli.to_str
    monkeypatch.setattr(Pauli, "to_str", lambda self: real(self)[1:])     # unsigned comparison
    ev = run_suite("p1a", tmp_path, INPUTS, only=("s_vs_sdg",))
    assert counts(ev, "s_vs_sdg")["false_authorizations"] == 1
