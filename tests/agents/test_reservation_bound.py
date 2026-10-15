"""§K: a call's USD reservation must cover the maximum total charge under the ceilings derived from
it, priced from the declared table (configs/broker/prices.json). m1-live-2 reserved $0.60 per call
under ceilings that allow ≈ $11.5."""
from __future__ import annotations

import json

import pytest

from zkqec.agents.providers import ReservationBelowBound, call_ceilings, check_reservation, reservation_floor
from zkqec.contracts import Reservation
from zkqec.orchestrator.campaign import REPO, CampaignConfig
from zkqec.orchestrator.live import run_live

M1_LIVE_2 = Reservation(usd=0.6, requests=3, input_tokens=400_000, output_tokens=32_000,
                        wall_seconds=900, cpu_seconds=0)


def test_floor_is_the_maximum_charge_under_the_derived_ceilings():
    # 3 requests x (400 kB input x $8/MTok (1h cache write) + 32k output x $20/MTok)
    assert reservation_floor("claude-opus-5-5", M1_LIVE_2) == pytest.approx(3 * (3.2 + 0.64))
    r = M1_LIVE_2.model_copy(update={"requests": 2, "input_tokens": 40_000})
    assert reservation_floor("claude-opus-5-5", r) == pytest.approx(2 * (0.32 + 0.64))


def test_floor_uses_the_highest_tier_of_a_tiered_model():
    # claude-haiku-5-5 prompts over 100k tokens pay the higher tier ($1/MTok 1h write, $2.50 output)
    assert reservation_floor("claude-haiku-5-5", M1_LIVE_2) == pytest.approx(3 * (0.4 + 0.08))


def test_ceilings_and_floor_share_one_derivation():
    c = call_ceilings(M1_LIVE_2)
    assert (c.max_model_requests, c.max_input_bytes, c.max_output_tokens) == (3, 400_000, 32_000)
    assert call_ceilings(M1_LIVE_2.model_copy(update={"requests": 0})).max_model_requests == 1


def test_reservation_below_the_bound_or_unpriced_is_refused():
    with pytest.raises(ReservationBelowBound, match="below"):
        check_reservation("claude-opus-5-5", M1_LIVE_2)
    with pytest.raises(ReservationBelowBound, match="no declared price"):
        check_reservation("claude-unpriced", M1_LIVE_2.model_copy(update={"usd": 1e6}))
    ok = M1_LIVE_2.model_copy(update={"requests": 2, "input_tokens": 40_000, "usd": 1.92})
    assert check_reservation("claude-opus-5-5", ok) == pytest.approx(1.92)


def test_run_live_refuses_before_any_side_effect(tmp_path):
    cfg = json.loads((REPO / "configs" / "campaigns" / "m1-live.json").read_text())
    cfg["per_call"] = M1_LIVE_2.model_dump()
    with pytest.raises(ReservationBelowBound):
        run_live(CampaignConfig.model_validate(cfg), tmp_path)
    assert list(tmp_path.iterdir()) == []                 # no ops dir, slice or broker was created
