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


class LaunchFailingDouble(ReplayProviderDouble):
    def call(self, *, job_id, system_prompt, request, schema, reservation):
        self.requests.append(json.loads(request))
        return CallResult("launch_failure", None, {}, {"provider": "replay"})


def test_repeated_launch_failures_stop_the_campaign(tmp_path):
    import zkqec.orchestrator.campaign as c
    prov = LaunchFailingDouble([])
    with pytest.raises(c.LaunchFailures):
        run_campaign(cfg(), prov, tmp_path, require_clean=False, seal_bundle=False)
    assert len(prov.requests) == 2


def test_designer_schema_has_no_draft_uri():
    from pathlib import Path
    schema = json.loads((Path(__file__).resolve().parents[2] / "roles/designer/output.schema.json").read_text())
    assert "$schema" not in schema       # the Claude CLI validator rejects the 2020-12 meta-schema URI


class TimeoutPartialUsageDouble(ReplayProviderDouble):
    """A killed call that reports only partial telemetry (Codex M1 review #5)."""

    def call(self, *, job_id, system_prompt, request, schema, reservation):
        self.requests.append(json.loads(request))
        return CallResult("timeout", None, {"requests": 1.0}, {"provider": "replay"})


def test_interrupted_call_with_partial_usage_keeps_its_full_reservation(tmp_path):
    out = run_campaign(cfg(max_proposals_per_task=1), TimeoutPartialUsageDouble([]), tmp_path,
                       require_clean=False, seal_bundle=False)
    assert out["totals"]["usd"]["spent"] == pytest.approx(0.2)
    assert out["totals"]["requests"]["spent"] == pytest.approx(2)


class SecretRecordDouble(ReplayProviderDouble):
    """A provider whose call record carries a secret under a sensitive key (Codex M1 review #8)."""

    def call(self, *, job_id, system_prompt, request, schema, reservation):
        self.requests.append(json.loads(request))
        return CallResult("completed", GOOD, {"usd": 0.01, "requests": 1},
                          {"provider": "replay", "api_key": "opaque-secret-123"})


def _disk(root):
    import os
    out = b""
    for d, _, files in os.walk(root):
        for f in files:
            with open(os.path.join(d, f), "rb") as fh:
                out += fh.read()
    return out


def test_call_record_sidecars_hold_only_sanitized_bytes(tmp_path):
    out = run_campaign(cfg(max_proposals_per_task=1), SecretRecordDouble([]), tmp_path,
                       require_clean=False, seal_bundle=False)
    disk = _disk(out["run_dir"])
    assert b"opaque-secret-123" not in disk and b"[REDACTED:secret_field]" in disk


# --- Codex M1 review #13: confirmation reserved up front; campaign deadline ------------------------
def _ops(run_dir):
    import sqlite3
    conn = sqlite3.connect(f"{run_dir}/ledger.db")
    rows = conn.execute("SELECT kind, state, reservation FROM ops_jobs ORDER BY created_at").fetchall()
    conn.close()
    return rows


def test_confirmation_capacity_is_reserved_before_any_dispatch(tmp_path):
    prov = ReplayProviderDouble([GOOD])
    out = run_campaign(cfg(caps={"usd": 1.0, "requests": 20, "cpu_seconds": 5000,
                                 "wall_seconds": 5000}), prov, tmp_path,
                       require_clean=False, seal_bundle=False)
    rows = _ops(out["run_dir"])
    assert rows[0][0] == "confirm" and rows[0][1] == "reserved"
    assert json.loads(rows[0][2])["cpu_seconds"] > 0
    assert out["totals"]["cpu_seconds"]["open"] == json.loads(rows[0][2])["cpu_seconds"]
    report = (tmp_path / "t1" / "report.md").read_text()
    assert "| cpu_seconds | 5000 |" in report and "reserved for confirmation" in report


def test_no_dispatch_without_confirmation_capacity(tmp_path):
    from zkqec.orchestrator.jobs import BudgetExceeded
    prov = ReplayProviderDouble([GOOD])
    with pytest.raises(BudgetExceeded):
        run_campaign(cfg(caps={"usd": 1.0, "cpu_seconds": 10}), prov, tmp_path,
                     require_clean=False, seal_bundle=False)
    assert prov.requests == []


def test_campaign_deadline_stops_dispatch(tmp_path):
    prov = ReplayProviderDouble([GOOD] * 4)
    out = run_campaign(cfg(), prov, tmp_path, require_clean=False, seal_bundle=False,
                       deadline_seconds=0)
    assert prov.requests == [] and out["stop"]["sa-secret-id"] == "deadline"


def test_frozen_live_config_still_validates_and_its_labels_are_admissible(tmp_path):
    """The committed live config must load under the closed label vocabulary (§A5 typed labels)."""
    from pathlib import Path
    from zkqec.admission import Admission
    from zkqec.pipeline import build_registry
    from zkqec.store import ArtifactStore
    path = Path(__file__).resolve().parents[2] / "configs" / "campaigns" / "m1-live.json"
    live = CampaignConfig.model_validate_json(path.read_text())
    store = ArtifactStore(tmp_path / "s")
    with Admission(tmp_path / "l.db", store, build_registry(store)) as adm:
        adm.freeze_campaign(live.campaign_id, namespace=live.campaign_id, config={},
                            tasks=[t.model_dump(mode="json") for t in live.tasks], caps=live.caps,
                            labels=list(live.labels), domain="0" * 64,
                            manifest=store.put_json({}))


