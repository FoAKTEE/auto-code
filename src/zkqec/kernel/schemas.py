"""Typed public contracts. Unknown fields fail validation."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class TermSpec(FrozenModel):
    coefficient: float
    pauli: str = Field(pattern=r"^[IXYZ]+$")


class TaskSpec(FrozenModel):
    name: str
    terms: tuple[TermSpec, ...]
    evolution_time: float = Field(gt=0)
    trotter_steps: int = Field(ge=1, le=10000)
    observable: str = Field(pattern=r"^[IXYZ]+$")
    initial_state: Literal["zero_product", "external_verified_state"] = "zero_product"
    target_precision: float = Field(gt=0, le=2)
    confidence_alpha: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def common_width(self):
        if not self.terms or len({len(t.pauli) for t in self.terms}) != 1:
            raise ValueError("Terms must have one common register size.")
        if len(self.observable) != len(self.terms[0].pauli):
            raise ValueError("Observable register size differs from Hamiltonian.")
        if len(self.terms)*self.trotter_steps > 200_000:
            raise ValueError("Workload exceeds the research compiler's operation cap.")
        return self


class HardRequirements(FrozenModel):
    min_k: int = Field(default=1, ge=1)
    min_distance: int = Field(default=3, ge=1)
    max_n: int = Field(default=128, ge=1, le=512)
    max_check_weight: int = Field(default=12, ge=1)
    max_total_degree: int = Field(default=16, ge=1)
    require_disjoint_z: bool = False
    require_pairwise_global_h: bool = False


class SearchBudget(FrozenModel):
    max_candidates: int = Field(default=8, ge=1, le=10000)
    half_weight: int = Field(default=2, ge=0, le=4)
    max_half_patterns: int = Field(default=200000, ge=1, le=2000000)
    max_collision_pairs: int = Field(default=20000000, ge=0)


class CampaignSpec(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    seed: int = Field(default=0, ge=0)
    task: TaskSpec
    requirements: HardRequirements = HardRequirements()
    budget: SearchBudget = SearchBudget()
    mode: Literal["offline_baseline", "replay", "model_proposals"] = "offline_baseline"


class Proposal(FrozenModel):
    family: Literal["steane", "bicycle_chain", "bivariate_bicycle", "hgp_repetition"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    source_keys: tuple[str, ...] = ()


class Evidence(FrozenModel):
    evidence_id: str
    claim: str
    domain_sha256: str
    status: Literal["verified", "refuted", "unknown"]
    kind: Literal["exact_algebra", "numerical_check", "statistical_estimate", "assumption", "derived"]
    method: str
    parents: tuple[str, ...] = ()
    payload: dict[str, Any] = Field(default_factory=dict)
    source_keys: tuple[str, ...] = ()


class Requirement(FrozenModel):
    requirement_id: str
    need: str
    proposed_structure: str
    scope: str
    hardness: Literal["task_hard", "route_conditional", "preference"]
    status: Literal["verified", "refuted", "unknown"] = "unknown"
    source_keys: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
