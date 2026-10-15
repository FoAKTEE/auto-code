"""Strict proposal intake for untrusted agent output (prompt §8 L0; appendix §B3, §N).

Order, before any construction: L0 raw caps → strict JSON parse (no duplicate keys, no NaN/inf) →
complete output-schema validation (the role's declared schema: unknown fields, wrong types and
non-integer indices are rejected, never coerced) → verifier-owned field names rejected at any depth
(§B3 class d) → the frozen profile's grammar families (anything else is ``rejected: policy``) → the
family's parameter schema → the :class:`Proposal` contract. Every failure is a
:class:`MalformedProposal`.
"""
from __future__ import annotations

from typing import Any, Iterable

import jsonschema

from ..contracts import VERIFIER_OWNED_FIELDS, Proposal
from .errors import MalformedProposal
from .rawjson import parse_untrusted_json

POLICY = "rejected: policy"
L0 = {"max_bytes": 2_000_000, "max_depth": 12, "max_list_len": 200_000}


def _first_error(validator: jsonschema.protocols.Validator, obj: Any) -> str | None:
    err = jsonschema.exceptions.best_match(validator.iter_errors(obj))
    if err is None:
        return None
    where = "/".join(str(x) for x in err.absolute_path) or "<root>"
    return f"{where}: {err.message[:200]}"


def owned_keys(obj: Any) -> set[str]:
    """Verifier-owned field names used as object keys anywhere in ``obj`` (iterative walk)."""
    out: set[str] = set()
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            out |= VERIFIER_OWNED_FIELDS & {k for k in cur if isinstance(k, str)}
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return out


def parse_proposal(text: str | bytes, *, schema: dict, families: Iterable[str],
                   manifests: dict[str, dict] | None = None) -> Proposal:
    """Untrusted text → :class:`Proposal`, or :class:`MalformedProposal`."""
    obj = parse_untrusted_json(text, **L0)
    problem = _first_error(jsonschema.Draft202012Validator(schema), obj)
    if problem:
        raise MalformedProposal(f"schema: {problem}", field="schema")
    owned = owned_keys({k: obj.get(k) for k in ("params", "witnesses")})
    if owned:
        raise MalformedProposal(f"verifier-owned fields supplied by proposer: {sorted(owned)}",
                                field="proposal")
    if obj.get("family") not in set(families):
        raise MalformedProposal(POLICY, field="family")
    if manifests is None:
        from . import MANIFESTS as manifests
    param_schema = (manifests.get(obj["family"]) or {}).get("param_schema")
    if param_schema is not None:
        problem = _first_error(jsonschema.Draft202012Validator(param_schema), obj["params"])
        if problem:
            raise MalformedProposal(f"params: {problem}", field="params")
    try:
        return Proposal.model_validate(obj)
    except ValueError as exc:
        raise MalformedProposal(str(exc).splitlines()[-1][:300] if str(exc) else "invalid",
                                field="proposal") from None
