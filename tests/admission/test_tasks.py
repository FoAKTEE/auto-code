"""Task derivations (S-A, S-F) and identity transport (contracts §A3 transport table, §B1, §F)."""
import pytest

from admission_helpers import blen, css, degree, dim, lower, prop, sha, upper, vr, weight
from zkqec.contracts import (DesignCandidate, HardwareDomain, LogicalBasis, OperationRef,
                             PhysicalOperation, SignedPauliImages, StructuralTargets, TaskSA, TaskSF,
                             content_hash)

CODE = sha("code:task")
SA = TaskSA(task_id="sa", targets=StructuralTargets(n_max=20, k_min=2, d_min=4, w_max=6,
                                                     deg_max=4))


def full(env, label="P", code=CODE, *, d=4, k=2, n=12, w=6, q=3):
    p = env.presentation(label)
    env.admit(css(p), blen(p, n), weight(p, w), degree(p, q))
    env.admit(dim(code, k), lower(code, "X", d), lower(code, "Z", d))
    env.identity(p, code)
    return p


def test_sa_positive_with_structured_explanation(env):
    p = full(env)
    res = env.resolver().satisfies_task(DesignCandidate(C=p), SA)
    assert res.status == "verified" and res.rule == "task_conjunction"
    names = [r.name for r in res.requirements]
    assert names == ["css_valid", "n_max", "k_min", "d_min", "w_max", "deg_max"]
    assert all(r.status == "met" and r.premises for r in res.requirements)
    identity_id = env.resolver().code_of(p)[1]
    assert identity_id in res.premises and res.guarantee.kind == "exact"
    d_req = res.requirements[3]
    assert "sector_min" in d_req.explanation and identity_id in d_req.premises


@pytest.mark.parametrize("missing", ["css_valid", "n_max", "k_min", "d_min", "w_max", "deg_max"])
def test_sa_each_missing_premise_leaves_unknown(env, missing):
    p = env.presentation("P")
    by_name = {"css_valid": [css(p)], "n_max": [blen(p, 12)], "w_max": [weight(p, 6)],
               "deg_max": [degree(p, 3)], "k_min": [dim(CODE, 2)],
               "d_min": [lower(CODE, "X", 4), lower(CODE, "Z", 4)]}
    env.admit(*[r for name, rs in by_name.items() if name != missing for r in rs])
    env.identity(p, CODE)
    res = env.resolver().satisfies_task(DesignCandidate(C=p), SA)
    assert res.status == "unknown" and missing in res.explanation
    assert [r.name for r in res.requirements if r.status != "met"] == [missing]


@pytest.mark.parametrize("requirement,results", [
    ("n_max", lambda p: [blen(p, 21)]),
    ("k_min", lambda p: [dim(CODE, 1)]),
    ("d_min", lambda p: [upper(CODE, "X", 3)]),
    ("d_min", lambda p: [upper(CODE, "both", 3)]),
    ("w_max", lambda p: [weight(p, 7)]),
    ("deg_max", lambda p: [degree(p, 5)]),
])
def test_target_refuted_needs_a_verified_opposite_bound(env, requirement, results):
    p = env.presentation("P")
    env.admit(*results(p))
    env.identity(p, CODE)
    cand = DesignCandidate(C=p)
    res = env.resolver().target_refuted(cand, SA, requirement)
    assert res.status == "verified" and res.premises
    assert env.resolver().satisfies_task(cand, SA).status == "unknown"


@pytest.mark.parametrize("requirement,results", [
    ("d_min", lambda p: [lower(CODE, "Z", 2)]),             # a lower bound refutes nothing
    ("d_min", lambda p: [lower(CODE, "Z", 3, status="refuted")]),   # refuted premise ≠ refutation
    ("d_min", lambda p: [upper(CODE, "Z", 4)]),             # not below d_min
    ("k_min", lambda p: [dim(CODE, 2, status="refuted")]),
    ("w_max", lambda p: [weight(p, 6)]),
])
def test_target_refuted_negatives(env, requirement, results):
    p = env.presentation("P")
    env.admit(*results(p))
    env.identity(p, CODE)
    assert env.resolver().target_refuted(DesignCandidate(C=p), SA, requirement).status == "unknown"


def test_target_refuted_requirement_names(env):
    p = env.presentation("P")
    no_w = TaskSA(task_id="t", targets=StructuralTargets(n_max=20, k_min=1, d_min=3))
    env.admit(weight(p, 99))
    assert env.resolver().target_refuted(DesignCandidate(C=p), no_w, "w_max").status == "unknown"
    with pytest.raises(ValueError):
        env.resolver().target_refuted(DesignCandidate(C=p), no_w, "speed")


def test_code_level_evidence_transports_through_identity(env):
    p1 = full(env, "P1")
    p2 = env.presentation("P2")
    env.admit(css(p2), blen(p2, 12), weight(p2, 6), degree(p2, 3))
    env.identity(p2, CODE)                                     # same stabilizer group
    r = env.resolver()
    for p in (p1, p2):
        res = r.satisfies_task(DesignCandidate(C=p), SA)
        assert res.status == "verified", res.explanation
    without_identity = env.presentation("P3")
    env.admit(css(without_identity), blen(without_identity, 12), weight(without_identity, 6),
              degree(without_identity, 3))
    res = env.resolver().satisfies_task(DesignCandidate(C=without_identity), SA)
    assert {r.name for r in res.requirements if r.status != "met"} == {"k_min", "d_min"}


