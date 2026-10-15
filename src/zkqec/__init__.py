"""zkqec: evidence-gated, multi-agent, zero-knowledge QEC code-discovery workflow.

The scientific kernel in ``zkqec.kernel`` is vendored from QECForge 0.1.0 (see
``zkqec/kernel/PROVENANCE.md``). Agents propose; registered verifiers admit.

Importing zkqec before numpy pins BLAS/OpenMP to one thread unless the caller set otherwise:
verifier CPU is charged by process time (§K), and unpinned OpenBLAS spins one thread per core
(7.3 CPU-s for an import on a 144-core host).
"""
import os as _os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")

__version__ = "0.0.1"
