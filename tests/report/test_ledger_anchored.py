"""Report inputs and accounting are ledger-anchored; the audit reconciles operational state and
traverses every referenced artifact (Codex M1 review #3 and #7; appendix §A5, §K, §L, §M)."""
import inspect
import json
import sqlite3

import numpy as np
import pytest

from zkqec.admission import Admission, LedgerError
from zkqec.contracts import Proposal, StructuralTargets, TaskSA, content_hash
from zkqec.grammar import GrammarCaps
from zkqec.kernel.codes import steane
from zkqec.pipeline import Pipeline, build_registry, evaluator_domain
from zkqec.report.bundle import audit_campaign
from zkqec.report.render import esc, lint, render
from zkqec.store import ArtifactStore


def sparse(a):
    a = np.asarray(a)
    return {"shape": list(a.shape), "entries": [[int(i), int(j)] for i, j in zip(*np.nonzero(a))]}


def steane_task(d_min):
    return TaskSA(task_id="sa-steane", targets=StructuralTargets(n_max=7, k_min=1, d_min=d_min))


@pytest.fixture
def camp(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    reg = build_registry(store)
    adm = Admission(tmp_path / "ledger.db", store, reg)
    dom = evaluator_domain(reg)
    task = steane_task(5)
    adm.freeze_campaign("c3", namespace="c3", config={"tasks": [task.model_dump(mode="json")]},
                        tasks=[task.model_dump(mode="json")], caps={"cpu_seconds": 1e4},
                        labels=["single-implementation"], domain=dom,
                        manifest=store.put_json({"c": 3}))
    book = adm.job_book("c3")
    book.set_caps({"cpu_seconds": 1e4})
    pipe = Pipeline(adm, store, book, dom, caps=GrammarCaps())
    h = np.asarray(steane().hx)
    ev = pipe.evaluate(Proposal(family="explicit_sparse",
                                params={"hx": sparse(h), "hz": sparse(h)}), task)
    adm.record_evaluation("c3", "sa-steane", candidate=ev.candidate, source="fixture")
    yield adm, store, book, dom
    adm.close()


# --- #3: tasks, membership, accounting and labels come from the ledger -----------------------------
def test_tasks_come_from_the_frozen_campaign_not_the_caller(camp):
    adm, store, _, dom = camp
    md = render(adm, "c3")
    assert "**unknown**" in md and "**verified**" not in md        # Steane d=3 < d_min=5
    assert list(inspect.signature(render).parameters) == ["adm", "campaign_id", "commentary"]
    with pytest.raises(LedgerError, match="already frozen"):        # no second, laxer task set
        adm.freeze_campaign("c3", namespace="c3", config={}, tasks=[steane_task(3).model_dump(
            mode="json")], caps={}, labels=[], domain=dom, manifest=store.put_json({}))
    with pytest.raises(LedgerError, match="not frozen"):
        adm.record_evaluation("c3", "sa-other", candidate=adm.ledger.evaluations("c3")[0][
            "candidate"], source="fixture")
    forged = md.replace('"d_min": 5', '"d_min": 3').replace("**unknown**", "**verified**")
    assert lint(forged, adm, "c3") and lint(md, adm, "c3") == []


def test_frozen_config_must_carry_the_same_tasks(tmp_path):
    store = ArtifactStore(tmp_path / "s")
    with Admission(tmp_path / "l.db", store, build_registry(store)) as adm:
        with pytest.raises(LedgerError, match="frozen config's tasks"):
            adm.freeze_campaign("cx", namespace="cx",
                                config={"tasks": [steane_task(5).model_dump(mode="json")]},
                                tasks=[steane_task(3).model_dump(mode="json")], caps={},
                                labels=[], domain="0" * 64, manifest=store.put_json({}))


def _settlement(**kw):
    base = {"campaign_id": "c3", "namespace": "c3", "job_id": "a" * 32, "kind": "propose",
            "idem_key": "k", "retry_of": None, "generation": 1, "from_state": "completed",
            "reservation": {u: 1.0 for u in ("usd", "requests", "input_tokens", "output_tokens",
                                             "wall_seconds", "cpu_seconds")},
            "actual": {"usd": 0.5}, "overrun": False, "result": None}
    base["charged"] = {**base["reservation"], "usd": 0.5}
    base.update(kw)
    return base


def test_accounting_is_derived_from_validated_settlement_events(camp):
    adm, _, book, _ = camp
    data_spent = book.totals()["cpu_seconds"]["spent"]
    assert data_spent > 0 and f"| cpu_seconds | 10000 | {data_spent:g} |" in render(adm, "c3")
    bad = [_settlement(charged={**_settlement()["charged"], "usd": -99.0}),      # negative
           _settlement(charged={**_settlement()["charged"], "usd": 0.0}),        # not the rule
           _settlement(actual={"usd": float("nan")}),                            # non-finite
           _settlement(namespace="other"),                                       # wrong owner
           _settlement(overrun=True)]                                            # inconsistent
    for payload in bad:
        with pytest.raises((LedgerError, ValueError)):      # NaN is refused by canonical JSON
            with adm.ledger.transaction():
                adm.ledger.append("job_settled", payload, writer=adm._writer)


def test_labels_sources_and_ids_are_typed(camp, tmp_path):
    adm, store, _, dom = camp
    cand = adm.ledger.evaluations("c3")[0]["candidate"]
    for source in ("designer:x\n## injected heading", "Designer", "x|y", "a" * 65):
        with pytest.raises(LedgerError, match="source"):
            adm.record_evaluation("c3", "sa-steane", candidate=cand, source=source)
    with pytest.raises(LedgerError, match="labels"):
        adm.freeze_campaign("c4", namespace="c4", config={}, tasks=[steane_task(3).model_dump(
            mode="json")], caps={}, labels=["verified-novel"], domain=dom,
            manifest=store.put_json({}))
    bad_task = TaskSA(task_id="x|## y", targets=StructuralTargets(n_max=7, k_min=1, d_min=3))
    with pytest.raises(LedgerError, match="task id"):
        adm.freeze_campaign("c5", namespace="c5", config={}, tasks=[bad_task.model_dump(
            mode="json")], caps={}, labels=[], domain=dom, manifest=store.put_json({}))
    assert esc("a|b\n## c <x> [l](u)") == "a\\|b \\#\\# c \\<x\\> \\[l\\](u)"


# --- #7: the audit reconciles operations and traverses every referenced artifact -------------------
class ReplayDouble:
    """TEST DOUBLE provider: replays fixed outputs (never used for scientific runs)."""
    name, model = "replay", "replay-double"

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def call(self, *, job_id, system_prompt, request, schema, reservation):
        from zkqec.orchestrator.campaign import CallResult
        return CallResult("completed", self.outputs.pop(0), {"usd": 0.01, "requests": 1},
                          {"provider": "replay"})


@pytest.fixture
def ran(tmp_path):
    from zkqec.contracts import Profile, Reservation
    from zkqec.kernel.codes import bicycle_chain
    from zkqec.orchestrator.campaign import CampaignConfig, run_campaign
    c = bicycle_chain(3, 7)
    good = json.dumps({"family": "explicit_sparse", "rationale": "x",
                       "params": {"hx": sparse(c.hx), "hz": sparse(c.hz)}})
    cfg = CampaignConfig(
        campaign_id="t1", profile=Profile(knowledge="K0", prompt="P-min", grammar=("G-open",)),
        tasks=(TaskSA(task_id="sa-secret-id", targets=StructuralTargets(
            n_max=50, k_min=6, d_min=6, w_max=8, deg_max=8)),),
        provider="replay", model="replay-double", max_proposals_per_task=4,
        caps={"usd": 1.0, "requests": 20},
        per_call=Reservation(usd=0.2, requests=2, input_tokens=0, output_tokens=0,
                             wall_seconds=0, cpu_seconds=0))
    out = run_campaign(cfg, ReplayDouble(["not json", good]), tmp_path,
                       require_clean=False, seal_bundle=False)
    run = tmp_path / "t1"
    store = ArtifactStore(run / "store")
    adm = Admission(run / "ledger.db", store, build_registry(store))
    yield out, run, adm, store
    adm.close()


def test_clean_campaign_audits_clean(ran):
    _, run, adm, store = ran
    assert adm.verify().ok and audit_campaign(adm, store, "t1") == [] and store.verify_all() == []
    assert lint((run / "report.md").read_text(), adm, "t1") == []


def test_tampered_operational_charge_is_detected(ran):
    _, run, adm, store = ran
    conn = sqlite3.connect(run / "ledger.db", isolation_level=None)
    job, actual = conn.execute("SELECT job_id, actual FROM ops_jobs WHERE kind='propose' "
                               "AND state='settled' LIMIT 1").fetchone()
    tampered = {**json.loads(actual), "usd": 0.0}
    conn.execute("UPDATE ops_jobs SET actual=? WHERE job_id=?", (json.dumps(tampered), job))
    conn.close()
    problems = audit_campaign(adm, store, "t1")
    assert any(job in p and "differs from its settlement event" in p for p in problems), problems


def test_missing_call_record_and_corrupt_proposal_fail_the_audit(ran):
    _, run, adm, store = ran
    ev = adm.ledger.evaluations("t1")[0]
    store.path_for(ev["call_record"]).unlink()
    path = store.path_for(ev["proposal"])
    path.chmod(0o644)
    path.write_bytes(path.read_bytes().replace(b"explicit_sparse", b"explicit_sparsX"))
    joined = " | ".join(adm.verify().problems)
    assert ev["call_record"] in joined and "ArtifactNotFound" in joined
    assert ev["proposal"] in joined and "IntegrityError" in joined
    assert any(ev["call_record"] in p for p in audit_campaign(adm, store, "t1"))
    assert any(ev["proposal"] in p for p in store.verify_all())


def test_unsettled_jobs_and_orphan_events_fail_reconciliation(ran):
    _, run, adm, store = ran
    conn = sqlite3.connect(run / "ledger.db", isolation_level=None)
    settled = conn.execute("SELECT job_id FROM ops_jobs WHERE state='settled' ORDER BY job_id "
                           "LIMIT 1").fetchone()[0]
    conn.execute("DELETE FROM ops_jobs WHERE job_id = ?", (settled,))
    conn.execute("UPDATE ops_jobs SET state='dispatched' WHERE kind='propose' AND state='settled'")
    conn.close()
    problems = audit_campaign(adm, store, "t1")
    assert any(settled in p and "has no operational job" in p for p in problems), problems
    assert any("dispatched: unsettled at audit" in p for p in problems), problems


# --- Codex M1 review #17 (code side): §I control summary; cost and autonomy from events -----------
CONTROLS = [
    {"control": "semantic_conflict_lower5_upper3", "injections": 1, "false_authorizations": 0,
     "paired_positives": 1, "false_rejections": 0},
    {"control": "steane_t2_d5_misreading", "injections": 2, "false_authorizations": 0,
     "paired_positives": 2, "false_rejections": 1},
]


def test_report_renders_the_control_summary_when_supplied(camp):
    adm, store, _, _ = camp
    assert "## Controls" in render(adm, "c3") and "none recorded" in render(adm, "c3")
    evidence = store.put_json({"pytest": "log"})
    adm.record_controls("c3", "p1a-controls-v1", CONTROLS, evidence=evidence)
    md = render(adm, "c3")
    assert "| semantic_conflict_lower5_upper3 | 0/1 | 0/1 |" in md
    assert "| steane_t2_d5_misreading | 0/2 | 1/2 |" in md
    assert "False authorizations: 0/3; false rejections: 1/3" in md
    assert lint(md, adm, "c3") == []


@pytest.mark.parametrize("bad", [
    {"false_authorizations": 2},                 # k > N
    {"false_rejections": -1},
    {"injections": 1.5},
    {"control": "Bad Name"},
    {"extra": 1},
])
def test_control_results_are_validated_at_apply(camp, bad):
    adm, store, _, _ = camp
    row = {**CONTROLS[0], **bad}
    with pytest.raises((LedgerError, ValueError)):
        adm.record_controls("c3", "p1a-controls-v1", [row], evidence=store.put_json({"e": 1}))


def test_autonomy_and_cost_sections_come_from_ledger_events(camp):
    adm, _, book, _ = camp
    adm.record_autonomy("c3", "owner", "pause", "paused for broker maintenance")
    md = render(adm, "c3")
    assert "- owner: pause — paused for broker maintenance" in md
    spent = sum(s["charged"]["cpu_seconds"] for s in adm.ledger.settlements("c3"))
    assert spent == book.totals()["cpu_seconds"]["spent"] and f"| {spent:g} |" in md


# --- Codex M1 milestone review #7: the audit replays the complete operational history -------------
def _ops(run, sql, *args):
    conn = sqlite3.connect(run / "ledger.db", isolation_level=None)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_codex_repro_edited_input_hashes_and_a_deleted_confirmation_fail_the_audit(ran):
    """The milestone repro: proposal input_sha values changed and the reserved confirmation job
    deleted from a copy of the ledger gave verify=True and audit_campaign=[]."""
    _, run, adm, store = ran
    other = adm.ledger.campaign("t1")["manifest"]                    # a valid, unrelated artifact
    _ops(run, "UPDATE ops_jobs SET input_sha=? WHERE kind='propose'", other)
    (confirm,) = _ops(run, "SELECT job_id FROM ops_jobs WHERE kind='confirm'")
    _ops(run, "DELETE FROM ops_jobs WHERE kind='confirm'")
    assert adm.verify().ok                                 # the hash chain cannot see ops tables
    problems = audit_campaign(adm, store, "t1")
    jobs = [j for j, in _ops(run, "SELECT job_id FROM ops_jobs WHERE kind='propose'")]
    for job in jobs:
        assert any(job in p and "input" in p for p in problems), problems
    assert any("confirmation" in p and "no operational job" in p for p in problems), problems
    assert any(confirm[0] in p and "ops_log" in p for p in problems), problems   # orphan history


def test_each_input_hash_must_name_the_intent_of_its_call(ran):
    _, run, adm, store = ran
    (job, intent), *_ = _ops(run, "SELECT job_id, input_sha FROM ops_jobs WHERE kind='propose' "
                                  "ORDER BY created_at")
    # a call_inputs intent of the right shape, but not the one this call recorded
    forged = store.put_json({**store.get_json(intent), "attempt": 7})
    _ops(run, "UPDATE ops_jobs SET input_sha=? WHERE job_id=?", forged, job)
    problems = audit_campaign(adm, store, "t1")
    assert any(job in p and "input" in p for p in problems), problems


def test_reservations_must_be_the_frozen_ones(ran):
    _, run, adm, store = ran
    _ops(run, "UPDATE ops_jobs SET reservation=? WHERE kind='confirm'",
         json.dumps({"usd": 0.0, "requests": 0, "input_tokens": 0, "output_tokens": 0,
                     "wall_seconds": 1, "cpu_seconds": 1}))
    problems = audit_campaign(adm, store, "t1")
    assert any("confirmation" in p for p in problems), problems


def test_orphan_operational_records_are_flagged(ran):
    _, run, adm, store = ran
    conn = sqlite3.connect(run / "ledger.db", isolation_level=None)
    conn.execute("INSERT INTO ops_log (job_id, event, generation, detail, at) VALUES "
                 "(?, 'reserved', 0, '{}', 'x')", ("e" * 32,))
    conn.execute("INSERT INTO ops_jobs (job_id, campaign, kind, idem_key, input_sha, retry_of, state, "
                 "generation, reservation, created_at, updated_at) SELECT ?, campaign, kind, "
                 "'sa-unfrozen:0', input_sha, NULL, 'reserved', 0, reservation, created_at, "
                 "updated_at FROM ops_jobs WHERE kind='propose' LIMIT 1", ("d" * 32,))
    conn.close()
    problems = audit_campaign(adm, store, "t1")
    assert any("e" * 32 in p and "no operational job" in p for p in problems), problems
    assert any("d" * 32 in p and "not an attempt of a frozen task" in p for p in problems), problems


def test_an_out_of_order_operational_history_is_flagged(ran):
    _, run, adm, store = ran
    (job,) = _ops(run, "SELECT job_id FROM ops_jobs WHERE kind='propose' ORDER BY created_at LIMIT 1")
    detail = _ops(run, "SELECT detail FROM ops_log WHERE job_id=? AND event='settled'", job[0])[0][0]
    conn = sqlite3.connect(run / "ledger.db", isolation_level=None)
    conn.execute("INSERT INTO ops_log (job_id, event, generation, detail, at) "
                 "SELECT job_id, 'dispatched', generation + 1, '{}', at FROM ops_log WHERE "
                 "job_id=? AND event='settled'", (job[0],))
    conn.execute("INSERT INTO ops_log (job_id, event, generation, detail, at) "
                 "SELECT job_id, 'settled', generation, ?, at FROM ops_log WHERE "
                 "job_id=? AND event='settled'", (detail, job[0]))
    conn.close()
    problems = audit_campaign(adm, store, "t1")
    assert any(job[0] in p and "settled -> dispatched" in p for p in problems), problems


def test_a_campaign_frozen_without_campaign_ceilings_audits_clean(camp):
    """A ledger frozen outside run_campaign (no verifier ceilings, per-call reservation or
    confirmation in its config) has no frozen ceilings to compare its verifier jobs with."""
    adm, store, _, _ = camp
    assert audit_campaign(adm, store, "c3") == []
