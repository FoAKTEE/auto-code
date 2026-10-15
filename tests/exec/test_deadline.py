"""In-process ITIMER_REAL deadlines compose: the earliest deadline fires and the previous timer is
restored on exit (Codex M1 review B5: L2's timer replaced and cancelled admission's wall deadline)."""
import signal
import time

import pytest

from zkqec.admission.service import _WallDeadline, _wall_deadline
from zkqec.verify.distance import _Deadline, _deadline


@pytest.fixture(autouse=True)
def no_timer_leak():
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
    yield
    signal.setitimer(signal.ITIMER_REAL, 0)


def test_a_later_inner_deadline_does_not_override_the_outer_one():
    t0 = time.monotonic()
    with pytest.raises(_WallDeadline):
        with _wall_deadline(0.5):                     # admission: the job's wall reservation
            with _deadline(5):                        # L2 distance_budget.max_seconds
                time.sleep(3)
    assert time.monotonic() - t0 < 1.5


def test_an_earlier_inner_deadline_is_enforced_under_an_outer_one():
    t0 = time.monotonic()
    with pytest.raises(_WallDeadline):
        with _deadline(5):
            with _wall_deadline(0.5):                 # previously skipped when a timer was armed
                time.sleep(3)
    assert time.monotonic() - t0 < 1.5


def test_the_outer_deadline_survives_an_inner_one_that_fired():
    t0 = time.monotonic()
    with pytest.raises(_WallDeadline):
        with _wall_deadline(1.0):
            with pytest.raises(_Deadline):
                with _deadline(0.2):
                    time.sleep(3)
            assert time.monotonic() - t0 < 0.6
            time.sleep(3)                             # the outer 1 s deadline still fires
    assert time.monotonic() - t0 < 2.0


def test_the_outer_deadline_survives_an_inner_one_that_finished():
    t0 = time.monotonic()
    with pytest.raises(_WallDeadline):
        with _wall_deadline(1.0):
            with _deadline(0.5):
                pass
            remaining = signal.getitimer(signal.ITIMER_REAL)[0]
            assert 0.5 < remaining <= 1.0             # restored, minus the elapsed time
            time.sleep(3)
    assert time.monotonic() - t0 < 2.0


def test_previous_handler_and_timer_are_restored():
    fired = []

    def outer(signum, frame):
        fired.append(time.monotonic())
    previous = signal.signal(signal.SIGALRM, outer)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.8)
        t0 = time.monotonic()
        with _deadline(0.3):
            pass
        with _wall_deadline(0.3):
            pass
        assert signal.getsignal(signal.SIGALRM) is outer
        time.sleep(1.5)
        assert len(fired) == 1 and 0.6 < fired[0] - t0 < 1.2
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_an_outer_deadline_that_expired_inside_the_inner_block_fires_on_exit():
    def outer(signum, frame):
        raise TimeoutError("outer")
    previous = signal.signal(signal.SIGALRM, outer)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.3)
        with pytest.raises(TimeoutError):
            with _deadline(0.2):                      # earlier: armed, outer suspended
                try:
                    time.sleep(1)
                except _Deadline:
                    time.sleep(0.3)                   # outer deadline passes in here
            time.sleep(2)                             # must not get here
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
