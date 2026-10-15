"""M1 evaluation pipeline: registered verifier adapters + candidate evaluation through admission.

Flow for one proposal (prompt §7 step 3, appendix §B, §C, §F):
  L0/grammar build → store presentation → L1 (css_algebra) → identity (code_sha) →
  L2 (distance at the half-weight the task needs: certify d ≥ d_min ⇔ exclusion through d_min−1,
  so t = ⌈(d_min−1)/2⌉) → [S-F] L3 (logical_action) → DesignCandidate → resolver verdicts.

Every verifier and identity run is a job in the :class:`JobBook` with a CPU and a wall-clock
reservation, and runs in a bounded child (:func:`zkqec.exec.run_bounded`) whose ``RLIMIT_CPU`` and
wall kill come from that reservation and whose ``RLIMIT_AS`` headroom is the distance budget's
``max_memory_bytes`` (or :data:`DEFAULT_MEMORY_BYTES`) plus :data:`MEMORY_SLACK_BYTES`. Exceeding
any limit closes the execution as ``crashed`` with no receipt; L2 work is additionally bounded by
its deterministic budgets (patterns, pairs, memory). The measured CPU (the child's, from ``wait4``,
plus this process's own) and wall time are charged. Settlement commits inside the admission's
closing transaction (fenced by the lease generation). Recovery (§L): a settled job is only ever
*read* (its execution's recorded closure); a settled job whose execution never closed gets its old
execution closed as abandoned and a **new** reserved retry job (``retry_of``, its own execution key
and reservation, fenced settlement). Namespacing (§N): every execution carries the job book's
namespace (campaign/arm/split/profile) in its identity and record, and every evidence lookup uses
the namespace's resolver, so another namespace's closure is never reused and its receipts never
count here; within a namespace, settled work is read, not re-run. Agents never call this module;
the orchestrator does.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .admission import Admission, VerifierRegistry
from .admission.registry import ArtifactInput
from .contracts import (DesignCandidate, LogicalBasis, OperationRef, PhysicalOperation, Presentation,
                        Proposal, Reservation, SignedPauliImages, TaskSA, TaskSF, content_hash)
from .contracts.registry import REGISTRY_VERSION
from .exec import Limits
from .grammar import GrammarCaps, MalformedProposal, build
from .grammar.errors import UnsupportedFamily
from .grammar.intake import POLICY
from .identity import code_sha
from .orchestrator.jobs import JobBook, JobError
from .store import ArtifactStore
from .verify import DistanceBudget, analyze, certify_distance, implements
from .verify.logical_action import MalformedInput, UnsupportedInput

KERNEL_VERSION = "kernel-0.1.0+1"
PIPELINE_VERSION = "1.0.0"
MAX_ATTEMPTS = 3
# §K memory ceiling of one bounded verifier/identity child: the address space it may add beyond what
# it inherits. The declared default is the L2 default budget; the slack covers interpreter, allocator
# and result-pickling overhead that the L2 preflight estimate does not model.
DEFAULT_MEMORY_BYTES = DistanceBudget().max_memory_bytes
MEMORY_SLACK_BYTES = 256 * 2**20


class RetriesExhausted(JobError):
    """A verifier run could not be completed within MAX_ATTEMPTS reserved attempts."""


def _dense(p: Presentation) -> tuple[np.ndarray, np.ndarray]:
    out = []
    for m in (p.hx, p.hz):
        a = np.zeros(m.shape, dtype=np.uint8)
        for i, j in m.entries:
            a[i, j] = 1
        out.append(a)
    return out[0], out[1]


def build_registry(store: ArtifactStore) -> VerifierRegistry:
    """The frozen evaluator: thin adapters from hash-verified artifact bytes to zkqec.verify."""
    sink = store.put_json
    reg = VerifierRegistry()

    def l1(presentation: ArtifactInput):
        return analyze(Presentation.model_validate(presentation.json()), artifact_sink=sink)

    def l2(presentation: ArtifactInput, params: ArtifactInput):
        pres, cfg = Presentation.model_validate(presentation.json()), params.json()
        hx, hz = _dense(pres)
        budget = DistanceBudget(**cfg.get("budget", {}))
        return certify_distance(hx, hz, int(cfg["half_weight"]), artifact_sink=sink, budget=budget)

    def l3(presentation: ArtifactInput, basis: ArtifactInput, operation: ArtifactInput,
           target: ArtifactInput, spec: ArtifactInput):
        return implements(Presentation.model_validate(presentation.json()),
                          LogicalBasis.model_validate(basis.json()),
                          PhysicalOperation.model_validate(operation.json()),
                          SignedPauliImages.model_validate(target.json()),
                          tuple(spec.json()["logical_qubits"]), artifact_sink=sink)

    def ident(presentation: ArtifactInput):
        hx, hz = _dense(Presentation.model_validate(presentation.json()))
        return code_sha(hx, hz)

    reg.register("l1.css_algebra", "1.0.0", l1)
    reg.register("l2.distance", "1.1.0", l2)
    reg.register("l3.logical_action", "1.0.0", l3)
    reg.register_identity("identity.code_sha", "1.0.0", ident)
    return reg


def evaluator_domain(reg: VerifierRegistry) -> str:
    """Domain hash: the evaluator identity (verifier versions, kernel, predicate registry)."""
    return content_hash({"registry": reg.manifest(), "kernel": KERNEL_VERSION,
                         "predicates": REGISTRY_VERSION, "pipeline": PIPELINE_VERSION})


def half_weight_for(d_min: int) -> int:
    return max(0, math.ceil((d_min - 1) / 2))


@dataclass
class Evaluation:
    """Facts about one evaluated proposal (rendered into verdict-free digests)."""
    outcome: str                          # malformed_proposal | unsupported | evaluated
    detail: dict[str, Any] = field(default_factory=dict)
    presentation: str | None = None
    code: str | None = None
    candidate: str | None = None
    satisfies: str = "unknown"            # verified | unknown (derived predicate)
    refuted: dict[str, str] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)


def _int_list(v: Any, field: str) -> tuple[int, ...]:
    """A list of genuine ints (no bool, float or str coercion)."""
    if not isinstance(v, (list, tuple)) or any(type(x) is not int for x in v):
        raise ValueError(f"{field} must be a list of integers")
    return tuple(v)


def _strict_witnesses(w: Any) -> dict[str, Any]:
    """S-F witnesses (§B3 class c), checked strictly: known keys only, integer entries only."""
    if not isinstance(w, dict) or set(w) - {"basis", "operation", "logical_qubits"}:
        raise ValueError(f"unknown witness fields {sorted(set(w) - {'basis', 'operation', 'logical_qubits'})}"
                         if isinstance(w, dict) else "witnesses must be an object")
    out: dict[str, Any] = {}
    if "basis" in w:
        b = w["basis"]
        if not isinstance(b, dict) or set(b) != {"lx", "lz"}:
            raise ValueError("basis needs exactly lx and lz")
        out["basis"] = {k: tuple(_int_list(row, f"basis.{k}") for row in
                                 (b[k] if isinstance(b[k], (list, tuple)) else [None]))
                        for k in ("lx", "lz")}
    if "operation" in w:
        o = w["operation"]
        if not isinstance(o, dict) or set(o) - {"permutation", "local_cliffords"}:
            raise ValueError("operation allows only permutation and local_cliffords")
        out["operation"] = dict(o)                       # None means absent
        if o.get("permutation") is not None:
            out["operation"]["permutation"] = _int_list(o["permutation"], "operation.permutation")
        lc = o.get("local_cliffords")
        if lc is not None and (not isinstance(lc, (list, tuple))
                               or any(type(g) is not str for g in lc)):
            raise ValueError("operation.local_cliffords must be a list of strings")
    if "logical_qubits" in w:
        out["logical_qubits"] = _int_list(w["logical_qubits"], "logical_qubits")
    return out


class Pipeline:
    def __init__(self, admission: Admission, store: ArtifactStore, book: JobBook, domain: str, *,
                 caps: GrammarCaps, distance_budget: dict | None = None, cpu_reservation: int = 600,
                 wall_reservation: int = 600, families: tuple[str, ...] | None = None,
                 guard=None, memory_bytes: int | None = None):
        self.adm, self.store, self.book, self.domain = admission, store, book, domain
        self.namespace = book.namespace           # §N: executions and evidence are scoped to it
        self.memory_bytes = memory_bytes          # None: from the distance budget (see limits())
        self.guard = guard            # frozen-evaluator check before every verifier run (§4.3)
        self.caps, self.distance_budget = caps, distance_budget or {}
        self.cpu_reservation, self.wall_reservation = cpu_reservation, wall_reservation
        self.families = None if families is None else frozenset(families)   # frozen profile

    # -- one verifier run = one settled job -------------------------------------------------------
    def _run(self, name: str, inputs: dict[str, str], key: str, *, identity: bool = False):
        res = Reservation(usd=0, requests=0, input_tokens=0, output_tokens=0,
                          wall_seconds=self.wall_reservation, cpu_seconds=self.cpu_reservation)
        prev = None
        for attempt in range(MAX_ATTEMPTS):
            ekey = key if attempt == 0 else f"{key}#retry{attempt}"
            job = self.book.find(f"{name}:{ekey}") or self.book.reserve(
                "verify", f"{name}:{ekey}", res, input_sha=content_hash(inputs), retry_of=prev)
            state = self.book.state(job)
            if state == "dispatched":
                raise JobError(f"{job} holds a live lease; run recover() before resuming")
            if state == "reserved":
                done = self.adm.closed_report(name, inputs, self.domain, ekey,
                                              namespace=self.namespace)
                if done is not None:                     # closed by this namespace's earlier job
                    self.book.cancel(job)
                    return done
                return self._execute(job, name, inputs, ekey, identity)
            if state != "settled":                       # completed/failed/uncertain: charge it
                self.book.settle(job)
            done = self.adm.closed_report(name, inputs, self.domain, ekey, namespace=self.namespace)
            if done is not None and done.detail.get("error_type") != "abandoned":
                return done                              # settled work is only ever read
            if done is None:
                eid = self.adm.execution_id_for(name, inputs, self.domain, ekey,
                                                namespace=self.namespace)
                if self.adm.ledger.execution(eid) is not None:
                    self.adm.abandon(eid, f"job {job} settled without a closure; retried")
            prev = job
        raise RetriesExhausted(f"{name}:{key}: no completed closure after {MAX_ATTEMPTS} attempts")

    def limits(self) -> Limits:
        """The bounded child's ceilings: CPU and wall from the job reservation; memory from the
        distance budget's ``max_memory_bytes`` (or the declared default) plus the slack."""
        memory = self.memory_bytes
        if memory is None:
            memory = int(self.distance_budget.get("max_memory_bytes", DEFAULT_MEMORY_BYTES)) \
                + MEMORY_SLACK_BYTES
        return Limits(cpu_seconds=self.cpu_reservation, wall_seconds=self.wall_reservation,
                      memory_bytes=int(memory))

    def _execute(self, job: str, name: str, inputs: dict[str, str], ekey: str, identity: bool):
        if self.guard is not None:
            self.guard(f"verifier {name}")          # typed abort before dispatch on any change
        limits = self.limits()
        gen = self.book.dispatch(job)
        t0, w0 = time.process_time(), time.monotonic()
        child: dict = {"cpu_seconds": 0.0, "outcome": None}

        def measure(res) -> None:
            # BoundedResult.usage() omits CPU it could not measure (processes killed before the
            # child reaped them): that unit is then charged at the reservation (Codex M1
            # re-review N1).
            child.update(cpu_seconds=res.usage().get("cpu_seconds"), outcome=res.outcome)

        def settle(outcome: str) -> None:
            usage = {"wall_seconds": max(0.0, time.monotonic() - w0)}
            if child["cpu_seconds"] is not None:
                usage["cpu_seconds"] = max(0.0, time.process_time() - t0) + child["cpu_seconds"]
            if child["outcome"] == "escaped":         # group members outlived the kill: the job
                state = "uncertain"                   # keeps its full reservation (§L)
            else:
                state = "completed" if outcome == "admitted" else "failed"
            self.book.complete(job, gen, state, actual=usage)
            self.book.settle(job)
        if identity:
            return self.adm.run_identity(name, inputs["presentation"], self.domain,
                                         idempotency_key=ekey, on_close=settle, limits=limits,
                                         on_measure=measure, namespace=self.namespace)
        return self.adm.run_verifier(name, inputs, self.domain, idempotency_key=ekey,
                                     on_close=settle, limits=limits, on_measure=measure,
                                     namespace=self.namespace)

    def resolver(self):
        """Current authority from this namespace's executions only (§N)."""
        return self.adm.resolver(self.domain, namespace=self.namespace)

    # -- evaluation --------------------------------------------------------------------------------
    def evaluate(self, proposal: Proposal, task: TaskSA | TaskSF) -> Evaluation:
        if self.families is not None and proposal.family not in self.families:
            return Evaluation("malformed_proposal", {"reason": POLICY})     # §N: policy only
        try:
            pres, _, _ = build(proposal, self.caps)
        except UnsupportedFamily as e:
            return Evaluation("unsupported", {"reason": str(e)})
        except MalformedProposal as e:
            return Evaluation("malformed_proposal", {"reason": str(e)})
        p_sha = self.store.put_json(pres.model_dump(mode="json"))
        ev = Evaluation("evaluated", presentation=p_sha)
        self._run("l1.css_algebra", {"presentation": p_sha}, f"l1:{p_sha}")
        res = self.resolver()
        facts = self._presentation_facts(res, p_sha)
        if facts.get("css_valid") != "verified":
            ev.facts = facts
            return self._verdicts(ev, task, DesignCandidate(C=p_sha))
        self._run("identity.code_sha", {"presentation": p_sha}, f"id:{p_sha}", identity=True)
        res = self.resolver()
        ev.code = (res.code_of(p_sha) or (None, None))[0]
        facts = self._presentation_facts(res, p_sha)
        t = task.targets
        cheap_fail = (facts["n"] > t.n_max or (facts["k"] is not None and facts["k"] < t.k_min)
                      or (t.w_max is not None and facts["max_check_weight"] > t.w_max)
                      or (t.deg_max is not None and facts["max_qubit_degree"] > t.deg_max))
        if not cheap_fail and (facts["k"] or 0) >= 1:
            params = self.store.put_json({"half_weight": half_weight_for(t.d_min),
                                          "budget": self.distance_budget})
            self._run("l2.distance", {"presentation": p_sha, "params": params},
                      f"l2:{p_sha}:{params}")
        candidate = DesignCandidate(C=p_sha)
        if isinstance(task, TaskSF) and not cheap_fail:
            candidate = self._evaluate_operation(proposal, task, p_sha, facts, candidate, ev)
        ev.facts = {**facts, **self._distance_facts(self.resolver(), ev.code)}
        return self._verdicts(ev, task, candidate)

    def _evaluate_operation(self, proposal, task: TaskSF, p_sha, facts, candidate, ev):
        try:
            w = _strict_witnesses(proposal.witnesses or {})
            if "basis" in w:
                basis = LogicalBasis(presentation=p_sha, lx=w["basis"]["lx"], lz=w["basis"]["lz"])
                b_sha = self.store.put_json(basis.model_dump(mode="json"))
            else:
                b_sha = facts.get("canonical_basis")
                if b_sha is None:
                    ev.detail["operation"] = "no basis available"
                    return candidate
            op = PhysicalOperation(n=facts["n"], **(w.get("operation") or {}))
            lq = w.get("logical_qubits", tuple(range(task.k_req)))
        except (ValueError, TypeError, KeyError) as e:
            ev.detail["operation"] = f"malformed witness: {e}"
            return candidate
        if not op.operation_classes <= set(task.hardware.allowed_operations):
            ev.detail["operation"] = f"operation class {sorted(op.operation_classes)} not allowed"
            return candidate
        o_sha = self.store.put_json(op.model_dump(mode="json"))
        tgt_sha = self.store.put_json(task.target_action.model_dump(mode="json"))
        spec = self.store.put_json({"logical_qubits": list(lq)})
        self._run("l3.logical_action", {"presentation": p_sha, "basis": b_sha, "operation": o_sha,
                                         "target": tgt_sha, "spec": spec}, f"l3:{o_sha}:{b_sha}:{tgt_sha}:{spec}")
        ref = OperationRef(target_action=tgt_sha, circuit=o_sha, basis=b_sha, logical_qubits=lq)
        return DesignCandidate(C=p_sha, L=b_sha, G=(ref,))

    # -- facts & verdicts -----------------------------------------------------------------------
    def _presentation_facts(self, res, p_sha) -> dict[str, Any]:
        """Verifier-established facts about a presentation and its code (current receipts only)."""
        facts: dict[str, Any] = {"n": None, "k": None, "max_check_weight": None,
                                 "max_qubit_degree": None, "css_valid": "unknown",
                                 "canonical_basis": None}
        code = (res.code_of(p_sha) or (None, None))[0]
        for r in res.current().values():
            pr = r.proposition
            if pr.subject == p_sha:
                if pr.predicate == "css_valid":
                    facts["css_valid"] = r.status
                elif r.status != "verified":
                    continue
                elif pr.predicate == "block_length_eq":
                    facts["n"] = pr.args["n"]
                elif pr.predicate == "max_check_weight_eq":
                    facts["max_check_weight"] = pr.args["w"]
                elif pr.predicate == "max_qubit_degree_eq":
                    facts["max_qubit_degree"] = pr.args["q"]
            elif (pr.predicate == "dimension_eq" and r.status == "verified" and code is not None
                  and pr.subject == code):
                facts["k"] = pr.args["k"]          # code-level: transports across presentations
        if code is None and facts["css_valid"] == "verified":
            # Before the identity is recorded, k is read from this presentation's L1 execution.
            for r in res.current().values():
                if r.proposition.predicate == "dimension_eq" and p_sha in r.input_hashes:
                    facts["k"] = r.proposition.args["k"]
        facts["canonical_basis"] = self._own_basis(res, p_sha)
        return facts

    def _own_basis(self, res, p_sha: str) -> str | None:
        """The canonical basis stored by THIS presentation's own L1 execution, validated to belong
        to it. A basis is presentation-bound (§A3, §B2); dimension evidence transports separately."""
        for r in res.current().values():
            if not (r.proposition.predicate == "dimension_eq" and r.status == "verified"
                    and r.issuer == "l1.css_algebra" and tuple(r.input_hashes) == (p_sha,)
                    and r.artifacts):
                continue
            try:
                basis = LogicalBasis.model_validate(self.store.get_json(r.artifacts[0]))
            except Exception:                                        # noqa: BLE001
                continue
            if basis.presentation == p_sha:
                return r.artifacts[0]
        return None

    def _distance_facts(self, res, code) -> dict[str, Any]:
        """Certified distance interval per sector, via the resolver (incl. the derived ``both``):
        [largest verified lower bound, smallest verified upper bound]; None when absent."""
        if code is None:
            return {}
        from .contracts import Proposition
        values = sorted({r.proposition.args["d"] for r in res.current().values()
                         if r.proposition.subject == code
                         and r.proposition.predicate in ("distance_lower_bound", "distance_upper_bound")})

        def ok(pred, sector, d):
            return res.status(Proposition(predicate=pred, subject_type="code", subject=code,
                                          args={"sector": sector, "d": d})).status == "verified"
        out = {}
        for sector in ("X", "Z", "both"):
            lows = [d for d in values if ok("distance_lower_bound", sector, d)]
            highs = [d for d in values if ok("distance_upper_bound", sector, d)]
            out[f"d_{sector}"] = [max(lows) if lows else None, min(highs) if highs else None]
        return out

    def _verdicts(self, ev: Evaluation, task, candidate: DesignCandidate) -> Evaluation:
        ev.candidate = self.store.put_json(candidate.model_dump(mode="json"))
        res = self.resolver()
        ev.satisfies = res.satisfies_task(candidate, task).status
        for req in res.requirement_names(task):
            st = res.target_refuted(candidate, task, req).status
            if st != "unknown":
                ev.refuted[req] = st
        return ev
