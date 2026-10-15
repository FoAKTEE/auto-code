"""Pauli workload IR, conservative compilation, and limited symmetry analysis."""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Iterable
import numpy as np
from . import gf2


@dataclass(frozen=True)
class PauliTerm:
    coefficient: float
    pauli: str

    def __post_init__(self) -> None:
        if not isfinite(self.coefficient) or not self.pauli or set(self.pauli)-set("IXYZ"):
            raise ValueError("A term requires a finite real coefficient and an IXYZ string.")

    @property
    def support(self) -> tuple[int, ...]:
        return tuple(i for i, p in enumerate(self.pauli) if p != "I")


def anticommutes(a: str, b: str) -> bool:
    if len(a) != len(b) or set(a+b)-set("IXYZ"):
        raise ValueError("Invalid Pauli strings or unequal lengths.")
    return bool(sum(x != y and x != "I" and y != "I" for x, y in zip(a, b)) % 2)


def validate_terms(terms: Iterable[PauliTerm]) -> list[PauliTerm]:
    terms = list(terms)
    if not terms or len({len(t.pauli) for t in terms}) != 1:
        raise ValueError("A Hamiltonian needs terms with a common nonzero register size.")
    return terms


def z2_symmetries(terms: Iterable[PauliTerm]) -> list[str]:
    """Independent Z-only Pauli symmetries. This is NOT a complete symmetry group.

    No tapering is performed: sector, state, observable, and transformed operators
    must be supplied and checked by a separate reduction backend.
    """
    terms = validate_terms(terms)
    n = len(terms[0].pauli)
    x = np.array([[int(c in "XY") for c in t.pauli] for t in terms
                  if t.coefficient != 0], dtype=np.uint8).reshape(-1, n)
    return ["".join("Z" if v else "I" for v in r) for r in gf2.nullspace(x)]


def tfim_chain(n: int, coupling: float = 1.0, field: float = 0.7,
               periodic: bool = False) -> list[PauliTerm]:
    if not isinstance(n, int) or n < 2:
        raise ValueError("Use an integer chain length >= 2.")
    edges = {(i, i+1) for i in range(n-1)}
    if periodic and n > 2:
        edges.add((0, n-1))
    terms = []
    for i, j in sorted(edges):
        p = ["I"]*n
        p[i] = p[j] = "Z"
        terms.append(PauliTerm(-coupling, "".join(p)))
    for i in range(n):
        p = ["I"]*n
        p[i] = "X"
        terms.append(PauliTerm(-field, "".join(p)))
    return terms


def jw_spinless(n: int, hopping: list[tuple[int, int, float]],
                 onsite: list[float] | None = None) -> list[PauliTerm]:
    """Jordan-Wigner image of -t(c_i^dagger c_j+h.c.) + sum mu_i n_i.

    Orbital order is 0,...,n-1; qubit 0 is the leftmost Pauli-string character.
    Hopping amplitudes are real. This is a tested reference mapping, not compact
    encoding and not a general electronic-structure integral interface.
    """
    if n < 1 or not isinstance(n, int):
        raise ValueError("n must be a positive integer.")
    terms = []
    for i, j, amplitude in hopping:
        if not 0 <= i < j < n or not isfinite(amplitude):
            raise ValueError("Hopping edges need 0 <= i < j < n and finite amplitude.")
        for endpoint in "XY":
            p = ["I"]*n
            p[i] = p[j] = endpoint
            for k in range(i+1, j):
                p[k] = "Z"
            terms.append(PauliTerm(-amplitude/2, "".join(p)))
    if onsite is not None:
        if len(onsite) != n:
            raise ValueError("Onsite list has the wrong size.")
        for i, mu in enumerate(onsite):
            p = ["I"]*n
            p[i] = "Z"
            terms.extend((PauliTerm(mu/2, "I"*n), PauliTerm(-mu/2, "".join(p))))
    return validate_terms(terms or [PauliTerm(0.0, "I"*n)])


@dataclass(frozen=True)
class Rotation:
    pauli: str
    angle: float  # R_P(angle) = exp(-i angle P/2)
    step: int
    layer: int


def first_order(terms: Iterable[PauliTerm], time: float, steps: int) -> list[Rotation]:
    terms = validate_terms(terms)
    if not isfinite(time) or time < 0 or not isinstance(steps, int) or steps < 1:
        raise ValueError("Require finite nonnegative time and positive integer steps.")
    n = len(terms[0].pauli)
    last = [-1]*n
    out = []
    for step in range(steps):
        for t in terms:
            if not t.support or t.coefficient == 0 or time == 0:
                continue
            layer = max(last[q] for q in t.support)+1
            out.append(Rotation(t.pauli, 2*t.coefficient*time/steps, step, layer))
            for q in t.support:
                last[q] = layer
    return out


def identity_phase_radians(terms: Iterable[PauliTerm], time: float) -> float:
    """Omitted identity-term global phase: full unitary is exp(i*phase)*IR unitary."""
    terms = validate_terms(terms)
    if not isfinite(time) or time < 0:
        raise ValueError("Time must be finite and nonnegative.")
    return -time*sum(t.coefficient for t in terms if not t.support)


def first_order_operator_bound(terms: Iterable[PauliTerm], time: float, steps: int) -> float:
    """Unitary error bound after restoring the known omitted identity phase.

    The physically irrelevant identity term is absent from the rotation IR.
    Comparing raw unitaries without restoring its global phase is not covered.
    Observable predictions do not depend on that global phase.
    """
    terms = validate_terms(terms)
    if not isfinite(time) or time < 0 or not isinstance(steps, int) or steps < 1:
        raise ValueError("Invalid time or step count.")
    pair_sum = sum(abs(a.coefficient*b.coefficient)
                   for i, a in enumerate(terms) for b in terms[i+1:]
                   if anticommutes(a.pauli, b.pauli))
    return min(2.0, time*time*pair_sum/steps)


def summarize(rotations: list[Rotation], n: int) -> dict:
    if n < 1 or any(len(r.pauli) != n for r in rotations):
        raise ValueError("Inconsistent workload register.")
    weights = [sum(p != "I" for p in r.pauli) for r in rotations]
    layers: dict[int, int] = {}
    for r in rotations:
        layers[r.layer] = layers.get(r.layer, 0)+1
    return {"logical_qubits": n, "rotation_count": len(rotations),
            "angle_l1": sum(abs(r.angle) for r in rotations),
            "max_abs_angle": max((abs(r.angle) for r in rotations), default=0),
            "support_layer_count": max(layers, default=-1)+1,
            "peak_disjoint_rotations": max(layers.values(), default=0),
            "naive_parity_ladder_CNOTs": sum(2*(w-1) for w in weights),
            "layer_scope": "preserves_order_of_all_overlapping_operations"}
