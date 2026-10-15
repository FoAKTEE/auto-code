"""Budgeted, exact bounded distance certification; never convert timeout to proof."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from itertools import combinations
from math import comb
import numpy as np
from .codes import CSSCode
from .gf2 import as_mask


class EnumerationBudgetExceeded(RuntimeError):
    """No new lower-bound certificate is produced on an incomplete run."""


@dataclass(frozen=True)
class DistanceCertificate:
    sector: str
    lower_bound: int
    upper_bound: int
    exact: bool
    witness_support: tuple[int, ...]
    half_weight: int
    half_patterns: int
    pairs_checked: int
    method: str = "complete_syndrome_collision_enumeration"
    scope: str = "algebraic_code_distance_not_circuit_fault_distance"

    def to_dict(self) -> dict:
        return asdict(self)


def sector_distance(code: CSSCode, sector: str, half_weight: int = 2,
                    max_patterns: int = 200_000,
                    max_pairs: int = 20_000_000) -> DistanceCertificate:
    if code.k == 0:
        raise ValueError("Distance to a nontrivial logical operator requires k > 0.")
    if sector not in {"X", "Z"}:
        raise ValueError("sector must be X or Z.")
    n = code.n
    if not isinstance(half_weight, int) or not 0 <= half_weight <= n:
        raise ValueError("half_weight must be an integer in [0,n].")
    if max_patterns < 1 or max_pairs < 0:
        raise ValueError("Invalid enumeration budget.")
    h, logical_dual, logical_same = ((code.hz, code.lz, code.lx) if sector == "X"
                                     else (code.hx, code.lx, code.lz))
    count = sum(comb(n, w) for w in range(half_weight+1))
    if count > max_patterns:
        raise EnumerationBudgetExceeded(f"{count} half-patterns exceed cap {max_patterns}.")
    syndrome_cols = [as_mask(h[:, i]) for i in range(n)]
    logical_cols = [as_mask(logical_dual[:, i]) for i in range(n)]
    # Complete logical basis provides an independently known upper bound.
    row = min(logical_same, key=lambda v: int(v.sum()))
    best_mask = as_mask(row)
    best = best_mask.bit_count()
    buckets: dict[int, list[tuple[int, int]]] = {}
    pairs = 0
    for w in range(half_weight+1):
        for support in combinations(range(n), w):
            syndrome = signature = mask = 0
            for i in support:
                syndrome ^= syndrome_cols[i]
                signature ^= logical_cols[i]
                mask |= 1 << i
            bucket = buckets.setdefault(syndrome, [])
            for old_mask, old_signature in bucket:
                pairs += 1
                if pairs > max_pairs:
                    raise EnumerationBudgetExceeded("Pair budget exhausted; certification incomplete.")
                if signature != old_signature:
                    difference = mask ^ old_mask
                    weight = difference.bit_count()
                    if weight < best:
                        best, best_mask = weight, difference
            bucket.append((mask, signature))
    lower = min(best, 2*half_weight+1)
    support = tuple(i for i in range(n) if best_mask >> i & 1)
    witness = np.zeros(n, dtype=np.uint8)
    witness[list(support)] = 1
    if np.any(h @ witness % 2) or not np.any(logical_dual @ witness % 2):
        raise AssertionError("Distance witness failed independent verification.")
    return DistanceCertificate(sector, lower, best, lower == best, support,
                               half_weight, count, pairs)


def certify(code: CSSCode, half_weight: int = 2, **budgets: int) -> dict:
    """CSS distance is min(d_X,d_Z); certify both without assuming self-duality."""
    x = sector_distance(code, "X", half_weight, **budgets)
    z = sector_distance(code, "Z", half_weight, **budgets)
    lower, upper = min(x.lower_bound, z.lower_bound), min(x.upper_bound, z.upper_bound)
    return {"lower_bound": lower, "upper_bound": upper, "exact": lower == upper,
            "X": x.to_dict(), "Z": z.to_dict(), "scope": x.scope}
