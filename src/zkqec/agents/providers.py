"""Production providers: sandboxed call-mode CLI invocations through the qualified broker (§J, §K).

``ClaudeCallProvider`` is the only K0-qualified provider (qualification 20261015T105932Z: Claude runs
tool-free; Codex declares tools via ``additional_tools`` and is K0-unenforced, D16). The job's
reservation is the invocation total; per-request ceilings (total ÷ requests) are derived from it and
enforced at the broker (requests, input bytes, output tokens) and by the CLI (``max_tokens``,
retries, ``--max-budget-usd``); the scope's wall time bounds the call. Usage comes from the
broker log (authoritative for tokens/requests) and the CLI JSON (list-price USD). The reservation
must cover the maximum charge of those ceilings under the declared price table
(:func:`check_reservation`, enforced before a live campaign starts). Incomplete
telemetry or a possibly-billed failure is reported as ``uncertain`` so the job keeps its full
reservation (§K).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..broker.launcher import Broker, Ceilings, summarize_job
from ..broker.policy import Grant, charged_tokens, reservation_bound
from ..contracts import Reservation, content_hash
from ..orchestrator.campaign import CallResult
from ..sandbox.cgroup import CampaignSlice
from ..sandbox.profiles import agent_call_profile
from ..sandbox.runner import JobSpec, Limits, run_job
from .launch import CallSpec, claude_call


PRICES = Path(__file__).resolve().parents[3] / "configs" / "broker" / "prices.json"


class ReservationBelowBound(ValueError):
    """A per-call reservation that does not cover the §K maximum charge of its ceilings (or that
    reserves no model request)."""


def call_ceilings(reservation: Reservation) -> Ceilings:
    """Broker ceilings of one call, derived from its reservation (the only derivation).

    A reservation is the **invocation total** (Codex M1 milestone review B8): every request the grant
    allows (the call plus its CLI retries) gets ``total // requests`` input bytes (bytes ≥ tokens, §K)
    and output tokens, so the allowed requests together never exceed the reserved units. A live call
    needs at least one reserved model request."""
    n = reservation.requests
    if n < 1:
        raise ReservationBelowBound("a live call reserves at least one model request (requests >= 1)")
    return Ceilings(max_model_requests=n, max_total_requests=n + 4,
                    max_input_bytes=reservation.input_tokens // n,
                    max_output_tokens=reservation.output_tokens // n,
                    ttl_seconds=reservation.wall_seconds + 60)


def reservation_floor(model: str, reservation: Reservation, prices: dict | None = None) -> float | None:
    """§K maximum total charge of one call: the reserved input total × the highest input price of any
    tier (cache writes included) + the reserved output total × the highest output price, from the
    declared price table. It covers the broker's ceilings (``call_ceilings``: requests × ⌊total ÷
    requests⌋ ≤ total), which are checked against it. None when the model has no declared price."""
    table = prices if prices is not None else json.loads(PRICES.read_text())
    row = table.get("models", {}).get(model) or {}
    p_in = [v for k, v in row.items() if k.startswith(("input", "cache_write")) and v is not None]
    p_out = [v for k, v in row.items() if k.startswith("output") and v is not None]
    if not p_in or not p_out:
        return None
    c = call_ceilings(reservation)
    grant = Grant("bound", "claude", frozenset([model]), c.max_model_requests, c.max_total_requests,
                  c.max_input_bytes, c.max_output_tokens, 0)
    totals = (reservation.input_tokens * max(p_in) + reservation.output_tokens * max(p_out)) / 1e6
    return max(totals, reservation_bound(grant, max(p_in), max(p_out)))


def check_reservation(model: str, reservation: Reservation) -> float:
    """The floor, or :class:`ReservationBelowBound` if the reservation does not cover it."""
    floor = reservation_floor(model, reservation)
    if floor is None:
        raise ReservationBelowBound(f"{model} has no declared price in {PRICES.name}")
    if reservation.usd < floor - 1e-9:
        raise ReservationBelowBound(f"per-call reservation ${reservation.usd} is below the §K bound "
                                    f"${floor:.4f} of its ceilings for {model}")
    return floor


class ClaudeCallProvider:
    name = "claude"

    def __init__(self, model: str, *, broker: Broker, slice_: CampaignSlice, jobs_root: Path,
                 effort: str | None = None):
        self.model, self.broker, self.slice, self.jobs_root, self.effort = (
            model, broker, slice_, Path(jobs_root), effort)
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def call(self, *, job_id: str, system_prompt: str, request: str, schema: dict,
             reservation: Reservation) -> CallResult:
        ceil = call_ceilings(reservation)               # raises before any grant or launch
        spec = CallSpec("claude", self.model, system_prompt, request, schema, effort=self.effort,
                        max_budget_usd=reservation.usd, max_output_tokens=ceil.max_output_tokens,
                        max_retries=ceil.max_model_requests - 1)
        launch = claude_call(spec)
        # AF_UNIX socket paths are limited to 108 bytes: the sandbox job (and its broker grant) use a
        # 16-hex prefix of the job id under a short host jobs root.
        sandbox_job = job_id[:16]
        token = self.broker.grant(sandbox_job, "claude", [self.model], ceil)
        try:
            res = run_job(JobSpec(sandbox_job, agent_call_profile("claude"), launch.argv, launch.env,
                                  inputs=launch.inputs, home_files=launch.home_files,
                                  stdin_input=launch.stdin_input,
                                  limits=Limits(wall_seconds=reservation.wall_seconds),
                                  ca_cert=str(self.broker.ca_cert), broker_port=self.broker.port,
                                  token=token),
                          self.jobs_root, self.slice, blocked_probe=self.broker.blocked_reason)
        finally:
            self.broker.revoke(token)
        summ = summarize_job(self.broker.records(sandbox_job))
        cli: dict[str, Any] = {}
        try:
            cli = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
        except ValueError:
            cli = {}
        structured = cli.get("structured_output") if isinstance(cli, dict) else None
        usage_known = summ["usage"] != "unknown" and summ["usage_missing"] == 0
        # Codex M1 re-review N2: tokens only with both mandatory counters, never a default of 0
        tokens = charged_tokens(summ["usage"]) if usage_known else None
        usage_known = tokens is not None
        record = {
            "provider": "claude", "model": self.model, "effort": self.effort,
            "launch": {**launch.describe(), "argv": [a if len(a) < 400 else f"<{len(a)} chars>"
                                                     for a in launch.describe()["argv"]]},
            "profile_sha256": res.profile_sha256, "job_outcome": res.outcome,
            "exit_code": res.exit_code, "wall_seconds": res.wall_seconds,
            "blocked_reason": res.blocked_reason,
            "broker": {k: summ[k] for k in ("upstream_requests", "model_requests", "blocked", "models",
                                            "declared_tools", "usage", "usage_records",
                                            "usage_missing", "failed")},
            "cli": {k: cli.get(k) for k in ("total_cost_usd", "num_turns", "duration_ms",
                                            "duration_api_ms", "is_error", "stop_reason", "subtype")}
            if isinstance(cli, dict) else {},
            "stdout_sha256": content_hash(res.stdout.decode("utf-8", "replace")),
            # The raw CLI stdout/stderr are retained in full as sanitized artifacts (CallResult.raw).
            "sandbox_job_id": sandbox_job,
            "cli_stderr_tail": res.stderr.decode("utf-8", "replace")[-2000:],
            "telemetry_complete": bool(usage_known),
        }
        usage = {"requests": float(summ["model_requests"]), "wall_seconds": float(res.wall_seconds)}
        if tokens is not None:
            usage.update(tokens)
        if isinstance(cli, dict) and cli.get("total_cost_usd") is not None:
            usage["usd"] = float(cli["total_cost_usd"])
        if summ["model_requests"] == 0 and not summ["failed"]:
            # The broker logs every upstream request before forwarding it (§L): no model request
            # forwarded establishes a zero model charge. Otherwise unmeasured units keep the
            # reservation at settlement.
            usage.update(usd=0.0, input_tokens=0.0, output_tokens=0.0)
        if res.outcome == "policy_blocked" or summ["failed"]:
            outcome = "policy_blocked"
        elif res.outcome == "timeout":
            outcome = "timeout"
        elif res.outcome == "launch_failure":
            outcome = "launch_failure"
        elif res.outcome == "completed" and isinstance(structured, dict) and not cli.get("is_error"):
            outcome = "completed"
        else:
            outcome = "uncertain" if summ["model_requests"] else "launch_failure"
        if outcome != "completed" and summ["model_requests"] and not ("usd" in usage and usage_known):
            outcome = "uncertain"                       # possibly billed, charge not established
        if outcome == "completed" and not (usage_known and "usd" in usage):
            outcome = "uncertain"                       # result exists but the charge is not proven
            record["note"] = "structured output produced but telemetry incomplete; charged in full"
        text = json.dumps(structured, sort_keys=True) if isinstance(structured, dict) else None
        if outcome == "uncertain" and text is not None:
            # The proposal is still evaluable (evidence comes from verifiers), only the charge is
            # uncertain; the orchestrator settles it at the full reservation.
            pass
        return CallResult(outcome, text, usage, record,
                          raw={"stdout": bytes(res.stdout), "stderr": bytes(res.stderr)})
