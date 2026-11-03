"""Undo / Redo buttons and the History panel, for the history of what's on screen.

The buttons' tooltips name the step ("Undo: Move SlimPAR 103"). History lists every step
of the current history; clicking one goes back (or forward) to it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QGuiApplication, QUndoGroup
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QToolTip, QUndoView, QVBoxLayout, QWidget

from . import icons, theme


class HistoryView(QUndoView):
    """The list of steps; a step name that's shortened with "…" shows in full as a tooltip."""

    def __init__(self, group: QUndoGroup, parent: QWidget | None = None) -> None:
        super().__init__(group, parent)
        self.setProperty("fullTextTooltips", True)  # (for the layout test)

    def viewportEvent(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            text = index.data() if index.isValid() else None
            rect = self.visualRect(index)
            if isinstance(text, str) and self.fontMetrics().horizontalAdvance(text) > rect.width() - 8:
                QToolTip.showText(event.globalPos(), text, self.viewport(), rect)
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)


class HistoryPopup(QFrame):
    """The steps in the current history, oldest first. Click one to go back to it."""

    def __init__(self, group: QUndoGroup, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("HistoryPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)
        title = QLabel("HISTORY")
        title.setObjectName("PanelTitle")
        hint = QLabel("Click a step to go back to it. Ctrl+Z / Ctrl+Y step one at a time.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        self.view = HistoryView(group)
        self.view.setObjectName("HistoryList")
        self.view.setEmptyLabel("Start (before these changes)")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.view.setUniformItemSizes(True)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.view, 1)

    def sizeHint(self) -> QSize:  # noqa: N802
        line = self.fontMetrics().height()
        return QSize(max(300, line * 20), max(320, line * 22))

    def popup_under(self, anchor: QWidget) -> None:
        """Open below anchor, right edges lined up, kept on the screen."""
        self.resize(self.sizeHint())
        corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 4))
        x, y = corner.x() - self.width(), corner.y()
        screen = anchor.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() - self.width()))
            if y + self.height() > area.bottom():
                y = max(area.top(), anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4)
        self.move(x, y)
        self.show()
        self.view.scrollToBottom()
        self.view.setFocus()


class UndoBar(QWidget):
    """[Undo] [Redo] [History] for whichever history is active in the group."""

    def __init__(self, group: QUndoGroup, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.group = group
        self.undo_button = self._button(icons.draw_undo, "Undo")
        self.redo_button = self._button(icons.draw_redo, "Redo")
        self.history_button = self._button(icons.draw_history, "History")
        self.history_button.setToolTip("History: every change since the show was opened")
        self.undo_button.clicked.connect(lambda *_: self.group.undo())
        self.redo_button.clicked.connect(lambda *_: self.group.redo())
        self.history_button.clicked.connect(lambda *_: self.show_history())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for button in (self.undo_button, self.redo_button, self.history_button):
            layout.addWidget(button)
        self.popup: HistoryPopup | None = None
        for signal in (group.canUndoChanged, group.canRedoChanged, group.undoTextChanged, group.redoTextChanged, group.activeStackChanged):
            signal.connect(lambda *_: self.update_buttons())
        self.update_buttons()

    @staticmethod
    def _button(draw, name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("HeaderIcon")
        button.setIcon(icons.line_icon(draw, theme.TEXT_2))
        button.setAccessibleName(name)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return button

    def update_buttons(self) -> None:
        can_undo, can_redo = self.group.canUndo(), self.group.canRedo()
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)
        self.undo_button.setToolTip(f"Undo: {self.group.undoText()}  (Ctrl+Z)" if can_undo else "Nothing to undo")
        self.redo_button.setToolTip(f"Redo: {self.group.redoText()}  (Ctrl+Y)" if can_redo else "Nothing to redo")

    def show_history(self) -> HistoryPopup:
        if self.popup is None:
            self.popup = HistoryPopup(self.group, self.window())
        self.popup.popup_under(self.history_button)
        return self.popup
