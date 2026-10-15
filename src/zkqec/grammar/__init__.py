"""Proposal grammars with declared capability manifests (prompt §3, §8 L0, appendix §B3–§B4).

``build(proposal, caps)`` turns an untrusted :class:`~zkqec.contracts.Proposal` into an exact
:class:`~zkqec.contracts.Presentation` plus dense uint8 ``(hx, hz)``. Every cap is checked before a
dense array is allocated; any violation raises :class:`MalformedProposal`. The grammar asserts no
scientific property: commutation, k, distance and actions are verifier-owned (``zkqec.verify``).
"""
from __future__ import annotations

import numpy as np

from ..contracts import Presentation, Proposal
from . import explicit_sparse, two_block
from .caps import HARD_CEILINGS, GrammarCaps
from .errors import GrammarError, MalformedProposal, UnsupportedFamily
from .matrices import dense_to_sparse, from_dense, sparse_to_dense, to_dense
from .rawjson import check_raw_json, parse_untrusted_json
from .two_block import reduce_polynomial

MANIFESTS = {explicit_sparse.NAME: explicit_sparse.CAPABILITY_MANIFEST,
             two_block.NAME: two_block.CAPABILITY_MANIFEST}

_BUILDERS = {explicit_sparse.NAME: explicit_sparse.build_explicit_sparse,
             two_block.NAME: two_block.build_two_block}


def build(proposal: Proposal, caps: GrammarCaps) -> tuple[Presentation, np.ndarray, np.ndarray]:
    """Build ``(Presentation, hx, hz)`` from a proposal's construction parameters (§B3 class a)."""
    if not isinstance(proposal, Proposal):
        raise MalformedProposal("expected a zkqec.contracts.Proposal", field="proposal")
    builder = _BUILDERS.get(proposal.family)
    if builder is None:
        raise UnsupportedFamily(f"family {proposal.family!r} has no Phase-1a builder",
                                field="family")
    return builder(proposal.params, caps)


__all__ = ["GrammarCaps", "GrammarError", "HARD_CEILINGS", "MANIFESTS", "MalformedProposal",
           "UnsupportedFamily", "build", "check_raw_json", "dense_to_sparse", "from_dense",
           "parse_untrusted_json", "reduce_polynomial", "sparse_to_dense", "to_dense"]
