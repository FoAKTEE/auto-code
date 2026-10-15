"""Codex M1 re-review N2: usage is complete only with its mandatory counters (contracts §J5, §K).

A broker usage record is ``reported`` only with ``input_tokens`` and ``output_tokens``; the provider
never turns a missing mandatory counter into zero: a call with such a record, alone or followed by a
complete retry, is ``uncertain`` and keeps its full reservation. The records below are what a broker
log could hold (a usage record logged as reported without a counter); no process is launched."""
from __future__ import annotations

import json

import pytest

from zkqec.agents import providers
from zkqec.broker.launcher import summarize_job
from zkqec.contracts import Reservation
from zkqec.contracts.jobs import charge_for
from zkqec.orchestrator.campaign import outcome_state

RES = Reservation(usd=1.92, requests=2, input_tokens=80_000, output_tokens=64_000,
                  wall_seconds=900, cpu_seconds=0)
REQUEST = {"event": "request", "job_id": "j", "decision": "allow", "model_request": True,
           "model": "claude-opus-5-5"}


def response(usage):
    return {"event": "response", "job_id": "j", "usage": usage,
            "usage_status": "reported" if usage else "unknown"}


FULL = {"input_tokens": 900, "cache_creation_input_tokens": 100, "output_tokens": 2_000}


class _Job:
    outcome, exit_code, wall_seconds, blocked_reason, profile_sha256 = "completed", 0, 3.0, None, ""
    stdout = json.dumps({"structured_output": {"family": "explicit_sparse"},
                         "total_cost_usd": 0.0472, "is_error": False}).encode()
    stderr = b""


def call(monkeypatch, tmp_path, records):
    class Broker:
        ca_cert, port = "/nonexistent/ca.pem", 1
        grant = staticmethod(lambda *a, **k: "token")
        revoke = staticmethod(lambda token: None)
        blocked_reason = staticmethod(lambda job: None)

        @staticmethod
        def records(job):
            return records
    monkeypatch.setattr(providers, "run_job", lambda *a, **k: _Job())
    prov = providers.ClaudeCallProvider("claude-opus-5-5", broker=Broker(), slice_=None,
                                        jobs_root=tmp_path)
    return prov.call(job_id="a" * 32, system_prompt="s", request="r", schema={}, reservation=RES)


def test_a_complete_record_is_charged_its_counters_with_absent_cache_counters_as_zero(monkeypatch,
                                                                                      tmp_path):
    res = call(monkeypatch, tmp_path, [REQUEST, response(FULL)])
    assert res.outcome == "completed" and res.record["telemetry_complete"] is True
    assert (res.usage["input_tokens"], res.usage["output_tokens"]) == (1000.0, 2000.0)
    res = call(monkeypatch, tmp_path, [REQUEST, response({"input_tokens": 5, "output_tokens": 7})])
    assert res.outcome == "completed" and res.usage["input_tokens"] == 5.0


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("counter", ["input_tokens", "output_tokens"])
def test_a_record_without_a_mandatory_counter_is_missing_usage(monkeypatch, tmp_path, counter,
                                                               retry):
    partial = {k: v for k, v in FULL.items() if k != counter}
    records = [REQUEST, response(partial)] + ([REQUEST, response(FULL)] if retry else [])
    s = summarize_job(records)
    assert s["usage_records"] == int(retry) and s["usage_missing"] == 1
    res = call(monkeypatch, tmp_path, records)
    assert res.outcome == "uncertain" and res.record["telemetry_complete"] is False
    assert "input_tokens" not in res.usage and "output_tokens" not in res.usage
    reservation = RES.model_dump()
    assert charge_for(outcome_state(res.outcome), reservation, res.usage) == \
        {u: float(v) for u, v in reservation.items()}
