"""§K: a call's reservation is the invocation total. Broker ceilings are derived from it per request
(total ÷ requests, floor), so every request the grant allows together stays within the reserved
units, and the USD reservation must cover the maximum charge of those totals under the declared price
table (configs/broker/prices.json). m1-live-2 reserved $0.60 for up to 400 kB of input; Codex M1
milestone review B8: m1-live-3 booked 40k/32k tokens while its grant allowed 2 requests of 40k/32k
each, and ``requests=0`` became permission for one model request."""
from __future__ import annotations

import json
import time

import pytest

from zkqec.agents import providers
from zkqec.agents.providers import ReservationBelowBound, call_ceilings, check_reservation, reservation_floor
from zkqec.broker import policy as P
from zkqec.contracts import Reservation
from zkqec.orchestrator.campaign import REPO, CampaignConfig
from zkqec.orchestrator.live import run_live

M1_LIVE_2 = Reservation(usd=0.6, requests=3, input_tokens=400_000, output_tokens=32_000,
                        wall_seconds=900, cpu_seconds=0)
LIVE_4 = Reservation(usd=1.92, requests=2, input_tokens=80_000, output_tokens=64_000,
                     wall_seconds=900, cpu_seconds=0)


def test_floor_is_the_maximum_charge_of_the_reserved_totals():
    # 400 kB input x $8/MTok (1h cache write) + 32k output x $20/MTok, however many requests
    assert reservation_floor("claude-opus-5-5", M1_LIVE_2) == pytest.approx(3.2 + 0.64)
    assert reservation_floor("claude-opus-5-5", LIVE_4) == pytest.approx(0.64 + 1.28)


def test_floor_uses_the_highest_tier_of_a_tiered_model():
    # claude-haiku-5-5 prompts over 100k tokens pay the higher tier ($1/MTok 1h write, $2.50 output)
    assert reservation_floor("claude-haiku-5-5", M1_LIVE_2) == pytest.approx(0.4 + 0.08)


def test_per_request_ceilings_divide_the_invocation_totals():
    c = call_ceilings(LIVE_4)
    assert (c.max_model_requests, c.max_input_bytes, c.max_output_tokens) == (2, 40_000, 32_000)
    c = call_ceilings(M1_LIVE_2)                          # floor division never exceeds the total
    assert (c.max_model_requests, c.max_input_bytes, c.max_output_tokens) == (3, 133_333, 10_666)
    assert 3 * c.max_input_bytes <= 400_000 and 3 * c.max_output_tokens <= 32_000


def test_zero_request_reservations_are_refused_for_live_calls():
    zero = LIVE_4.model_copy(update={"requests": 0})
    with pytest.raises(ReservationBelowBound, match="requests"):
        call_ceilings(zero)
    with pytest.raises(ReservationBelowBound, match="requests"):
        check_reservation("claude-opus-5-5", zero)


def _policy(tmp_path) -> P.CampaignPolicy:
    doc = json.loads((REPO / "configs" / "broker" / "policy.json").read_text())
    doc.pop("_comment", None)
    doc.update(campaign="t", log_path=str(tmp_path / "log.jsonl"), state_dir=str(tmp_path / "state"),
               dispatch_disabled_path=str(tmp_path / "OFF"), deadline_unix=None)
    return P.parse_policy(doc)


def test_retries_allowed_by_the_grant_stay_within_the_reserved_totals(tmp_path):
    c = call_ceilings(LIVE_4)
    grant = P.Grant("j", "claude", frozenset({"claude-opus-5-5"}), c.max_model_requests,
                    c.max_total_requests, c.max_input_bytes, c.max_output_tokens, time.time() + 60)
    pol, counters = _policy(tmp_path), P.Counters()
    path = "/v1/messages"

    def attempt(input_bytes: int, max_tokens: int) -> P.Decision:
        ins = P.Inspection(model="claude-opus-5-5", input_bytes=input_bytes, is_model_request=True,
                           max_output_tokens=max_tokens, output_field="present")
        if max_tokens > grant.max_output_tokens:
            ins.violations.append("output_ceiling")
        return P.decide_request(pol, grant, counters, "api.anthropic.com", "POST", path, ins)

    assert attempt(40_001, 32_000).reason == "input_bound"
    assert attempt(40_000, 32_001).reason == "output_ceiling"
    assert attempt(40_000, 32_000).allowed and attempt(40_000, 32_000).allowed   # call + one retry
    assert attempt(1, 1).reason == "model_request_ceiling"
    assert counters.model_requests * grant.max_output_tokens <= LIVE_4.output_tokens
    assert counters.input_bytes <= LIVE_4.input_tokens


