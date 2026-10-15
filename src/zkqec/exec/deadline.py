"""Composable in-process ``ITIMER_REAL`` deadlines (Codex M1 review B5).

There is one ``ITIMER_REAL`` per process, so nested deadlines must share it. :func:`alarm_deadline`
arms the timer for the **earliest** of its own deadline and an already armed one, and restores the
previous ``SIGALRM`` handler and the previous timer's remaining time on exit:

* if the armed timer is due first, it fires first: its handler runs (zkqec deadlines raise, ending
  the block); if that handler returns, this deadline is armed for its remaining time;
* if this deadline is due first, it raises ``exc_type()``; on exit the outer timer is re-armed with
  what is left of it (immediately, when it expired while this block ran).

The bounded child (:mod:`zkqec.exec.bounded`) is the primary wall limit for evaluator work; these
timers are the in-process limits that remain (L2's ``max_seconds``, admission's unbounded path).
"""
from __future__ import annotations

import os
import signal
import threading
import time
from contextlib import contextmanager
from typing import Iterator

_MIN = 1e-6                     # "now": setitimer(0) would disarm instead of firing


def _invoke(handler, signum, frame) -> None:
    """Run a previous SIGALRM disposition as the kernel would have."""
    if callable(handler):
        handler(signum, frame)
    elif handler in (signal.SIG_DFL, None):
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGALRM)              # default action: terminate


def usable() -> bool:
    """SIGALRM deadlines work only in the main thread of the main interpreter."""
    return hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()


@contextmanager
def alarm_deadline(seconds: float, exc_type: type[BaseException]) -> Iterator[None]:
    """Raise ``exc_type()`` after ``seconds`` unless an earlier armed timer ends the block first."""
    if not usable():
        raise RuntimeError("alarm_deadline needs SIGALRM in the main thread")
    seconds = float(seconds)
    if not seconds > 0:
        raise ValueError("a deadline needs a positive number of seconds")
    start = time.monotonic()
    deadline = start + seconds
    prev_handler = signal.getsignal(signal.SIGALRM)
    prev_remaining, prev_interval = signal.getitimer(signal.ITIMER_REAL)
    outer_first = 0 < prev_remaining <= seconds
    state = {"outer_fired": False}

    def _fire(signum, frame):
        if outer_first and not state["outer_fired"]:
            state["outer_fired"] = True                   # the outer deadline's moment
            signal.setitimer(signal.ITIMER_REAL, max(deadline - time.monotonic(), _MIN))
            _invoke(prev_handler, signum, frame)
            return
        raise exc_type()

    signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, prev_remaining if outer_first else seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prev_handler if prev_handler is not None else signal.SIG_DFL)
        if prev_remaining > 0 and not state["outer_fired"]:
            left = prev_remaining - (time.monotonic() - start)
            signal.setitimer(signal.ITIMER_REAL, max(left, _MIN), prev_interval)
        elif state["outer_fired"] and prev_interval > 0:
            signal.setitimer(signal.ITIMER_REAL, prev_interval, prev_interval)
