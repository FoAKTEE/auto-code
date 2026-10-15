"""Grammar families explicit_sparse and two_block_group_algebra (appendix §B3–§B4)."""
import numpy as np
import pytest

from zkqec.contracts import Presentation, Proposal
from zkqec.grammar import (MANIFESTS, GrammarCaps, MalformedProposal, UnsupportedFamily, build,
                           from_dense, reduce_polynomial, to_dense)
from zkqec.kernel.codes import bivariate_bicycle, shift_matrix

CAPS = GrammarCaps(max_n=200, max_rows=100, max_entries=2000, max_monomials=8, max_exponent=1000)
BB72 = {"l": 6, "m": 6, "a": [[3, 0], [0, 1], [0, 2]], "b": [[0, 3], [1, 0], [2, 0]]}


def es(hx, hz):
    return Proposal(family="explicit_sparse", params={"hx": hx, "hz": hz})


def tb(**params):
    return Proposal(family="two_block_group_algebra", params=params)


# ---------------------------------------------------------------- manifests
@pytest.mark.parametrize("name", ["explicit_sparse", "two_block_group_algebra"])
def test_manifests_declare_name_version_algebra_schema_caps(name):
    m = MANIFESTS[name]
    assert m["name"] == name and m["version"] and len(m["algebra"]) > 100
    assert m["param_schema"]["additionalProperties"] is False
    assert m["caps"]["checked_before_allocation"] is True


def test_two_block_manifest_states_wrap_cancellation_and_convention():
    algebra = MANIFESTS["two_block_group_algebra"]["algebra"]
    for phrase in ("Z_l x Z_m", "cancel", "H_X = [A | B]", "H_Z = [B^T | A^T]", "x^p y^q"):
        assert phrase in algebra


# ---------------------------------------------------------------- explicit_sparse
def test_explicit_sparse_builds_presentation_and_dense_arrays():
    p, hx, hz = build(es({"shape": [1, 4], "entries": [[0, 0], [0, 1]]},
                         {"shape": [1, 4], "entries": [[0, 0], [0, 1], [0, 2], [0, 3]]}), CAPS)
    assert isinstance(p, Presentation)
    assert hx.dtype == np.uint8 and hx.tolist() == [[1, 1, 0, 0]] and hz.tolist() == [[1, 1, 1, 1]]
    assert all(np.array_equal(a, b) for a, b in zip(to_dense(p), (hx, hz)))
    assert from_dense(hx, hz) == p                     # bijection: same canonical presentation


@pytest.mark.parametrize("hx", [
    {"shape": [1, 3], "entries": [[0, 1], [0, 1]]},           # duplicate (never XORed)
    {"shape": [1, 3], "entries": [[0, 2], [0, 1]]},           # unsorted
    {"shape": [1, 3], "entries": [[0, 3]]},                   # out of range
    {"shape": [1, 3], "entries": [[1, 0]]},
    {"shape": [1, 3], "entries": [[0, -1]]},
    {"shape": [1, 3], "entries": [[0, True]]},                # bool is not an int
    {"shape": [1, 3], "entries": [[0, 1.0]]},
    {"shape": [1, "3"], "entries": []},
    {"shape": [1, 3]},                                        # missing key
    {"shape": [1, 3], "entries": [], "n": 3},                 # extra key
    {"shape": [101, 3], "entries": []},                       # rows over cap
    {"shape": [1, 201], "entries": []},                       # cols over cap
    {"shape": [1, 3], "entries": [[0, 0]] * 2001},            # entries over cap
])
def test_explicit_sparse_rejections(hx):
    with pytest.raises(MalformedProposal):
        build(es(hx, {"shape": [1, 3], "entries": []}), CAPS)


def test_explicit_sparse_rejects_column_mismatch_and_extra_params():
    with pytest.raises(MalformedProposal):
        build(es({"shape": [1, 3], "entries": []}, {"shape": [1, 4], "entries": []}), CAPS)
    with pytest.raises(MalformedProposal):
        build(Proposal(family="explicit_sparse", params={"hx": {}, "hz": {}, "extra": 1}), CAPS)


def test_isolated_qubits_and_zero_rows_are_allowed_and_counted():
    p, hx, hz = build(es({"shape": [0, 5], "entries": []},
                         {"shape": [1, 5], "entries": [[0, 0], [0, 1]]}), CAPS)
    assert hx.shape == (0, 5) and hz.shape == (1, 5)


def test_program_family_is_unsupported_not_malformed():
    with pytest.raises(UnsupportedFamily) as e:
        build(Proposal(family="program", params={}), CAPS)
    assert e.value.outcome == "unsupported"


