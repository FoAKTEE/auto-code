"""zkqec command line: qualify-host | campaign run | audit (prompt §5).

``campaign run`` never runs the working tree (contracts §O; Codex M1 review #14): launched from the
repository it extracts the committed evaluator closure into a read-only snapshot and re-executes
itself there in an isolated interpreter (:mod:`zkqec.exec.snapshot`); only the process that entered
the snapshot (:func:`zkqec.orchestrator.freeze.enter_snapshot`) loads the configuration and runs the
campaign. ``--dry-run`` stops after entering the snapshot and prints its facts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="zkqec")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("qualify-host", help="run the M0 host qualification suite")
    q.add_argument("rest", nargs=argparse.REMAINDER)
    c = sub.add_parser("campaign", help="campaign commands")
    csub = c.add_subparsers(dest="action", required=True)
    r = csub.add_parser("run", help="run a live K0 campaign from a config JSON, from the "
                                    "read-only snapshot of the committed evaluator")
    r.add_argument("config", type=Path)
    r.add_argument("--runs", type=Path, default=Path("runs/campaigns"))
    r.add_argument("--deadline-seconds", type=int, default=3600)
    r.add_argument("--archive", type=Path, default=None,
                   help="archive holding the evaluator closure and snapshots (default: "
                        "$ZKQEC_ARCHIVE or the configured archive)")
    r.add_argument("--dry-run", action="store_true",
                   help="enter the snapshot, validate the config, print the facts; launch nothing")
    a = sub.add_parser("audit", help="offline audit of a sealed bundle")
    a.add_argument("bundle", type=Path)
    a.add_argument("--workdir", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "qualify-host":
        from .qualify import main as qmain
        return qmain(args.rest)
    if args.cmd == "campaign":
        from .orchestrator import freeze
        snap = freeze.active_snapshot()
        if snap is None:                         # repository launch: re-execute from the snapshot
            from .exec.snapshot import launch
            return launch(argv, archive=args.archive)
        freeze.check_origin()
        from .contracts import content_hash
        from .orchestrator.campaign import CampaignConfig
        cfg = CampaignConfig.model_validate_json(args.config.read_text())
        if args.dry_run:
            modules = {name: mod.__file__ for name, mod in sorted(sys.modules.items())
                       if (name == "zkqec" or name.startswith("zkqec."))
                       and getattr(mod, "__file__", None)}
            print(json.dumps({"snapshot_root": snap["root"], "snapshot": snap, "modules": modules,
                              "sys_path": list(sys.path), "runtime": freeze.runtime_facts(),
                              "config": {"campaign_id": cfg.campaign_id,
                                         "sha256": content_hash(cfg.model_dump(mode="json"))}},
                             sort_keys=True))
            return 0
        from .orchestrator.live import run_live
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
