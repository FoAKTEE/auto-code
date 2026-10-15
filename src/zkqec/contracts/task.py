"""Task contracts, hardware domains, profiles and budgets (prompt §3, appendix §B, §K).

Algorithm choice and step counts are *not* task fields (qecforge `TaskSpec.trotter_steps` defect).
"""
from __future__ import annotations

from typing import Literal, get_args

from pydantic import Field, model_validator

from .base import Frozen
from .design import SignedPauliImages

OperationClass = Literal["qubit_permutation", "transversal_1q_clifford"]
# Closed vocabulary of campaign labels rendered in authoritative reports (§A5, §11 M1, D16).
CampaignLabel = Literal["single-implementation", "non-evaluation", "K0-unenforced", "dev-only",
                        "fixture", "M1-live-slice"]
CAMPAIGN_LABELS = frozenset(get_args(CampaignLabel))


class HardwareDomain(Frozen):
    name: str
    allowed_operations: tuple[OperationClass, ...] = ()
    operation_costs: dict[str, float] = Field(default_factory=dict)
    notes: str = ""


class StructuralTargets(Frozen):
    n_max: int = Field(ge=1, le=4096)
    k_min: int = Field(ge=1)
    d_min: int = Field(ge=1)
    w_max: int | None = Field(default=None, ge=1)
    deg_max: int | None = Field(default=None, ge=1)


class TaskSA(Frozen):
    """S-A: family-free structural target (autonomy A0+)."""
    kind: Literal["S-A"] = "S-A"
    task_id: str
    targets: StructuralTargets


class TaskSF(Frozen):
    """S-F: functional logical-operation target (autonomy A1). The route is the agent's choice."""
    kind: Literal["S-F"] = "S-F"
    task_id: str
    targets: StructuralTargets
    k_req: int = Field(ge=1)
    target_action: SignedPauliImages
    hardware: HardwareDomain

    @model_validator(mode="after")
    def _consistent(self):
        if len(self.target_action.x_images) != self.k_req:
            raise ValueError("target action must act on exactly k_req logical qubits")
        if self.targets.k_min < self.k_req:
            raise ValueError("k_min must be at least k_req")
        if not self.hardware.allowed_operations:
            raise ValueError("S-F needs a declared allowed operation class")
        return self


class Profile(Frozen):
    knowledge: Literal["K0", "K0-unenforced", "K1", "K2"]
    prompt: Literal["P-min", "P-domain"]
    grammar: tuple[Literal["G-open", "G-struct"], ...]

    @model_validator(mode="after")
    def _valid_combo(self):
        if self.prompt == "P-min" and "G-struct" in self.grammar:
            raise ValueError("P-min + G-struct is invalid (named families)")
        if not self.grammar:
            raise ValueError("at least one grammar")
        return self


class Budget(Frozen):
    """Ceilings (§K). Units are never summed."""
    max_proposals: int = Field(ge=0)
    max_usd: float = Field(ge=0)
    max_wall_seconds: int = Field(ge=1)
    max_verifier_cpu_seconds: int = Field(ge=1)
    per_call_max_input_tokens: int = Field(ge=1, le=1_000_000)
    per_call_max_output_tokens: int = Field(ge=1, le=200_000)
    per_call_max_requests: int = Field(ge=1, le=20)
    per_call_max_wall_seconds: int = Field(ge=1)
    confirmation_reserve_usd: float = Field(default=0.0, ge=0)
