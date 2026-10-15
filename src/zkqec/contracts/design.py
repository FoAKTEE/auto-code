"""Design objects, physical operations and proposal field classes (prompt appendix §B).

Conventions
-----------
* Binary CSS, +1 stabilizer signs (§B4). ``hx`` rows detect Z errors; ``hz`` rows detect X errors.
* Sparse matrices: explicit ``shape=(rows, cols)`` plus sorted unique ``(row, col)`` coordinates of
  ones over GF(2). Duplicates or out-of-range entries are rejected, never XORed (§B3).
* Signed Pauli strings: optional sign ``+``/``-`` followed by one of ``IXYZ`` per qubit, e.g.
  ``"-XZI"``. Hermitian Paulis only (sign ±1).
* A signed Clifford action on m qubits is given by the images of ``X_1..X_m`` and ``Z_1..Z_m``
  (``phase_convention="signed_pauli_images_v1"``).
* Physical operation (Phase 1): a qubit permutation followed by single-qubit Cliffords. Permutation
  convention (as the kernel): old physical index ``q`` moves to ``perm[q]``.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .base import SHA256_HEX, Frozen

PAULI_RE = re.compile(r"^[+-]?[IXYZ]+$")
MAX_DIM = 4096          # hard cap before any allocation (L0); campaigns set tighter caps
MAX_ENTRIES = 200_000

# Verifier-owned observations: rejected if a proposer supplies them (§B3 class d).
VERIFIER_OWNED_FIELDS = frozenset({
    "n", "k", "d", "distance", "distance_lower", "distance_upper", "weights", "max_check_weight",
    "max_qubit_degree", "logical_action", "action", "error_rate", "logical_error_rate", "novel",
    "novelty", "verified", "status", "issuer",
})


class SparseMatrix(Frozen):
    shape: tuple[int, int]
    entries: tuple[tuple[int, int], ...]

    @model_validator(mode="after")
    def _valid(self):
        r, c = self.shape
        if not (0 <= r <= MAX_DIM and 1 <= c <= MAX_DIM):
            raise ValueError(f"shape {self.shape} outside caps")
        if len(self.entries) > MAX_ENTRIES:
            raise ValueError("too many entries")
        if list(self.entries) != sorted(set(self.entries)):
            raise ValueError("entries must be sorted and unique (duplicates are rejected, not XORed)")
        for i, j in self.entries:
            if not (0 <= i < r and 0 <= j < c):
                raise ValueError(f"entry {(i, j)} out of range for shape {self.shape}")
        return self


class Presentation(Frozen):
    """An exact CSS presentation (subject type ``presentation``)."""
    hx: SparseMatrix
    hz: SparseMatrix

    @model_validator(mode="after")
    def _cols(self):
        if self.hx.shape[1] != self.hz.shape[1]:
            raise ValueError("hx and hz need a common number of columns")
        return self


class SignedPauliImages(Frozen):
    """Images of X_1..X_m and Z_1..Z_m under a Clifford (signed Hermitian Paulis)."""
    x_images: tuple[str, ...]
    z_images: tuple[str, ...]

    @model_validator(mode="after")
    def _shape(self):
        m = len(self.x_images)
        if m < 1 or len(self.z_images) != m:
            raise ValueError("need m >= 1 X images and m Z images")
        for p in (*self.x_images, *self.z_images):
            if not PAULI_RE.match(p) or len(p.lstrip("+-")) != m:
                raise ValueError(f"bad signed Pauli {p!r} for m={m}")
        return self


ONE_QUBIT_CLIFFORDS = ("I", "X", "Y", "Z", "H", "S", "Sdg", "SX", "SXdg", "HS", "SH", "HSH")


class PhysicalOperation(Frozen):
    """Phase-1 physical operation: permutation, then a single-qubit Clifford on each qubit."""
    n: int = Field(ge=1, le=MAX_DIM)
    permutation: tuple[int, ...] | None = None
    local_cliffords: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _valid(self):
        if self.permutation is None and self.local_cliffords is None:
            raise ValueError("operation needs a permutation and/or local Cliffords")
        if self.permutation is not None and sorted(self.permutation) != list(range(self.n)):
            raise ValueError("permutation must be a permutation of range(n)")
        if self.local_cliffords is not None:
            if len(self.local_cliffords) != self.n:
                raise ValueError("one local Clifford per physical qubit")
            if any(g not in ONE_QUBIT_CLIFFORDS for g in self.local_cliffords):
                raise ValueError(f"local Cliffords must be in {ONE_QUBIT_CLIFFORDS}")
        return self

    @property
    def operation_classes(self) -> frozenset[str]:
        out = set()
        if self.permutation is not None and list(self.permutation) != list(range(self.n)):
            out.add("qubit_permutation")
        if self.local_cliffords is not None and any(g != "I" for g in self.local_cliffords):
            out.add("transversal_1q_clifford")
        return frozenset(out)


class LogicalBasis(Frozen):
    """Logical representatives for a presentation: rows of L_X and L_Z (dense 0/1 rows)."""
    presentation: str = Field(pattern=SHA256_HEX)
    lx: tuple[tuple[int, ...], ...]
    lz: tuple[tuple[int, ...], ...]

    @field_validator("lx", "lz")
    @classmethod
    def _binary(cls, v):
        if any(x not in (0, 1) for row in v for x in row):
            raise ValueError("logical rows must be binary")
        return v


class OperationRef(Frozen):
    target_action: str = Field(pattern=SHA256_HEX)   # artifact: SignedPauliImages on logical qubits
    circuit: str = Field(pattern=SHA256_HEX)         # artifact: PhysicalOperation
    basis: str = Field(pattern=SHA256_HEX)           # artifact: LogicalBasis
    logical_qubits: tuple[int, ...]                  # which logical indices the target acts on
    phase_convention: Literal["signed_pauli_images_v1"] = "signed_pauli_images_v1"


ComponentRef = str  # artifact sha256, or "absent" / "unsupported"


def _component(v: str) -> str:
    if v in ("absent", "unsupported") or re.match(SHA256_HEX, v):
        return v
    raise ValueError("component ref must be an artifact hash, 'absent' or 'unsupported'")


class DesignCandidate(Frozen):
    """Immutable candidate x = (E, A, C, L, G, P, D, F, eta). Phase 1 activates C, L, G."""
    E: ComponentRef = "absent"
    A: ComponentRef = "absent"
    C: ComponentRef
    L: ComponentRef = "absent"
    G: tuple[OperationRef, ...] = ()
    P: ComponentRef = "absent"
    D: ComponentRef = "absent"
    F: ComponentRef = "absent"
    eta: ComponentRef = "absent"
    parent: str | None = None

    @field_validator("E", "A", "C", "L", "P", "D", "F", "eta")
    @classmethod
    def _ref(cls, v):
        return _component(v)


class Proposal(Frozen):
    """An agent's component-edit proposal. Construction params, requested predicates and untrusted
    witnesses only; verifier-owned observations are rejected (§B3)."""
    family: Literal["explicit_sparse", "two_block_group_algebra", "program"]
    params: dict[str, Any]
    requested: tuple[str, ...] = ()            # non-binding target predicates (free text ids)
    witnesses: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def _no_owned(self):
        bad = VERIFIER_OWNED_FIELDS & (set(self.params) | set(self.witnesses))
        if bad:
            raise ValueError(f"verifier-owned fields supplied by proposer: {sorted(bad)}")
        return self
