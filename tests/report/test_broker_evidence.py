"""Bundles carry the broker evidence and the confirmation settles outside the seal only through a
hash-linked supplement (Codex M1 milestone review B6, #15 residual; contracts §J5, §K, §M, §O).

A live-like campaign: the production ``ClaudeCallProvider`` and a real (not started) campaign
``Broker``; ``run_job`` is replaced by a CLI stand-in that writes the broker records a sandboxed CLI
would cause (connect, allowed model requests, responses with or without complete usage). No process
is launched and nothing leaves the host.
"""
from __future__ import annotations

import io
import json
import shutil
import tarfile
from pathlib import Path

import numpy as np
import pytest
import zstandard

from zkqec.admission import Admission
from zkqec.agents import providers
from zkqec.broker.launcher import Broker, sha256_file
from zkqec.broker.testing import local_policy_overrides
from zkqec.contracts import Profile, Reservation, StructuralTargets, TaskSA
from zkqec.kernel.codes import bicycle_chain
from zkqec.orchestrator.campaign import CampaignConfig, run_campaign
from zkqec.pipeline import build_registry
from zkqec.report import bundle as bundle_mod
from zkqec.report.bundle import audit_broker, audit_campaign, check_confirmation
from zkqec.store import ArtifactStore, SecretDetected

PER_CALL = Reservation(usd=1.92, requests=2, input_tokens=80_000, output_tokens=64_000,
                       wall_seconds=900, cpu_seconds=0)


def _sparse(a):
    a = np.asarray(a)
    return {"shape": list(a.shape), "entries": [[int(i), int(j)] for i, j in zip(*np.nonzero(a))]}


GOOD = {"family": "explicit_sparse", "rationale": "x",
        "params": {"hx": _sparse(bicycle_chain(3, 7).hx), "hz": _sparse(bicycle_chain(3, 7).hz)}}
BAD = {"family": "no_such_family", "rationale": "x", "params": {}}


def cfg(cid: str) -> CampaignConfig:
    return CampaignConfig(
        campaign_id=cid, profile=Profile(knowledge="K0", prompt="P-min", grammar=("G-open",)),
        tasks=(TaskSA(task_id="sa-hidden", targets=StructuralTargets(
            n_max=50, k_min=6, d_min=6, w_max=8, deg_max=8)),),
        provider="claude", model="claude-opus-5-5", max_proposals_per_task=2,
        caps={"usd": 10.0, "requests": 40, "input_tokens": 4e6, "output_tokens": 4e5,
              "wall_seconds": 1e5, "cpu_seconds": 1e5}, per_call=PER_CALL)


class FakeCli:
    """Stands in for ``run_job``: appends the broker records of one sandboxed CLI call."""

    def __init__(self, broker: Broker, second_usage: dict | None = None) -> None:
        self.broker, self.calls, self.seq = broker, 0, 0
        self.second_usage = second_usage
        self._write({"event": "broker_start", "policy_sha256": sha256_file(broker.policy_path),
                     "pid": 1})

    def _write(self, rec: dict) -> None:
        self.seq += 1
        line = json.dumps({"seq": self.seq, "ts": float(self.seq), "campaign": self.broker.campaign,
                           **rec}, sort_keys=True, separators=(",", ":"))
        with open(self.broker.log_path, "a") as fh:
            fh.write(line + "\n")

    def _request(self, job: str, n: int) -> None:
        self._write({"event": "request", "job_id": job, "host": "api.anthropic.com", "method": "POST",
                     "path": "/v1/messages", "kind": "model", "decision": "allow", "reason": "ok",
                     "model": "claude-opus-5-5", "tools": ["StructuredOutput"], "tools_key": True,
                     "max_output_tokens": 32_000, "output_field": "present", "input_bytes": 9_000,
                     "model_request": True, "model_requests_after": n, "total_requests_after": n})

    def _response(self, job: str, usage: dict | None) -> None:
        self._write({"event": "response", "job_id": job, "host": "api.anthropic.com",
                     "path": "/v1/messages", "status": 200, "body_bytes": 100, "usage": usage,
                     "stop_reason": "end_turn" if usage else None,
                     "usage_status": "reported" if usage else "unknown"})

    def __call__(self, spec, *a, **k):
        self.calls += 1
        job = spec.job_id
        self._write({"event": "connect", "job_id": job, "host": "api.anthropic.com", "port": 443,
                     "token_present": True, "decision": "allow", "reason": "host_allowlisted"})
        usage = {"input_tokens": 900, "cache_creation_input_tokens": 100, "output_tokens": 2_000}
        if self.calls == 1:                 # a cut stream, then the CLI's retry: usage incomplete
            self._request(job, 1)
            self._response(job, None)
            self._request(job, 2)
            self._response(job, usage)
            out = BAD
        else:
            self._request(job, 1)
            self._response(job, self.second_usage or usage)
            out = GOOD
        stdout = json.dumps({"structured_output": out, "total_cost_usd": 0.0472,
                             "is_error": False}).encode()
        return type("Res", (), {"outcome": "completed", "exit_code": 0, "wall_seconds": 3.0,
                                "blocked_reason": None, "profile_sha256": "0" * 64,
                                "stdout": stdout, "stderr": b""})()


