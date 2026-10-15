"""Conditional resource accounting; no uncalibrated code-distance error formula."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from math import ceil, isfinite
from .statistics import hoeffding_shots


@dataclass(frozen=True)
class ResourceInputs:
    data_qubits: int
    factory_qubits_each: int
    factories: int
    syndrome_ancillas: int
    buffer_qubits: int
    preparations_per_shot: int
    preparation_attempt_seconds: float
    joint_acceptance_lower: float
    fixed_circuit_seconds: float
    state_prep_seconds: float
    readout_seconds: float
    decoder_feedback_seconds: float
    logical_failure_upper: float
    systematic_bias_bound: float
    observable_norm: float
    target_precision: float
    confidence_alpha: float
    whole_shot_acceptance_lower: float = 1.0
    assumptions_verified: bool = False

    def __post_init__(self) -> None:
        ints = [self.data_qubits, self.factory_qubits_each, self.factories,
                self.syndrome_ancillas, self.buffer_qubits, self.preparations_per_shot]
        if any(not isinstance(x, int) or x < 0 for x in ints) or self.factories < 1:
            raise ValueError("Resource counts must be nonnegative integers; factories >= 1.")
        times = [self.preparation_attempt_seconds, self.fixed_circuit_seconds,
                 self.state_prep_seconds, self.readout_seconds, self.decoder_feedback_seconds,
                 self.systematic_bias_bound]
        if any(not isfinite(x) or x < 0 for x in times):
            raise ValueError("Times and systematic bias must be finite and nonnegative.")
        for p in [self.joint_acceptance_lower, self.whole_shot_acceptance_lower]:
            if not isfinite(p) or not 0 < p <= 1:
                raise ValueError("A strictly positive acceptance lower bound is required.")
        if not isfinite(self.logical_failure_upper) or not 0 <= self.logical_failure_upper <= 1:
            raise ValueError("Logical failure must be a probability upper bound.")
        if not isfinite(self.observable_norm) or self.observable_norm <= 0:
            raise ValueError("Observable norm must be positive and finite.")
        if not isfinite(self.target_precision) or self.target_precision <= 0 or not 0 < self.confidence_alpha < 1:
            raise ValueError("Invalid target precision or confidence alpha.")


def answer_resources(x: ResourceInputs) -> dict:
    """A deliberately SERIAL factory-work policy, even with multiple factories.

    Each requested full batch is delivered by one factory; different requests
    are not overlapped in this conservative reference policy. Thus the delivered
    time uses R*tau/s, not R*tau/(F*s), and no unproved perfect batching benefit
    is reported. Multiple factories consume space but are unused by this policy.
    Fresh iid attempts, validity on accepted shots, and a task-level conditional
    logical-failure upper bound are required. This is NOT a discrete-event model.
    """
    bias = x.systematic_bias_bound+2*x.observable_norm*x.logical_failure_upper
    remaining = x.target_precision-bias
    qubits = (x.data_qubits+x.factory_qubits_each*x.factories+x.syndrome_ancillas
              +x.buffer_qubits)
    common = {"peak_qubits": qubits, "observable_bias_bound": bias,
              "sampling_precision_remaining": remaining,
              "assumptions_verified": x.assumptions_verified,
              "policy": "serial_full_batch_delivery_no_factory_overlap",
              "scope": "conditional_planning_model_not_hardware_prediction"}
    if remaining <= 0:
        return {**common, "feasible": False, "reason": "No positive sampling-error budget."}
    shots = hoeffding_shots(x.observable_norm, remaining, x.confidence_alpha)
    factory_time = (x.preparations_per_shot*x.preparation_attempt_seconds
                    /x.joint_acceptance_lower)
    one_attempt = (factory_time+x.fixed_circuit_seconds+x.state_prep_seconds
                   +x.readout_seconds+x.decoder_feedback_seconds)
    accepted_latency = one_attempt/x.whole_shot_acceptance_lower
    return {**common, "feasible": True, "accepted_shots": shots,
            "per_accepted_shot_expected_seconds": accepted_latency,
            "time_to_answer_expected_seconds": shots*accepted_latency,
            "serial_throughput_shots_per_second": (1/accepted_latency if accepted_latency else None),
            "scientific_claim_ready": False,
            "claim_gate_reason": "Caller assertions do not supply independently verified domain evidence.",
            "input": asdict(x)}


def union_failure_bound(per_location_upper: list[float], multiplicities: list[int]) -> float:
    if len(per_location_upper) != len(multiplicities):
        raise ValueError("Mismatched probability and multiplicity arrays.")
    if any(not isfinite(p) or not 0 <= p <= 1 for p in per_location_upper):
        raise ValueError("Invalid probability upper bound.")
    if any(not isinstance(n, int) or n < 0 for n in multiplicities):
        raise ValueError("Multiplicities must be nonnegative integers.")
    return min(1.0, sum(p*n for p, n in zip(per_location_upper, multiplicities)))


def pareto_front(records: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """All objectives minimized. Callers must separate fidelity and feasibility."""
    if not keys:
        raise ValueError("Provide at least one objective.")
    for r in records:
        if any(not isinstance(r[k], (int, float)) or not isfinite(r[k]) for k in keys):
            raise ValueError("Pareto objectives must be finite numeric values.")
    return [r for i, r in enumerate(records) if not any(
        all(s[k] <= r[k] for k in keys) and any(s[k] < r[k] for k in keys)
        for j, s in enumerate(records) if i != j)]
