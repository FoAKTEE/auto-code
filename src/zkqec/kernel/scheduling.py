"""Conservative collision-free CSS syndrome scheduling.

The two CSS sectors are serialized. Greedy matching layers within each sector
are valid commuting-CNOT extraction orders, but neither depth nor fault distance
is optimized. Mixing X/Z layers is intentionally not inferred from edge coloring.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import numpy as np
from .codes import CSSCode


@dataclass(frozen=True)
class CNOTLayer:
    sector: str
    pairs: tuple[tuple[int, int], ...]


def _match_layers(pairs: list[tuple[int, int]], sector: str) -> list[CNOTLayer]:
    layers: list[list[tuple[int, int]]] = []
    used: list[set[int]] = []
    for pair in pairs:
        for i, occupied in enumerate(used):
            if not (set(pair) & occupied):
                layers[i].append(pair)
                occupied.update(pair)
                break
        else:
            layers.append([pair])
            used.append(set(pair))
    return [CNOTLayer(sector, tuple(layer)) for layer in layers]


def css_schedule(code: CSSCode) -> list[CNOTLayer]:
    n, rx = code.n, len(code.hx)
    xpairs = [(n+i, int(q)) for i, row in enumerate(code.hx) for q in np.flatnonzero(row)]
    zpairs = [(int(q), n+rx+i) for i, row in enumerate(code.hz) for q in np.flatnonzero(row)]
    return _match_layers(xpairs, "X")+_match_layers(zpairs, "Z")


def validate_schedule(code: CSSCode, layers: list[CNOTLayer]) -> bool:
    expected = [(layer.sector, pair) for layer in css_schedule(code) for pair in layer.pairs]
    got = [(layer.sector, pair) for layer in layers for pair in layer.pairs]
    if sorted(expected) != sorted(got):
        return False
    for layer in layers:
        flat = [q for pair in layer.pairs for q in pair]
        if len(flat) != len(set(flat)):
            return False
    # Explicitly enforce the conservative grammar, not just edge coverage.
    seen_z = False
    for layer in layers:
        if layer.sector == "Z":
            seen_z = True
        if seen_z and layer.sector == "X":
            return False
    return True
