"""Declarative search baselines; no generated Python is executed."""
from __future__ import annotations
from copy import deepcopy
import numpy as np
from .agents import Proposer
from .schemas import CampaignSpec, Proposal


class RandomBBProposer:
    """Seeded fixed-grammar random BB search, not structure discovery.

    For self_dual=True, B is A^T (negative torus exponents). Duplicate
    representations and invalid candidates are charged by the coordinator.
    The trial budget, algebra verifier and task remain identical across agents.
    """
    def __init__(self, seed: int, dimensions: tuple[tuple[int, int], ...] = ((3, 7),),
                 monomials: int = 4, self_dual: bool = True):
        if not dimensions or any(type(l) is not int or type(m) is not int or min(l,m)<1
                                 for l,m in dimensions):
            raise ValueError("Supply positive integer dimensions.")
        if type(monomials) is not int or monomials < 1 or any(monomials > l*m for l,m in dimensions):
            raise ValueError("Invalid polynomial weight.")
        self.rng = np.random.default_rng(seed)
        self.dimensions, self.monomials, self.self_dual = dimensions, monomials, self_dual

    def propose(self, spec: CampaignSpec, history: list[dict]) -> Proposal:
        ell, m = self.dimensions[int(self.rng.integers(len(self.dimensions)))]
        def sample():
            ids = sorted(int(i) for i in self.rng.choice(ell*m, self.monomials, replace=False))
            return [[i//m,i%m] for i in ids]
        a = sample()
        b = [[(-i)%ell,(-j)%m] for i,j in a] if self.self_dual else sample()
        return Proposal(family="bivariate_bicycle", parameters={"ell":ell,"m":m,"a":a,"b":b},
                        rationale="Seeded random fixed-grammar baseline; not novel structural inference.")


class RoundRobinProposer:
    """Deterministic portfolio across independently supplied proposal policies.

    Provides a multi-proposer baseline, not an assertion that debate is useful.
    A policy returning None is retired. All see the same immutable task snapshot
    and copied history. Hosted models, credentials and tool permissions remain
    the caller's responsibility.
    """
    def __init__(self, proposers: list[Proposer]):
        if not proposers:
            raise ValueError("Provide at least one proposer.")
        self.proposers = list(proposers)
        self.index = 0

    def propose(self, spec: CampaignSpec, history: list[dict]) -> Proposal | None:
        while self.proposers:
            self.index %= len(self.proposers)
            proposer = self.proposers[self.index]
            item = proposer.propose(spec, deepcopy(history))
            if item is None:
                self.proposers.pop(self.index)
                continue
            self.index = (self.index+1)%len(self.proposers)
            return item
        return None
