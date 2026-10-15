"""Bounded execution in a forked child: CPU, wall and memory ceilings are enforced, usage is measured
by ``wait4`` and only an ``ok`` run carries a value (Codex M1 review B5; appendix §K)."""
import os
import signal
import subprocess
import sys
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


# --- Codex M1 re-review N1: nested bounded workers never outlive the enclosing job ----------------
SMALL = Limits(cpu_seconds=60, wall_seconds=60, memory_bytes=256 * 2**20)


def _stat(pid):
    """(state, pgrp) of a process from /proc, or None once it is gone."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
    except OSError:
        return None
    fields = stat[stat.rindex(")") + 2:].split()
    return fields[0], int(fields[2])


def _alive(pid):
    s = _stat(pid)
    return s is not None and s[0] not in "ZX"


def _live_members(pgid):
    """Live (non-zombie) processes of process group ``pgid``, read from /proc."""
    return [int(d) for d in os.listdir("/proc") if d.isdigit()
            and (s := _stat(int(d))) is not None and s[1] == pgid and s[0] not in "ZX"]


def _busy(path):
    with open(path, "w") as f:
        f.write(f"{os.getpid()} {os.getpgid(0)}")
    spin(60)


def _cleanup(path):
    if os.path.exists(path):
        pid = int(open(path).read().split()[0])
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)


def test_an_expired_outer_job_leaves_no_inner_bounded_worker_running(tmp_path):
    """The Codex repro: an outer job (a control suite) whose bounded child runs an inner bounded
    worker (a pipeline verifier) expires while the inner one is busy. The inner worker stays in
    the outer job's process group, so the outer wall kill reaches it; the parent returns only once
    the whole group is gone; the inner worker's CPU was never measured, so the CPU unit is
    unresolved (absent from the usage, i.e. charged at its reservation)."""
    path = str(tmp_path / "inner")
    try:
        res = run_bounded(lambda: run_bounded(_busy, path, limits=SMALL).outcome,
                          limits=Limits(cpu_seconds=30, wall_seconds=1, memory_bytes=512 * 2**20))
        assert res.outcome == "wall_exceeded" and res.value is None
        inner, group = map(int, open(path).read().split())
        assert not _alive(inner) and _live_members(group) == []          # no survivor at return
        assert res.detail["process_group"] == group                       # it never left the group
        assert res.detail["cpu_complete"] is False and "cpu_seconds" not in res.usage()
        assert res.usage()["wall_seconds"] == res.wall_seconds >= 1
    finally:
        _cleanup(path)


def test_an_evaluator_re_executed_inside_a_bounded_child_stays_in_its_group(tmp_path):
    """The confirmation re-executes the evaluator (a fresh interpreter, as in ``audit_bundle``),
    whose own bounded workers must also stay inside the confirmation job's process group."""
    import sys
    path = str(tmp_path / "inner")
    code = ("import sys; from zkqec.exec import Limits, run_bounded; import time, os\n"
            "def busy(p):\n"
            "    open(p, 'w').write(f'{os.getpid()} {os.getpgid(0)}')\n"
            "    while True: pass\n"
            "run_bounded(busy, sys.argv[1], limits=Limits(cpu_seconds=60, wall_seconds=60, "
            "memory_bytes=256 * 2**20))\n")
    try:
        res = run_bounded(lambda: subprocess.run([sys.executable, "-c", code, path]).returncode,
                          limits=Limits(cpu_seconds=30, wall_seconds=3, memory_bytes=512 * 2**20))
        assert res.outcome == "wall_exceeded"
        inner, group = map(int, open(path).read().split())
        assert group == res.detail["process_group"] and not _alive(inner)
        assert _live_members(group) == []
    finally:
        _cleanup(path)


