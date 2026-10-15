"""Identity keys (prompt appendix §A1 subject types, §D1–§D2).

Three distinct keys, never interchangeable:

* :func:`presentation_sha` — subject ``presentation``: the content hash of the exact canonical
  presentation JSON (sorted unique sparse coordinates), i.e. of the exact ``(H_X, H_Z)`` matrices.
* :func:`code_sha` — subject ``code``: the stabilizer *group*. Hash of ``n`` and the canonical
  reduced row-echelon bases of rowspace(H_X) and rowspace(H_Z) over GF(2) (RREF of a row space is
  unique). Presentations with equal row spaces share it; the X and Z parts are kept separate, so
  an X/Z swap is a *relation*, not equality.
* :func:`presentation_key` — §D1 check-presentation key: canonical certificate (pynauty) of the
  colored Tanner graph of the *given* generators (colors: qubit / X-check / Z-check). Invariant
  under qubit and check permutations; **generator-dependent** (two generating sets of one group
  generally get different keys). A deduplication key only, never an equivalence certificate.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pynauty

from ..contracts import Presentation, content_hash
from ..kernel import gf2

CODE_SHA_KIND = "css_stabilizer_group_rref_v1"
PRESENTATION_KEY_KIND = "colored_tanner_graph_nauty_v1"
NAUTY_TAG = f"pynauty-{pynauty.__version__}"


def presentation_sha(p: Presentation) -> str:
    if not isinstance(p, Presentation):
        raise TypeError("presentation_sha needs a zkqec.contracts.Presentation")
    return content_hash(p)


def _binary(a, name: str) -> np.ndarray:
    try:
        return gf2.matrix(a)
    except ValueError:
        raise ValueError(f"{name} must be a binary two-dimensional matrix") from None


def _supports(rows: np.ndarray) -> list[list[int]]:
    return [np.flatnonzero(r).tolist() for r in rows]


def code_sha(hx, hz) -> str:
    """Hash of the stabilizer group: n plus canonical RREF row bases of both row spaces."""
    hx, hz = _binary(hx, "hx"), _binary(hz, "hz")
    if hx.shape[1] != hz.shape[1]:
        raise ValueError("hx and hz need a common number of columns")
    payload = {"kind": CODE_SHA_KIND, "n": int(hx.shape[1]),
               "x_rref": _supports(gf2.row_basis(hx)), "z_rref": _supports(gf2.row_basis(hz))}
    return content_hash(payload)


def tanner_graph(hx, hz) -> pynauty.Graph:
    """Vertices: qubits 0..n-1, X-checks n..n+rx-1, Z-checks n+rx..; one color per vertex kind."""
    hx, hz = _binary(hx, "hx"), _binary(hz, "hz")
    n, rx, rz = hx.shape[1], hx.shape[0], hz.shape[0]
    if hz.shape[1] != n:
        raise ValueError("hx and hz need a common number of columns")
    adj: dict[int, list[int]] = {}
    for offset, h in ((n, hx), (n + rx, hz)):
        for i, j in np.argwhere(h):
            adj.setdefault(int(j), []).append(offset + int(i))
    coloring = [set(range(n)), set(range(n, n + rx)), set(range(n + rx, n + rx + rz))]
    return pynauty.Graph(n + rx + rz, directed=False, adjacency_dict=adj, vertex_coloring=coloring)


def presentation_key(hx, hz) -> str:
    """§D1 key: sha256 over (kind, nauty version, n, rx, rz, canonical certificate).

    nauty's canonical labelling keeps the color cells in order, so equal certificates together
    with equal cell sizes ``(n, rx, rz)`` imply a color-preserving Tanner-graph isomorphism.
    """
    hx, hz = _binary(hx, "hx"), _binary(hz, "hz")
    n, rx, rz = hx.shape[1], hx.shape[0], hz.shape[0]
    cert = pynauty.certificate(tanner_graph(hx, hz))
    h = hashlib.sha256()
    h.update(f"{PRESENTATION_KEY_KIND}|{NAUTY_TAG}|{n}|{rx}|{rz}|".encode())
    h.update(cert)
    return h.hexdigest()


__all__ = ["CODE_SHA_KIND", "NAUTY_TAG", "PRESENTATION_KEY_KIND", "code_sha", "presentation_key",
           "presentation_sha", "tanner_graph"]
