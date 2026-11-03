"""Carries the engine's newest frame to the screen, about 10 times a second.

The engine never waits for the screen: it drops frames into a LatestSlot, and this
timer picks up whatever is newest. While the window is minimised it slows right
down (the live state is still saved).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

FAST_MS = 100
SLOW_MS = 500


class EngineBridge(QObject):
    frame_ready = Signal(object)  # EngineFrame

    def __init__(self, engine: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._version = -1
        self._timer = QTimer(self)
        self._timer.setInterval(FAST_MS)
        self._timer.timeout.connect(self.poll)
        self._timer.start()

    def poll(self) -> None:
        version, frame = self._engine.frames.get_versioned()
        if frame is not None and version != self._version:
            self._version = version
            self.frame_ready.emit(frame)

    def set_slow(self, slow: bool) -> None:
        self._timer.setInterval(SLOW_MS if slow else FAST_MS)

    def stop(self) -> None:
        self._timer.stop()
