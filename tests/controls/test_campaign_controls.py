"""A campaign with ``controls`` set runs the §I suite after dispatch and before sealing, as a reserved
verifier-class job, and records the summary in its ledger; the report renders it (contracts §I,
§K; M1 acceptance #17)."""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from zkqec.admission import Admission
from zkqec.contracts import Profile, Reservation, StructuralTargets, TaskSA, content_hash
from zkqec.kernel.codes import bicycle_chain
from zkqec.orchestrator.campaign import CallResult, CampaignConfig, run_campaign
from zkqec.pipeline import build_registry
from zkqec.report.bundle import audit_campaign
from zkqec.report.render import lint
from zkqec.store import ArtifactStore

REPO = Path(__file__).resolve().parents[2]


def sparse(a):
    a = np.asarray(a)
    return {"shape": list(a.shape), "entries": [[int(i), int(j)] for i, j in zip(*np.nonzero(a))]}


GOOD = json.dumps({"family": "explicit_sparse", "rationale": "x",
                   "params": {"hx": sparse(bicycle_chain(3, 7).hx),
                              "hz": sparse(bicycle_chain(3, 7).hz)}})


class ReplayDouble:
    """TEST DOUBLE provider: replays fixed outputs (never used for scientific runs)."""
    name, model = "replay", "replay-double"

    def __init__(self, outputs):
        self.outputs, self.requests = list(outputs), []

    def call(self, *, job_id, system_prompt, request, schema, reservation):
        self.requests.append(json.loads(request))
        return CallResult("completed", self.outputs.pop(0), {"usd": 0.01, "requests": 1},
                          {"provider": "replay"})


def cfg(**kw):
    base = dict(campaign_id="ctl", profile=Profile(knowledge="K0", prompt="P-min",
                                                   grammar=("G-open",)),
                tasks=(TaskSA(task_id="sa-hidden", targets=StructuralTargets(
                    n_max=50, k_min=6, d_min=6, w_max=8, deg_max=8)),),
                provider="replay", model="replay-double", max_proposals_per_task=2,
                caps={"usd": 1.0, "requests": 20, "cpu_seconds": 5000, "wall_seconds": 5000},
                per_call=Reservation(usd=0.2, requests=2, input_tokens=0, output_tokens=0,
                                     wall_seconds=0, cpu_seconds=0), controls="p1a")
    base.update(kw)
    return CampaignConfig(**base)


@pytest.fixture(scope="module")
def ran(controls_campaign):
    out, run = controls_campaign                    # conftest: one shared replay campaign
    store = ArtifactStore(run / "store")
    adm = Admission(run / "ledger.db", store, build_registry(store))
    yield out, run, adm, store
    adm.close()


def test_controls_are_recorded_in_the_ledger_and_returned(ran):
    out, run, adm, store = ran
    (rec,) = adm.ledger.controls("ctl")
    evidence = store.get_json(rec["evidence"])
    assert rec["control_set"] == "p1a" and rec["results"] == evidence["results"]
    assert content_hash(evidence) == rec["evidence"]
    assert len(rec["results"]) == 20
    assert out["controls"] == {
        "control_set": "p1a", "ok": True, "passed": True, "evidence": rec["evidence"],
        "injections": sum(r["injections"] for r in rec["results"]),
        "false_authorizations": 0,
        "paired_positives": sum(r["paired_positives"] for r in rec["results"]),
        "false_rejections": 0, "outcome_mismatches": 0}
    # the inputs come from the frozen campaign: its manifest's output schema, its profile families
    manifest = store.get_json(adm.ledger.campaign("ctl")["manifest"])
    schema = json.loads((REPO / "roles" / "designer" / "output.schema.json").read_text())
    assert json.loads(store.get_bytes(manifest["output_schema"])) == schema
    assert evidence["inputs"] == {"schema_sha256": manifest["output_schema"],
                                  "families": ["explicit_sparse"]}
    assert (run / "controls").is_dir()                       # scratch under the campaign work dir


def test_report_renders_the_recorded_controls(ran):
    out, run, adm, store = ran
    md = (run / "report.md").read_text()
    assert "Control set `p1a`" in md and "none recorded" not in md
    assert "- Result: **PASSED**" in md and "FAILED" not in md
    assert "| task_mutated | 0/3 | 0/1 |" in md and "| semantic_conflict | 0/2 | 0/2 |" in md
    c = out["controls"]
    assert f"False authorizations: 0/{c['injections']}; false rejections: 0/" \
           f"{c['paired_positives']}" in md
    assert lint(md, adm, "ctl") == []


def test_the_control_run_is_a_charged_verifier_job_reserved_before_dispatch(ran):
    out, run, adm, store = ran
    conn = sqlite3.connect(run / "ledger.db")
    jobs = conn.execute("SELECT kind, idem_key, state FROM ops_jobs ORDER BY created_at").fetchall()
    conn.close()
    assert [j[:2] for j in jobs[:3]] == [("confirm", "confirmation"), ("verify", "controls:p1a"),
                                        ("propose", "sa-hidden:0")]
    (s,) = [s for s in adm.ledger.settlements("ctl") if s["idem_key"] == "controls:p1a"]
    assert s["kind"] == "verify" and s["from_state"] == "completed"
    assert 0 < s["charged"]["wall_seconds"] <= s["reservation"]["wall_seconds"]
    assert 0 < s["charged"]["cpu_seconds"] <= s["reservation"]["cpu_seconds"]
    assert s["charged"]["usd"] == 0
    assert adm.verify().ok and audit_campaign(adm, store, "ctl") == []


