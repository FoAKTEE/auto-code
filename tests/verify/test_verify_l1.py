"""L1 css_algebra: commutation, n, k (GF(2) ranks), presentation weights, canonical basis."""
import numpy as np
import pytest

from zkqec.contracts import LogicalBasis, Proposal, VerifierResult
from zkqec.grammar import GrammarCaps, build, from_dense
from zkqec.identity import code_sha, presentation_sha
from zkqec.kernel.codes import bicycle_chain, steane
from zkqec.verify import (REGISTRY, analyze, block_length_eq, canonical_basis, css_valid,
                          dimension_eq, max_check_weight_eq, max_qubit_degree_eq)


def by_pred(results):
    return {r.proposition.predicate: r for r in results}


def test_css_valid_verified_on_steane(sink):
    c = steane()
    p = from_dense(c.hx, c.hz)
    (r,) = css_valid(p, artifact_sink=sink)
    assert isinstance(r, VerifierResult) and r.status == "verified" and r.completion == "completed"
    assert r.proposition.subject == presentation_sha(p) and r.evidence_kind == "exact_algebra"


def test_css_valid_refuted_with_offending_row_pair_witness(sink):
    hx = np.array([[1, 1, 0], [0, 1, 1]], dtype=np.uint8)
    hz = np.array([[1, 0, 0], [1, 1, 1]], dtype=np.uint8)
    p = from_dense(hx, hz)
    (r,) = css_valid(p, artifact_sink=sink)
    assert r.status == "refuted" and len(r.witnesses) == 1
    w = sink.get(r.witnesses[0])
    assert w["kind"] == "css_commutation_violation_v1" and w["presentation"] == presentation_sha(p)
    i, j = w["hx_row"], w["hz_row"]
    assert int(hx[i] @ hz[j]) % 2 == 1 and w["overlap"] == np.flatnonzero(hx[i] & hz[j]).tolist()
    assert (i, j) == (0, 0) and r.detail["violating_pairs"] == 1


def test_non_css_presentation_gets_no_dimension_and_unknown_if_asked(sink):
    hx = np.array([[1, 0]], dtype=np.uint8)
    hz = np.array([[1, 1]], dtype=np.uint8)
    p = from_dense(hx, hz)
    preds = by_pred(analyze(p, artifact_sink=sink))
    assert preds["css_valid"].status == "refuted" and "dimension_eq" not in preds
    (r,) = dimension_eq(p, 0)
    assert r.status == "unknown" and r.detail["outcome"] == "premise_refuted"


def test_real_field_rank_control():
    """Control: GF(2) rank 2 but real rank 3 — k must come from GF(2) ranks."""
    hx = np.array([[1, 1, 0, 0], [0, 1, 1, 0], [1, 0, 1, 0]], dtype=np.uint8)
    hz = np.array([[1, 1, 1, 0]], dtype=np.uint8)
    assert np.linalg.matrix_rank(hx.astype(float)) == 3       # real-field rank would give k = 0
    p = from_dense(hx, hz)
    (good,) = dimension_eq(p, 1)
    (bad,) = dimension_eq(p, 0)
    assert good.status == "verified" and bad.status == "refuted"
    assert good.detail["rank_hx"] == 2 and good.proposition.subject == code_sha(hx, hz)
    assert good.proposition.subject_type == "code"


def test_block_length_and_presentation_weights():
    c = bicycle_chain(2, 7)
    p = from_dense(c.hx, c.hz)
    assert block_length_eq(p, 28)[0].status == "verified"
    assert block_length_eq(p, 27)[0].status == "refuted"
    assert max_check_weight_eq(p, 8)[0].status == "verified"
    assert max_check_weight_eq(p, 6)[0].status == "refuted"
    assert max_qubit_degree_eq(p, 8)[0].status == "verified"
    assert max_qubit_degree_eq(p, 7)[0].status == "refuted"
    for r in (block_length_eq(p, 28)[0], max_check_weight_eq(p, 8)[0]):
        assert r.proposition.subject_type == "presentation"