def run_live_like(root: Path, mp: pytest.MonkeyPatch, cid: str = "evid", second_usage=None,
                  **kw) -> tuple[dict, Path, Broker]:
    broker = Broker(cid.replace(".", "-"), root / "ops", overrides=local_policy_overrides(443))
    mp.setattr(providers, "run_job", FakeCli(broker, second_usage))
    provider = providers.ClaudeCallProvider("claude-opus-5-5", broker=broker, slice_=None,
                                            jobs_root=root / "jobs")
    out = run_campaign(cfg(cid), provider, root / "runs", require_clean=False, broker=broker, **kw)
    return out, root / "runs" / cid, broker


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    root = tmp_path_factory.mktemp("live-like")
    with pytest.MonkeyPatch.context() as mp:
        out, run, broker = run_live_like(root, mp, seal_bundle=False)
    store = ArtifactStore(run / "store")
    adm = Admission(run / "ledger.db", store, build_registry(store))
    yield out, run, broker, adm, store
    adm.close()


def test_the_frozen_manifest_pins_the_broker(live):
    out, run, broker, adm, store = live
    frozen = store.get_json(adm.ledger.campaign("evid")["manifest"])["broker"]
    assert frozen["policy_sha256"] == sha256_file(broker.policy_path)
    assert frozen["prices_sha256"] == sha256_file(providers.PRICES)
    assert frozen["addon_sha256"] == out["broker_manifest"]["addon_sha256"]


def test_broker_evidence_is_exported_into_the_run_before_sealing(live):
    out, run, broker, adm, store = live
    ev = run / "broker"
    assert (ev / "broker-log.jsonl").read_bytes() == broker.log_path.read_bytes()
    assert json.loads((ev / "broker-manifest.json").read_text()) == out["broker_manifest"]
    jobs = sorted(store.get_json(s["result"])["sandbox_job_id"]
                  for s in adm.ledger.settlements("evid") if s["kind"] == "propose")
    assert sorted(p.stem for p in (ev / "grants").glob("*.json")) == jobs and len(jobs) == 2
    grant = json.loads((ev / "grants" / f"{jobs[0]}.json").read_text())
    assert (grant["max_model_requests"], grant["max_input_bytes"], grant["max_output_tokens"]) == \
        (2, 40_000, 32_000)
    assert bundle_mod.scan_bundle_inputs(ev) == []


def test_the_audit_reconstructs_calls_from_the_sealed_broker_log(live):
    out, run, broker, adm, store = live
    assert adm.verify().ok and audit_campaign(adm, store, "evid") == []
    assert audit_broker(adm, store, "evid", run / "broker") == []
    settled = {s["from_state"] for s in adm.ledger.settlements("evid") if s["kind"] == "propose"}
    assert settled == {"uncertain", "completed"}                # the cut stream stays uncertain


def _edit_log(ev: Path, fn) -> None:
    lines = [json.loads(x) for x in (ev / "broker-log.jsonl").read_text().splitlines()]
    (ev / "broker-log.jsonl").write_text("".join(json.dumps(x) + "\n" for x in fn(lines)))


def _second_job(ev: Path) -> str:
    return [x for x in map(json.loads, (ev / "broker-log.jsonl").read_text().splitlines())
            if x.get("event") == "connect"][1]["job_id"]


