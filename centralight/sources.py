"""SourceManager: shares NDI receivers between everything that needs a picture.

Users are named (base, overlay1-4, transition, solo, editor). NDI feeds used by any
input stay connected all the time (in practice just Media Background Pane), so sending
an input is instant. Other feeds stop 10 seconds after their last user lets go.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from .ndi_source import NdiDiscovery, NdiReceiver, SourceFrame, SourceStatus, _Use

LINGER_SECONDS = 10.0


class SourceManager:
    def __init__(
        self,
        ndi: Any | None = None,
        *,
        linger: float = LINGER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        receiver_options: dict | None = None,
    ) -> None:
        self._ndi = ndi
        self._linger = linger
        self._clock = clock
        self._options = receiver_options or {}
        self._lock = threading.Lock()
        self._uses: dict[str, _Use] = {}  # wanted name (lower case) -> receiver and its users
        self._keep: set[str] = set()
        self.discovery = NdiDiscovery(ndi)
        self._started = False
        self._closed = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            self.discovery.start()

    def close(self) -> None:
        self._closed = True
        with self._lock:
            uses = list(self._uses.values())
            self._uses.clear()
        for use in uses:
            use.receiver.stop()
        self.discovery.stop()

    # ------------------------------------------------------------ who needs what

    def set_keep(self, names: Iterable[str]) -> None:
        """Feeds used by any input: kept connected whether or not they're on air."""
        wanted = {n.strip().lower() for n in names if n and n.strip()}
        with self._lock:
            self._keep = wanted
            for key in wanted:
                self._ensure(key)

    def acquire(self, name: str, owner: str) -> None:
        key = name.strip().lower()
        if not key:
            return
        with self._lock:
            use = self._ensure(key)
            use.owners.add(owner)
            use.idle_since = None

    def release(self, name: str, owner: str) -> None:
        key = name.strip().lower()
        with self._lock:
            use = self._uses.get(key)
            if use is not None:
                use.owners.discard(owner)
                if not use.owners:
                    use.idle_since = self._clock()

    def tidy(self) -> None:
        """Stop feeds nobody has used for a while (the engine calls this now and then)."""
        now = self._clock()
        stopping = []
        with self._lock:
            for key, use in list(self._uses.items()):
                if key in self._keep or use.owners:
                    continue
                if use.idle_since is None:
                    use.idle_since = now
                elif now - use.idle_since >= self._linger:
                    stopping.append(use.receiver)
                    del self._uses[key]
        for receiver in stopping:
            receiver.stop()

    def _ensure(self, key: str) -> _Use:
        use = self._uses.get(key)
        if use is None:
            receiver = NdiReceiver(key, self.discovery, self._ndi, **self._options)
            use = _Use(receiver)
            self._uses[key] = use
            if not self._closed:
                self.start()
                receiver.start()
        return use

    # ------------------------------------------------------------ pictures and status

    def frame(self, name: str) -> SourceFrame | None:
        with self._lock:
            use = self._uses.get(name.strip().lower())
        return use.receiver.latest() if use is not None else None

    def statuses(self) -> dict[str, SourceStatus]:
        with self._lock:
            receivers = {key: use.receiver for key, use in self._uses.items()}
        return {key: receiver.status() for key, receiver in receivers.items()}

    def available(self) -> list[str]:
        """Names of the NDI sources that can be seen right now."""
        return self.discovery.names()
