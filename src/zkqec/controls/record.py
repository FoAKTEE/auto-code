"""Binding of a control run to its campaign (contracts §I, §M).

The suite's inputs are taken from the frozen campaign only — the designer output schema artifact
named by the ``campaign_frozen`` manifest and the grammar families of the frozen profile — so the
campaign run and the offline audit derive them identically from the ledger and the store.
"""
from __future__ import annotations

from ..contracts import Profile
from .env import SuiteInputs


def campaign_inputs(adm, store, campaign_id: str) -> SuiteInputs:
    """The control inputs a campaign froze (output schema bytes, profile grammar families)."""
    from ..orchestrator.campaign import grammar_for            # the campaign's own derivation
    camp = adm.ledger.campaign(campaign_id)
    if camp is None:
        raise KeyError(f"campaign {campaign_id} is not frozen in this ledger")
    schema_sha = store.get_json(camp["manifest"]).get("output_schema")
    if not isinstance(schema_sha, str):
        raise KeyError(f"the frozen manifest of {campaign_id} names no output schema")
    profile = Profile.model_validate(camp["config"]["profile"])
    return SuiteInputs(store.get_bytes(schema_sha), tuple(grammar_for(profile)))
