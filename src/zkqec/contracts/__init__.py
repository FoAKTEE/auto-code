"""Typed, frozen contracts shared by every zkqec module (prompt appendix §A, §B, §K, §L)."""
from .base import Frozen, canonical_json, content_hash
from .design import (DesignCandidate, LogicalBasis, OperationRef, PhysicalOperation, Presentation,
                     Proposal, SignedPauliImages, SparseMatrix, VERIFIER_OWNED_FIELDS)
from .evidence import EXACT, Guarantee, Proposition, Receipt, VerifierResult
from .jobs import AgentCallRecord, Reservation, TRANSITIONS
from .registry import REGISTRY_VERSION, SIGNATURES, Signature, SignatureError
from .task import Budget, HardwareDomain, Profile, StructuralTargets, TaskSA, TaskSF

__all__ = [n for n in dir() if not n.startswith("_")]
