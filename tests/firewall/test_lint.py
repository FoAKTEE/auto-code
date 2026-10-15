"""Prompt-linter and contract-fingerprint tests (prompt §3, §6, §12)."""
from pathlib import Path

import pytest

from zkqec.firewall.lint import check_fingerprints, lint_contract, lint_request

ROLES = Path(__file__).resolve().parents[2] / "roles"


def test_contract_fingerprints_match_registry():
    assert check_fingerprints(ROLES) == []


@pytest.mark.parametrize("path", sorted(p for p in ROLES.rglob("*.md")))
def test_contracts_are_size_capped(path):
    assert len(path.read_bytes()) <= 5 * 1024


def test_pmin_contract_names_no_family():
    assert lint_contract((ROLES / "designer/P-min.md").read_text(), "P-min") == []


def test_pdomain_contract_would_fail_pmin_screening():
    assert lint_contract((ROLES / "designer/P-domain.md").read_text(), "P-min")


@pytest.mark.parametrize("leak", ["Try a bivariate bicycle code.", "use the surface code", "x^3+y+y^2 polynomial",
                                  "a hypergraph product of repetition codes", "Steane-like"])
def test_pmin_screening_catches_obvious_family_hints(leak):
    assert lint_contract("Design a code. " + leak, "P-min")


def test_request_screening_catches_hidden_tokens():
    assert lint_request("task SA-heldout-0007 with target 12", ["SA-heldout-0007"])
    assert lint_request("nothing here", ["SA-heldout-0007"]) == []


def test_unknown_profile_rejected():
    assert lint_contract("x", "P-nothing")
