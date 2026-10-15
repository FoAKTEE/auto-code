"""Operational job states and model-call records (prompt appendix §K, §L).

Operational state is kept separate from the scientific event log. A job's lifecycle is
``reserved → dispatched → {completed | uncertain | failed} → settled``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import Frozen

JobState = Literal["reserved", "dispatched", "completed", "uncertain", "failed", "settled"]
JobKind = Literal["propose", "verify", "falsify", "revise_route", "baseline"]

TRANSITIONS: dict[str, frozenset[str]] = {
    "reserved": frozenset({"dispatched", "settled"}),          # settled = cancelled before dispatch
    "dispatched": frozenset({"completed", "uncertain", "failed"}),
    "completed": frozenset({"settled"}),
    "uncertain": frozenset({"settled"}),
    "failed": frozenset({"settled"}),
    "settled": frozenset(),
}


class Reservation(Frozen):
    usd: float = Field(ge=0)
    requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    wall_seconds: int = Field(ge=0)
    cpu_seconds: int = Field(ge=0)


class AgentCallRecord(Frozen):
    job_id: str
    provider: Literal["claude", "codex", "replay", "fake"]
    model: str
    role: str
    request_sha256: str
    response_sha256: str | None
    broker_requests: int = Field(ge=0)
    input_tokens: int | None = None
    output_tokens: int | None = None
    usd_list_price: float | None = None
    declared_tools: tuple[str, ...] = ()
    outcome: Literal["completed", "launch_failure", "timeout", "resource_killed", "unsupported",
                     "malformed_proposal", "policy_blocked", "uncertain"]
    telemetry_complete: bool
