"""Shared helpers for admission/resolver tests.

Test doubles (tests only): ``ScriptedVerifierDouble`` returns whatever results a test queues and
``IdentityTableDouble`` maps presentation hashes to code hashes from a table the test fills. Both are
registered through the real registry and run through the real admission path.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from zkqec.admission import Admission, VerifierRegistry
from zkqec.contracts import Proposition, VerifierResult
from zkqec.store import ArtifactStore


def sha(label: str) -> str:
    """A synthetic 64-hex subject."""
    return hashlib.sha256(label.encode()).hexdigest()


DOMAIN = sha("domain:test")
OTHER_DOMAIN = sha("domain:other")


def prop(predicate: str, subject_type: str, subject: str, **args) -> Proposition:
    return Proposition(predicate=predicate, subject_type=subject_type, subject=subject, args=args)


def vr(predicate: str, subject_type: str, subject: str, status: str = "verified", *,
       kind: str = "exact_algebra", completion: str = "completed", witnesses=(), artifacts=(),
       detail=None, **args) -> VerifierResult:
    return VerifierResult(proposition=prop(predicate, subject_type, subject, **args), status=status,
                          evidence_kind=kind, method="test-method", completion=completion,
                          witnesses=tuple(witnesses), artifacts=tuple(artifacts),
                          detail=detail or {})


# convenience constructors -------------------------------------------------------------------------
def lower(code, sector, d, status="verified", **kw):
    return vr("distance_lower_bound", "code", code, status, sector=sector, d=d, **kw)


def upper(code, sector, d, status="verified", **kw):
    return vr("distance_upper_bound", "code", code, status, sector=sector, d=d, **kw)


def dim(code, k, status="verified"):
    return vr("dimension_eq", "code", code, status, k=k)


def css(pres, status="verified"):
    return vr("css_valid", "presentation", pres, status)


def blen(pres, n, status="verified"):
    return vr("block_length_eq", "presentation", pres, status, n=n)


def weight(pres, w, status="verified"):
    return vr("max_check_weight_eq", "presentation", pres, status, w=w)


def degree(pres, q, status="verified"):
    return vr("max_qubit_degree_eq", "presentation", pres, status, q=q)


class ScriptedVerifierDouble:
    """TEST DOUBLE: returns the queued results (or runs a queued action) on each call."""

    def __init__(self):
        self.queue: list = []
        self.calls: list[dict] = []
        self.action: Callable | None = None

    def __call__(self, **inputs):
        self.calls.append(inputs)
        if self.action is not None:
            action, self.action = self.action, None
            return action(**inputs)
        return self.queue.pop(0) if self.queue else []


class IdentityTableDouble:
    """TEST DOUBLE: identity function backed by a presentation→code table."""

    def __init__(self):
        self.table: dict[str, object] = {}

    def __call__(self, presentation):
        value = self.table[presentation.sha]
        if isinstance(value, Exception):
            raise value
        return value


@dataclass
class Env:
    root: Path
    store: ArtifactStore
    adm: Admission
    script: ScriptedVerifierDouble
    ids: IdentityTableDouble
    seed: str = ""
    presentations: dict = field(default_factory=dict)

    def presentation(self, label: str) -> str:
        """A real (stored) presentation-like artifact; its hash is the presentation subject."""
        if label not in self.presentations:
            self.presentations[label] = self.store.put_json({"presentation": label})
        return self.presentations[label]

    def admit(self, *results, domain: str = DOMAIN, inputs: dict | None = None, **kw):
        """Run the scripted verifier through real admission and return its report."""
        if inputs is None:
            inputs = {"seed": self.seed}
            pres = sorted({r.proposition.subject for r in results
                           if r.proposition.subject_type == "presentation"})
            inputs.update({f"p{i}": p for i, p in enumerate(pres)})
        self.script.queue.append(list(results))
        return self.adm.run_verifier("scripted", inputs, domain, **kw)

    def identity(self, presentation: str, code: str, domain: str = DOMAIN):
        self.ids.table[presentation] = code
        return self.adm.run_identity("table_identity", presentation, domain)

    def resolver(self, domain: str = DOMAIN):
        return self.adm.resolver(domain)

    def event_kinds(self) -> list[str]:
        return [e.kind for e in self.adm.ledger.events()]


def make_env(root: Path) -> Env:
    store = ArtifactStore(root / "store")
    script, ids = ScriptedVerifierDouble(), IdentityTableDouble()
    reg = VerifierRegistry()
    reg.register("scripted", "1.0", script)
    reg.register_identity("table_identity", "1.0", ids)
    adm = Admission(root / "ledger.db", store, reg)
    env = Env(root, store, adm, script, ids)
    env.seed = store.put_json({"seed": "input"})
    return env
