"""Bounded execution in a forked child: CPU, wall and memory ceilings are enforced, usage is measured
by ``wait4`` and only an ``ok`` run carries a value (Codex M1 review B5; appendix §K)."""
import os
import signal
import subprocess
import time

import numpy as np
import pytest

from zkqec.exec import BoundedResult, Limits, run_bounded

ROOMY = Limits(cpu_seconds=30, wall_seconds=30, memory_bytes=512 * 2**20)


def spin(seconds: float) -> float:
    """Busy CPU for ``seconds`` of process time."""
    t0 = time.process_time()
    x = 0
    while time.process_time() - t0 < seconds:
        x += 1
    return time.process_time() - t0


def test_ok_returns_the_value_and_measures_the_child():
    res = run_bounded(lambda a, *, b: {"sum": a + b, "pid": os.getpid()}, 2, b=3, limits=ROOMY)
    assert isinstance(res, BoundedResult) and res.outcome == "ok" and res.ok
    assert res.value["sum"] == 5 and res.value["pid"] != os.getpid()      # ran in a child
    assert res.cpu_seconds >= 0 and 0 < res.wall_seconds < 30 and res.peak_rss_bytes > 0
    assert res.usage() == {"cpu_seconds": res.cpu_seconds, "wall_seconds": res.wall_seconds}


def test_cpu_is_measured_for_the_child_only():
    before = time.process_time()
    res = run_bounded(spin, 0.5, limits=ROOMY)
    assert res.outcome == "ok" and res.cpu_seconds >= 0.45
    assert time.process_time() - before < 0.4          # the parent only waited


def test_verifier_results_cross_the_pipe():
    from zkqec.grammar.matrices import from_dense
    from zkqec.kernel.codes import steane
    from zkqec.verify import analyze
    c = steane()
    res = run_bounded(analyze, from_dense(c.hx, c.hz), artifact_sink=lambda o: "0" * 64,
                      limits=ROOMY)
    assert res.outcome == "ok"
    assert [r.proposition.predicate for r in res.value][:1] == ["css_valid"]


def test_cpu_exhaustion_is_killed():
    t0 = time.monotonic()
    res = run_bounded(spin, 60, limits=Limits(cpu_seconds=1, wall_seconds=30,
                                              memory_bytes=256 * 2**20))
    assert res.outcome == "cpu_exceeded" and res.value is None
    assert 0.9 <= res.cpu_seconds < 3.5 and time.monotonic() - t0 < 10
    assert res.detail["signal"] in ("SIGXCPU", "SIGKILL")


def test_finishing_beyond_a_fractional_cpu_ceiling_is_not_ok():
    res = run_bounded(spin, 0.8, limits=Limits(cpu_seconds=0.3, wall_seconds=30,
                                               memory_bytes=256 * 2**20))
    assert res.outcome == "cpu_exceeded" and res.value is None and res.cpu_seconds > 0.3


def test_wall_exhaustion_kills_the_process_group():
    def sleeper():
        subprocess.run(["sleep", "30"])                 # a descendant must die with it
        return "finished"
    t0 = time.monotonic()
    res = run_bounded(sleeper, limits=Limits(cpu_seconds=30, wall_seconds=1,
                                             memory_bytes=256 * 2**20))
    elapsed = time.monotonic() - t0
    assert res.outcome == "wall_exceeded" and res.value is None
    assert 1.0 <= res.wall_seconds < 5 and elapsed < 5 and res.cpu_seconds < 1
    out = subprocess.run(["pgrep", "-f", "^sleep 30$"], capture_output=True, text=True).stdout
    assert not [p for p in out.split() if int(p) in _descendants_of(os.getpid())]


def _descendants_of(pid):
    out = subprocess.run(["ps", "-e", "-o", "pid=,ppid="], capture_output=True, text=True).stdout
    kids, frontier = set(), {pid}
    rows = [tuple(map(int, line.split())) for line in out.splitlines() if line.strip()]
    while frontier:
        frontier = {p for p, pp in rows if pp in frontier} - kids
        kids |= frontier
    return kids


def test_memory_exhaustion_is_refused_by_the_address_space_limit():
    def hog():
        a = np.ones(512 * 2**20, dtype=np.uint8)       # 512 MiB against a 64 MiB allowance
        return int(a[-1])
    res = run_bounded(hog, limits=Limits(cpu_seconds=30, wall_seconds=30, memory_bytes=64 * 2**20))
    assert res.outcome == "memory_exceeded" and res.value is None
    assert res.detail["error_type"].endswith("MemoryError")
    assert res.peak_rss_bytes < 400 * 2**20
    ok = run_bounded(lambda: int(np.ones(16 * 2**20, dtype=np.uint8).sum()),
                     limits=Limits(cpu_seconds=30, wall_seconds=30, memory_bytes=256 * 2**20))
    assert ok.outcome == "ok" and ok.value == 16 * 2**20


def test_an_exception_is_a_crash_without_a_value():
    def boom():
        raise ValueError("bad input")
    res = run_bounded(boom, limits=ROOMY)
    assert (res.outcome, res.value) == ("crashed", None)
    assert res.detail["error_type"] == "ValueError" and res.detail["message"] == "bad input"


def test_a_child_killed_by_a_signal_is_a_crash():
    res = run_bounded(lambda: os.kill(os.getpid(), signal.SIGTERM), limits=ROOMY)
    assert res.outcome == "crashed" and res.detail["signal"] == "SIGTERM"


def test_an_unpicklable_result_is_a_crash():
    res = run_bounded(lambda: (lambda: 1), limits=ROOMY)
    assert res.outcome == "crashed" and res.detail["error_type"] == "unpicklable_result"


def test_the_child_cannot_mutate_parent_state():
    seen = []
    res = run_bounded(lambda: seen.append(1) or len(seen), limits=ROOMY)
    assert res.value == 1 and seen == []


def test_an_armed_parent_timer_does_not_fire_in_the_child():
    def handler(signum, frame):
        raise TimeoutError("parent deadline")
    previous = signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, 5.0)
    try:
        res = run_bounded(lambda: signal.getitimer(signal.ITIMER_REAL)[0], limits=ROOMY)
        assert res.outcome == "ok" and res.value == 0.0
        assert 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 5.0       # the parent's is intact
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize("bad", [dict(cpu_seconds=0, wall_seconds=1, memory_bytes=1),
                                 dict(cpu_seconds=1, wall_seconds=float("nan"), memory_bytes=1),
                                 dict(cpu_seconds=1, wall_seconds=1, memory_bytes=0),
                                 dict(cpu_seconds=1, wall_seconds=1, memory_bytes=1.5),
                                 dict(cpu_seconds=True, wall_seconds=1, memory_bytes=1)])
def test_limits_are_validated(bad):
    with pytest.raises(ValueError):
        Limits(**bad)
