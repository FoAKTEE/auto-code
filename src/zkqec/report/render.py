"""Authoritative campaign report rendered ONLY from the ledger (appendix §A5; prompt §4.10).

The authoritative section is a deterministic function of (ledger, campaign id). Everything in it is
derived from hash-chained events of that campaign: the frozen tasks, labels, configuration hash,
domain and manifest (``campaign_frozen``), the evaluated candidates (``evaluation_recorded``), the
current receipts (resolver), the accounting (``job_settled``; units never summed), the §I control
summary (``controls_recorded``: k/N false authorizations, j/M false rejections) and the autonomy
log (``autonomy_recorded``). No caller-supplied record is rendered, so a caller cannot substitute
tasks, membership, totals or prose. Strings come from closed vocabularies or validated patterns and
are Markdown-escaped anyway. The linter re-renders and requires byte equality. LLM-written text may
only follow ``COMMENTARY_MARKER`` and is never evidence.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from ..admission import Admission
from ..contracts import UNITS, DesignCandidate, TaskSA, TaskSF

COMMENTARY_MARKER = "## Unverified commentary — not evidence"
_MD_SPECIAL = re.compile(r"([\\`*\[\]<>#!|~])")


def esc(value: Any) -> str:
    """Markdown-inert text: structural characters (code spans, emphasis, links, HTML, headings,
    table cells) escaped, line breaks and other control characters flattened to spaces."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return _MD_SPECIAL.sub(r"\\\1", text)


def _task(d: dict) -> TaskSA | TaskSF:
    return TaskSF.model_validate(d) if d.get("kind") == "S-F" else TaskSA.model_validate(d)


def report_data(adm: Admission, campaign_id: str) -> dict[str, Any]:
    camp = adm.ledger.campaign(campaign_id)
    if camp is None:
        raise KeyError(f"campaign {campaign_id} is not frozen in this ledger")
    domain = camp["domain"]
    res = adm.resolver(domain)
    tasks = {t["task_id"]: _task(t) for t in camp["tasks"]}
    rows = []
    for ev in adm.ledger.evaluations(campaign_id):
        task = tasks[ev["task_id"]]
        cand = DesignCandidate.model_validate(adm.store.get_json(ev["candidate"]))
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
        rows.append({"evaluation_id": ev["evaluation_id"], "task_id": ev["task_id"],
                     "source": ev["source"], "candidate": ev["candidate"],
                     "proposal": ev["proposal"], "call_record": ev["call_record"],
                     "satisfies_task": sat.status, "rule": sat.rule,
                     "premises": sorted(sat.premises), "requirements": reqs})
    current = res.current()
    receipts = sorted(({"receipt_id": rid, "predicate": r.proposition.predicate,
                        "subject_type": r.proposition.subject_type, "subject": r.proposition.subject,
                        "args": r.proposition.args, "status": r.status, "kind": r.evidence_kind,
                        "issuer": f"{r.issuer} {r.issuer_version}", "method": r.method}
                       for rid, r in current.items()), key=lambda x: (x["predicate"], x["receipt_id"]))
    settlements = adm.ledger.settlements(campaign_id)
    spent = {u: sum(s["charged"][u] for s in settlements) for u in UNITS}
    conf = camp["confirmation"] or {}
    budget = {u: {"cap": camp["caps"].get(u), "spent": spent[u],
                  "confirmation_reserved": conf.get(u, 0.0)} for u in UNITS}
    jobs = Counter((s["kind"], s["from_state"]) for s in settlements)
    head = adm.ledger.head()
    return {"campaign_id": campaign_id, "namespace": camp["namespace"],
            "manifest_sha256": camp["manifest"], "config_sha256": camp["config_sha256"],
            "domain": domain, "ledger_head": {"seq": head[0], "hash": head[1]},
            "labels": list(camp["labels"]), "tasks": camp["tasks"], "evaluations": rows,
            "receipts": receipts, "budget": budget,
            "jobs": [{"kind": k, "settled_from": st, "count": n}
                     for (k, st), n in sorted(jobs.items())],
            "overruns": sorted(s["job_id"] for s in settlements if s["overrun"]),
            "autonomy_log": [{k: a[k] for k in ("actor", "action", "detail")}
                             for a in adm.ledger.autonomy(campaign_id)],
            "controls": [{k: c[k] for k in ("control_set", "results", "evidence")}
                         for c in adm.ledger.controls(campaign_id)],
            "integrity": _integrity(adm)}


def _integrity(adm: Admission) -> dict[str, Any]:
    v = adm.verify()
    return {"ok": v.ok, "events": v.events, "problems": list(v.problems)}


def _fmt_args(args: dict) -> str:
    return ", ".join(f"{esc(k)}={esc(v)}" for k, v in sorted(args.items()))


def _num(x) -> str:
    return "—" if x is None else f"{x:g}"


