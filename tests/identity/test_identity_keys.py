"""Identity keys: presentation hash, code (stabilizer-group) hash, §D1 presentation key."""
import numpy as np
import pytest

from zkqec.contracts import content_hash
from zkqec.grammar import from_dense
from zkqec.identity import code_sha, presentation_key, presentation_sha
from zkqec.kernel.codes import bicycle_chain, steane

HZ = np.array([[1, 1, 1, 1]], dtype=np.uint8)
HX_A = np.array([[1, 1, 0, 0], [0, 0, 1, 1]], dtype=np.uint8)
HX_B = np.array([[1, 1, 0, 0], [1, 1, 1, 1]], dtype=np.uint8)   # same row space as HX_A


def test_four_one_pair_same_group_different_presentations():
    """[[4,1]] example: equal code_sha, different presentation keys and presentation hashes."""
    assert code_sha(HX_A, HZ) == code_sha(HX_B, HZ)
    assert presentation_key(HX_A, HZ) != presentation_key(HX_B, HZ)
    assert presentation_sha(from_dense(HX_A, HZ)) != presentation_sha(from_dense(HX_B, HZ))


def test_presentation_sha_is_the_canonical_content_hash():
    p = from_dense(HX_A, HZ)
    assert presentation_sha(p) == content_hash(p)
    assert presentation_sha(from_dense(HX_A.copy(), HZ.copy())) == presentation_sha(p)


def _permuted(hx, hz, rng):
    q = rng.permutation(hx.shape[1])
    return hx[rng.permutation(hx.shape[0])][:, q], hz[rng.permutation(hz.shape[0])][:, q]


@pytest.mark.parametrize("make", [steane, lambda: bicycle_chain(2, 7)])
def test_presentation_key_invariant_under_qubit_and_check_permutations(make):
    c = make()
    rng = np.random.default_rng(7)
    key = presentation_key(c.hx, c.hz)
    for _ in range(5):
        hx, hz = _permuted(c.hx, c.hz, rng)
        assert presentation_key(hx, hz) == key


def test_code_sha_detects_different_groups_and_qubit_relabelling():
    c = bicycle_chain(2, 7)
    rng = np.random.default_rng(3)
    hx, hz = _permuted(c.hx, c.hz, rng)
    # A qubit relabelling is a *relation* (qubit_permutation), not group equality.
    assert code_sha(hx, hz) != code_sha(c.hx, c.hz)
    # Row permutations / row operations alone keep the group.
    assert code_sha(c.hx[::-1], c.hz) == code_sha(c.hx, c.hz)
    mixed = c.hx.copy()
    mixed[0] ^= mixed[1]
    assert code_sha(mixed, c.hz) == code_sha(c.hx, c.hz)


def test_xz_swap_is_not_identity_for_any_key():
    hx = np.array([[1, 1, 1, 1]], dtype=np.uint8)
    hz = np.array([[1, 1, 0, 0], [0, 0, 1, 1]], dtype=np.uint8)
    assert code_sha(hx, hz) != code_sha(hz, hx)
    assert presentation_key(hx, hz) != presentation_key(hz, hx)


def test_color_cell_order_matters_even_with_equal_row_counts():
    a, b = np.array([[1, 1, 0]], np.uint8), np.array([[1, 1, 1]], np.uint8)
    assert presentation_key(a, b) != presentation_key(b, a)


def test_presentation_key_agrees_with_networkx_colored_isomorphism():
    nx = pytest.importorskip("networkx")

    def graph(hx, hz):
        g = nx.Graph()
        g.add_nodes_from((("q", i), {"c": 0}) for i in range(hx.shape[1]))
        for tag, color, h in (("x", 1, hx), ("z", 2, hz)):
            g.add_nodes_from(((tag, i), {"c": color}) for i in range(h.shape[0]))
            g.add_edges_from(((tag, int(i)), ("q", int(j))) for i, j in np.argwhere(h))
        return g

    rng = np.random.default_rng(1)
    seen = {True: 0, False: 0}
    for t in range(200):
        hx, hz = (rng.integers(0, 2, (2, 6)).astype(np.uint8) for _ in range(2))
        hx2, hz2 = _permuted(hx, hz, rng)
        if t % 2:                                     # perturb half of the copies
            hx2 = hx2.copy()
            hx2[0, int(rng.integers(6))] ^= 1
        iso = nx.is_isomorphic(graph(hx, hz), graph(hx2, hz2),
                               node_match=lambda u, v: u["c"] == v["c"])
        assert iso == (presentation_key(hx, hz) == presentation_key(hx2, hz2))
        seen[iso] += 1
    assert min(seen.values()) >= 50


def test_presentation_key_distinguishes_extra_rows_and_isolated_qubits():
    base = presentation_key(HX_A, HZ)
    assert presentation_key(np.vstack((HX_A, HX_A[:1])), HZ) != base     # duplicate generator
    pad = (lambda a: np.hstack((a, np.zeros((a.shape[0], 1), np.uint8))))
    assert presentation_key(pad(HX_A), pad(HZ)) != base                   # isolated qubit
    assert code_sha(pad(HX_A), pad(HZ)) != code_sha(HX_A, HZ)


def test_identity_rejects_non_binary_input():
    with pytest.raises(ValueError):
        code_sha(np.array([[2, 0]]), np.array([[1, 1]]))
    with pytest.raises(ValueError):
        presentation_key(np.array([[1, 0]]), np.array([[1, 1, 0]]))
