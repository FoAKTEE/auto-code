"""Minimal M1 campaign runner (prompt §7; appendix §J3, §K, §L, §M, §N).

freeze (clean evaluator tree, manifest, ``campaign_frozen`` ledger event) → per task: request
(task + verdict-free digest + grammar manifest), syntactic request screening, evaluator-fingerprint
and frozen-task checks, reserve → dispatch → provider call (call mode, no tools) → settle with
actual usage (a ``job_settled`` event in the same transaction) → strict intake → frozen-task check →
pipeline.evaluate → ``evaluation_recorded`` → stop when the task is satisfied or the proposal budget
is spent → [``controls``: the §I control suite in a scratch directory, as a verifier-class job reserved
at freeze; its evidence stored and recorded with ``controls_recorded``] → templated report (from the
ledger only) → sealed bundle → offline audit (the Phase-1a confirmation: re-execution from the
sealed bundle, the control suite included).
"""
from __future__ import annotations

import json
import resource
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from .. import controls
from ..admission import Admission
from ..contracts import (CampaignLabel, Frozen, Profile, Proposal, Reservation, TaskSA, TaskSF,
                         content_hash)
from ..firewall.lint import check_fingerprints, family_terms, lint_contract, lint_request
from ..grammar import MANIFESTS, GrammarCaps
from ..grammar.intake import parse_proposal
from ..pipeline import Evaluation, Pipeline, build_registry, evaluator_domain
from ..report.bundle import audit_bundle, seal
from ..report.render import render
from ..store import ArtifactStore
from . import freeze
from .digest import render_digest
from .jobs import BudgetExceeded, namespace

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
    # §K: per-verifier-job ceilings (wall enforced, CPU and wall charged as measured) and the
    # confirmation (offline re-execution) capacity reserved before any proposal is dispatched.
    verifier_cpu_seconds: int = Field(default=600, ge=1)
    verifier_wall_seconds: int = Field(default=600, ge=1)
    confirmation: Reservation = Reservation(usd=0, requests=0, input_tokens=0, output_tokens=0,
                                            wall_seconds=1800, cpu_seconds=1800)
    deadline_seconds: int | None = Field(default=None, ge=0)   # campaign wall-clock deadline
    labels: tuple[CampaignLabel, ...] = ("single-implementation", "non-evaluation")
    controls: Literal["p1a"] | None = None    # §I control set run after dispatch, before sealing


@dataclass
class CallResult:
    outcome: Literal["completed", "launch_failure", "timeout", "resource_killed", "policy_blocked",
                     "uncertain", "unsupported"]
    text: str | None
    usage: dict[str, float] = field(default_factory=dict)
    record: dict[str, Any] = field(default_factory=dict)      # sanitized call record
    raw: dict[str, bytes] = field(default_factory=dict)       # raw provider outputs (stdout, ...)


class Provider(Protocol):
    name: str
    model: str

    def call(self, *, job_id: str, system_prompt: str, request: str, schema: dict,
             reservation: Reservation) -> CallResult: ...


