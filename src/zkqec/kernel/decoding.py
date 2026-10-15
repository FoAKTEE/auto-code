"""Transparent finite-weight sector decoder and code-capacity smoke experiments."""
from __future__ import annotations
from itertools import combinations
from math import comb, isfinite
import time
import numpy as np
from .codes import CSSCode
from .gf2 import as_mask, matrix
from .statistics import clopper_pearson


class SyndromeLookup:
    """Minimum weight within an exhaustively enumerated weight cutoff.

    Not degenerate maximum-likelihood decoding, not circuit-level decoding, and
    not a scalable qLDPC decoder. Missing syndromes are explicit failures.
    """
    def __init__(self, checks: np.ndarray, max_weight: int = 2,
                 max_patterns: int = 1_000_000):
        self.h = matrix(checks)
        n = self.h.shape[1]
        if not isinstance(max_weight, int) or not 0 <= max_weight <= n:
            raise ValueError("Invalid decoder weight cutoff.")
        patterns = sum(comb(n, w) for w in range(max_weight+1))
        if patterns > max_patterns:
            raise ValueError("Decoder enumeration exceeds its safety cap.")
        cols = [as_mask(self.h[:, q]) for q in range(n)]
        self.table: dict[int, int] = {}
        for w in range(max_weight+1):
            for support in combinations(range(n), w):
                syn = correction = 0
                for q in support:
                    syn ^= cols[q]
                    correction |= 1 << q
                self.table.setdefault(syn, correction)
        self.max_weight, self.patterns = max_weight, patterns

    def decode(self, syndrome: np.ndarray) -> np.ndarray | None:
        s = np.asarray(syndrome)
        if s.shape != (self.h.shape[0],):
            raise ValueError("Syndrome has the wrong shape.")
        key = as_mask(s)
        mask = self.table.get(key)
        if mask is None:
            return None
        return np.array([(mask >> q) & 1 for q in range(self.h.shape[1])], dtype=np.uint8)


def code_capacity(code: CSSCode, physical_p: float = 0.02, shots: int = 2000,
                  seed: int = 0, max_weight: int = 2, alpha: float = 0.05) -> dict:
    if not isfinite(physical_p) or not 0 <= physical_p <= 1 or not isinstance(shots, int) or shots < 1:
        raise ValueError("Invalid physical probability or shot count.")
    if code.k < 1:
        raise ValueError("Need at least one logical qubit.")
    dx = SyndromeLookup(code.hz, max_weight)
    dz = SyndromeLookup(code.hx, max_weight)
    rng = np.random.default_rng(seed)
    errors = rng.integers(1, 4, size=(shots, code.n))
    errors[rng.random((shots, code.n)) >= physical_p] = 0
    xerrors = ((errors == 1) | (errors == 2)).astype(np.uint8)
    zerrors = ((errors == 2) | (errors == 3)).astype(np.uint8)
    failures = missing = 0
    start = time.perf_counter()
    for x, z in zip(xerrors, zerrors):
        cx, cz = dx.decode(code.hz @ x % 2), dz.decode(code.hx @ z % 2)
        if cx is None or cz is None:
            missing += 1
            failures += 1
            continue
        rx, rz = x ^ cx, z ^ cz
        failed = (np.any(code.hz @ rx % 2) or np.any(code.hx @ rz % 2)
                  or np.any(code.lz @ rx % 2) or np.any(code.lx @ rz % 2))
        failures += int(failed)
    elapsed = time.perf_counter()-start
    lo, hi = clopper_pearson(failures, shots, alpha)
    return {"scope": "code_capacity_iid_depolarizing_perfect_syndromes",
            "decoder": "independent_XZ_bounded_weight_lookup_not_MLE",
            "code_sha256": code.identity, "physical_p": physical_p,
            "shots": shots, "failures": failures, "missing_syndromes": missing,
            "logical_failure_fraction": failures/shots,
            "confidence_interval": [lo, hi], "confidence_alpha": alpha,
            "seed": seed, "max_weight": max_weight,
            "offline_decode_seconds": elapsed,
            "real_time_decoder_claim": False,
            "statistical_design": "fixed_shots_iid_not_optional_stopping"}
