"""Propositions, verifier results and receipts (prompt appendix §A).

A *verifier* returns a :class:`VerifierResult`. Only the admission service turns it into a
:class:`Receipt`, stamping the issuer, version, execution id and input hashes from its own execution
record. Nothing in a payload can choose its issuer or status of authority.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .base import SHA256_HEX, Frozen, content_hash
from .registry import SIGNATURES, check

Status = Literal["verified", "refuted", "unknown"]
Completion = Literal["completed", "budget_exhausted", "timeout", "resource_killed", "crashed",
                     "unsupported"]
EvidenceKind = Literal["exact_algebra", "numerical_check", "statistical_estimate", "solver_claim",
                       "proof_checked", "assumption", "derived", "agent_assertion"]
# Typed operational/scientific outcomes; never inferred from an exit code (§A4).
Outcome = Literal["launch_failure", "timeout", "resource_killed", "unsupported",
                  "malformed_proposal", "counterexample", "completed"]
AUTHORIZING_KINDS = frozenset({"exact_algebra", "numerical_check", "statistical_estimate",
                               "proof_checked", "derived"})


class Guarantee(Frozen):
    kind: Literal["exact", "numerical", "statistical"]
    tolerance: float | None = Field(default=None, gt=0)
    alpha: float | None = Field(default=None, gt=0, lt=1)
    design: str | None = None

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == "exact" and (self.tolerance or self.alpha):
            raise ValueError("exact guarantees carry no tolerance or alpha")
        if self.kind == "numerical" and self.tolerance is None:
            raise ValueError("numerical guarantees need a tolerance")
        if self.kind == "statistical" and (self.alpha is None or not self.design):
            raise ValueError("statistical guarantees need alpha and a design reference")
        return self


EXACT = Guarantee(kind="exact")


class Proposition(Frozen):
    predicate: str
    subject_type: str
    subject: str = Field(pattern=SHA256_HEX)
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _signature(self):
        check(self.predicate, self.subject_type, self.args)
        return self

    @property
    def signature(self):
        return SIGNATURES[self.predicate]

    @property
    def key(self) -> str:
        return content_hash(self)


class VerifierResult(Frozen):
    """What a registered verifier returns. It never names its own issuer."""
    proposition: Proposition
    status: Status
    evidence_kind: EvidenceKind
    guarantee: Guarantee = EXACT
    method: str
    completion: Completion
    witnesses: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    detail: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _authority(self):
        if self.completion != "completed" and self.status != "unknown":
            raise ValueError("only a completed check may report verified/refuted")
        if self.evidence_kind not in AUTHORIZING_KINDS and self.status != "unknown":
            raise ValueError(f"{self.evidence_kind} can never carry verified/refuted")
        if self.proposition.signature.derived_only:
            raise ValueError(f"{self.proposition.predicate} is derived only by the resolver")
        if self.guarantee.kind not in self.proposition.signature.guarantees:
            raise ValueError("guarantee kind not allowed for this predicate")
        return self

    @field_validator("witnesses", "artifacts")
    @classmethod
    def _hashes(cls, v):
        import re
        if any(not re.match(SHA256_HEX, h) for h in v):
            raise ValueError("witnesses/artifacts must be artifact hashes")
        return v


class Receipt(Frozen):
    """Admitted evidence. Built only by the admission service (issuer from execution record)."""
    proposition: Proposition
    status: Status
    evidence_kind: EvidenceKind
    guarantee: Guarantee
    method: str
    completion: Completion
    witnesses: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    detail: dict[str, Any] = Field(default_factory=dict)
    issuer: str
    issuer_version: str
    execution_id: str
    input_hashes: tuple[str, ...]
    domain: str = Field(pattern=SHA256_HEX)
    parents: tuple[str, ...] = ()

    @property
    def receipt_id(self) -> str:
        return content_hash(self)
