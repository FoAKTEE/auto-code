"""Live campaign driver: campaign slice + broker + deadline supervisor + sandboxed Claude provider.

Codex is not K0-qualified on this host (qualification 20261015T105932Z: tools declared through
``additional_tools``), so live K0 campaigns use the Claude provider only (D16).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ..agents.providers import ClaudeCallProvider
from ..broker.launcher import Broker
from ..sandbox.cgroup import CampaignSlice, DeadlineSupervisor
from .campaign import CampaignConfig, run_campaign


LIVE_SEAL_MARGIN_SECONDS = 900


def run_live(cfg: CampaignConfig, run_root: Path, *, deadline_seconds: int = 3600,
             memory_max: int = 64 * 1024**3, tasks_max: int = 4096) -> dict[str, Any]:
    if cfg.provider != "claude" or cfg.profile.knowledge != "K0":
        raise ValueError("live K0 campaigns are qualified for the claude provider only (D16)")
    ops = Path(run_root) / f"{cfg.campaign_id}.ops"
    ops.mkdir(parents=True, exist_ok=False)
    ident = re.sub(r"[^A-Za-z0-9_]", "_", cfg.campaign_id)[:48]   # systemd/broker identifier rule
    slice_ = CampaignSlice(ident, memory_max=memory_max, tasks_max=tasks_max)
    slice_.create()
    broker = Broker(ident, ops, deadline_unix=time.time() + deadline_seconds)
    broker.start()
    sup = DeadlineSupervisor(slice_, broker.switch_path, time.time() + deadline_seconds)
    sup.arm()
    try:
        provider = ClaudeCallProvider(cfg.model, broker=broker, slice_=slice_,
                                      jobs_root=Path(f"/tmp/zkq-{ident}"), effort=cfg.effort)
        # Stop dispatching early enough to seal and confirm before the supervisor's hard deadline.
        out = run_campaign(cfg, provider, Path(run_root),
                           deadline_seconds=max(0, deadline_seconds - LIVE_SEAL_MARGIN_SECONDS))
        out["broker_manifest"] = broker.manifest()
        (Path(out["run_dir"]) / "broker-manifest.json").write_text(
            json.dumps(out["broker_manifest"], indent=2, sort_keys=True, default=str))
        return out
    finally:
        sup.disarm()
        broker.stop()
        broker.remove_ca_key()
        slice_.cleanup()