def profile_key(profile: Profile) -> str:
    """Namespace component for a profile (§N), e.g. ``K0.P-min.G-open``."""
    return ".".join((profile.knowledge, profile.prompt, "+".join(profile.grammar)))


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
                 archive: Path | None = None,
                 deadline_seconds: float | None = None) -> dict[str, Any]:
    started = time.monotonic()
    limits = [x for x in (cfg.deadline_seconds, deadline_seconds) if x is not None]
    deadline = started + min(limits) if limits else None
    gs = git_state()
    if require_clean and gs["dirty"]:
        raise EvaluatorDirty(f"evaluator tree is dirty: {gs['dirty_paths']}")
    # §4.3/§O: content fingerprint of every evaluator file, equal to the recorded commit (when the
    # tree must be clean) and re-checked before every provider dispatch and verifier run.
    guard = freeze.EvaluatorGuard(REPO, commit=gs["commit"] if require_clean else None)
    problems = check_fingerprints(REPO / "roles")
    contract = contract_path(cfg.profile).read_text()
    problems += lint_contract(contract, cfg.profile.prompt)
    if problems:
        raise RuntimeError(f"role contract checks failed: {problems}")
    schema = json.loads((REPO / "roles" / "designer" / "output.schema.json").read_text())
    schema_bytes = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()  # as sent
    run = run_root / cfg.campaign_id
    run.mkdir(parents=True, exist_ok=False)
    store = ArtifactStore(run / "store")
    schema_sha = store.put_bytes(schema_bytes, "json")       # frozen with the manifest (§I inputs)
    reg = build_registry(store)
    adm = Admission(run / "ledger.db", store, reg)
    dom = evaluator_domain(reg)
    config = cfg.model_dump(mode="json")
    manifest = {"campaign_id": cfg.campaign_id, "config_sha256": content_hash(config),
                "config": config, "domain": dom, "registry": reg.manifest(),
                "git": gs, "contract_sha256": content_hash(contract),
                "evaluator_fingerprint": guard.manifest(), "output_schema": schema_sha,
                "provider": provider.name, "model": provider.model}
    manifest_sha = store.put_json(manifest)
    (run / "manifest.json").write_bytes(store.get_bytes(manifest_sha))
    prof = profile_key(cfg.profile)
    confirmation = config["confirmation"]
    adm.freeze_campaign(cfg.campaign_id, namespace=namespace(cfg.campaign_id, profile=prof),
                        config=config, tasks=config["tasks"], caps=dict(cfg.caps),
                        labels=list(cfg.labels), domain=dom, manifest=manifest_sha,
                        confirmation={u: float(v) for u, v in confirmation.items()})
    book = adm.job_book(cfg.campaign_id, profile=prof)
    book.set_caps(cfg.caps)
    try:
        # §K: confirmation (and control) capacity is reserved before any proposal is dispatched.
        confirm_job = book.reserve("confirm", "confirmation", cfg.confirmation)
        controls_job = None
        if cfg.controls is not None:
            controls_job = book.reserve("verify", f"controls:{cfg.controls}",
                                        _verifier_reservation(cfg), input_sha=manifest_sha)
    except BaseException:
        adm.close()
        raise
    check_evaluator = freeze.boundary_check(guard, adm)
    families = tuple(grammar_for(cfg.profile))
    pipe = Pipeline(adm, store, book, dom, caps=GrammarCaps(max_n=cfg.max_n),
                    distance_budget=cfg.distance_budget, families=families,
                    cpu_reservation=cfg.verifier_cpu_seconds,
                    wall_reservation=cfg.verifier_wall_seconds, guard=check_evaluator)
    evaluations = 0
    calls_dir = run / "calls"; calls_dir.mkdir()
    stop_reason = {}
    try:
        for task in cfg.tasks:
            history: list[Evaluation] = []
            launch_failures = 0
            stop_reason[task.task_id] = "proposal_budget_spent"
            if deadline is not None and time.monotonic() >= deadline:
                stop_reason[task.task_id] = "deadline"
                continue
            for i in range(cfg.max_proposals_per_task):
                if deadline is not None and time.monotonic() >= deadline:
                    stop_reason[task.task_id] = "deadline"         # §7 stop rule: no new dispatch
                    break
                request = json.dumps({"task": _task_view(task), "digest": json.loads(render_digest(history)),
                                      "grammar": grammar_for(cfg.profile)}, sort_keys=True)
                if lint_request(request, [task.task_id]):
                    raise RuntimeError("rendered request leaked a task identifier")
                if cfg.profile.prompt == "P-min" and family_terms(request):
                    raise RuntimeError("rendered P-min request names a code family/construction")
                check_evaluator("provider dispatch")          # typed abort before reserving
                freeze.check_task(adm, cfg.campaign_id, task)   # §I mode 1: frozen task hash
                try:
                    job = book.reserve("propose", f"{task.task_id}:{i}", cfg.per_call,
                                       input_sha=content_hash(request))
                except BudgetExceeded as e:
                    stop_reason[task.task_id] = f"budget: {e}"
                    break
                gen = book.dispatch(job)
                res = provider.call(job_id=job, system_prompt=contract, request=request,
                                    schema=schema, reservation=cfg.per_call)
                # §M: every attempt retains its exact inputs and raw outputs as sanitized
                # artifacts, referenced from the call record before settlement.
                arts = {"request": store.put_bytes(request.encode(), "prompt"),
                        "system_prompt": store.put_bytes(contract.encode(), "prompt"),
                        "schema": store.put_bytes(schema_bytes, "json")}
                if res.text is not None:
                    arts["output_text"] = store.put_bytes(res.text.encode(), "response")
                for name, data in sorted(res.raw.items()):
                    arts[f"raw_{name}"] = store.put_bytes(bytes(data), "response")
                rec = {**res.record, "job_id": job, "outcome": res.outcome, "usage": res.usage,
                       "request_sha256": content_hash(request), "artifacts": arts}
                rec_sha = store.put_json(rec)
                # The sidecar is the sanitized, content-addressed bytes, never the original (§M).
                (calls_dir / f"{task.task_id}-{i:03d}.json").write_bytes(store.get_bytes(rec_sha))
                if res.outcome == "launch_failure" and res.text is None:
                    launch_failures += 1
                else:
                    launch_failures = 0
                state = outcome_state(res.outcome)
                book.complete(job, gen, state, actual=res.usage or None, result_sha=rec_sha)
                book.settle(job)                  # + job_settled event, same transaction (§L)
                proposal = None
                # An "uncertain" call keeps its full reservation, but if it produced output the
                # proposal is still evaluated: scientific authority comes from verifiers, not the call.
                if res.outcome in ("completed", "uncertain") and res.text is not None:
                    try:      # strict parse + full schema + frozen profile, before construction
                        proposal = parse_proposal(res.text, schema=schema, families=families)
                    except Exception as e:                       # noqa: BLE001
                        reason = getattr(e, "reason", None) or str(e)
                        history.append(Evaluation("malformed_proposal", {"reason": reason[:200]}))
                if proposal is None:
                    if res.outcome not in ("completed",) and res.text is None:
                        history.append(Evaluation(res.outcome, {"reason": res.outcome}))
                    if launch_failures >= 2:
                        # Infrastructure fault, not science: stop instead of burning attempts (§4.14).
                        raise LaunchFailures(f"{task.task_id}: {launch_failures} consecutive launch "
                                             f"failures; see {calls_dir}")
                    continue
                p_sha = store.put_json(proposal.model_dump(mode="json"))
                freeze.check_task(adm, cfg.campaign_id, task)
                ev = pipe.evaluate(proposal, task)
                history.append(ev)
                if ev.candidate is not None:
                    adm.record_evaluation(cfg.campaign_id, task.task_id, candidate=ev.candidate,
                                          proposal=p_sha, call_record=rec_sha,
                                          source=f"designer:{provider.name}", artifacts=arts)
                    evaluations += 1
                if ev.satisfies == "verified":
                    stop_reason[task.task_id] = "target_met"
                    break
        control_summary = None
        if controls_job is not None:
            control_summary = _run_controls(cfg, adm, store, book, controls_job, run / "controls")
        report_md = render(adm, cfg.campaign_id)          # from the ledger only (§A5)
        (run / "report.md").write_text(report_md)
        totals = book.totals()
    finally:
        adm.close()
    out: dict[str, Any] = {"run_dir": str(run), "stop": stop_reason, "evaluations": evaluations,
                           "satisfied": sorted(k for k, v in stop_reason.items() if v == "target_met"),
                           "totals": totals}
    if control_summary is not None:
        out["controls"] = control_summary
    if seal_bundle:
        kw = {"archive": archive} if archive else {}
        entry = seal(run, cfg.campaign_id, run / "ledger.db", run / "store", report_md,
                     extra_dirs={"calls": calls_dir}, commit=gs["commit"], **kw)
        audit, confirmation_charge = _confirm(run, cfg, prof, store, confirm_job, entry, kw)
        out.update(bundle=entry, audit={k: audit[k] for k in ("ok", "problems") if k in audit},
                   confirmation=confirmation_charge)
    return out


