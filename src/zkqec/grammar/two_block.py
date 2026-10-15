"""Grammar family ``two_block_group_algebra`` (G-struct) over the abelian group Z_l x Z_m."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from ..contracts import Presentation
from ..kernel.codes import shift_matrix
from .caps import KERNEL_MAX_GROUP_ORDER, GrammarCaps
from .errors import MalformedProposal
from .matrices import from_dense

NAME = "two_block_group_algebra"
VERSION = "1.0.0"

ALGEBRA = (
    "Group G = Z_l x Z_m (abelian); group algebra R = GF(2)[G] = GF(2)[x, y]/(x^l - 1, y^m - 1). "
    "A listed monomial [p, q] (any integers) denotes x^p y^q, the group element (p mod l, q mod m). "
    "A polynomial is the GF(2) sum of its listed monomials, so monomials that coincide after "
    "reduction mod (l, m) cancel in pairs (wrap-cancellation; listing a monomial twice cancels it). "
    "Matrices use the regular representation of zkqec.kernel.codes.shift_matrix: the term x^p y^q "
    "puts a one at row i*m + j, column ((i + p) mod l)*m + (j + q) mod m, for 0 <= i < l, "
    "0 <= j < m. H_X = [A | B], H_Z = [B^T | A^T], n = 2*l*m, l*m rows each, stabilizer signs +1 "
    "(same convention as kernel bivariate_bicycle). H_X H_Z^T = AB + BA = 0 because G is abelian; "
    "verifier L1 re-checks it anyway.")

CAPABILITY_MANIFEST: dict[str, Any] = {
    "name": NAME,
    "version": VERSION,
    "profile": "G-struct",
    "algebra": ALGEBRA,
    "param_schema": {
        "type": "object", "additionalProperties": False, "required": ["l", "m", "a", "b"],
        "properties": {
            "l": {"type": "integer", "minimum": 1},
            "m": {"type": "integer", "minimum": 1},
            "a": {"type": "array", "items": {"type": "array", "items": {"type": "integer"},
                                            "minItems": 2, "maxItems": 2}},
            "b": {"type": "array", "items": {"type": "array", "items": {"type": "integer"},
                                            "minItems": 2, "maxItems": 2}}}},
    "caps": {"caller_supplied": ["max_n (n = 2*l*m)", "max_rows (l*m)", "max_entries",
                                 "max_monomials (per polynomial, before cancellation)",
                                 "max_exponent (|p|, |q|)"],
             "hard_ceilings": {"group_order_l_times_m": KERNEL_MAX_GROUP_ORDER},
             "checked_before_allocation": True},
}


def _int(v: Any, field: str) -> int:
    if type(v) is not int:
        raise MalformedProposal(f"expected an integer, got {type(v).__name__}", field=field)
    return v


def _monomials(poly: Any, name: str, caps: GrammarCaps) -> list[tuple[int, int]]:
    if not isinstance(poly, (list, tuple)):
        raise MalformedProposal("polynomial must be a list of [p, q] monomials", field=name)
    if len(poly) > caps.max_monomials:
        raise MalformedProposal(f"{len(poly)} monomials exceed cap {caps.max_monomials}", field=name)
    out = []
    for t, mono in enumerate(poly):
        f = f"{name}[{t}]"
        if not isinstance(mono, (list, tuple)) or len(mono) != 2:
            raise MalformedProposal("each monomial must be a pair [p, q]", field=f)
        p, q = _int(mono[0], f + "[0]"), _int(mono[1], f + "[1]")
        if abs(p) > caps.max_exponent or abs(q) > caps.max_exponent:
            raise MalformedProposal(f"exponent magnitude exceeds cap {caps.max_exponent}", field=f)
        out.append((p, q))
    return out


def reduce_polynomial(l: int, m: int, monomials: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Normal form in GF(2)[Z_l x Z_m]: reduce exponents, keep monomials of odd multiplicity."""
    counts = Counter((p % l, q % m) for p, q in monomials)
    return sorted(g for g, c in counts.items() if c % 2)


def parse_params(params: dict[str, Any], caps: GrammarCaps
                 ) -> tuple[int, int, list[tuple[int, int]], list[tuple[int, int]]]:
    """Validate every cap and type; no matrix is allocated here."""
    if not isinstance(params, dict) or set(params) != {"l", "m", "a", "b"}:
        raise MalformedProposal("params need exactly the keys 'l', 'm', 'a', 'b'", field="params")
    l, m = _int(params["l"], "l"), _int(params["m"], "m")
    if l < 1 or m < 1:
        raise MalformedProposal("l and m must be >= 1", field="params")
    order = l * m
    if order > KERNEL_MAX_GROUP_ORDER:
        raise MalformedProposal(f"l*m = {order} exceeds the ceiling {KERNEL_MAX_GROUP_ORDER}",
                                field="params")
    if 2 * order > caps.max_n:
        raise MalformedProposal(f"n = 2*l*m = {2 * order} exceeds cap {caps.max_n}", field="params")
    if order > caps.max_rows:
        raise MalformedProposal(f"l*m = {order} rows exceed cap {caps.max_rows}", field="params")
    a, b = _monomials(params["a"], "a", caps), _monomials(params["b"], "b", caps)
    if order * (len(a) + len(b)) > caps.max_entries:
        raise MalformedProposal(f"up to {order * (len(a) + len(b))} ones exceed cap "
                                f"{caps.max_entries}", field="params")
    return l, m, a, b


def build_two_block(params: dict[str, Any], caps: GrammarCaps
                    ) -> tuple[Presentation, np.ndarray, np.ndarray]:
    l, m, a, b = parse_params(params, caps)
    A = shift_matrix(l, m, reduce_polynomial(l, m, a))
    B = shift_matrix(l, m, reduce_polynomial(l, m, b))
    hx = np.hstack((A, B)).astype(np.uint8)
    hz = np.hstack((B.T, A.T)).astype(np.uint8)
    return from_dense(hx, hz), hx, hz