TAMPER = {
    "dropped_request": (lambda ev: _edit_log(ev, lambda L: [
        x for x in L if not (x.get("event") == "request" and x.get("job_id") == _second_job(ev))]),
        "call record broker model_requests differs from the sealed broker log"),
    "inflated_usage": (lambda ev: _edit_log(ev, lambda L: [
        {**x, "usage": {**x["usage"], "output_tokens": 1}} if x.get("event") == "response"
        and x.get("usage") else x for x in L]),
        "call record broker usage differs from the sealed broker log"),
    "completed_cut_stream": (lambda ev: _edit_log(ev, lambda L: [
        {**x, "usage": {"input_tokens": 1, "output_tokens": 1}, "usage_status": "reported"}
        if x.get("event") == "response" and not x.get("usage") else x for x in L]),
        "call record broker usage_missing differs"),
    "orphan_job": (lambda ev: _edit_log(ev, lambda L: L + [
        {**[x for x in L if x.get("event") == "request"][0], "job_id": "ffffffffffffffff",
         "seq": L[-1]["seq"] + 1}]),
        "broker job ffffffffffffffff has broker records but no settled call"),
    "torn_line": (lambda ev: (ev / "broker-log.jsonl").write_bytes(
        (ev / "broker-log.jsonl").read_bytes() + b'{"seq":99,"ev\n'), "is unreadable"),
    "policy_swapped": (lambda ev: (ev / "policy.json").write_text("{}"),
                       "policy.json is not the frozen policy_sha256"),
    "grant_widened": (lambda ev: [(g.write_text(g.read_text().replace('"max_input_bytes": 40000',
                                                                      '"max_input_bytes": 80000')))
                                  for g in (ev / "grants").glob("*.json")],
                      "issued grant is not the per-request derivation of its reservation"),
    "evidence_missing": (lambda ev: shutil.rmtree(ev), "holds no broker evidence"),
}


@pytest.mark.parametrize("name", sorted(TAMPER))
def test_tampered_broker_evidence_fails_the_audit(live, tmp_path, name):
    out, run, broker, adm, store = live
    ev = tmp_path / "broker"
    shutil.copytree(run / "broker", ev)
    edit, expected = TAMPER[name]
    edit(ev)
    problems = audit_broker(adm, store, "evid", ev)
    assert any(expected in p for p in problems), problems


def _lenient_summary(records):
    """The pre-N2 summary: any non-empty usage record counted as reported."""
    from zkqec.broker import launcher
    s = dict(launcher.summarize_job(records))
    reported = [r["usage"] for r in records if r.get("event") in ("response", "ws_server")
                and r.get("usage")]
    totals: dict = {}
    for u in reported:
        for k, v in u.items():
            totals[k] = totals.get(k, 0) + int(v)
    s.update(usage=totals or "unknown", usage_records=len(reported),
             usage_missing=max(0, s["model_requests"] - len(reported)))
    return s


def test_a_missing_input_counter_is_never_audited_as_zero(tmp_path, monkeypatch):
    """Codex M1 re-review N2: the second call's sealed broker log holds a complete-looking usage
    record without input_tokens, and the call was settled completed as if that counter were zero
    (the defect: the provider defaulted it, and the auditor made the same assumption). The broker
    audit must refuse it: missing mandatory usage settles uncertain at the reservation."""
    monkeypatch.setattr(providers, "summarize_job", _lenient_summary)
    monkeypatch.setattr(providers, "charged_tokens", lambda u: {        # the defaulting provider
        "input_tokens": float(u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                              + u.get("cache_read_input_tokens", 0)),
        "output_tokens": float(u.get("output_tokens", 0))}, raising=False)
    out, run, broker = run_live_like(tmp_path, monkeypatch, cid="n2", seal_bundle=False,
                                     second_usage={"cache_creation_input_tokens": 100,
                                                   "output_tokens": 2_000})
    store = ArtifactStore(run / "store")
    with Admission(run / "ledger.db", store, build_registry(store)) as adm:
        forged = [s for s in adm.ledger.settlements("n2")
                  if s["kind"] == "propose" and s["from_state"] == "completed"]
        assert len(forged) == 1 and forged[0]["actual"]["input_tokens"] == 100.0
        problems = audit_broker(adm, store, "n2", run / "broker")
    job = forged[0]["job_id"]
    assert any(job in p and "did not settle uncertain" in p for p in problems), problems


def test_a_secret_in_the_broker_evidence_refuses_the_seal(live, tmp_path, monkeypatch):
    out, run, broker, adm, store = live
    ev = tmp_path / "broker"
    shutil.copytree(run / "broker", ev)
    _edit_log(ev, lambda L: L + [{"seq": 999, "event": "note", "api_key": "opaque-secret-123"}])
    monkeypatch.setattr(bundle_mod, "ensure_closures", lambda *a, **k: {})
    with pytest.raises(SecretDetected, match="broker/broker-log.jsonl"):
        bundle_mod.seal(run, "evid", run / "ledger.db", run / "store", "# r\n",
                        archive=tmp_path / "archive", extra_dirs={"broker": ev}, commit="0" * 40)


