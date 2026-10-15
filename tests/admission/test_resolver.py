"""Resolver inference rules (contracts §A3; prompt §4.5)."""
import pytest

from admission_helpers import DOMAIN, OTHER_DOMAIN, dim, lower, prop, sha, upper, vr, weight
from zkqec.admission import ReceiptRecord, Resolver, weakest
from zkqec.contracts import Guarantee, Receipt, content_hash

CODE = sha("code:resolver")


def st(env, p, domain=DOMAIN):
    return env.resolver(domain).status(p)


def L(sector, d):
    return prop("distance_lower_bound", "code", CODE, sector=sector, d=d)


def U(sector, d):
    return prop("distance_upper_bound", "code", CODE, sector=sector, d=d)


def EXACT(sector, d):
    return prop("distance_exact", "code", CODE, sector=sector, d=d)


def test_lower_bounds_are_monotone_downward(env):
    rid = env.admit(lower(CODE, "Z", 5)).records[0]
    direct = st(env, L("Z", 5))
    assert direct.status == "verified" and direct.rule == "direct" and direct.premises == (rid,)
    weaker = st(env, L("Z", 3))
    assert weaker.status == "verified" and weaker.rule == "lower_monotone"
    assert st(env, L("Z", 6)).status == "unknown"
    assert st(env, L("X", 3)).status == "unknown"          # sectors are distinct


def test_upper_bounds_are_monotone_upward(env):
    env.admit(upper(CODE, "X", 4))
    assert st(env, U("X", 6)).rule == "upper_monotone"
    assert st(env, U("X", 6)).status == "verified"
    assert st(env, U("X", 3)).status == "unknown"


def test_equal_bounds_derive_distance_exact(env):
    env.admit(lower(CODE, "Z", 5), upper(CODE, "Z", 5))
    res = st(env, EXACT("Z", 5))
    assert res.status == "verified" and res.rule == "exact_from_bounds" and len(res.premises) == 2
    assert st(env, EXACT("Z", 4)).status == "unknown"       # derived claims are never "refuted"
    env2_only_lower = st(env, EXACT("X", 5))
    assert env2_only_lower.status == "unknown"


def test_sector_min_rule(env):
    env.admit(lower(CODE, "X", 5), lower(CODE, "Z", 4))
    both4 = st(env, L("both", 4))
    assert both4.status == "verified" and both4.rule == "sector_min" and len(both4.premises) == 2
    assert st(env, L("both", 5)).status == "unknown"        # min(5, 4) = 4


def test_both_bounds_and_sectors(env):
    env.admit(lower(CODE, "both", 6), upper(CODE, "X", 7))
    assert st(env, L("X", 6)).rule == "both_lower_to_sector"
    assert st(env, L("Z", 6)).status == "verified"
    up = st(env, U("both", 7))
    assert up.status == "verified" and up.rule == "sector_upper"
    # upper(both) alone says nothing about either sector individually
    env.admit(upper(CODE, "both", 8))
    assert st(env, U("Z", 8)).status == "unknown"


def test_eq_implies_le_and_opposite_value_refutes_eq(env):
    p = env.presentation("P")
    env.admit(weight(p, 6), dim(CODE, 2))
    le = lambda w: prop("max_check_weight_le", "presentation", p, w=w)  # noqa: E731
    assert st(env, le(6)).status == "verified" and st(env, le(6)).rule == "eq_implies_bound"
    assert st(env, le(9)).status == "verified"
    assert st(env, le(5)).status == "unknown"                # derived-only: never refuted
    assert st(env, prop("dimension_eq", "code", CODE, k=2)).rule == "direct"
    other = st(env, prop("dimension_eq", "code", CODE, k=3))
    assert other.status == "refuted" and other.rule == "opposite_bound"


def test_witness_never_entails_the_opposite_direction(env):
    env.admit(upper(CODE, "Z", 3))                           # a weight-3 logical witness
    for d in (1, 2, 3):
        assert st(env, L("Z", d)).status == "unknown"        # never a lower bound
    refuted = st(env, L("Z", 4))
    assert refuted.status == "refuted" and refuted.rule == "opposite_bound"
    env.admit(lower(CODE, "X", 5))
    for d in (5, 6, 9):
        assert st(env, U("X", d)).status == "unknown"        # a lower bound never an upper bound


@pytest.mark.parametrize("kind", ["solver_claim", "agent_assertion", "assumption"])
def test_non_authorizing_kinds_never_authorize(env, kind):
    env.admit(vr("distance_lower_bound", "code", CODE, "unknown", kind=kind, sector="Z", d=5))
    assert st(env, L("Z", 5)).status == "unknown"
    # even a forged, admitted-looking receipt with that kind is ignored by the pure core
    forged = _receipt(L("Z", 5), evidence_kind=kind)
    res = Resolver(DOMAIN, [ReceiptRecord(content_hash(forged), forged)]).status(L("Z", 5))
    assert res.status == "unknown"


