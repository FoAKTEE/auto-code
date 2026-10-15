"""Minimal M1 campaign runner (prompt §7; appendix §J3, §K, §L, §M, §N).

freeze (clean evaluator tree, manifest) → per task: request (task + verdict-free digest + grammar
manifest), syntactic request screening, reserve → dispatch → provider call (call mode, no tools) →
settle with actual usage → L0 caps + strict Proposal parse → pipeline.evaluate → stop when the task
is satisfied or the proposal budget is spent → templated report → sealed bundle → offline audit
(the Phase-1a confirmation: re-execution from the sealed bundle).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from ..admission import Admission
from ..contracts import (Frozen, Profile, Proposal, Reservation, TaskSA, TaskSF, content_hash)
from ..firewall.lint import check_fingerprints, family_terms, lint_contract, lint_request
from ..grammar import MANIFESTS, GrammarCaps
from ..grammar.rawjson import check_raw_json
from ..pipeline import Evaluation, Pipeline, build_registry, evaluator_domain
from ..report.bundle import audit_bundle, seal
from ..report.render import CampaignRecord, EvaluationRef, render
from ..store import ArtifactStore
from .digest import render_digest
from .jobs import BudgetExceeded, JobBook

REPO = Path(__file__).resolve().parents[3]
FROZEN_PATHS = ("src", "roles", "configs", "pyproject.toml", "requirements.lock")


class CampaignConfig(Frozen):
    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    profile: Profile
    tasks: tuple[TaskSA | TaskSF, ...]
    provider: Literal["claude", "codex", "replay"]
    model: str
    effort: str | None = None
    max_proposals_per_task: int = Field(ge=1, le=1000)
    caps: dict[str, float]
    per_call: Reservation
    distance_budget: dict[str, int] = Field(default_factory=dict)
    max_n: int = Field(default=200, ge=1, le=1024)
    labels: tuple[str, ...] = ("single-implementation", "non-evaluation")


@dataclass
class CallResult:
    outcome: Literal["completed", "launch_failure", "timeout", "resource_killed", "policy_blocked",
                     "uncertain", "unsupported"]
    text: str | None
    usage: dict[str, float] = field(default_factory=dict)
    record: dict[str, Any] = field(default_factory=dict)      # sanitized call record


class Provider(Protocol):
    name: str
    model: str

    def call(self, *, job_id: str, system_prompt: str, request: str, schema: dict,
             reservation: Reservation) -> CallResult: ...


def contract_path(profile: Profile) -> Path:
    return REPO / "roles" / "designer" / f"{profile.prompt}.md"


def grammar_for(profile: Profile) -> dict[str, Any]:
    fams = ["explicit_sparse"] + (["two_block_group_algebra"] if "G-struct" in profile.grammar else [])
    return {f: MANIFESTS[f] for f in fams}


def git_state() -> dict[str, Any]:
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", *FROZEN_PATHS],
                           capture_output=True, text=True, check=True).stdout.strip()
    return {"commit": head, "dirty": bool(dirty), "dirty_paths": dirty.splitlines()[:20]}


class EvaluatorDirty(RuntimeError):
    pass


class LaunchFailures(RuntimeError):
    """Repeated provider launch failures: an infrastructure fault that stops the campaign."""


# An interrupted, killed or blocked call may have been billed: it keeps its full reservation
# (uncertain) unless complete accounting establishes the charge (§K). Only a call that never reached
# the provider (launch failure, unsupported) settles as failed, still charging every unmeasured unit.
UNCERTAIN_OUTCOMES = frozenset({"uncertain", "timeout", "resource_killed", "policy_blocked"})


def outcome_state(outcome: str) -> str:
    if outcome == "completed":
        return "completed"
    return "uncertain" if outcome in UNCERTAIN_OUTCOMES else "failed"


def _task_view(task: TaskSA | TaskSF) -> dict[str, Any]:
    d = task.model_dump(mode="json")
    d.pop("task_id", None)                    # identifiers are not shown to proposers (§N)
    return d


def run_campaign(cfg: CampaignConfig, provider: Provider, run_root: Path, *,
                 require_clean: bool = True, seal_bundle: bool = True,
                 archive: Path | None = None) -> dict[str, Any]:
    gs = git_state()
    if require_clean and gs["dirty"]:
        raise EvaluatorDirty(f"evaluator tree is dirty: {gs['dirty_paths']}")
    problems = check_fingerprints(REPO / "roles")
    contract = contract_path(cfg.profile).read_text()
    problems += lint_contract(contract, cfg.profile.prompt)
    if problems:
        raise RuntimeError(f"role contract checks failed: {problems}")
    schema = json.loads((REPO / "roles" / "designer" / "output.schema.json").read_text())
    run = run_root / cfg.campaign_id
    run.mkdir(parents=True, exist_ok=False)
    store = ArtifactStore(run / "store")
    reg = build_registry(store)
    adm = Admission(run / "ledger.db", store, reg)
    dom = evaluator_domain(reg)
    book = JobBook(adm.ledger.conn, cfg.campaign_id)
    book.set_caps(cfg.caps)
    pipe = Pipeline(adm, store, book, dom, caps=GrammarCaps(max_n=cfg.max_n),
                    distance_budget=cfg.distance_budget)
    manifest = {"campaign_id": cfg.campaign_id, "config_sha256": content_hash(cfg),
                "config": cfg.model_dump(mode="json"), "domain": dom, "registry": reg.manifest(),
                "git": gs, "contract_sha256": content_hash(contract),
                "provider": provider.name, "model": provider.model}
    manifest_sha = store.put_json(manifest)
    (run / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    evaluations: list[EvaluationRef] = []
    autonomy_log: list[dict[str, Any]] = []
    calls_dir = run / "calls"; calls_dir.mkdir()
    stop_reason = {}
    try:
        for task in cfg.tasks:
            history: list[Evaluation] = []
            launch_failures = 0
            stop_reason[task.task_id] = "proposal_budget_spent"
            for i in range(cfg.max_proposals_per_task):
                request = json.dumps({"task": _task_view(task), "digest": json.loads(render_digest(history)),
                                      "grammar": grammar_for(cfg.profile)}, sort_keys=True)
                if lint_request(request, [task.task_id]):
                    raise RuntimeError("rendered request leaked a task identifier")
                if cfg.profile.prompt == "P-min" and family_terms(request):
                    raise RuntimeError("rendered P-min request names a code family/construction")
                try:
                    job = book.reserve("propose", f"{task.task_id}:{i}", cfg.per_call,
                                       input_sha=content_hash(request))
                except BudgetExceeded as e:
                    stop_reason[task.task_id] = f"budget: {e}"
                    break
                gen = book.dispatch(job)
                res = provider.call(job_id=job, system_prompt=contract, request=request,
                                    schema=schema, reservation=cfg.per_call)
                rec = {**res.record, "job_id": job, "outcome": res.outcome, "usage": res.usage,
                       "request_sha256": content_hash(request)}
                rec_sha = store.put_json(rec)
                (calls_dir / f"{task.task_id}-{i:03d}.json").write_text(json.dumps(rec, indent=2, sort_keys=True))
                if res.outcome == "launch_failure" and res.text is None:
                    launch_failures += 1
                else:
                    launch_failures = 0
                state = outcome_state(res.outcome)
                book.complete(job, gen, state, actual=res.usage or None)
                book.settle(job)
                proposal = None
                # An "uncertain" call keeps its full reservation, but if it produced output the
                # proposal is still evaluated: scientific authority comes from verifiers, not the call.
                if res.outcome in ("completed", "uncertain") and res.text is not None:
                    try:
                        check_raw_json(res.text, max_bytes=2_000_000, max_depth=12, max_list_len=200_000)
                        proposal = Proposal.model_validate_json(res.text)
                    except Exception as e:                       # noqa: BLE001
                        history.append(Evaluation("malformed_proposal", {"reason": str(e)[:200]}))
                if proposal is None:
                    if res.outcome not in ("completed",) and res.text is None:
                        history.append(Evaluation(res.outcome, {"reason": res.outcome}))
                    if launch_failures >= 2:
                        # Infrastructure fault, not science: stop instead of burning attempts (§4.14).
                        raise LaunchFailures(f"{task.task_id}: {launch_failures} consecutive launch "
                                             f"failures; see {calls_dir}")
                    continue
                p_sha = store.put_json(proposal.model_dump(mode="json"))
                ev = pipe.evaluate(proposal, task)
                history.append(ev)
                if ev.candidate is not None:
                    evaluations.append(EvaluationRef(task_id=task.task_id, candidate=ev.candidate,
                                                     proposal=p_sha, source=f"designer:{provider.name}",
                                                     call_record=rec_sha))
                if ev.satisfies == "verified":
                    stop_reason[task.task_id] = "target_met"
                    break
        camp = CampaignRecord(campaign_id=cfg.campaign_id, manifest={**manifest, "manifest_sha256": manifest_sha},
                              tasks=cfg.tasks, evaluations=tuple(evaluations),
                              autonomy_log=tuple(autonomy_log), budget_totals=book.totals(),
                              labels=cfg.labels)
        report_md = render(adm, dom, camp)
        (run / "report.md").write_text(report_md)
        (run / "campaign.json").write_text(json.dumps(camp.model_dump(mode="json"), indent=2, sort_keys=True))
        totals = book.totals()
    finally:
        adm.close()
    out: dict[str, Any] = {"run_dir": str(run), "stop": stop_reason, "evaluations": len(evaluations),
                           "satisfied": sorted(k for k, v in stop_reason.items() if v == "target_met"),
                           "totals": totals}
    if seal_bundle:
        kw = {"archive": archive} if archive else {}
        entry = seal(run, cfg.campaign_id, run / "ledger.db", run / "store", report_md,
                     camp.model_dump(mode="json"), extra_dirs={"calls": calls_dir},
                     commit=gs["commit"], **kw)
        audit = audit_bundle(Path(entry["path"]), workdir=run / "audit", **kw)
        out.update(bundle=entry, audit={k: audit[k] for k in ("ok", "problems") if k in audit})
    return out
