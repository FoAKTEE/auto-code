"""Closed, versioned predicate signature registry (prompt appendix §A1).

Every proposition names a predicate from this registry. A signature fixes the subject type, the
exact argument names and types, the bound direction (``kind``), the scalar quantity used for
interval-consistency checks, and whether the predicate may only be *derived* by the resolver.
Records inconsistent with their signature are rejected before admission.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

REGISTRY_VERSION = "p1a-1"

SubjectType = Literal["presentation", "code", "basis", "operation", "candidate", "task",
                      "campaign", "arm", "route"]
Kind = Literal["lower", "upper", "equality", "boolean", "relation", "derived", "statistical"]
Guarantee = Literal["exact", "numerical", "statistical"]

SECTORS = ("X", "Z", "both")
RELATIONS = ("same_stabilizer_group", "qubit_permutation", "xz_swap", "logical_basis_change")
PHASE_CONVENTIONS = ("signed_pauli_images_v1",)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Signature:
    name: str
    subject_type: SubjectType
    args: tuple[tuple[str, str], ...]
    kind: Kind
    quantity: str | None = None
    value_arg: str | None = None
    sector_arg: str | None = None
    derived_only: bool = False
    guarantees: frozenset[str] = frozenset({"exact"})
    phase: str = "P1a"


def _sig(*a, **k) -> Signature:
    return Signature(*a, **k)


SIGNATURES: dict[str, Signature] = {s.name: s for s in [
    _sig("css_valid", "presentation", (), "boolean"),
    _sig("block_length_eq", "presentation", (("n", "pos_int"),), "equality", "n", "n"),
    _sig("dimension_eq", "code", (("k", "nonneg_int"),), "equality", "k", "k"),
    _sig("distance_lower_bound", "code", (("sector", "sector"), ("d", "pos_int")), "lower",
         "distance", "d", "sector"),
    _sig("distance_upper_bound", "code", (("sector", "sector"), ("d", "pos_int")), "upper",
         "distance", "d", "sector"),
    _sig("distance_exact", "code", (("sector", "sector"), ("d", "pos_int")), "equality",
         "distance", "d", "sector", derived_only=True),
    _sig("max_check_weight_eq", "presentation", (("w", "nonneg_int"),), "equality",
         "max_check_weight", "w"),
    _sig("max_check_weight_le", "presentation", (("w", "nonneg_int"),), "upper",
         "max_check_weight", "w", derived_only=True),
    _sig("max_qubit_degree_eq", "presentation", (("q", "nonneg_int"),), "equality",
         "max_qubit_degree", "q"),
    _sig("max_qubit_degree_le", "presentation", (("q", "nonneg_int"),), "upper",
         "max_qubit_degree", "q", derived_only=True),
    _sig("implements", "operation", (("target_action", "hash"), ("basis", "hash"),
                                      ("phase_convention", "phase_convention")), "boolean"),
    _sig("same_stabilizer_group", "code", (("other", "hash"),), "relation"),
    _sig("equivalent_under", "code", (("other", "hash"), ("relation", "relation")), "relation",
         phase="P1b"),
    _sig("batch_count_le", "basis", (("b", "pos_int"),), "upper", "batch_count", "b",
         phase="P1b"),
    _sig("target_refuted", "candidate", (("task", "hash"), ("requirement", "str")), "derived",
         derived_only=True),
    _sig("nogo", "route", (("scope", "str"), ("certificate", "hash")), "boolean", phase="P1b"),
    _sig("satisfies_task", "candidate", (("task", "hash"),), "derived", derived_only=True),
    _sig("arm_contrast", "arm", (("estimand", "str"), ("value", "float"), ("lo", "float"),
                                 ("hi", "float"), ("design", "hash")), "statistical",
         guarantees=frozenset({"statistical"}), phase="P1b"),
]}


class SignatureError(ValueError):
    """A proposition does not match its registered signature."""


def _check_arg(kind: str, value: Any) -> bool:
    if kind == "pos_int":
        return type(value) is int and value >= 1
    if kind == "nonneg_int":
        return type(value) is int and value >= 0
    if kind == "sector":
        return value in SECTORS
    if kind == "relation":
        return value in RELATIONS
    if kind == "phase_convention":
        return value in PHASE_CONVENTIONS
    if kind == "hash":
        return isinstance(value, str) and bool(_HEX64.match(value))
    if kind == "str":
        return isinstance(value, str) and 0 < len(value) <= 200
    if kind == "float":
        return isinstance(value, (int, float)) and value == value and abs(value) != float("inf")
    raise SignatureError(f"unknown argument kind {kind!r}")


def check(predicate: str, subject_type: str, args: dict[str, Any]) -> Signature:
    """Validate a proposition's fields against its signature; return the signature."""
    sig = SIGNATURES.get(predicate)
    if sig is None:
        raise SignatureError(f"unknown predicate {predicate!r} (registry {REGISTRY_VERSION})")
    if subject_type != sig.subject_type:
        raise SignatureError(f"{predicate} requires subject type {sig.subject_type}, got {subject_type}")
    expected = dict(sig.args)
    if set(args) != set(expected):
        raise SignatureError(f"{predicate} requires exactly args {sorted(expected)}, got {sorted(args)}")
    for name, kind in expected.items():
        if not _check_arg(kind, args[name]):
            raise SignatureError(f"{predicate}: argument {name}={args[name]!r} is not a valid {kind}")
    return sig
