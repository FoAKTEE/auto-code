"""Independent dense cross-check of L3 (test oracle only; appendix §F twins come later in M2).

On many small codes (n <= 14), bases (canonical and randomized), operations (permutations,
transversal and random per-qubit Cliffords, sampled automorphisms) and targets (the computed
tableau, sign-flipped and restricted variants, standard gates), the exact L3 verdict must agree
with the dense state-vector oracle: ``verified`` iff V† U V equals the target up to global phase.
"""
from collections import Counter

import numpy as np
import pytest

from _verify_oracle import oracle
from zkqec.contracts import LogicalBasis, PhysicalOperation, SignedPauliImages
from zkqec.contracts.design import ONE_QUBIT_CLIFFORDS
from zkqec.grammar import from_dense
from zkqec.identity import presentation_sha
from zkqec.kernel import gf2
from zkqec.kernel.codes import CSSCode, bicycle_chain, steane
from zkqec.kernel.structure import permutation_action
from zkqec.verify import compute_logical_action, implements

U8 = (lambda rows: np.array(rows, dtype=np.uint8))
EXT_HAMMING = U8([[0, 0, 0, 0, 1, 1, 1, 1], [0, 0, 1, 1, 0, 0, 1, 1], [0, 1, 0, 1, 0, 1, 0, 1]])
FIXED_CODES = {
    "steane": (steane().hx, steane().hz),
    "4_2_2": (U8([[1, 1, 1, 1]]), U8([[1, 1, 1, 1]])),
    "6_4_2": (U8([[1] * 6]), U8([[1] * 6])),
    "6_2_2": (U8([[1, 1, 1, 1, 0, 0], [0, 0, 1, 1, 1, 1]]),) * 2,
    "8_2_hamming": (EXT_HAMMING, EXT_HAMMING),
    "8_3_2": (U8([[1] * 8]), np.vstack((EXT_HAMMING, U8([[1] * 8])))),
}


def random_css(rng):
    while True:
        n = int(rng.integers(3, 9))
        hx = rng.integers(0, 2, (int(rng.integers(1, n - 1)), n)).astype(np.uint8)
        null = gf2.nullspace(hx)
        if not len(null):
            continue
        hz = (rng.integers(0, 2, (int(rng.integers(1, len(null) + 1)), len(null))) @ null % 2)
        hz = hz.astype(np.uint8)
        if n - gf2.rank(hx) - gf2.rank(hz) >= 1:
            return hx, hz


def random_basis(code, rng):
    """Random paired basis: lx' = A lx + stabilizers, lz' = A^{-T} lz + stabilizers."""
    k = code.k
    while True:
        a = rng.integers(0, 2, (k, k)).astype(np.uint8)
        if gf2.rank(a) == k:
            break
    lx = a @ code.lx % 2
    lz = gf2.inverse(a).T @ code.lz % 2
    if code.hx.shape[0]:
        lx = (lx + rng.integers(0, 2, (k, code.hx.shape[0])) @ code.hx) % 2
    if code.hz.shape[0]:
        lz = (lz + rng.integers(0, 2, (k, code.hz.shape[0])) @ code.hz) % 2
    return lx.astype(np.uint8), lz.astype(np.uint8)


def operations(code, rng):
    n = code.n
    ops = [(None, (g,) * n) for g in rng.choice(ONE_QUBIT_CLIFFORDS, 3, replace=False)]
    ops.append((tuple(int(v) for v in rng.permutation(n)), None))
    ops.append((tuple(int(v) for v in rng.permutation(n)),
                tuple(str(g) for g in rng.choice(ONE_QUBIT_CLIFFORDS, n))))
    autos = [tuple(int(v) for v in p) for p in (rng.permutation(n) for _ in range(40))
             if permutation_action(code, [int(v) for v in p])["preserves_code"]]
    for perm in autos[:2]:
        ops.append((perm, None))
        ops.append((perm, (str(rng.choice(ONE_QUBIT_CLIFFORDS)),) * n))
    return ops


def unit(k, c, j):
    return "+" + "".join(c if i == j else "I" for i in range(k))