def test_a_failing_control_suite_keeps_its_evidence_and_fails_the_campaign(tmp_path, monkeypatch):
    """Codex M1 milestone review B3: a suite exception preserves the evidence produced so far, writes
    a failed-controls record, and the audit and the report show the required set as FAILED (it used
    to report "controls not supplied" with a clean audit)."""
    from zkqec.controls import p1a
    from zkqec.controls.record import audit_controls
    names = [c.name for c in p1a.CONTROL_SETS["p1a"]]
    orig = p1a._run_control

    def third_fails(c, root, inputs):
        if c.name == names[2]:
            raise RuntimeError("injected suite failure")
        return orig(c, root, inputs)
    monkeypatch.setattr(p1a, "_run_control", third_fails)
    out = run_campaign(cfg(), ReplayDouble([GOOD]), tmp_path, require_clean=False,
                       seal_bundle=False)
    c = out["controls"]
    assert c["ok"] is False and c["passed"] is False and "RuntimeError" in c["error"]
    store = ArtifactStore(tmp_path / "ctl" / "store")
    evidence = store.get_json(c["evidence"])
    assert evidence["kind"] == p1a.FAILED_KIND and evidence["control_set"] == "p1a"
    assert evidence["failed"]["control"] == names[2] and "RuntimeError" in evidence["failed"]["error"]
    assert [x["control"] for x in evidence["partial"]["controls"]] == names[:2]
    with Admission(tmp_path / "ctl" / "ledger.db", store, build_registry(store)) as adm:
        assert adm.ledger.controls("ctl") == []
        (inc,) = [e.payload for e in adm.ledger.events() if e.kind == "incident_opened"]
        assert inc["category"] == "controls_failed" and inc["detail"]["evidence"] == c["evidence"]
        (s,) = [s for s in adm.ledger.settlements("ctl") if s["idem_key"] == "controls:p1a"]
        assert s["from_state"] == "failed" and s["result"] == c["evidence"]
        assert audit_campaign(adm, store, "ctl") == []          # the accounting itself is sound
        problems, _ = audit_controls(adm, store, "ctl", tmp_path / "rerun-scratch")
        assert any("required control set p1a" in p and "not recorded" in p for p in problems)
        md = (tmp_path / "ctl" / "report.md").read_text()
        assert lint(md, adm, "ctl") == []
    assert "not supplied" not in md and "**FAILED**" in md and c["evidence"] in md


def test_a_reproducible_false_authorization_fails_the_controls(tmp_path, monkeypatch):
    """Byte-identical reproduction is not a pass: k > 0 fails the result, the report and the audit."""
    from zkqec.controls import p1a
    from zkqec.controls.record import audit_controls
    first = p1a.CONTROL_SETS["p1a"][0].name
    orig = p1a._run_control

    def leaky(c, root, inputs):
        row = orig(c, root, inputs)
        if c.name == first:
            row["injections"][0]["observed"]["authorized"] = True
            row["counts"]["false_authorizations"] = 1
        return row
    monkeypatch.setattr(p1a, "_run_control", leaky)
    out = run_campaign(cfg(), ReplayDouble([GOOD]), tmp_path, require_clean=False,
                       seal_bundle=False)
    assert out["controls"]["ok"] is True and out["controls"]["passed"] is False
    assert out["controls"]["false_authorizations"] == 1
    md = (tmp_path / "ctl" / "report.md").read_text()
    assert "- Result: **FAILED** (1 false authorizations, 0 false rejections, 0 outcome mismatches)" in md
    store = ArtifactStore(tmp_path / "ctl" / "store")
    with Admission(tmp_path / "ctl" / "ledger.db", store, build_registry(store)) as adm:
        problems, reproduced = audit_controls(adm, store, "ctl", tmp_path / "rerun-scratch")
    assert reproduced == 1                                  # it reproduces byte-identically ...
    assert any("did not pass" in p and "1 false authorizations" in p for p in problems)   # ... and fails


@pytest.mark.parametrize("edit, passed", [
    (lambda e: None, True),
    (lambda e: e["controls"][0]["counts"].update(false_rejections=1), False),
    (lambda e: e["controls"][1]["counts"].update(outcome_mismatches=2), False),
    (lambda e: e["controls"].pop(), False),                                   # incomplete set
    (lambda e: e.update(kind="something-else"), False),
])
def test_the_control_pass_result(ran, edit, passed):
    from zkqec.controls.p1a import verdict
    out, run, adm, store = ran
    (rec,) = adm.ledger.controls("ctl")
    evidence = json.loads(json.dumps(store.get_json(rec["evidence"])))
    edit(evidence)
    assert verdict(evidence)["passed"] is passed


def test_controls_are_off_by_default_and_on_for_the_live_config(tmp_path):
    assert CampaignConfig(**{**cfg().model_dump(), "controls": None}).controls is None
    assert "controls" not in {k for k, v in cfg(controls=None).model_dump().items() if v}
    live = CampaignConfig.model_validate_json(
        (REPO / "configs" / "campaigns" / "m1-live.json").read_text())
    assert live.controls == "p1a" and live.campaign_id.startswith("m1-live-")
    with pytest.raises(ValueError):
        cfg(controls="p9")
