"""Typed grammar outcomes (prompt appendix §A4, §B3).

A grammar never returns a partial object: invalid untrusted input raises :class:`MalformedProposal`
(typed outcome ``malformed_proposal``); a family that exists in the contract but has no Phase-1a
builder raises :class:`UnsupportedFamily` (typed outcome ``unsupported``). Neither is a scientific
counterexample.
"""
from __future__ import annotations


class GrammarError(ValueError):
    """Base class; ``outcome`` is the §A4 typed outcome the orchestrator records."""

    outcome = "malformed_proposal"

    def __init__(self, reason: str, *, field: str = ""):
        self.reason = reason
        self.field = field
        super().__init__(f"{field}: {reason}" if field else reason)


class MalformedProposal(GrammarError):
    """Untrusted input violates a cap, the schema, or the declared algebra's input rules."""

    outcome = "malformed_proposal"


class UnsupportedFamily(GrammarError):
    """The family is declared by the contracts but not built in this phase (e.g. ``program``)."""

    outcome = "unsupported"
