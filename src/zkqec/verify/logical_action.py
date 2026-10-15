"""L3 ``logical_action``: signed tableau check of a Phase-1 physical operation (appendix §F).

Inputs: a CSS presentation with +1 stabilizers, a :class:`LogicalBasis` for it (``L_X``, ``L_Z``,
logical qubit ``j`` = row ``j``), a :class:`PhysicalOperation` (permutation, then single-qubit
Cliffords; conventions in :mod:`zkqec.verify.pauli`) and a target :class:`SignedPauliImages` on the
chosen ``logical_qubits``.

Algorithm (exact; phase convention ``signed_pauli_images_v1``):

1. Refuse (``unknown``, outcome ``malformed_proposal``) unless the basis is complete and valid:
   k rows each with ``k = n - rank H_X - rank H_Z``, ``H_Z L_X^T = 0``, ``H_X L_Z^T = 0`` and
   ``L_X L_Z^T = I`` (kernel ``CSSCode`` checks). Also refuse a basis tied to another presentation,
   a wrong-size operation, bad logical-qubit indices, or a target that is not a valid signed
   Clifford tableau (it must satisfy the Pauli commutation relations).
2. Map every stabilizer generator ``X(h)``, ``Z(h)`` through ``U · U†``. The stabilizer group of a
   +1 CSS code is exactly ``{ +X(a)Z(b) : a ∈ rowspace H_X, b ∈ rowspace H_Z }``, so each image
   must have ``x ∈ rowspace H_X``, ``z ∈ rowspace H_Z`` **and phase +1**. Otherwise ``implements``
   is refuted (the operation does not preserve the code space with signs); the offending
   generator and its image are stored.
3. Map all 2k logical generators. An image ``i^e X(x) Z(z)`` decomposes as
   ``x = Σ α_i L_X[i] + a``, ``z = Σ β_i L_Z[i] + b`` with ``α_i = x·L_Z[i]``, ``β_i = z·L_X[i]``,
   ``a ∈ rowspace H_X``, ``b ∈ rowspace H_Z``; then ``X(x)Z(z) = X(Σα L_X) Z(Σβ L_Z) · (+X(a)Z(b))``
   with no extra phase (``a ⊥ L_Z``), so the logical image is exactly ``i^e X̄^α Z̄^β``.
4. Conservative semantics: ``implements`` is verified iff the images of ``X_j, Z_j`` for every
   targeted logical qubit equal the target images (embedded at ``logical_qubits``, identity on the
   other logical qubits), signs included, **and** every non-targeted logical generator maps to
   itself with sign +. Otherwise refuted; the computed tableau is stored either way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..contracts import (LogicalBasis, OperationRef, PhysicalOperation, Presentation,
                         SignedPauliImages, VerifierResult)
from ..grammar.matrices import to_dense
from ..identity import presentation_sha
from ..kernel import gf2
from ..kernel.codes import CSSCode
from .common import (MALFORMED, ArtifactSink, RowSpace, VerifierId, css_violations, proposition,
                     refusal, result, store)
from .pauli import Pauli, PhysicalAction

VERIFIER = VerifierId("zkqec.verify.logical_action", "1.0.0", "L3", ("implements",))
PHASE_CONVENTION = "signed_pauli_images_v1"
METHOD = "signed_symplectic_tableau_v1"


class UnsupportedInput(ValueError):
    """Input that cannot even be stated as a registered proposition (typed outcome unsupported)."""

    outcome = "unsupported"


class MalformedInput(ValueError):
    outcome = MALFORMED


@dataclass(frozen=True)
class ActionTableau:
    """Computed signed action. ``x_images``/``z_images`` are signed strings on k logical qubits."""

    k: int
    stabilizers_preserved: bool
    x_images: tuple[str, ...] = ()
    z_images: tuple[str, ...] = ()
    offending: dict[str, Any] | None = field(default=None)

    def as_dict(self) -> dict[str, Any]:
        return {"k": self.k, "stabilizers_preserved": self.stabilizers_preserved,
                "x_images": list(self.x_images), "z_images": list(self.z_images),
                "offending": self.offending}


def _rows(rows: tuple[tuple[int, ...], ...], n: int, name: str) -> np.ndarray:
    if any(len(r) != n for r in rows):
        raise MalformedInput(f"{name} rows must have length n = {n}")
    return np.array(rows, dtype=np.uint8).reshape(len(rows), n)


def validate_basis(presentation: Presentation, basis: LogicalBasis
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return dense ``(hx, hz, lx, lz)`` or raise :class:`MalformedInput`."""
    hx, hz = to_dense(presentation)
    n = hx.shape[1]
    if len(css_violations(hx, hz)):
        raise MalformedInput("presentation is not CSS-valid")
    if basis.presentation != presentation_sha(presentation):
        raise MalformedInput("basis is tied to a different presentation")
    lx, lz = _rows(basis.lx, n, "lx"), _rows(basis.lz, n, "lz")
    k = n - gf2.rank(hx) - gf2.rank(hz)
    if lx.shape[0] != k or lz.shape[0] != k:
        raise MalformedInput(f"incomplete basis: need k = {k} rows each, got "
                             f"{lx.shape[0]} X and {lz.shape[0]} Z")
    try:
        CSSCode(hx, hz, lx, lz)     # anticommutation with checks, pairing L_X L_Z^T = I
    except ValueError as exc:
        raise MalformedInput(f"invalid basis: {exc}") from None
    return hx, hz, lx, lz


