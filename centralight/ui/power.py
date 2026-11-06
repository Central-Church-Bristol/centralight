"""Sleep, wake and shutdown: Windows tells every window when the PC goes to sleep and wakes up
(WM_POWERBROADCAST), and asks them before it shuts down, restarts or signs out (WM_QUERYENDSESSION, then
WM_ENDSESSION). The engine also notices waking by its clocks jumping, in case a message is
missed; either way it reconnects the dongle and NDI and checks the automations.

Shutting down and going to sleep (M18): the global automations "The PC shuts down or restarts" and "The PC goes to
sleep" run at once, and Windows waits while their fade runs and the last frame goes out: at most SHUTDOWN_WAIT seconds
(Windows waits about 5), with "CentraLight: fading the lights out" on its shutdown screen meanwhile, or SLEEP_WAIT
going to sleep (it can't be held). Then Windows carries on. A power cut, holding the power button or a crash can't be
caught.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

from ..commands import SHUTDOWN_FADE, SLEEP_FADE, PowerEvent
from ..log import log_exception, log_info

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
ENDSESSION_CLOSEAPP = 0x00000001  # (Windows closing CentraLight for an update: it stops then too)
SHUTDOWN_WAIT = SHUTDOWN_FADE + 0.5  # seconds Windows' shutdown waits for the lights at most (it waits about 5)
SLEEP_WAIT = SLEEP_FADE + 0.5  # ...and going to sleep (Windows allows about 2 s)
WAIT_STEP = 0.02  # seconds between looks while it waits
SAME_SLEEP = 30.0  # seconds: "going to sleep" told again this soon (to another window) is the same sleep
BLOCK_REASON = "CentraLight: fading the lights out"


def power_event(message: int, wparam: int, lparam: int = 0) -> str:
    """'sleeping', 'woke', 'shutdown' (Windows asks whether it may shut down, restart or sign out: or close CentraLight
    for an update, lparam ENDSESSION_CLOSEAPP, as CentraLight stops then too), 'shutdown cancelled' (it won't after
    all) or '' for a Windows message."""
    if message == WM_QUERYENDSESSION:
        return "shutdown"
    if message == WM_ENDSESSION:
        return "" if wparam else "shutdown cancelled"
    if message != WM_POWERBROADCAST:
        return ""
    if wparam == PBT_APMSUSPEND:
        return "sleeping"
    if wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
        return "woke"
    return ""


def block_shutdown(hwnd: int, reason: str) -> bool:
    """Windows' shutdown screen says why CentraLight is holding it up (ShutdownBlockReasonCreate)."""
    create = ctypes.WinDLL("user32").ShutdownBlockReasonCreate
    create.argtypes, create.restype = (ctypes.c_void_p, ctypes.c_wchar_p), ctypes.c_int
    return bool(create(hwnd, reason))


def unblock_shutdown(hwnd: int) -> bool:
    """...and that it isn't any more (ShutdownBlockReasonDestroy)."""
    destroy = ctypes.WinDLL("user32").ShutdownBlockReasonDestroy
    destroy.argtypes, destroy.restype = (ctypes.c_void_p,), ctypes.c_int
    return bool(destroy(hwnd))


class PowerDown:
    """What Windows' power messages do. submit: the engine's. block / unblock: ShutdownBlockReasonCreate and
    ShutdownBlockReasonDestroy, clock / sleep: the clock it waits by (tests pass fakes)."""

    def __init__(self, submit: Callable[[PowerEvent], None], *, block: Callable[[int, str], bool] = block_shutdown,
                 unblock: Callable[[int], bool] = unblock_shutdown, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._submit = submit
        self._block, self._unblock = block, unblock
        self._clock, self._sleep = clock, sleep
        # (Windows tells each of CentraLight's windows in turn: the lights are seen to at the first)
        self._ending = False
        self._slept: float | None = None  # when the last "going to sleep" came (until the PC wakes)

    def __call__(self, kind: str, hwnd: int = 0) -> None:
        if kind == "woke":
            self._slept = None
            self._submit(PowerEvent("woke"))
        elif kind == "sleeping":
            if self._slept is not None and 0.0 <= self._clock() - self._slept < SAME_SLEEP:
                return
            self._slept = self._clock()
            log_info("The PC is going to sleep")
            self._wait("sleeping", SLEEP_WAIT)
        elif kind == "shutdown" and not self._ending:
            self._ending = True
            log_info("Windows is shutting down, restarting or signing out")
            blocked = self._safely(self._block, hwnd, BLOCK_REASON) if hwnd else False
            try:
                self._wait("shutdown", SHUTDOWN_WAIT)
            finally:
                if blocked:
                    self._safely(self._unblock, hwnd)
        elif kind == "shutdown cancelled" and self._ending:
            self._ending = False
            log_info("Windows didn't shut down after all: CentraLight carries on")

    def _wait(self, kind: str, most: float) -> bool:
        """The engine runs the global automations about it: wait until their fades are over and the last frame has
        gone out, but never longer than `most` seconds. True if they got there in time."""
        done = threading.Event()
        self._submit(PowerEvent(kind, done))
        deadline = self._clock() + most
        while not done.is_set():
            if self._clock() >= deadline:
                log_info(f"The lights didn't get there within {most:g} s: Windows carries on anyway")
                return False
            self._sleep(WAIT_STEP)
        return True

    @staticmethod
    def _safely(call: Callable[..., bool], *args) -> bool:
        try:
            return bool(call(*args))
        except Exception as exc:  # (never stop Windows shutting down)
            log_exception("the shutdown message", exc)
            return False


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_uint), ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]


class PowerEventFilter(QAbstractNativeEventFilter):
    """Calls on_event(kind, the window's handle) when Windows says so ("sleeping", "woke", "shutdown", "shutdown
    cancelled"). It returns before Windows carries on, so PowerDown can hold the shutdown while the lights fade; Qt then
    answers Windows as before (and quits as Windows ends the session)."""

    def __init__(self, on_event: Callable[[str, int], None]) -> None:
        super().__init__()
        self._on_event = on_event

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt name)
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                msg = _MSG.from_address(int(message))
                kind = power_event(msg.message, msg.wParam, msg.lParam)
                if kind:
                    self._on_event(kind, int(msg.hwnd or 0))
        except Exception:
            pass  # never let a stray message break the app
        return False, 0
