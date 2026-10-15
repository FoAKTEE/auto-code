"""Exact signed Pauli algebra (phase convention ``signed_pauli_images_v1``).

Representation
--------------
A Pauli operator on n qubits is ``P = i^e · X(x) · Z(z)`` with ``x, z ∈ GF(2)^n`` and ``e ∈ Z_4``,
where ``X(x) = ⊗_q X_q^{x_q}`` and ``Z(z) = ⊗_q Z_q^{z_q}``. Hence ``Y = i·X·Z`` (e = 1, x = z = 1).

* Product: ``(e1,x1,z1)·(e2,x2,z2) = (e1 + e2 + 2·(z1·x2), x1 ⊕ x2, z1 ⊕ z2)`` (moving Z(z1) past
  X(x2) costs ``(-1)^{z1·x2}``).
* Hermitian iff ``e ≡ x·z (mod 2)``; a Hermitian Pauli has sign ``i^(e - #Y) ∈ {+1, -1}``.
* Conjugation by a Clifford is a group homomorphism, so images of generators determine everything.

Signed strings (contracts ``SignedPauliImages``): optional ``+``/``-`` then one of ``IXYZ`` per
qubit, character ``q`` acting on qubit ``q``.

Single-qubit Cliffords (contracts ``ONE_QUBIT_CLIFFORDS``): a multi-letter name is the *matrix
product read left to right*, so ``"HS"`` is the unitary ``H·S`` (S acts first), ``"SH" = S·H``,
``"HSH" = H·S·H``; ``"SX" = √X = H·S·H`` and ``"SXdg" = H·S†·H``. The table below lists
``U X U†`` and ``U Z U†`` for each; tests re-derive it from the 2×2 matrices.

Physical operation (contracts ``PhysicalOperation``): ``U = C · Π``. First the permutation Π moves
old qubit ``q`` to ``perm[q]`` (kernel convention), then ``local_cliffords[p]`` acts on the qubit
now at position ``p``. All arithmetic is integer/GF(2); no floating point.
"""
from __future__ import annotations

import numpy as np

from ..contracts import PhysicalOperation
from ..contracts.design import ONE_QUBIT_CLIFFORDS

# U X U†, U Z U† for every allowed single-qubit Clifford (matrix-product naming, see module doc).
CLIFFORD_CONJUGATION: dict[str, tuple[str, str]] = {
    "I": ("+X", "+Z"),
    "X": ("+X", "-Z"),
    "Y": ("-X", "-Z"),
    "Z": ("-X", "+Z"),
    "H": ("+Z", "+X"),
    "S": ("+Y", "+Z"),
    "Sdg": ("-Y", "+Z"),
    "SX": ("+X", "-Y"),
    "SXdg": ("+X", "+Y"),
    "HS": ("-Y", "+X"),
    "SH": ("+Z", "+Y"),
    "HSH": ("+X", "-Y"),
}
assert set(CLIFFORD_CONJUGATION) == set(ONE_QUBIT_CLIFFORDS)


