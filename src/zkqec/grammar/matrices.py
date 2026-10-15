"""Exact conversion between contract presentations and dense GF(2) arrays.

Because :class:`~zkqec.contracts.SparseMatrix` requires sorted unique coordinates, the map
``Presentation <-> (hx, hz)`` is a bijection, so a presentation hash computed from either side is
the same.
"""
from __future__ import annotations

import numpy as np

from ..contracts import Presentation, SparseMatrix


def sparse_to_dense(m: SparseMatrix) -> np.ndarray:
    rows, cols = m.shape
    out = np.zeros((rows, cols), dtype=np.uint8)
    if m.entries:
        idx = np.asarray(m.entries, dtype=np.int64)
        out[idx[:, 0], idx[:, 1]] = 1
    return out


def dense_to_sparse(a: np.ndarray) -> SparseMatrix:
    a = np.asarray(a)
    if a.ndim != 2 or not np.all((a == 0) | (a == 1)):
        raise ValueError("expected a binary two-dimensional matrix")
    coords = tuple((int(i), int(j)) for i, j in np.argwhere(a))   # row-major: sorted, unique
    return SparseMatrix(shape=(int(a.shape[0]), int(a.shape[1])), entries=coords)


def to_dense(p: Presentation) -> tuple[np.ndarray, np.ndarray]:
    """``(hx, hz)`` as fresh uint8 arrays."""
    return sparse_to_dense(p.hx), sparse_to_dense(p.hz)


def from_dense(hx: np.ndarray, hz: np.ndarray) -> Presentation:
    return Presentation(hx=dense_to_sparse(hx), hz=dense_to_sparse(hz))
