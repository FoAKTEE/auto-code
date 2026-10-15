"""Transparent seeded requirement abduction; replaceable by a model proposal role.

Rules here are a human-specified baseline. Their output is not a zero-knowledge
inference result and never automatically becomes verified evidence.
"""
from .schemas import Requirement


def infer_seed_requirements(workload: dict) -> list[Requirement]:
    out = [Requirement(requirement_id="R-CSS", need="blockwise_logical_CNOT",
                       proposed_structure="CSS_with_aligned_logical_bases",
                       scope="same_code_blocks_and_matching_basis", hardness="route_conditional",
                       source_keys=("gottesman1997",),
                       alternatives=("logical_parity_measurement", "code_switching"),
                       checks=("CSS_commutation", "logical_CNOT_action", "fault_propagation"))]
    if workload["rotation_count"]:
        out.append(Requirement(requirement_id="R-ROT", need="small_angle_logical_rotations",
                               proposed_structure="analog_injection_or_Clifford_T_synthesis",
                               scope="angle_noise_and_protocol_specific", hardness="route_conditional",
                               source_keys=("akahoshi2024", "ross2016"),
                               alternatives=("distilled_T_synthesis", "batched_parity_measurements"),
                               checks=("accepted_instrument", "joint_noise", "RUS_tail", "calibration_domain")))
    if workload["peak_disjoint_rotations"] > 1:
        out.append(Requirement(requirement_id="R-PAR", need="parallel_selected_rotations",
                               proposed_structure="disjoint_logical_representatives",
                               scope="candidate_parallel_injection_route_not_universal_necessity",
                               hardness="route_conditional", source_keys=("ismail2026",),
                               alternatives=("multiple_batches", "different_injection_gadget"),
                               checks=("support_overlap", "joint_accepted_logical_map", "schedule_conflicts")))
    return out
