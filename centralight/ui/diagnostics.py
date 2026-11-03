"""Diagnostics: how CentraLight is doing right now, for when something seems wrong (or to see it's all fine).

The engine (ticks a second, time per tick, faults, restarts), the output (what it sends to, how often), NDI feeds
(frames a second, size), audio (how often sound arrives), what's playing, automations, and this process's CPU and
memory. "Copy" puts it all on
the clipboard as text, to paste into a message; "Open the log" opens session.log.
"""

from __future__ import annotations

import ctypes
import os
import time
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..paths import session_log_path


class _Times(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


class _Memory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def _api():
    """The two Windows functions, with their types declared (a 64-bit handle mustn't be cut to 32 bits)."""
    kernel32 = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_Times)] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Memory), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return kernel32, psapi


class ProcessMeter:
    """This process's CPU use (% of one core, between two looks) and memory (MB)."""

    def __init__(self) -> None:
        self._last: tuple[float, float] | None = None
        try:
            self._kernel32, self._psapi = _api()
        except (AttributeError, OSError):
            self._kernel32 = self._psapi = None

    def _cpu_seconds(self) -> float:
        if self._kernel32 is None:
            return time.process_time()
        creation, exit_, kernel, user = _Times(), _Times(), _Times(), _Times()
        ok = self._kernel32.GetProcessTimes(self._kernel32.GetCurrentProcess(), ctypes.byref(creation), ctypes.byref(exit_),
                                            ctypes.byref(kernel), ctypes.byref(user))
        if not ok:
            return time.process_time()
        return ((kernel.high << 32 | kernel.low) + (user.high << 32 | user.low)) / 1e7

    def cpu(self) -> float | None:
        now = (time.monotonic(), self._cpu_seconds())
        last, self._last = self._last, now
        if last is None or now[0] - last[0] <= 0:
            return None
        return 100.0 * (now[1] - last[1]) / (now[0] - last[0])

    def memory_mb(self) -> float | None:
        if self._psapi is None:
            return None
        counters = _Memory()
        counters.cb = ctypes.sizeof(_Memory)
        if not self._psapi.GetProcessMemoryInfo(self._kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return counters.PrivateUsage / 1e6


def report(frame, meter: ProcessMeter | None = None, cores: int | None = None) -> list[tuple[str, str]]:
    """(what, how it is) lines for a frame from the engine."""
    lines: list[tuple[str, str]] = []
    if frame is None:
        return [("Engine", "not started yet")]
    lines.append(("Engine", f"{frame.tick_rate:.0f} ticks a second · {frame.tick_ms:.2f} ms a tick on average, "
                            f"{frame.tick_max_ms:.1f} ms at worst"))
    trouble = []
    if frame.faults:
        trouble.append(f"{frame.faults} ticks failed")
    if frame.restarts:
        trouble.append(f"restarted {frame.restarts} times")
    lines.append(("Problems", ", ".join(trouble) + " (see the log)" if trouble else "none"))
    output = frame.output
    lines.append(("Output", f"{output.label or output.kind}: {output.state} · {output.rate:.0f} packets a second, "
                            f"{output.sent} sent" + (f" · {output.message}" if output.message else "")))
    if frame.ndi:
        for name, status in frame.ndi.items():
            size = f" · {status.size[0]}×{status.size[1]}" if getattr(status, "size", None) else ""
            lines.append((f"NDI {status.found_name or name}", f"{status.state} · {status.fps:.0f} pictures a second{size}"
                          + (f" · {status.message}" if status.message else "")))
    else:
        lines.append(("NDI", "no feeds in use"))
    level = round(float(frame.audio.get("level", 0.0)) * 100) if frame.audio else 0
    listening = frame.audio_state == "listening"
    lines.append(("Audio", f"{frame.audio_state}" + (f": {frame.audio_detail}" if frame.audio_detail else "")
                           + (f" · sound arriving {frame.audio_rate:.0f} times a second" if listening else "")
                           + (f" · loudness {level} %" if listening else "")))
    overlays = ", ".join(f"{i}: {v.name}" for i, v in enumerate(frame.overlays, start=1) if v.on and v.name) or "none"
    lines.append(("Playing", f"Base: {frame.base} · overlays: {overlays}" + (f" · Editor showing {frame.solo}" if frame.solo else "")))
    auto = frame.automations
    lines.append(("Automations", "paused" if auto.paused else ("on" + (f" · next: {auto.next_text}" if getattr(auto, "next_text", "") else ""))))
    if frame.element_faults:
        lines.append(("Elements with problems", "; ".join(f"{k}: {v}" for k, v in list(frame.element_faults.items())[:5])))
    if meter is not None:
        cpu = meter.cpu()
        memory = meter.memory_mb()
        cores = cores or os.cpu_count() or 1
        cpu_text = "measuring…" if cpu is None else f"{cpu:.1f} % of one core ({cpu / cores:.2f} % of the PC)"
        lines.append(("CentraLight's CPU", cpu_text))
        lines.append(("CentraLight's memory", "?" if memory is None else f"{memory:.0f} MB"))
    return lines


class DiagnosticsDialog(QDialog):
    def __init__(self, frame_provider: Callable[[], object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Diagnostics")
        self._frame = frame_provider
        self._meter = ProcessMeter()  # its first look (in the first refresh) starts the measuring
        self._rows: dict[str, QLabel] = {}
        self._lines: list[tuple[str, str]] = []
        self.form = QFormLayout()
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        intro = QLabel("How CentraLight is doing right now. If something's wrong, Copy and paste this into a message.")
        intro.setObjectName("Hint")
        intro.setWordWrap(True)
        copy = QPushButton("Copy")
        copy.setToolTip("Put all of this on the clipboard, as text")
        copy.clicked.connect(self.copy)
        log = QPushButton("Open the log")
        log.clicked.connect(self._open_log)
        buttons_row = QHBoxLayout()
        buttons_row.addWidget(copy)
        buttons_row.addWidget(log)
        buttons_row.addStretch(1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(self.form)
        layout.addLayout(buttons_row)
        layout.addStretch(1)  # spare height goes below, not round the words at the top
        layout.addWidget(close)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()
        self.resize(640, 420)

    def lines(self) -> list[tuple[str, str]]:
        return report(self._frame(), self._meter)

    def refresh(self) -> None:
        self._lines = self.lines()
        for what, how in self._lines:
            label = self._rows.get(what)
            if label is None:
                label = QLabel()
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self._rows[what] = label
                self.form.addRow(what, label)
            label.setText(how)
        showing = {what for what, _ in self._lines}
        for what, label in self._rows.items():  # e.g. an NDI feed nothing uses any more
            self.form.setRowVisible(label, what in showing)

    def text(self) -> str:
        """What the window shows now (not a fresh look: that would restart the CPU measuring)."""
        return "\n".join(f"{what}: {how}" for what, how in self._lines)

    def copy(self) -> str:
        text = "CentraLight diagnostics, " + time.strftime("%d %b %Y %H:%M") + "\n" + self.text()
        QGuiApplication.clipboard().setText(text)
        return text

    def _open_log(self) -> None:
        path = session_log_path()
        if path.exists():
            os.startfile(path)  # noqa: S606 (opens it in Notepad)

    def done(self, result: int) -> None:  # noqa: D401 (Qt name)
        self._timer.stop()
        super().done(result)
