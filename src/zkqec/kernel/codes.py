"""CSS objects and independently checked finite-length constructors."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable
import hashlib
import json
import numpy as np
from . import gf2


@dataclass(frozen=True)
class CSSCode:
    hx: np.ndarray
    hz: np.ndarray
    lx: np.ndarray | None = None
    lz: np.ndarray | None = None
    name: str = "css"

    def __post_init__(self) -> None:
        hx, hz = gf2.matrix(self.hx), gf2.matrix(self.hz)
        if hx.shape[1] != hz.shape[1] or hx.shape[1] < 1:
            raise ValueError("Check matrices need a common positive block length.")
        if np.any(hx @ hz.T % 2):
            raise ValueError("CSS checks do not commute.")
        if (self.lx is None) != (self.lz is None):
            raise ValueError("Supply both logical bases or neither.")
        lx, lz = (gf2.canonical_logicals(hx, hz) if self.lx is None
                  else (gf2.matrix(self.lx), gf2.matrix(self.lz)))
        k = hx.shape[1] - gf2.rank(hx) - gf2.rank(hz)
        if lx.shape != (k, hx.shape[1]) or lz.shape != lx.shape:
            raise ValueError("Logical basis has the wrong dimensions.")
        if np.any(hz @ lx.T % 2) or np.any(hx @ lz.T % 2):
            raise ValueError("A proposed logical operator anticommutes with a check.")
        if not np.array_equal(lx @ lz.T % 2, np.eye(k, dtype=np.uint8)):
            raise ValueError("Logical bases are not canonically paired.")
        for name, a in (("hx", hx), ("hz", hz), ("lx", lx), ("lz", lz)):
            a.flags.writeable = False
            object.__setattr__(self, name, a)

    @property
    def n(self) -> int:
        return self.hx.shape[1]

    @property
    def k(self) -> int:
        return self.lx.shape[0]

    @property
    def identity(self) -> str:
        # Representation identity, NOT a code-equivalence certificate.
        payload = {s: getattr(self, s).tolist() for s in ("hx", "hz", "lx", "lz")}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "n": self.n, "k": self.k,
                "representation_sha256": self.identity,
                **{s: getattr(self, s).tolist() for s in ("hx", "hz", "lx", "lz")}}


def shift_matrix(ell: int, m: int, monomials: Iterable[tuple[int, int]]) -> np.ndarray:
    if not isinstance(ell, int) or not isinstance(m, int) or min(ell, m) < 1:
        raise ValueError("ell and m must be positive integers.")
    if ell * m > 2048:
        raise ValueError("Dense research constructor is limited to 2048 cells.")
    a = np.zeros((ell*m, ell*m), dtype=np.uint8)
    for p, q in monomials:
        if not isinstance(p, int) or not isinstance(q, int):
            raise ValueError("Monomial exponents must be integers.")
        for i in range(ell):
            for j in range(m):
                a[i*m+j, ((i+p) % ell)*m+(j+q) % m] ^= 1
    return a


def bivariate_bicycle(ell: int, m: int, a: Iterable[tuple[int, int]],
                       b: Iterable[tuple[int, int]]) -> CSSCode:
    A, B = shift_matrix(ell, m, a), shift_matrix(ell, m, b)
    return CSSCode(np.hstack((A, B)), np.hstack((B.T, A.T)),
                   name=f"bb_{ell}_{m}")


def bicycle_chain(ell: int = 3, m: int = 7) -> CSSCode:
    """Appendix B of arXiv:2606.25011v1; do not assume k or d formulas.

    The paper's common disjoint basis is used only if it passes all checks.
    Other parameter choices fall back to a generic canonical basis.
    """
    A = shift_matrix(ell, m, ((0, 0), (0, 3), (1, 2), (1, 4)))
    h = np.hstack((A, A.T))
    logicals = np.zeros((2*ell, 2*ell*m), dtype=np.uint8)
    for q in range(2*ell):
        logicals[q, q*m:(q+1)*m] = 1
    try:
        return CSSCode(h, h, logicals, logicals, name=f"chain_{ell}_{m}")
    except ValueError:
        return CSSCode(h, h, name=f"chain_{ell}_{m}_generic_basis")


def steane() -> CSSCode:
    h = np.array([[1,1,1,1,0,0,0], [1,1,0,0,1,1,0], [1,0,1,0,1,0,1]], dtype=np.uint8)
    l = np.ones((1, 7), dtype=np.uint8)
    return CSSCode(h, h, l, l, name="steane_7_1_3")


def repetition_checks(length: int) -> np.ndarray:
    if not isinstance(length, int) or length < 2:
        raise ValueError("Repetition length must be an integer >= 2.")
    h = np.zeros((length-1, length), dtype=np.uint8)
    for i in range(length-1):
        h[i, i:i+2] = 1
    return h


def hypergraph_product(h1: np.ndarray, h2: np.ndarray) -> CSSCode:
    h1, h2 = gf2.matrix(h1), gf2.matrix(h2)
    r1, n1 = h1.shape
    r2, n2 = h2.shape
    if n1*n2+r1*r2 > 4096:
        raise ValueError("Dense hypergraph-product constructor exceeds its size guard.")
    hx = np.hstack((np.kron(h1, np.eye(n2, dtype=np.uint8)),
                    np.kron(np.eye(r1, dtype=np.uint8), h2.T)))
    hz = np.hstack((np.kron(np.eye(n1, dtype=np.uint8), h2),
                    np.kron(h1.T, np.eye(r2, dtype=np.uint8))))
    return CSSCode(hx, hz, name=f"hgp_{n1}_{n2}")


def construct(family: str, parameters: dict[str, Any]) -> CSSCode:
    # Declarative allowlist: no eval, exec, imports, shell, or model-generated code.
    if family == "steane":
        if parameters:
            raise ValueError("Steane constructor accepts no parameters.")
        return steane()
    if family == "bicycle_chain":
        if set(parameters) - {"ell", "m"}:
            raise ValueError("Unknown bicycle-chain parameter.")
        return bicycle_chain(**parameters)
    if family == "bivariate_bicycle":
        if set(parameters) != {"ell", "m", "a", "b"}:
            raise ValueError("BB requires exactly ell, m, a, and b.")
        return bivariate_bicycle(parameters["ell"], parameters["m"],
                                parameters["a"], parameters["b"])
    if family == "hgp_repetition":
        if set(parameters) != {"length"}:
            raise ValueError("HGP repetition requires exactly length.")
        h = repetition_checks(parameters["length"])
        return hypergraph_product(h, h)
    raise ValueError(f"Unsupported code family: {family}")
