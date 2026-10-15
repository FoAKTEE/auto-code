"""L2 distance: §C1 preflight, §C2 completion rule with w*, §C3 witnesses, budget/timeout controls."""
import json
import pathlib
import tracemalloc

import numpy as np
import pytest

from zkqec.contracts import Proposal
from zkqec.grammar import GrammarCaps, build, from_dense
from zkqec.identity import code_sha, presentation_sha
from zkqec.kernel import gf2
from zkqec.kernel.codes import CSSCode, bicycle_chain, steane
from zkqec.kernel.distance import sector_distance
from zkqec.verify import (DistanceBudget, analyze, certify_distance, half_patterns, preflight,
                          validate_logical_witness)
from zkqec.verify.distance import memory_estimate

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def bounds(results):
    """{(predicate, sector): d} for verified results; asserts no duplicate keys."""
    out = {}
    for r in results:
        if r.status == "verified":
            key = (r.proposition.predicate, r.proposition.args["sector"])
            assert key not in out
            out[key] = r.proposition.args["d"]
    return out


def assert_witnesses_reverified(results, hx, hz, sink):
    """Every upper bound cites a witness with zero syndrome and nonzero logical signature."""
    ups = [r for r in results if r.proposition.predicate == "distance_upper_bound"
           and r.status == "verified"]
    for r in ups:
        assert len(r.witnesses) == 1
        w = sink.get(r.witnesses[0])
        sector, d = r.proposition.args["sector"], r.proposition.args["d"]
        v = np.zeros(hx.shape[1], dtype=np.uint8)
        v[w["support"]] = 1
        h, stab = (hz, hx) if sector == "X" else (hx, hz)
        assert w["sector"] == sector and w["weight"] == d == int(v.sum())
        assert not (h @ v % 2).any()                           # zero syndrome
        assert gf2.rank(np.vstack((stab, v))) > gf2.rank(stab)  # not a stabilizer
        assert w["code"] == r.proposition.subject == code_sha(hx, hz)
    return len(ups)


# ------------------------------------------------------------------ acceptance: reference table
@pytest.mark.parametrize("ell,n,k,d", [(2, 28, 4, 5), (3, 42, 6, 6), (4, 56, 8, 6)])
def test_qecforge_reference_table(ell, n, k, d, sink):
    c = bicycle_chain(ell, 7)
    p = from_dense(c.hx, c.hz)
    l1 = {r.proposition.predicate: r for r in analyze(p, artifact_sink=sink)}
    assert l1["css_valid"].status == "verified"
    assert l1["block_length_eq"].proposition.args == {"n": n}
    assert l1["dimension_eq"].proposition.args == {"k": k}
    res = certify_distance(c.hx, c.hz, 3, artifact_sink=sink)
    assert all(r.completion == "completed" for r in res)
    assert bounds(res) == {("distance_lower_bound", "X"): d, ("distance_upper_bound", "X"): d,
                           ("distance_lower_bound", "Z"): d, ("distance_upper_bound", "Z"): d}
    assert assert_witnesses_reverified(res, c.hx, c.hz, sink) == 2
    for r in res:
        rec = sink.get(r.artifacts[0])
        assert rec["kind"] == "distance_completed_range_v1" and rec["t"] == 3
        assert rec["w_star"] == d and rec["completion"] == "completed"
        assert rec["half_patterns"] == half_patterns(n, 3) and rec["pairs_checked"] >= 0


# ------------------------------------------------------------------ acceptance: Steane t = 2
def test_steane_t2_certifies_d3_not_d5(sink):
    c = steane()
    res = certify_distance(c.hx, c.hz, 2, artifact_sink=sink)
    b = bounds(res)
    assert b == {("distance_lower_bound", "X"): 3, ("distance_upper_bound", "X"): 3,
                 ("distance_lower_bound", "Z"): 3, ("distance_upper_bound", "Z"): 3}
    assert not any(r.proposition.args["d"] >= 5 for r in res
                   if r.proposition.predicate == "distance_lower_bound")
    for r in res:
        assert r.detail["w_star"] == 3 and r.detail["rule"].startswith("w* <= 2t")
    assert_witnesses_reverified(res, c.hx, c.hz, sink)


def _basis_min(hx, hz, sector):
    code = CSSCode(hx, hz)                       # canonical basis, as the verifier uses
    rows = code.lx if sector == "X" else code.lz
    return int(rows.sum(axis=1).min())


def test_steane_smaller_t_rules(sink):
    c = steane()
    assert _basis_min(c.hx, c.hz, "X") == 3      # canonical basis already has a weight-3 row
    # t = 1: nothing through 2t = 2, so lower = 2t+1 = 3; upper from the best known logical.
    res = certify_distance(c.hx, c.hz, 1, artifact_sink=sink, sectors=("X",))
    assert bounds(res) == {("distance_lower_bound", "X"): 3, ("distance_upper_bound", "X"): 3}
    assert all(r.detail["w_star"] is None and r.detail["rule"].startswith("none") for r in res)
    # t = 0: only d >= 1.
    res0 = certify_distance(c.hx, c.hz, 0, artifact_sink=sink, sectors=("Z",))
    assert bounds(res0) == {("distance_lower_bound", "Z"): 1, ("distance_upper_bound", "Z"): 3}
    assert_witnesses_reverified(res0, c.hx, c.hz, sink)


