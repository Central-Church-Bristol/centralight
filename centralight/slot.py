"""LatestSlot: a hand-off box between threads that only keeps the newest value.

The engine puts a frame in 40 times a second; the screen and the DMX output take
whatever is newest when they're ready. Nobody ever waits for a slow reader.
"""

from __future__ import annotations

import threading
from typing import Generic, TypeVar

T = TypeVar("T")


class LatestSlot(Generic[T]):
    def __init__(self, value: T | None = None) -> None:
        self._cond = threading.Condition()
        self._value: T | None = value
        self._version = 0

    def put(self, value: T) -> int:
        """Replace the value; returns its version number (1, 2, 3...)."""
        with self._cond:
            self._value = value
            self._version += 1
            self._cond.notify_all()
            return self._version

    def get(self) -> T | None:
        with self._cond:
            return self._value

    def get_versioned(self) -> tuple[int, T | None]:
        with self._cond:
            return self._version, self._value

    def wait_newer(self, version: int, timeout: float | None) -> tuple[int, T | None]:
        """Wait until the version differs from ``version`` (or the timeout passes)."""
        with self._cond:
            self._cond.wait_for(lambda: self._version != version, timeout)
            return self._version, self._value
