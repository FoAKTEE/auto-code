"""Production intake enforces the strict parser, the complete output schema and the frozen
profile's grammar families before any construction (Codex M1 review #12; appendix §B3, §N)."""
import json

import numpy as np
import pytest

from zkqec.contracts import (HardwareDomain, Profile, Proposal, Reservation, SignedPauliImages,
                             StructuralTargets, TaskSF)
from zkqec.grammar import MalformedProposal
from zkqec.kernel.codes import steane
from zkqec.orchestrator.campaign import CallResult, CampaignConfig, run_campaign

from test_campaign import ReplayProviderDouble, sparse

H = np.asarray(steane().hx)
TASK = TaskSF(task_id="sf-steane-identity", k_req=1,
              target_action=SignedPauliImages(x_images=("X",), z_images=("Z",)),
              targets=StructuralTargets(n_max=7, k_min=1, d_min=3),
              hardware=HardwareDomain(name="perm", allowed_operations=("qubit_permutation",)))
TWO_BLOCK = {"family": "two_block_group_algebra", "rationale": "x",
             "params": {"l": 6, "m": 6, "a": [[3, 0], [0, 1], [0, 2]],
                        "b": [[0, 3], [1, 0], [2, 0]]}}


def steane_output(**witness_extra):
    w = {"operation": {"permutation": list(range(7))}, "logical_qubits": [0],
         "basis": {"lx": [[1] * 7], "lz": [[1] * 7]}}
    for k, v in witness_extra.items():
        if k == "basis_extra":
            w["basis"].update(v)
        else:
            w[k] = v
    return {"family": "explicit_sparse", "params": {"hx": sparse(H), "hz": sparse(H)},
            "witnesses": w, "rationale": "fixture"}


def cfg(**kw):
    base = dict(campaign_id="intake", tasks=(TASK,), provider="replay", model="replay-double",
                profile=Profile(knowledge="K0", prompt="P-min", grammar=("G-open",)),
                max_proposals_per_task=2, caps={"usd": 1.0, "requests": 20},
                per_call=Reservation(usd=0.1, requests=1, input_tokens=0, output_tokens=0,
                                     wall_seconds=0, cpu_seconds=0))
    base.update(kw)
    return CampaignConfig(**base)


def outcomes(prov):
    if len(prov.requests) == 1:                 # the first proposal already satisfied the task
        return [("satisfied_on_first_proposal", "")]
    last = prov.requests[-1]["digest"]["earlier_attempts"]
    return [(a["outcome"], a.get("reason", "")) for a in last]


@pytest.mark.parametrize("bad", [
    {"phase_convention": "unsigned_v0"},                    # unknown witness field
    {"logical_qubits": [0.9]},                              # non-integer index (was int()-coerced)
    {"logical_qubits": [True]},                             # bool is not an index
    {"basis_extra": {"status": "verified"}},                # nested verifier-owned field
])
def test_schema_violating_proposals_are_malformed_not_coerced(tmp_path, bad):
    prov = ReplayProviderDouble([json.dumps(steane_output(**bad)),
                                 json.dumps(steane_output())])
    out = run_campaign(cfg(), prov, tmp_path, require_clean=False, seal_bundle=False)
    assert outcomes(prov)[0][0] == "malformed_proposal", outcomes(prov)
    assert out["satisfied"] == ["sf-steane-identity"]        # paired positive: the clean proposal


def test_family_outside_the_frozen_profile_is_rejected_by_policy(tmp_path):
    prov = ReplayProviderDouble([json.dumps(TWO_BLOCK), json.dumps(steane_output())])
    run_campaign(cfg(), prov, tmp_path, require_clean=False, seal_bundle=False)
    assert outcomes(prov)[0] == ("malformed_proposal", "rejected: policy")


def test_duplicate_keys_and_non_finite_numbers_are_malformed(tmp_path):
    good = json.dumps(steane_output())
    dup = good[:-1] + ', "family": "explicit_sparse"}'
    prov = ReplayProviderDouble([dup, '{"family": "explicit_sparse", "params": {}, "rationale": NaN}',
                                 good])
    run_campaign(cfg(max_proposals_per_task=3), prov, tmp_path, require_clean=False,
                 seal_bundle=False)
    assert [o for o, _ in outcomes(prov)] == ["malformed_proposal"] * 2


def test_pipeline_enforces_profile_families_and_integer_witnesses(tmp_path):
    from zkqec.admission import Admission
    from zkqec.grammar import GrammarCaps
    from zkqec.orchestrator.jobs import JobBook
    from zkqec.pipeline import Pipeline, build_registry, evaluator_domain
    from zkqec.store import ArtifactStore
    store = ArtifactStore(tmp_path / "store")
    reg = build_registry(store)
    adm = Admission(tmp_path / "ledger.db", store, reg)
    book = JobBook(adm.ledger.conn, "p"); book.set_caps({"cpu_seconds": 1e4})
    pipe = Pipeline(adm, store, book, evaluator_domain(reg), caps=GrammarCaps(),
                    families=("explicit_sparse",))
    try:
        ev = pipe.evaluate(Proposal(**TWO_BLOCK), TASK)
        assert (ev.outcome, ev.detail["reason"]) == ("malformed_proposal", "rejected: policy")
        p = Proposal.model_construct(**steane_output(logical_qubits=[0.9]), requested=())
        ev = pipe.evaluate(p, TASK)
        assert ev.satisfies != "verified" and "logical_qubits" in ev.detail.get("operation", "")
    finally:
        adm.close()


def test_intake_rejects_nested_verifier_owned_fields_and_bad_params():
    from zkqec.grammar.intake import parse_proposal
    from zkqec.orchestrator.campaign import REPO
    schema = json.loads((REPO / "roles" / "designer" / "output.schema.json").read_text())
    ok = parse_proposal(json.dumps(steane_output()), schema=schema, families=("explicit_sparse",))
    assert ok.family == "explicit_sparse"
    nested = steane_output()
    nested["params"]["hx"]["k"] = 1                          # verifier-owned, nested in params
    extra = steane_output()
    extra["params"]["hx"]["note"] = "x"                      # outside the family param schema
    for obj in (nested, extra, steane_output(basis_extra={"status": "verified"})):
        with pytest.raises(MalformedProposal):
            parse_proposal(json.dumps(obj), schema=schema, families=("explicit_sparse",))