def render_markdown(data: dict[str, Any]) -> str:
    L = [f"# Campaign report — {esc(data['campaign_id'])}", "",
         "Authoritative section: generated from the evidence ledger; do not edit (linted by "
         "re-rendering).", "",
         f"- Namespace: `{esc(data['namespace'])}`",
         f"- Evaluator domain: `{data['domain']}`",
         f"- Frozen manifest artifact: `{data['manifest_sha256']}`",
         f"- Frozen config sha256: `{data['config_sha256']}`",
         f"- Ledger head: seq {data['ledger_head']['seq']}, hash `{data['ledger_head']['hash']}`",
         f"- Labels: {', '.join(esc(x) for x in data['labels']) or 'none'}", "",
         "## Tasks (frozen in the ledger)", ""]
    for t in data["tasks"]:
        L.append(f"- `{esc(t['task_id'])}` ({esc(t['kind'])}): targets "
                 f"{esc(json.dumps(t['targets'], sort_keys=True))}"
                 + (f"; k_req={t['k_req']}; allowed operations "
                    f"{esc(list(t['hardware']['allowed_operations']))}" if t["kind"] == "S-F" else ""))
    L += ["", "## Evaluated candidates", "",
          "| task | source | candidate | satisfies_task | rule |", "|---|---|---|---|---|"]
    for e in data["evaluations"]:
        L.append(f"| `{esc(e['task_id'])}` | {esc(e['source'])} | `{e['candidate'][:16]}` | "
                 f"**{e['satisfies_task']}** | {esc(e['rule'])} |")
    for e in data["evaluations"]:
        L += ["", f"### `{esc(e['task_id'])}` / `{e['candidate'][:16]}` ({esc(e['source'])})", "",
              "| requirement | status | target_refuted | explanation | premises |",
              "|---|---|---|---|---|"]
        for r in e["requirements"]:
            prem = ", ".join(f"`{p[:12]}`" for p in r["premises"]) or "—"
            L.append(f"| {esc(r['name'])} | {r['status']} | {r['refuted']} | "
                     f"{esc(r['explanation'])} | {prem} |")
    L += ["", "## Current receipts (authoritative evidence)", "",
          "| predicate | subject | args | status | kind | issuer | receipt |",
          "|---|---|---|---|---|---|---|"]
    for r in data["receipts"]:
        L.append(f"| {esc(r['predicate'])} | {esc(r['subject_type'])}:`{r['subject'][:12]}` | "
                 f"{_fmt_args(r['args'])} | {r['status']} | {esc(r['kind'])} | {esc(r['issuer'])} | "
                 f"`{r['receipt_id'][:12]}` |")
    L += ["", "## Accounting (settled-job events; units never summed)", "",
          "| unit | cap | spent | reserved for confirmation |", "|---|---|---|---|"]
    for unit in UNITS:
        v = data["budget"][unit]
        L.append(f"| {unit} | {_num(v['cap'])} | {_num(v['spent'])} | "
                 f"{_num(v['confirmation_reserved'])} |")
    L += ["", "| job kind | settled from | jobs |", "|---|---|---|"]
    L += [f"| {esc(j['kind'])} | {esc(j['settled_from'])} | {j['count']} |" for j in data["jobs"]] \
        or ["| — | — | 0 |"]
    L += ["", f"- Overrun jobs (charged beyond their reservation): "
              f"{', '.join(f'`{j}`' for j in data['overruns']) or 'none'}"]
    L += ["", "## Controls (§I: k/N false authorizations, j/M false rejections)", ""]
    if not data["controls"]:
        L.append("- none recorded (controls not supplied for this campaign)")
    for c in data["controls"]:
        rows = c["results"]
        L += [f"Control set `{esc(c['control_set'])}`, evidence `{c['evidence']}`:", "",
              "| control | false authorizations k/N | false rejections j/M |", "|---|---|---|"]
        L += [f"| {esc(r['control'])} | {r['false_authorizations']}/{r['injections']} | "
              f"{r['false_rejections']}/{r['paired_positives']} |" for r in rows]
        k, n = sum(r["false_authorizations"] for r in rows), sum(r["injections"] for r in rows)
        j, m = sum(r["false_rejections"] for r in rows), sum(r["paired_positives"] for r in rows)
        L += ["", f"- False authorizations: {k}/{n}; false rejections: {j}/{m}", ""]
    L += ["", "## Autonomy log (human actions)", ""]
    L += [f"- {esc(a['actor'])}: {esc(a['action'])} — {esc(a['detail'])}"
          for a in data["autonomy_log"]] or ["- none"]
    L += ["", "## Ledger integrity", "",
          f"`{esc(json.dumps(data['integrity'], sort_keys=True, default=str))}`", ""]
    return "\n".join(L)


def render(adm: Admission, campaign_id: str, commentary: str | None = None) -> str:
    md = render_markdown(report_data(adm, campaign_id))
    if commentary:
        md += f"\n{COMMENTARY_MARKER}\n\n{commentary.strip()}\n"
    return md


def lint(report_md: str, adm: Admission, campaign_id: str) -> list[str]:
    """Problems with a report: the authoritative part must equal a fresh re-render."""
    authoritative = report_md.split(f"\n{COMMENTARY_MARKER}\n", 1)[0]
    expected = render_markdown(report_data(adm, campaign_id))
    if authoritative != expected:
        return ["authoritative section differs from the ledger re-render"]
    if report_md.count(COMMENTARY_MARKER) > 1:
        return ["more than one commentary marker"]
    return []
