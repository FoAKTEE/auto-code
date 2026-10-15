"""L3 logical_action: exact signed Pauli algebra and the signed tableau check (appendix §F)."""
import json
import pathlib

import numpy as np
import pytest

from _verify_oracle import GATES, dense_unitary, oracle, pauli_matrix
from zkqec.contracts import (LogicalBasis, OperationRef, PhysicalOperation, Presentation,
                             SignedPauliImages)
from zkqec.contracts.design import ONE_QUBIT_CLIFFORDS
from zkqec.grammar import from_dense
from zkqec.identity import presentation_sha
from zkqec.kernel.codes import bicycle_chain, steane
from zkqec.kernel.structure import chain_shift, permutation_action
from zkqec.verify import (CLIFFORD_CONJUGATION, PHASE_CONVENTION, Pauli, PhysicalAction,
                          UnsupportedInput, canonical_basis, compute_logical_action, implements)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def basis_of(p: Presentation, lx, lz) -> LogicalBasis:
    rows = (lambda a: tuple(tuple(int(v) for v in r) for r in np.asarray(a)))
    return LogicalBasis(presentation=presentation_sha(p), lx=rows(lx), lz=rows(lz))


def common(code):
    p = from_dense(code.hx, code.hz)
    return p, basis_of(p, code.lx, code.lz)


def uniform(k, x_char, z_char, sign_x="+", sign_z="+"):
    xs = tuple(sign_x + "".join(x_char if i == j else "I" for i in range(k)) for j in range(k))
    zs = tuple(sign_z + "".join(z_char if i == j else "I" for i in range(k)) for j in range(k))
    return SignedPauliImages(x_images=xs, z_images=zs)


# ------------------------------------------------------------------ exact Pauli algebra
def test_pauli_strings_roundtrip_and_y_convention():
    y = Pauli.from_str("Y")
    assert (y.e, y.x.tolist(), y.z.tolist()) == (1, [1], [1])           # Y = i X Z
    for s in ("+XYZI", "-YYI", "+I", "-Z"):
        assert Pauli.from_str(s).to_str() == s
        assert Pauli.from_str(s).is_hermitian()
    assert Pauli.from_str("XZ").to_str() == "+XZ"


def test_pauli_products_match_dense_matrices():
    rng = np.random.default_rng(11)
    for _ in range(200):
        n = int(rng.integers(1, 4))
        a, b = ("".join(rng.choice(list("IXYZ"), n)) for _ in range(2))
        a, b = rng.choice(["+", "-"]) + a, rng.choice(["+", "-"]) + b
        prod = Pauli.from_str(a) * Pauli.from_str(b)
        dense = pauli_matrix(a) @ pauli_matrix(b)
        phase = {0: 1, 1: 1j, 2: -1, 3: -1j}[prod.e]
        mat = phase * pauli_matrix("+" + "".join("IXZY"[int(x) + 2 * int(z)]
                                                 for x, z in zip(prod.x, prod.z)))
        # i^e X(x)Z(z) as a matrix: Y-positions carry i^{-1} relative to the string
        ny = int(prod.x @ prod.z)
        assert np.allclose(dense, mat * (-1j) ** ny)
        assert Pauli.from_str(a).commutes(Pauli.from_str(b)) == np.allclose(
            pauli_matrix(a) @ pauli_matrix(b), pauli_matrix(b) @ pauli_matrix(a))


@pytest.mark.parametrize("gate", ONE_QUBIT_CLIFFORDS)
def test_clifford_table_matches_matrices(gate):
    u = GATES[gate]
    ix, iz = CLIFFORD_CONJUGATION[gate]
    assert np.allclose(u @ GATES["X"] @ u.conj().T, pauli_matrix(ix))
    assert np.allclose(u @ GATES["Z"] @ u.conj().T, pauli_matrix(iz))


def test_physical_conjugation_matches_dense_unitary():
    rng = np.random.default_rng(5)
    for _ in range(60):
        n = int(rng.integers(1, 5))
        perm = tuple(int(v) for v in rng.permutation(n))
        gates = tuple(str(g) for g in rng.choice(ONE_QUBIT_CLIFFORDS, n))
        op = PhysicalOperation(n=n, permutation=perm, local_cliffords=gates)
        u = dense_unitary(n, perm, gates)
        s = rng.choice(["+", "-"]) + "".join(rng.choice(list("IXYZ"), n))
        img = PhysicalAction(op).conjugate(Pauli.from_str(s))
        assert np.allclose(u @ pauli_matrix(s) @ u.conj().T, pauli_matrix(img.to_str()))


