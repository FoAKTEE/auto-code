"""Authoritative campaign report rendered ONLY from ledger state (appendix §A5).

The authoritative report is a deterministic function of (ledger, domain, campaign record). The
linter re-renders it and requires byte equality, so nothing in the authoritative part can be edited
by hand or by a model. LLM-written text may only follow ``COMMENTARY_MARKER`` and is never evidence.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from ..admission import Admission
from ..contracts import DesignCandidate, Frozen, TaskSA, TaskSF, content_hash

COMMENTARY_MARKER = "## Unverified commentary — not evidence"


class EvaluationRef(Frozen):
    task_id: str
    candidate: str
    proposal: str | None = None
    source: str                       # e.g. "fixture", "designer-a", "b1-random"
    call_record: str | None = None


class CampaignRecord(Frozen):
    campaign_id: str
    manifest: dict[str, Any]
    tasks: tuple[TaskSA | TaskSF, ...]
    evaluations: tuple[EvaluationRef, ...] = ()
    autonomy_log: tuple[dict[str, Any], ...] = ()
    budget_totals: dict[str, dict[str, float]] = Field(default_factory=dict)
    labels: tuple[str, ...] = ()      # e.g. "single-implementation", "non-evaluation"


def report_data(adm: Admission, domain: str, camp: CampaignRecord) -> dict[str, Any]:
    res = adm.resolver(domain)
    tasks = {t.task_id: t for t in camp.tasks}
    rows = []
    for ev in camp.evaluations:
        task = tasks[ev.task_id]
        cand = DesignCandidate.model_validate(adm.store.get_json(ev.candidate))
        sat = res.satisfies_task(cand, task)
        reqs = []
        for name in res.requirement_names(task):
            ref = res.target_refuted(cand, task, name)
            r = next((q for q in sat.requirements if q.name == name), None)
            reqs.append({"name": name,
                         "status": r.status if r is not None else "unknown",
                         "refuted": ref.status,
                         "explanation": r.explanation if r is not None else "",
                         "premises": sorted(r.premises) if r is not None else []})
        rows.append({"task_id": ev.task_id, "source": ev.source, "candidate": ev.candidate,
                     "proposal": ev.proposal, "call_record": ev.call_record,
                     "satisfies_task": sat.status, "rule": sat.rule,
                     "premises": sorted(sat.premises), "requirements": reqs})
    current = res.current()
    receipts = sorted(({"receipt_id": rid, "predicate": r.proposition.predicate,
                        "subject_type": r.proposition.subject_type, "subject": r.proposition.subject,
                        "args": r.proposition.args, "status": r.status, "kind": r.evidence_kind,
                        "issuer": f"{r.issuer} {r.issuer_version}", "method": r.method}
                       for rid, r in current.items()), key=lambda x: (x["predicate"], x["receipt_id"]))
    head = adm.ledger.head()
    return {"campaign_id": camp.campaign_id, "manifest": camp.manifest,
            "manifest_sha256": content_hash(camp.manifest), "domain": domain,
            "ledger_head": {"seq": head[0], "hash": head[1]}, "labels": list(camp.labels),
            "tasks": [t.model_dump(mode="json") for t in camp.tasks], "evaluations": rows,
            "receipts": receipts, "budget_totals": camp.budget_totals,
            "autonomy_log": list(camp.autonomy_log),
            "integrity": _integrity(adm)}


def _integrity(adm: Admission) -> dict[str, Any]:
    v = adm.verify()
    return {"ok": v.ok, "events": v.events, "problems": list(v.problems)}


def _fmt_args(args: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(args.items()))


def render_markdown(data: dict[str, Any]) -> str:
    L = [f"# Campaign report — {data['campaign_id']}", "",
         "Authoritative section: generated from the evidence ledger; do not edit (linted by "
         "re-rendering).", "",
         f"- Evaluator domain: `{data['domain']}`",
         f"- Manifest sha256: `{data['manifest_sha256']}`",
         f"- Ledger head: seq {data['ledger_head']['seq']}, hash `{data['ledger_head']['hash']}`",
         f"- Labels: {', '.join(data['labels']) or 'none'}", "", "## Tasks", ""]
    for t in data["tasks"]:
        L.append(f"- `{t['task_id']}` ({t['kind']}): targets "
                 f"{json.dumps(t['targets'], sort_keys=True)}"
                 + (f"; k_req={t['k_req']}; allowed operations "
                    f"{t['hardware']['allowed_operations']}" if t["kind"] == "S-F" else ""))
    L += ["", "## Evaluated candidates", "",
          "| task | source | candidate | satisfies_task | rule |", "|---|---|---|---|---|"]
    for e in data["evaluations"]:
        L.append(f"| `{e['task_id']}` | {e['source']} | `{e['candidate'][:16]}` | "
                 f"**{e['satisfies_task']}** | {e['rule']} |")
    for e in data["evaluations"]:
        L += ["", f"### `{e['task_id']}` / `{e['candidate'][:16]}` ({e['source']})", "",
              "| requirement | status | target_refuted | explanation | premises |",
              "|---|---|---|---|---|"]
        for r in e["requirements"]:
            prem = ", ".join(f"`{p[:12]}`" for p in r["premises"]) or "—"
            L.append(f"| {r['name']} | {r['status']} | {r['refuted']} | {r['explanation']} | {prem} |")
    L += ["", "## Current receipts (authoritative evidence)", "",
          "| predicate | subject | args | status | kind | issuer | receipt |",
          "|---|---|---|---|---|---|---|"]
    for r in data["receipts"]:
        L.append(f"| {r['predicate']} | {r['subject_type']}:`{r['subject'][:12]}` | "
                 f"{_fmt_args(r['args'])} | {r['status']} | {r['kind']} | {r['issuer']} | "
                 f"`{r['receipt_id'][:12]}` |")
    L += ["", "## Budget (units never summed)", "", "| unit | spent | open |", "|---|---|---|"]
    for unit, v in sorted(data["budget_totals"].items()):
        L.append(f"| {unit} | {v.get('spent', 0):g} | {v.get('open', 0):g} |")
    L += ["", "## Autonomy log", ""]
    L += [f"- {json.dumps(a, sort_keys=True)}" for a in data["autonomy_log"]] or ["- none"]
    L += ["", "## Ledger integrity", "", f"`{json.dumps(data['integrity'], sort_keys=True, default=str)}`", ""]
    return "\n".join(L)


def render(adm: Admission, domain: str, camp: CampaignRecord, commentary: str | None = None) -> str:
    md = render_markdown(report_data(adm, domain, camp))
    if commentary:
        md += f"\n{COMMENTARY_MARKER}\n\n{commentary.strip()}\n"
    return md


def lint(report_md: str, adm: Admission, domain: str, camp: CampaignRecord) -> list[str]:
    """Problems with a report: the authoritative part must equal a fresh re-render."""
    authoritative = report_md.split(f"\n{COMMENTARY_MARKER}\n", 1)[0]
    expected = render_markdown(report_data(adm, domain, camp))
    if authoritative != expected:
        return ["authoritative section differs from the ledger re-render"]
    if report_md.count(COMMENTARY_MARKER) > 1:
        return ["more than one commentary marker"]
    return []