# --- sealing order and the confirmation supplement ---------------------------------------------------
def _fake_closures(archive: Path, tmp: Path) -> dict:
    closures = {}
    for kind in ("runtime", "wheelhouse", "evaluator"):
        src = tmp / f"src-{kind}"
        src.mkdir()
        (src / "f.txt").write_text(kind)
        bundle_mod._tar_zst(src, tmp / f"{kind}.tar.zst", kind)
        closures[kind] = bundle_mod._cas_put(tmp / f"{kind}.tar.zst", {"kind": kind}, archive)
    return {**closures, "git_commit": "0" * 40, "lock_sha256": "1" * 64}


@pytest.fixture(scope="module")
def sealed(tmp_path_factory):
    import zkqec.orchestrator.campaign as campaign
    root = tmp_path_factory.mktemp("sealed")
    archive = root / "archive"
    closures = _fake_closures(archive, root)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bundle_mod, "ensure_closures", lambda *a, **k: closures)
        # the offline re-execution itself is covered by the slow round trip in test_bundle.py
        mp.setattr(campaign, "audit_bundle", lambda *a, **k: {"ok": True, "problems": []})
        out, run, broker = run_live_like(root, mp, cid="sealed", archive=archive)
    return out, run, Path(out["bundle"]["path"]), Path(out["confirmation_supplement"]["path"])


def test_the_bundle_seals_the_broker_evidence(sealed):
    out, run, bundle, _ = sealed
    data = zstandard.ZstdDecompressor().decompress(bundle.read_bytes(), max_output_size=1 << 30)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = set(tar.getnames())
        manifest = json.loads(tar.extractfile("bundle/broker/broker-manifest.json").read())
    for rel in ("broker-log.jsonl", "policy.json", "prices.json", "broker-manifest.json"):
        assert f"bundle/broker/{rel}" in names
    assert len([n for n in names if n.startswith("bundle/broker/grants/")]) == 2
    assert manifest == out["broker_manifest"]
    assert not (run / "broker-manifest.json").exists()          # nothing is written after sealing


def test_the_confirmation_supplement_links_settlement_and_totals_to_the_bundle(sealed):
    out, run, bundle, supp = sealed
    assert supp.parent == bundle.parent and supp.name == f"{bundle.name[:-8]}.confirmation.json"
    assert out["confirmation_supplement"]["problems"] == [] and check_confirmation(bundle, supp) == []
    doc = json.loads(supp.read_text())
    (ev,) = doc["events"]
    assert ev["kind"] == "job_settled" and ev["payload"]["kind"] == "confirm"
    assert ev["prev_hash"] == out["bundle"]["ledger_head"]["hash"]
    assert doc["final_totals"] == out_totals(run)


def out_totals(run: Path) -> dict:
    store = ArtifactStore(run / "store")
    with Admission(run / "ledger.db", store, build_registry(store)) as adm:
        return adm.job_book("sealed", profile="K0.P-min.G-open").totals()


SUPPLEMENT_TAMPER = {
    "totals": (lambda d: d["final_totals"]["usd"].update(spent=d["final_totals"]["usd"]["spent"] - 1),
               "final totals for usd"),
    "charge": (lambda d: d["events"][0]["payload"]["charged"].update(usd=5.0),
               "does not chain from the sealed head"),
    "dropped": (lambda d: d.update(events=[]), "not exactly the confirmation"),
    "other_bundle": (lambda d: d.update(bundle_sha256="0" * 64), "does not name this bundle"),
    "failed_audit": (lambda d: d["audit"].update(ok=False), "confirmation audit did not pass"),
}


@pytest.mark.parametrize("name", sorted(SUPPLEMENT_TAMPER))
def test_a_tampered_supplement_is_refused(sealed, tmp_path, name):
    out, run, bundle, supp = sealed
    doc = json.loads(supp.read_text())
    edit, expected = SUPPLEMENT_TAMPER[name]
    edit(doc)
    bad = tmp_path / supp.name
    bad.write_text(json.dumps(doc))
    problems = check_confirmation(bundle, bad)
    assert any(expected in p for p in problems), problems
