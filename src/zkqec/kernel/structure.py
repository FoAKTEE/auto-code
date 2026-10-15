"""Logical-basis, permutation and overlap verification."""
from __future__ import annotations
import numpy as np
from .codes import CSSCode
from . import gf2


def structural_report(code: CSSCode) -> dict:
    common = np.array_equal(code.lx, code.lz)
    self_dual = (gf2.rank(np.vstack((code.hx, code.hz))) == gf2.rank(code.hx)
                 == gf2.rank(code.hz))
    doubly_even = self_dual and bool(np.all(code.hx.sum(axis=1) % 4 == 0))
    disjoint_z = bool(np.all(code.lz.sum(axis=0) <= 1))
    disjoint_x = bool(np.all(code.lx.sum(axis=0) <= 1))
    action = None
    if self_dual and common and doubly_even:
        action = ["S" if int(w) % 4 == 1 else "S_dagger" for w in code.lx.sum(axis=1)]
    return {"n": code.n, "k": code.k, "encoding_rate": code.k/code.n,
            "rank_hx": gf2.rank(code.hx), "rank_hz": gf2.rank(code.hz),
            "max_check_weight": int(max(code.hx.sum(axis=1).max(initial=0),
                                        code.hz.sum(axis=1).max(initial=0))),
            "max_total_degree": int((code.hx.sum(axis=0)+code.hz.sum(axis=0)).max()),
            "self_dual_rowspace": self_dual, "common_logical_basis": common,
            "doubly_even_stabilizers": doubly_even,
            "disjoint_logical_Z": disjoint_z, "disjoint_logical_X": disjoint_x,
            "global_H_is_pairwise_logical_H": self_dual and common,
            "global_S_actions": action,
            "logical_Z_weights": list(map(int, code.lz.sum(axis=1)))}


def permute_columns(a: np.ndarray, permutation: list[int]) -> np.ndarray:
    """Convention: old physical index q moves to permutation[q]."""
    n = a.shape[1]
    if sorted(permutation) != list(range(n)):
        raise ValueError("Not a physical-qubit permutation.")
    out = np.zeros_like(a)
    out[:, permutation] = a
    return out


def permutation_action(code: CSSCode, permutation: list[int]) -> dict:
    hx = permute_columns(code.hx, permutation)
    hz = permute_columns(code.hz, permutation)
    preserved = (all(gf2.in_rowspace(r, code.hx) for r in hx)
                 and all(gf2.in_rowspace(r, code.hz) for r in hz))
    if not preserved:
        return {"preserves_code": False, "logical_X": None, "logical_Z": None}
    x = permute_columns(code.lx, permutation) @ code.lz.T % 2
    z = permute_columns(code.lz, permutation) @ code.lx.T % 2
    # Pure CSS X/Z strings, a physical permutation and +1 stabilizers introduce
    # no phases. General Clifford transformations require phase-aware tableaus.
    return {"preserves_code": True, "logical_X": x.tolist(), "logical_Z": z.tolist(),
            "phase_scope": "pure_CSS_permutation_only"}


def chain_shift(ell: int, m: int, dx: int = 1, dy: int = 0) -> list[int]:
    return [half*ell*m+((i+dx) % ell)*m+(j+dy) % m
            for half in range(2) for i in range(ell) for j in range(m)]


def overlap_graph(representatives: np.ndarray) -> list[set[int]]:
    a = gf2.matrix(representatives)
    # Integer multiplication must not wrap at weight 256.
    overlap = a.astype(np.int64) @ a.astype(np.int64).T
    return [{j for j in range(len(a)) if j != i and overlap[i, j] > 0}
            for i in range(len(a))]


def dsatur_coloring(graph: list[set[int]]) -> list[int]:
    """Deterministic greedy DSATUR: a feasible coloring, NOT chromatic optimality."""
    n = len(graph)
    if any(i in neighbors or any(j < 0 or j >= n or i not in graph[j] for j in neighbors)
           for i, neighbors in enumerate(graph)):
        raise ValueError("Expected a simple undirected graph.")
    colors = [-1]*n
    while -1 in colors:
        left = [i for i, c in enumerate(colors) if c < 0]
        i = max(left, key=lambda v: (len({colors[j] for j in graph[v] if colors[j] >= 0}),
                                     len(graph[v]), -v))
        used = {colors[j] for j in graph[i]}
        c = 0
        while c in used:
            c += 1
        colors[i] = c
    return colors


def injection_batches(representatives: np.ndarray) -> dict:
    colors = dsatur_coloring(overlap_graph(representatives))
    return {"colors": colors, "feasible_batch_count": max(colors, default=-1)+1,
            "optimal": False, "scope": "support_conflicts_only_not_gadget_correctness"}
