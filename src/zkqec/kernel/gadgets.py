"""Small-instance dense logical-map checks, not a noisy STAR implementation."""
from __future__ import annotations
from itertools import product
import numpy as np
from .codes import CSSCode
from .gf2 import as_mask, row_basis


def css_isometry(code: CSSCode, max_n: int = 12) -> np.ndarray:
    """Construct |j_L> = uniform superposition of X-stabilizer coset j L_X.

    Computational state-vector index has physical qubit q at bit q. The logical
    index uses logical qubit j at bit j. All generators have +1 stabilizer sign.
    """
    if code.n > max_n:
        raise ValueError("Dense isometry exceeds its explicit size guard.")
    generators = [as_mask(r) for r in row_basis(code.hx)]
    stabilizers = [0]
    for g in generators:
        stabilizers += [s ^ g for s in stabilizers]
    logicals = [as_mask(r) for r in code.lx]
    v = np.zeros((1 << code.n, 1 << code.k), dtype=complex)
    amplitude = 1/np.sqrt(len(stabilizers))
    for j in range(1 << code.k):
        offset = 0
        for i, l in enumerate(logicals):
            if j >> i & 1:
                offset ^= l
        for s in stabilizers:
            v[offset ^ s, j] = amplitude
    if not np.allclose(v.conj().T @ v, np.eye(1 << code.k), atol=1e-12):
        raise AssertionError("Constructed isometry is not orthonormal.")
    return v


def projected_z_rotations(code: CSSCode, physical_angles: list[float],
                          max_n: int = 12) -> np.ndarray:
    """K = V^dagger [product Rz(theta_q)] V for ideal code-space postselection.

    Tests this particular ideal map, NOT the complete TMR/RUS protocol in the
    reference architecture (which has its own preparation and measurement steps).
    """
    if len(physical_angles) != code.n or not np.all(np.isfinite(physical_angles)):
        raise ValueError("Supply one finite angle per physical qubit.")
    v = css_isometry(code, max_n)
    basis = np.arange(1 << code.n, dtype=np.int64)
    phase = np.zeros(len(basis))
    for q, theta in enumerate(physical_angles):
        phase += theta*(1-2*((basis >> q) & 1))
    diagonal = np.exp(-0.5j*phase)
    return v.conj().T @ (diagonal[:, None]*v)


def logical_rz(angles: list[float]) -> np.ndarray:
    if not np.all(np.isfinite(angles)):
        raise ValueError("Angles must be finite.")
    diagonal = []
    for j in range(1 << len(angles)):
        diagonal.append(np.exp(-0.5j*sum(theta*(1-2*((j >> i) & 1))
                                        for i, theta in enumerate(angles))))
    return np.diag(diagonal)


def analyze_postselected_map(kraus: np.ndarray, target: np.ndarray,
                             tolerance: float = 1e-10) -> dict:
    k, u = np.asarray(kraus, complex), np.asarray(target, complex)
    if k.ndim != 2 or k.shape[0] != k.shape[1] or k.shape != u.shape:
        raise ValueError("K and target must be equal-sized square matrices.")
    if not np.all(np.isfinite(k)) or not np.all(np.isfinite(u)) or tolerance <= 0:
        raise ValueError("Invalid map entries or tolerance.")
    d = k.shape[0]
    # zkqec KD-1: rtol=0 so the declared tolerance is the whole tolerance.
    if not np.allclose(u.conj().T @ u, np.eye(d), rtol=0.0, atol=tolerance):
        raise ValueError("Target must be unitary.")
    acceptance = k.conj().T @ k
    spectrum = np.linalg.eigvalsh(acceptance)
    if spectrum.min() < -tolerance or spectrum.max() > 1+tolerance:
        raise ValueError("K is not a trace-nonincreasing Kraus operator.")
    c = np.trace(u.conj().T @ k)/d
    residual = float(np.linalg.norm(k-c*u, ord=2))
    spread = float(spectrum.max()-spectrum.min())
    nonzero = abs(c) > 0.0
    # zkqec KD-1: K = cU is a scale-free statement; judge the residual relative to |c| so a tiny
    # Kraus operator cannot hide a large relative phase under an absolute tolerance.
    relative_residual = residual/abs(c) if nonzero else None
    return {"state_independent_acceptance": spread <= tolerance,
            "acceptance_min": float(max(0, spectrum.min())),
            "acceptance_max": float(min(1, spectrum.max())),
            "target_up_to_scalar": bool(relative_residual is not None
                                        and relative_residual <= tolerance),
            "scalar_abs_squared": float(abs(c)**2),
            "operator_residual": residual, "relative_residual": relative_residual,
            "acceptance_spread": spread,
            "tolerance": tolerance, "evidence_level": "small_instance_numerical_check",
            "scope": "single_ideal_accepted_branch_not_noisy_protocol"}
