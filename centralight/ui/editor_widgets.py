"""Small editing fields for the Editor's details panel (built from schema.py).

Each field shows a value and emits edited(value, final): final is False while a slider is
being dragged and True when it's let go (or a number is typed).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from ..colours import is_colour_text, parse_colour, to_hex
from . import theme
from .widgets import Fader

SUFFIXES = {"%": " %", "°": "°", "×": "×", "s": " s"}


def display_scale(unit: str) -> float:
    return 100.0 if unit == "%" else 1.0


class NumberField(QWidget):
    edited = Signal(object, bool)  # value, final

    def __init__(self, minimum: float, maximum: float, step: float = 0.01, unit: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min, self._max = float(minimum), float(maximum)
        self._scale = display_scale(unit)
        self.slider = Fader()
        self.slider.setMaximumWidth(16777215)
        self.slider.setMinimumWidth(56)
        self.spin = QDoubleSpinBox()
        self.spin.setObjectName("NumberSpin")  # style.qss gives its (hidden) arrow buttons no width
        self.spin.setRange(self._min * self._scale, self._max * self._scale)
        self.spin.setDecimals(1 if self._scale == 100.0 or step >= 0.1 else 2)
        self.spin.setSingleStep(step * self._scale)
        self.spin.setSuffix(SUFFIXES.get(unit, ""))
        self.spin.setKeyboardTracking(False)
        # (its arrow buttons are given no width in style.qss: Qt's NoButtons squeezes the text to 1 px when styled)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        widest = max(f"{self.spin.minimum():.{self.spin.decimals()}f}", f"{self.spin.maximum():.{self.spin.decimals()}f}", key=len)
        self.spin.setFixedWidth(self.spin.fontMetrics().horizontalAdvance(widest + self.spin.suffix()) + 20)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)
        self.slider.moved.connect(self._slider_moved)
        self.slider.released.connect(lambda: self.edited.emit(self.value(), True))
        self.spin.valueChanged.connect(self._spin_changed)
        self._value = self._min
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _fraction(self, value: float) -> float:
        span = self._max - self._min
        return 0.0 if span <= 0 else (value - self._min) / span

    def value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        """Show a value (from the document) without emitting edited. Not while it's being dragged or typed into
        (the playing Editor updates values every moment, which used to wipe out what was being typed)."""
        if self.slider.is_dragging() or self.spin.hasFocus():
            return
        value = float(value)
        self._value = value
        self.slider.set_level(self._fraction(value))
        blocked = self.spin.blockSignals(True)
        self.spin.setValue(value * self._scale)
        self.spin.blockSignals(blocked)

    def _slider_moved(self, fraction: float) -> None:
        value = self._min + (self._max - self._min) * fraction
        self._value = value
        blocked = self.spin.blockSignals(True)
        self.spin.setValue(value * self._scale)
        self.spin.blockSignals(blocked)
        self.edited.emit(value, False)

    def _spin_changed(self, shown: float) -> None:
        value = shown / self._scale
        self._value = value
        self.slider.set_level(self._fraction(value))
        self.edited.emit(value, True)


class _Swatch(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.colour = QColor("#FFFFFF")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Choose a colour")

    def sizeHint(self) -> QSize:  # noqa: N802
        side = self.fontMetrics().height() + 10
        return QSize(side * 2, side)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QColor(theme.BORDER_STRONG if not self.underMouse() else theme.ACCENT))
        painter.setBrush(self.colour)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
        painter.end()


class ColourField(QWidget):
    edited = Signal(object, bool)  # "#RRGGBB", final (False while a pick with Preview is under way: one undo step)

    def __init__(self, parent: QWidget | None = None, live: bool = True) -> None:
        """live: the picker's Preview applies each change at once (M14). Off for colours that don't change the lights
        (a tile's colour, a show variable's starting value): OK applies it, as before."""
        super().__init__(parent)
        self.live = live
        self.picks = 0  # counts each pick (and each typed colour): changes that belong to one undo step share it
        # hold(True) as a live pick's picker opens and hold(False) as it closes (a document's hold_saving: it saves once
        # the picker has closed). A plain function, not a signal: it's called even if this field was rebuilt away
        self.hold: Callable[[bool], None] | None = None
        self.swatch = _Swatch()
        self.text = QLineEdit()
        self.text.setMaxLength(7)
        self.text.setPlaceholderText("#RRGGBB")
        fm = self.text.fontMetrics()  # room for "#" and six of the widest hex digit (not "W", which never appears)
        self.text.setFixedWidth(fm.horizontalAdvance("#") + 6 * max(fm.horizontalAdvance(c) for c in "0123456789ABCDEF") + 22)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.swatch)
        layout.addWidget(self.text)
        layout.addStretch(1)
        self.swatch.clicked.connect(self._choose)
        self.text.editingFinished.connect(self._typed)
        self._value = "#FFFFFF"

    def value(self) -> str:
        return self._value

    def set_value(self, value: Any) -> None:
        if is_colour_text(value):
            text = str(value).upper()
        elif isinstance(value, (tuple, list)) and len(value) == 3:
            text = to_hex(tuple(float(v) for v in value))
        else:
            text = to_hex(parse_colour(value, (1.0, 1.0, 1.0)))
        self._value = text
        self.swatch.colour = QColor(text)
        self.swatch.update()
        if not self.text.hasFocus():
            self.text.setText(text)

    def _choose(self) -> None:
        """The colour picker (M14): with Preview, each change applies at once (edited(colour, False), merged into one
        undo step), OK ends it (edited(colour, True)), and Cancel puts the colour back (merging back to nothing)."""
        from .colour_picker import LiveColourDialog, preview_choice

        self.picks += 1
        start, live = self._value, self.live
        hold = self.hold if live else None
        # The window is the dialog's parent: the details panel can rebuild (deleting this field) while it's open
        dialog = LiveColourDialog(start, preview_choice.wanted() if live else False, live, self.window())
        dialog.changed.connect(self._live)
        if hold is not None:
            hold(True)
        try:
            try:
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
                if live:
                    preview_choice.remember(dialog.preview.isChecked())
                chosen, tried = dialog.colour(), dialog.tried()
            finally:
                dialog.deleteLater()
            try:
                if accepted and tried:
                    self.set_value(chosen)
                    self.edited.emit(chosen, True)  # (the last of the pick's changes: its one undo step ends here)
                elif accepted:
                    self._apply(chosen)
                elif tried:
                    self.set_value(start)
                    self.edited.emit(start, True)  # (Cancel: back as it was, and the step goes with it)
            except RuntimeError:  # this field was rebuilt away meanwhile
                pass
        finally:
            if hold is not None:
                hold(False)

    def _live(self, text: str) -> None:
        """Preview: a colour picked in the open picker, applied now."""
        try:
            self.set_value(text)
            self.edited.emit(text, False)
        except RuntimeError:  # this field was rebuilt away meanwhile
            pass

    def _typed(self) -> None:
        text = self.text.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        if is_colour_text(text):
            self._apply(text.upper())
        else:
            self.text.setText(self._value)

    def _apply(self, text: str) -> None:
        if text != self._value:
            self.picks += 1  # (a change of its own: its own undo step)
            self.set_value(text)
            self.edited.emit(text, True)


class ChoiceField(QComboBox):
    edited = Signal(object, bool)

    def __init__(self, choices: tuple[tuple[str, str], ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        for value, label in choices:
            self.addItem(label, value)
        self.currentIndexChanged.connect(lambda _i: self.edited.emit(self.currentData(), True))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: Any) -> None:
        index = self.findData(value)
        if index >= 0 and index != self.currentIndex():
            blocked = self.blockSignals(True)
            self.setCurrentIndex(index)
            self.blockSignals(blocked)


class SourceField(QComboBox):
    """An NDI source: pick one that can be seen, or type part of a name."""

    edited = Signal(object, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().editingFinished.connect(self._typed)
        self.activated.connect(lambda _i: self._typed())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._value = ""

    def set_sources(self, names: list[str]) -> None:
        current = self.currentText()
        blocked = self.blockSignals(True)
        self.clear()
        for name in names:
            short = name[name.find("(") + 1:name.rfind(")")] if "(" in name and name.endswith(")") else name
            self.addItem(short, short)
        self.setEditText(current)
        self.blockSignals(blocked)

    def set_value(self, value: Any) -> None:
        self._value = str(value or "")
        if not self.lineEdit().hasFocus():
            self.setEditText(self._value)

    def _typed(self) -> None:
        text = self.currentText().strip()
        if text and text != self._value:
            self._value = text
            self.edited.emit(text, True)
