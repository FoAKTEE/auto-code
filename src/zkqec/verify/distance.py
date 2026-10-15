"""L2 ``distance``: preflight, completed bounded enumeration, witness validation (appendix §C).

Sectors (kernel convention): sector ``X`` is the minimum weight of an X-type nontrivial logical,
i.e. ``x`` with ``H_Z x = 0`` and ``x ∉ rowspace(H_X)``; sector ``Z`` swaps the roles. The ``both``
bound is the minimum of the two sectors and is derived by the resolver, not emitted here.

Completion rule (§C2), per sector, for a complete run at half-weight ``t`` (every half-error of
weight ``<= t`` enumerated, every same-syndrome pair examined, complete canonical logical basis):

* if the minimum nontrivial logical weight found, ``w*``, satisfies ``w* <= 2t``:
  ``distance_lower_bound(sector, w*)`` and ``distance_upper_bound(sector, w*)`` (the resolver
  derives ``distance_exact``); never the weaker reading ``d >= 2t+1``;
* otherwise: ``distance_lower_bound(sector, 2t+1)`` and ``distance_upper_bound(sector, w)`` for the
  best known logical ``w`` (from the basis).

Every upper bound cites a re-verified witness artifact (zero syndrome in the sector's check matrix,
not in the opposite stabilizer row space — a basis-free test). Every run, complete or not, stores a
completed-range record. A preflight refusal, a budget exception or a deadline yields ``unknown``
on ``distance_lower_bound(sector, 2t+1)`` and **no bound at all**.

Enumeration is the vendored kernel's ``sector_distance`` (complete syndrome-collision MITM), run on
the canonical RREF row bases of ``H_X`` and ``H_Z`` (the same row spaces, hence the same code, code
hash, logical basis and certificate): the kernel encodes one syndrome bit per check row, so redundant
rows would widen every syndrome integer beyond the rank the memory estimate assumes. The preflight
memory estimate is preprocessing (input copies, row reduction, CSS check, logical basis; estimated
from the input shapes, whose size the grammar caps bound) plus the enumeration at the exact syndrome
width; a refusal is ``budget_exhausted`` before any enumeration. Process-level limits are the
pipeline's: every L2 execution runs in a bounded child (:mod:`zkqec.exec`) with ``RLIMIT_AS`` from
``max_memory_bytes``, ``RLIMIT_CPU`` and a wall kill from the job reservation; a ``MemoryError``
caught here is ``resource_killed`` (``unknown``), one outside the enumeration closes the execution
as crashed. ``max_seconds`` composes with any armed ``ITIMER_REAL`` (the earliest deadline fires).
"""
from __future__ import annotations

import signal
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from math import comb
from typing import Any, Iterator

import numpy as np

from ..contracts import VerifierResult
from ..exec.deadline import alarm_deadline
from ..identity import code_sha
from ..kernel import gf2
from ..kernel.codes import CSSCode
from ..kernel.distance import EnumerationBudgetExceeded, sector_distance
from .common import (ArtifactSink, VerifierId, binary_matrix, css_violations, proposition,
                     refusal, result, store)

VERIFIER = VerifierId("zkqec.verify.distance", "1.1.0", "L2",
                      ("distance_lower_bound", "distance_upper_bound"))
WITNESS_VERIFIER = VerifierId("zkqec.verify.logical_witness", "1.1.0", "L2",
                              ("distance_upper_bound",))
M_ENUM = "complete_syndrome_collision_enumeration(zkqec.kernel.distance.sector_distance)"
M_WITNESS = "logical_witness_recheck_v1"
SECTORS = ("X", "Z")


@dataclass(frozen=True)
class DistanceBudget:
    """Deterministic ceilings checked by preflight; ``max_seconds`` is an optional wall clock."""

    max_patterns: int = 2_000_000
    max_pairs: int = 50_000_000
    max_memory_bytes: int = 4 * 2**30
    max_seconds: float | None = None

    def __post_init__(self):
        for name in ("max_patterns", "max_pairs", "max_memory_bytes"):
            v = getattr(self, name)
            if type(v) is not int or v < 0:
                raise ValueError(f"{name} must be a non-negative int")
        if self.max_seconds is not None and not self.max_seconds > 0:
            raise ValueError("max_seconds must be positive")


