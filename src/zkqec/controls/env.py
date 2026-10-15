"""Scratch evaluators for the §I control suite (contracts §I; §L, §N).

Every control case runs in its own empty directory with its own artifact store and ledger, built as a
campaign builds them: :func:`~zkqec.pipeline.build_registry`, :func:`~zkqec.pipeline.evaluator_domain`,
a frozen scratch campaign (``campaign_frozen``), its ledger-bound job book (every settlement is a
``job_settled`` event) and a :class:`~zkqec.pipeline.Pipeline` restricted to the campaign's grammar
families. Designer outputs enter as text through the production intake
(:func:`~zkqec.grammar.intake.parse_proposal`) with the campaign's frozen output schema, exactly as
``run_campaign`` admits them. No case sees another case's evidence.

A control whose injection is a *faulty verifier* (conflicting or non-authorizing evidence) adds one
issuer, ``control.fault_injection``, to its own scratch registry. It returns the results named by its
input artifact, built with ``VerifierResult.model_construct`` (no validation), so admission's result
re-validation, the conflict quarantine and the resolver decide what carries authority. A campaign
registry never contains it.

Observations are deterministic and path-free: statuses, outcome labels, incident categories, counts
and certified intervals — never ids drawn at random (jobs, incidents), times or file paths.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..admission import Admission
from ..admission.registry import ArtifactInput
from ..contracts import (EXACT, DesignCandidate, Proposition, TaskSA, TaskSF, VerifierResult,
                         content_hash)
from ..grammar import GrammarCaps, from_dense
from ..grammar.intake import parse_proposal
from ..pipeline import Evaluation, Pipeline, build_registry, evaluator_domain
from ..store import ArtifactStore

CAMPAIGN = "controls"
CAPS = {"cpu_seconds": 1e6, "wall_seconds": 1e6}
INJECTOR, INJECTOR_VERSION = "control.fault_injection", "1.0.0"
INJECTED_METHOD = "control_fault_injection_v1"


@dataclass(frozen=True)
class SuiteInputs:
    """What a control run takes from its campaign: the frozen designer output schema (the artifact
    bytes sent to providers) and the frozen profile's grammar families."""
    schema_bytes: bytes
    families: tuple[str, ...]

    @property
    def schema(self) -> dict:
        return json.loads(self.schema_bytes)

    @property
    def schema_sha(self) -> str:
        return hashlib.sha256(self.schema_bytes).hexdigest()


def _inject(results: ArtifactInput) -> list[VerifierResult]:
    """The fault-injection issuer: exactly the listed results, unvalidated (a faulty verifier)."""
    return [VerifierResult.model_construct(
        proposition=Proposition.model_validate(r["proposition"]), status=r["status"],
        evidence_kind=r["evidence_kind"], guarantee=EXACT, method=INJECTED_METHOD,
        completion=r["completion"], witnesses=(), artifacts=(), detail={})
        for r in results.json()]


def claim(predicate: str, subject_type: str, subject: str, *, status: str = "verified",
          evidence_kind: str = "exact_algebra", **args) -> dict[str, Any]:
    """One result for the fault-injection issuer."""
    return {"proposition": {"predicate": predicate, "subject_type": subject_type,
                            "subject": subject, "args": args},
            "status": status, "evidence_kind": evidence_kind, "completion": "completed"}


