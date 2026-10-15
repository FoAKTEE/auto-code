"""Grammar family ``explicit_sparse`` (G-open): binary CSS check matrices given explicitly."""
from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import ValidationError

from ..contracts import Presentation, SparseMatrix
from .caps import HARD_CEILINGS, GrammarCaps
from .errors import MalformedProposal
from .matrices import to_dense

NAME = "explicit_sparse"
VERSION = "1.0.0"

CAPABILITY_MANIFEST: dict[str, Any] = {
    "name": NAME,
    "version": VERSION,
    "profile": "G-open",
    "algebra": (
        "A binary CSS presentation (H_X, H_Z) over GF(2), stabilizer signs +1. Each matrix is given "
        "by an explicit shape [rows, cols] and the sorted, unique list of [row, col] coordinates of "
        "its ones; both matrices share cols = n. Duplicate, unsorted or out-of-range coordinates are "
        "rejected, never XORed. Zero rows and isolated qubits (zero columns) are allowed; isolated "
        "qubits count in n. Rows of H_X are X-type generators (they detect Z errors); rows of H_Z "
        "are Z-type generators. Commutation H_X H_Z^T = 0 is not assumed: verifier L1 checks it."),
    "param_schema": {
        "type": "object", "additionalProperties": False, "required": ["hx", "hz"],
        "properties": {m: {
            "type": "object", "additionalProperties": False, "required": ["shape", "entries"],
            "properties": {
                "shape": {"type": "array", "items": {"type": "integer", "minimum": 0},
                          "minItems": 2, "maxItems": 2},
                "entries": {"type": "array", "items": {
                    "type": "array", "items": {"type": "integer", "minimum": 0},
                    "minItems": 2, "maxItems": 2}}}} for m in ("hx", "hz")}},
    "caps": {"caller_supplied": ["max_n", "max_rows", "max_entries"],
             "hard_ceilings": {k: HARD_CEILINGS[k] for k in ("max_n", "max_rows", "max_entries")},
             "checked_before_allocation": True},
}


def _int(v: Any, field: str) -> int:
    if type(v) is not int:           # bool, float, str are rejected, never coerced
        raise MalformedProposal(f"expected an integer, got {type(v).__name__}", field=field)
    return v


def _matrix(spec: Any, name: str, caps: GrammarCaps) -> SparseMatrix:
    if not isinstance(spec, dict) or set(spec) != {"shape", "entries"}:
        raise MalformedProposal("needs exactly the keys 'shape' and 'entries'", field=name)
    shape, entries = spec["shape"], spec["entries"]
    if not isinstance(shape, (list, tuple)) or len(shape) != 2:
        raise MalformedProposal("shape must be [rows, cols]", field=f"{name}.shape")
    rows, cols = _int(shape[0], f"{name}.shape[0]"), _int(shape[1], f"{name}.shape[1]")
    if not 0 <= rows <= caps.max_rows:
        raise MalformedProposal(f"rows={rows} outside [0, {caps.max_rows}]", field=f"{name}.shape")
    if not 1 <= cols <= caps.max_n:
        raise MalformedProposal(f"cols={cols} outside [1, {caps.max_n}]", field=f"{name}.shape")
    if not isinstance(entries, (list, tuple)):
        raise MalformedProposal("entries must be a list of [row, col] pairs", field=f"{name}.entries")
    if len(entries) > caps.max_entries:
        raise MalformedProposal(f"{len(entries)} entries exceed cap {caps.max_entries}",
                                field=f"{name}.entries")
    coords = []
    for t, e in enumerate(entries):
        if not isinstance(e, (list, tuple)) or len(e) != 2:
            raise MalformedProposal("each entry must be a [row, col] pair", field=f"{name}.entries[{t}]")
        coords.append((_int(e[0], f"{name}.entries[{t}][0]"), _int(e[1], f"{name}.entries[{t}][1]")))
    try:
        return SparseMatrix(shape=(rows, cols), entries=tuple(coords))
    except ValidationError as exc:
        raise MalformedProposal(exc.errors()[0]["msg"][:300], field=name) from None


def build_explicit_sparse(params: dict[str, Any], caps: GrammarCaps
                          ) -> tuple[Presentation, np.ndarray, np.ndarray]:
    if not isinstance(params, dict) or set(params) != {"hx", "hz"}:
        raise MalformedProposal("params need exactly the keys 'hx' and 'hz'", field="params")
    hx, hz = _matrix(params["hx"], "hx", caps), _matrix(params["hz"], "hz", caps)
    if hx.shape[1] != hz.shape[1]:
        raise MalformedProposal(f"hx has {hx.shape[1]} columns but hz has {hz.shape[1]}",
                                field="params")
    p = Presentation(hx=hx, hz=hz)
    dx, dz = to_dense(p)
    return p, dx, dz