def test_a_nested_run_meets_its_own_deadline_inside_the_enclosing_group(tmp_path):
    path = str(tmp_path / "inner")

    def outer():
        inner = run_bounded(_busy, path, limits=Limits(cpu_seconds=30, wall_seconds=0.5,
                                                       memory_bytes=256 * 2**20))
        return inner.outcome, inner.detail["process_group"], inner.wall_seconds, os.getpgid(0)
    try:
        res = run_bounded(outer, limits=ROOMY)
        assert res.outcome == "ok"
        outcome, inner_group, wall, outer_group = res.value
        assert outcome == "wall_exceeded" and inner_group is None and 0.5 <= wall < 5
        inner, group = map(int, open(path).read().split())
        assert group == outer_group == res.detail["process_group"] and not _alive(inner)
    finally:
        _cleanup(path)


def test_descendants_left_behind_by_a_finished_child_are_killed():
    res = run_bounded(lambda: subprocess.Popen(["sleep", "30"]).pid, limits=ROOMY)
    assert res.outcome == "ok" and not _alive(res.value)
    assert res.detail["stragglers_killed"] is True and res.detail["cpu_complete"] is False
    assert "cpu_seconds" not in res.usage()
    clean = run_bounded(lambda: 1, limits=ROOMY)
    assert clean.detail["stragglers_killed"] is False and "cpu_seconds" in clean.usage()


def test_members_surviving_the_grace_period_are_an_escape(monkeypatch):
    from zkqec.exec import bounded
    monkeypatch.setattr(bounded, "_group_alive", lambda pgid: True)     # a member that never dies
    monkeypatch.setattr(bounded, "ESCAPE_GRACE_SECONDS", 0.2)
    res = run_bounded(lambda: 1, limits=ROOMY)
    assert res.outcome == "escaped" and res.value is None and not res.ok
    assert "escaped" in bounded.OUTCOMES and "cpu_seconds" not in res.usage()


# --- Codex M1 re-review 3 B1: no signal to a process-group id after its leader was reaped ----------
def _leave_a_straggler(path):
    """Return at once, leaving a grandchild in the job's process group (a straggler to drain)."""
    p = subprocess.Popen(["sleep", "30"])
    with open(path, "w") as f:
        f.write(str(p.pid))
    return "done"


def test_the_group_is_drained_before_its_leader_is_reaped(tmp_path, monkeypatch):
    # Reaping the leader frees its pid; a later killpg on that id could hit a reused, unrelated
    # group. Every group signal must precede the reap, so the id stays allocated (zombie leader).
    import zkqec.exec.bounded as bounded
    events = []
    real_killpg, real_wait4 = os.killpg, os.wait4

    def killpg(pgid, sig):
        events.append(("killpg", pgid, sig))
        return real_killpg(pgid, sig)

    def wait4(pid, options):
        out = real_wait4(pid, options)
        events.append(("reap", pid))
        return out
    monkeypatch.setattr(bounded.os, "killpg", killpg)
    monkeypatch.setattr(bounded.os, "wait4", wait4)
    marker = tmp_path / "straggler"
    res = run_bounded(_leave_a_straggler, str(marker), limits=Limits(5, 10, 256 * 2**20))
    leader = res.detail["process_group"]
    reap = events.index(("reap", leader))
    assert [e for e in events[reap + 1:] if e[0] == "killpg" and e[1] == leader] == []
    assert any(e[0] == "killpg" and e[2] == signal.SIGKILL for e in events[:reap])
    assert res.detail["stragglers_killed"] is True
    straggler = int(marker.read_text())
    deadline = time.time() + 5
    while os.path.exists(f"/proc/{straggler}") and time.time() < deadline:
        time.sleep(0.02)
    stat = open(f"/proc/{straggler}/stat").read() if os.path.exists(f"/proc/{straggler}") else ""
    assert stat == "" or stat[stat.rindex(")") + 2] in "ZX"   # dead (reparented zombie at worst)
    assert not os.path.exists(f"/proc/{leader}")                # the leader itself was reaped


def test_a_zombie_leader_alone_is_not_a_live_group():
    import zkqec.exec.bounded as bounded
    pid = os.fork()
    if pid == 0:
        os.setpgid(0, 0)
        os._exit(0)
    os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)          # exited, not reaped: a zombie
    try:
        assert bounded._group_alive(pid) is False
    finally:
        os.waitpid(pid, 0)


