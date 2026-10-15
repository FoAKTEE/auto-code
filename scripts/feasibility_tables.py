#!/usr/bin/env python
"""M1 measured certification feasibility tables (contracts §C1; master prompt D10, D14).

Measures the production L2 entry point, ``zkqec.verify.certify_distance`` (version 1.1.0, called
exactly as the pipeline's ``l2.distance`` adapter calls it: grammar ``build`` -> presentation ->
``zkqec.pipeline._dense`` -> ``certify_distance(hx, hz, t, budget=DistanceBudget(**cfg))``), one
case per fresh subprocess, per (code, t, sector). Every row records the §C1 preflight (M, memory
estimate, pair-work bounds) and the measured outcome: completion, certified interval, kernel pair
counter, wall and CPU seconds, peak RSS.

Run kinds (each its own subprocess):

* ``sector``: the timing run. Unmodified production code; ``certify_distance(..., sectors=(s,))``.
  A ``t = 0`` call on the same sector first measures the fixed per-call cost (row reduction, CSS
  check, canonical logical basis, code hash, witness re-check).
* ``audit``: the same call under a ``sys.settrace`` hook that reads the kernel's bucket table when
  ``sector_distance`` returns. It counts the stored half-patterns and recomputes
  ``Σ_s C(M_s, 2)`` from the bucket sizes. The kernel reports ``half_patterns`` from the formula
  ``Σ C(n, j)`` computed before enumeration, not from a counter, so this audit is the measurement.
  The hook slows the interpreter, so audit runs carry no timing.
* ``task``: both sectors through the pipeline's registered ``l2.distance`` adapter
  (``zkqec.pipeline.build_registry``) on a temporary artifact store. It checks that a task costs
  about the sum of its sector CPUs, and that its peak is about the larger sector's peak.

Children run with BLAS/OpenMP pools pinned to one thread (``OPENBLAS_NUM_THREADS=1``...); L2 is
single-threaded Python, and unpinned OpenBLAS pools add CPU time with no work done (see the
report). Scheduling: at most ``--workers`` children, and the sum of their preflight memory
estimates stays within ``--mem-pool-gib``. Cases whose estimate exceeds the per-case caps are not
run; they are recorded as ``skipped`` rows that carry their estimates.

Usage::

    .venv/bin/python scripts/feasibility_tables.py --mode full      # ~30-45 min, writes results/
    .venv/bin/python scripts/feasibility_tables.py --mode tiny --out-dir /tmp/x
    .venv/bin/python scripts/feasibility_tables.py --render-only    # regenerate the .md

Outputs ``<out-dir>/M1-certification.json`` and ``<out-dir>/M1-certification.md``. The JSON is
canonical (sorted keys); measured timing fields live under each row's ``measured`` object and
under the top-level ``measurement`` object.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
from math import comb
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
SCHEMA = "zkqec.feasibility.certification/v1"
GiB = 2**30
MB = 2**20

# Production campaign budget (configs/campaigns/m1-live.json, distance_budget; other fields take
# the DistanceBudget defaults). The preflight verdict under it is recorded on every row.
PRODUCTION_BUDGET_SOURCE = "configs/campaigns/m1-live.json"

# Candidate per-task (both sectors) verifier budgets for the frontier tables.
CPU_BUDGETS = (60, 600, 3600)
MEM_BUDGETS_GIB = (8, 32)

# Candidate distance_budget tiers, evaluated against the fit and the measured tasks. "m1-live" is
# the current campaign config, with CampaignConfig's default verifier_cpu_seconds = 600.
TIERS = (
    {"name": "m1-live", "cpu_s": 600, "mem_gib": None, "max_patterns": 3_000_000,
     "max_pairs": 200_000_000, "max_memory_bytes": 4 * 2**30},
    {"name": "A", "cpu_s": 60, "mem_gib": 8, "max_patterns": 4_000_000,
     "max_pairs": 20_000_000, "max_memory_bytes": 2 * 2**30},
    {"name": "B", "cpu_s": 600, "mem_gib": 32, "max_patterns": 50_000_000,
     "max_pairs": 100_000_000, "max_memory_bytes": 20 * 2**30},
)

# Source files whose content defines the measured implementation.
FINGERPRINT_FILES = ("src/zkqec/verify/distance.py", "src/zkqec/verify/common.py",
                     "src/zkqec/kernel/distance.py", "src/zkqec/kernel/gf2.py",
                     "src/zkqec/kernel/codes.py", "src/zkqec/identity/__init__.py",
                     "src/zkqec/pipeline.py", "src/zkqec/grammar/two_block.py",
                     "src/zkqec/grammar/explicit_sparse.py", "src/zkqec/grammar/matrices.py",
                     "src/zkqec/search/random_baseline.py")

MODES = {
    # Per-case caps: preflight memory estimate, M, kernel pair cap, predicted CPU per sector run.
    "full": {"case_mem_gib": 64, "max_patterns": 250_000_000, "max_pairs": 2_000_000_000,
             "max_pred_cpu_s": 900.0, "max_t": 12, "timeout_s": 2400, "audit_max_patterns": 5_000_000,
             "audit_max_pred_cpu_s": 30.0, "small_n": 40, "small_n_cpu_s": 60.0,
             "big_tier_gib": 16, "workers": 32, "mem_pool_gib": 240},
    "tiny": {"case_mem_gib": 1, "max_patterns": 20_000, "max_pairs": 50_000,
             "max_pred_cpu_s": 5.0, "max_t": 3, "timeout_s": 120, "audit_max_patterns": 20_000,
             "audit_max_pred_cpu_s": 5.0, "small_n": 0, "small_n_cpu_s": 5.0,
             "big_tier_gib": 1, "workers": 2, "mem_pool_gib": 2},
}

# Planning prior (calibrated by hand on BB72/BB144/toric, BLAS pinned; replaced by the fit in the
# report). Used only to order and cap the work.
PRIOR_S_PER_PATTERN = 2.4e-6
PRIOR_S_PER_PAIR = 0.25e-6
PRIOR_FIXED_S = 0.3


# ================================================================================================
# Code catalogue
# ================================================================================================

def _two_block(code_id, l, m, a, b, *, subfamily, d_ref=None, ref=None, tier="flagship", meta=None):
    return {"code_id": code_id, "family": "two_block_group_algebra", "subfamily": subfamily,
            "params": {"l": l, "m": m, "a": [list(x) for x in a], "b": [list(x) for x in b]},
            "d_reference": d_ref, "reference": ref, "tier": tier, "meta": meta or {}}


def _explicit(code_id, hx, hz, *, subfamily, d_ref=None, ref=None, tier="standard", meta=None):
    import numpy as np
    params = {name: {"shape": [int(a.shape[0]), int(a.shape[1])],
                     "entries": [[int(i), int(j)] for i, j in np.argwhere(a)]}
              for name, a in (("hx", hx), ("hz", hz))}
    return {"code_id": code_id, "family": "explicit_sparse", "subfamily": subfamily,
            "params": params, "d_reference": d_ref, "reference": ref, "tier": tier,
            "meta": meta or {}}


BB_POLY = ([[3, 0], [0, 1], [0, 2]], [[0, 3], [1, 0], [2, 0]])   # A = x^3+y+y^2, B = y^3+x+x^2
BB_REF = "Bravyi et al. 2024, arXiv:2308.07915, Table 3"


def _bb_codes() -> list[dict]:
    a, b = BB_POLY
    return [
        _two_block("bb72", 6, 6, a, b, subfamily="bb_literature", d_ref=6, ref=BB_REF),
        _two_block("bb90", 15, 3, [[9, 0], [0, 1], [0, 2]], [[0, 0], [2, 0], [7, 0]],
                   subfamily="bb_literature", d_ref=10, ref=BB_REF),
        _two_block("bb108", 9, 6, a, b, subfamily="bb_literature", d_ref=10, ref=BB_REF),
        _two_block("bb144", 12, 6, a, b, subfamily="bb_literature", d_ref=12, ref=BB_REF),
    ]


def _chain_codes(ells=(1, 2, 3, 4)) -> list[dict]:
    """qecforge bicycle_chain(ell, 7): H_X = H_Z = [A | A^T], A = 1 + y^3 + x y^2 + x y^4, so
    B = A^T = the inverse monomials. Reference: the qecforge table [[28,4,5]] [[42,6,6]] [[56,8,6]]."""
    a = [[0, 0], [0, 3], [1, 2], [1, 4]]
    out = []
    for ell in ells:
        b = [[-p, -q] for p, q in a]
        d = {2: 5, 3: 6, 4: 6}.get(ell)
        out.append(_two_block(f"chain{ell}_7", ell, 7, a, b, subfamily="qecforge_chain", d_ref=d,
                              ref="qecforge reference table (tests/verify/test_verify_l2.py)"
                              if d else None, tier="standard"))
    return out


B1_GRID = {24: ((2, 6), (3, 4), (4, 3), (6, 2)), 36: ((2, 9), (3, 6), (6, 3), (9, 2)),
           48: ((3, 8), (4, 6), (6, 4), (8, 3)), 60: ((3, 10), (5, 6), (6, 5), (10, 3)),
           72: ((3, 12), (4, 9), (6, 6), (9, 4), (12, 3)), 90: ((3, 15), (5, 9), (9, 5), (15, 3)),
           108: ((3, 18), (6, 9), (9, 6), (18, 3)), 126: ((3, 21), (7, 9), (9, 7), (21, 3)),
           144: ((6, 12), (8, 9), (9, 8), (12, 6)), 162: ((3, 27), (9, 9), (27, 3)),
           180: ((6, 15), (9, 10), (10, 9), (15, 6)), 198: ((3, 33), (9, 11), (11, 9), (33, 3))}
# n = 200 (l*m = 100) is absent: 300 draws of weight-3 a, b at each of (4,25), (5,20), (10,10),
# (20,5), (2,50) all gave k = 0, so the grid tops out at n = 198 (l*m = 99).
B1_SEED_BASE = 20261015


def _b1_codes(grid: dict[int, tuple]) -> list[dict]:
    """One B1 random two-block code (|a| = |b| = 3, check weight 6) per n: the first draw of the
    seeded sampler with k >= 2 (k >= 1 if none of the first 400 draws has k >= 2)."""
    from zkqec.contracts import Proposal
    from zkqec.grammar import GrammarCaps, build
    from zkqec.kernel import gf2
    from zkqec.search.random_baseline import RandomTwoBlockProposer, RandomTwoBlockSpec
    out = []
    for n, dims in sorted(grid.items()):
        spec = RandomTwoBlockSpec(dims=dims, weights_a=(3,), weights_b=(3,), n_cap=n,
                                  seed=B1_SEED_BASE + n)
        prop = RandomTwoBlockProposer(spec)
        first_k1 = None
        chosen = None
        for draw in range(400):
            p = prop.propose()
            _, hx, hz = build(p, GrammarCaps(max_n=200))
            k = n - gf2.rank(hx) - gf2.rank(hz)
            if k >= 2:
                chosen = (draw, p)
                break
            if k >= 1 and first_k1 is None:
                first_k1 = (draw, p)
        chosen = chosen or first_k1
        if chosen is None:
            continue
        draw, p = chosen
        out.append({"code_id": f"b1_n{n}", "family": "two_block_group_algebra",
                    "subfamily": "b1_random", "params": json.loads(json.dumps(dict(p.params))), "d_reference": None,
                    "reference": None, "tier": "standard",
                    "meta": {"b1_spec": spec.model_dump(mode="json"), "draw_index": draw}})
    return out


def _cyclic_rep(L):
    import numpy as np
    h = np.zeros((L, L), dtype=np.uint8)
    for i in range(L):
        h[i, i] = h[i, (i + 1) % L] = 1
    return h


def _hamming(r):
    import numpy as np
    cols = [[(c >> i) & 1 for i in range(r)] for c in range(1, 2**r)]
    return np.array(cols, dtype=np.uint8).T.copy()


def _rotated_surface(L):
    """Rotated surface code [[L^2, 1, L]], L odd: weight-4 bulk plaquettes in a checkerboard,
    weight-2 X plaquettes on the top/bottom boundaries and Z plaquettes on the left/right."""
    import numpy as np
    q = lambda i, j: i * L + j                                      # noqa: E731
    xs, zs = [], []
    for i in range(-1, L):
        for j in range(-1, L):
            sup = [q(a, b) for a in (i, i + 1) for b in (j, j + 1) if 0 <= a < L and 0 <= b < L]
            is_x = (i + j) % 2 == 0
            bulk = 0 <= i < L - 1 and 0 <= j < L - 1
            if bulk:
                (xs if is_x else zs).append(sup)
            elif len(sup) == 2:
                if (i in (-1, L - 1)) and 0 <= j < L - 1 and is_x:
                    xs.append(sup)
                elif (j in (-1, L - 1)) and 0 <= i < L - 1 and not is_x:
                    zs.append(sup)
    mk = lambda rows: np.array([[1 if c in r else 0 for c in range(L * L)] for r in rows],  # noqa
                               dtype=np.uint8)
    return mk(xs), mk(zs)


def _golay23():
    import numpy as np
    from zkqec.kernel import gf2
    g = [1, 0, 1, 0, 1, 1, 1, 0, 0, 0, 1, 1]                        # 1+x^2+x^4+x^5+x^6+x^10+x^11
    G = np.zeros((12, 23), dtype=np.uint8)
    for i in range(12):
        G[i, i:i + 12] = g
    h = gf2.nullspace(G)                                             # C^perp, [23,11,8] in C
    return h, h


def _explicit_codes(tiny: bool = False) -> list[dict]:
    import numpy as np
    from zkqec.kernel.codes import hypergraph_product, repetition_checks, steane
    st = steane()
    out = [_explicit("steane7", st.hx, st.hz, subfamily="hamming_css", d_ref=3,
                     ref="Steane [[7,1,3]]", tier="flagship")]
    if tiny:
        return out
    for r in (4, 5, 6, 7):
        h = _hamming(r)
        out.append(_explicit(f"qhamming{2**r - 1}", h, h, subfamily="hamming_css", d_ref=3,
                             ref=f"quantum Hamming CSS(H_{r}, H_{r})"))
    gh = _golay23()
    out.append(_explicit("golay23", *gh, subfamily="golay_css", d_ref=7,
                         ref="CSS from the [23,12,7] Golay code", tier="flagship"))
    for L in (3, 5, 7, 9, 11, 13):
        hx, hz = _rotated_surface(L)
        out.append(_explicit(f"rsurface{L}", hx, hz, subfamily="rotated_surface", d_ref=L,
                             ref=f"rotated surface [[{L*L},1,{L}]]",
                             tier="flagship" if L == 7 else "standard"))
    for L in range(3, 11):
        c = hypergraph_product(repetition_checks(L), repetition_checks(L))
        out.append(_explicit(f"psurface{L}", c.hx, c.hz, subfamily="planar_surface", d_ref=L,
                             ref=f"planar surface HGP(rep{L}, rep{L})",
                             tier="flagship" if L == 5 else "standard"))
    for L in range(3, 11):
        c = hypergraph_product(_cyclic_rep(L), _cyclic_rep(L))
        out.append(_explicit(f"toric{L}", c.hx, c.hz, subfamily="toric", d_ref=L,
                             ref=f"toric [[{2*L*L},2,{L}]]",
                             tier="flagship" if L == 5 else "standard"))
    h7 = _hamming(3)
    e8 = np.vstack([np.hstack([h7, np.zeros((3, 1), np.uint8)]), np.ones((1, 8), np.uint8)])
    cyc7 = np.zeros((7, 7), dtype=np.uint8)
    for i in range(7):
        for s in (0, 1, 3):                                          # g(x) = 1 + x + x^3
            cyc7[i, (i + s) % 7] = 1
    for cid, h1, h2, d, ref in (
            ("hgp_ham7", h7, h7, 3, "HGP(H_3, H_3), H_3 the [7,4,3] Hamming checks"),
            ("hgp_eham8", e8, e8, 4, "HGP of the [8,4,4] extended Hamming checks"),
            ("hgp_cycham7", cyc7, cyc7, 4, "HGP of the 7x7 circulant of 1+x+x^3 (rank 4; "
                                           "kernel = [7,3,4] simplex code)")):
        c = hypergraph_product(h1, h2)
        out.append(_explicit(cid, c.hx, c.hz, subfamily="hypergraph_product", d_ref=d, ref=ref,
                             tier="flagship" if cid == "hgp_eham8" else "standard"))
    return out


def catalogue(mode: str) -> list[dict]:
    """Build every code through the production grammar; record n, k, ranks and weights."""
    import numpy as np
    from zkqec.contracts import Proposal
    from zkqec.grammar import GrammarCaps, build
    from zkqec.kernel import gf2
    from zkqec.pipeline import _dense
    if mode == "tiny":
        specs = _explicit_codes(tiny=True) + [_bb_codes()[0]]
    else:
        specs = _bb_codes() + _chain_codes() + _b1_codes(B1_GRID) + _explicit_codes()
    out = []
    for c in specs:
        pres, _, _ = build(Proposal(family=c["family"], params=c["params"]), GrammarCaps(max_n=200))
        hx, hz = _dense(pres)
        n = int(hx.shape[1])
        if np.any(hx.astype(np.int64) @ hz.T.astype(np.int64) % 2):
            raise RuntimeError(f"{c['code_id']} is not CSS-valid")
        rx, rz = gf2.rank(hx), gf2.rank(hz)
        deg = (hx.sum(axis=0) + hz.sum(axis=0)).max()
        c.update(n=n, k=n - rx - rz, rank_hx=rx, rank_hz=rz, rows_hx=int(hx.shape[0]),
                 rows_hz=int(hz.shape[0]),
                 max_check_weight=int(max(hx.sum(axis=1).max(initial=0),
                                          hz.sum(axis=1).max(initial=0))),
                 max_qubit_degree=int(deg))
        if c["k"] < 1:
            raise RuntimeError(f"{c['code_id']} has k = {c['k']}")
        out.append(c)
    return out


# ================================================================================================
# Planning
# ================================================================================================

def production_budget() -> dict:
    cfg = json.loads((REPO / PRODUCTION_BUDGET_SOURCE).read_text())
    from zkqec.verify import DistanceBudget
    from dataclasses import asdict
    return asdict(DistanceBudget(**cfg.get("distance_budget", {})))


def _syndrome_rank(code: dict, sector: str) -> int:
    return code["rank_hz"] if sector == "X" else code["rank_hx"]


def preflights(code: dict, t: int, sector: str, budget: dict) -> dict:
    """The exact §C1 preflight certify_distance runs for this case (same functions, same inputs)."""
    from zkqec.verify import DistanceBudget
    from zkqec.verify.distance import preflight, preprocess_estimate
    pre = preprocess_estimate(code["rows_hx"] + code["rows_hz"], code["n"])
    return preflight(code["n"], code["k"], _syndrome_rank(code, sector), t,
                     DistanceBudget(**budget), preprocess_bytes=pre)


def predicted_cpu(m: int, pair_lb: int) -> float:
    return PRIOR_FIXED_S + PRIOR_S_PER_PATTERN * m + PRIOR_S_PER_PAIR * pair_lb


def plan(codes: list[dict], caps: dict, prod: dict) -> tuple[list[dict], list[dict]]:
    """Sector runs to execute and skipped rows (preflight only). For each code and sector, t runs
    from 1 while the case fits the caps; the first t that does not fit is recorded as skipped,
    as is every larger t up to the one a reference distance would need for an exact result."""
    meas_budget = {"max_patterns": caps["max_patterns"], "max_pairs": caps["max_pairs"],
                   "max_memory_bytes": caps["case_mem_gib"] * GiB, "max_seconds": None}
    runs, skipped = [], []
    for c in codes:
        t_ref = math.ceil(c["d_reference"] / 2) if c["d_reference"] else None
        mem_cap = caps["case_mem_gib"] if c["tier"] == "flagship" else caps["big_tier_gib"]
        for s in ("X", "Z"):
            t_hi = min(caps["max_t"], -(-c["n"] // 2))      # 2t >= n finds every logical
            cpu_cap = caps["max_pred_cpu_s"]
            if c["n"] < caps["small_n"]:                    # small codes: pair-bound, little news
                cpu_cap = min(cpu_cap, caps["small_n_cpu_s"])
                if t_ref is not None:
                    t_hi = min(t_hi, t_ref + 1)
            stopped = False
            for t in range(1, t_hi + 1):
                pre = preflights(c, t, s, meas_budget)
                prod_pre = preflights(c, t, s, prod)
                cpu = predicted_cpu(pre["half_patterns"], pre["pair_work_lower_bound"])
                fits = (pre["ok"] and pre["memory_bytes_estimate"] <= mem_cap * GiB
                        and cpu <= cpu_cap)
                case = {"code_id": c["code_id"], "t": t, "sector": s, "preflight": pre,
                        "production_preflight": prod_pre, "budget": meas_budget,
                        "predicted_cpu_s": cpu}
                if fits and not stopped:
                    runs.append(case)
                    continue
                if not stopped or (t_ref is not None and t <= t_ref):
                    reason = ("; ".join(pre["reasons"]) if not pre["ok"] else
                              f"memory estimate {pre['memory_bytes_estimate']} B > tier cap "
                              f"{mem_cap} GiB" if pre["memory_bytes_estimate"] > mem_cap * GiB
                              else f"predicted CPU {cpu:.0f} s > {cpu_cap:.0f} s")
                    skipped.append({**case, "skip_reason": reason})
                stopped = True
                if t_ref is None or t >= t_ref:
                    break
    return runs, skipped


# ================================================================================================
# Child process
# ================================================================================================

class _DictSink:
    def __init__(self):
        self.blobs: dict[str, Any] = {}

    def __call__(self, obj) -> str:
        from zkqec.contracts import content_hash
        sha = content_hash(obj)
        self.blobs[sha] = json.loads(json.dumps(obj))
        return sha

    def get(self, sha):
        return self.blobs[sha]


def _vm(field: str) -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1]) * 1024
    return 0


def _summarize(results, get) -> dict:
    """Per-sector outcome from VerifierResults plus their completed-range records."""
    out: dict[str, dict] = {}
    for r in results:
        s = r.proposition.args["sector"]
        e = out.setdefault(s, {"completion": r.completion, "lower": None, "upper": None,
                               "record": None, "reason": None})
        if r.status == "verified":
            if r.proposition.predicate == "distance_lower_bound":
                e["lower"] = r.proposition.args["d"]
            else:
                e["upper"] = r.proposition.args["d"]
        elif r.status == "unknown":
            e["completion"] = r.completion
            e["reason"] = r.detail.get("reason") or r.detail.get("outcome")
        if r.artifacts and e["record"] is None:
            rec = get(r.artifacts[0])
            e["record"] = {k: rec.get(k) for k in ("completion", "half_patterns", "pairs_checked",
                                                   "w_star", "lower_bound", "upper_bound", "t")}
            e["reason"] = e["reason"] or rec.get("reason")
    return out


def _audit_hook(obs: dict):
    from zkqec.kernel import distance as kd
    target = kd.sector_distance.__code__

    def local(frame, event, arg):
        if event == "return":
            b = frame.f_locals.get("buckets") or {}
            obs["entries"] = sum(len(v) for v in b.values())
            obs["distinct_syndromes"] = len(b)
            obs["pairs_from_buckets"] = sum(len(v) * (len(v) - 1) // 2 for v in b.values())
            obs["pairs_counter"] = frame.f_locals.get("pairs")
            obs["returned"] = True
        return local

    def glob(frame, event, arg):
        if frame.f_code is target:
            frame.f_trace_lines = False
            return local
        return None
    return glob


def child_main() -> None:
    spec = json.loads(sys.stdin.read())
    import resource
    if spec.get("rlimit_as"):
        resource.setrlimit(resource.RLIMIT_AS, (spec["rlimit_as"], spec["rlimit_as"]))
    import gc
    from zkqec.contracts import Proposal
    from zkqec.grammar import GrammarCaps, build
    from zkqec.kernel.codes import steane
    from zkqec.pipeline import _dense
    from zkqec.verify import DistanceBudget, certify_distance
    sink = _DictSink()
    st = steane()
    certify_distance(st.hx, st.hz, 1, artifact_sink=sink)          # warm-up: every lazy path
    pres, _, _ = build(Proposal(family=spec["family"], params=spec["params"]),
                       GrammarCaps(max_n=200))
    hx, hz = _dense(pres)                                           # the l2 adapter's conversion
    budget = DistanceBudget(**spec["budget"])
    t, kind = spec["t"], spec["kind"]
    out: dict[str, Any] = {"kind": kind}
    if kind in ("sector", "audit"):
        sectors = (spec["sector"],)
        c0, w0 = time.process_time(), time.perf_counter()
        certify_distance(hx, hz, 0, artifact_sink=sink, budget=budget, sectors=sectors)
        out["overhead_cpu_s"] = time.process_time() - c0
        out["overhead_wall_s"] = time.perf_counter() - w0
        obs: dict[str, Any] = {}
        gc.collect()
        rss0 = _vm("VmRSS")
        if kind == "audit":
            sys.settrace(_audit_hook(obs))
        c0, w0 = time.process_time(), time.perf_counter()
        try:
            res = certify_distance(hx, hz, t, artifact_sink=sink, budget=budget, sectors=sectors)
        finally:
            sys.settrace(None)
        cpu, wall = time.process_time() - c0, time.perf_counter() - w0
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        out["sectors"] = _summarize(res, sink.get)
        if kind == "audit":
            out["audit"] = obs
        else:
            out.update(cpu_s=cpu, wall_s=wall, rss_before_bytes=rss0, peak_rss_bytes=peak,
                       peak_rss_delta_bytes=max(0, peak - rss0))
    elif kind == "task":
        import tempfile
        from zkqec.admission.registry import ArtifactInput
        from zkqec.contracts import canonical_json
        from zkqec.pipeline import build_registry
        from zkqec.store import ArtifactStore
        with tempfile.TemporaryDirectory(prefix="zkqec-feas-") as d:
            store = ArtifactStore(d)
            fn = build_registry(store).get("l2.distance", "verifier").fn
            pobj = pres.model_dump(mode="json")
            qobj = {"half_weight": t, "budget": spec["budget"]}
            p_in = ArtifactInput(store.put_json(pobj), canonical_json(pobj))
            q_in = ArtifactInput(store.put_json(qobj), canonical_json(qobj))
            gc.collect()
            rss0 = _vm("VmRSS")
            c0, w0 = time.process_time(), time.perf_counter()
            res = fn(presentation=p_in, params=q_in)
            cpu, wall = time.process_time() - c0, time.perf_counter() - w0
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            out["sectors"] = _summarize(res, store.get_json)
            out.update(cpu_s=cpu, wall_s=wall, rss_before_bytes=rss0, peak_rss_bytes=peak,
                       peak_rss_delta_bytes=max(0, peak - rss0))
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()


# ================================================================================================
# Parent: scheduler
# ================================================================================================

def _child_env() -> dict:
    env = dict(os.environ)
    env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
               NUMEXPR_NUM_THREADS="1", PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = str(REPO / "src") + (os.pathsep + env["PYTHONPATH"]
                                             if env.get("PYTHONPATH") else "")
    return env


def run_child(spec: dict, timeout: float) -> dict:
    """One fresh subprocess; returns its JSON plus the kernel's rusage for that child (wait4)."""
    t0 = time.perf_counter()
    p = subprocess.Popen([sys.executable, str(SCRIPT), "--child"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_child_env(),
                         cwd=str(REPO))
    killed = threading.Event()

    def _kill():
        killed.set()
        p.kill()
    timer = threading.Timer(timeout, _kill)
    timer.start()
    chunks: dict[str, bytes] = {}

    def _drain(name, stream):
        chunks[name] = stream.read()
    readers = [threading.Thread(target=_drain, args=(n, s)) for n, s in
               (("out", p.stdout), ("err", p.stderr))]
    for r in readers:
        r.start()
    try:
        p.stdin.write(json.dumps(spec).encode())
        p.stdin.close()
    except BrokenPipeError:
        pass
    for r in readers:
        r.join()
    _, status, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(status)
    timer.cancel()
    res: dict[str, Any] = {"exit_code": p.returncode, "child_wall_s": time.perf_counter() - t0,
                           "child_cpu_s": ru.ru_utime + ru.ru_stime,
                           "child_maxrss_bytes": ru.ru_maxrss * 1024}
    try:
        res["result"] = json.loads(chunks.get("out") or b"null")
    except json.JSONDecodeError:
        res["result"] = None
    if killed.is_set():
        res["error"] = f"measurement timeout after {timeout} s"
    elif p.returncode != 0 or res["result"] is None:
        res["error"] = (chunks.get("err") or b"").decode(errors="replace")[-2000:]
    return res


def _log(msg: str) -> None:
    print(msg, flush=True)


def schedule(jobs: list[dict], *, workers: int, mem_pool: int, timeout: float,
             log=_log) -> list[dict]:
    """Longest-predicted-first, memory-aware: a job starts only when its reservation fits."""
    pending = sorted(jobs, key=lambda j: -j["predicted_cpu_s"])
    lock = threading.Condition()
    state = {"running": 0, "mem": 0, "done": 0}
    total = len(pending)
    results: list[dict] = []

    def worker(job):
        try:
            r = run_child(job["spec"], timeout)
        except Exception as exc:                                     # noqa: BLE001
            r = {"error": f"{type(exc).__name__}: {exc}", "result": None}
        with lock:
            results.append({**job, "run": r})
            state["running"] -= 1
            state["mem"] -= job["reserve_bytes"]
            state["done"] += 1
            if log and (state["done"] % 25 == 0 or state["done"] == total or "error" in r):
                log(f"  {state['done']}/{total} done; running {state['running']}, "
                    f"reserved {state['mem'] / GiB:.1f} GiB"
                    + (f"; ERROR {job['spec']['code_id']} t={job['spec']['t']}: "
                       f"{r['error'][:200]}" if "error" in r else ""))
            lock.notify_all()

    with lock:
        while pending or state["running"]:
            started = False
            for j in list(pending):
                if state["running"] >= workers:
                    break
                if state["mem"] + j["reserve_bytes"] <= mem_pool or state["running"] == 0:
                    pending.remove(j)
                    state["running"] += 1
                    state["mem"] += j["reserve_bytes"]
                    threading.Thread(target=worker, args=(j,), daemon=True).start()
                    started = True
            if not started:
                lock.wait(timeout=5)
    return results


# ================================================================================================
# Host facts
# ================================================================================================

def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO),
                              timeout=30).stdout.strip()
    except Exception:                                                # noqa: BLE001
        return ""


