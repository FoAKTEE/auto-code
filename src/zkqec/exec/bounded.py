"""Bounded execution of evaluator work in a forked child (appendix §K, §L; Codex M1 review B5).

:func:`run_bounded` runs ``fn(*args, **kwargs)`` in a child created with ``os.fork`` (the child
inherits the evaluator already loaded in this process: registry closures, monkeypatches, the pinned
BLAS environment) and enforces three independent ceilings from :class:`Limits`:

* **CPU** — the child sets ``RLIMIT_CPU`` (soft ``ceil(cpu_seconds)``, hard one second later: the
  kernel sends ``SIGXCPU`` and then ``SIGKILL``). A run that finishes but used more CPU than
  ``cpu_seconds`` (the soft limit is a whole second) is also ``cpu_exceeded``.
* **Memory** — the child sets ``RLIMIT_AS`` to its address space at fork plus ``memory_bytes``; an
  allocation beyond it raises ``MemoryError`` in the child (reported as ``memory_exceeded``; a
  reserve buffer is released first so the report can still be written).
* **Wall** — the parent reads the result pipe only until ``wall_seconds`` after the fork, then kills
  the child's process group (``SIGKILL``; descendants included) and reports ``wall_exceeded``.

Containment of nested work (Codex M1 re-review N1). A top-level run puts its child in a new process
group that the child leads; the child marks its process (:data:`_IN_BOUNDED_CHILD`, and the
``ZKQEC_BOUNDED_PGID`` environment variable, so an evaluator re-executed in a fresh interpreter, as
the confirmation's offline audit is, sees the mark too). A ``run_bounded`` inside a marked process
is *nested*: its child stays in the enclosing group (no ``setpgid``), so the enclosing job's kill
reaches it, and at its own deadline it is killed by pid (never ``killpg`` on a group it does not
lead). Once the child of a top-level run has exited, the parent kills whatever is left of the group
and waits until no live (non-zombie) member remains — while the exited leader is still an unreaped
zombie, which keeps its pid and the group id allocated — and only then reaps it; members that survive
:data:`ESCAPE_GRACE_SECONDS` make the outcome ``escaped`` (non-ok, never evidence; callers keep the
job's full reservation).

Ownership of the child's pid (Codex M1 re-reviews 3–4, B1). The parent signals the child's pid or
group id only while it owns the unreaped child: leader signals go through a pidfd (never a reused pid);
ownership ends *before* any reaping wait, so an interrupt landing after the reap sends nothing; a
child found already reaped (``ECHILD``) is never signalled again and the outcome is ``escaped`` with
``ownership_lost`` (charged at the reservation). The process must not reap children it does not own:
``run_bounded`` refuses to start while ``SIGCHLD`` is ignored (the kernel would reap the child).

Asynchronous interrupts (re-review 5). In the main thread, the parent-side lifecycle — pipe, fork, pidfd,
the wait, the kill, the drain, the reap and closing the descriptors — runs with the Python handlers of
:data:`_DEFERRED` signals replaced by one that only records the signal; a recorded interrupt makes the
wait loop kill the child and clean up through the ordinary ordered path, and only afterwards are the
original handlers restored and the recorded signals re-delivered (a ``KeyboardInterrupt`` then surfaces
with no child, group member or descriptor left). An interrupt therefore never lands between an
ownership transition and its bookkeeping. The child restores the original handlers, and dies with its
parent (``PR_SET_PDEATHSIG``). Without a pidfd, a numeric signal is sent only after ``waitid(WNOHANG |
WNOWAIT)`` positively shows the pid is still this process's unreaped child.

The CPU of processes the parent killed (or found left behind) was never reaped by the child, so
``wait4`` did not measure it: ``detail["cpu_complete"]`` is then False and :meth:`BoundedResult.usage`
omits ``cpu_seconds`` (unmeasured: charged at the reservation, §K), unless the measured lower bound
alone exceeds the CPU ceiling (an overrun, charged in full).

The child returns ``("ok", value)`` — or the failure class with the exception type and message —
pickled through a pipe and leaves with ``os._exit`` (no atexit hooks, no inherited finalizers, no
use of inherited database handles). The parent reaps the child with ``wait4``, which yields the
child's own CPU time (user + system) and peak RSS, so concurrent children of this process never
pollute the measurement. Only an ``ok`` result carries a value; every other outcome carries
``value=None`` and the measured usage, and callers must treat it as no evidence (§K: measured usage
is charged, a non-ok run closes as crashed).

Inherited interval timers do not reach the child (``fork(2)``), so an in-process ``ITIMER_REAL``
deadline in the caller never fires inside the child; the parent's wall kill is the deadline.
"""
from __future__ import annotations

