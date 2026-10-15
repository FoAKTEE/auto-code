"""zkqec command line: qualify-host | campaign run | audit (prompt §5)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zkqec")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("qualify-host", help="run the M0 host qualification suite")
    q.add_argument("rest", nargs=argparse.REMAINDER)
    c = sub.add_parser("campaign", help="campaign commands")
    csub = c.add_subparsers(dest="action", required=True)
    r = csub.add_parser("run", help="run a live K0 campaign from a config JSON")
    r.add_argument("config", type=Path)
    r.add_argument("--runs", type=Path, default=Path("runs/campaigns"))
    r.add_argument("--deadline-seconds", type=int, default=3600)
    a = sub.add_parser("audit", help="offline audit of a sealed bundle")
    a.add_argument("bundle", type=Path)
    a.add_argument("--workdir", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "qualify-host":
        from .qualify import main as qmain
        return qmain(args.rest)
    if args.cmd == "campaign":
        from .orchestrator.campaign import CampaignConfig
        from .orchestrator.live import run_live
        cfg = CampaignConfig.model_validate_json(args.config.read_text())
        out = run_live(cfg, args.runs, deadline_seconds=args.deadline_seconds)
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
        return 0 if out.get("audit", {}).get("ok", True) else 1
    if args.cmd == "audit":
        from .report.bundle import audit_bundle
        res = audit_bundle(args.bundle, workdir=args.workdir)
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0 if res["ok"] else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