class Env:
    """One scratch evaluator (see module doc)."""

    def __init__(self, root: Path, inputs: SuiteInputs, tasks: Sequence[TaskSA | TaskSF], *,
                 injector: bool = False, distance_budget: dict | None = None):
        root.mkdir(parents=True, exist_ok=False)
        self.root, self.inputs = root, inputs
        self.store = ArtifactStore(root / "store")
        self.registry = build_registry(self.store)
        if injector:
            self.registry.register(INJECTOR, INJECTOR_VERSION, _inject)
        self.adm = Admission(root / "ledger.db", self.store, self.registry)
        try:
            self.domain = evaluator_domain(self.registry)
            frozen = [t.model_dump(mode="json") for t in tasks]
            self.adm.freeze_campaign(CAMPAIGN, namespace=CAMPAIGN, config={"tasks": frozen},
                                     tasks=frozen, caps=dict(CAPS), labels=["fixture"],
                                     domain=self.domain,
                                     manifest=self.store.put_json({"kind": "control_scratch"}))
            self.book = self.adm.job_book(CAMPAIGN)
            self.book.set_caps(CAPS)
            self.pipe = self.pipeline(distance_budget=distance_budget)
        except BaseException:
            self.adm.close()
            raise

    def pipeline(self, *, domain: str | None = None, distance_budget: dict | None = None,
                 guard=None) -> Pipeline:
        return Pipeline(self.adm, self.store, self.book, domain or self.domain, caps=GrammarCaps(),
                        distance_budget=distance_budget, families=self.inputs.families,
                        guard=guard)

    def close(self) -> None:
        self.adm.close()

    def __enter__(self) -> "Env":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- production entry points -------------------------------------------------------------------
    def parse(self, obj: dict):
        """Designer output text → Proposal through the production intake (or MalformedProposal)."""
        return parse_proposal(json.dumps(obj, sort_keys=True), schema=self.inputs.schema,
                              families=self.inputs.families)

    def evaluate(self, obj: dict, task: TaskSA | TaskSF, *, pipe: Pipeline | None = None
                 ) -> Evaluation:
        return (pipe or self.pipe).evaluate(self.parse(obj), task)

    def put_presentation(self, hx, hz) -> str:
        return self.store.put_json(from_dense(hx, hz).model_dump(mode="json"))

    def inject(self, results: list[dict], key: str):
        """Admit results from the fault-injection issuer through the real admission path."""
        return self.adm.run_verifier(INJECTOR, {"results": self.store.put_json(results)},
                                     self.domain, idempotency_key=key)

    # -- observation -------------------------------------------------------------------------------
    def resolver(self, domain: str | None = None):
        return self.adm.resolver(domain or self.domain)

    def incidents(self) -> list[str]:
        return [e.payload["category"] for e in self.adm.ledger.events()
                if e.kind == "incident_opened"]

    def count(self, table: str) -> int:
        return self.adm.ledger.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def verdicts(self, candidate: str | DesignCandidate, task: TaskSA | TaskSF, *,
                 domain: str | None = None) -> dict[str, Any]:
        """Resolver verdicts for a candidate: ``satisfies_task``, the requirements whose
        ``target_refuted`` is verified, and (S-F) each operation's ``implements`` status, with a
        verifier refusal (malformed input) reported as ``rejected``."""
        res = self.resolver(domain)
        cand = candidate if isinstance(candidate, DesignCandidate) else \
            DesignCandidate.model_validate(self.store.get_json(candidate))
        out: dict[str, Any] = {
            "satisfies_task": res.satisfies_task(cand, task).status,
            "target_refuted": sorted(n for n in res.requirement_names(task)
                                     if res.target_refuted(cand, task, n).status == "verified")}
        if cand.G:
            found = [self._implements(res, op, domain) for op in cand.G]
            out["implements"] = [status for status, _ in found]
            if any(reason for _, reason in found):
                out["implements_refusals"] = [reason for _, reason in found]
        return out

    def _implements(self, res, op, domain) -> tuple[str, str | None]:
        prop = Proposition(predicate="implements", subject_type="operation",
                           subject=content_hash(op),
                           args={"target_action": op.target_action, "basis": op.basis,
                                 "phase_convention": op.phase_convention})
        status = res.status(prop).status
        if status != "unknown":
            return status, None
        for rec in self.adm.ledger.receipt_records(domain or self.domain):
            if rec.receipt.proposition.key == prop.key and \
                    rec.receipt.detail.get("outcome") == "malformed_proposal":
                return "rejected", str(rec.receipt.detail.get("reason", ""))[:200]
        return "unknown", None


def label(v: dict[str, Any]) -> str:
    """Outcome label of resolver verdicts."""
    if v["satisfies_task"] == "verified":
        return "verified"
    if v["target_refuted"] or "refuted" in v.get("implements", ()):
        return "refuted"
    if "rejected" in v.get("implements", ()):
        return "rejected"
    return "unknown"


def observed(authorized: bool, outcome: str, **facts: Any) -> dict[str, Any]:
    return {"authorized": bool(authorized), "outcome": outcome, "facts": facts}


def judge(env: Env, ev: Evaluation, task: TaskSA | TaskSF, **facts: Any) -> dict[str, Any]:
    """Observation of an evaluated proposal: authorized iff ``satisfies_task`` is verified."""
    v = env.verdicts(ev.candidate, task)
    if "d_both" in ev.facts:
        facts["d_both"] = ev.facts["d_both"]
    return observed(v["satisfies_task"] == "verified", label(v), **v, **facts)
