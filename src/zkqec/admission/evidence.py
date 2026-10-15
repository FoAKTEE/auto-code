"""Pure evidence model shared by admission and the resolver (contracts §A2, §A3).

* :class:`ReceiptRecord` / :class:`IdentityRecord` are ledger rows with their revocation and
  quarantine flags.
* A receipt is **current** in a domain iff it is authorizing (``completed`` + an authorizing evidence
  kind + status ``verified``/``refuted``), of that domain, not revoked, not quarantined, and all its
  parents are current (revocation invalidates descendants).
* Each current receipt on a scalar quantity becomes one or two interval **atoms**: verified lower →
  ``ge d``; verified upper → ``le d``; verified equality → ``ge v`` and ``le v``; refuted lower(d) →
  ``le d-1``; refuted upper(d) → ``ge d+1``; refuted equality(v) → ``ne v``. Refuted atoms are used
  only for consistency checking, never for entailment.
* :func:`find_conflicts` checks interval consistency per (subject, quantity), including the sector
  rule ``both = min(X, Z)``, plus direct verified/refuted contradictions for non-scalar predicates.
  Every atom in a violated pair/triple is a culprit; the culprit set is removed to a fixed point.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Literal

from zkqec.contracts import Guarantee, Receipt, content_hash
from zkqec.contracts.evidence import AUTHORIZING_KINDS
from zkqec.contracts.registry import SIGNATURES

SECTORED = ("X", "Z", "both")


@dataclass(frozen=True)
class ReceiptRecord:
    receipt_id: str
    receipt: Receipt
    revoked: bool = False
    quarantined: bool = False


@dataclass(frozen=True)
class IdentityRecord:
    identity_id: str
    presentation: str
    code: str
    domain: str
    execution_id: str
    issuer: str
    issuer_version: str
    revoked: bool = False
    quarantined: bool = False


def is_authorizing(r: Receipt) -> bool:
    """Only a completed receipt with an authorizing evidence kind can carry authority (§A2)."""
    return (r.completion == "completed" and r.evidence_kind in AUTHORIZING_KINDS
            and r.status in ("verified", "refuted")
            and not SIGNATURES[r.proposition.predicate].derived_only)


def current_receipts(records: Iterable[ReceiptRecord], domain: str) -> dict[str, Receipt]:
    """Receipts that currently carry authority in ``domain`` (see module doc)."""
    pool = {rec.receipt_id: rec for rec in records if rec.receipt.domain == domain}
    memo: dict[str, bool] = {}

    def ok(rid: str, stack: frozenset = frozenset()) -> bool:
        if rid in memo:
            return memo[rid]
        rec = pool.get(rid)
        good = (rec is not None and rid not in stack and not rec.revoked and not rec.quarantined
                and content_hash(rec.receipt) == rid
                and all(ok(p, stack | {rid}) for p in rec.receipt.parents))
        memo[rid] = good
        return good

    return {rid: pool[rid].receipt for rid in pool
            if ok(rid) and is_authorizing(pool[rid].receipt)}


def current_identities(records: Iterable[IdentityRecord], domain: str) -> list[IdentityRecord]:
    return [r for r in records if r.domain == domain and not r.revoked and not r.quarantined]


# --- atoms ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Atom:
    receipt_id: str
    subject: str
    quantity: str
    sector: str | None
    op: Literal["ge", "le", "ne"]
    value: int
    verified: bool


def atoms_of(receipt_id: str, r: Receipt) -> list[Atom]:
    sig = SIGNATURES[r.proposition.predicate]
    if sig.quantity is None or sig.value_arg is None:
        return []
    args = r.proposition.args
    v = args[sig.value_arg]
    sector = args[sig.sector_arg] if sig.sector_arg else None
    subject = r.proposition.subject
    mk = lambda op, val: Atom(receipt_id, subject, sig.quantity, sector, op, val,  # noqa: E731
                              r.status == "verified")
    if r.status == "verified":
        if sig.kind == "lower":
            return [mk("ge", v)]
        if sig.kind == "upper":
            return [mk("le", v)]
        if sig.kind == "equality":
            return [mk("ge", v), mk("le", v)]
    elif r.status == "refuted":
        if sig.kind == "lower":
            return [mk("le", v - 1)]
        if sig.kind == "upper":
            return [mk("ge", v + 1)]
        if sig.kind == "equality":
            return [mk("ne", v)]
    return []


# --- guarantees -------------------------------------------------------------------------------------
def guarantee_rank(g: Guarantee) -> tuple[int, float]:
    """Sort key: smaller is stronger (exact > numerical(tol) > statistical(alpha))."""
    if g.kind == "exact":
        return (0, 0.0)
    if g.kind == "numerical":
        return (1, float(g.tolerance))
    return (2, float(g.alpha))


def weakest(guarantees: Iterable[Guarantee]) -> Guarantee | None:
    """Guarantee of a derivation: the weakest premise; statistical alphas add (union bound).

    Returns ``None`` when the union bound is vacuous (total alpha ≥ 1); the derivation is then
    ``unknown``.
    """
    gs = list(guarantees)
    stats = [g for g in gs if g.kind == "statistical"]
    if stats:
        alpha = sum(g.alpha for g in stats)
        if alpha >= 1:
            return None
        designs = sorted({g.design for g in stats})
        design = designs[0] if len(designs) == 1 else "union_bound(" + ",".join(designs) + ")"
        return Guarantee(kind="statistical", alpha=alpha, design=design)
    nums = [g for g in gs if g.kind == "numerical"]
    if nums:
        return Guarantee(kind="numerical", tolerance=max(g.tolerance for g in nums))
    return Guarantee(kind="exact")


# --- conflicts --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Conflict:
    category: Literal["semantic_conflict", "direct_contradiction"]
    subject: str
    key: str                                   # quantity name or proposition key
    receipt_ids: tuple[str, ...]


def _interval_culprits(atoms: list[Atom]) -> set[str]:
    ge: dict = defaultdict(list)
    le: dict = defaultdict(list)
    ne: dict = defaultdict(list)
    for a in atoms:
        {"ge": ge, "le": le, "ne": ne}[a.op][a.sector].append(a)
    bad: set[str] = set()
    for s in set(ge) | set(le):                                   # same sector: lo > hi
        for g in ge[s]:
            for l in le[s]:
                if g.value > l.value:
                    bad |= {g.receipt_id, l.receipt_id}
    for g in ge["both"]:                                          # lower(both) applies to X and Z
        for s in ("X", "Z"):
            for l in le[s]:
                if g.value > l.value:
                    bad |= {g.receipt_id, l.receipt_id}
    for gx in ge["X"]:                                            # min(lo X, lo Z) > hi(both)
        for gz in ge["Z"]:
            for l in le["both"]:
                if min(gx.value, gz.value) > l.value:
                    bad |= {gx.receipt_id, gz.receipt_id, l.receipt_id}
    for s in ne:                                                  # [lo, hi] entirely excluded
        if not ge[s] or not le[s]:
            continue
        lo = max(a.value for a in ge[s])
        hi = min(a.value for a in le[s])
        excluded = {a.value for a in ne[s]}
        if lo <= hi and all(v in excluded for v in range(lo, hi + 1)):
            bad |= {a.receipt_id for a in ge[s] if a.value == lo}
            bad |= {a.receipt_id for a in le[s] if a.value == hi}
            bad |= {a.receipt_id for a in ne[s] if lo <= a.value <= hi}
    return bad


def find_conflicts(receipts: dict[str, Receipt]) -> list[Conflict]:
    """Jointly unsatisfiable groups among (already current) receipts of one domain."""
    groups: dict[tuple[str, str], list[Atom]] = defaultdict(list)
    props: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for rid, r in receipts.items():
        atoms = atoms_of(rid, r)
        if atoms:
            for a in atoms:
                groups[(a.subject, a.quantity)].append(a)
        else:
            props[r.proposition.key][r.status].append(rid)
    out: list[Conflict] = []
    for (subject, quantity), atoms in sorted(groups.items()):
        culprits: set[str] = set()
        while True:
            live = [a for a in atoms if a.receipt_id not in culprits]
            new = _interval_culprits(live)
            if not new:
                break
            culprits |= new
        if culprits:
            out.append(Conflict("semantic_conflict", subject, quantity, tuple(sorted(culprits))))
    for key, by_status in sorted(props.items()):
        if by_status.get("verified") and by_status.get("refuted"):
            ids = sorted(by_status["verified"] + by_status["refuted"])
            subject = receipts[ids[0]].proposition.subject
            out.append(Conflict("direct_contradiction", subject, key, tuple(ids)))
    return out