def half_patterns(n: int, t: int) -> int:
    """``M = Σ_{j<=t} C(n, j)`` (exact integer)."""
    return sum(comb(n, j) for j in range(t + 1))


def memory_estimate(n: int, k: int, r: int, m: int) -> int:
    """Conservative bytes for the kernel's buckets: every pattern in its own bucket, CPython 3.10
    object sizes (dict slot + key int + list + 2-tuple + two ints), plus the syndrome columns.
    ``r`` must be the syndrome width the kernel actually uses (here: the rank, after reduction)."""
    words = (lambda bits: 28 + 4 * max(1, -(-bits // 30)))
    per_pattern = 104 + words(r) + 64 + 64 + words(n) + words(k)
    return m * per_pattern + 2 * n * (words(r) + words(k)) + (1 << 20)


def preprocess_estimate(rows: int, n: int) -> int:
    """Conservative bytes for preprocessing ``rows`` input check rows of length ``n``: binary
    validation and uint8 copies (with boolean temporaries), RREF row reduction, the int64 CSS check
    on the reduced bases, and the kernel's canonical logical basis (nullspace / quotient /
    inverse, O(n^2) uint8 with temporaries)."""
    return 12 * rows * n + 96 * n * n + (2 << 20)


def pair_work_lower_bound(m: int, r: int) -> int:
    """Provable lower bound on ``Σ_s C(M_s, 2)``: at most ``min(2^r, M)`` buckets, convexity."""
    buckets = min(1 << r, m) if r < 64 else m
    return max(0, (m * m // buckets - m) // 2)


def preflight(n: int, k: int, r: int, t: int, budget: DistanceBudget, *,
              preprocess_bytes: int = 0) -> dict[str, Any]:
    """§C1 checks before any enumeration; ``r`` = GF(2) rank of the sector's syndrome matrix (the
    syndrome width of the reduced matrix the kernel enumerates on)."""
    m = half_patterns(n, t)
    rec = {"n": n, "k": k, "syndrome_rank": r, "syndrome_width": r, "half_weight": t,
           "half_patterns": m, "preprocess_bytes_estimate": preprocess_bytes,
           "memory_bytes_estimate": preprocess_bytes + memory_estimate(n, k, r, m),
           "pair_work_lower_bound": pair_work_lower_bound(m, r),
           "pair_work_upper_bound": m * (m - 1) // 2, "budget": asdict(budget)}
    reasons = []
    if m > budget.max_patterns:
        reasons.append(f"M = {m} half-patterns exceed max_patterns = {budget.max_patterns}")
    if rec["memory_bytes_estimate"] > budget.max_memory_bytes:
        reasons.append(f"memory estimate {rec['memory_bytes_estimate']} B exceeds "
                       f"max_memory_bytes = {budget.max_memory_bytes}")
    if rec["pair_work_lower_bound"] > budget.max_pairs:
        reasons.append(f"pair work >= {rec['pair_work_lower_bound']} exceeds max_pairs = "
                       f"{budget.max_pairs}")
    rec["ok"] = not reasons
    rec["reasons"] = reasons
    return rec


class _Deadline(BaseException):
    """Raised from SIGALRM; BaseException so kernel code cannot swallow it."""


@contextmanager
def _deadline(seconds: float | None) -> Iterator[None]:
    """``budget.max_seconds`` for one sector, composed with any armed timer: an earlier outer
    deadline (e.g. admission's wall ceiling) still fires first, and it is restored on exit."""
    if seconds is None:
        yield
        return
    with alarm_deadline(seconds, _Deadline):
        yield


def _sector_matrices(hx: np.ndarray, hz: np.ndarray, sector: str) -> tuple[np.ndarray, np.ndarray]:
    """(syndrome matrix, opposite stabilizer rows) for logicals of the given sector."""
    return (hz, hx) if sector == "X" else (hx, hz)


def check_logical(hx: np.ndarray, hz: np.ndarray, sector: str, v: np.ndarray) -> str | None:
    """``None`` if ``v`` is a nontrivial logical of the sector, else the reason it is not."""
    h, stab = _sector_matrices(hx, hz, sector)
    if v.shape != (hx.shape[1],):
        return f"length {v.shape} differs from n = {hx.shape[1]}"
    if not v.any():
        return "zero vector"
    if np.any(h.astype(np.int64) @ v.astype(np.int64) % 2):
        return "nonzero syndrome"
    if stab.shape[0] and gf2.in_rowspace(v, stab):
        return "trivial: in the stabilizer row space (zero logical signature)"
    return None


def _witness(hx, hz, sector, v, csha, sink) -> str:
    support = np.flatnonzero(v).tolist()
    return store(sink, {"kind": "logical_witness_v1", "code": csha, "sector": sector,
                        "n": int(hx.shape[1]), "support": support, "weight": len(support)})


def _load(hx, hz) -> tuple[np.ndarray, np.ndarray, str | None]:
    """Binary-validate and reduce to canonical RREF row bases (same row spaces, so the same code
    and the same logical/stabilizer membership tests); CSS validity is checked on the bases."""
    hx, hz = binary_matrix(hx, "hx"), binary_matrix(hz, "hz")
    if hx.shape[1] != hz.shape[1] or hx.shape[1] < 1:
        return hx, hz, "hx and hz need a common positive number of columns"
    hx, hz = gf2.row_basis(hx), gf2.row_basis(hz)
    if len(css_violations(hx, hz)):
        return hx, hz, "presentation is not CSS-valid"
    return hx, hz, None


def _shape(a) -> tuple[int, int]:
    shape = np.shape(a)
    return (int(shape[0]), int(shape[1])) if len(shape) == 2 else (0, 0)


def certify_distance(hx, hz, half_weight: int, *, artifact_sink: ArtifactSink,
                     budget: DistanceBudget = DistanceBudget(),
                     sectors: tuple[str, ...] = SECTORS) -> list[VerifierResult]:
    """Run the §C2 completed enumeration per sector and emit bounds exactly as specified."""
    if type(half_weight) is not int or half_weight < 0:
        raise ValueError("half_weight must be a non-negative int")
    if not sectors or any(s not in SECTORS for s in sectors) or len(set(sectors)) != len(sectors):
        raise ValueError("sectors must be distinct values from ('X', 'Z')")
    if budget.max_seconds is not None and (
            threading.current_thread() is not threading.main_thread()
            or not hasattr(signal, "setitimer")):
        raise ValueError("budget.max_seconds needs SIGALRM in the main thread; "
                         "enforce wall-clock limits in the job runner instead")
    (rx, nx), (rz, nz) = _shape(hx), _shape(hz)
    pre_bytes = preprocess_estimate(rx + rz, max(nx, nz))
    hx, hz, problem = _load(hx, hz)
    if hx.shape[1] != hz.shape[1] or hx.shape[1] < 1:
        raise ValueError(problem)
    t = half_weight
    csha = code_sha(hx, hz)
    out: list[VerifierResult] = []
    if problem:
        return [refusal(proposition("distance_lower_bound", "code", csha, sector=s, d=2 * t + 1),
                        M_ENUM, problem, outcome="premise_refuted") for s in sectors]
    code = CSSCode(hx, hz)                 # reduced bases: syndrome width = rank
    n, k = code.n, code.k
    if t > n:
        raise ValueError(f"half_weight {t} exceeds n = {n}")
    for s in sectors:
        target = proposition("distance_lower_bound", "code", csha, sector=s, d=2 * t + 1)
        if k == 0:
            out.append(refusal(target, M_ENUM, "k = 0: no nontrivial logical operator",
                               outcome="unsupported", completion="unsupported"))
            continue
        h, _ = _sector_matrices(hx, hz, s)
        pre = preflight(n, k, int(h.shape[0]), t, budget, preprocess_bytes=pre_bytes)
        record = {"kind": "distance_completed_range_v1", "code": csha, "sector": s, "n": n, "k": k,
                  "t": t, "method": M_ENUM, "verifier": VERIFIER.as_dict(), "preflight": pre}
        if not pre["ok"]:
            record.update(completion="budget_exhausted", half_patterns=pre["half_patterns"],
                          pairs_checked=0, w_star=None)
            ref = store(artifact_sink, record)
            out.append(refusal(target, M_ENUM, "; ".join(pre["reasons"]),
                               outcome="budget_exhausted", completion="budget_exhausted",
                               artifacts=(ref,)))
            continue
        cert = None
        try:
            with _deadline(budget.max_seconds):
                cert = sector_distance(code, s, t, max_patterns=budget.max_patterns,
                                       max_pairs=budget.max_pairs)
        except EnumerationBudgetExceeded as exc:
            cert, completion, reason = None, "budget_exhausted", str(exc)
        except _Deadline:
            cert, completion, reason = None, "timeout", f"deadline of {budget.max_seconds} s reached"
        except MemoryError:
            cert, completion, reason = None, "resource_killed", "MemoryError during enumeration"
        except Exception as exc:  # noqa: BLE001 - any other failure is a crash, never a bound
            cert, completion, reason = None, "crashed", f"{type(exc).__name__}: {str(exc)[:200]}"
        if cert is None:
            record.update(completion=completion, half_patterns=pre["half_patterns"],
                          pairs_checked=None, w_star=None, reason=reason)
            ref = store(artifact_sink, record)
            out.append(refusal(target, M_ENUM, reason, outcome=completion, completion=completion,
                               artifacts=(ref,)))
            continue
        best = int(cert.upper_bound)
        w_star = best if best <= 2 * t else None
        v = np.zeros(n, dtype=np.uint8)
        v[list(cert.witness_support)] = 1
        bad = check_logical(hx, hz, s, v)
        if bad is None and int(v.sum()) != best:
            bad = "witness weight differs from the reported upper bound"
        if bad is None and cert.lower_bound != (w_star if w_star is not None else 2 * t + 1):
            bad = "kernel lower bound disagrees with the §C2 rule"
        if bad is not None:
            record.update(completion="crashed", half_patterns=cert.half_patterns,
                          pairs_checked=cert.pairs_checked, w_star=None,
                          reason=f"kernel witness failed re-verification: {bad}")
            ref = store(artifact_sink, record)
            out.append(refusal(target, M_ENUM, record["reason"], outcome="crashed",
                               completion="crashed", artifacts=(ref,)))
            continue
        lower = w_star if w_star is not None else 2 * t + 1
        record.update(completion="completed", half_patterns=cert.half_patterns,
                      pairs_checked=cert.pairs_checked, w_star=w_star, lower_bound=lower,
                      upper_bound=best, witness_support=list(cert.witness_support))
        ref = store(artifact_sink, record)
        wit = _witness(hx, hz, s, v, csha, artifact_sink)
        detail = {"t": t, "w_star": w_star, "half_patterns": cert.half_patterns,
                  "pairs_checked": cert.pairs_checked,
                  "rule": "w* <= 2t: exact" if w_star is not None else "none through 2t: >= 2t+1"}
        out.append(result(proposition("distance_lower_bound", "code", csha, sector=s, d=lower),
                          "verified", M_ENUM, artifacts=(ref,), detail=detail))
        out.append(result(proposition("distance_upper_bound", "code", csha, sector=s, d=best),
                          "verified", M_ENUM, witnesses=(wit,), artifacts=(ref,), detail=detail))
    return out


def validate_logical_witness(hx, hz, sector: str, vector, *,
                             artifact_sink: ArtifactSink) -> list[VerifierResult]:
    """Witness check (§C3): a nontrivial sector logical of weight w proves ``d_sector <= w``.

    An invalid witness proves nothing in either direction: the result is ``unknown`` with
    ``detail.witness_valid = False`` and the reason; it is never a refutation of the bound.
    """
    if sector not in SECTORS:
        raise ValueError("sector must be 'X' or 'Z'")
    hx, hz, problem = _load(hx, hz)
    v = np.asarray(vector)
    if hx.shape[1] != hz.shape[1] or hx.shape[1] < 1:
        raise ValueError(problem)
    csha = code_sha(hx, hz)
    binary = v.ndim == 1 and bool(np.all((v == 0) | (v == 1)))
    weight = int(v.astype(np.int64).sum()) if binary else 0
    prop = proposition("distance_upper_bound", "code", csha, sector=sector, d=max(weight, 1))
    if problem:
        return [refusal(prop, M_WITNESS, problem, outcome="premise_refuted")]
    if not binary:
        return [refusal(prop, M_WITNESS, "witness must be a binary vector")]
    v = v.astype(np.uint8)
    bad = check_logical(hx, hz, sector, v)
    if bad is not None:
        return [result(prop, "unknown", M_WITNESS,
                       detail={"witness_valid": False, "reason": bad, "weight": weight})]
    wit = _witness(hx, hz, sector, v, csha, artifact_sink)
    return [result(prop, "verified", M_WITNESS, witnesses=(wit,),
                   detail={"witness_valid": True, "weight": weight})]
