"""Sleep and wake: Windows tells every window when the PC goes to sleep and wakes up
(WM_POWERBROADCAST). The engine also notices waking by its clocks jumping, in case a message is
missed; either way it reconnects the dongle and NDI and checks the automations.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012


def power_event(message: int, wparam: int) -> str:
    """'sleeping', 'woke' or '' for a Windows message."""
    if message != WM_POWERBROADCAST:
        return ""
    if wparam == PBT_APMSUSPEND:
        return "sleeping"
    if wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
        return "woke"
    return ""


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_uint), ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]


class PowerEventFilter(QAbstractNativeEventFilter):
    """Calls on_event("sleeping" / "woke") when Windows says so."""

    def __init__(self, on_event: Callable[[str], None]) -> None:
        super().__init__()
        self._on_event = on_event

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt name)
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                msg = _MSG.from_address(int(message))
                kind = power_event(msg.message, msg.wParam)
                if kind:
                    self._on_event(kind)
        except Exception:
            pass  # never let a stray message break the app
        return False, 0