def test_torus_collision_control_weights_follow_the_built_matrices(sink):
    """Control: colliding monomials cancel, so a weight-6 claim is refuted (actual weight 4)."""
    params = {"l": 3, "m": 3, "a": [[0, 0], [0, 1], [0, 4]], "b": [[1, 0], [0, 2], [2, 2]]}
    p, hx, hz = build(Proposal(family="two_block_group_algebra", params=params), GrammarCaps())
    assert max_check_weight_eq(p, 6)[0].status == "refuted"
    assert max_check_weight_eq(p, 4)[0].status == "verified"
    assert by_pred(analyze(p, artifact_sink=sink))["css_valid"].status == "verified"


def test_isolated_qubits_count_in_n_and_k(sink):
    hx = np.array([[1, 1, 0]], dtype=np.uint8)
    hz = np.array([[1, 1, 0]], dtype=np.uint8)
    p = from_dense(hx, hz)
    preds = by_pred(analyze(p, artifact_sink=sink))
    assert preds["block_length_eq"].proposition.args == {"n": 3}
    assert preds["dimension_eq"].proposition.args == {"k": 1}
    assert preds["max_qubit_degree_eq"].proposition.args == {"q": 2}


def test_analyze_emits_all_predicates_and_stores_the_canonical_basis(sink):
    c = bicycle_chain(3, 7)
    p = from_dense(c.hx, c.hz)
    results = analyze(p, artifact_sink=sink)
    preds = by_pred(results)
    assert set(preds) == {"css_valid", "block_length_eq", "dimension_eq", "max_check_weight_eq",
                          "max_qubit_degree_eq"}
    assert all(r.status == "verified" for r in results)
    assert preds["dimension_eq"].proposition.args == {"k": 6}
    basis = LogicalBasis(**sink.get(preds["dimension_eq"].artifacts[0]))
    assert basis == canonical_basis(p) and basis.presentation == presentation_sha(p)
    lx, lz = np.array(basis.lx), np.array(basis.lz)
    assert lx.shape == (6, 42) and np.array_equal(lx @ lz.T % 2, np.eye(6, dtype=int))
    assert not (c.hz @ lx.T % 2).any() and not (c.hx @ lz.T % 2).any()


def test_zero_dimensional_code_has_empty_basis(sink):
    p = from_dense(np.array([[1, 1], [0, 1]], np.uint8), np.zeros((0, 2), np.uint8))
    preds = by_pred(analyze(p, artifact_sink=sink))
    assert preds["dimension_eq"].proposition.args == {"k": 0}
    assert canonical_basis(p).lx == ()


def test_subjects_separate_presentation_level_from_code_level(sink):
    """Feeds the §A3 transport control: on the [[4,1]] pair (same group, different generators)
    k is keyed to the shared code hash, weights to distinct presentation hashes."""
    hz = np.array([[1, 1, 1, 1]], dtype=np.uint8)
    pa = from_dense(np.array([[1, 1, 0, 0], [0, 0, 1, 1]], np.uint8), hz)
    pb = from_dense(np.array([[1, 1, 0, 0], [1, 1, 1, 1]], np.uint8), hz)
    a, b = by_pred(analyze(pa, artifact_sink=sink)), by_pred(analyze(pb, artifact_sink=sink))
    assert a["dimension_eq"].proposition == b["dimension_eq"].proposition      # k = 1, same code
    assert a["max_qubit_degree_eq"].proposition.args == {"q": 2}
    assert b["max_qubit_degree_eq"].proposition.args == {"q": 3}
    assert a["max_check_weight_eq"].proposition.subject != b["max_check_weight_eq"].proposition.subject


def test_registry_names_versions_and_entrypoints():
    vid, fns = REGISTRY["zkqec.verify.css_algebra"]
    assert vid.version == "1.0.0" and vid.level == "L1" and "dimension_eq" in vid.predicates
    assert fns["css_valid"] is css_valid
    assert {v.name for v, _ in REGISTRY.values()} == set(REGISTRY)
