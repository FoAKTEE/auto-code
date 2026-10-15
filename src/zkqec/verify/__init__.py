"""Registered verifiers L1–L3 (prompt §8; appendix §A, §C, §F).

Each entrypoint returns ``list[VerifierResult]`` and never names an issuer; the admission service
stamps issuer/version from :data:`REGISTRY` via its own execution record. Artifacts go through the
caller's ``artifact_sink(obj) -> sha256``.

=====================================  ========================================  =============
verifier (name, version)               entrypoints                               predicates
=====================================  ========================================  =============
zkqec.verify.css_algebra 1.0.0 (L1)    analyze, css_valid, block_length_eq,      css_valid, ...
                                       dimension_eq, max_check_weight_eq,
                                       max_qubit_degree_eq (+ canonical_basis)
zkqec.verify.distance 1.0.0 (L2)       certify_distance (+ preflight)            distance_*_bound
zkqec.verify.logical_witness 1.0.0     validate_logical_witness                  distance_upper_bound
zkqec.verify.logical_action 1.0.0 (L3) implements (+ compute_logical_action)     implements
=====================================  ========================================  =============
"""
from __future__ import annotations

from . import css_algebra, distance, logical_action
from .common import VerifierId
from .css_algebra import (analyze, block_length_eq, canonical_basis, css_valid, dimension_eq,
                          max_check_weight_eq, max_qubit_degree_eq)
from .distance import (DistanceBudget, certify_distance, half_patterns, preflight,
                       validate_logical_witness)
from .logical_action import (PHASE_CONVENTION, ActionTableau, MalformedInput, UnsupportedInput,
                             compute_logical_action, implements)
from .pauli import CLIFFORD_CONJUGATION, Pauli, PhysicalAction

REGISTRY: dict[str, tuple[VerifierId, dict[str, object]]] = {
    css_algebra.VERIFIER.name: (css_algebra.VERIFIER, {
        "analyze": analyze, "css_valid": css_valid, "block_length_eq": block_length_eq,
        "dimension_eq": dimension_eq, "max_check_weight_eq": max_check_weight_eq,
        "max_qubit_degree_eq": max_qubit_degree_eq}),
    distance.VERIFIER.name: (distance.VERIFIER, {"certify_distance": certify_distance}),
    distance.WITNESS_VERIFIER.name: (distance.WITNESS_VERIFIER,
                                     {"validate_logical_witness": validate_logical_witness}),
    logical_action.VERIFIER.name: (logical_action.VERIFIER, {"implements": implements}),
}

__all__ = ["ActionTableau", "CLIFFORD_CONJUGATION", "DistanceBudget", "MalformedInput",
           "PHASE_CONVENTION", "Pauli", "PhysicalAction", "REGISTRY", "UnsupportedInput",
           "VerifierId", "analyze", "block_length_eq", "canonical_basis", "certify_distance",
           "compute_logical_action", "css_valid", "dimension_eq", "half_patterns", "implements",
           "max_check_weight_eq", "max_qubit_degree_eq", "preflight", "validate_logical_witness"]