def targets(tab, k, rng):
    lq_all = tuple(range(k))
    out = [(SignedPauliImages(x_images=tuple(unit(k, "X", j) for j in range(k)),
                              z_images=tuple(unit(k, "Z", j) for j in range(k))), lq_all),
           (SignedPauliImages(x_images=tuple("-" + unit(k, "Y", j)[1:] for j in range(k)),
                              z_images=tuple(unit(k, "Z", j) for j in range(k))), lq_all),
           (SignedPauliImages(x_images=tuple(unit(k, "Z", j) for j in range(k)),
                              z_images=tuple(unit(k, "X", j) for j in range(k))), lq_all)]
    if not tab.stabilizers_preserved:
        return out
    xs, zs = list(tab.x_images), list(tab.z_images)
    out.append((SignedPauliImages(x_images=tuple(xs), z_images=tuple(zs)), lq_all))
    flip = int(rng.integers(2 * k))
    fx, fz = xs[:], zs[:]
    seq = fx if flip < k else fz
    j = flip % k
    seq[j] = ("-" if seq[j][0] == "+" else "+") + seq[j][1:]
    out.append((SignedPauliImages(x_images=tuple(fx), z_images=tuple(fz)), lq_all))
    lq = tuple(int(v) for v in rng.permutation(k)[:int(rng.integers(1, k + 1))])
    local = all(xs[j] == unit(k, "X", j) and zs[j] == unit(k, "Z", j)
                for j in range(k) if j not in lq)
    local &= all(s[1 + i] == "I" for j in lq for s in (xs[j], zs[j]) for i in range(k)
                 if i not in lq)
    if local:
        restrict = (lambda s: s[0] + "".join(s[1 + q] for q in lq))
        rx, rz = [restrict(xs[j]) for j in lq], [restrict(zs[j]) for j in lq]
        out.append((SignedPauliImages(x_images=tuple(rx), z_images=tuple(rz)), lq))
        rz[0] = ("-" if rz[0][0] == "+" else "+") + rz[0][1:]           # partial, wrong sign
        out.append((SignedPauliImages(x_images=tuple(rx), z_images=tuple(rz)), lq))
    return out


def all_codes(rng):
    codes = [(name, hx, hz) for name, (hx, hz) in FIXED_CODES.items()]
    codes += [(f"random{i}", *random_css(rng)) for i in range(14)]
    return codes


@pytest.mark.parametrize("seed", [0, 1])
def test_l3_agrees_with_dense_oracle(seed, sink):
    rng = np.random.default_rng(seed)
    stats = Counter()
    for name, hx, hz in all_codes(rng):
        code = CSSCode(hx, hz)
        p = from_dense(hx, hz)
        for lx, lz in ((code.lx, code.lz), random_basis(code, rng)):
            basis = LogicalBasis(presentation=presentation_sha(p),
                                 lx=tuple(map(tuple, lx.tolist())), lz=tuple(map(tuple, lz.tolist())))
            for perm, gates in operations(code, rng):
                op = PhysicalOperation(n=code.n, permutation=perm, local_cliffords=gates)
                tab = compute_logical_action(p, basis, op)
                for target, lq in targets(tab, code.k, rng):
                    (r,) = implements(p, basis, op, target, lq, artifact_sink=sink)
                    o = oracle(hx, hz, lx, lz, perm, gates, target.x_images, target.z_images, lq,
                               check_full_unitary=code.n <= 5)
                    assert r.detail["stabilizers_preserved"] == o["preserved"], (name, perm, gates)
                    assert (r.status == "verified") == o["implements"], (name, perm, gates, target)
                    stats[r.status] += 1
                    stats["preserved"] += o["preserved"]
    assert stats["verified"] >= 60 and stats["refuted"] >= 200 and stats["preserved"] >= 200


def test_l3_agrees_with_dense_oracle_on_chain_1_7(sink):
    c = bicycle_chain(1, 7)
    p = from_dense(c.hx, c.hz)
    basis = LogicalBasis(presentation=presentation_sha(p), lx=tuple(map(tuple, c.lx.tolist())),
                         lz=tuple(map(tuple, c.lz.tolist())))
    rng = np.random.default_rng(3)
    seen = Counter()
    shift = tuple((j + 1) % 7 + 7 * h for h in range(2) for j in range(7))   # y-translation
    for perm, gates in [(None, (g,) * 14) for g in ONE_QUBIT_CLIFFORDS] + [(shift, None),
                                                                         (shift, ("S",) * 14)]:
        op = PhysicalOperation(n=14, permutation=perm, local_cliffords=gates)
        tab = compute_logical_action(p, basis, op)
        for target, lq in targets(tab, 2, rng):
            (r,) = implements(p, basis, op, target, lq, artifact_sink=sink)
            o = oracle(c.hx, c.hz, c.lx, c.lz, perm, gates, target.x_images, target.z_images, lq)
            assert (r.status == "verified") == o["implements"]
            seen[r.status] += 1
    assert seen["verified"] >= 10 and seen["refuted"] >= 10