def _verifier_reservation(cfg: CampaignConfig) -> Reservation:
    return Reservation(usd=0, requests=0, input_tokens=0, output_tokens=0,
                       wall_seconds=cfg.verifier_wall_seconds, cpu_seconds=cfg.verifier_cpu_seconds)


def _run_controls(cfg: CampaignConfig, adm: Admission, store: ArtifactStore, book, job: str,
                  scratch: Path) -> dict[str, Any]:
    """§I controls after dispatch and before sealing, as the verifier-class job reserved at freeze
    (#13 pattern: measured CPU and wall charged). Inputs come from the frozen campaign; the evidence
    goes through the sanitizing store and ``controls_recorded`` commits with the job's settlement.
    A failing suite is charged, recorded as a ``controls_failed`` incident and records no summary."""
    gen = book.dispatch(job)
    t0, w0 = time.process_time(), time.monotonic()

    def settle(state: str) -> None:
        usage = {"cpu_seconds": max(0.0, time.process_time() - t0),
                 "wall_seconds": max(0.0, time.monotonic() - w0)}
        book.complete(job, gen, state, actual=usage)
        book.settle(job)
    try:
        inputs = controls.campaign_inputs(adm, store, cfg.campaign_id)
        evidence = controls.run_suite(cfg.controls, scratch, inputs)
        sha = store.put_json(evidence)
    except Exception as exc:                                       # noqa: BLE001
        settle("failed")
        adm.open_incident("controls_failed", {"control_set": cfg.controls,
                                              "error": type(exc).__name__})
        return {"control_set": cfg.controls, "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    rows = evidence["results"]
    adm.record_controls(cfg.campaign_id, cfg.controls, rows, evidence=sha,
                        on_close=lambda: settle("completed"))
    return {"control_set": cfg.controls, "ok": True, "evidence": sha,
            **{k: sum(r[k] for r in rows) for k in ("injections", "false_authorizations",
                                                    "paired_positives", "false_rejections")}}


def _confirm(run: Path, cfg: CampaignConfig, prof: str, store: ArtifactStore, job: str,
             entry: dict, kw: dict) -> tuple[dict, dict]:
    """Phase-1a confirmation (§7.4): offline re-execution from the sealed bundle, run under the
    confirmation job reserved at freeze. Its dispatch and settlement happen after the bundle was
    sealed (the sealed ledger shows the reservation as reserved, not spent); the measured wall and
    child CPU time are charged in the run ledger."""
    adm = Admission(run / "ledger.db", store, build_registry(store))
    try:
        book = adm.job_book(cfg.campaign_id, profile=prof)
        gen = book.dispatch(job)
        w0 = time.monotonic()
        c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        audit: dict[str, Any] = {"ok": False, "problems": ["confirmation did not complete"]}
        try:
            audit = audit_bundle(Path(entry["path"]), workdir=run / "audit", **kw)
        finally:
            c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            usage = {"wall_seconds": time.monotonic() - w0,
                     "cpu_seconds": (c1.ru_utime + c1.ru_stime) - (c0.ru_utime + c0.ru_stime),
                     "usd": 0.0, "requests": 0.0, "input_tokens": 0.0, "output_tokens": 0.0}
            book.complete(job, gen, "completed" if audit.get("ok") else "failed", actual=usage)
            charged = book.settle(job)
    finally:
        adm.close()
    return audit, charged
