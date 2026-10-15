"""Semantic-conflict control (contracts §A3, §I): conflicting premises are quarantined, an incident
is opened, and neither ``satisfies_task`` nor ``target_refuted`` can be derived from them."""
import pytest

from admission_helpers import blen, css, degree, dim, lower, sha, upper, weight
from zkqec.admission import find_conflicts
from zkqec.contracts import DesignCandidate, StructuralTargets, TaskSA

CODE = sha("code:conflict")
TASK = TaskSA(task_id="sa-conflict", targets=StructuralTargets(n_max=20, k_min=1, d_min=4,
                                                                w_max=6, deg_max=4))


def ready(env):
    """Every S-A premise except distance is established for a candidate on presentation P."""
    p = env.presentation("P")
    env.admit(css(p), blen(p, 12), weight(p, 6), degree(p, 3), dim(CODE, 2))
    env.identity(p, CODE)
    return DesignCandidate(C=p)


def incidents(env):
    return env.adm.ledger.conn.execute("SELECT category FROM incidents").fetchall()


def test_control_lower5_upper3_quarantines_and_authorizes_nothing(env):
    cand = ready(env)
    env.admit(lower(CODE, "X", 5))                           # unrelated, stays current
    rep = env.admit(lower(CODE, "Z", 5), upper(CODE, "Z", 3))
    assert sorted(rep.quarantined) == sorted(rep.records) and len(rep.quarantined) == 2
    assert rep.incidents and incidents(env) == [("semantic_conflict",)]
    r = env.resolver()
    assert r.unquarantined_conflicts == ()
    assert r.satisfies_task(cand, TASK).status == "unknown"
    assert r.target_refuted(cand, TASK, "d_min").status == "unknown"
    for p in (lower(CODE, "Z", 5).proposition, upper(CODE, "Z", 3).proposition):
        assert r.status(p).status == "unknown"
    assert r.status(lower(CODE, "X", 5).proposition).status == "verified"
    assert env.adm.verify().ok
    before = env.adm.ledger.snapshot()
    env.adm.rebuild()                                        # quarantine survives a rebuild
    assert env.adm.ledger.snapshot() == before


def test_control_conflict_across_separate_admissions(env):
    cand = ready(env)
    env.admit(lower(CODE, "X", 5), lower(CODE, "Z", 5))
    rep = env.admit(upper(CODE, "Z", 3))
    assert len(rep.quarantined) == 2 and rep.incidents
    r = env.resolver()
    assert r.satisfies_task(cand, TASK).status == "unknown"
    assert r.target_refuted(cand, TASK, "d_min").status == "unknown"


def test_paired_positive_consistent_bounds_authorize(env):
    cand = ready(env)
    rep = env.admit(lower(CODE, "X", 5), lower(CODE, "Z", 5), upper(CODE, "Z", 6))
    assert rep.quarantined == () and rep.incidents == ()
    res = env.resolver().satisfies_task(cand, TASK)
    assert res.status == "verified"
    assert env.resolver().target_refuted(cand, TASK, "d_min").status == "unknown"


def test_paired_positive_target_refuted_from_consistent_upper_bound(env):
    cand = ready(env)
    env.admit(lower(CODE, "X", 5), upper(CODE, "Z", 3))
    r = env.resolver()
    refuted = r.target_refuted(cand, TASK, "d_min")
    assert refuted.status == "verified" and refuted.rule == "opposite_bound"
    assert r.satisfies_task(cand, TASK).status == "unknown"


@pytest.mark.parametrize("results,expected", [
    ([lower(CODE, "both", 5), upper(CODE, "X", 3)], 2),               # both-lower vs sector-upper
    ([lower(CODE, "X", 5), lower(CODE, "Z", 4), upper(CODE, "both", 3)], 3),   # min rule
    ([dim(CODE, 2), dim(CODE, 3)], 2),                                 # conflicting equalities
    ([dim(CODE, 2), dim(CODE, 2, status="refuted")], 2),               # eq vs refuted eq
    ([lower(CODE, "Z", 5), lower(CODE, "Z", 3, status="refuted")], 2),  # d>=5 and d<3
])
def test_semantic_conflicts_are_quarantined(env, results, expected):
    rep = env.admit(*results)
    assert len(rep.quarantined) == expected and rep.incidents


def test_direct_contradiction_on_a_boolean_predicate(env):
    p = env.presentation("P")
    rep = env.admit(css(p), css(p, status="refuted"))
    assert len(rep.quarantined) == 2
    assert incidents(env) == [("direct_contradiction",)]
    assert env.resolver().status(css(p).proposition).status == "unknown"


@pytest.mark.parametrize("results", [
    [upper(CODE, "both", 3), lower(CODE, "X", 5)],                    # Z may still be <= 3
    [lower(CODE, "both", 3), upper(CODE, "X", 3)],
    [lower(CODE, "X", 5), upper(CODE, "Z", 2)],                       # different sectors
    [dim(CODE, 2), dim(CODE, 3, status="refuted")],
])
def test_consistent_sets_are_not_quarantined(env, results):
    rep = env.admit(*results)
    assert rep.quarantined == () and rep.incidents == ()


def test_revoked_or_quarantined_receipts_do_not_create_new_conflicts(env):
    rep = env.admit(upper(CODE, "Z", 3))
    env.adm.revoke(rep.records[0], "bad witness")
    assert env.admit(lower(CODE, "Z", 5)).quarantined == ()


def test_find_conflicts_excluded_interval():
    from admission_helpers import DOMAIN, vr
    from zkqec.admission import ReceiptRecord, current_receipts
    from zkqec.contracts import Receipt, content_hash
    recs = []
    for r in (vr("max_check_weight_eq", "presentation", sha("p"), w=4),
              vr("max_check_weight_eq", "presentation", sha("p"), "refuted", w=4)):
        rc = Receipt(**r.model_dump(), issuer="t", issuer_version="0", execution_id="e",
                     input_hashes=(sha("i"),), domain=DOMAIN)
        recs.append(ReceiptRecord(content_hash(rc), rc))
    (conflict,) = find_conflicts(current_receipts(recs, DOMAIN))
    assert conflict.key == "max_check_weight" and len(conflict.receipt_ids) == 2
