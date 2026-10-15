"""Fixed-sample statistical tools with explicit interpretation."""
from __future__ import annotations
from math import ceil, log, isfinite
from scipy.stats import beta


def clopper_pearson(failures: int, shots: int, alpha: float = 0.05) -> tuple[float, float]:
    if not isinstance(failures, int) or not isinstance(shots, int) or not 0 <= failures <= shots or shots < 1:
        raise ValueError("Require integer 0 <= failures <= shots, with shots > 0.")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0,1).")
    lo = 0.0 if failures == 0 else float(beta.ppf(alpha/2, failures, shots-failures+1))
    hi = 1.0 if failures == shots else float(beta.ppf(1-alpha/2, failures+1, shots-failures))
    return lo, hi


def simultaneous_alpha(family_alpha: float, comparisons: int) -> float:
    if not 0 < family_alpha < 1 or not isinstance(comparisons, int) or comparisons < 1:
        raise ValueError("Invalid fixed comparison count or family alpha.")
    return family_alpha/comparisons


def hoeffding_shots(observable_norm: float, precision: float, alpha: float) -> int:
    """For iid measurements in [-B,B]: 2 exp[-N epsilon^2/(2B^2)]."""
    if not all(map(isfinite, [observable_norm, precision, alpha])) or observable_norm <= 0 or precision <= 0 or not 0 < alpha < 1:
        raise ValueError("Invalid observable norm, precision, or alpha.")
    return max(1, ceil(2*observable_norm**2*log(2/alpha)/precision**2))


def frechet_all_success(marginals: list[float]) -> tuple[float, float]:
    """Bounds for joint acceptance without assuming independence across a batch."""
    if not marginals or any(not isfinite(p) or not 0 <= p <= 1 for p in marginals):
        raise ValueError("Need nonempty valid acceptance marginals.")
    return max(0.0, sum(marginals)-len(marginals)+1), min(marginals)
