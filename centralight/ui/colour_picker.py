"""The colour picker (M14): with Preview ticked, each change applies at once, so the viewports follow and, if the
input is on the lights, the lights do too, at most about 20 times a second. OK keeps it; Cancel puts the colour back.

Preview is ticked unless someone unticked it since the PC last started: the choice is kept with the PC's start time
and forgotten after a restart (the user: "I think it should reset when the PC restarts").
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QPushButton, QVBoxLayout, QWidget

LIVE_EVERY_MS = 50  # at most about 20 changes a second reach the show (and the engine), so neither is flooded
BOOT_SLACK = 120.0  # seconds: the PC's start time as worked out now can differ a little from one worked out earlier


class PreviewChoice:
    """Whether Preview is ticked: stored (by the main window) as {"on": bool, "boot": the PC's start time}."""

    def __init__(self) -> None:
        self.load: Callable[[], Any] = lambda: None  # what was stored, or None
        self.store: Callable[[dict], None] = lambda _data: None

    def wanted(self) -> bool:
        data = self.load()
        if not isinstance(data, dict) or not isinstance(data.get("boot"), (int, float)):
            return True
        if abs(float(data["boot"]) - pc_started_at()) > BOOT_SLACK:
            return True  # (the PC has restarted since: ticked again)
        return bool(data.get("on", True))

    def remember(self, on: bool) -> None:
        self.store({"on": bool(on), "boot": round(pc_started_at())})


def pc_started_at() -> float:
    """When the PC started (seconds since 1970), from how long it has been up."""
    from ..automations import system_uptime

    return time.time() - system_uptime()


preview_choice = PreviewChoice()


class _Picker(QColorDialog):
    """Qt's colour picker as a part of the window around it: Enter and Esc are that window's (OK and Cancel). Qt's own
    would close just the picker, leaving the window open without it."""

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.ignore()  # (on to the window)
            return
        super().keyPressEvent(event)


class LiveColourDialog(QDialog):
    """Qt's colour picker, with a Preview tick box: while it's ticked, changed(colour) comes at most every 50 ms."""

    changed = Signal(str)  # "#RRGGBB"

    def __init__(self, colour: str, preview: bool, live: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a colour")
        self.picker = _Picker(QColor(colour), self)
        self.picker.setOptions(QColorDialog.ColorDialogOption.NoButtons | QColorDialog.ColorDialogOption.DontUseNativeDialog)
        self.picker.setWindowFlags(Qt.WindowType.Widget)
        self.preview = QCheckBox("Preview: show each change straight away (on the lights too, if they're showing it)")
        self.preview.setChecked(preview)
        self.preview.setVisible(live)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.picker)
        layout.addWidget(self.preview)
        layout.addWidget(buttons)
        # Enter is OK. Qt's picker makes its own buttons the default ("Pick Screen Color", once it has had the focus),
        # so Enter would start picking a colour off the screen instead
        for button in self.picker.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setDefault(True)
        self._start = QColor(colour).name().upper()
        self._pending: str | None = None
        self._shown = self._start  # the colour Preview has applied (the start, until something changes)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LIVE_EVERY_MS)
        self._timer.timeout.connect(self.flush)
        self.picker.currentColorChanged.connect(self._colour_changed)
        self.preview.toggled.connect(self._preview_toggled)
        self._live = live

    def colour(self) -> str:
        return self.picker.currentColor().name().upper()

    def tried(self) -> bool:
        """Has Preview applied a colour other than the one it started with?"""
        return self._shown != self._start

    def _colour_changed(self, colour: QColor) -> None:
        if self._live and self.preview.isChecked():
            self._pending = colour.name().upper()
            if not self._timer.isActive():  # (the newest one goes when the timer ends)
                self._timer.start()

    def _preview_toggled(self, on: bool) -> None:
        # unticked: the colour goes back to how it was until OK; ticked again: the one picked now shows
        self._pending = self.colour() if on else self._start
        self.flush()

    def flush(self) -> None:
        self._timer.stop()
        if self._pending is not None:
            text, self._pending = self._pending, None
            if text != self._shown:
                self._shown = text
                self.changed.emit(text)

    def done(self, result: int) -> None:  # noqa: D401 (Qt name)
        self._timer.stop()
        self._pending = None
        super().done(result)