class _Broker:
    ca_cert, port = "/nonexistent/ca.pem", 1

    def __init__(self) -> None:
        self.granted: list = []

    def grant(self, job, provider, models, ceilings):
        self.granted.append(ceilings)
        return "token"

    revoke = staticmethod(lambda token: None)
    blocked_reason = staticmethod(lambda job: None)
    records = staticmethod(lambda job: [])


class _Job:
    outcome, exit_code, wall_seconds, blocked_reason, profile_sha256 = "launch_failure", 1, 0.1, None, ""
    stdout, stderr = b"", b""


def test_provider_grants_per_request_ceilings_and_sets_the_cli_per_request(monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(providers, "run_job", lambda spec, *a, **k: launched.append(spec) or _Job())
    broker = _Broker()
    prov = providers.ClaudeCallProvider("claude-opus-5-5", broker=broker, slice_=None, jobs_root=tmp_path)
    prov.call(job_id="a" * 32, system_prompt="s", request="r", schema={}, reservation=LIVE_4)
    (ceil,) = broker.granted
    assert (ceil.max_model_requests, ceil.max_input_bytes, ceil.max_output_tokens) == (2, 40_000, 32_000)
    env = launched[0].env
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "32000" and env["CLAUDE_CODE_MAX_RETRIES"] == "1"
    with pytest.raises(ReservationBelowBound, match="requests"):
        prov.call(job_id="b" * 32, system_prompt="s", request="r", schema={},
                  reservation=LIVE_4.model_copy(update={"requests": 0}))
    assert len(broker.granted) == 1 and len(launched) == 1          # nothing granted or launched


def test_reservation_below_the_bound_or_unpriced_is_refused():
    with pytest.raises(ReservationBelowBound, match="below"):
        check_reservation("claude-opus-5-5", M1_LIVE_2)
    with pytest.raises(ReservationBelowBound, match="no declared price"):
        check_reservation("claude-unpriced", M1_LIVE_2.model_copy(update={"usd": 1e6}))
    assert check_reservation("claude-opus-5-5", LIVE_4) == pytest.approx(1.92)
    with pytest.raises(ReservationBelowBound, match="below"):
        check_reservation("claude-opus-5-5", LIVE_4.model_copy(update={"usd": 1.91}))


def test_the_live_config_reserves_its_invocation_totals():
    cfg = CampaignConfig.model_validate_json((REPO / "configs" / "campaigns" / "m1-live.json").read_text())
    assert cfg.campaign_id.startswith("m1-live-") and cfg.per_call == LIVE_4
    assert check_reservation(cfg.model, cfg.per_call) == pytest.approx(cfg.per_call.usd)


def test_run_live_refuses_before_any_side_effect(tmp_path, monkeypatch):
    import zkqec.orchestrator.live as live

    def side_effect(*a, **k):
        raise AssertionError("run_live went past its reservation check")
    for name in ("CampaignSlice", "Broker", "ClaudeCallProvider", "run_campaign"):
        monkeypatch.setattr(live, name, side_effect)      # never a live call, even on regression
    cfg = json.loads((REPO / "configs" / "campaigns" / "m1-live.json").read_text())
    for per_call in (M1_LIVE_2, LIVE_4.model_copy(update={"requests": 0})):
        cfg["per_call"] = per_call.model_dump()
        with pytest.raises(ReservationBelowBound):
            run_live(CampaignConfig.model_validate(cfg), tmp_path)
    assert list(tmp_path.iterdir()) == []                 # no ops dir, slice or broker was created
