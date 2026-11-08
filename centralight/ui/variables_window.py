"""The Show variables window (8 Nov): Show ▾ → Show variables…, or Edit on the Dashboard's Variables panel.

The loaded show's variables in a window of their own, on top of the main window and not modal, with the controls of
the Editor's Show variables tab (globals_panel.GlobalsPanel). So they can be added and changed with no input open, with
the Dashboard in sight. Changes go through the show document, as in the Editor: each is a step in the show's history
(Ctrl+Z in this window undoes the newest). One at a time; it remembers its size and place (Settings).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..model import Show
from .document import SCOPE_SHOW, ShowDocument
from .globals_panel import GlobalsPanel

SIZE = (440, 600)  # logical pixels, the first time it opens (then as it was left)
MIN_WIDTH = 340  # (as the Editor's details panel: the variables' cards fit it)


class ShowVariablesWindow(QDialog):
    def __init__(self, document: ShowDocument, url_base: Callable[[], str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ShowVariablesWindow")
        self.setModal(False)
        self.document = document
        self._locked = False
        self.panel = GlobalsPanel(document, url_base)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumWidth(MIN_WIDTH)
        content = QWidget()
        content.setObjectName("ScrollContent")  # (the window's own background shows through, as in the Editor)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 6, 0)
        content_layout.addWidget(self.panel)
        self.scroll.setWidget(content)
        self.none = QLabel("No show loaded.")
        self.none.setObjectName("Hint")
        self.none.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.none, 1)
        layout.addWidget(self.scroll, 1)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.redo)
        document.changed.connect(self._show_changed)
        self._show_changed(document.show)

    def _show_changed(self, show: Show) -> None:
        """Any change, or another show loaded (its variables), or none (greyed out until a show loads)."""
        self.setWindowTitle("Show variables" if show.no_show else f"Show variables — {show.name}")
        self.panel.refresh(show)
        self.none.setVisible(show.no_show)
        self.scroll.setVisible(not show.no_show)
        self.panel.setEnabled(not self._locked)

    def set_locked(self, locked: bool) -> None:
        """The padlock: nothing can be changed here while it's on."""
        self._locked = locked
        self.panel.setEnabled(not locked)

    def undo(self) -> None:
        if not self._locked and not self.document.show.no_show:
            self.document.stack(SCOPE_SHOW).undo()

    def redo(self) -> None:
        if not self._locked and not self.document.show.no_show:
            self.document.stack(SCOPE_SHOW).redo()

    def place(self, saved: list[int] | None, over: QRect) -> None:
        """Where it was left (if that's still on a screen), or over the middle of the main window."""
        if saved:
            rect = QRect(*saved)
            if any(screen.availableGeometry().intersects(rect) for screen in QGuiApplication.screens()):
                self.setGeometry(rect)
                return
        width, height = SIZE
        screen = QGuiApplication.screenAt(over.center()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            width, height = min(width, area.width()), min(height, area.height())
        self.resize(width, height)
        self.move(over.center().x() - width // 2, over.center().y() - height // 2)

    def geometry_list(self) -> list[int]:
        g = self.geometry()
        return [g.x(), g.y(), g.width(), g.height()]
