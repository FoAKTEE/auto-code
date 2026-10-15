"""Deterministic fixtures for the P1a controls (contracts §I): codes from the vendored kernel, frozen
tasks and designer outputs.

* ``bicycle_chain(2, 7)`` is [[28, 4, 5]] and ``bicycle_chain(3, 7)`` is [[42, 6, 6]] (the qecforge
  reference table); max check weight 8. Their kernel logical basis is the common disjoint basis:
  logical qubit ``q`` is the indicator of qubits ``7q .. 7q+6`` in both L_X and L_Z.
* On ``bicycle_chain(3, 7)`` in that basis, physical S on every qubit is logical S† on every logical
  qubit (X → −Y, Z → +Z), and the permutation ``chain_shift(3, 7, 1, 0)`` is the logical permutation
  ``q → SIGMA[q]`` with all signs + (the S-F fixture of ``tests/verify/fixtures``).
* The Steane code is [[7, 1, 3]].
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from ..contracts import HardwareDomain, SignedPauliImages, StructuralTargets, TaskSA, TaskSF
from ..grammar import from_dense
from ..kernel.codes import bicycle_chain, steane
from ..kernel.structure import chain_shift

SIGMA = (1, 2, 0, 4, 5, 3)


def _u8(a) -> np.ndarray:
    return np.asarray(a, dtype=np.uint8)


def chain(ell: int) -> tuple[np.ndarray, np.ndarray]:
    c = bicycle_chain(ell, 7)
    return _u8(c.hx), _u8(c.hz)


def chain_basis(ell: int = 3) -> tuple[np.ndarray, np.ndarray]:
    c = bicycle_chain(ell, 7)
    return _u8(c.lx), _u8(c.lz)


def steane_checks() -> tuple[np.ndarray, np.ndarray]:
    c = steane()
    return _u8(c.hx), _u8(c.hz)


def shift(dx: int) -> list[int]:
    return [int(q) for q in chain_shift(3, 7, dx, 0)]


def rows(a: np.ndarray) -> list[list[int]]:
    return [[int(v) for v in r] for r in np.asarray(a)]


def output(hx, hz, *, witnesses: dict | None = None, requested: Iterable[str] | None = None,
           rationale: str = "control fixture") -> dict[str, Any]:
    """A designer output object (the text the production intake parses)."""
    obj: dict[str, Any] = {"family": "explicit_sparse",
                           "params": from_dense(_u8(hx), _u8(hz)).model_dump(mode="json"),
                           "rationale": rationale}
    if witnesses is not None:
        obj["witnesses"] = witnesses
    if requested is not None:
        obj["requested"] = list(requested)
    return obj


# --- frozen tasks -----------------------------------------------------------------------------------
def _sa(task_id: str, n_max: int, k_min: int, d_min: int, **kw) -> TaskSA:
    return TaskSA(task_id=task_id, targets=StructuralTargets(n_max=n_max, k_min=k_min, d_min=d_min,
                                                             **kw))


STEANE_D3 = _sa("sa-steane-d3", 7, 1, 3)
STEANE_D5 = _sa("sa-steane-d5", 7, 1, 5)
D1 = _sa("sa-d1", 50, 4, 1)
D3_K6 = _sa("sa-d3-k6", 50, 6, 3)
D5 = _sa("sa-d5", 50, 4, 5)
D6 = _sa("sa-d6", 50, 4, 6)
D7 = _sa("sa-d7", 50, 4, 7)
W8 = _sa("sa-w8", 50, 6, 3, w_max=8)

CLIFFORDS = HardwareDomain(name="transversal-1q", allowed_operations=("transversal_1q_clifford",))
PERMUTATIONS = HardwareDomain(name="permutation", allowed_operations=("qubit_permutation",))


def _single(k: int, j: int, char: str, sign: str = "+") -> str:
    return sign + "".join(char if i == j else "I" for i in range(k))


def _sf(task_id: str, x_images, z_images, hardware: HardwareDomain) -> TaskSF:
    k = len(x_images)
    return TaskSF(task_id=task_id, k_req=k,
                  target_action=SignedPauliImages(x_images=tuple(x_images), z_images=tuple(z_images)),
                  targets=StructuralTargets(n_max=50, k_min=k, d_min=3), hardware=hardware)


SF_S = _sf("sf-s", [_single(6, j, "Y") for j in range(6)], [_single(6, j, "Z") for j in range(6)],
           CLIFFORDS)
SF_SDG = _sf("sf-sdg", [_single(6, j, "Y", "-") for j in range(6)],
             [_single(6, j, "Z") for j in range(6)], CLIFFORDS)
SF_XBAR = _sf("sf-xbar0", ["+X"], ["-Z"], CLIFFORDS)        # logical X on logical qubit 0
SF_SHIFT = _sf("sf-shift", [_single(6, SIGMA[q], "X") for q in range(6)],
               [_single(6, SIGMA[q], "Z") for q in range(6)], PERMUTATIONS)