def host_facts() -> dict:
    import numpy
    import scipy
    from zkqec.pipeline import KERNEL_VERSION
    from zkqec.verify.distance import VERIFIER
    cpuinfo = Path("/proc/cpuinfo").read_text()
    model = next((ln.split(":", 1)[1].strip() for ln in cpuinfo.splitlines()
                  if ln.startswith("model name")), platform.processor())
    phys = {(ln_pair[0], ln_pair[1]) for ln_pair in _core_ids(cpuinfo)}
    mem = next(int(ln.split()[1]) * 1024 for ln in Path("/proc/meminfo").read_text().splitlines()
               if ln.startswith("MemTotal:"))
    blas = {}
    try:
        cfg = numpy.show_config(mode="dicts")
        blas = {k: cfg["Build Dependencies"][k].get("openblas configuration")
                or cfg["Build Dependencies"][k].get("name")
                for k in ("blas", "lapack")}
    except Exception:                                                # noqa: BLE001
        pass
    fp = {}
    for rel in FINGERPRINT_FILES:
        fp[rel] = hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
    dirty = _sh(["git", "status", "--porcelain", "--", *FINGERPRINT_FILES])
    return {"cpu_model": model, "logical_cpus": os.cpu_count(), "physical_cores": len(phys) or None,
            "sockets": len({s for s, _ in phys}) or None, "ram_bytes": mem,
            "os": platform.platform(), "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "numpy": numpy.__version__, "scipy": scipy.__version__, "blas": blas,
            "git_commit": _sh(["git", "rev-parse", "HEAD"]),
            "git_branch": _sh(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
            "measured_sources_dirty": bool(dirty), "source_fingerprints": fp,
            "l2_verifier": {"name": VERIFIER.name, "version": VERIFIER.version},
            "pipeline_registry": {"l2.distance": "1.1.0"}, "kernel_version": KERNEL_VERSION,
            "child_env": {k: v for k, v in _child_env().items()
                          if k.endswith("_NUM_THREADS") or k == "PYTHONHASHSEED"}}


def _core_ids(cpuinfo: str):
    phys = core = None
    for ln in cpuinfo.splitlines():
        if ln.startswith("physical id"):
            phys = ln.split(":")[1].strip()
        elif ln.startswith("core id"):
            core = ln.split(":")[1].strip()
        elif not ln.strip() and phys is not None:
            yield (phys, core)
            phys = core = None


# ================================================================================================
# Assembly
# ================================================================================================

def _interval(sec: dict | None) -> list | None:
    if not sec or sec.get("completion") != "completed":
        return None
    return [sec["lower"], sec["upper"]]


def build_rows(codes, runs_done, audits_done, skipped) -> list[dict]:
    by_code = {c["code_id"]: c for c in codes}
    audit_idx = {(a["spec"]["code_id"], a["spec"]["t"], a["spec"]["sector"]): a for a in audits_done}
    rows = []
    for j in runs_done + [{"case": s, "run": None} for s in skipped]:
        case = j["case"]
        c = by_code[case["code_id"]]
        pre, ppre = case["preflight"], case["production_preflight"]
        row = {"code_id": c["code_id"], "family": c["family"], "subfamily": c["subfamily"],
               "n": c["n"], "k": c["k"], "t": case["t"], "sector": case["sector"],
               "syndrome_rank": pre["syndrome_rank"], "half_patterns_M": pre["half_patterns"],
               "memory_bytes_estimate": pre["memory_bytes_estimate"],
               "pair_work_lower_bound": pre["pair_work_lower_bound"],
               "pair_work_upper_bound": pre["pair_work_upper_bound"],
               "measurement_budget": case["budget"],
               "production_preflight_ok": ppre["ok"],
               "production_preflight_reasons": ppre["reasons"]}
        run = j["run"]
        if run is None:
            row.update(outcome="skipped", skip_reason=case["skip_reason"], measured=None,
                       kernel_half_patterns=None, pairs_checked=None, w_star=None,
                       interval=None, audit=None, production_outcome="refused_preflight"
                       if not ppre["ok"] else "not_measured")
            rows.append(row)
            continue
        res = run.get("result") or {}
        sec = (res.get("sectors") or {}).get(case["sector"])
        rec = (sec or {}).get("record") or {}
        if run.get("error"):
            outcome = "measurement_error"
        else:
            outcome = sec["completion"] if sec else "measurement_error"
        row.update(outcome=outcome, skip_reason=None,
                   kernel_half_patterns=rec.get("half_patterns"),
                   pairs_checked=rec.get("pairs_checked"), w_star=rec.get("w_star"),
                   interval=_interval(sec), failure_reason=(sec or {}).get("reason")
                   if outcome != "completed" else None,
                   error=run.get("error"))
        row["measured"] = {k: res.get(k) for k in ("cpu_s", "wall_s", "overhead_cpu_s",
                                                    "overhead_wall_s", "peak_rss_bytes",
                                                    "peak_rss_delta_bytes", "rss_before_bytes")}
        row["measured"].update({k: run.get(k) for k in ("child_cpu_s", "child_wall_s",
                                                         "child_maxrss_bytes")})
        a = audit_idx.get((c["code_id"], case["t"], case["sector"]))
        if a is not None and a["run"].get("result"):
            ar = a["run"]["result"]
            asec = (ar.get("sectors") or {}).get(case["sector"]) or {}
            obs = ar.get("audit") or {}
            row["audit"] = {"patterns_enumerated": obs.get("entries"),
                            "distinct_syndromes": obs.get("distinct_syndromes"),
                            "pairs_from_bucket_sizes": obs.get("pairs_from_buckets"),
                            "kernel_pair_counter": obs.get("pairs_counter"),
                            "completion": asec.get("completion"),
                            "interval": _interval(asec)}
        else:
            row["audit"] = None
        pairs = row["pairs_checked"]
        prod = ppre["ok"] and outcome == "completed" and pairs is not None \
            and pairs <= ppre["budget"]["max_pairs"]
        row["production_outcome"] = ("refused_preflight" if not ppre["ok"] else
                                     "completed" if prod else
                                     "pair_budget_exhausted" if outcome == "completed"
                                     else "unknown")
        rows.append(row)
    rows.sort(key=lambda r: (r["family"], r["subfamily"], r["n"], r["code_id"], r["t"],
                             r["sector"]))
    return rows


def build_tasks(rows: list[dict], task_runs: list[dict]) -> list[dict]:
    """Both-sector task view: CPU = sum of the sectors, peak = max, outcome = both completed."""
    idx: dict[tuple, dict] = {}
    for r in rows:
        idx.setdefault((r["code_id"], r["t"]), {})[r["sector"]] = r
    task_idx = {(j["spec"]["code_id"], j["spec"]["t"]): j for j in task_runs}
    out = []
    for (cid, t), secs in sorted(idx.items()):
        x, z = secs.get("X"), secs.get("Z")
        if x is None or z is None:
            continue
        both = x["outcome"] == z["outcome"] == "completed"
        meas = None
        if x["measured"] and z["measured"] and both:
            meas = {"cpu_s": x["measured"]["cpu_s"] + z["measured"]["cpu_s"],
                    "wall_s": x["measured"]["wall_s"] + z["measured"]["wall_s"],
                    "peak_rss_delta_bytes": max(x["measured"]["peak_rss_delta_bytes"],
                                                z["measured"]["peak_rss_delta_bytes"])}
        dx, dz = x["interval"], z["interval"]
        d_both = [min(dx[0], dz[0]), min(dx[1], dz[1])] if dx and dz else None
        task = {"code_id": cid, "family": x["family"], "subfamily": x["subfamily"], "n": x["n"],
                "k": x["k"], "t": t, "half_patterns_M": x["half_patterns_M"],
                "outcome": "completed" if both else
                ("skipped" if "skipped" in (x["outcome"], z["outcome"]) else "incomplete"),
                "certified_d_lower_both": d_both[0] if d_both else None, "interval_both": d_both,
                "pairs_checked": [x["pairs_checked"], z["pairs_checked"]],
                "production_outcome": ("completed" if x["production_outcome"] ==
                                       z["production_outcome"] == "completed" else
                                       "refused_preflight" if "refused_preflight" in
                                       (x["production_outcome"], z["production_outcome"])
                                       else "not_measured" if "not_measured" in
                                       (x["production_outcome"], z["production_outcome"])
                                       else "unknown"),
                "measured_sum_of_sectors": meas, "measured_task_run": None}
        tr = task_idx.get((cid, t))
        if tr is not None and tr["run"].get("result"):
            res = tr["run"]["result"]
            task["measured_task_run"] = {
                "cpu_s": res.get("cpu_s"), "wall_s": res.get("wall_s"),
                "peak_rss_delta_bytes": res.get("peak_rss_delta_bytes"),
                "completion": {s: v["completion"] for s, v in (res.get("sectors") or {}).items()},
                "intervals": {s: _interval(v) for s, v in (res.get("sectors") or {}).items()}}
        out.append(task)
    return out


# ================================================================================================
# Fits and frontiers
# ================================================================================================

def fit_models(rows: list[dict]) -> dict:
    """CPU (enumeration part) ~ a*M + b*M*t + c*pairs + e*M*n/100, nonnegative least squares with
    relative weights; memory ~ bytes per pattern (peak RSS delta / M) for M >= 1e6."""
    import numpy as np
    from scipy.optimize import nnls
    pts = [r for r in rows if r["outcome"] == "completed" and r["measured"]
           and r["measured"]["cpu_s"] - r["measured"]["overhead_cpu_s"] >= 0.5]
    out: dict[str, Any] = {"cpu_model": "cpu_s - overhead_cpu_s = a*M + b*M*t + c*pairs + "
                                        "e*M*n/100 (NNLS, rows weighted by 1/y)",
                           "cpu_fit_points": len(pts)}
    if len(pts) >= 4:
        X = np.array([[r["half_patterns_M"], r["half_patterns_M"] * r["t"], r["pairs_checked"],
                       r["half_patterns_M"] * r["n"] / 100] for r in pts], dtype=float)
        y = np.array([r["measured"]["cpu_s"] - r["measured"]["overhead_cpu_s"] for r in pts])
        w = 1.0 / y
        coef, _ = nnls(X * w[:, None], y * w)
        pred = X @ coef
        rel = pred / y - 1
        out.update(a_s_per_pattern=coef[0], b_s_per_pattern_per_t=coef[1], c_s_per_pair=coef[2],
                   e_s_per_pattern_per_100n=coef[3],
                   rel_error_median=float(np.median(np.abs(rel))),
                   rel_error_max=float(np.max(np.abs(rel))))
    mem_pts = [r for r in rows if r["outcome"] == "completed" and r["measured"]
               and r["half_patterns_M"] >= 1_000_000]
    if mem_pts:
        bpp = np.array([r["measured"]["peak_rss_delta_bytes"] / r["half_patterns_M"]
                        for r in mem_pts])
        ratio = np.array([r["memory_bytes_estimate"] / max(1, r["measured"]["peak_rss_delta_bytes"])
                          for r in mem_pts])
        out.update(memory_points=len(mem_pts), bytes_per_pattern_min=float(bpp.min()),
                   bytes_per_pattern_median=float(np.median(bpp)),
                   bytes_per_pattern_max=float(bpp.max()),
                   memory_estimate_over_actual_min=float(ratio.min()),
                   memory_estimate_over_actual_median=float(np.median(ratio)),
                   memory_estimate_over_actual_max=float(ratio.max()))
    # Noise: X and Z runs of the same task that did identical work (same M, same pair count).
    idx: dict[tuple, dict] = {}
    for r in rows:
        if r["outcome"] == "completed" and r["measured"] and r["half_patterns_M"] >= 1_000_000:
            idx.setdefault((r["code_id"], r["t"]), {})[r["sector"]] = r
    rat = sorted(max(x["measured"]["cpu_s"], z["measured"]["cpu_s"])
                 / min(x["measured"]["cpu_s"], z["measured"]["cpu_s"])
                 for x, z in ((v.get("X"), v.get("Z")) for v in idx.values())
                 if x and z and x["pairs_checked"] == z["pairs_checked"])
    if rat:
        out.update(sector_twin_points=len(rat), sector_twin_cpu_ratio_median=rat[len(rat) // 2],
                   sector_twin_cpu_ratio_p90=rat[int(0.9 * (len(rat) - 1))],
                   sector_twin_cpu_ratio_max=rat[-1])
    ov = [r["measured"]["overhead_cpu_s"] for r in rows if r["measured"]
          and r["measured"].get("overhead_cpu_s") is not None]
    if ov:
        out.update(overhead_cpu_s_median=float(np.median(ov)), overhead_cpu_s_max=float(max(ov)))
    return out


def predict_sector_cpu(fit: dict, n: int, t: int, m: int, pairs: int = 0) -> float:
    return (fit.get("overhead_cpu_s_max", PRIOR_FIXED_S)
            + m * (fit["a_s_per_pattern"] + fit["b_s_per_pattern_per_t"] * t
                   + fit["e_s_per_pattern_per_100n"] * n / 100)
            + pairs * fit["c_s_per_pair"])


def measured_frontier(tasks: list[dict]) -> dict:
    """Per family and per candidate budget: for each t, the largest measured n whose task (both
    sectors) completed within the CPU budget with its peak RSS delta within the memory budget."""
    out: dict[str, Any] = {}
    fams = sorted({t["family"] for t in tasks})
    for fam in fams + ["all"]:
        for cpu in CPU_BUDGETS:
            for mem in MEM_BUDGETS_GIB:
                key = f"{fam}|{cpu}s|{mem}GiB"
                best: dict[int, dict] = {}
                for tk in tasks:
                    m = tk["measured_sum_of_sectors"]
                    if (fam != "all" and tk["family"] != fam) or tk["outcome"] != "completed" \
                            or m is None:
                        continue
                    if m["cpu_s"] <= cpu and m["peak_rss_delta_bytes"] <= mem * GiB:
                        cur = best.get(tk["t"])
                        if cur is None or tk["n"] > cur["n"] or (
                                tk["n"] == cur["n"] and m["cpu_s"] > cur["cpu_s"]):
                            best[tk["t"]] = {"n": tk["n"], "code_id": tk["code_id"],
                                             "cpu_s": m["cpu_s"], "M": tk["half_patterns_M"]}
                out[key] = {str(t): v for t, v in sorted(best.items())}
    return out


def model_frontier(fit: dict, pairs_per_pattern: float, margin: float) -> dict:
    """Largest n <= 200 per (t, budget) predicted to complete a task (both sectors) within
    budget/margin, with the production memory estimate within the memory budget."""
    from zkqec.verify.distance import memory_estimate, preprocess_estimate
    if "a_s_per_pattern" not in fit:
        return {}
    out: dict[str, Any] = {}
    for cpu in CPU_BUDGETS:
        for mem in MEM_BUDGETS_GIB:
            row = {}
            for t in range(1, 9):
                n_ok = None
                for n in range(8, 201):
                    m = sum(comb(n, j) for j in range(t + 1))
                    r = n // 2
                    est = memory_estimate(n, max(1, n // 8), r, m) + preprocess_estimate(n, n)
                    c = 2 * predict_sector_cpu(fit, n, t, m, int(pairs_per_pattern * m))
                    if c * margin <= cpu and est <= mem * GiB:
                        n_ok = n
                row[str(t)] = n_ok
            out[f"{cpu}s|{mem}GiB"] = row
    return out


def tier_tables(fit: dict, tasks: list[dict]) -> list[dict]:
    """Per candidate tier and t: the largest n <= 200 whose M fits max_patterns (and whose
    production memory estimate, at syndrome rank n/2 and k = n/8, fits max_memory_bytes); the
    fitted worst-case task CPU (both sectors at the pattern and pair caps, plus the largest
    measured fixed cost); and the measured task with the largest CPU that falls in the region."""
    from zkqec.verify.distance import memory_estimate, preprocess_estimate
    if "a_s_per_pattern" not in fit:
        return []
    out = []
    for tier in TIERS:
        per_t = []
        for t in range(1, 9):
            n_ok = None
            for n in range(max(2 * t, 8), 201):
                m = sum(comb(n, j) for j in range(t + 1))
                est = memory_estimate(n, max(1, n // 8), n // 2, m) + preprocess_estimate(n, n)
                if m <= tier["max_patterns"] and est <= tier["max_memory_bytes"]:
                    n_ok = n
            if n_ok is None:
                continue
            m = sum(comb(n_ok, j) for j in range(t + 1))
            worst = 2 * predict_sector_cpu(fit, n_ok, t, m, tier["max_pairs"])
            est = memory_estimate(n_ok, max(1, n_ok // 8), n_ok // 2, m) \
                + preprocess_estimate(n_ok, n_ok)
            inside = [tk for tk in tasks if tk["t"] == t and tk["outcome"] == "completed"
                      and tk["measured_sum_of_sectors"] and tk["n"] <= n_ok
                      and tk["half_patterns_M"] <= tier["max_patterns"]
                      and max(tk["pairs_checked"]) <= tier["max_pairs"]]
            capped = [tk["code_id"] for tk in tasks if tk["t"] == t and tk["n"] <= n_ok
                      and tk["outcome"] == "completed"
                      and tk["half_patterns_M"] <= tier["max_patterns"]
                      and max(tk["pairs_checked"]) > tier["max_pairs"]]
            top = max(inside, key=lambda tk: tk["measured_sum_of_sectors"]["cpu_s"], default=None)
            per_t.append({
                "t": t, "certifies_d_ge": 2 * t + 1, "n_max": n_ok, "M_at_n_max": m,
                "memory_estimate_at_n_max": est, "predicted_worst_task_cpu_s": worst,
                "cpu_margin": tier["cpu_s"] / worst if worst > 0 else None,
                "measured_inside": len(inside),
                "measured_max": None if top is None else {
                    "code_id": top["code_id"], "n": top["n"], "M": top["half_patterns_M"],
                    "cpu_s": top["measured_sum_of_sectors"]["cpu_s"],
                    "peak_rss_delta_bytes": top["measured_sum_of_sectors"]["peak_rss_delta_bytes"]},
                "measured_pair_capped": sorted(set(capped))})
        out.append({**tier, "per_t": per_t})
    return out


def derive(doc: dict) -> dict:
    """Every derived section, recomputed from the measured rows and tasks (deterministic)."""
    rows, tasks = doc["rows"], doc["tasks"]
    fits = fit_models(rows)
    doc["fits"] = fits
    doc["frontier_measured"] = measured_frontier(tasks)
    doc.pop("frontier_model", None)
    doc.pop("frontier_model_assumptions", None)
    if "a_s_per_pattern" in fits:
        pairs_allow = 0.5
        doc["frontier_model"] = model_frontier(fits, pairs_allow, margin=2.0)
        doc["frontier_model_assumptions"] = (
            f"both sectors, margin 2× on CPU, pairs = {pairs_allow}·M, syndrome rank n/2, "
            f"k = n/8, production memory estimate ≤ the memory budget")
    doc["tiers"] = tier_tables(fits, tasks)
    doc["anomalies"] = find_anomalies(doc["codes"], rows, tasks)
    return _clean(doc)


# ================================================================================================
# Markdown
# ================================================================================================

def _fmt_int(v) -> str:
    if v is None:
        return "—"
    v = int(v)
    if v >= 10**6:
        return f"{v / 1e6:.3g}M" if v < 10**9 else f"{v / 1e9:.3g}G"
    if v >= 10**4:
        return f"{v / 1e3:.3g}k"
    return str(v)


def _fmt_s(v) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}" if v < 10 else f"{v:.0f}"


def _fmt_b(v) -> str:
    if v is None:
        return "—"
    return f"{v / GiB:.2f} G" if v >= GiB else f"{v / MB:.0f} M"


def _iv(iv) -> str:
    if not iv:
        return "—"
    lo, hi = iv
    return f"{lo}" if lo == hi else f"[{lo},{hi}]"


SHORT = {"completed": "ok", "budget_exhausted": "pairs>cap", "skipped": "skip",
         "measurement_error": "ERR", "timeout": "timeout", "resource_killed": "oom",
         "crashed": "crash"}


def render_md(doc: dict) -> str:
    rows, tasks, fit, host = doc["rows"], doc["tasks"], doc["fits"], doc["host"]
    meas = doc["measurement"]
    L = []
    w = L.append
    w("# M1 measured certification feasibility tables")
    w("")
    w(f"Contracts §C1: measured on this host for the production L2 entry point "
      f"(`{host['l2_verifier']['name']}` {host['l2_verifier']['version']}, pipeline adapter "
      f"`l2.distance` {host['pipeline_registry']['l2.distance']}), commit "
      f"`{host['git_commit'][:12]}`. Generated by `scripts/feasibility_tables.py --mode "
      f"{doc['config']['mode']}`; the JSON next to this file holds every row.")
    w("")
    w("## Host and method")
    w("")
    w(f"- **Host:** {host['cpu_model']}, {host['logical_cpus']} logical CPUs "
      f"({host['physical_cores']} physical cores, {host['sockets']} sockets), "
      f"{host['ram_bytes'] / GiB:.0f} GiB RAM; {host['os']}.")
    w(f"- **Software:** {host['python_implementation']} {host['python']}, numpy {host['numpy']}, "
      f"scipy {host['scipy']}; kernel `{host['kernel_version']}`; measured sources dirty: "
      f"{host['measured_sources_dirty']}.")
    cfg = doc["config"]
    w(f"- **Runs:** one fresh subprocess per (code, t, sector); BLAS/OpenMP pools pinned to one "
      f"thread; at most {cfg['workers']} children at once, with the sum of their preflight "
      f"memory estimates ≤ {cfg['mem_pool_gib']} GiB. Per-case caps: memory estimate ≤ "
      f"{cfg['case_mem_gib']} GiB (≤ {cfg['big_tier_gib']} GiB for non-flagship codes), M ≤ "
      f"{_fmt_int(cfg['max_patterns'])}, kernel `max_pairs` = {_fmt_int(cfg['max_pairs'])}, "
      f"predicted CPU ≤ {cfg['max_pred_cpu_s']:.0f} s per sector.")
    w("- **Columns:** CPU and wall time cover the `certify_distance` call only, including its "
      "fixed per-call part (row reduction, CSS check, canonical basis, code hash, witness "
      "re-check). Peak RSS is the child's `ru_maxrss` minus its RSS just before the call. For "
      "small cases this delta has a floor of about 10–17 MB, which comes from transient "
      "allocations during interpreter start-up, not from the call. "
      "Pairs is the kernel's counter of same-syndrome pairs examined. `ok` means the run "
      "completed and certified the interval shown. `pairs>cap` means the kernel's pair cap "
      "stopped the run (`budget_exhausted`, unknown). `skip` means the preflight estimate "
      "exceeded the measurement caps, so the case was not run.")
    w(f"- **Total measurement:** {meas['wall_s'] / 60:.1f} min wall; "
      f"{meas['child_cpu_s'] / 3600:.2f} CPU-hours over {meas['children']} child processes; "
      f"largest child peak RSS {_fmt_b(meas['max_child_rss_bytes'])}.")
    w("")
    # ---- throughput & estimate ratios
    w("## Throughput and estimate-vs-actual")
    w("")
    if "a_s_per_pattern" in fit:
        a, b, c, e = (fit["a_s_per_pattern"], fit["b_s_per_pattern_per_t"], fit["c_s_per_pair"],
                      fit["e_s_per_pattern_per_100n"])
        w(f"Fitted on {fit['cpu_fit_points']} completed sector runs (enumeration CPU ≥ 0.5 s): "
          f"`cpu − fixed = M·({a * 1e6:.3f} + {b * 1e6:.3f}·t + {e * 1e6:.3f}·n/100) µs + "
          f"pairs·{c * 1e6:.3f} µs`. Median |relative error| {fit['rel_error_median']:.1%}, max "
          f"{fit['rel_error_max']:.1%}. The fixed per-call CPU (the `t = 0` call) has median "
          f"{fit['overhead_cpu_s_median']:.3f} s and max {fit['overhead_cpu_s_max']:.3f} s.")
        w("")
        w("| n | t | µs per pattern (fit) | patterns/s (fit) |")
        w("|---|---|---|---|")
        for n, t in ((72, 3), (72, 5), (144, 3), (144, 4), (200, 3), (200, 4)):
            us = (a + b * t + e * n / 100) * 1e6
            w(f"| {n} | {t} | {us:.2f} | {1e6 / us / 1e3:.0f}k |")
        w(f"| — | — | per pair: {c * 1e6:.3f} µs | pairs/s: {1 / c / 1e6:.2f}M |" if c > 0 else
          "| — | — | per pair: not resolved | — |")
        w("")
    if "sector_twin_points" in fit:
        w(f"**Noise.** In {fit['sector_twin_points']} tasks with M ≥ 10⁶, the X and Z runs did "
          f"identical work (same M, same pairs). The ratio of the slower to the faster run's "
          f"CPU is median {fit['sector_twin_cpu_ratio_median']:.2f}, p90 "
          f"{fit['sector_twin_cpu_ratio_p90']:.2f}, max {fit['sector_twin_cpu_ratio_max']:.2f}. "
          f"This is the run-to-run spread with up to 32 single-threaded children on 72 "
          f"physical cores, two hardware threads per core.")
        w("")
    obs = [r for r in rows if r["outcome"] == "completed" and r["measured"]
           and r["half_patterns_M"] >= 1_000_000]
    if obs:
        import numpy as np
        pps = np.array([r["half_patterns_M"] / r["measured"]["cpu_s"] for r in obs])
        w(f"Observed on the {len(obs)} completed runs with M ≥ 10⁶: patterns/s "
          f"(M / CPU, pair work included) min {pps.min() / 1e3:.0f}k, median "
          f"{np.median(pps) / 1e3:.0f}k, max {pps.max() / 1e3:.0f}k.")
        w("")
    if "memory_points" in fit:
        w(f"**Memory** ({fit['memory_points']} runs with M ≥ 10⁶). Measured peak RSS delta is "
          f"{fit['bytes_per_pattern_min']:.0f}–{fit['bytes_per_pattern_max']:.0f} B per "
          f"half-pattern (median {fit['bytes_per_pattern_median']:.0f}). The preflight estimate "
          f"divided by the measured delta is {fit['memory_estimate_over_actual_min']:.2f}–"
          f"{fit['memory_estimate_over_actual_max']:.2f} (median "
          f"{fit['memory_estimate_over_actual_median']:.2f}). A ratio ≥ 1 means the estimate is "
          f"conservative.")
        w("")
    pr = [r for r in rows if r["outcome"] == "completed" and r["pairs_checked"] is not None]
    if pr:
        lb_pos = [r for r in pr if r["pair_work_lower_bound"] > 0]
        vac = len(pr) - len(lb_pos)
        ppm = sorted(r["pairs_checked"] / r["half_patterns_M"] for r in pr)
        w(f"**Pairs.** {len(pr)} completed runs. In {vac} of them the preflight lower bound "
          f"on pair work is 0, because 2^rank ≥ M, so the bound says nothing; pairs then come "
          f"from the code's low-weight kernel elements. Measured pairs per half-pattern: median "
          f"{ppm[len(ppm) // 2]:.3g}, max {ppm[-1]:.3g}.")
        if lb_pos:
            rat = sorted(r["pairs_checked"] / r["pair_work_lower_bound"] for r in lb_pos)
            w(f"Where the lower bound is positive ({len(lb_pos)} runs), actual / lower bound is "
              f"{rat[0]:.2f}–{rat[-1]:.2f} (median {rat[len(rat) // 2]:.2f}).")
        top = sorted(pr, key=lambda r: -r["pairs_checked"] / r["half_patterns_M"])[:6]
        w("Highest pairs per pattern: " + "; ".join(
            f"{r['code_id']} t={r['t']} {r['sector']}: {r['pairs_checked'] / r['half_patterns_M']:.3g}"
            for r in top) + ".")
        w("")
    au = [r for r in rows if r.get("audit")]
    if au:
        ok_e = sum(1 for r in au if r["audit"]["patterns_enumerated"] == r["half_patterns_M"])
        ok_p = sum(1 for r in au if r["audit"]["pairs_from_bucket_sizes"] == r["pairs_checked"]
                   == r["audit"]["kernel_pair_counter"])
        w(f"**Audit** (bucket table read when the kernel returns; {len(au)} runs with M ≤ "
          f"{_fmt_int(cfg['audit_max_patterns'])}). Stored half-patterns equal the preflight M "
          f"in {ok_e}/{len(au)} runs. The pairs recomputed from bucket sizes, Σ C(M_s,2), equal "
          f"the kernel counter and the timing run's count in {ok_p}/{len(au)}. The kernel's "
          f"reported `half_patterns` is the formula Σ C(n,j), computed before enumeration, so "
          f"this audit is the only real count.")
        w("")
    # ---- frontier
    w("## Largest certifiable (n, t) per per-task budget (measured)")
    w("")
    w("A task certifies both sectors, as the pipeline does: CPU is the sum of the sector "
      "runs and memory is the larger sector peak. Each cell gives the largest measured n whose "
      "task completed within the budget at that t, with the code in parentheses. The model "
      "frontier further down covers the n that were not measured.")
    w("")
    fr = doc["frontier_measured"]
    ts = sorted({int(t) for v in fr.values() for t in v})
    for fam in sorted({t["family"] for t in tasks}) + ["all"]:
        w(f"**{fam}**")
        w("")
        w("| budget | " + " | ".join(f"t={t}" for t in ts) + " |")
        w("|---|" + "---|" * len(ts))
        for cpu in CPU_BUDGETS:
            for mem in MEM_BUDGETS_GIB:
                cell = fr.get(f"{fam}|{cpu}s|{mem}GiB", {})
                w(f"| {cpu} s × {mem} GiB | " + " | ".join(
                    f"{cell[str(t)]['n']} ({cell[str(t)]['code_id']})" if str(t) in cell else "—"
                    for t in ts) + " |")
        w("")
    mf = doc.get("frontier_model") or {}
    if mf:
        w(f"**Model frontier** (fit above; n ≤ 200; {doc['frontier_model_assumptions']}). Each "
          f"cell is the largest n for which a task at that t is predicted to complete:")
        w("")
        tt = sorted({int(t) for v in mf.values() for t in v})
        w("| budget | " + " | ".join(f"t={t} (d≥{2 * t + 1})" for t in tt) + " |")
        w("|---|" + "---|" * len(tt))
        for key, v in mf.items():
            cpu, mem = key.split("|")
            w(f"| {cpu} × {mem.replace('GiB', ' GiB')} | " + " | ".join(
                str(v[str(t)]) if v[str(t)] else "—" for t in tt) + " |")
        w("")
    tiers = doc.get("tiers") or []
    if tiers:
        w("## Candidate `distance_budget` tiers (fit + measured check)")
        w("")
        w("A tier is a per-task (both sectors) verifier budget plus the `distance_budget` that "
          "keeps the worst case inside it. n_max is the largest n ≤ 200 whose M fits "
          "`max_patterns`. Predicted worst is the fit with both sectors at the pattern and pair "
          "caps plus the largest measured fixed cost. Measured max is the largest-CPU measured "
          "task inside the region (n ≤ n_max, M ≤ `max_patterns`, pairs ≤ `max_pairs`). "
          "Pair-capped lists the measured tasks that are inside the region by M but whose "
          "pairs exceed `max_pairs`; the verifier would return `budget_exhausted` (unknown) "
          "for them.")
        w("")
        for tier in tiers:
            mem = f", {tier['mem_gib']} GiB" if tier["mem_gib"] else ""
            w(f"**Tier {tier['name']}**: per-task budget {tier['cpu_s']} s CPU{mem}; "
              f"`max_patterns` {tier['max_patterns']:,}, `max_pairs` {tier['max_pairs']:,}, "
              f"`max_memory_bytes` {tier['max_memory_bytes']:,} "
              f"({tier['max_memory_bytes'] / GiB:.0f} GiB).")
            w("")
            w("| t | certifies d ≥ | n_max | M(n_max) | mem est | predicted worst task CPU | "
              "margin | measured max (code, n, CPU, peak Δ) | pair-capped |")
            w("|---|---|---|---|---|---|---|---|---|")
            for p in tier["per_t"]:
                mm = p["measured_max"]
                mtxt = (f"{mm['code_id']}, {mm['n']}, {_fmt_s(mm['cpu_s'])} s, "
                        f"{_fmt_b(mm['peak_rss_delta_bytes'])}" if mm else "—")
                w(f"| {p['t']} | {p['certifies_d_ge']} | {p['n_max']} | {_fmt_int(p['M_at_n_max'])} "
                  f"| {_fmt_b(p['memory_estimate_at_n_max'])} | "
                  f"{_fmt_s(p['predicted_worst_task_cpu_s'])} s | {p['cpu_margin']:.1f}× | {mtxt} | "
                  f"{', '.join(p['measured_pair_capped']) or '—'} |")
            w("")
    # ---- task validation
    tv = [t for t in tasks if t["measured_task_run"]]
    if tv:
        w("## Task-mode validation (pipeline `l2.distance` adapter, both sectors, one process)")
        w("")
        w("| code | n | t | task CPU s | Σ sector CPU s | task peak Δ | max sector peak Δ | "
          "intervals X / Z |")
        w("|---|---|---|---|---|---|---|---|")
        for t in tv:
            m, s = t["measured_task_run"], t["measured_sum_of_sectors"] or {}
            ivs = m["intervals"]
            w(f"| {t['code_id']} | {t['n']} | {t['t']} | {_fmt_s(m['cpu_s'])} | "
              f"{_fmt_s(s.get('cpu_s'))} | {_fmt_b(m['peak_rss_delta_bytes'])} | "
              f"{_fmt_b(s.get('peak_rss_delta_bytes'))} | {_iv(ivs.get('X'))} / "
              f"{_iv(ivs.get('Z'))} |")
        w("")
    # ---- per family tables
    w("## Per-family tables")
    w("")
    w("Each row is a task (code, t); a value written `X / Z` is per sector. M is the number of "
      "half-patterns, Σ_{j≤t} C(n,j). Mem est is the larger sector's preflight estimate. "
      "Interval is the certified d_X / d_Z (one number means exact). Prod is the verdict under "
      "the m1-live `distance_budget` (max_patterns 3M, max_pairs 200M, default 4 GiB memory): "
      "`ok`, `refused` (preflight), `unk` (the measured pairs exceed 200M, or the run did not "
      "complete), or `n/m` (not measured).")
    w("")
    idx: dict[tuple, dict] = {}
    for r in rows:
        idx.setdefault((r["code_id"], r["t"]), {})[r["sector"]] = r
    task_by = {(t["code_id"], t["t"]): t for t in tasks}
    for fam in sorted({r["family"] for r in rows}):
        w(f"### {fam}")
        w("")
        w("| code | n | k | t | M | mem est | pairs X / Z | wall s X / Z | CPU s X / Z | "
          "peak RSS Δ X / Z | outcome X / Z | interval X / Z | prod |")
        w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        keys = sorted({(r["subfamily"], r["n"], r["code_id"], r["t"]) for r in rows
                       if r["family"] == fam})
        for sub, n, cid, t in keys:
            secs = idx[(cid, t)]
            x, z = secs.get("X"), secs.get("Z")
            pick = lambda f: " / ".join(f(s) for s in (x, z) if s is not None)  # noqa: E731
            mx = lambda s: (s["measured"] or {})                              # noqa: E731
            tk = task_by.get((cid, t))
            prod = {"completed": "ok", "refused_preflight": "refused",
                    "not_measured": "n/m"}.get(tk["production_outcome"], "unk") if tk else "—"
            r0 = x or z
            w(f"| {cid} | {n} | {r0['k']} | {t} | {_fmt_int(r0['half_patterns_M'])} | "
              f"{_fmt_b(max(s['memory_bytes_estimate'] for s in (x, z) if s))} | "
              f"{pick(lambda s: _fmt_int(s['pairs_checked']))} | "
              f"{pick(lambda s: _fmt_s(mx(s).get('wall_s')))} | "
              f"{pick(lambda s: _fmt_s(mx(s).get('cpu_s')))} | "
              f"{pick(lambda s: _fmt_b(mx(s).get('peak_rss_delta_bytes')))} | "
              f"{pick(lambda s: SHORT.get(s['outcome'], s['outcome']))} | "
              f"{pick(lambda s: _iv(s['interval']))} | {prod} |")
        w("")
    # ---- codes
    w("## Codes")
    w("")
    w("| code | family / subfamily | n | k | rank H_X / H_Z | max check weight | max qubit "
      "degree | reference d | source |")
    w("|---|---|---|---|---|---|---|---|---|")
    for c in doc["codes"]:
        src = c["reference"] or (f"B1 seed {c['meta']['b1_spec']['seed']}, draw "
                                 f"{c['meta']['draw_index']}" if c["subfamily"] == "b1_random"
                                 else "")
        w(f"| {c['code_id']} | {c['family']} / {c['subfamily']} | {c['n']} | {c['k']} | "
          f"{c['rank_hx']} / {c['rank_hz']} | {c['max_check_weight']} | {c['max_qubit_degree']} | "
          f"{c['d_reference'] if c['d_reference'] else '—'} | {src} |")
    w("")
    if doc.get("anomalies"):
        w("## Anomalies")
        w("")
        for a in doc["anomalies"]:
            w(f"- {a}")
        w("")
    return "\n".join(L) + "\n"


def find_anomalies(codes, rows, tasks) -> list[str]:
    """Contradictions between certified intervals and references, audit mismatches, errors."""
    out = []
    by = {c["code_id"]: c for c in codes}
    for r in rows:
        c = by[r["code_id"]]
        if r["outcome"] == "measurement_error":
            out.append(f"{r['code_id']} t={r['t']} {r['sector']}: measurement error "
                       f"{(r.get('error') or '')[:160]!r}")
        if r["interval"] and c["d_reference"]:
            lo, hi = r["interval"]
            if not lo <= c["d_reference"] <= hi:
                out.append(f"{r['code_id']} t={r['t']} {r['sector']}: certified [{lo},{hi}] "
                           f"excludes reference d={c['d_reference']}")
        if r["outcome"] == "completed" and r["kernel_half_patterns"] != r["half_patterns_M"]:
            out.append(f"{r['code_id']} t={r['t']} {r['sector']}: kernel half_patterns "
                       f"{r['kernel_half_patterns']} != preflight M {r['half_patterns_M']}")
        a = r.get("audit")
        if a and r["outcome"] == "completed":
            if a["patterns_enumerated"] != r["half_patterns_M"]:
                out.append(f"{r['code_id']} t={r['t']} {r['sector']}: audited patterns "
                           f"{a['patterns_enumerated']} != M {r['half_patterns_M']}")
            if a["pairs_from_bucket_sizes"] != r["pairs_checked"]:
                out.append(f"{r['code_id']} t={r['t']} {r['sector']}: Σ C(M_s,2) = "
                           f"{a['pairs_from_bucket_sizes']} != kernel pairs {r['pairs_checked']}")
            if a["interval"] != r["interval"]:
                out.append(f"{r['code_id']} t={r['t']} {r['sector']}: audit interval "
                           f"{a['interval']} != timing interval {r['interval']}")
        m = r.get("measured")
        if r["outcome"] == "completed" and m and m.get("peak_rss_delta_bytes") \
                and r["half_patterns_M"] >= 100_000 \
                and m["peak_rss_delta_bytes"] > r["memory_bytes_estimate"]:
            out.append(f"{r['code_id']} t={r['t']} {r['sector']}: peak RSS delta "
                       f"{m['peak_rss_delta_bytes']} B exceeds the preflight memory estimate "
                       f"{r['memory_bytes_estimate']} B")
    for t in tasks:
        tr = t.get("measured_task_run")
        if tr:
            for s, iv in tr["intervals"].items():
                secrow = next((r for r in rows if r["code_id"] == t["code_id"]
                               and r["t"] == t["t"] and r["sector"] == s), None)
                if secrow and iv != secrow["interval"]:
                    out.append(f"{t['code_id']} t={t['t']} task-mode {s} interval {iv} != "
                               f"sector run {secrow['interval']}")
    return out


# ================================================================================================
# Main
# ================================================================================================

def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, allow_nan=False, default=float) + "\n"


def _clean(obj):
    """numpy scalars -> python; NaN/inf are not expected."""
    import numpy as np
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    return obj


TASK_VALIDATION = ("bb72|3", "bb90|4", "bb144|3", "toric6|4", "rsurface7|3", "golay23|3",
                   "b1_n60|3", "hgp_eham8|3", "steane7|1", "bb72|1")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--mode", choices=sorted(MODES), default="full")
    ap.add_argument("--out-dir", default=str(REPO / "results" / "feasibility"))
    ap.add_argument("--workers", type=int)
    ap.add_argument("--mem-pool-gib", type=int)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--render-only", action="store_true")
    args = ap.parse_args(argv)
    if args.child:
        child_main()
        return 0
    out_dir = Path(args.out_dir)
    jpath, mpath = out_dir / "M1-certification.json", out_dir / "M1-certification.md"
    if args.render_only:                  # re-derive from the measured rows; no re-measurement
        doc = derive(json.loads(jpath.read_text()))
        jpath.write_text(_dump(doc))
        mpath.write_text(render_md(doc))
        return 0
    os.environ.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    caps = dict(MODES[args.mode])
    if args.workers:
        caps["workers"] = args.workers
    if args.mem_pool_gib:
        caps["mem_pool_gib"] = args.mem_pool_gib
    caps["workers"] = min(caps["workers"], 32)
    codes = catalogue(args.mode)
    prod = production_budget()
    runs, skipped = plan(codes, caps, prod)
    by_code = {c["code_id"]: c for c in codes}

    def spec_for(case, kind, budget=None):
        c = by_code[case["code_id"]]
        return {"code_id": c["code_id"], "family": c["family"], "params": c["params"],
                "t": case["t"], "sector": case["sector"], "kind": kind,
                "budget": budget or case["budget"],
                "rlimit_as": int((caps["case_mem_gib"] + 4) * GiB)}

    jobs = [{"case": r, "spec": spec_for(r, "sector"), "predicted_cpu_s": r["predicted_cpu_s"],
             "reserve_bytes": r["preflight"]["memory_bytes_estimate"] + 150 * MB} for r in runs]
    audits = [{"case": r, "spec": spec_for(r, "audit"),
               "predicted_cpu_s": 1.8 * r["predicted_cpu_s"],
               "reserve_bytes": r["preflight"]["memory_bytes_estimate"] + 150 * MB}
              for r in runs if r["preflight"]["half_patterns"] <= caps["audit_max_patterns"]
              and r["predicted_cpu_s"] <= caps["audit_max_pred_cpu_s"]]
    tasks_v = []
    want = set(TASK_VALIDATION) if args.mode == "full" else {"steane7|1", "bb72|1"}
    for r in runs:
        if f"{r['code_id']}|{r['t']}" in want and r["sector"] == "X":
            z = next((q for q in runs if q["code_id"] == r["code_id"] and q["t"] == r["t"]
                      and q["sector"] == "Z"), None)
            if z is None:
                continue
            spec = spec_for(r, "task")
            spec.pop("sector")
            tasks_v.append({"case": r, "spec": spec,
                            "predicted_cpu_s": r["predicted_cpu_s"] + z["predicted_cpu_s"],
                            "reserve_bytes": max(r["preflight"]["memory_bytes_estimate"],
                                                 z["preflight"]["memory_bytes_estimate"]) + 200 * MB})
    pred_cpu = sum(j["predicted_cpu_s"] for j in jobs + audits + tasks_v)
    print(f"[plan] {len(codes)} codes; {len(jobs)} sector runs, {len(audits)} audit runs, "
          f"{len(tasks_v)} task runs, {len(skipped)} skipped rows; predicted CPU "
          f"{pred_cpu / 3600:.2f} h; workers {caps['workers']}, pool {caps['mem_pool_gib']} GiB",
          flush=True)
    if args.plan_only:
        for j in sorted(jobs, key=lambda j: -j["predicted_cpu_s"])[:40]:
            print(f"  {j['case']['code_id']:14s} t={j['case']['t']} {j['case']['sector']} "
                  f"M={j['case']['preflight']['half_patterns']:>12,d} "
                  f"est={j['reserve_bytes'] / GiB:6.1f} GiB cpu~{j['predicted_cpu_s']:7.1f} s")
        return 0
    host = host_facts()
    started = time.time()
    w0 = time.perf_counter()
    done = schedule(jobs + audits + tasks_v, workers=caps["workers"],
                    mem_pool=caps["mem_pool_gib"] * GiB, timeout=caps["timeout_s"])
    wall = time.perf_counter() - w0
    host_after = host_facts()
    if host_after["source_fingerprints"] != host["source_fingerprints"]:
        print("[warn] measured sources changed during the run", flush=True)
    kinds = {"sector": [], "audit": [], "task": []}
    for d in done:
        kinds[d["spec"]["kind"]].append(d)
    rows = build_rows(codes, kinds["sector"], kinds["audit"], skipped)
    tasks = build_tasks(rows, kinds["task"])
    doc = {"schema": SCHEMA, "host": host,
           "config": {"mode": args.mode, **caps, "production_budget": prod,
                      "production_budget_source": PRODUCTION_BUDGET_SOURCE,
                      "cpu_budgets_s": list(CPU_BUDGETS), "mem_budgets_gib": list(MEM_BUDGETS_GIB),
                      "b1_seed_base": B1_SEED_BASE,
                      "prior": {"s_per_pattern": PRIOR_S_PER_PATTERN,
                                "s_per_pair": PRIOR_S_PER_PAIR, "fixed_s": PRIOR_FIXED_S}},
           "codes": [{k: v for k, v in c.items() if k != "params"} | {
               "params_sha256": hashlib.sha256(json.dumps(c["params"], sort_keys=True)
                                               .encode()).hexdigest(), "params": c["params"]
               if c["family"] == "two_block_group_algebra" else None} for c in codes],
           "rows": rows, "tasks": tasks,
           "measurement": {"started_unix": started, "wall_s": wall,
                           "children": len(done),
                           "child_cpu_s": sum(d["run"].get("child_cpu_s") or 0 for d in done),
                           "max_child_rss_bytes": max((d["run"].get("child_maxrss_bytes") or 0)
                                                      for d in done) if done else 0,
                           "sources_unchanged": host_after["source_fingerprints"]
                           == host["source_fingerprints"]}}
    doc = derive(_clean(doc))
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath.write_text(_dump(doc))
    mpath.write_text(render_md(doc))
    print(f"[done] {len(done)} children in {wall / 60:.1f} min; "
          f"{doc['measurement']['child_cpu_s'] / 3600:.2f} CPU-h; wrote {jpath} and {mpath}; "
          f"{len(doc['anomalies'])} anomalies", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