def _decompose(p: Pauli, lx, lz, sx: RowSpace, sz: RowSpace) -> Pauli:
    alpha = (lz.astype(np.int64) @ p.x.astype(np.int64)) % 2
    beta = (lx.astype(np.int64) @ p.z.astype(np.int64)) % 2
    rx = p.x ^ ((alpha.astype(np.int64) @ lx.astype(np.int64)) % 2).astype(np.uint8)
    rz = p.z ^ ((beta.astype(np.int64) @ lz.astype(np.int64)) % 2).astype(np.uint8)
    if not (sx.contains(rx)[0] and sz.contains(rz)[0]):
        raise AssertionError("logical image is not a logical operator; basis or algebra bug")
    return Pauli(p.e, alpha.astype(np.uint8), beta.astype(np.uint8))


def compute_logical_action(presentation: Presentation, basis: LogicalBasis,
                           operation: PhysicalOperation) -> ActionTableau:
    """Steps 1–3 of the module algorithm. Raises :class:`MalformedInput` on invalid inputs."""
    hx, hz, lx, lz = validate_basis(presentation, basis)
    n, k = hx.shape[1], lx.shape[0]
    if operation.n != n:
        raise MalformedInput(f"operation acts on {operation.n} qubits, code has n = {n}")
    act = PhysicalAction(operation)
    sx, sz = RowSpace(hx, n), RowSpace(hz, n)
    for kind, h in (("X", hx), ("Z", hz)):
        for i, row in enumerate(h):
            img = act.conjugate(Pauli.x_type(row) if kind == "X" else Pauli.z_type(row))
            in_group = bool(sx.contains(img.x)[0] and sz.contains(img.z)[0])
            if not in_group or img.e != 0:
                reason = ("image is not in the stabilizer group" if not in_group
                          else "image is a stabilizer with sign -1")
                return ActionTableau(k, False, offending={
                    "generator": kind, "row": i, "reason": reason, "image": img.to_str()})
    xs = tuple(_decompose(act.conjugate(Pauli.x_type(r)), lx, lz, sx, sz).to_str() for r in lx)
    zs = tuple(_decompose(act.conjugate(Pauli.z_type(r)), lx, lz, sx, sz).to_str() for r in lz)
    return ActionTableau(k, True, xs, zs)


def _target_paulis(target: SignedPauliImages) -> tuple[list[Pauli], list[Pauli]]:
    xs = [Pauli.from_str(s) for s in target.x_images]
    zs = [Pauli.from_str(s) for s in target.z_images]
    m = len(xs)
    for i in range(m):
        for j in range(m):
            if not xs[i].commutes(xs[j]) or not zs[i].commutes(zs[j]):
                raise MalformedInput("target images of X's (or of Z's) must commute pairwise")
            if xs[i].commutes(zs[j]) == (i == j):
                raise MalformedInput("target X_i and Z_j images must anticommute iff i == j")
    return xs, zs


