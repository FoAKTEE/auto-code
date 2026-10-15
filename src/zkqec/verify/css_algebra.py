"""L1 ``css_algebra``: commutation, n, k, presentation weights, canonical logical basis.

Everything is exact GF(2) / integer arithmetic. Predicates and subjects (appendix §A1):

* ``css_valid(presentation)`` — ``H_X H_Z^T = 0`` over GF(2). Refuted with a stored witness naming
  the first offending row pair (and the count of all offending pairs).
* ``block_length_eq(presentation, n)`` — number of columns (isolated qubits count).
* ``dimension_eq(code, k)`` — ``k = n - rank_GF2(H_X) - rank_GF2(H_Z)``; only for a CSS-valid
  presentation (otherwise ``unknown``: a refuted premise leaves derived claims unknown).
* ``max_check_weight_eq(presentation, w)`` — max row weight over H_X and H_Z (0 if no rows).
* ``max_qubit_degree_eq(presentation, q)`` — max over qubits of the number of X- and Z-checks
  touching it (Tanner-graph degree, as kernel ``structural_report.max_total_degree``).

The last two are presentation-level: they never transfer across presentations (§A3).
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ..contracts import LogicalBasis, Presentation, VerifierResult
from ..grammar.matrices import to_dense
from ..identity import code_sha, presentation_sha
from ..kernel import gf2
from ..kernel.codes import CSSCode
from .common import (ArtifactSink, VerifierId, css_violations, proposition, refusal, result,
                     store)

VERIFIER = VerifierId("zkqec.verify.css_algebra", "1.0.0", "L1",
                      ("css_valid", "block_length_eq", "dimension_eq", "max_check_weight_eq",
                       "max_qubit_degree_eq"))
M_COMMUTE = "gf2_commutation_check_v1"
M_SHAPE = "matrix_shape_v1"
M_RANK = "gf2_rank_dimension_v1"
M_WEIGHT = "integer_weight_count_v1"


def _dense(p: Presentation) -> tuple[np.ndarray, np.ndarray]:
    if not isinstance(p, Presentation):
        raise TypeError("expected a zkqec.contracts.Presentation")
    return to_dense(p)


def _status(ok: bool) -> str:
    return "verified" if ok else "refuted"


def _stats(hx: np.ndarray, hz: np.ndarray) -> dict[str, int]:
    rows = [r for h in (hx, hz) for r in h.astype(np.int64).sum(axis=1)]
    degree = hx.astype(np.int64).sum(axis=0) + hz.astype(np.int64).sum(axis=0)
    return {"n": int(hx.shape[1]), "max_check_weight": int(max(rows, default=0)),
            "max_qubit_degree": int(degree.max(initial=0))}


def css_valid(presentation: Presentation, *, artifact_sink: ArtifactSink) -> list[VerifierResult]:
    hx, hz = _dense(presentation)
    psha = presentation_sha(presentation)
    prop = proposition("css_valid", "presentation", psha)
    bad = css_violations(hx, hz)
    if not len(bad):
        return [result(prop, "verified", M_COMMUTE, detail={"rows_hx": int(hx.shape[0]),
                                                            "rows_hz": int(hz.shape[0])})]
    i, j = (int(v) for v in bad[0])
    witness = {"kind": "css_commutation_violation_v1", "presentation": psha, "hx_row": i,
               "hz_row": j, "overlap": np.flatnonzero(hx[i] & hz[j]).tolist()}
    w = store(artifact_sink, witness)
    return [result(prop, "refuted", M_COMMUTE, witnesses=(w,),
                   detail={"violating_pairs": int(len(bad)), "first_pair": [i, j]})]


def block_length_eq(presentation: Presentation, n: int) -> list[VerifierResult]:
    hx, _ = _dense(presentation)
    prop = proposition("block_length_eq", "presentation", presentation_sha(presentation), n=n)
    actual = int(hx.shape[1])
    return [result(prop, _status(actual == n), M_SHAPE, detail={"computed_n": actual})]


def _ranks(hx: np.ndarray, hz: np.ndarray) -> tuple[int, int, int]:
    rx, rz = gf2.rank(hx), gf2.rank(hz)
    return rx, rz, int(hx.shape[1]) - rx - rz


def dimension_eq(presentation: Presentation, k: int, *,
                 basis_ref: str | None = None) -> list[VerifierResult]:
    hx, hz = _dense(presentation)
    prop = proposition("dimension_eq", "code", code_sha(hx, hz), k=k)
    if len(css_violations(hx, hz)):
        return [refusal(prop, M_RANK, "presentation is not CSS-valid; k is undefined",
                        outcome="premise_refuted")]
    rx, rz, actual = _ranks(hx, hz)
    return [result(prop, _status(actual == k), M_RANK, artifacts=(basis_ref,) if basis_ref else (),
                   detail={"computed_k": actual, "rank_hx": rx, "rank_hz": rz,
                           "presentation": presentation_sha(presentation)})]


def max_check_weight_eq(presentation: Presentation, w: int) -> list[VerifierResult]:
    hx, hz = _dense(presentation)
    prop = proposition("max_check_weight_eq", "presentation", presentation_sha(presentation), w=w)
    actual = _stats(hx, hz)["max_check_weight"]
    return [result(prop, _status(actual == w), M_WEIGHT, detail={"computed_w": actual})]


def max_qubit_degree_eq(presentation: Presentation, q: int) -> list[VerifierResult]:
    hx, hz = _dense(presentation)
    prop = proposition("max_qubit_degree_eq", "presentation", presentation_sha(presentation), q=q)
    actual = _stats(hx, hz)["max_qubit_degree"]
    return [result(prop, _status(actual == q), M_WEIGHT, detail={"computed_q": actual})]


def canonical_basis(presentation: Presentation) -> LogicalBasis:
    """Kernel canonical logical basis (``gf2.canonical_logicals``): L_X L_Z^T = I, k rows each.

    Raises ``ValueError`` for a presentation that is not CSS-valid.
    """
    hx, hz = _dense(presentation)
    code = CSSCode(hx, hz)          # validates commutation; computes the canonical pairing
    to_rows = (lambda a: tuple(tuple(int(v) for v in r) for r in a))
    return LogicalBasis(presentation=presentation_sha(presentation), lx=to_rows(code.lx),
                        lz=to_rows(code.lz))


def analyze(presentation: Presentation, *, artifact_sink: ArtifactSink) -> list[VerifierResult]:
    """Every L1 predicate at its computed value (all ``verified``), plus ``css_valid``.

    For a CSS-valid presentation the canonical :class:`LogicalBasis` is stored and cited in the
    ``dimension_eq`` result's artifacts.
    """
    hx, hz = _dense(presentation)
    stats = _stats(hx, hz)
    out = css_valid(presentation, artifact_sink=artifact_sink)
    out += block_length_eq(presentation, stats["n"])
    out += max_check_weight_eq(presentation, stats["max_check_weight"])
    out += max_qubit_degree_eq(presentation, stats["max_qubit_degree"])
    if out[0].status == "verified":
        basis_ref = store(artifact_sink, canonical_basis(presentation))
        out += dimension_eq(presentation, _ranks(hx, hz)[2], basis_ref=basis_ref)
    return out


def summary(presentation: Presentation) -> dict[str, Any]:
    """Plain computed values (no authority) for logging and twins."""
    hx, hz = _dense(presentation)
    valid = not len(css_violations(hx, hz))
    rx, rz, k = _ranks(hx, hz)
    return {**_stats(hx, hz), "css_valid": valid, "rank_hx": rx, "rank_hz": rz,
            "k": k if valid else None}