# --- Codex M1 re-review 4 (residual B1): no signal once the child may have been reaped ---------------
def _quick():
    return "ok"


def _record_signals(monkeypatch, bounded, events):
    real = {"killpg": os.killpg, "kill": os.kill, "pidfd": signal.pidfd_send_signal}

    def rec(name):
        def f(*a, **k):
            events.append(("signal", name, a[0], a[1] if len(a) > 1 else None))
            return real[name](*a, **k)
        return f
    monkeypatch.setattr(bounded.os, "killpg", rec("killpg"))
    monkeypatch.setattr(bounded.os, "kill", rec("kill"))
    monkeypatch.setattr(bounded.signal, "pidfd_send_signal", rec("pidfd"))


@pytest.mark.parametrize("nested", [False, True])
def test_an_interrupt_right_after_the_reap_sends_no_signal(monkeypatch, nested):
    import zkqec.exec.bounded as bounded
    if nested:
        monkeypatch.setattr(bounded, "_IN_BOUNDED_CHILD", True)
    events = []
    real_wait4 = os.wait4

    def wait4_then_interrupt(pid, options):
        out = real_wait4(pid, options)                       # the child is reaped here ...
        events.append(("reaped", pid))
        raise KeyboardInterrupt                              # ... and SIGINT lands before `reaped =`
    _record_signals(monkeypatch, bounded, events)
    monkeypatch.setattr(bounded.os, "wait4", wait4_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_bounded(_quick, limits=Limits(5, 10, 256 * 2**20))
    i = next(k for k, e in enumerate(events) if e[0] == "reaped")
    assert [e for e in events[i + 1:] if e[0] == "signal"] == []


@pytest.mark.parametrize("nested", [False, True])
def test_a_child_reaped_elsewhere_is_never_signalled(monkeypatch, nested):
    import zkqec.exec.bounded as bounded
    if nested:
        monkeypatch.setattr(bounded, "_IN_BOUNDED_CHILD", True)
    events = []
    real_waitpid = os.waitpid

    def foreign_reaper(idtype, ident, options):
        pid = ident if idtype == os.P_PID else None
        real_waitpid(pid, 0)                                 # another reaper takes the child
        events.append(("reaped", pid))
        raise ChildProcessError(10, "No child processes")
    _record_signals(monkeypatch, bounded, events)
    monkeypatch.setattr(bounded.os, "waitid", foreign_reaper)
    res = run_bounded(_quick, limits=Limits(5, 10, 256 * 2**20))
    assert res.outcome == "escaped" and res.value is None
    assert res.detail.get("ownership_lost") is True and "cpu_seconds" not in res.usage()
    i = next(k for k, e in enumerate(events) if e[0] == "reaped")
    assert [e for e in events[i + 1:] if e[0] == "signal"] == []


def test_an_ignored_sigchld_is_refused():
    # SIG_IGN makes the kernel reap children itself: run_bounded could never own its child's pid.
    old = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    try:
        with pytest.raises(RuntimeError, match="SIGCHLD"):
            run_bounded(_quick, limits=Limits(5, 10, 256 * 2**20))
    finally:
        signal.signal(signal.SIGCHLD, old)


# --- Codex M1 re-review 5: asynchronous interrupts anywhere in the lifecycle (B1 combined, B2) ------
class _Interrupted(Exception):
    pass


@pytest.fixture
def real_sigint():
    """A real SIGINT to this process raises _Interrupted (not KeyboardInterrupt, which pytest owns)."""
    def handler(signum, frame):
        raise _Interrupted()
    old = signal.signal(signal.SIGINT, handler)
    yield lambda: os.kill(os.getpid(), signal.SIGINT)
    signal.signal(signal.SIGINT, old)


def _sleep_writing_pid(path):
    with open(path, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(30)


def _fds():
    return len(os.listdir("/proc/self/fd"))


def _dead(pid, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read()
        except OSError:
            return True
        if stat[stat.rindex(")") + 2] in "ZX":
            return True
        time.sleep(0.02)
    return False


@pytest.mark.parametrize("nested", [False, True])
def test_ownership_loss_with_an_interrupt_and_no_pidfd_sends_no_signal(monkeypatch, real_sigint, nested):
    import zkqec.exec.bounded as bounded
    if nested:
        monkeypatch.setattr(bounded, "_IN_BOUNDED_CHILD", True)
    events = []
    real_waitpid = os.waitpid

    def no_pidfd(pid):
        raise OSError(38, "pidfd_open unavailable")

    def foreign_reaper_then_interrupt(idtype, ident, options):
        real_waitpid(ident, 0)
        events.append(("reaped", ident))
        real_sigint()                                         # lands before ownership is cleared
        raise ChildProcessError(10, "No child processes")
    _record_signals(monkeypatch, bounded, events)
    monkeypatch.setattr(bounded.os, "pidfd_open", no_pidfd)
    monkeypatch.setattr(bounded.os, "waitid", foreign_reaper_then_interrupt)
    with pytest.raises(_Interrupted):
        run_bounded(_quick, limits=Limits(5, 10, 256 * 2**20))
    i, child = next((k, e[1]) for k, e in enumerate(events) if e[0] == "reaped")
    assert [e for e in events[i + 1:] if e[0] == "signal" and e[2] == child] == []


@pytest.mark.parametrize("nested", [False, True])
def test_an_interrupt_during_setup_still_kills_and_reaps_the_child(monkeypatch, real_sigint, tmp_path,
                                                                   nested):
    import zkqec.exec.bounded as bounded
    if nested:
        monkeypatch.setattr(bounded, "_IN_BOUNDED_CHILD", True)
    real_open = os.pidfd_open

    def open_then_interrupt(pid, *a):
        fd = real_open(pid, *a)
        real_sigint()                                         # before the lifecycle try/finally
        return fd
    monkeypatch.setattr(bounded.os, "pidfd_open", open_then_interrupt)
    marker = tmp_path / "child"
    before, t0 = _fds(), time.monotonic()
    with pytest.raises(_Interrupted):
        run_bounded(_sleep_writing_pid, str(marker), limits=Limits(60, 60, 256 * 2**20))
    assert time.monotonic() - t0 < 10                        # the interrupt was honoured promptly
    end = time.time() + 3
    while not marker.exists() and time.time() < end:
        time.sleep(0.02)
    if marker.exists():
        assert _dead(int(marker.read_text()))
    assert _fds() == before                                   # pipe and pidfd closed


def test_an_interrupt_during_the_final_cleanup_leaks_no_descriptor(monkeypatch, real_sigint):
    import zkqec.exec.bounded as bounded
    real_reap = bounded._reap

    def reap_then_interrupt(pid):
        out = real_reap(pid)
        real_sigint()
        return out
    monkeypatch.setattr(bounded, "_reap", reap_then_interrupt)
    before = _fds()
    with pytest.raises(_Interrupted):
        run_bounded(_quick, limits=Limits(5, 10, 256 * 2**20))
    assert _fds() == before


def test_a_bounded_child_dies_with_its_parent(tmp_path):
    marker = tmp_path / "child"
    code = ("import sys, time\nfrom zkqec.exec import Limits, run_bounded\n"
            "sys.path.insert(0, %r)\nimport test_bounded as t\n"
            "run_bounded(t._sleep_writing_pid, %r, limits=Limits(60, 60, 256 * 2**20))\n"
            % (os.path.dirname(__file__), str(marker)))
    parent = subprocess.Popen([sys.executable, "-c", code])
    end = time.time() + 20
    while not marker.exists() and time.time() < end:
        time.sleep(0.05)
    assert marker.exists()
    parent.kill()
    parent.wait()
    assert _dead(int(marker.read_text()))
