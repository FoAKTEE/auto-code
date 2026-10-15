"""Caller-supplied grammar caps, checked before any dense allocation (prompt §8 L0, §B3)."""
from __future__ import annotations

from dataclasses import dataclass

from ..contracts.design import MAX_DIM, MAX_ENTRIES

KERNEL_MAX_GROUP_ORDER = 2048   # zkqec.kernel.codes.shift_matrix guard (l*m <= 2048)


@dataclass(frozen=True)
class GrammarCaps:
    """Per-campaign caps. Values may only tighten the hard ceilings below."""

    max_n: int = 1024              # physical qubits (columns)
    max_rows: int = 1024           # rows per check matrix
    max_entries: int = 65_536      # ones per check matrix
    max_monomials: int = 16        # listed monomials per polynomial (two-block family)
    max_exponent: int = 1 << 20    # |p|, |q| for a listed monomial (two-block family)

    def __post_init__(self):
        for name in ("max_n", "max_rows", "max_entries", "max_monomials", "max_exponent"):
            v = getattr(self, name)
            if type(v) is not int or v < 1:
                raise ValueError(f"{name} must be a positive int")
        if self.max_n > MAX_DIM or self.max_rows > MAX_DIM:
            raise ValueError(f"caps may not exceed the contract ceiling MAX_DIM={MAX_DIM}")
        if self.max_entries > MAX_ENTRIES:
            raise ValueError(f"max_entries may not exceed MAX_ENTRIES={MAX_ENTRIES}")

    def to_dict(self) -> dict[str, int]:
        return {"max_n": self.max_n, "max_rows": self.max_rows, "max_entries": self.max_entries,
                "max_monomials": self.max_monomials, "max_exponent": self.max_exponent}


HARD_CEILINGS = {"max_n": MAX_DIM, "max_rows": MAX_DIM, "max_entries": MAX_ENTRIES,
                 "two_block_group_order": KERNEL_MAX_GROUP_ORDER}