# ------------------------------------------------------------------ control: S vs S-dagger
def test_s_vs_sdg_control_on_chain_3_7(sink):
    """qecforge §4: physical global S induces logical S† on every logical qubit (common basis)."""
    c = bicycle_chain(3, 7)
    p, b = common(c)
    op = PhysicalOperation(n=c.n, local_cliffords=("S",) * c.n)
    (good,) = implements(p, b, op, uniform(6, "Y", "Z", sign_x="-"), range(6), artifact_sink=sink)
    (bad,) = implements(p, b, op, uniform(6, "Y", "Z"), range(6), artifact_sink=sink)
    assert good.status == "verified" and bad.status == "refuted"
    tab = sink.get(bad.witnesses[0])
    assert tab["kind"] == "logical_action_tableau_v1" and tab["stabilizers_preserved"]
    assert tab["x_images"][0] == "-YIIIII" and len(tab["mismatches"]) == 6
    # Physical S-dagger gives logical S.
    op2 = PhysicalOperation(n=c.n, local_cliffords=("Sdg",) * c.n)
    assert implements(p, b, op2, uniform(6, "Y", "Z"), range(6),
                      artifact_sink=sink)[0].status == "verified"


@pytest.mark.parametrize("make", [steane, lambda: bicycle_chain(1, 7)])
def test_s_vs_sdg_small_analogue_dense_oracle(make, sink):
    """Dense check of the same claim on codes small enough for state vectors (n = 7, 14)."""
    c = make()
    p, b = common(c)
    gates = ("S",) * c.n
    op = PhysicalOperation(n=c.n, local_cliffords=gates)
    for sign, expect in (("-", True), ("+", False)):
        t = uniform(c.k, "Y", "Z", sign_x=sign)
        (r,) = implements(p, b, op, t, range(c.k), artifact_sink=sink)
        o = oracle(c.hx, c.hz, c.lx, c.lz, None, gates, t.x_images, t.z_images, tuple(range(c.k)))
        assert (r.status == "verified") == o["implements"] == expect


def test_basis_dependence_of_global_s(sink):
    """The S† claim is basis-specific: in the kernel's generic canonical basis of chain(3,7)
    global S is not S† on every logical qubit (finding recorded in notes/build/verify.md)."""
    c = bicycle_chain(3, 7)
    p = from_dense(c.hx, c.hz)
    op = PhysicalOperation(n=c.n, local_cliffords=("S",) * c.n)
    tab = compute_logical_action(p, canonical_basis(p), op)
    assert tab.stabilizers_preserved
    assert tab.x_images != uniform(6, "Y", "Z", sign_x="-").x_images
    (r,) = implements(p, canonical_basis(p), op, uniform(6, "Y", "Z", sign_x="-"), range(6),
                      artifact_sink=sink)
    assert r.status == "refuted"


# ------------------------------------------------------------------ positive S-F fixture
def load_sf():
    fx = json.loads((FIXTURES / "sf_positive_chain_shift.json").read_text())
    p = Presentation.model_validate(fx["presentation"])
    b = basis_of(p, fx["basis_rows"]["lx"], fx["basis_rows"]["lz"])
    op = PhysicalOperation(**fx["operation"])
    return fx, p, b, op, SignedPauliImages(**fx["target"])


def test_sf_positive_fixture_chain_shift_verified(sink):
    fx, p, b, op, target = load_sf()
    c = bicycle_chain(3, 7)
    assert presentation_sha(p) == fx["presentation_sha"] == presentation_sha(from_dense(c.hx, c.hz))
    assert list(op.permutation) == chain_shift(3, 7, 1, 0)
    (r,) = implements(p, b, op, target, tuple(fx["logical_qubits"]), artifact_sink=sink)
    assert r.status == "verified" and r.completion == "completed"
    assert r.proposition.predicate == "implements" and r.proposition.subject_type == "operation"
    assert r.proposition.args["phase_convention"] == PHASE_CONVENTION
    ref = OperationRef(**sink.get(r.proposition.subject))
    assert ref.circuit and ref.basis == r.proposition.args["basis"]
    assert r.detail["operation_classes"] == ["qubit_permutation"]
    # Kernel cross-check (unsigned, pure-CSS permutation scope).
    ka = permutation_action(c, list(op.permutation))
    sigma = fx["sigma"]
    assert ka["preserves_code"] and all(ka["logical_X"][q][sigma[q]] == 1 for q in range(6))


def test_wrong_permutation_controls(sink):
    fx, p, b, op, target = load_sf()
    lq = tuple(fx["logical_qubits"])
    # (a) a transposition of two qubits is not an automorphism: refuted, offending stabilizer.
    perm = list(range(42))
    perm[0], perm[1] = perm[1], perm[0]
    (r,) = implements(p, b, PhysicalOperation(n=42, permutation=tuple(perm)), target, lq,
                      artifact_sink=sink)
    assert r.status == "refuted" and not r.detail["stabilizers_preserved"]
    assert r.detail["offending"]["reason"] == "image is not in the stabilizer group"
    # (b) a genuine automorphism with the wrong claimed action (inverse shift): refuted.
    inv = PhysicalOperation(n=42, permutation=tuple(chain_shift(3, 7, 2, 0)))
    (r2,) = implements(p, b, inv, target, lq, artifact_sink=sink)
    assert r2.status == "refuted" and r2.detail["stabilizers_preserved"]
    assert r2.detail["mismatch_count"] == 12


