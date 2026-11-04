"""The banner across the top of the window while a countdown runs before an automatic change (M13; countdown.py).

"Loading Evening in 0:15 · Load now · Wait 10 min · Skip", or "Fading to black in 0:15 · …". If nobody touches it, the
change happens at zero. Under the Lock, only Wait 10 min and Skip work (they stop a change; Load now makes one).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from . import icons, theme
from .widgets import plain_tip


class CountdownBanner(QFrame):
    pressed = Signal(str, str)  # "now" / "wait" / "skip", the countdown's id

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("CountdownBanner")
        self._id = ""
        self._locked = False
        icon = QLabel()
        icon.setPixmap(icons.line_icon(icons.draw_clock, theme.WARN).pixmap(18, 18))
        self.text = QLabel()
        self.text.setObjectName("CountdownText")
        self.text.setTextFormat(Qt.TextFormat.PlainText)  # (names come from files: never read as HTML)
        self.now_button = QPushButton("Load now")
        self.wait_button = QPushButton("Wait 10 min")
        self.skip_button = QPushButton("Skip")
        self.now_button.setToolTip("Do it now")
        self.wait_button.setToolTip("Do it 10 minutes later (with the countdown again first)")
        self.skip_button.setToolTip("Don't do it this time (the automation runs as normal next time)")
        for button, what in ((self.now_button, "now"), (self.wait_button, "wait"), (self.skip_button, "skip")):
            button.setObjectName("CountdownButton")
            button.clicked.connect(lambda _checked=False, w=what: self.pressed.emit(w, self._id))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(10)
        layout.addWidget(icon)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.now_button)
        layout.addWidget(self.wait_button)
        layout.addWidget(self.skip_button)
        self.setVisible(False)

    def set_view(self, view, show_name: Callable[[str], str] | None = None) -> None:
        """view: the engine frame's CountdownView (None = no countdown)."""
        if view is None:
            if self.isVisible() or self._id:
                self._id = ""
                self.setVisible(False)
            return
        if view.id != self._id:
            self._id = view.id
            self._label = f"Loading {show_name(view.show) if show_name is not None else view.show}" if view.show else view.text
            self.now_button.setText("Load now" if view.show else "Do it now")
            self.setToolTip(plain_tip(f"Automation: {view.automation}"))
        more = f"  (and {view.more} more)" if view.more else ""
        text = f"{self._label} in {view.clock}{more}"
        if self.text.text() != text:
            self.text.setText(text)
        self.now_button.setEnabled(not self._locked)
        self.setVisible(True)

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self.now_button.setEnabled(not locked)
