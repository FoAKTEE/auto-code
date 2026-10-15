"""Campaign runner with a replay provider (test double; never used for scientific runs)."""
import json

import numpy as np
import pytest

from zkqec.contracts import Profile, Reservation, StructuralTargets, TaskSA
from zkqec.kernel.codes import bicycle_chain
from zkqec.orchestrator.campaign import CallResult, CampaignConfig, run_campaign


def sparse(a):
    a = np.asarray(a)
    return {"shape": list(a.shape), "entries": [[int(i), int(j)] for i, j in zip(*np.nonzero(a))]}


class ReplayProviderDouble:
    name, model = "replay", "replay-double"

    def __init__(self, outputs, usd=0.01):
        self.outputs, self.requests, self.usd = list(outputs), [], usd

    def call(self, *, job_id, system_prompt, request, schema, reservation):
        self.requests.append(json.loads(request))
        out = self.outputs.pop(0)
        if out is None:
            return CallResult("timeout", None, {"usd": reservation.usd}, {"provider": "replay"})
        return CallResult("completed", out, {"usd": self.usd, "requests": 1}, {"provider": "replay"})


def cfg(**kw):
    base = dict(campaign_id="t1", profile=Profile(knowledge="K0", prompt="P-min", grammar=("G-open",)),
                tasks=(TaskSA(task_id="sa-secret-id", targets=StructuralTargets(n_max=50, k_min=6, d_min=6,
                                                                           w_max=8, deg_max=8)),),
                provider="replay", model="replay-double", max_proposals_per_task=4,
                caps={"usd": 1.0, "requests": 20},
                per_call=Reservation(usd=0.2, requests=2, input_tokens=0, output_tokens=0,
                                     wall_seconds=0, cpu_seconds=0))
    base.update(kw)
    return CampaignConfig(**base)


GOOD = json.dumps({"family": "explicit_sparse",
                   "params": {"hx": sparse(bicycle_chain(3, 7).hx), "hz": sparse(bicycle_chain(3, 7).hz)},
                   "rationale": "x"})


def test_campaign_stops_when_target_met_and_charges_calls(tmp_path):
    prov = ReplayProviderDouble(["not json", None, GOOD, GOOD])
    out = run_campaign(cfg(), prov, tmp_path, require_clean=False, seal_bundle=False)
    assert out["satisfied"] == ["sa-secret-id"] and out["stop"]["sa-secret-id"] == "target_met"
    assert len(prov.requests) == 3                          # stopped after the satisfying proposal
    # timeout charged its full reservation; completed calls their actual usage
    assert out["totals"]["usd"]["spent"] == pytest.approx(0.01 + 0.2 + 0.01)
    # the digest reports earlier typed outcomes, never the hidden task id
    assert all("sa-secret-id" not in json.dumps(r) for r in prov.requests)
    assert prov.requests[2]["digest"]["earlier_attempts"][0]["outcome"] == "malformed_proposal"


def test_campaign_respects_budget(tmp_path):
    prov = ReplayProviderDouble(["{}"] * 10, usd=0.2)      # every call costs its full reservation
    out = run_campaign(cfg(caps={"usd": 0.45}), prov, tmp_path, require_clean=False, seal_bundle=False)
    assert out["stop"]["sa-secret-id"].startswith("budget")
    assert len(prov.requests) == 2                          # 0.4 spent + 0.2 reservation > 0.45
    assert out["totals"]["usd"]["spent"] == pytest.approx(0.4)


def test_campaign_refuses_dirty_evaluator(tmp_path, monkeypatch):
    import zkqec.orchestrator.campaign as c
    monkeypatch.setattr(c, "git_state", lambda: {"commit": "x", "dirty": True, "dirty_paths": ["src/x"]})
    with pytest.raises(c.EvaluatorDirty):
        run_campaign(cfg(), ReplayProviderDouble([GOOD]), tmp_path)


def test_large_pmin_requests_are_screened_for_family_terms_not_size(tmp_path):
    from zkqec.firewall.lint import family_terms
    assert family_terms("x" * 20000) == []
    assert family_terms("use a bicycle construction") == ["bicycle"]
