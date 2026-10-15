"""Shared plumbing for registered verifiers.

A verifier returns ``list[VerifierResult]`` and never names its issuer: the admission service stamps
issuer, version and execution id from its own execution record. Artifacts (witnesses, records,
tableaux) are handed to the caller-provided ``artifact_sink(obj) -> sha256 hex``; results cite them
only by the hash the sink returns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from ..contracts import Proposition, VerifierResult

ArtifactSink = Callable[[Any], str]
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

MALFORMED = "malformed_proposal"


@dataclass(frozen=True)
class VerifierId:
    name: str
    version: str
    level: str
    predicates: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "level": self.level,
                "predicates": list(self.predicates)}


def store(sink: ArtifactSink | None, obj: Any) -> str:
    if sink is None:
        raise ValueError("this verifier stores artifacts: pass artifact_sink")
    sha = sink(obj)
    if not isinstance(sha, str) or not _HEX64.match(sha):
        raise ValueError("artifact_sink must return a sha256 hex digest")
    return sha


def proposition(predicate: str, subject_type: str, subject: str, **args: Any) -> Proposition:
    return Proposition(predicate=predicate, subject_type=subject_type, subject=subject, args=args)


def result(prop: Proposition, status: str, method: str, *, completion: str = "completed",
           witnesses: tuple[str, ...] = (), artifacts: tuple[str, ...] = (),
           detail: dict[str, Any] | None = None) -> VerifierResult:
    return VerifierResult(proposition=prop, status=status, evidence_kind="exact_algebra",
                          method=method, completion=completion, witnesses=tuple(witnesses),
                          artifacts=tuple(artifacts), detail=dict(detail or {}))


def refusal(prop: Proposition, method: str, reason: str, *, outcome: str = MALFORMED,
            completion: str = "completed", artifacts: tuple[str, ...] = ()) -> VerifierResult:
    """An input the verifier will not judge: status ``unknown``, typed outcome in ``detail``."""
    return result(prop, "unknown", method, completion=completion, artifacts=artifacts,
                  detail={"outcome": outcome, "reason": reason})


def binary_matrix(a: Any, name: str) -> np.ndarray:
    arr = np.asarray(a)
    if arr.ndim != 2 or arr.size and not np.all((arr == 0) | (arr == 1)):
        raise ValueError(f"{name} must be a binary two-dimensional matrix")
    return arr.astype(np.uint8)


def css_violations(hx: np.ndarray, hz: np.ndarray) -> np.ndarray:
    """Index pairs (i, j) with |hx_i AND hz_j| odd, in row-major order (int64, no overflow)."""
    overlap = hx.astype(np.int64) @ hz.astype(np.int64).T
    return np.argwhere(overlap % 2 == 1)


class RowSpace:
    """Exact GF(2) membership/reduction against a fixed row space (canonical RREF pivots)."""

    def __init__(self, rows: np.ndarray, n: int):
        from ..kernel import gf2
        rows = np.asarray(rows, dtype=np.uint8).reshape(-1, n)
        reduced, pivots = gf2.rref(rows) if rows.shape[0] else (rows, ())
        self.basis = reduced[:len(pivots)].copy()
        self.pivots = tuple(pivots)
        self.n = n

    @property
    def rank(self) -> int:
        return len(self.pivots)

    def residual(self, vectors: np.ndarray) -> np.ndarray:
        """Reduce each row modulo the space; a row is in the space iff its residual is zero."""
        v = np.array(vectors, dtype=np.uint8).reshape(-1, self.n)
        for row, p in zip(self.basis, self.pivots):
            hit = v[:, p] == 1
            if hit.any():
                v[hit] ^= row
        return v

    def contains(self, vectors: np.ndarray) -> np.ndarray:
        return ~self.residual(vectors).any(axis=1)
