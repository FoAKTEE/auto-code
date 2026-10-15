"""B1: seeded random sampler over the two-block group-algebra grammar (prompt §10 arm B1).

Declared distribution (frozen by ``RandomTwoBlockSpec`` and hashed into the manifest):
  1. (l, m) uniformly from ``dims`` (only pairs with 2*l*m <= n_cap are kept);
  2. |a| uniformly from ``weights_a``, |b| uniformly from ``weights_b``;
  3. monomials of a (and of b) drawn uniformly *without replacement* from Z_l x Z_m.
Monomials are distinct, so wrap-cancellation cannot occur inside one polynomial. No knowledge of
known-good polynomials is used. Every proposal is charged by the orchestrator like any other.
"""
from __future__ import annotations

import numpy as np
from pydantic import Field, model_validator

from ..contracts import Frozen, Proposal, content_hash


class RandomTwoBlockSpec(Frozen):
    dims: tuple[tuple[int, int], ...] = Field(min_length=1)
    weights_a: tuple[int, ...] = (3,)
    weights_b: tuple[int, ...] = (3,)
    n_cap: int = Field(ge=2)
    seed: int = Field(ge=0)

    @model_validator(mode="after")
    def _valid(self):
        usable = [(l, m) for l, m in self.dims if l >= 1 and m >= 1 and 2 * l * m <= self.n_cap]
        if not usable:
            raise ValueError("no (l, m) satisfies 2*l*m <= n_cap")
        for w in (*self.weights_a, *self.weights_b):
            if w < 1 or any(w > l * m for l, m in usable):
                raise ValueError("polynomial weight must be in [1, l*m] for every usable (l, m)")
        return self

    @property
    def usable_dims(self) -> tuple[tuple[int, int], ...]:
        return tuple((l, m) for l, m in self.dims if 2 * l * m <= self.n_cap)


class RandomTwoBlockProposer:
    """Deterministic stream of proposals for a given spec."""

    def __init__(self, spec: RandomTwoBlockSpec):
        self.spec = spec
        self.spec_sha = content_hash(spec)
        self._rng = np.random.default_rng(spec.seed)

    def _poly(self, l: int, m: int, w: int) -> list[list[int]]:
        cells = sorted(int(c) for c in self._rng.choice(l * m, size=w, replace=False))
        return [[c // m, c % m] for c in cells]

    def propose(self) -> Proposal:
        dims = self.spec.usable_dims
        l, m = dims[int(self._rng.integers(len(dims)))]
        wa = self.spec.weights_a[int(self._rng.integers(len(self.spec.weights_a)))]
        wb = self.spec.weights_b[int(self._rng.integers(len(self.spec.weights_b)))]
        return Proposal(family="two_block_group_algebra",
                        params={"l": l, "m": m, "a": self._poly(l, m, wa), "b": self._poly(l, m, wb)},
                        rationale=f"B1 random two-block sample (spec {self.spec_sha[:12]})")
