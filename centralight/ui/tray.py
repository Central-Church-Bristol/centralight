"""CentraLight in the system tray (by the clock), so it can keep running in the background.

Closing the window hides it here; double-click the icon (or "Show CentraLight") to bring it
back. Quit is only in this menu, and asks first: once CentraLight stops, the lights stop
following automations until it starts again.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from . import icons


class Tray(QSystemTrayIcon):
    show_requested = Signal()
    quit_requested = Signal()
    pause_toggled = Signal(bool)  # True = pause the automations

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(icons.app_icon(), parent)
        self.setToolTip("CentraLight")
        menu = QMenu(parent)
        self.show_action = menu.addAction("Show CentraLight")
        self.show_action.triggered.connect(self.show_requested.emit)
        self.pause_action = QAction("Pause automations", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.pause_toggled.emit)
        menu.addAction(self.pause_action)
        menu.addSeparator()
        self.quit_action = menu.addAction("Quit CentraLight…")
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.setContextMenu(menu)
        self._menu = menu
        self.activated.connect(self._activated)
        self.told_about_hiding = False

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            self.show_requested.emit()

    def set_paused(self, paused: bool) -> None:
        if self.pause_action.isChecked() != paused:
            blocked = self.pause_action.blockSignals(True)
            self.pause_action.setChecked(paused)
            self.pause_action.blockSignals(blocked)

    def set_status(self, text: str) -> None:
        self.setToolTip(f"CentraLight\n{text}" if text else "CentraLight")

    def tell_about_hiding(self) -> None:
        """The first time the window hides: say where it went."""
        if not self.told_about_hiding and self.supportsMessages():
            self.told_about_hiding = True
            self.showMessage("CentraLight is still running",
                             "It keeps the lights and automations going. Double-click this icon to open it; Quit is in its menu.",
                             QSystemTrayIcon.MessageIcon.Information, 6000)
