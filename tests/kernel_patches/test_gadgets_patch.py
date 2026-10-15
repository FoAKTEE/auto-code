"""Test-first regression tests for kernel deviation KD-1 (analyze_postselected_map tolerances).

Reproduced defects (Codex independent analysis; reproduced on this host 2026-10-15):
  * a non-unitary target passes the unitarity guard because np.allclose keeps its default rtol;
  * an absolute residual on a tiny Kraus operator accepts a 1-rad relative phase as "target up to
    scalar".
"""
import numpy as np
import pytest

from zkqec.kernel.gadgets import analyze_postselected_map, logical_rz


def test_non_unitary_target_is_rejected_at_declared_tolerance():
    u = np.diag([np.sqrt(1 + 1e-6), np.sqrt(1 - 1e-6)]).astype(complex)
    with pytest.raises(ValueError):
        analyze_postselected_map(u / 2, u, tolerance=1e-10)


def test_tiny_kraus_with_relative_phase_is_not_target_up_to_scalar():
    k = 2e-10 * np.diag([1, np.exp(1j)])
    report = analyze_postselected_map(k, np.eye(2), tolerance=1e-10)
    assert not report["target_up_to_scalar"]


def test_tiny_kraus_proportional_to_target_is_accepted():
    u = logical_rz([0.3])
    report = analyze_postselected_map(1e-9 * u, u, tolerance=1e-10)
    assert report["target_up_to_scalar"]
    assert report["state_independent_acceptance"]


def test_relative_residual_is_reported():
    u = logical_rz([0.3])
    report = analyze_postselected_map(0.5 * u, u, tolerance=1e-10)
    assert report["relative_residual"] <= 1e-12
