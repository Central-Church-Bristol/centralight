"""NDI video in: finding sources on the network, and receiving one of them.

- NdiDiscovery (thread "ndi-discovery") keeps the list of NDI sources up to date.
- NdiReceiver (thread "ndi-<name>") follows one source by name, e.g. "Media Background
  Pane" (any source whose name contains it, ignoring case). Each frame is shrunk to at
  most 320x180 straight away and freed; the newest one waits in a LatestSlot.
  If the picture stops, the last frame is held (so the lights keep their colour) and
  the status says "no signal". It reconnects by itself when the source comes back,
  for example after Media Background Pane restarts.

Reading NDI never touches the lights. The NDI library is passed in, so tests can use a
fake one.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .log import log_exception, log_info
from .slot import LatestSlot

MAX_W = 320
MAX_H = 180
NO_SIGNAL_SECONDS = 1.0  # no frame for this long = "no signal"
RECONNECT_SECONDS = 3.0  # not connected (NDI says no connection) for this long: start again
QUIET_RECONNECT_SECONDS = 30.0  # connected but no pictures: try a fresh connection this often
RECEIVER_NAME = "CentraLight"

SEARCHING = "searching"
CONNECTED = "connected"
NO_SIGNAL = "no-signal"
STOPPED = "stopped"
ERROR = "error"

_init_lock = threading.Lock()
_initialized: set[int] = set()


def load_ndi() -> Any:
    import NDIlib

    return NDIlib


def ensure_initialized(ndi: Any) -> bool:
    """Call ndi.initialize() once per library (it checks the CPU can run NDI)."""
    with _init_lock:
        if id(ndi) in _initialized:
            return True
        ok = bool(ndi.initialize())
        if ok:
            _initialized.add(id(ndi))
        return ok


RESTART_AFTER_ERROR = 2.0  # seconds before an NDI thread that hit an unexpected error starts again


def matches(wanted: str, ndi_name: str) -> bool:
    return wanted.strip().lower() in ndi_name.lower()


@dataclass(frozen=True)
class SourceFrame:
    image: np.ndarray  # (h, w, 3) uint8 RGB, at most 320x180
    source_size: tuple[int, int]  # (width, height) as sent
    time: float  # time.monotonic() when it arrived
    seq: int


@dataclass(frozen=True)
class SourceStatus:
    wanted: str
    state: str = SEARCHING
    found_name: str = ""  # the full NDI name, e.g. "AV-PC (Media Background Pane)"
    fps: float = 0.0
    size: tuple[int, int] = (0, 0)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.state == CONNECTED


def shrink(data: np.ndarray, xres: int, yres: int) -> np.ndarray:
    """BGRX/BGRA frame -> RGB uint8 no bigger than 320x180 (keeping its shape)."""
    import cv2

    frame = np.asarray(data)
    if frame.ndim == 2:  # (height, stride) bytes
        frame = frame.reshape(yres, -1, 4)
    frame = frame[:yres, :xres, :]
    scale = min(MAX_W / max(1, xres), MAX_H / max(1, yres), 1.0)
    width = max(1, round(xres * scale))
    height = max(1, round(yres * scale))
    if (width, height) != (xres, yres):
        frame = cv2.resize(np.ascontiguousarray(frame), (width, height), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(frame[:, :, 2::-1])  # BGR(X) -> RGB


class NdiDiscovery:
    """Keeps a list of the NDI sources on the network (and this PC)."""

    def __init__(self, ndi: Any | None = None, interval: float = 1.0) -> None:
        self._ndi = ndi
        self._interval = interval
        self._lock = threading.Lock()
        self._sources: list[tuple[str, str]] = []  # (ndi name, url)
        self._running = False
        self._thread: threading.Thread | None = None
        self.changed = threading.Event()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="ndi-discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(3.0)

    def sources(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._sources)

    def names(self) -> list[str]:
        return [name for name, _url in self.sources()]

    def find(self, wanted: str) -> tuple[str, str] | None:
        for name, url in self.sources():
            if matches(wanted, name):
                return name, url
        return None

    def _run(self) -> None:
        """Keeps going whatever happens: an unexpected error restarts it after a moment."""
        while True:
            self._failed = False
            self._run_once()
            if not (self._running and self._failed):
                return
            time.sleep(RESTART_AFTER_ERROR)

    def _run_once(self) -> None:
        finder = None
        try:
            ndi = self._ndi or load_ndi()
            self._ndi = ndi
            if not ensure_initialized(ndi):
                log_info("NDI couldn't start on this PC (ndi.initialize failed)")
                return
            finder = ndi.find_create_v2()
            while self._running:
                ndi.find_wait_for_sources(finder, int(self._interval * 1000))
                found = [(s.ndi_name, getattr(s, "url_address", "") or "") for s in ndi.find_get_current_sources(finder)]
                with self._lock:
                    if found != self._sources:
                        self._sources = found
                        self.changed.set()
        except Exception as exc:
            self._failed = True
            log_exception("ndi-discovery", exc)
        finally:
            if finder is not None:
                try:
                    self._ndi.find_destroy(finder)
                except Exception:
                    pass


class NdiReceiver:
    """Receives one NDI source (found by name) on its own thread."""

    def __init__(
        self,
        wanted: str,
        discovery: NdiDiscovery,
        ndi: Any | None = None,
        *,
        no_signal_after: float = NO_SIGNAL_SECONDS,
        reconnect_after: float = RECONNECT_SECONDS,
        quiet_reconnect_after: float = QUIET_RECONNECT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.wanted = wanted
        self.frames: LatestSlot[SourceFrame] = LatestSlot()
        self._discovery = discovery
        self._ndi = ndi
        self._no_signal_after = no_signal_after
        self._reconnect_after = reconnect_after
        self._quiet_reconnect_after = quiet_reconnect_after
        self._clock = clock
        self._lock = threading.Lock()
        self._status = SourceStatus(wanted)
        self._arrivals: deque[float] = deque(maxlen=120)
        self._seq = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self.connects = 0  # how many times a receiver was created (for tests and diagnostics)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name=f"ndi-{self.wanted[:24]}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(3.0)
        self._set(state=STOPPED)

    def status(self) -> SourceStatus:
        now = self._clock()
        with self._lock:
            while self._arrivals and now - self._arrivals[0] > 2.0:
                self._arrivals.popleft()
            span = self._arrivals[-1] - self._arrivals[0] if len(self._arrivals) >= 2 else 0.0
            fps = (len(self._arrivals) - 1) / span if span > 0 else 0.0
            status = self._status
        return SourceStatus(status.wanted, status.state, status.found_name, fps, status.size, status.message)

    def latest(self) -> SourceFrame | None:
        return self.frames.get()

    def _set(self, **changes: Any) -> None:
        with self._lock:
            values = {**self._status.__dict__, **changes}
            self._status = SourceStatus(**values)

    def _run(self) -> None:
        """Keeps going whatever happens: an unexpected error restarts the receiver after a moment (the lights hold
        the last picture meanwhile) instead of leaving them stuck until CentraLight restarts."""
        while True:
            self._failed = False
            self._run_once()
            if not (self._running and self._failed):
                return
            time.sleep(RESTART_AFTER_ERROR)

    def _run_once(self) -> None:
        ndi = None
        recv = None
        last_frame: float | None = None  # when the newest picture arrived
        connected_at = 0.0
        connected_to: tuple[str, str] | None = None
        vanished = False  # the source left the network while we were connected
        unconnected_since: float | None = None
        try:
            ndi = self._ndi or load_ndi()
            if not ensure_initialized(ndi):
                self._set(state=ERROR, message="NDI couldn't start on this PC")
                return
            while self._running:
                if recv is None:
                    found = self._discovery.find(self.wanted)
                    if found is None:
                        self._set(state=SEARCHING if last_frame is None else NO_SIGNAL, found_name="",
                                  message=f"Looking for an NDI source called “{self.wanted}”")
                        self._discovery.changed.wait(0.5)
                        self._discovery.changed.clear()
                        continue
                    recv = self._connect(ndi, found)
                    connected_at = self._clock()
                    connected_to = found
                    vanished = False
                    unconnected_since = None
                    continue
                # A restart of the source (it vanished and came back, or moved): connect afresh at once
                found = self._discovery.find(self.wanted)
                if found is None:
                    vanished = True
                elif vanished or found != connected_to:
                    log_info(f"NDI {self.wanted}: the source restarted or moved; reconnecting")
                    recv = self._disconnect(ndi, recv)
                    continue
                kind, video, _audio, _meta = ndi.recv_capture_v2(recv, 100, want_audio=False, want_metadata=False)
                now = self._clock()
                if kind == ndi.FRAME_TYPE_VIDEO:
                    try:
                        image = shrink(video.data, int(video.xres), int(video.yres))
                        size = (int(video.xres), int(video.yres))
                    finally:
                        ndi.recv_free_video_v2(recv, video)
                    self._seq += 1
                    last_frame = now
                    unconnected_since = None
                    with self._lock:
                        self._arrivals.append(now)
                    self.frames.put(SourceFrame(image, size, now, self._seq))
                    if self._status.state != CONNECTED or self._status.size != size:
                        self._set(state=CONNECTED, size=size, message="")
                    continue
                if kind == ndi.FRAME_TYPE_ERROR:
                    log_info(f"NDI {self.wanted}: the connection reported an error; reconnecting", key=f"ndi-error-{self.wanted}")
                    recv = self._disconnect(ndi, recv)
                    time.sleep(1.0)
                    continue
                silent_for = now - (last_frame if last_frame is not None else connected_at)
                if silent_for > self._no_signal_after:
                    if last_frame is None:
                        self._set(state=SEARCHING, message=f"Connected to {self._status.found_name}, waiting for a picture")
                    elif self._status.state != NO_SIGNAL:
                        self._set(state=NO_SIGNAL, message="The source isn't sending pictures: holding the last colours")
                    # Only reconnect if NDI says we've lost the connection, or (rarely) after a long quiet spell,
                    # so a source that's simply quiet isn't pestered with new connections
                    if _connections(ndi, recv) == 0:
                        unconnected_since = unconnected_since if unconnected_since is not None else now
                    else:
                        unconnected_since = None
                    lost = unconnected_since is not None and now - unconnected_since > self._reconnect_after
                    if lost or now - connected_at > self._quiet_reconnect_after:
                        recv = self._disconnect(ndi, recv)
        except Exception as exc:
            self._failed = True
            log_exception(f"ndi receiver {self.wanted}", exc)
            self._set(state=ERROR, message=str(exc))
        finally:
            if recv is not None and ndi is not None:
                self._disconnect(ndi, recv)

    def _connect(self, ndi: Any, found: tuple[str, str]) -> Any:
        name, url = found
        settings = ndi.RecvCreateV3()
        settings.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
        settings.bandwidth = ndi.RECV_BANDWIDTH_HIGHEST
        settings.ndi_recv_name = RECEIVER_NAME
        recv = ndi.recv_create_v3(settings)
        ndi.recv_connect(recv, ndi.Source(name, url or None))
        self.connects += 1
        if self.connects == 1 or self._status.found_name != name:
            log_info(f"NDI: connecting to {name}")
        self._set(found_name=name)
        return recv

    def _disconnect(self, ndi: Any, recv: Any) -> None:
        try:
            ndi.recv_destroy(recv)
        except Exception as exc:
            log_exception("ndi recv_destroy", exc)
        return None


def _connections(ndi: Any, recv: Any) -> int:
    """How many connections the receiver has (1 when it's linked to the source); -1 if unknown."""
    try:
        return int(ndi.recv_get_no_connections(recv))
    except Exception:
        return -1


@dataclass
class _Use:
    receiver: NdiReceiver
    owners: set[str] = field(default_factory=set)
    idle_since: float | None = None
