"""Command-line entry point; JSON outputs are the authoritative experiment records."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .agents import Coordinator, OfflineProposer, ReplayProposer
from .codes import bicycle_chain, steane
from .distance import certify
from .structure import structural_report
from .schemas import CampaignSpec
from .decoding import code_capacity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qecforge")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="Run a seeded multi-role baseline or replay proposals.")
    p.add_argument("config", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--replay", type=Path)
    v = sub.add_parser("verify", help="Verify one reference bicycle-chain instance.")
    v.add_argument("--ell", type=int, default=3)
    v.add_argument("--m", type=int, default=7)
    v.add_argument("--half-weight", type=int, default=3)
    c = sub.add_parser("capacity", help="Run a fixed-shot Steane code-capacity smoke test.")
    c.add_argument("--shots", type=int, default=2000)
    c.add_argument("--p", type=float, default=0.02)
    c.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            spec = CampaignSpec.model_validate_json(args.config.read_text())
            if spec.mode == "model_proposals":
                parser.error("Model mode requires an explicitly supplied JSONModel via the Python API.")
            if args.replay:
                spec = spec.model_copy(update={"mode": "replay"})
            elif spec.mode == "replay":
                parser.error("Replay mode requires --replay path.")
            result = Coordinator(spec, ReplayProposer(args.replay) if args.replay else OfflineProposer(), args.out).run()
            print(json.dumps({"report": str(args.out/"report.json"),
                              "candidates": len(result["candidates"]),
                              "scientific_claim_ready": result["scientific_claim_ready"]}, indent=2))
        elif args.command == "verify":
            code = bicycle_chain(args.ell, args.m)
            print(json.dumps({"structure": structural_report(code),
                              "distance": certify(code, args.half_weight)}, indent=2))
        else:
            print(json.dumps(code_capacity(steane(), args.p, args.shots, args.seed), indent=2))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
