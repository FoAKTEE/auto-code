"""Verdict-free digests of earlier attempts (prompt §6: facts only, bounded, rendered from the DB).

A digest lists, per earlier attempt of the same task, the typed outcome and the verifier-established
facts (n, k, check weight, qubit degree, certified distance intervals, CSS validity, operation-check
status). It never contains prose judgements, rankings, hidden task identifiers, known-code labels or
audit results (§N: no audit feedback before sealing).
"""
from __future__ import annotations

import json
from typing import Any

from ..pipeline import Evaluation

MAX_ITEMS = 12
MAX_BYTES = 6000
_FACT_KEYS = ("n", "k", "max_check_weight", "max_qubit_degree", "css_valid", "d_X", "d_Z", "d_both")


def attempt_facts(ev: Evaluation) -> dict[str, Any]:
    out: dict[str, Any] = {"outcome": ev.outcome}
    if ev.outcome != "evaluated":
        reason = str(ev.detail.get("reason", ""))[:200]
        out["reason"] = reason
        return out
    out.update({k: ev.facts.get(k) for k in _FACT_KEYS if k in ev.facts})
    if "operation" in ev.detail:
        out["operation_note"] = str(ev.detail["operation"])[:200]
    out["requirements_violated_by_verified_bounds"] = sorted(
        k for k, v in ev.refuted.items() if v == "verified")
    out["task_satisfied"] = ev.satisfies == "verified"
    return out


def render_digest(history: list[Evaluation]) -> str:
    """Bounded JSON digest of the most recent attempts (oldest first)."""
    items = [attempt_facts(e) for e in history[-MAX_ITEMS:]]
    while True:
        text = json.dumps({"earlier_attempts": items}, sort_keys=True)
        if len(text.encode()) <= MAX_BYTES or not items:
            return text
        items = items[1:]
