"""Small, transparent binary linear algebra. All arithmetic is over GF(2)."""
from __future__ import annotations
import numpy as np
from numpy.typing import ArrayLike, NDArray

Binary = NDArray[np.uint8]


def matrix(value: ArrayLike) -> Binary:
    a = np.asarray(value)
    if a.ndim != 2 or not np.all((a == 0) | (a == 1)):
        raise ValueError("Expected a binary, two-dimensional matrix.")
    return np.array(a, dtype=np.uint8, copy=True)


def rref(value: ArrayLike) -> tuple[Binary, tuple[int, ...]]:
    a = matrix(value)
    pivots: list[int] = []
    r = 0
    for c in range(a.shape[1]):
        found = np.flatnonzero(a[r:, c])
        if not len(found):
            continue
        j = r + int(found[0])
        a[[r, j]] = a[[j, r]]
        rows = np.flatnonzero(a[:, c])
        rows = rows[rows != r]
        a[rows] ^= a[r]
        pivots.append(c)
        r += 1
        if r == a.shape[0]:
            break
    return a, tuple(pivots)


def rank(value: ArrayLike) -> int:
    return len(rref(value)[1])


def row_basis(value: ArrayLike) -> Binary:
    reduced, pivots = rref(value)
    return reduced[:len(pivots)].copy()


def nullspace(value: ArrayLike) -> Binary:
    a, pivots = rref(value)
    free = [c for c in range(a.shape[1]) if c not in pivots]
    out = np.zeros((len(free), a.shape[1]), dtype=np.uint8)
    for i, f in enumerate(free):
        out[i, f] = 1
        for j, p in enumerate(pivots):
            out[i, p] = a[j, f]
    return out


def in_rowspace(vector: ArrayLike, basis: ArrayLike) -> bool:
    b = matrix(basis)
    v = matrix(np.asarray(vector).reshape(1, -1))
    if v.shape[1] != b.shape[1]:
        raise ValueError("Dimension mismatch.")
    return rank(np.vstack((b, v))) == rank(b)


def quotient_basis(space: ArrayLike, subspace: ArrayLike) -> Binary:
    s, b = matrix(space), row_basis(subspace)
    if s.shape[1] != b.shape[1]:
        raise ValueError("Dimension mismatch.")
    if any(not in_rowspace(v, s) for v in b):
        raise ValueError("The supplied subspace is not contained in the space.")
    chosen = []
    for v in row_basis(s):
        if not in_rowspace(v, b):
            chosen.append(v.copy())
            b = np.vstack((b, v))
    return np.array(chosen, dtype=np.uint8).reshape(-1, s.shape[1])


def inverse(value: ArrayLike) -> Binary:
    a = matrix(value)
    n, m = a.shape
    if n != m:
        raise ValueError("Only square matrices have an inverse.")
    aug = np.hstack((a, np.eye(n, dtype=np.uint8)))
    reduced, pivots = rref(aug)
    if pivots[:n] != tuple(range(n)):
        raise ValueError("Singular binary matrix.")
    return reduced[:, n:]


def canonical_logicals(hx: ArrayLike, hz: ArrayLike) -> tuple[Binary, Binary]:
    hx, hz = matrix(hx), matrix(hz)
    if hx.shape[1] != hz.shape[1] or np.any(hx @ hz.T % 2):
        raise ValueError("Invalid CSS check matrices.")
    lx = quotient_basis(nullspace(hz), hx)
    lz = quotient_basis(nullspace(hx), hz)
    pairing = lx @ lz.T % 2
    lz = inverse(pairing).T @ lz % 2
    return lx, lz.astype(np.uint8)


def as_mask(row: ArrayLike) -> int:
    a = np.asarray(row)
    if a.ndim != 1 or not np.all((a == 0) | (a == 1)):
        raise ValueError("Expected a binary vector.")
    return sum(int(v) << i for i, v in enumerate(a))
