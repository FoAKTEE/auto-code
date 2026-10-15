"""Binding of a control run to its campaign (contracts §I, §M).

The suite's inputs are taken from the frozen campaign only — the designer output schema artifact
named by the ``campaign_frozen`` manifest and the grammar families of the frozen profile — so the
campaign run and the offline audit derive them identically from the ledger and the store.

:func:`audit_controls` is the offline reproduction: every ``controls_recorded`` event of the campaign
is re-run with the evaluator being audited (the bundle's embedded closure) in a scratch directory;
the canonical evidence must hash to the recorded evidence artifact (byte-identical) and the recorded
rows must equal the re-run rows. Anything else — an unknown control set, missing inputs, a re-run
that raises — is an audit problem, never a pass. Reproduction is not acceptance (Codex M1 milestone
review B3): the control set the frozen config requires must be recorded, and the reproduced evidence
must pass (:func:`~zkqec.controls.p1a.verdict`: k = 0, j = 0, no outcome mismatch, complete).
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from ..contracts import Profile, canonical_json
from .env import SuiteInputs
from .p1a import CONTROL_SETS, run_suite, verdict


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


def audit_controls(adm, store, campaign_id: str, scratch: Path) -> tuple[list[str], int]:
    """Re-run every recorded control summary of the campaign; return (problems, reproduced)."""
    records = adm.ledger.controls(campaign_id)
    problems: list[str] = []
    reproduced = 0
    camp = adm.ledger.campaign(campaign_id) or {}
    required = (camp.get("config") or {}).get("controls")
    if required and not any(r["control_set"] == required for r in records):
        problems.append(f"required control set {required} of the frozen config was not recorded "
                        "(the suite failed or never ran)")
    if not records:
        return problems, reproduced
    Path(scratch).mkdir(parents=True, exist_ok=True)
    reruns: dict[str, dict] = {}            # deterministic: one re-run per control set suffices
    for i, rec in enumerate(records):
        tag = f"control set {rec['control_set']} (record {i})"
        if rec["control_set"] not in CONTROL_SETS:
            problems.append(f"{tag}: unknown control set; it cannot be reproduced")
            continue
        try:
            if rec["control_set"] not in reruns:
                reruns[rec["control_set"]] = run_suite(
                    rec["control_set"], Path(tempfile.mkdtemp(prefix="rerun-", dir=scratch)),
                    campaign_inputs(adm, store, campaign_id))
        except Exception as exc:                                   # noqa: BLE001
            problems.append(f"{tag}: offline re-run failed: {type(exc).__name__}: "
                            f"{str(exc)[:200]}")
            continue
        evidence, found = reruns[rec["control_set"]], []
        sha = hashlib.sha256(canonical_json(evidence)).hexdigest()
        if sha != rec["evidence"]:
            found.append(f"{tag}: recorded evidence {rec['evidence']} differs from the offline "
                         f"re-run {sha}")
        if rec["results"] != evidence["results"]:
            found.append(f"{tag}: recorded counts differ from the offline re-run")
        problems += found
        reproduced += not found
    for name, evidence in sorted(reruns.items()):
        v = verdict(evidence)
        if not v["passed"]:
            problems.append(f"control set {name} did not pass: {v['false_authorizations']} false "
                            f"authorizations, {v['false_rejections']} false rejections, "
                            f"{v['outcome_mismatches']} outcome mismatches"
                            + ("" if v["complete"] else ", incomplete"))
    return problems, reproduced
