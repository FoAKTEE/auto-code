"""Contract-level rejection tests (prompt appendix §A, §B)."""
import pytest
from pydantic import ValidationError

from zkqec.contracts import (DesignCandidate, PhysicalOperation, Profile, Proposal, Proposition,
                             SignedPauliImages, SparseMatrix, TaskSF, StructuralTargets,
                             HardwareDomain, VerifierResult, content_hash, SIGNATURES)

H = "a" * 64


def prop(**kw):
    base = dict(predicate="distance_lower_bound", subject_type="code", subject=H,
                args={"sector": "Z", "d": 5})
    base.update(kw)
    return Proposition(**base)


def test_valid_proposition_hash_is_stable():
    assert prop().key == prop().key == content_hash(prop())


@pytest.mark.parametrize("kw", [
    {"subject_type": "presentation"},                       # wrong subject type
    {"args": {"sector": "Q", "d": 5}},                      # bad sector
    {"args": {"sector": "Z", "d": 0}},                      # non-positive bound
    {"args": {"sector": "Z", "d": 5, "extra": 1}},          # extra arg
    {"args": {"sector": "Z", "d": True}},                   # bool is not int
    {"predicate": "unknown_predicate"},
    {"subject": "XYZ"},                                     # not a hash
])
def test_signature_rejections(kw):
    with pytest.raises(ValidationError):
        prop(**kw)


@pytest.mark.parametrize("kind,completion", [("agent_assertion", "completed"),
                                             ("solver_claim", "completed"),
                                             ("assumption", "completed"),
                                             ("exact_algebra", "timeout"),
                                             ("exact_algebra", "budget_exhausted")])
def test_non_authorizing_results_cannot_verify(kind, completion):
    with pytest.raises(ValidationError):
        VerifierResult(proposition=prop(), status="verified", evidence_kind=kind, method="m",
                       completion=completion)


def test_unknown_is_always_allowed():
    VerifierResult(proposition=prop(), status="unknown", evidence_kind="exact_algebra", method="m",
                   completion="timeout")


def test_derived_only_predicates_cannot_come_from_verifiers():
    p = Proposition(predicate="distance_exact", subject_type="code", subject=H,
                    args={"sector": "both", "d": 6})
    with pytest.raises(ValidationError):
        VerifierResult(proposition=p, status="verified", evidence_kind="exact_algebra", method="m",
                       completion="completed")


def test_every_signature_is_well_formed():
    for sig in SIGNATURES.values():
        assert sig.kind in {"lower", "upper", "equality", "boolean", "relation", "derived", "statistical"}
        if sig.quantity:
            assert sig.value_arg in dict(sig.args)


@pytest.mark.parametrize("field", ["d", "k", "distance", "logical_action", "verified", "issuer"])
def test_proposer_cannot_supply_verifier_owned_fields(field):
    with pytest.raises(ValidationError):
        Proposal(family="explicit_sparse", params={field: 1})
    with pytest.raises(ValidationError):
        Proposal(family="explicit_sparse", params={}, witnesses={field: 1})


@pytest.mark.parametrize("entries", [((0, 1), (0, 1)), ((0, 2), (0, 1)), ((2, 0),), ((0, 3),)])
def test_sparse_rejects_duplicates_unsorted_out_of_range(entries):
    with pytest.raises(ValidationError):
        SparseMatrix(shape=(2, 3), entries=entries)


def test_isolated_columns_are_allowed_and_counted():
    m = SparseMatrix(shape=(1, 4), entries=((0, 0), (0, 1)))
    assert m.shape == (1, 4)


def test_operation_validation_and_classes():
    op = PhysicalOperation(n=3, permutation=(1, 2, 0))
    assert op.operation_classes == {"qubit_permutation"}
    assert PhysicalOperation(n=2, local_cliffords=("H", "H")).operation_classes == {"transversal_1q_clifford"}
    with pytest.raises(ValidationError):
        PhysicalOperation(n=3, permutation=(0, 0, 1))
    with pytest.raises(ValidationError):
        PhysicalOperation(n=2, local_cliffords=("T", "I"))


def test_signed_pauli_images_shape():
    SignedPauliImages(x_images=("+XI", "-IX"), z_images=("ZI", "IZ"))
    with pytest.raises(ValidationError):
        SignedPauliImages(x_images=("XI",), z_images=("Z",))


def test_profile_combinations():
    Profile(knowledge="K0", prompt="P-min", grammar=("G-open",))
    Profile(knowledge="K0", prompt="P-domain", grammar=("G-open", "G-struct"))
    with pytest.raises(ValidationError):
        Profile(knowledge="K0", prompt="P-min", grammar=("G-struct",))


def test_sf_task_requires_operation_class_and_matching_width():
    t = StructuralTargets(n_max=20, k_min=2, d_min=2)
    act = SignedPauliImages(x_images=("XI", "IX"), z_images=("ZI", "IZ"))
    with pytest.raises(ValidationError):
        TaskSF(task_id="t", targets=t, k_req=2, target_action=act, hardware=HardwareDomain(name="h"))
    with pytest.raises(ValidationError):
        TaskSF(task_id="t", targets=t, k_req=1, target_action=act,
               hardware=HardwareDomain(name="h", allowed_operations=("qubit_permutation",)))


def test_candidate_component_refs():
    DesignCandidate(C=H)
    with pytest.raises(ValidationError):
        DesignCandidate(C="not-a-hash")
