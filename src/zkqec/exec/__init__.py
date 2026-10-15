"""Bounded execution of evaluator work (appendix §K; Codex M1 review B5)."""
from .bounded import OUTCOMES, BoundedResult, Limits, run_bounded

__all__ = ["BoundedResult", "Limits", "OUTCOMES", "run_bounded"]
