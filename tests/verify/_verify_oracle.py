"""Independent dense (floating-point) oracle for L3. TEST-ONLY: never imported by zkqec.

It shares no algebra with ``zkqec.verify.pauli``: physical operations are applied to state vectors
from their 2x2 matrices, the code space comes from the kernel's ``css_isometry`` (|j_L> = uniform
superposition over the X-stabilizer coset of j·L_X; physical qubit q at bit q, logical qubit j at
bit j), and the induced logical map W = V† U V is compared with the target by conjugating dense
Pauli matrices.
"""
from __future__ import annotations

import numpy as np

from zkqec.kernel.codes import CSSCode
from zkqec.kernel.gadgets import css_isometry

_S2 = 1 / np.sqrt(2)
GATES = {
    "I": np.eye(2, dtype=complex),
    "X": np.array([[0, 1], [1, 0]], dtype=complex),
    "Y": np.array([[0, -1j], [1j, 0]], dtype=complex),
    "Z": np.diag([1, -1]).astype(complex),
    "H": _S2 * np.array([[1, 1], [1, -1]], dtype=complex),
    "S": np.diag([1, 1j]),
    "Sdg": np.diag([1, -1j]),
    "SX": 0.5 * np.array([[1 + 1j, 1 - 1j], [1 - 1j, 1 + 1j]]),
}
GATES["SXdg"] = GATES["SX"].conj().T
GATES["HS"] = GATES["H"] @ GATES["S"]          # matrix-product naming: S acts first
GATES["SH"] = GATES["S"] @ GATES["H"]
GATES["HSH"] = GATES["H"] @ GATES["S"] @ GATES["H"]


def apply_operation(states: np.ndarray, n: int, permutation, local_cliffords) -> np.ndarray:
    """U·states for U = C·Π (Π: old qubit q -> perm[q]; then C = ⊗ local_cliffords[p] on qubit p)."""
    states = np.asarray(states, dtype=complex).reshape(1 << n, -1)
    if permutation is not None:
        idx = np.arange(1 << n)
        new = np.zeros_like(idx)
        for q, pq in enumerate(permutation):
            new |= ((idx >> q) & 1) << pq
        out = np.zeros_like(states)
        out[new] = states
        states = out
    if local_cliffords is not None:
        for q, g in enumerate(local_cliffords):
            if g == "I":
                continue
            t = states.reshape(1 << (n - q - 1), 2, 1 << q, -1)
            states = np.einsum("ab,ibjc->iajc", GATES[g], t).reshape(1 << n, -1)
    return states


def dense_unitary(n: int, permutation, local_cliffords) -> np.ndarray:
    return apply_operation(np.eye(1 << n, dtype=complex), n, permutation, local_cliffords)


def pauli_matrix(s: str) -> np.ndarray:
    """Signed Pauli string -> dense matrix; character q acts on qubit q (= bit q of the index)."""
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+-")
    m = np.array([[1]], dtype=complex)
    for c in reversed(s):                       # qubit 0 is the last Kronecker factor
        m = np.kron(m, GATES[c] if c != "I" else np.eye(2))
    return sign * m


def full_target(target_x, target_z, logical_qubits, k) -> tuple[list[str], list[str]]:
    """Embed a target on ``logical_qubits`` into k logical qubits; identity elsewhere."""
    def embed(s):
        sign, body = ("-" if s.startswith("-") else "+"), s.lstrip("+-")
        chars = ["I"] * k
        for i, q in enumerate(logical_qubits):
            chars[q] = body[i]
        return sign + "".join(chars)
    unit = (lambda c, j: "+" + "".join(c if i == j else "I" for i in range(k)))
    xs, zs = [unit("X", j) for j in range(k)], [unit("Z", j) for j in range(k)]
    for i, q in enumerate(logical_qubits):
        xs[q], zs[q] = embed(target_x[i]), embed(target_z[i])
    return xs, zs


def oracle(hx, hz, lx, lz, permutation, local_cliffords, target_x, target_z, logical_qubits,
           *, check_full_unitary: bool = False, atol: float = 1e-9) -> dict:
    code = CSSCode(np.asarray(hx), np.asarray(hz), np.asarray(lx), np.asarray(lz))
    n, k = code.n, code.k
    v = css_isometry(code, max_n=16)
    uv = apply_operation(v, n, permutation, local_cliffords)
    if check_full_unitary:
        u = dense_unitary(n, permutation, local_cliffords)
        assert np.allclose(u.conj().T @ u, np.eye(1 << n), atol=atol)
        assert np.allclose(u @ v, uv, atol=atol)
    w = v.conj().T @ uv
    preserved = bool(np.allclose(uv, v @ w, atol=atol))
    if not preserved:
        return {"preserved": False, "implements": False}
    xs, zs = full_target(target_x, target_z, logical_qubits, k)
    ok = True
    for j in range(k):
        for gen, want in (("X", xs[j]), ("Z", zs[j])):
            p = pauli_matrix("+" + "".join(gen if i == j else "I" for i in range(k)))
            ok &= bool(np.allclose(w @ p @ w.conj().T, pauli_matrix(want), atol=atol))
    return {"preserved": True, "implements": ok, "w": w}
