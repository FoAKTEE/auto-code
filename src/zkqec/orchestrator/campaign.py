"""Minimal M1 campaign runner (prompt §7; appendix §J3, §K, §L, §M, §N).

freeze (clean evaluator tree, manifest, ``campaign_frozen`` ledger event) → per task: request
(task + verdict-free digest + grammar manifest), syntactic request screening, evaluator-fingerprint
and frozen-task checks, reserve → dispatch → provider call (call mode, no tools) → settle with
actual usage (a ``job_settled`` event in the same transaction) → strict intake → frozen-task check →
pipeline.evaluate → ``evaluation_recorded`` → stop when the task is satisfied or the proposal budget
is spent → [``controls``: the §I control suite in a scratch directory, as a verifier-class job reserved
at freeze; its evidence stored and recorded with ``controls_recorded``] → templated report (from the
ledger only) → [broker evidence exported] → sealed bundle → offline audit (the Phase-1a confirmation:
re-execution from the sealed bundle, the control suite included) → confirmation supplement.

Evidence order (§M; Codex M1 milestone review B6). A call's inputs (request bytes, system prompt,
schema as sent) are stored durably and named by a ``call_inputs`` intent artifact, which is the job's
``input_sha``, **before** the job is reserved and dispatched: a crash during the call keeps them. A
live campaign's broker is pinned in the frozen manifest (§O) and its evidence (log, effective policy,
declared prices, issued grants, markers, broker manifest) is exported into the run before sealing, so
the bundle carries it. The confirmation runs after sealing; its settlement and the final totals are
published next to the bundle as a supplement whose events chain from the sealed ledger head.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from .. import controls
from ..admission import Admission
from ..exec import Limits, run_bounded
from ..contracts import (CampaignLabel, Frozen, Profile, Proposal, Reservation, TaskSA, TaskSF,
                         content_hash)
from ..firewall.lint import check_fingerprints, family_terms, lint_contract, lint_request
from ..grammar import MANIFESTS, GrammarCaps
from ..grammar.intake import parse_proposal
from ..pipeline import (DEFAULT_MEMORY_BYTES, MEMORY_SLACK_BYTES, Evaluation, Pipeline,
                        build_registry, evaluator_domain)
from ..report.bundle import (audit_bundle, check_confirmation, confirmation_supplement,
                             publish_confirmation, seal)
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


class BrokerEvidence(Protocol):
    """A campaign broker as evidence (``zkqec.broker.launcher.Broker``)."""

    def manifest(self) -> dict[str, Any]: ...

    def export_evidence(self, dest: Path) -> dict[str, Any]: ...


CALL_INPUTS = "zkqec.call_inputs/1"


def profile_key(profile: Profile) -> str:
    """Namespace component for a profile (§N), e.g. ``K0.P-min.G-open``."""
    return ".".join((profile.knowledge, profile.prompt, "+".join(profile.grammar)))


def contract_path(profile: Profile) -> Path:
    return REPO / "roles" / "designer" / f"{profile.prompt}.md"


def grammar_for(profile: Profile) -> dict[str, Any]:
    fams = ["explicit_sparse"] + (["two_block_group_algebra"] if "G-struct" in profile.grammar else [])
    return {f: MANIFESTS[f] for f in fams}


def git_state() -> dict[str, Any]:
    snap = freeze.active_snapshot()
    if snap is not None:      # §O: running from the read-only evaluator snapshot (not a checkout)
        return {"commit": snap["commit"], "dirty": False, "dirty_paths": [],
                "snapshot": snap["evaluator_closure"]}
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
                 deadline_seconds: float | None = None,
                 broker: BrokerEvidence | None = None) -> dict[str, Any]:
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
                "provider": provider.name, "model": provider.model,
                "broker": broker.manifest() if broker is not None else None}
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
                # §M: the call's exact inputs are durable and named by the job's intent before it
                # is reserved and dispatched; outputs are added to the call record afterwards.
                inputs = {"request": store.put_bytes(request.encode(), "prompt"),
                          "system_prompt": store.put_bytes(contract.encode(), "prompt"),
                          "schema": store.put_bytes(schema_bytes, "json")}
                intent = store.put_json({"kind": CALL_INPUTS, "campaign_id": cfg.campaign_id,
                                         "task_id": task.task_id, "attempt": i,
                                         "provider": provider.name, "model": provider.model,
                                         "reservation": cfg.per_call.model_dump(mode="json"),
                                         "artifacts": inputs})
                for sha in (*inputs.values(), intent):
                    store.ensure_durable(sha)
                try:
                    job = book.reserve("propose", f"{task.task_id}:{i}", cfg.per_call,
                                       input_sha=intent)
                except BudgetExceeded as e:
                    stop_reason[task.task_id] = f"budget: {e}"
                    break
                gen = book.dispatch(job)
                res = provider.call(job_id=job, system_prompt=contract, request=request,
                                    schema=schema, reservation=cfg.per_call)
                arts = dict(inputs)
                if res.text is not None:
                    arts["output_text"] = store.put_bytes(res.text.encode(), "response")
                for name, data in sorted(res.raw.items()):
                    arts[f"raw_{name}"] = store.put_bytes(bytes(data), "response")
                rec = {**res.record, "job_id": job, "outcome": res.outcome, "usage": res.usage,
                       "request_sha256": content_hash(request), "inputs": intent, "artifacts": arts}
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
            check_evaluator("controls")               # §O: the control suite is a boundary too
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
    extra_dirs = {"calls": calls_dir}
    if broker is not None:              # sealed with the bundle, never written after it (§M)
        out["broker_manifest"] = broker.export_evidence(run / "broker")
        extra_dirs["broker"] = run / "broker"
    if seal_bundle:
        kw = {"archive": archive} if archive else {}
        entry = seal(run, cfg.campaign_id, run / "ledger.db", run / "store", report_md,
                     extra_dirs=extra_dirs, commit=gs["commit"], **kw)
        guard.check("confirmation")                   # adm is closed: the guard directly
        audit, confirmation_charge, supplement = _confirm(run, cfg, prof, store, confirm_job,
                                                          entry, kw)
        out.update(bundle=entry, audit={k: audit[k] for k in ("ok", "problems") if k in audit},
                   confirmation=confirmation_charge, confirmation_supplement=supplement)
    return out


def _verifier_reservation(cfg: CampaignConfig) -> Reservation:
    return Reservation(usd=0, requests=0, input_tokens=0, output_tokens=0,
                       wall_seconds=cfg.verifier_wall_seconds, cpu_seconds=cfg.verifier_cpu_seconds)


def _limits(cfg: CampaignConfig, reservation: Reservation) -> Limits:
    """Bounded-child ceilings of a job (B5): CPU and wall from its reservation, memory headroom as
    for verifier runs (the distance budget's ``max_memory_bytes`` or the default, plus slack)."""
    memory = int(cfg.distance_budget.get("max_memory_bytes", DEFAULT_MEMORY_BYTES)) + MEMORY_SLACK_BYTES
    return Limits(cpu_seconds=max(1, reservation.cpu_seconds),
                  wall_seconds=max(1, reservation.wall_seconds), memory_bytes=memory)


def _suite(control_set: str, scratch: Path, inputs: Any) -> dict[str, Any]:
    """The control suite inside its bounded child: a suite failure comes back as a value, so the
    evidence it produced survives the process boundary."""
    try:
        return {"evidence": controls.run_suite(control_set, scratch, inputs)}
    except Exception as exc:                                       # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "partial": getattr(exc, "evidence", None)}


def _run_controls(cfg: CampaignConfig, adm: Admission, store: ArtifactStore, book, job: str,
                  scratch: Path) -> dict[str, Any]:
    """§I controls after dispatch and before sealing, as the verifier-class job reserved at freeze.
    The suite runs in a bounded child whose CPU and wall ceilings are that job's reservation (Codex
    M1 milestone review B5); the child's measured CPU and wall are charged, and any outcome other
    than ``ok`` (CPU, wall or memory exceeded, crashed) is a failed suite, never a pass. Inputs come
    from the frozen campaign; the evidence goes through the sanitizing store and
    ``controls_recorded`` commits with the job's settlement. ``ok`` says the suite ran to
    completion; ``passed`` is its pass result (k = 0, j = 0, no outcome mismatch). A failing suite
    keeps the evidence it produced: a failure record (``FAILED_KIND``, with the completed controls)
    is the failed job's settled result and a ``controls_failed`` incident names it (B3). The suite's
    own bounded workers (its pipeline verifiers) stay in the suite's process group, so the job's kill
    reaches them; their unmeasured CPU is charged at the reservation, and an ``escaped`` group
    (members outliving the kill) settles uncertain at the full reservation (Codex M1 re-review N1)."""
    gen = book.dispatch(job)
    t0, w0 = time.process_time(), time.monotonic()
    measured: dict[str, float] = {}
    escaped = False

    def settle(state: str, result: str | None = None) -> None:
        usage = measured or {"cpu_seconds": max(0.0, time.process_time() - t0),
                             "wall_seconds": max(0.0, time.monotonic() - w0)}
        # N1: members of the suite's process group outlived the kill: full reservation (§L)
        book.complete(job, gen, "uncertain" if escaped else state, actual=usage,
                      result_sha=result)
        book.settle(job)
    error, partial, evidence = None, None, None
    try:
        inputs = controls.campaign_inputs(adm, store, cfg.campaign_id)
        res = run_bounded(_suite, cfg.controls, scratch, inputs,
                          limits=_limits(cfg, _verifier_reservation(cfg)))
        # Codex M1 re-review N1: CPU the child never reaped (its killed workers) is unmeasured,
        # absent from usage(), so it is charged at the reservation.
        child = res.usage()
        measured.update(wall_seconds=max(child["wall_seconds"], time.monotonic() - w0))
        if "cpu_seconds" in child:
            measured["cpu_seconds"] = child["cpu_seconds"] + max(0.0, time.process_time() - t0)
        escaped = res.outcome == "escaped"
        if not res.ok:
            error = f"{res.outcome}: {res.detail.get('message', res.detail.get('error_type', ''))}"[:400]
        elif "error" in res.value:
            error, partial = res.value["error"], res.value["partial"]
        else:
            evidence = res.value["evidence"]
            sha = store.put_json(evidence)
    except Exception as exc:                                       # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    if error is not None:
        failure = partial or {"kind": controls.FAILED_KIND, "control_set": cfg.controls,
                              "failed": {"control": None, "error": error},
                              "partial": {"controls": []}}
        try:
            sha = store.put_json(failure)
        except Exception:                                          # noqa: BLE001
            sha = store.put_json({"kind": controls.FAILED_KIND, "control_set": cfg.controls,
                                  "failed": {"control": None, "error": error.split(":")[0]},
                                  "partial": {"controls": []}})
        settle("failed", sha)
        adm.open_incident("controls_failed", {"campaign_id": cfg.campaign_id,
                                              "control_set": cfg.controls,
                                              "error": error.split(":")[0][:64], "evidence": sha})
        return {"control_set": cfg.controls, "ok": False, "passed": False, "evidence": sha,
                "error": error}
    rows = evidence["results"]
    v = controls.verdict(evidence)
    adm.record_controls(cfg.campaign_id, cfg.controls, rows, evidence=sha,
                        on_close=lambda: settle("completed"))
    return {"control_set": cfg.controls, "ok": True, "passed": v["passed"], "evidence": sha,
            **{k: sum(r[k] for r in rows) for k in ("injections", "false_authorizations",
                                                    "paired_positives", "false_rejections")},
            "outcome_mismatches": v["outcome_mismatches"]}


def _confirm(run: Path, cfg: CampaignConfig, prof: str, store: ArtifactStore, job: str,
             entry: dict, kw: dict) -> tuple[dict, dict, dict]:
    """Phase-1a confirmation (§7.4): offline re-execution from the sealed bundle, run under the
    confirmation job reserved at freeze. Its dispatch and settlement happen after the bundle was
    sealed (the sealed ledger shows the reservation as reserved, not spent); it runs in a bounded
    child under the confirmation reservation, the child's measured wall and CPU time are charged in
    the run ledger, and the settlement event with the final totals is published next to the bundle
    as a hash-linked confirmation supplement (§K, §M)."""
    adm = Admission(run / "ledger.db", store, build_registry(store))
    try:
        book = adm.job_book(cfg.campaign_id, profile=prof)
        gen = book.dispatch(job)
        w0 = time.monotonic()
        audit: dict[str, Any] = {"ok": False, "problems": ["confirmation did not complete"]}
        usage = {"wall_seconds": float(cfg.confirmation.wall_seconds),
                 "cpu_seconds": float(cfg.confirmation.cpu_seconds)}
        state = "failed"
        try:
            # B5: the re-execution (and every process it starts) runs in a bounded child whose
            # CPU and wall ceilings are the confirmation reservation; its measured usage
            # (wait4: the child and its reaped descendants) is charged; non-ok is a failure.
            # N1: the re-executed evaluator's own bounded workers stay in the child's process
            # group; unmeasured CPU (killed workers) is absent from usage(), so charged at the
            # reservation; an escaped group settles uncertain at the full reservation.
            # N4: the pre-confirmation replay; its supplement is published (and checked) below
            res = run_bounded(audit_bundle, Path(entry["path"]), workdir=run / "audit",
                              final=False, limits=_limits(cfg, cfg.confirmation), **kw)
            usage = res.usage()
            if res.outcome == "escaped":
                state = "uncertain"
            if res.ok and isinstance(res.value, dict):
                audit = res.value
            else:
                audit = {"ok": False, "problems": [
                    f"confirmation {res.outcome}: {res.detail.get('message', '')}"[:400]]}
        finally:
            usage = {**usage, "wall_seconds": max(usage["wall_seconds"], time.monotonic() - w0),
                     "usd": 0.0, "requests": 0.0, "input_tokens": 0.0, "output_tokens": 0.0}
            book.complete(job, gen, "completed" if audit.get("ok") and state != "uncertain"
                          else state, actual=usage)
            charged = book.settle(job)
        doc = confirmation_supplement(adm, cfg.campaign_id, entry, job, book.totals(), audit)
    finally:
        adm.close()
    path, sha = publish_confirmation(entry, doc)
    problems = check_confirmation(Path(entry["path"]), path)
    return audit, charged, {"path": str(path), "sha256": sha, "problems": problems}