def test_sign_flipping_operation_is_refuted(sink):
    c = steane()
    p, b = common(c)
    op = PhysicalOperation(n=7, local_cliffords=("Z",) + ("I",) * 6)
    (r,) = implements(p, b, op, uniform(1, "X", "Z"), (0,), artifact_sink=sink)
    assert r.status == "refuted"
    assert r.detail["offending"]["reason"] == "image is a stabilizer with sign -1"


def test_partial_target_conservative_semantics(sink):
    c = bicycle_chain(3, 7)
    p, b = common(c)
    # Physical X on the support of logical qubit 0 = logical X on qubit 0, identity elsewhere.
    gates = tuple("X" if c.lx[0, q] else "I" for q in range(c.n))
    op = PhysicalOperation(n=c.n, local_cliffords=gates)
    logical_x = SignedPauliImages(x_images=("+X",), z_images=("-Z",))
    assert implements(p, b, op, logical_x, (0,), artifact_sink=sink)[0].status == "verified"
    assert implements(p, b, op, logical_x, (1,), artifact_sink=sink)[0].status == "refuted"
    # Global S acts on all six logical qubits: a target on qubit 0 alone is refuted.
    op_s = PhysicalOperation(n=c.n, local_cliffords=("S",) * c.n)
    sdg = SignedPauliImages(x_images=("-Y",), z_images=("+Z",))
    assert implements(p, b, op_s, sdg, (0,), artifact_sink=sink)[0].status == "refuted"


# ------------------------------------------------------------------ refusals (never verified)
def _malformed(r):
    assert r.status == "unknown" and r.detail["outcome"] == "malformed_proposal"


def test_incomplete_and_invalid_bases_are_refused(sink):
    c = bicycle_chain(3, 7)
    p, _ = common(c)
    op = PhysicalOperation(n=c.n, local_cliffords=("S",) * c.n)
    target = uniform(5, "Y", "Z", sign_x="-")
    truncated = basis_of(p, c.lx[:5], c.lz[:5])                    # incomplete basis control
    (r,) = implements(p, truncated, op, target, range(5), artifact_sink=sink)
    _malformed(r)
    assert "incomplete basis" in r.detail["reason"]
    full = uniform(6, "Y", "Z", sign_x="-")
    unpaired = basis_of(p, c.lx, c.lz[[1, 0, 2, 3, 4, 5]])          # L_X L_Z^T != I
    _malformed(implements(p, unpaired, op, full, range(6), artifact_sink=sink)[0])
    bad_lx = c.lx.copy()
    bad_lx[0, 0] ^= 1                                              # anticommutes with a check
    _malformed(implements(p, basis_of(p, bad_lx, c.lz), op, full, range(6), artifact_sink=sink)[0])
    other = LogicalBasis(presentation="0" * 64, lx=basis_of(p, c.lx, c.lz).lx,
                         lz=basis_of(p, c.lx, c.lz).lz)
    _malformed(implements(p, other, op, full, range(6), artifact_sink=sink)[0])
    short = LogicalBasis(presentation=presentation_sha(p), lx=((1, 0),) * 6, lz=((1, 0),) * 6)
    _malformed(implements(p, short, op, full, range(6), artifact_sink=sink)[0])


def test_invalid_targets_and_indices_are_refused(sink):
    c = steane()
    p, b = common(c)
    op = PhysicalOperation(n=7, local_cliffords=("H",) * 7)
    not_clifford = SignedPauliImages(x_images=("+X",), z_images=("+X",))
    _malformed(implements(p, b, op, not_clifford, (0,), artifact_sink=sink)[0])
    h = SignedPauliImages(x_images=("+Z",), z_images=("+X",))
    assert implements(p, b, op, h, (0,), artifact_sink=sink)[0].status == "verified"
    _malformed(implements(p, b, op, h, (1,), artifact_sink=sink)[0])
    _malformed(implements(p, b, PhysicalOperation(n=8, local_cliffords=("H",) * 8), h, (0,),
                          artifact_sink=sink)[0])
    c2 = bicycle_chain(1, 7)
    p2, b2 = common(c2)
    two = SignedPauliImages(x_images=("XI", "IX"), z_images=("ZI", "IZ"))
    _malformed(implements(p2, b2, PhysicalOperation(n=14, permutation=tuple(range(14))), two,
                          (0, 0), artifact_sink=sink)[0])


def test_non_css_presentation_is_refused(sink):
    p = from_dense(np.array([[1, 0]], np.uint8), np.array([[1, 1]], np.uint8))
    b = LogicalBasis(presentation=presentation_sha(p), lx=(), lz=())
    op = PhysicalOperation(n=2, permutation=(1, 0))
    _malformed(implements(p, b, op, SignedPauliImages(x_images=("X",), z_images=("Z",)), (0,),
                          artifact_sink=sink)[0])


def test_mismatched_phase_convention_is_unsupported(sink):
    c = steane()
    p, b = common(c)
    op = PhysicalOperation(n=7, local_cliffords=("H",) * 7)
    h = SignedPauliImages(x_images=("+Z",), z_images=("+X",))
    with pytest.raises(UnsupportedInput):
        implements(p, b, op, h, (0,), artifact_sink=sink, phase_convention="unsigned_v0")