# ---------------------------------------------------------------- two_block_group_algebra
def test_two_block_matches_kernel_bivariate_bicycle():
    p, hx, hz = build(tb(**BB72), GrammarCaps())
    ref = bivariate_bicycle(6, 6, [(3, 0), (0, 1), (0, 2)], [(0, 3), (1, 0), (2, 0)])
    assert np.array_equal(hx, ref.hx) and np.array_equal(hz, ref.hz)
    a = shift_matrix(6, 6, [(3, 0), (0, 1), (0, 2)])
    b = shift_matrix(6, 6, [(0, 3), (1, 0), (2, 0)])
    assert np.array_equal(hx, np.hstack((a, b))) and np.array_equal(hz, np.hstack((b.T, a.T)))
    assert hx.shape == (36, 72)


def test_monomial_convention_x_shifts_the_first_coordinate():
    _, hx, _ = build(tb(l=2, m=3, a=[[1, 0]], b=[]), CAPS)
    # x^1: row i*m + j -> column ((i+1) mod l)*m + j
    for i in range(2):
        for j in range(3):
            assert hx[i * 3 + j, ((i + 1) % 2) * 3 + j] == 1
    assert hx[:, :6].sum() == 6 and not hx[:, 6:].any()


def test_torus_collision_control_wrap_cancellation():
    """Control 'torus collision': monomials that coincide mod (l, m) cancel over GF(2)."""
    assert reduce_polynomial(2, 3, [(0, 0), (2, 3)]) == []
    assert reduce_polynomial(3, 3, [(0, 0), (0, 1), (0, 4)]) == [(0, 0)]
    assert reduce_polynomial(3, 3, [(1, 0), (1, 0), (1, 0)]) == [(1, 0)]
    assert reduce_polynomial(3, 3, [(-1, -1)]) == [(2, 2)]
    _, hx, hz = build(tb(l=2, m=3, a=[[0, 0], [2, 3]], b=[[0, 0]]), CAPS)
    assert not hx[:, :6].any()                       # A = 1 + x^2 y^3 = 1 + 1 = 0
    # A naive monomial count would claim row weight 6; the declared algebra gives 4.
    _, hx, _ = build(tb(l=3, m=3, a=[[0, 0], [0, 1], [0, 4]], b=[[1, 0], [0, 2], [2, 2]]), CAPS)
    assert set(hx.sum(axis=1).tolist()) == {4}
    _, hx2, _ = build(tb(l=3, m=3, a=[[0, 0], [0, 1], [0, 2]], b=[[1, 0], [0, 2], [2, 2]]), CAPS)
    assert set(hx2.sum(axis=1).tolist()) == {6}      # paired positive: no collision
    ref = shift_matrix(3, 3, [(0, 0), (0, 1), (0, 4)])     # kernel XOR semantics agree
    assert np.array_equal(hx[:, :9], ref)


@pytest.mark.parametrize("params", [
    {"l": 6, "m": 6, "a": [[0, 0]]},                                    # missing b
    {"l": 6, "m": 6, "a": [], "b": [], "c": []},                       # extra key
    {"l": 0, "m": 6, "a": [], "b": []},
    {"l": True, "m": 6, "a": [], "b": []},
    {"l": 6.0, "m": 6, "a": [], "b": []},
    {"l": 6, "m": 6, "a": [[0]], "b": []},
    {"l": 6, "m": 6, "a": [[0, "1"]], "b": []},
    {"l": 6, "m": 6, "a": [[0, 1001]], "b": []},                        # exponent cap
    {"l": 6, "m": 6, "a": [[0, i] for i in range(9)], "b": []},       # monomial cap
    {"l": 10, "m": 11, "a": [], "b": []},                               # n = 220 > 200
    {"l": 64, "m": 64, "a": [], "b": []},                               # l*m > kernel ceiling
    {"l": 100, "m": 10**9, "a": [], "b": []},                           # must fail before allocating
])
def test_two_block_rejections_before_allocation(params):
    with pytest.raises(MalformedProposal):
        build(tb(**params), CAPS)


def test_entry_cap_counts_listed_monomials():
    caps = GrammarCaps(max_n=200, max_rows=100, max_entries=100, max_monomials=8)
    with pytest.raises(MalformedProposal):
        build(tb(l=6, m=6, a=[[0, 0], [0, 1]], b=[[1, 0]]), caps)   # 36*3 = 108 > 100


def test_caps_cannot_exceed_contract_ceilings():
    with pytest.raises(ValueError):
        GrammarCaps(max_n=5000)
    with pytest.raises(ValueError):
        GrammarCaps(max_monomials=0)