class Pauli:
    """Immutable signed Pauli ``i^e X(x) Z(z)``."""

    __slots__ = ("e", "x", "z")

    def __init__(self, e: int, x, z):
        x = np.asarray(x, dtype=np.uint8).copy()
        z = np.asarray(z, dtype=np.uint8).copy()
        if x.ndim != 1 or x.shape != z.shape or np.any(x > 1) or np.any(z > 1):
            raise ValueError("x and z must be binary vectors of equal length")
        x.flags.writeable = False
        z.flags.writeable = False
        object.__setattr__(self, "e", int(e) % 4)
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "z", z)

    def __setattr__(self, *_):
        raise AttributeError("Pauli is immutable")

    @property
    def n(self) -> int:
        return int(self.x.shape[0])

    @classmethod
    def identity(cls, n: int) -> "Pauli":
        return cls(0, np.zeros(n, np.uint8), np.zeros(n, np.uint8))

    @classmethod
    def x_type(cls, v) -> "Pauli":
        v = np.asarray(v, dtype=np.uint8)
        return cls(0, v, np.zeros_like(v))

    @classmethod
    def z_type(cls, v) -> "Pauli":
        v = np.asarray(v, dtype=np.uint8)
        return cls(0, np.zeros_like(v), v)

    @classmethod
    def from_str(cls, s: str) -> "Pauli":
        sign = 0
        if s[:1] in "+-":
            sign, s = (2 if s[0] == "-" else 0), s[1:]
        if not s or any(c not in "IXYZ" for c in s):
            raise ValueError(f"bad Pauli string {s!r}")
        x = np.array([c in "XY" for c in s], dtype=np.uint8)
        z = np.array([c in "ZY" for c in s], dtype=np.uint8)
        return cls(sign + int(x @ z), x, z)       # each Y contributes i (Y = iXZ)

    def is_hermitian(self) -> bool:
        return self.e % 2 == int(self.x.astype(np.int64) @ self.z.astype(np.int64)) % 2

    def to_str(self) -> str:
        if not self.is_hermitian():
            raise ValueError("only Hermitian Paulis have a signed string")
        ny = int(self.x.astype(np.int64) @ self.z.astype(np.int64))
        sign = "+" if (self.e - ny) % 4 == 0 else "-"
        chars = "IXZY"
        return sign + "".join(chars[int(a) + 2 * int(b)] for a, b in zip(self.x, self.z))

    def __mul__(self, other: "Pauli") -> "Pauli":
        if self.n != other.n:
            raise ValueError("length mismatch")
        cross = int(self.z.astype(np.int64) @ other.x.astype(np.int64))
        return Pauli(self.e + other.e + 2 * cross, self.x ^ other.x, self.z ^ other.z)

    def commutes(self, other: "Pauli") -> bool:
        s = int(self.x.astype(np.int64) @ other.z.astype(np.int64)
                + self.z.astype(np.int64) @ other.x.astype(np.int64))
        return s % 2 == 0

    def __eq__(self, other) -> bool:
        return (isinstance(other, Pauli) and self.e == other.e
                and np.array_equal(self.x, other.x) and np.array_equal(self.z, other.z))

    def __hash__(self):
        return hash((self.e, self.x.tobytes(), self.z.tobytes()))

    def __repr__(self) -> str:
        try:
            return f"Pauli({self.to_str()})"
        except ValueError:
            return f"Pauli(e={self.e}, x={self.x.tolist()}, z={self.z.tolist()})"


def _gate_tables() -> tuple[dict[str, int], np.ndarray, np.ndarray, np.ndarray]:
    """Per gate g and local Pauli X^a Z^b (index 2a + b): image (e, x, z) of U X^a Z^b U†."""
    names = list(ONE_QUBIT_CLIFFORDS)
    E = np.zeros((len(names), 4), dtype=np.int64)
    X = np.zeros((len(names), 4), dtype=np.uint8)
    Z = np.zeros((len(names), 4), dtype=np.uint8)
    for g, name in enumerate(names):
        ix, iz = (Pauli.from_str(s) for s in CLIFFORD_CONJUGATION[name])
        images = {0: Pauli.identity(1), 2: ix, 1: iz, 3: ix * iz}
        for idx, p in images.items():
            E[g, idx], X[g, idx], Z[g, idx] = p.e, p.x[0], p.z[0]
    return {nm: i for i, nm in enumerate(names)}, E, X, Z


_GATE_INDEX, _TE, _TX, _TZ = _gate_tables()


class PhysicalAction:
    """Exact conjugation ``P -> U P U†`` for a Phase-1 :class:`PhysicalOperation`."""

    def __init__(self, op: PhysicalOperation):
        self.n = op.n
        self.perm = (np.arange(op.n) if op.permutation is None
                     else np.asarray(op.permutation, dtype=np.int64))
        gates = op.local_cliffords if op.local_cliffords is not None else ("I",) * op.n
        self.gate_idx = np.array([_GATE_INDEX[g] for g in gates], dtype=np.int64)

    def conjugate(self, p: Pauli) -> Pauli:
        if p.n != self.n:
            raise ValueError("Pauli length differs from the operation's n")
        x = np.zeros(self.n, np.uint8)
        z = np.zeros(self.n, np.uint8)
        x[self.perm] = p.x               # old qubit q -> position perm[q]; no phase
        z[self.perm] = p.z
        idx = 2 * x.astype(np.int64) + z.astype(np.int64)
        e = p.e + int(_TE[self.gate_idx, idx].sum())
        return Pauli(e, _TX[self.gate_idx, idx], _TZ[self.gate_idx, idx])
