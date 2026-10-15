"""Resolver: current authority by entailment within one domain (contracts §A3; prompt §4.5).

Only *current* receipts are used (completed, authorizing evidence kind, not revoked, not
quarantined, parents current; see :mod:`.evidence`). ``solver_claim``, ``agent_assertion`` and
``assumption`` never authorize. Any conflict still present among current receipts (defence in depth:
admission quarantines them) removes the conflicting receipts here too.

Registered inference rules
--------------------------
* ``lower_monotone``: lower(d) ⇒ lower(d') for d' ≤ d; ``upper_monotone``: upper(d) ⇒ upper(d'), d' ≥ d.
* ``eq_implies_bound``: verified ``*_eq(v)`` gives lower v and upper v (so ``*_le(v')`` for v' ≥ v).
* ``exact_from_bounds``: lower(d) and upper(d) ⇒ ``distance_exact(d)`` (derived).
* sector rule ``both = min(X, Z)``: ``both_lower_to_sector`` (lower(both) ≥ d ⇒ lower(X), lower(Z)
  ≥ d); ``sector_min`` (lower(X) ≥ d and lower(Z) ≥ d ⇒ lower(both) ≥ d); ``sector_upper``
  (upper(X or Z) ≤ d ⇒ upper(both) ≤ d).
* ``opposite_bound``: a *verified* bound in the opposite direction refutes a non-derived bound
  (e.g. upper(Z) ≤ 4 refutes lower(Z, 5)). Upper evidence never yields a lower bound and vice versa
  (a witness proves only its own direction).
* Derived-only predicates (``distance_exact``, ``*_le``, ``satisfies_task``, ``target_refuted``) are
  only ever ``verified`` or ``unknown``; a refuted premise leaves them ``unknown``.

Transport: code-level predicates live on the code subject and reach a presentation only through a
current identity record (presentation → code) in the same domain. Presentation-level predicates
(``css_valid``, ``block_length_eq``, ``max_check_weight_*``, ``max_qubit_degree_*``) are looked up on
the candidate's own presentation and never transfer. Guarantees: the weakest premise (union bound for
statistical alphas).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Iterable, Literal

from zkqec.contracts import (DesignCandidate, Guarantee, LogicalBasis, PhysicalOperation,
                             Proposition, TaskSA, TaskSF, content_hash)
from zkqec.contracts.evidence import Status
from zkqec.store import check_sha

from .evidence import (IdentityRecord, ReceiptRecord, atoms_of, current_identities,
                       current_receipts, find_conflicts, guarantee_rank, weakest)

TASK_REQUIREMENTS = ("n_max", "k_min", "d_min", "w_max", "deg_max", "operation")


@dataclass(frozen=True)
class Support:
    value: int | None
    premises: tuple[str, ...]
    guarantee: Guarantee
    rule: str


@dataclass(frozen=True)
class Requirement:
    name: str
    status: Literal["met", "violated", "unknown"]
    premises: tuple[str, ...] = ()
    guarantee: Guarantee | None = None
    explanation: str = ""


@dataclass(frozen=True)
class Resolution:
    proposition: Proposition
    status: Status
    rule: str
    premises: tuple[str, ...] = ()
    guarantee: Guarantee | None = None
    explanation: str = ""
    requirements: tuple[Requirement, ...] = ()


def _best(cands: Iterable[Support | None]) -> Support | None:
    live = [c for c in cands if c is not None]
    if not live:
        return None
    return min(live, key=lambda s: (guarantee_rank(s.guarantee), len(s.premises), s.premises))


def _combine(rule: str, *parts: Support, value: int | None = None) -> Support | None:
    g = weakest(p.guarantee for p in parts)
    if g is None:
        return None
    premises = tuple(sorted({rid for p in parts for rid in p.premises}))
    return Support(value, premises, g, rule)


class Resolver:
    def __init__(self, domain: str, receipts: Iterable[ReceiptRecord],
                 identities: Iterable[IdentityRecord] = (), *, store=None):
        self.domain = check_sha(domain)
        self.store = store
        current = current_receipts(receipts, domain)
        self.unquarantined_conflicts = tuple(find_conflicts(current))
        for c in self.unquarantined_conflicts:
            for rid in c.receipt_ids:
                current.pop(rid, None)
        self._receipts = current
        self._by_key: dict[str, list[str]] = defaultdict(list)
        self._atoms: dict[tuple[str, str], list] = defaultdict(list)
        for rid, r in sorted(current.items()):
            self._by_key[r.proposition.key].append(rid)
            for atom in atoms_of(rid, r):
                if atom.verified:                      # refuted atoms never entail anything
                    self._atoms[(atom.subject, atom.quantity)].append(atom)
        self._codes: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for rec in current_identities(identities, domain):
            self._codes[rec.presentation][rec.code].append(rec.identity_id)

    # -- identity --------------------------------------------------------------------------------
    def code_of(self, presentation: str) -> tuple[str, str] | None:
        """(code, identity record id) for a presentation, if exactly one current code is known."""
        codes = self._codes.get(presentation, {})
        if len(codes) != 1:
            return None
        (code, ids), = codes.items()
        return code, sorted(ids)[0]

    # -- bound supports --------------------------------------------------------------------------
    def _direct(self, subject: str, quantity: str, sector: str | None, op: str,
                ok) -> list[Support]:
        out = []
        for a in self._atoms.get((subject, quantity), ()):
            if a.sector == sector and a.op == op and ok(a.value):
                r = self._receipts[a.receipt_id]
                kind = r.proposition.signature.kind
                rule = ("eq_implies_bound" if kind == "equality"
                        else f"{'lower' if op == 'ge' else 'upper'}_monotone")
                out.append(Support(a.value, (a.receipt_id,), r.guarantee, rule))
        return out

    def lower(self, subject: str, quantity: str, sector: str | None, d: int) -> Support | None:
        """Best support for ``quantity(sector) >= d`` on ``subject``."""
        cands = self._direct(subject, quantity, sector, "ge", lambda v: v >= d)
        if sector in ("X", "Z"):
            cands += [replace(s, rule="both_lower_to_sector")
                      for s in self._direct(subject, quantity, "both", "ge", lambda v: v >= d)]
        elif sector == "both":
            bx = _best(self._direct(subject, quantity, "X", "ge", lambda v: v >= d))
            bz = _best(self._direct(subject, quantity, "Z", "ge", lambda v: v >= d))
            if bx and bz:
                cands.append(_combine("sector_min", bx, bz, value=min(bx.value, bz.value)))
        return _best(cands)

    def upper(self, subject: str, quantity: str, sector: str | None, d: int) -> Support | None:
        """Best support for ``quantity(sector) <= d`` on ``subject``."""
        cands = self._direct(subject, quantity, sector, "le", lambda v: v <= d)
        if sector == "both":
            for s in ("X", "Z"):
                cands += [replace(c, rule="sector_upper")
                          for c in self._direct(subject, quantity, s, "le", lambda v: v <= d)]
        return _best(cands)

    # -- propositions ----------------------------------------------------------------------------
    def status(self, prop: Proposition) -> Resolution:
        sig = prop.signature
        if sig.derived_only and sig.quantity is None:
            raise ValueError(f"{prop.predicate}: use satisfies_task() / target_refuted()")
        direct = self._by_key.get(prop.key, [])
        as_support = lambda rid: Support(None, (rid,), self._receipts[rid].guarantee,  # noqa: E731
                                         "direct")
        ref_direct = [as_support(rid) for rid in direct
                      if self._receipts[rid].status == "refuted"]
        if sig.quantity is None:
            ver = _best(as_support(rid) for rid in direct
                        if self._receipts[rid].status == "verified")
            ref = _best(ref_direct)
        else:
            s = prop.args[sig.sector_arg] if sig.sector_arg else None
            v = prop.args[sig.value_arg]
            subj, q = prop.subject, sig.quantity
            if sig.kind == "lower":
                ver, ref = self.lower(subj, q, s, v), self.upper(subj, q, s, v - 1)
            elif sig.kind == "upper":
                ver, ref = self.upper(subj, q, s, v), self.lower(subj, q, s, v + 1)
            else:                                                       # equality
                lo, hi = self.lower(subj, q, s, v), self.upper(subj, q, s, v)
                rule = "exact_from_bounds" if sig.derived_only else "bounds_meet"
                ver = _combine(rule, lo, hi, value=v) if lo and hi else None
                ref = _best([self.lower(subj, q, s, v + 1), self.upper(subj, q, s, v - 1)])
            if ref is not None:
                ref = replace(ref, rule="opposite_bound")
            ref = _best([ref, *ref_direct])
            if ver is not None and len(ver.premises) == 1 and \
                    self._receipts[ver.premises[0]].proposition.key == prop.key:
                ver = replace(ver, rule="direct")
        if sig.derived_only:
            ref = None                         # derived claims are never refuted by the resolver
        if ver is not None and ref is not None:
            return Resolution(prop, "unknown", "inconsistent", tuple(sorted(
                set(ver.premises) | set(ref.premises))), None,
                "verified and refuted are both derivable (expected quarantine)")
        if ver is not None:
            return Resolution(prop, "verified", ver.rule, ver.premises, ver.guarantee)
        if ref is not None:
            return Resolution(prop, "refuted", ref.rule, ref.premises, ref.guarantee)
        return Resolution(prop, "unknown", "no_current_evidence",
                          explanation="no current authorizing receipt entails or refutes this")

    # -- task derivations ------------------------------------------------------------------------
    @staticmethod
    def requirement_names(task: TaskSA | TaskSF) -> tuple[str, ...]:
        names = ["css_valid", "n_max", "k_min", "d_min"]
        names += [n for n in ("w_max", "deg_max") if getattr(task.targets, n) is not None]
        if task.kind == "S-F":
            names.append("operation")
        return tuple(names)

    def satisfies_task(self, candidate: DesignCandidate, task: TaskSA | TaskSF) -> Resolution:
        prop = Proposition(predicate="satisfies_task", subject_type="candidate",
                           subject=content_hash(candidate), args={"task": content_hash(task)})
        reqs = tuple(self._requirement(n, candidate, task) for n in self.requirement_names(task))
        unmet = [r.name for r in reqs if r.status != "met"]
        if unmet:
            return Resolution(prop, "unknown", "task_conjunction", requirements=reqs,
                              explanation=f"requirements not established: {', '.join(unmet)}")
        g = weakest(r.guarantee for r in reqs)
        premises = tuple(sorted({p for r in reqs for p in r.premises}))
        if g is None:
            return Resolution(prop, "unknown", "task_conjunction", premises, None,
                              "combined statistical guarantee is vacuous", reqs)
        return Resolution(prop, "verified", "task_conjunction", premises, g,
                          f"all {len(reqs)} requirements met", reqs)

    def target_refuted(self, candidate: DesignCandidate, task: TaskSA | TaskSF,
                       requirement: str) -> Resolution:
        if requirement not in TASK_REQUIREMENTS:
            raise ValueError(f"unknown task requirement {requirement!r}")
        prop = Proposition(predicate="target_refuted", subject_type="candidate",
                           subject=content_hash(candidate),
                           args={"task": content_hash(task), "requirement": requirement})
        if requirement not in self.requirement_names(task):
            return Resolution(prop, "unknown", "opposite_bound",
                              explanation=f"task declares no {requirement} requirement")
        req = self._requirement(requirement, candidate, task)
        if req.status == "violated":
            return Resolution(prop, "verified", "opposite_bound", req.premises, req.guarantee,
                              req.explanation, (req,))
        return Resolution(prop, "unknown", "opposite_bound", explanation=req.explanation,
                          requirements=(req,))

    def _requirement(self, name: str, cand: DesignCandidate,
                     task: TaskSA | TaskSF) -> Requirement:
        """Evaluate one task requirement: ``met`` / ``violated`` (verified opposite bound) / unknown."""
        if cand.C in ("absent", "unsupported"):
            return Requirement(name, "unknown", explanation="candidate has no presentation")
        t, pres = task.targets, cand.C
        identity: tuple[str, ...] = ()
        if name in ("k_min", "d_min"):
            found = self.code_of(pres)
            if found is None:
                return Requirement(name, "unknown",
                                   explanation="no unique current code identity for presentation")
            subject, identity = found[0], (found[1],)
        if name == "css_valid":
            res = self.status(Proposition(predicate="css_valid", subject_type="presentation",
                                          subject=pres))
            met = Support(None, res.premises, res.guarantee, res.rule) \
                if res.status == "verified" else None
            bad = Support(None, res.premises, res.guarantee, res.rule) \
                if res.status == "refuted" else None
            note = f"css_valid(C) is {res.status}"
        elif name == "n_max":
            met, bad = self.upper(pres, "n", None, t.n_max), self.lower(pres, "n", None, t.n_max + 1)
            note = f"block length vs n_max={t.n_max}"
        elif name == "k_min":
            met = self.lower(subject, "k", None, t.k_min)
            bad = self.upper(subject, "k", None, t.k_min - 1)
            note = f"dimension of code {subject} vs k_min={t.k_min}"
        elif name == "d_min":
            met = self.lower(subject, "distance", "both", t.d_min)
            bad = self.upper(subject, "distance", "both", t.d_min - 1)
            note = f"distance(both) of code {subject} vs d_min={t.d_min}"
        elif name == "w_max":
            met = self.upper(pres, "max_check_weight", None, t.w_max)
            bad = self.lower(pres, "max_check_weight", None, t.w_max + 1)
            note = f"max check weight vs w_max={t.w_max}"
        elif name == "deg_max":
            met = self.upper(pres, "max_qubit_degree", None, t.deg_max)
            bad = self.lower(pres, "max_qubit_degree", None, t.deg_max + 1)
            note = f"max qubit degree vs deg_max={t.deg_max}"
        elif name == "operation":
            met, note = self._operation(cand, task)
            bad = None                         # P1a: no existence refutation for routes (§F)
        else:
            raise ValueError(f"unknown requirement {name!r}")
        if met is not None and bad is not None:
            return Requirement(name, "unknown", explanation=f"{note}: inconsistent evidence")
        for status, sup in (("met", met), ("violated", bad)):
            if sup is not None:
                return Requirement(name, status, tuple(sorted(set(sup.premises) | set(identity))),
                                   sup.guarantee, f"{note}: {status} via {sup.rule}"
                                   + (f" (value {sup.value})" if sup.value is not None else ""))
        return Requirement(name, "unknown", explanation=f"{note}: not established")

    def _operation(self, cand: DesignCandidate, task: TaskSF) -> tuple[Support | None, str]:
        """S-F: a verified ``implements`` for an OperationRef of the candidate whose physical
        operation classes are allowed by the task's hardware domain and whose target, basis and
        presentation match the task and candidate."""
        if self.store is None:
            return None, "operation: no artifact store to inspect operations"
        target = content_hash(task.target_action)
        allowed = set(task.hardware.allowed_operations)
        found, notes = [], []
        for i, op in enumerate(cand.G):
            tag = f"G[{i}]"
            if op.target_action != target:
                notes.append(f"{tag}: target action differs from the task's")
                continue
            if op.basis != cand.L:
                notes.append(f"{tag}: basis is not the candidate's basis L")
                continue
            if len(op.logical_qubits) != task.k_req or len(set(op.logical_qubits)) != task.k_req:
                notes.append(f"{tag}: needs {task.k_req} distinct logical qubits")
                continue
            try:
                basis = LogicalBasis.model_validate(self.store.get_json(op.basis))
                circuit = PhysicalOperation.model_validate(self.store.get_json(op.circuit))
            except Exception as exc:                                # noqa: BLE001
                notes.append(f"{tag}: cannot load basis/circuit ({type(exc).__name__})")
                continue
            if basis.presentation != cand.C:
                notes.append(f"{tag}: basis belongs to another presentation")
                continue
            extra = circuit.operation_classes - allowed
            if extra:
                notes.append(f"{tag}: uses operation classes {sorted(extra)} outside the task's")
                continue
            res = self.status(Proposition(
                predicate="implements", subject_type="operation", subject=content_hash(op),
                args={"target_action": op.target_action, "basis": op.basis,
                      "phase_convention": op.phase_convention}))
            if res.status == "verified":
                found.append(Support(None, res.premises, res.guarantee, f"implements:{tag}"))
            else:
                notes.append(f"{tag}: implements is {res.status}")
        best = _best(found)
        return best, "operation: " + (best.rule if best else "; ".join(notes) or "no operations")
