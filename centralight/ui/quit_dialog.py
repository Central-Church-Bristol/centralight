"""'Quit CentraLight?': turn the lights off, or leave them as they are."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..settings import QUIT_LEAVE, QUIT_OFF


class QuitDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quit CentraLight")
        self.setModal(True)
        self.choice: str | None = None
        title = QLabel("What should the lights do when CentraLight closes?")
        title.setWordWrap(True)
        info = QLabel(
            "The Eurolite keeps showing the last look until something else is sent, "
            "so the lights can stay on after CentraLight has gone."
        )
        info.setObjectName("Hint")
        info.setWordWrap(True)
        self.remember = QCheckBox("Remember my choice (you can change it in Settings)")
        off = QPushButton("Turn lights off")
        off.setProperty("danger", True)
        leave = QPushButton("Leave lights as they are")
        cancel = QPushButton("Cancel")
        off.clicked.connect(lambda: self._choose(QUIT_OFF))
        leave.clicked.connect(lambda: self._choose(QUIT_LEAVE))
        cancel.clicked.connect(self.reject)
        off.setDefault(True)
        buttons = QHBoxLayout()
        buttons.addWidget(off)
        buttons.addWidget(leave)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(info)
        layout.addWidget(self.remember)
        layout.addLayout(buttons)
        self.setMinimumWidth(480)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()
