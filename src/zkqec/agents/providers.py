"""Production providers: sandboxed call-mode CLI invocations through the qualified broker (§J, §K).

``ClaudeCallProvider`` is the only K0-qualified provider (qualification 20261015T105932Z: Claude runs
tool-free; Codex declares tools via ``additional_tools`` and is K0-unenforced, D16). Ceilings are
derived from the job's reservation and enforced at the broker (requests, input bytes, output tokens)
and by the CLI (``--max-budget-usd``); the scope's wall time bounds the call. Usage comes from the
broker log (authoritative for tokens/requests) and the CLI JSON (list-price USD). Incomplete
telemetry or a possibly-billed failure is reported as ``uncertain`` so the job keeps its full
reservation (§K).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..broker.launcher import Broker, Ceilings, summarize_job
from ..contracts import Reservation, content_hash
from ..orchestrator.campaign import CallResult
from ..sandbox.cgroup import CampaignSlice
from ..sandbox.profiles import agent_call_profile
from ..sandbox.runner import JobSpec, Limits, run_job
from .launch import CallSpec, claude_call


class ClaudeCallProvider:
    name = "claude"

    def __init__(self, model: str, *, broker: Broker, slice_: CampaignSlice, jobs_root: Path,
                 effort: str | None = None):
        self.model, self.broker, self.slice, self.jobs_root, self.effort = (
            model, broker, slice_, Path(jobs_root), effort)
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def call(self, *, job_id: str, system_prompt: str, request: str, schema: dict,
             reservation: Reservation) -> CallResult:
        ceil = Ceilings(max_model_requests=max(1, reservation.requests),
                        max_total_requests=max(1, reservation.requests) + 4,
                        max_input_bytes=reservation.input_tokens,       # bytes ≥ tokens (§K)
                        max_output_tokens=reservation.output_tokens,
                        ttl_seconds=reservation.wall_seconds + 60)
        spec = CallSpec("claude", self.model, system_prompt, request, schema, effort=self.effort,
                        max_budget_usd=reservation.usd, max_output_tokens=reservation.output_tokens,
                        max_retries=max(0, reservation.requests - 1))
        launch = claude_call(spec)
        token = self.broker.grant(job_id, "claude", [self.model], ceil)
        try:
            res = run_job(JobSpec(job_id, agent_call_profile("claude"), launch.argv, launch.env,
                                  inputs=launch.inputs, home_files=launch.home_files,
                                  stdin_input=launch.stdin_input,
                                  limits=Limits(wall_seconds=reservation.wall_seconds),
                                  ca_cert=str(self.broker.ca_cert), broker_port=self.broker.port,
                                  token=token),
                          self.jobs_root, self.slice, blocked_probe=self.broker.blocked_reason)
        finally:
            self.broker.revoke(token)
        summ = summarize_job(self.broker.records(job_id))
        cli: dict[str, Any] = {}
        try:
            cli = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
        except ValueError:
            cli = {}
        structured = cli.get("structured_output") if isinstance(cli, dict) else None
        usage_known = summ["usage"] != "unknown" and summ["usage_missing"] == 0
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
            "telemetry_complete": bool(usage_known),
        }
        tokens = summ["usage"] if usage_known else {}
        usage = {"requests": float(summ["model_requests"]), "wall_seconds": float(res.wall_seconds)}
        if usage_known:
            usage["input_tokens"] = float(tokens.get("input_tokens", 0) + tokens.get("cache_creation_input_tokens", 0)
                                          + tokens.get("cache_read_input_tokens", 0))
            usage["output_tokens"] = float(tokens.get("output_tokens", 0))
        if isinstance(cli, dict) and cli.get("total_cost_usd") is not None:
            usage["usd"] = float(cli["total_cost_usd"])
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
        return CallResult(outcome, text, usage, record)