def test_refuted_premise_leaves_derived_claims_unknown(env):
    env.admit(lower(CODE, "Z", 5, status="refuted"), upper(CODE, "Z", 5))
    assert st(env, L("Z", 5)).status == "refuted"            # the premise itself
    assert st(env, EXACT("Z", 5)).status == "unknown"        # the derived claim: unknown


def test_revocation_makes_descendants_unknown(env):
    rep = env.admit(lower(CODE, "Z", 5), upper(CODE, "Z", 5))
    assert st(env, EXACT("Z", 5)).status == "verified"
    low_id = st(env, L("Z", 5)).premises[0]
    env.adm.revoke(low_id, "verifier bug")
    assert st(env, L("Z", 5)).status == "unknown"
    assert st(env, L("Z", 3)).status == "unknown"
    assert st(env, EXACT("Z", 5)).status == "unknown"
    assert set(rep.records) > {low_id}


def test_parent_revocation_propagates_in_the_pure_core():
    parent = _receipt(L("Z", 5))
    pid = content_hash(parent)
    child = _receipt(U("Z", 5), parents=(pid,))
    cid = content_hash(child)
    live = Resolver(DOMAIN, [ReceiptRecord(pid, parent), ReceiptRecord(cid, child)])
    assert live.status(U("Z", 5)).status == "verified"
    dead = Resolver(DOMAIN, [ReceiptRecord(pid, parent, revoked=True), ReceiptRecord(cid, child)])
    assert dead.status(U("Z", 5)).status == "unknown"
    orphan = Resolver(DOMAIN, [ReceiptRecord(cid, child)])           # parent unknown
    assert orphan.status(U("Z", 5)).status == "unknown"


def test_receipt_id_must_match_content_in_the_pure_core():
    r = _receipt(L("Z", 5))
    assert Resolver(DOMAIN, [ReceiptRecord(sha("wrong-id"), r)]).status(L("Z", 5)).status \
        == "unknown"


def test_guarantee_propagation_uses_the_weakest_premise():
    x = _receipt(L("X", 5))
    z = _receipt(L("Z", 5), guarantee=Guarantee(kind="numerical", tolerance=1e-9))
    res = Resolver(DOMAIN, [ReceiptRecord(content_hash(x), x),
                            ReceiptRecord(content_hash(z), z)]).status(L("both", 5))
    assert res.status == "verified" and res.guarantee.kind == "numerical"
    assert res.guarantee.tolerance == 1e-9
    a = Guarantee(kind="statistical", alpha=0.01, design=sha("d1"))
    b = Guarantee(kind="statistical", alpha=0.02, design=sha("d2"))
    g = weakest([Guarantee(kind="exact"), a, b, Guarantee(kind="numerical", tolerance=1e-3)])
    assert g.kind == "statistical" and g.alpha == pytest.approx(0.03)
    assert weakest([Guarantee(kind="numerical", tolerance=1e-6),
                    Guarantee(kind="numerical", tolerance=1e-3)]).tolerance == 1e-3
    assert weakest([Guarantee(kind="exact")]).kind == "exact"
    assert weakest([Guarantee(kind="statistical", alpha=0.6, design="a"),
                    Guarantee(kind="statistical", alpha=0.5, design="b")]) is None


def test_strongest_support_is_preferred():
    num = _receipt(L("Z", 7), guarantee=Guarantee(kind="numerical", tolerance=1e-6))
    ex = _receipt(L("Z", 5))
    res = Resolver(DOMAIN, [ReceiptRecord(content_hash(num), num),
                            ReceiptRecord(content_hash(ex), ex)]).status(L("Z", 4))
    assert res.guarantee.kind == "exact" and res.premises == (content_hash(ex),)


def test_evidence_is_domain_scoped(env):
    env.admit(lower(CODE, "Z", 5), domain=OTHER_DOMAIN)
    assert st(env, L("Z", 5), OTHER_DOMAIN).status == "verified"
    assert st(env, L("Z", 5), DOMAIN).status == "unknown"


def test_derived_task_predicates_need_the_task_api(env):
    with pytest.raises(ValueError):
        env.resolver().status(prop("satisfies_task", "candidate", sha("c"), task=sha("t")))


def _receipt(p, *, evidence_kind="exact_algebra", guarantee=None, parents=(), status="verified"):
    """TEST-ONLY receipt built directly (bypassing admission) to exercise the pure resolver core
    with guarantee kinds and parents that no P1a verifier signature can produce."""
    return Receipt(proposition=p, status=status, evidence_kind=evidence_kind,
                   guarantee=guarantee or Guarantee(kind="exact"), method="m",
                   completion="completed", issuer="test", issuer_version="0",
                   execution_id="e", input_hashes=(sha("in"),), domain=DOMAIN, parents=parents)