import math
import os
import pickle
import resource
import select
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

Outcome = Literal["ok", "cpu_exceeded", "wall_exceeded", "memory_exceeded", "crashed", "escaped"]
OUTCOMES: tuple[str, ...] = ("ok", "cpu_exceeded", "wall_exceeded", "memory_exceeded", "crashed",
                             "escaped")
_DEFERRED = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGALRM,
             signal.SIGUSR1, signal.SIGUSR2)
_POLL_SECONDS = 0.1                     # how soon a recorded interrupt is honoured
MAX_RESULT_BYTES = 256 * 2**20          # a larger pickled result is a crash, not a value
_RESERVE_BYTES = 8 * 2**20              # released on MemoryError so the child can still report
_MESSAGE = 500
ESCAPE_GRACE_SECONDS = 5.0              # group members still alive after this are an escape
GROUP_ENV = "ZKQEC_BOUNDED_PGID"        # the enclosing job's process group, across exec
_IN_BOUNDED_CHILD = False               # set in every bounded child (fork keeps it)
_RESULT_FD: int | None = None           # this bounded child's result pipe (not for its children)


@dataclass(frozen=True)
class Limits:
    """Per-execution ceilings: CPU-seconds and wall-seconds of the child, and the address space it
    may add to what it inherited at fork (bytes)."""

    cpu_seconds: float
    wall_seconds: float
    memory_bytes: int

    def __post_init__(self):
        for name in ("cpu_seconds", "wall_seconds"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
                    or v <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if type(self.memory_bytes) is not int or self.memory_bytes <= 0:
            raise ValueError("memory_bytes must be a positive int")


@dataclass(frozen=True)
class BoundedResult:
    outcome: Outcome
    value: Any                              # fn's return value iff outcome == "ok", else None
    cpu_seconds: float                      # the child's user + system time (wait4)
    wall_seconds: float                     # fork to reap
    peak_rss_bytes: int                     # the child's maximum resident set (wait4)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"

    def usage(self) -> dict[str, float]:
        """The measured units in the job book's vocabulary (§K). CPU is omitted (unmeasured, so
        charged at the reservation) when processes the child never reaped were killed, unless the
        measured part alone exceeds the CPU ceiling (then it is charged in full as an overrun)."""
        out = {"wall_seconds": float(self.wall_seconds)}
        ceiling = (self.detail.get("limits") or {}).get("cpu_seconds", 0.0)
        if self.detail.get("cpu_complete", True) or self.cpu_seconds > ceiling:
            out["cpu_seconds"] = float(self.cpu_seconds)
        return out


def _address_space_bytes() -> int:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")


def _lower_limit(which: int, soft: int, hard: int) -> None:
    """Set ``which`` to (soft, hard), never above an existing finite hard limit."""
    _, cur_hard = resource.getrlimit(which)
    if cur_hard != resource.RLIM_INFINITY:
        hard = min(hard, cur_hard)
        soft = min(soft, hard)
    resource.setrlimit(which, (soft, hard))


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _describe(exc: BaseException) -> dict[str, str]:
    return {"error_type": type(exc).__name__[:100], "message": str(exc)[:_MESSAGE]}


def _nested() -> bool:
    """True inside a bounded child (forked, or an interpreter it executed) still in its group."""
    if _IN_BOUNDED_CHILD:
        return True
    mark = os.environ.get(GROUP_ENV, "")
    try:
        return mark.isdigit() and os.getpgid(0) == int(mark)
    except OSError:
        return False


def _die_with_parent(parent: int) -> None:
    """``PR_SET_PDEATHSIG``: the kernel kills this child if its parent dies (an orchestrator killed
    by SIGKILL or a default-action signal must not leave a bounded child running)."""
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(1, int(signal.SIGKILL), 0, 0, 0)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass
    if os.getppid() != parent:                  # the parent died before the request took effect
        os._exit(72)


def _child(fn: Callable[..., Any], args: tuple, kwargs: dict, limits: Limits, w: int,
           nested: bool, saved: dict, parent: int) -> None:
    """Runs in the forked child; never returns."""
    global _IN_BOUNDED_CHILD, _RESULT_FD
    status = 70
    try:
        for sig, handler in saved.items():      # the parent's deferral is not the child's
            signal.signal(sig, handler)
        _die_with_parent(parent)
        if _RESULT_FD is not None:              # the enclosing child's pipe is not ours to hold
            try:
                os.close(_RESULT_FD)
            except OSError:
                pass
        _IN_BOUNDED_CHILD, _RESULT_FD = True, w
        if not nested:
            try:
                os.setpgid(0, 0)                # the wall kill reaches every descendant
            except OSError:
                pass
            os.environ[GROUP_ENV] = str(os.getpgid(0))
        _lower_limit(resource.RLIMIT_CORE, 0, 0)
        soft = max(1, math.ceil(limits.cpu_seconds))
        _lower_limit(resource.RLIMIT_CPU, soft, soft + 1)
        base = _address_space_bytes()
        _lower_limit(resource.RLIMIT_AS, base + limits.memory_bytes, base + limits.memory_bytes)
        meta = {"address_space_at_fork": base, "rlimit_as": base + limits.memory_bytes,
                "rlimit_cpu": [soft, soft + 1]}
        reserve = bytearray(_RESERVE_BYTES)
        value, info = None, None
        try:
            value = fn(*args, **kwargs)
            tag = "ok"
        except MemoryError as exc:
            tag, info = "memory_exceeded", _describe(exc)
        except BaseException as exc:                              # noqa: BLE001
            tag, info = "crashed", _describe(exc)
        if tag != "ok":
            del reserve
        try:
            payload = pickle.dumps((tag, value if tag == "ok" else info, meta),
                                   protocol=pickle.HIGHEST_PROTOCOL)
        except MemoryError as exc:
            value = None
            payload = pickle.dumps(("memory_exceeded", _describe(exc), meta))
        except BaseException as exc:                              # noqa: BLE001 unpicklable value
            value = None
            payload = pickle.dumps(("crashed", {"error_type": "unpicklable_result",
                                                "message": _describe(exc)["message"]}, meta))
        _write_all(w, payload)
        status = 0
    except BaseException:                                         # noqa: BLE001
        status = 71
    finally:
        os._exit(status)


def _still_ours(pid: int, pidfd: int | None) -> bool:
    """Whether the child is still this process's unreaped child (running, or a zombie not yet
    reaped): only then do its pid and group id stay allocated to it. Checked through the pidfd, or
    without one positively through ``waitid(P_PID, …, WNOHANG | WNOWAIT)``; fails closed."""
    try:
        if pidfd is not None:
            os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        else:
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
    except (ChildProcessError, OSError):
        return False
    return True


def _kill(pid: int, group: bool, pidfd: int | None) -> None:
    """SIGKILL the (owned, unreaped) child, and its process group when it leads one (a top-level
    run). The leader is signalled through its pidfd when there is one; the group only while the
    pidfd shows the leader unreaped."""
    def leader():
        if pidfd is not None:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        elif _still_ours(pid, None):
            os.kill(pid, signal.SIGKILL)
    kills = (lambda: _still_ours(pid, pidfd) and os.killpg(pid, signal.SIGKILL), leader)
    for kill in kills if group else kills[1:]:
        try:
            kill()
        except (ProcessLookupError, PermissionError):
            pass


def _group_alive(pgid: int) -> bool:
    """Whether process group ``pgid`` still has a member that is not a zombie."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                             # a member exists that we may not signal
    for entry in os.scandir("/proc"):           # members exist; zombies hold no resources
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/stat") as f:
                stat = f.read()
        except OSError:
            continue
        fields = stat[stat.rindex(")") + 2:].split()
        if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
            return True
    return False


def _wait_exit(pid: int) -> bool:
    """Block until the child has exited, without reaping it: its unreaped zombie keeps the pid and
    the process-group id allocated, so a later signal to them cannot reach a reused process. False
    when the child is no longer ours to wait for (reaped elsewhere: ``ECHILD``)."""
    try:
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
    except ChildProcessError:
        return False
    return True


def _reap(pid: int):
    """Reap the child (``wait4``: status and rusage), or None when it was already reaped."""
    try:
        _, status, ru = os.wait4(pid, 0)
    except ChildProcessError:
        return None
    return status, ru


def _drain_group(pgid: int, pidfd: int | None = None) -> tuple[bool, bool]:
    """Kill what is left of the child's group and wait until no live member remains; return
    (members were left, the group is gone). Called only while the leader is an unreaped zombie
    (:func:`_wait_exit`), so ``pgid`` cannot have been reused by another group (re-review 3 B1)."""
    left = False
    end = time.monotonic() + ESCAPE_GRACE_SECONDS
    while _still_ours(pgid, pidfd) and _group_alive(pgid):
        left = True
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        if time.monotonic() >= end:
            return left, False
        time.sleep(0.01)
    return left, True


def _defer_signals(pending: list[int], saved: dict) -> None:
    """Replace the Python handlers of :data:`_DEFERRED` signals by one that records them (main
    thread only: Python runs signal handlers in the main thread, so no other thread is interrupted)."""
    if threading.current_thread() is not threading.main_thread():
        return

    def record(signum, frame):
        pending.append(signum)
    for sig in _DEFERRED:
        handler = signal.getsignal(sig)
        if handler is None or handler == signal.SIG_IGN:
            continue
        saved[sig] = handler
        signal.signal(sig, record)


def _redeliver(pending: list[int], saved: dict) -> None:
    """Restore the original handlers, then deliver what was recorded while they were deferred."""
    for sig, handler in saved.items():
        signal.signal(sig, handler)
    for sig in dict.fromkeys(pending):
        handler = saved.get(sig)
        if callable(handler):
            handler(sig, None)
        elif handler == signal.SIG_DFL:
            signal.raise_signal(sig)


def run_bounded(fn: Callable[..., Any], *args: Any, limits: Limits, **kwargs: Any) -> BoundedResult:
    """Run ``fn(*args, **kwargs)`` in a forked child under ``limits`` (see the module doc)."""
    if not isinstance(limits, Limits):
        raise TypeError("limits must be a Limits")
    if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
        raise RuntimeError("SIGCHLD is ignored: the kernel would reap the child, so run_bounded "
                           "could not own its pid")
    pending: list[int] = []
    saved: dict = {}
    try:
        _defer_signals(pending, saved)
        return _lifecycle(fn, args, kwargs, limits, pending, saved)
    finally:
        _redeliver(pending, saved)            # after every child, member and descriptor is gone


def _lifecycle(fn, args, kwargs, limits: Limits, pending: list[int], saved: dict) -> BoundedResult:
    nested = _nested()
    r = w = pidfd = pid = None
    owned = lost = killed = oversized = interrupted = False
    chunks: list[bytes] = []
    size, reaped = 0, None
    left, gone = False, True
    t0 = time.monotonic()
    try:
        r, w = os.pipe()
        parent = os.getpid()
        pid = os.fork()
        if pid == 0:                                              # child
            os.close(r)
            _child(fn, args, kwargs, limits, w, nested, saved, parent)
        owned = True                      # the child is ours and not reaped: signals allowed
        os.close(w)
        w = None
        try:
            pidfd = os.pidfd_open(pid)
        except (AttributeError, OSError):
            pidfd = None
        if not nested:
            try:
                os.setpgid(pid, pid)          # also set by the child; whichever runs first wins
            except OSError:
                pass
        deadline = t0 + float(limits.wall_seconds)
        while True:
            if pending:                       # a deferred interrupt: stop the child, clean up
                interrupted = True
                _kill(pid, not nested, pidfd)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                killed = True
                _kill(pid, not nested, pidfd)
                break
            ready, _, _ = select.select([r], [], [], min(remaining, _POLL_SECONDS))
            if not ready:
                continue
            chunk = os.read(r, 1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESULT_BYTES:
                oversized = True
                _kill(pid, not nested, pidfd)
                break
            chunks.append(chunk)
        if _wait_exit(pid):                   # exited but unreaped: pid and group id stay ours
            if not nested:
                left, gone = _drain_group(pid, pidfd)
        else:
            lost = True                       # reaped elsewhere: never signal this id again
        owned = False                         # before the reap: nothing signals this id afterwards
        if not lost:
            reaped = _reap(pid)
            lost = reaped is None
    finally:
        if owned:                             # a synchronous error before the reap
            _kill(pid, not nested, pidfd)
            if _wait_exit(pid):
                if not nested:
                    _drain_group(pid, pidfd)
                reaped = _reap(pid)
            owned = False
        for fd in (r, w, pidfd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
    wall = time.monotonic() - t0
    if lost:
        return BoundedResult("escaped", None, 0.0, wall, 0, {
            "limits": {"cpu_seconds": limits.cpu_seconds, "wall_seconds": limits.wall_seconds,
                       "memory_bytes": limits.memory_bytes},
            "process_group": None if nested else pid, "ownership_lost": True,
            "cpu_complete": False,
            "message": "the child was reaped outside run_bounded; its usage is unmeasured"})
    status, ru = reaped
    cpu = float(ru.ru_utime + ru.ru_stime)
    peak = int(ru.ru_maxrss) * 1024
    detail: dict[str, Any] = {"limits": {"cpu_seconds": limits.cpu_seconds,
                                         "wall_seconds": limits.wall_seconds,
                                         "memory_bytes": limits.memory_bytes},
                              "process_group": None if nested else pid,
                              "stragglers_killed": left,
                              "cpu_complete": not (killed or oversized or left or interrupted)}
    if os.WIFSIGNALED(status):
        detail["signal"] = signal.Signals(os.WTERMSIG(status)).name
    elif os.WIFEXITED(status):
        detail["exit_status"] = os.WEXITSTATUS(status)

    def done(outcome: str, value: Any = None, **more: Any) -> BoundedResult:
        return BoundedResult(outcome, value, cpu, wall, peak, {**detail, **more})

    if not gone:
        return done("escaped", message=f"process group {pid} still had live members "
                                       f"{ESCAPE_GRACE_SECONDS} s after it was killed")
    if interrupted:
        return done("crashed", error_type="interrupted",
                    message=f"signal {pending[0]} arrived; the child was killed and reaped")
    if killed:
        return done("wall_exceeded", message=f"wall-clock ceiling of {limits.wall_seconds} s "
                                             "reached; " + ("child killed" if nested
                                                            else "process group killed"))
    if oversized:
        return done("crashed", error_type="result_too_large",
                    message=f"result exceeds {MAX_RESULT_BYTES} bytes")
    payload = None
    if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0 and chunks:
        try:
            payload = pickle.loads(b"".join(chunks))
        except Exception as exc:                                  # noqa: BLE001
            return done("crashed", error_type="unreadable_result", message=str(exc)[:_MESSAGE])
    if payload is None:
        soft = max(1, math.ceil(limits.cpu_seconds))
        if detail.get("signal") == "SIGXCPU" or (detail.get("signal") == "SIGKILL"
                                                 and cpu >= soft - 0.05):
            return done("cpu_exceeded", message=f"CPU ceiling of {limits.cpu_seconds} s reached")
        return done("crashed", error_type="child_died",
                    message="the child exited without a result")
    tag, body, meta = payload
    detail.update(meta)
    if tag == "ok":
        if cpu > float(limits.cpu_seconds):
            return done("cpu_exceeded", message=f"used {cpu:.3f} CPU-s of a "
                                                f"{limits.cpu_seconds} s ceiling")
        return done("ok", body)
    if tag not in ("memory_exceeded", "crashed"):
        return done("crashed", error_type="bad_result_tag", message=str(tag)[:100])
    return done(tag, **body)
