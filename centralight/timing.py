"""Sleeping for the engine loop, to the millisecond."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable


def begin_precise_timer() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
    except OSError:
        pass


def end_precise_timer() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeEndPeriod(1)
    except OSError:
        pass


def sleep_until(deadline: float, running: Callable[[], bool] | None = None) -> None:
    """Sleep until time.perf_counter() reaches deadline (or running() turns false).

    Python's time.sleep uses Windows' high-resolution timer, so this wakes at most about 1 ms late (0.3 ms typically,
    measured on the church PC). It used to spin for the last 2 ms to be exact to the microsecond: that cost about
    4 % of a core for nothing the lights could show.
    """
    while running is None or running():
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.25))