def _embed(p: Pauli, positions: tuple[int, ...], k: int) -> Pauli:
    x = np.zeros(k, np.uint8)
    z = np.zeros(k, np.uint8)
    x[list(positions)] = p.x
    z[list(positions)] = p.z
    return Pauli(p.e, x, z)


def expected_images(target: SignedPauliImages, logical_qubits: tuple[int, ...], k: int
                    ) -> tuple[list[str], list[str]]:
    """Full k-qubit target tableau under the conservative semantics (identity elsewhere)."""
    xs, zs = _target_paulis(target)
    ex = [Pauli.x_type(np.eye(k, dtype=np.uint8)[j]) for j in range(k)]
    ez = [Pauli.z_type(np.eye(k, dtype=np.uint8)[j]) for j in range(k)]
    for i, q in enumerate(logical_qubits):
        ex[q] = _embed(xs[i], logical_qubits, k)
        ez[q] = _embed(zs[i], logical_qubits, k)
    return [p.to_str() for p in ex], [p.to_str() for p in ez]


def implements(presentation: Presentation, basis: LogicalBasis, operation: PhysicalOperation,
               target: SignedPauliImages, logical_qubits: tuple[int, ...], *,
               artifact_sink: ArtifactSink,
               phase_convention: str = PHASE_CONVENTION) -> list[VerifierResult]:
    """Verify or refute ``implements(operation, target_action, basis, phase_convention)``.

    The proposition's subject is the stored :class:`OperationRef` (circuit, target, basis,
    logical qubits, phase convention). Malformed inputs give ``unknown`` (never verified).
    """
    if phase_convention != PHASE_CONVENTION:
        raise UnsupportedInput(f"phase convention {phase_convention!r} is not supported "
                               f"(only {PHASE_CONVENTION!r})")
    lq = tuple(int(q) for q in logical_qubits)
    psha = presentation_sha(presentation)
    bsha = store(artifact_sink, basis)
    tsha = store(artifact_sink, target)
    osha = store(artifact_sink, operation)
    ref = OperationRef(target_action=tsha, circuit=osha, basis=bsha, logical_qubits=lq,
                       phase_convention=PHASE_CONVENTION)
    subject = store(artifact_sink, ref)
    prop = proposition("implements", "operation", subject, target_action=tsha, basis=bsha,
                       phase_convention=PHASE_CONVENTION)
    try:
        tableau = compute_logical_action(presentation, basis, operation)
        k = tableau.k
        m = len(target.x_images)
        if len(lq) != m or len(set(lq)) != m or any(not 0 <= q < k for q in lq):
            raise MalformedInput(f"logical_qubits must be {m} distinct indices in [0, {k})")
        want_x, want_z = expected_images(target, lq, k)
    except MalformedInput as exc:
        return [refusal(prop, METHOD, str(exc))]
    except AssertionError as exc:
        return [refusal(prop, METHOD, str(exc), outcome="crashed", completion="crashed")]
    mismatches = []
    if tableau.stabilizers_preserved:
        for kind, got, want in (("X", tableau.x_images, want_x), ("Z", tableau.z_images, want_z)):
            mismatches += [{"generator": f"{kind}{j}", "computed": g, "target": w}
                           for j, (g, w) in enumerate(zip(got, want)) if g != w]
    ok = tableau.stabilizers_preserved and not mismatches
    artifact = {"kind": "logical_action_tableau_v1", "phase_convention": PHASE_CONVENTION,
                "presentation": psha, "basis": bsha, "operation": osha, "target": tsha,
                "operation_ref": subject, "logical_qubits": list(lq), **tableau.as_dict(),
                "target_x_images": want_x, "target_z_images": want_z, "mismatches": mismatches}
    tab = store(artifact_sink, artifact)
    detail = {"stabilizers_preserved": tableau.stabilizers_preserved,
              "mismatch_count": len(mismatches),
              "operation_classes": sorted(operation.operation_classes),
              "semantics": "target on logical_qubits, identity on all other logical qubits"}
    if not tableau.stabilizers_preserved:
        detail["offending"] = {k_: v for k_, v in tableau.offending.items() if k_ != "image"}
    return [result(prop, "verified" if ok else "refuted", METHOD,
                   witnesses=() if ok else (tab,), artifacts=(tab,), detail=detail)]
