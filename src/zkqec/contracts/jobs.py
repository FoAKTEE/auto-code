"""Operational job states and model-call records (prompt appendix §K, §L).

Operational state is kept separate from the scientific event log. A job's lifecycle is
``reserved → dispatched → {completed | uncertain | failed} → settled``.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field

from .base import Frozen

UNITS = ("usd", "requests", "input_tokens", "output_tokens", "wall_seconds", "cpu_seconds")

JobState = Literal["reserved", "dispatched", "completed", "uncertain", "failed", "settled"]
JobKind = Literal["propose", "verify", "falsify", "revise_route", "baseline", "confirm"]

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


class UsageError(ValueError):
    """Usage or a charge that is not finite, non-negative, typed usage per known unit."""


def check_usage(actual: Any, *, complete: bool = False) -> dict[str, float] | None:
    """Typed usage: known units only, each a finite non-negative int/float (never bool or str).
    ``complete`` additionally requires every unit (a charge or a reservation)."""
    if actual is None:
        if complete:
            raise UsageError("a complete usage record is required")
        return None
    if not isinstance(actual, dict):
        raise UsageError(f"usage must be a dict of units, got {type(actual).__name__}")
    out = {}
    for unit, v in actual.items():
        if unit not in UNITS:
            raise UsageError(f"unknown usage unit {unit!r}")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise UsageError(f"usage {unit}={v!r} must be a finite non-negative number")
        out[unit] = float(v)
    if complete and set(out) != set(UNITS):
        raise UsageError(f"usage must name every unit {UNITS}")
    return out


def charge_for(state: str, reservation: dict, actual: dict | None) -> dict[str, float]:
    """Settlement rule (§K): uncertain or unmeasured ⇒ the reservation; measured units ⇒ the
    measure; a cancelled (never dispatched) job ⇒ zero."""
    if state == "cancelled":
        return {u: 0.0 for u in UNITS}
    if state == "uncertain" or actual is None:
        return {u: float(reservation.get(u, 0.0)) for u in UNITS}
    return {u: float(actual[u]) if u in actual else float(reservation.get(u, 0.0)) for u in UNITS}