def test_control_presentation_weights_never_transport(env):
    """Control: presentation-level weights transported across equivalent presentations."""
    p1 = full(env, "P1", w=6)
    p2 = env.presentation("P2")
    env.admit(css(p2), blen(p2, 12), degree(p2, 3))           # no weight receipt on P2
    env.identity(p2, CODE)
    r = env.resolver()
    le6 = lambda p: prop("max_check_weight_le", "presentation", p, w=6)  # noqa: E731
    assert r.status(le6(p1)).status == "verified"
    assert r.status(le6(p2)).status == "unknown"
    res = r.satisfies_task(DesignCandidate(C=p2), SA)
    assert res.status == "unknown"
    assert [q.name for q in res.requirements if q.status != "met"] == ["w_max"]
    # also not via a verified same_stabilizer_group relation between codes
    env.admit(vr("same_stabilizer_group", "code", CODE, other=sha("other-code")))
    assert env.resolver().status(le6(p2)).status == "unknown"
    # paired positive: a receipt on P2 itself authorizes
    env.admit(weight(p2, 6))
    assert env.resolver().satisfies_task(DesignCandidate(C=p2), SA).status == "verified"


def test_revoked_identity_stops_transport(env):
    p = full(env)
    identity_id = env.resolver().code_of(p)[1]
    env.adm.revoke(identity_id, "identity function bug")
    res = env.resolver().satisfies_task(DesignCandidate(C=p), SA)
    assert res.status == "unknown"
    assert {q.name for q in res.requirements if q.status != "met"} == {"k_min", "d_min"}


def test_candidate_without_presentation_is_unknown(env):
    res = env.resolver().satisfies_task(DesignCandidate(C="unsupported"), SA)
    assert res.status == "unknown"


# --- S-F ------------------------------------------------------------------------------------------
TARGET = SignedPauliImages(x_images=("XI", "IX"), z_images=("ZI", "IZ"))
SWAP = SignedPauliImages(x_images=("IX", "XI"), z_images=("IZ", "ZI"))


def sf_task(allowed=("qubit_permutation",), target=SWAP):
    return TaskSF(task_id="sf", targets=StructuralTargets(n_max=20, k_min=2, d_min=4),
                  k_req=2, target_action=target,
                  hardware=HardwareDomain(name="perm-only", allowed_operations=allowed))


def sf_candidate(env, p, *, circuit=None, basis_presentation=None, target=SWAP, qubits=(0, 1)):
    store = env.store
    basis = store.put_json(LogicalBasis(presentation=basis_presentation or p,
                                        lx=((1, 1, 0, 0),), lz=((1, 0, 1, 0),)))
    circ = store.put_json(circuit or PhysicalOperation(n=4, permutation=(1, 0, 3, 2)))
    op = OperationRef(target_action=store.put_json(target), circuit=circ, basis=basis,
                      logical_qubits=qubits)
    return DesignCandidate(C=p, L=basis, G=(op,)), op


def implements(op, status="verified"):
    return vr("implements", "operation", content_hash(op), status, target_action=op.target_action,
              basis=op.basis, phase_convention=op.phase_convention)


def test_sf_positive(env):
    p = full(env)
    cand, op = sf_candidate(env, p)
    env.admit(implements(op))
    res = env.resolver().satisfies_task(cand, sf_task())
    assert res.status == "verified", res.explanation
    assert res.requirements[-1].name == "operation" and res.requirements[-1].status == "met"
    assert env.resolver().target_refuted(cand, sf_task(), "operation").status == "unknown"


@pytest.mark.parametrize("variant", ["no_receipt", "refuted", "class", "target", "basis_owner",
                                     "width"])
def test_sf_negatives(env, variant):
    p = full(env)
    kw = {}
    if variant == "class":
        kw["circuit"] = PhysicalOperation(n=4, local_cliffords=("H", "H", "H", "H"))
    if variant == "target":
        kw["target"] = TARGET                                   # identity, not the task's swap
    if variant == "basis_owner":
        kw["basis_presentation"] = env.presentation("someone-else")
    if variant == "width":
        kw["qubits"] = (0,)
    cand, op = sf_candidate(env, p, **kw)
    if variant != "no_receipt":
        env.admit(implements(op, "refuted" if variant == "refuted" else "verified"))
    res = env.resolver().satisfies_task(cand, sf_task())
    assert res.status == "unknown"
    assert [q.name for q in res.requirements if q.status != "met"] == ["operation"]


def test_sf_needs_an_artifact_store(env):
    from zkqec.admission import Resolver
    p = full(env)
    cand, op = sf_candidate(env, p)
    env.admit(implements(op))
    r = Resolver(env.resolver().domain, env.adm.ledger.receipt_records(),
                 env.adm.ledger.identity_records())
    assert r.satisfies_task(cand, sf_task()).status == "unknown"
