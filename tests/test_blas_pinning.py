"""Verifier CPU is charged by process time (§K). Unpinned OpenBLAS starts one spinning thread per core
on import (7.3 CPU-s for `import numpy, zkqec.pipeline` on this 144-core host vs 0.27 s pinned), so
every process that imports zkqec first pins BLAS to one thread unless the caller chose otherwise."""
import os
import subprocess
import sys

CODE = ("import time; c = time.process_time(); import zkqec, numpy, zkqec.pipeline, os; "
        "print(os.environ['OPENBLAS_NUM_THREADS'], round(time.process_time() - c, 3))")


def _run(env_extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", CODE], env=env, capture_output=True, text=True, check=True)
    threads, cpu = out.stdout.split()
    return threads, float(cpu)


def test_importing_zkqec_first_pins_blas_threads():
    threads, cpu = _run({})
    assert threads == "1"
    assert cpu < 2.0


def test_an_explicit_caller_setting_is_kept():
    assert _run({"OPENBLAS_NUM_THREADS": "2"})[0] == "2"