def test_interval_when_no_logical_through_2t(sink):
    """chain(3,7) has d = 6 > 2t = 4: the t = 2 run proves only d >= 5, and the upper bound is
    the best known logical (a canonical basis row), reported as an interval, never as exact."""
    c = bicycle_chain(3, 7)
    res = certify_distance(c.hx, c.hz, 2, artifact_sink=sink)
    b = bounds(res)
    for s in "XZ":
        assert b[("distance_lower_bound", s)] == 5               # 2t + 1, true d = 6
        assert b[("distance_upper_bound", s)] == _basis_min(c.hx, c.hz, s) >= 6
    assert_witnesses_reverified(res, c.hx, c.hz, sink)


# ------------------------------------------------------------------ control: ell = 2 declared d = 6
def test_chain_ell2_declared_d6_is_shown_to_be_d5(sink):
    c = bicycle_chain(2, 7)
    res = certify_distance(c.hx, c.hz, 3, artifact_sink=sink)
    b = bounds(res)
    for s in "XZ":
        assert b[("distance_upper_bound", s)] == 5           # witness proves d <= 5 < 6
        assert b[("distance_lower_bound", s)] == 5
    assert not any(r.status == "verified" and r.proposition.args["d"] >= 6 for r in res
                   if r.proposition.predicate == "distance_lower_bound")
    assert_witnesses_reverified(res, c.hx, c.hz, sink)
    # At t = 2 the run only proves d >= 2t+1 = 5: still no lower bound >= 6 can be produced.
    res2 = certify_distance(c.hx, c.hz, 2, artifact_sink=sink)
    b2 = bounds(res2)
    assert b2[("distance_lower_bound", "X")] == 5 and all(r.detail["w_star"] is None for r in res2)


# ------------------------------------------------------------------ control: budget / timeout
def _no_bounds(res):
    assert res and all(r.status == "unknown" for r in res)
    assert all(r.proposition.predicate == "distance_lower_bound" for r in res)


@pytest.mark.parametrize("budget,completion", [
    (DistanceBudget(max_patterns=100), "budget_exhausted"),              # preflight: M too large
    (DistanceBudget(max_memory_bytes=10_000), "budget_exhausted"),       # preflight: memory
    (DistanceBudget(max_pairs=10), "budget_exhausted"),                  # kernel pair cap mid-run
])
def test_budget_exhaustion_is_unknown_never_a_bound(budget, completion, sink):
    c = bicycle_chain(2, 7)
    res = certify_distance(c.hx, c.hz, 3, artifact_sink=sink, budget=budget)
    _no_bounds(res)
    for r in res:
        assert r.completion == completion and r.detail["outcome"] == completion
        rec = sink.get(r.artifacts[0])
        assert rec["completion"] == completion and rec["w_star"] is None
        assert "lower_bound" not in rec and "upper_bound" not in rec


def test_preflight_lower_bound_on_pair_work_refuses_early(sink):
    c = bicycle_chain(2, 7)
    pre = preflight(28, 4, gf2.rank(c.hz), 3, DistanceBudget(max_pairs=1))
    assert pre["pair_work_lower_bound"] <= 1540 <= pre["pair_work_upper_bound"]  # measured: 1540
    res = certify_distance(np.vstack([c.hx]), c.hz, 3, artifact_sink=sink,
                           budget=DistanceBudget(max_pairs=0))
    _no_bounds(res)


def test_timeout_is_unknown_never_a_bound(sink):
    params = {"l": 12, "m": 6, "a": [[3, 0], [0, 1], [0, 2]], "b": [[0, 3], [1, 0], [2, 0]]}
    _, hx, hz = build(Proposal(family="two_block_group_algebra", params=params), GrammarCaps())
    res = certify_distance(hx, hz, 3, artifact_sink=sink, sectors=("X",),
                           budget=DistanceBudget(max_seconds=0.02))
    _no_bounds(res)
    assert res[0].completion == "timeout"
    assert sink.get(res[0].artifacts[0])["completion"] == "timeout"


def test_reference_preflight_sizes():
    assert half_patterns(100, 4) == 4_087_976
    assert half_patterns(144, 6) == 11_642_049_421
    pre = preflight(144, 12, 66, 6, DistanceBudget())
    assert not pre["ok"] and pre["half_patterns"] == 11_642_049_421


def test_memory_estimate_is_conservative():
    params = {"l": 6, "m": 6, "a": [[3, 0], [0, 1], [0, 2]], "b": [[0, 3], [1, 0], [2, 0]]}
    _, hx, hz = build(Proposal(family="two_block_group_algebra", params=params), GrammarCaps())
    code = CSSCode(hx, hz)
    tracemalloc.start()
    try:
        sector_distance(code, "X", 3, max_patterns=10**6, max_pairs=10**8)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    est = memory_estimate(72, 12, gf2.rank(hz), half_patterns(72, 3))
    assert peak <= est


