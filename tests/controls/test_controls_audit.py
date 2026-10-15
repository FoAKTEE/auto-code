"""The offline audit re-runs the recorded control suite from the frozen campaign's inputs and
requires byte-identical evidence and equal counts (contracts §I, §M)."""
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from zkqec.admission import Admission
from zkqec.contracts import canonical_json
from zkqec.controls.record import audit_controls, campaign_inputs
from zkqec.orchestrator.campaign import run_campaign
from zkqec.pipeline import build_registry
from zkqec.report.bundle import audit_inner
from zkqec.report.render import render
from zkqec.store import ArtifactStore

from test_campaign_controls import GOOD, ReplayDouble, cfg


@pytest.fixture
def campaign(controls_campaign):
    return controls_campaign[1]


@pytest.fixture
def run(campaign, tmp_path):
    """A private copy of the campaign directory (ledger, store, report)."""
    dst = tmp_path / "ctl"
    shutil.copytree(campaign, dst, ignore=shutil.ignore_patterns("controls", "calls"))
    return dst


def opened(run):
    store = ArtifactStore(run / "store")
    return Admission(run / "ledger.db", store, build_registry(store)), store


def bundle_dir(run, dst):
    """The bundle layout ``audit_inner`` reads: manifest with the ledger head, ledger, store, report."""
    b = dst / "bundle"
    b.mkdir(parents=True)
    src = sqlite3.connect(run / "ledger.db")
    out = sqlite3.connect(b / "ledger.db")
    src.backup(out)
    out.close()
    head = src.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    src.close()
    shutil.copytree(run / "store", b / "store")
    shutil.copyfile(run / "report.md", b / "report.md")
    (b / "manifest.json").write_text(json.dumps({"campaign_id": "ctl", "ledger_head": {
        "seq": head[0], "hash": head[1]}}))
    return b


def test_recorded_controls_are_reproduced_offline(run, tmp_path):
    result = audit_inner(str(bundle_dir(run, tmp_path / "audit")))
    assert result["ok"], result["problems"]
    assert result["controls_reproduced"] == 1


def test_tampered_counts_or_evidence_and_unknown_sets_fail_the_audit(run, tmp_path):
    """Trusted-writer forgeries: further controls_recorded events that pass every apply check, with
    the report re-rendered, so that only the offline reproduction can object."""
    adm, store = opened(run)
    with adm:
        (rec,) = adm.ledger.controls("ctl")
        genuine = store.get_json(rec["evidence"])
        hidden = [dict(r) for r in rec["results"]]
        hidden[0]["injections"] += 1                  # an extra injection claimed to have held
        adm.record_controls("ctl", "p1a", hidden, evidence=rec["evidence"])
        edited = json.loads(canonical_json(genuine))
        edited["controls"][0]["injections"][0]["observed"]["outcome"] = "aborted (edited)"
        adm.record_controls("ctl", "p1a", rec["results"], evidence=store.put_json(edited))
        adm.record_controls("ctl", "p9-unknown", rec["results"], evidence=rec["evidence"])
        (run / "report.md").write_text(render(adm, "ctl"))
    result = audit_inner(str(bundle_dir(run, tmp_path / "audit")))
    problems = [p for p in result["problems"] if p.startswith("controls: ")]
    assert not result["ok"] and len(problems) == len(result["problems"]) == 3, result
    assert "(record 1): recorded counts differ from the offline re-run" in problems[0]
    assert "(record 2): recorded evidence" in problems[1] and "differs from the offline" in \
        problems[1] and "counts" not in problems[1]
    assert "p9-unknown (record 3): unknown control set" in problems[2]
    assert result["controls_reproduced"] == 1           # the genuine record still reproduces


def test_a_campaign_without_controls_needs_no_scratch(tmp_path):
    root = tmp_path / "plain"
    run_campaign(cfg(controls=None), ReplayDouble([GOOD]), root, require_clean=False,
                 seal_bundle=False)
    adm, store = opened(root / "ctl")
    with adm:
        assert audit_controls(adm, store, "ctl", tmp_path / "rerun") == ([], 0)
        assert campaign_inputs(adm, store, "ctl").families == ("explicit_sparse",)
    assert not (tmp_path / "rerun").exists()


@pytest.mark.slow
def test_sealed_bundle_reproduces_its_controls_offline(campaign, tmp_path):
    """Round trip through the real bundle: embedded evaluator closure, archived runtime and wheels,
    no network. Needs the controls package in the committed evaluator (HEAD)."""
    from zkqec.report.bundle import audit_bundle, seal
    run = tmp_path / "ctl"
    shutil.copytree(campaign, run, ignore=shutil.ignore_patterns("controls"))
    entry = seal(run, "ctl", run / "ledger.db", run / "store", (run / "report.md").read_text(),
                 extra_dirs={"calls": run / "calls"}, out_dir=tmp_path / "bundles")
    result = audit_bundle(Path(entry["path"]), workdir=tmp_path / "audit")
    assert result["ok"], result["problems"]
    assert result["controls_reproduced"] == 1 and result["offline"]


def test_the_controls_reservation_must_be_on_record(run):
    """Codex M1 milestone review #7: the frozen config's controls job is part of the operational
    history the audit replays; deleting it (and its event-free trace) is flagged."""
    import sqlite3
    from zkqec.report.bundle import audit_campaign
    conn = sqlite3.connect(run / "ledger.db", isolation_level=None)
    conn.execute("DELETE FROM ops_jobs WHERE idem_key='controls:p1a'")
    conn.close()
    adm, store = opened(run)
    with adm:
        problems = audit_campaign(adm, store, "ctl")
    assert any("controls:p1a" in p and "no operational job" in p for p in problems), problems
