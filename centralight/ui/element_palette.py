"""The Editor's element palette (on the left): drag an element onto a track, or click it to
add it to the first empty track. Transitions set how the input appears (drag one onto the
timeline, or click it)."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from . import icons, theme
from .widgets import Panel

ELEMENT_MIME = "application/x-centralight-element"
TRANSITION_MIME = "application/x-centralight-transition"

ELEMENTS = (
    ("colour", "Colour", "A solid colour or a gradient across the screen", icons.draw_swatch, True),
    ("paint", "Paint", "Brush painting and shapes (see-through until you paint on it)", icons.draw_pencil, True),
    ("image", "Image", "A picture", icons.draw_plus, True),
    ("video", "Video", "A video file, looping for as long as the element lasts", icons.draw_clock, True),
    ("ndi", "NDI", "A live feed, e.g. Media Background Pane", icons.draw_wave, True),
)
TRANSITIONS = (
    ("cut", "Cut", "Switch straight away"),
    ("fade", "Fade", "Mix evenly"),
    ("dip", "Dip", "Fade to black, then up"),
    ("wipe", "Wipe", "A soft edge sweeps across"),
    ("iris", "Iris", "A circle opens from the middle"),
    ("random", "Random", "Each light changes at its own moment"),
)


class PaletteItem(QFrame):
    activated = Signal(str)

    def __init__(self, mime: str, key: str, title: str, tip: str, draw=None, enabled: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PaletteItem")
        self.mime = mime
        self.key = key
        self._press: QPoint | None = None
        self.setToolTip(f"{tip}\nDrag onto a track, or click to add")
        self.setEnabled(enabled)
        self.setCursor(Qt.CursorShape.OpenHandCursor if enabled else Qt.CursorShape.ForbiddenCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)
        if draw is not None:
            icon = QLabel()
            icon.setPixmap(icons.line_icon(draw, theme.ACCENT if enabled else theme.TEXT_3).pixmap(18, 18))
            layout.addWidget(icon)
        label = QLabel(title)
        label.setObjectName("PaletteLabel")
        layout.addWidget(label, 1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        drag = QDrag(self)
        data = QMimeData()
        data.setData(self.mime, self.key.encode("utf-8"))
        drag.setMimeData(data)
        drag.setPixmap(self.grab())
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            self._press = None
            if self.rect().contains(event.position().toPoint()):
                self.activated.emit(self.key)
        super().mouseReleaseEvent(event)


class ElementPalette(QScrollArea):
    element_clicked = Signal(str)  # kind
    transition_clicked = Signal(str)  # kind

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ElementPalette")
        content = QWidget()
        content.setObjectName("ScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        elements = Panel("Elements")
        self.items: dict[str, PaletteItem] = {}
        for key, title, tip, draw, enabled in ELEMENTS:
            item = PaletteItem(ELEMENT_MIME, key, title, tip, draw, enabled)
            item.activated.connect(self.element_clicked.emit)
            elements.body.addWidget(item)
            self.items[key] = item
        transitions = Panel("Transitions")
        for key, title, tip in TRANSITIONS:
            item = PaletteItem(TRANSITION_MIME, key, title, tip)
            item.activated.connect(self.transition_clicked.emit)
            transitions.body.addWidget(item)
            self.items[f"transition:{key}"] = item
        layout.addWidget(elements)
        layout.addWidget(transitions)
        layout.addStretch(1)
        self.setWidget(content)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(170)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(190, 400)