# ------------------------------------------------------------------ refusals
def test_non_css_and_k0_are_unknown(sink):
    hx = np.array([[1, 0, 1]], dtype=np.uint8)
    hz = np.array([[1, 1, 0]], dtype=np.uint8)
    res = certify_distance(hx, hz, 1, artifact_sink=sink)
    assert all(r.status == "unknown" and r.detail["outcome"] == "premise_refuted" for r in res)
    hx0 = np.array([[1, 1], [0, 1]], dtype=np.uint8)
    res0 = certify_distance(hx0, np.zeros((0, 2), np.uint8), 1, artifact_sink=sink)
    assert all(r.status == "unknown" and r.completion == "unsupported" for r in res0)


def test_argument_validation():
    c = steane()
    with pytest.raises(ValueError):
        certify_distance(c.hx, c.hz, -1, artifact_sink=lambda o: "0" * 64)
    with pytest.raises(ValueError):
        certify_distance(c.hx, c.hz, 8, artifact_sink=lambda o: "0" * 64)
    with pytest.raises(ValueError):
        certify_distance(c.hx, c.hz, 1, artifact_sink=lambda o: "0" * 64, sectors=("both",))
    with pytest.raises(ValueError):          # the sink must return a hash
        certify_distance(c.hx, c.hz, 1, artifact_sink=lambda o: "nope")


# ------------------------------------------------------------------ witnesses (§C3)
def test_valid_witness_gives_upper_bound_only(sink):
    c = bicycle_chain(2, 7)
    v = c.lx[0]
    (r,) = validate_logical_witness(c.hx, c.hz, "X", v, artifact_sink=sink)
    assert r.status == "verified" and r.proposition.predicate == "distance_upper_bound"
    assert r.proposition.args == {"sector": "X", "d": 7}
    assert sink.get(r.witnesses[0])["support"] == np.flatnonzero(v).tolist()


@pytest.mark.parametrize("make_vector,reason", [
    (lambda c: c.hx[0], "trivial"),                                     # a stabilizer
    (lambda c: np.eye(28, dtype=np.uint8)[0], "nonzero syndrome"),
    (lambda c: np.zeros(28, dtype=np.uint8), "zero vector"),
    (lambda c: np.ones(27, dtype=np.uint8), "length"),
])
def test_invalid_witness_is_unknown_never_refuting(make_vector, reason, sink):
    c = bicycle_chain(2, 7)
    (r,) = validate_logical_witness(c.hx, c.hz, "X", make_vector(c), artifact_sink=sink)
    assert r.status == "unknown" and r.detail["witness_valid"] is False
    assert reason in r.detail["reason"] and not r.witnesses


def test_non_binary_witness_is_malformed(sink):
    c = steane()
    (r,) = validate_logical_witness(c.hx, c.hz, "Z", [2, 0, 0, 0, 0, 0, 1], artifact_sink=sink)
    assert r.status == "unknown" and r.detail["outcome"] == "malformed_proposal"


# ------------------------------------------------------------------ BB fixtures (Bravyi et al.)
@pytest.mark.parametrize("l,m,n,k", [(6, 6, 72, 12), (12, 6, 144, 12)])
def test_bb_fixture_n_and_k(l, m, n, k, sink):
    params = {"l": l, "m": m, "a": [[3, 0], [0, 1], [0, 2]], "b": [[0, 3], [1, 0], [2, 0]]}
    p, _, _ = build(Proposal(family="two_block_group_algebra", params=params), GrammarCaps())
    preds = {r.proposition.predicate: r for r in analyze(p, artifact_sink=sink)}
    assert preds["css_valid"].status == "verified"
    assert preds["block_length_eq"].proposition.args == {"n": n}
    assert preds["dimension_eq"].proposition.args == {"k": k}
    assert preds["max_check_weight_eq"].proposition.args == {"w": 6}


def test_bb72_weight6_witness_fixture_validates_deterministically(sink):
    fx = json.loads((FIXTURES / "bb72_weight6_logicals.json").read_text())
    p, hx, hz = build(Proposal(family=fx["family"], params=fx["params"]), GrammarCaps())
    assert presentation_sha(p) == fx["presentation_sha"] and code_sha(hx, hz) == fx["code_sha"]
    for sector, support in fx["witness_support"].items():
        v = np.zeros(72, dtype=np.uint8)
        v[support] = 1
        (r,) = validate_logical_witness(hx, hz, sector, v, artifact_sink=sink)
        assert r.status == "verified" and r.proposition.args == {"sector": sector, "d": 6}
        assert r.proposition.subject == fx["code_sha"]


def test_bb72_distance_six_certified_by_complete_t3_enumeration(sink):
    params = {"l": 6, "m": 6, "a": [[3, 0], [0, 1], [0, 2]], "b": [[0, 3], [1, 0], [2, 0]]}
    _, hx, hz = build(Proposal(family="two_block_group_algebra", params=params), GrammarCaps())
    res = certify_distance(hx, hz, 3, artifact_sink=sink)
    assert set(bounds(res).values()) == {6} and len(bounds(res)) == 4
    assert_witnesses_reverified(res, hx, hz, sink)