# --- Codex M1 review #15: every attempt retains its exact inputs and raw outputs --------------------
class RecordingDouble(ReplayProviderDouble):
    def __init__(self, outputs):
        super().__init__(outputs)
        self.sent = []

    def call(self, *, job_id, system_prompt, request, schema, reservation):
        self.sent.append((system_prompt, request, schema))
        out = super().call(job_id=job_id, system_prompt=system_prompt, request=request,
                           schema=schema, reservation=reservation)
        out.raw = {"stdout": f"raw stdout for {job_id}".encode(), "stderr": b"warn: x\n"}
        return out


def test_every_call_retains_exact_inputs_and_raw_outputs(tmp_path):
    from zkqec.store import ArtifactStore
    prov = RecordingDouble(["not json {", GOOD])
    run_campaign(cfg(), prov, tmp_path, require_clean=False, seal_bundle=False)
    store = ArtifactStore(tmp_path / "t1" / "store")
    import sqlite3
    conn = sqlite3.connect(tmp_path / "t1" / "ledger.db")
    results = [r for r, in conn.execute("SELECT result_sha FROM ops_jobs WHERE kind='propose' "
                                        "ORDER BY created_at")]
    events = [json.loads(p) for p, in conn.execute(
        "SELECT payload FROM events WHERE kind='evaluation_recorded'")]
    assert len(results) == len(prov.sent) == 2
    for (system_prompt, request, schema), rec_sha, output in zip(prov.sent, results,
                                                                 ["not json {", GOOD]):
        arts = store.get_json(rec_sha)["artifacts"]
        assert store.get_bytes(arts["request"]) == request.encode()
        assert store.get_bytes(arts["system_prompt"]) == system_prompt.encode()
        assert json.loads(store.get_bytes(arts["schema"])) == schema
        assert store.get_bytes(arts["output_text"]) == output.encode()   # malformed kept too
        assert store.get_bytes(arts["raw_stdout"]).startswith(b"raw stdout for ")
    assert events and events[0]["artifacts"] == store.get_json(results[1])["artifacts"]


# --- Codex M1 milestone review B6: call inputs are durable and referenced before dispatch ----------
class CrashingDouble(ReplayProviderDouble):
    """The orchestrator dies while the call is in flight."""

    def call(self, *, job_id, system_prompt, request, schema, reservation):
        self.sent = (system_prompt, request, schema)
        raise KeyboardInterrupt("orchestrator killed mid-call")


def test_call_inputs_survive_a_crash_during_the_call(tmp_path):
    import sqlite3
    from zkqec.store import ArtifactStore
    prov = CrashingDouble([])
    with pytest.raises(KeyboardInterrupt):
        run_campaign(cfg(), prov, tmp_path, require_clean=False, seal_bundle=False)
    conn = sqlite3.connect(tmp_path / "t1" / "ledger.db")
    ((state, intent),) = conn.execute("SELECT state, input_sha FROM ops_jobs WHERE kind='propose'")
    conn.close()
    assert state == "dispatched"                     # recovery turns it uncertain (full charge)
    store = ArtifactStore(tmp_path / "t1" / "store")
    doc = store.get_json(intent)
    system_prompt, request, schema = prov.sent
    assert doc["kind"] == "zkqec.call_inputs/1" and doc["task_id"] == "sa-secret-id"
    assert doc["reservation"] == cfg().per_call.model_dump(mode="json")
    arts = doc["artifacts"]
    assert store.get_bytes(arts["request"]) == request.encode()
    assert store.get_bytes(arts["system_prompt"]) == system_prompt.encode()
    assert json.loads(store.get_bytes(arts["schema"])) == schema


def test_call_records_name_their_input_intent(tmp_path):
    import sqlite3
    from zkqec.store import ArtifactStore
    run_campaign(cfg(), ReplayProviderDouble([GOOD]), tmp_path, require_clean=False, seal_bundle=False)
    conn = sqlite3.connect(tmp_path / "t1" / "ledger.db")
    ((intent, rec_sha),) = conn.execute("SELECT input_sha, result_sha FROM ops_jobs WHERE kind='propose'")
    conn.close()
    store = ArtifactStore(tmp_path / "t1" / "store")
    rec = store.get_json(rec_sha)
    assert rec["inputs"] == intent
    assert {k: rec["artifacts"][k] for k in ("request", "system_prompt", "schema")} == \
        store.get_json(intent)["artifacts"]


def test_run_live_hands_its_broker_to_the_campaign(tmp_path, monkeypatch):
    """The broker manifest and evidence are sealed by run_campaign, not written after it."""
    import zkqec.orchestrator.live as live
    seen = {}

    class Stub:
        def __init__(self, *a, **k):
            self.switch_path = tmp_path / "OFF"

        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(live, "CampaignSlice", Stub)
    monkeypatch.setattr(live, "Broker", Stub)
    monkeypatch.setattr(live, "DeadlineSupervisor", Stub)
    monkeypatch.setattr(live, "ClaudeCallProvider", Stub)

    def fake_run(cfg, provider, run_root, **kw):
        seen.update(kw)
        (Path(run_root) / cfg.campaign_id).mkdir()
        return {"run_dir": str(Path(run_root) / cfg.campaign_id)}
    monkeypatch.setattr(live, "run_campaign", fake_run)
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "configs" / "campaigns" / "m1-live.json"
    out = live.run_live(CampaignConfig.model_validate_json(path.read_text()), tmp_path)
    assert isinstance(seen.get("broker"), Stub)
    assert sorted(p.name for p in (tmp_path / "m1-live-4").iterdir()) == []
    assert "broker_manifest" not in out
