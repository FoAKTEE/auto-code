"""Syntactic prompt screening (prompt §3: "the prompt linter is syntactic screening only").

It cannot certify that a text carries no encoded answer. It catches the obvious leaks: named code
families in P-min material, task-specific hidden identifiers or target values, and oversize contracts.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MAX_CONTRACT_BYTES = 5 * 1024

# Named constructions/families that P-min material must not mention (case-insensitive, word-ish).
FAMILY_TERMS = (
    r"bicycle", r"\bBB\b", r"gross code", r"surface code", r"toric", r"hypergraph[- ]product",
    r"\bHGP\b", r"lifted[- ]product", r"balanced[- ]product", r"quasi[- ]cyclic", r"group algebra",
    r"two[- ]block", r"\bSteane\b", r"\bGolay\b", r"colou?r code", r"Reed[- ]Muller", r"Hamming code",
    r"tanner code", r"expander code", r"\bpolynomials?\b", r"\btorus\b", r"\bshift matri",
    r"self[- ]dual", r"doubly[- ]even", r"automorphism", r"fold[- ]transversal",
)
_FAMILY_RE = [re.compile(t, re.IGNORECASE) for t in FAMILY_TERMS]


def family_terms(text: str) -> list[str]:
    """Named families/constructions found in a text (P-min screening for contracts and requests)."""
    return [m.group(0) for pat in _FAMILY_RE for m in [pat.search(text)] if m]


def lint_contract(text: str, profile: str) -> list[str]:
    """Return violations for a role contract under a prompt profile (empty list = pass)."""
    out = []
    if len(text.encode("utf-8")) > MAX_CONTRACT_BYTES:
        out.append(f"contract exceeds {MAX_CONTRACT_BYTES} bytes")
    if profile == "P-min":
        for pat in _FAMILY_RE:
            m = pat.search(text)
            if m:
                out.append(f"P-min contract names a family/construction: {m.group(0)!r}")
    elif profile != "P-domain":
        out.append(f"unknown profile {profile!r}")
    return out


def lint_request(text: str, forbidden: list[str]) -> list[str]:
    """Screen a rendered request for task-specific forbidden strings (hidden targets, held-out ids)."""
    low = text.lower()
    return [f"forbidden token present: {tok!r}" for tok in forbidden if tok and tok.lower() in low]


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_fingerprints(roles_dir: Path) -> list[str]:
    """Compare every contract file with roles/FINGERPRINTS.json; contracts are never edited in place."""
    registry = json.loads((roles_dir / "FINGERPRINTS.json").read_text())
    problems = []
    for rel, sha in registry.items():
        p = roles_dir / rel
        if not p.is_file():
            problems.append(f"missing {rel}")
        elif fingerprint(p) != sha:
            problems.append(f"fingerprint mismatch for {rel}")
    for p in roles_dir.rglob("*"):
        if p.is_file() and p.name != "FINGERPRINTS.json" and str(p.relative_to(roles_dir)) not in registry:
            problems.append(f"unregistered contract file {p.relative_to(roles_dir)}")
    return problems
