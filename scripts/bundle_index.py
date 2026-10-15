"""Bundle index for D9: every sealed live bundle in the archive, re-audited offline, with its hashes,
labels, outcomes and spend, written to ``results/bundles/index.{json,md}``.

Each bundle is reconstructed in an empty directory and audited by its own embedded evaluator
(``zkqec.report.bundle.audit_bundle``); outcomes, labels and spend are read from the audited
bundle's report (which the audit lints against its ledger). Usage::

    .venv/bin/python scripts/bundle_index.py [--note CAMPAIGN=TEXT ...] [BUNDLE ...]

Without BUNDLE arguments, every ``<archive>/bundles/<campaign>/<sha>.tar.zst`` is indexed.

The audit is the final acceptance audit (Codex M1 re-review N4): a bundle that requires its
confirmation supplement (declared in its manifest, or inferred from sealed broker evidence for
bundles sealed before the declaration) is indexed only with it; a missing required supplement fails
the index (exit 2, nothing written). Only bundles with neither a declaration nor broker evidence
are labelled ``none (pre-B6)``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from zkqec.report.bundle import ARCHIVE, audit_bundle, load_supplement, supplement_path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "results" / "bundles"


def _report_facts(report: str) -> dict:
    labels = re.search(r"^- Labels: (.*)$", report, re.M)
    evaluated = re.findall(r"^\| `([^`]+)` \| ([^|]+) \| `([0-9a-f]+)` \| \**([a-z_]+)\** \|", report, re.M)
    spend: dict[str, str] = {}
    lines = report.splitlines()
    for i, line in enumerate(lines):                # the budget/accounting table: "| unit | ... spent ..."
        cols = [c.strip() for c in line.strip("|").split("|")]
        if cols[:1] == ["unit"] and "spent" in cols:
            j = cols.index("spent")
            for row in lines[i + 2:]:
                cells = [c.strip() for c in row.strip("|").split("|")]
                if not row.startswith("|") or len(cells) != len(cols):
                    break
                spend[cells[0]] = cells[j]
            break
    return {"labels": [x.strip() for x in labels.group(1).split(",")] if labels else [],
            "evaluated": [{"task": t, "source": s.strip(), "candidate": c, "satisfies_task": v}
                          for t, s, c, v in evaluated],
            "spend": {k: float(v) for k, v in sorted(spend.items())}}


LABELS = {"linked": "linked", "missing": "MISSING (required)", "pre-B6": "none (pre-B6)",
          "not required": "not required"}


def index_bundle(path: Path, note: str | None) -> dict:
    sup = supplement_path(path)
    with tempfile.TemporaryDirectory(prefix="zkqec-index-") as td:
        # final acceptance: discovers the supplement and fails without it when required (N4)
        audit = audit_bundle(path, workdir=Path(td) / "audit")
        b = Path(td) / "audit" / "bundle"
        manifest = json.loads((b / "manifest.json").read_text())
        facts = _report_facts((b / "report.md").read_text())
    conf = audit.get("confirmation") or {}
    confirmation: dict = {"status": conf.get("status"), "required": conf.get("required"),
                          "basis": conf.get("basis"), "supplement": None}
    if conf.get("status") == "linked":      # hash-linked confirmation settlement + totals (§M)
        try:
            totals = load_supplement(sup).get("final_totals", {})
        except ValueError:
            totals = {}                     # unreadable: reported by the audit
        confirmation.update(supplement=sup.name, sha256=hashlib.sha256(sup.read_bytes()).hexdigest(),
                            final_totals={u: v.get("spent") if isinstance(v, dict) else v
                                          for u, v in sorted(totals.items())}
                            if isinstance(totals, dict) else {})
    return {"campaign_id": manifest["campaign_id"], "bundle_sha256": path.name.split(".")[0],
            "bytes": path.stat().st_size, "path": str(path), "format": manifest.get("format", "zkqec-bundle-v1"),
            "evaluator_commit": manifest["closures"].get("git_commit"), "closures": manifest["closures"],
            "ledger_head": manifest["ledger_head"], **facts,
            "audit": {"ok": bool(audit.get("ok")), "problems": audit.get("problems", [])},
            "confirmation": confirmation, "note": note}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundles", nargs="*", type=Path)
    ap.add_argument("--note", action="append", default=[], help="CAMPAIGN=TEXT")
    args = ap.parse_args(argv)
    notes = dict(n.split("=", 1) for n in args.note)
    paths = args.bundles or sorted((ARCHIVE / "bundles").glob("*/*.tar.zst"))
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    entries = [index_bundle(p, notes.get(p.parent.name)) for p in paths]
    missing = [e["path"] for e in entries if e["confirmation"]["status"] == "missing"]
    if missing:                         # N4: never index a bundle without its final accounting
        print(json.dumps({"error": "required confirmation supplement missing", "bundles": missing},
                         indent=1), file=sys.stderr)
        return 2
    doc = {"schema": "zkqec.bundle-index/1", "archive": str(ARCHIVE), "indexed_with_commit": head,
           "indexed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "bundles": sorted(entries, key=lambda e: (e["campaign_id"], e["bundle_sha256"]))}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    L = ["# Live bundle index (D9)", "",
         f"Archive `{ARCHIVE}`; re-audited offline with each bundle's embedded evaluator; indexed at "
         f"`{head[:12]}` ({doc['indexed_utc']}). Machine-readable: `index.json`.", "",
         "| campaign | bundle sha256 | MB | evaluator commit | ledger head | audit | confirmation | satisfied | USD | requests | note |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in doc["bundles"]:
        sat = sum(x["satisfies_task"] == "verified" for x in e["evaluated"])
        L.append(f"| {e['campaign_id']} | `{e['bundle_sha256']}` | {e['bytes'] / 1e6:.2f} | "
                 f"`{(e['evaluator_commit'] or '')[:12]}` | {e['ledger_head']['seq']} | "
                 f"{'ok' if e['audit']['ok'] else 'FAIL'} | "
                 f"{LABELS.get(e['confirmation']['status'], 'unknown')} | {sat}/{len(e['evaluated'])} | "
                 f"{e['spend'].get('usd', 0):.4f} | {e['spend'].get('requests', 0):.0f} | {e['note'] or ''} |")
    if all({"single-implementation", "non-evaluation"} <= set(e["labels"]) for e in doc["bundles"]):
        L += ["", "Every bundle listed carries the labels `single-implementation, non-evaluation` (D11); "
              "full label sets are in `index.json`."]
    (OUT / "index.md").write_text("\n".join(L) + "\n")
    print(json.dumps([{k: e[k] for k in ("campaign_id", "bundle_sha256", "audit")} for e in entries], indent=1))
    return 0 if all(e["audit"]["ok"] for e in entries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
