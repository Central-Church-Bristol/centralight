"""Small widgets used across the app (switch, fader, chips, labels that shorten with "…").

Everything is sized from the font, so it scales with Windows display scaling.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QSplitterHandle,
    QVBoxLayout,
    QWidget,
)

from . import theme

# Chip states
STATE_OK = "ok"
STATE_WARN = "warn"
STATE_ERROR = "error"
STATE_INFO = "info"
STATE_OFF = "off"
STATE_COLOURS = {
    STATE_OK: theme.OK,
    STATE_WARN: theme.WARN,
    STATE_ERROR: theme.DANGER,
    STATE_INFO: theme.ACCENT,
    STATE_OFF: theme.TEXT_3,
}


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_property(widget: QWidget, name: str, value) -> None:
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


class ElidedLabel(QLabel):
    """A one-line label that shortens itself with "…" and shows the full text as a tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None, min_chars: int = 4) -> None:
        super().__init__(parent)
        self._full = text
        self._min_chars = min_chars
        self._own_tip = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self._refresh()

    def setText(self, text: str) -> None:  # noqa: N802 (Qt name)
        if text == self._full:
            return
        self._full = text
        self.updateGeometry()
        self._refresh()

    def text(self) -> str:  # noqa: N802
        return self._full

    def full_text(self) -> str:
        return self._full

    def is_elided(self) -> bool:
        return super().text() != self._full

    def setToolTip(self, tip: str) -> None:  # noqa: N802
        self._own_tip = tip
        self._refresh()

    def _text_width(self, text: str) -> int:
        # fractional widths, rounded up: whole-pixel measuring can shorten text that fits
        return math.ceil(QFontMetricsF(self.font()).horizontalAdvance(text))

    def sizeHint(self) -> QSize:  # noqa: N802
        margins = self.contentsMargins()
        width = self._text_width(self._full) + margins.left() + margins.right() + 2
        return QSize(width, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        sample = self._full[: self._min_chars] + "…" if len(self._full) > self._min_chars else self._full
        margins = self.contentsMargins()
        width = self._text_width(sample) + margins.left() + margins.right() + 2
        return QSize(width, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self.updateGeometry()
            self._refresh()

    def _refresh(self) -> None:
        available = self.contentsRect().width()
        if self._text_width(self._full) <= available:
            shown = self._full
        else:
            shown = QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, available - 2))
        if super().text() != shown:
            super().setText(shown)
        tip = self._own_tip or (self._full if shown != self._full else "")
        if QLabel.toolTip(self) != tip:
            QLabel.setToolTip(self, tip)


class ToggleSwitch(QAbstractButton):
    """An on/off switch (pale blue when on)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        h = max(18, self.fontMetrics().height() + 2)
        return QSize(int(h * 1.85), h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        h = rect.height()
        on = self.isChecked()
        enabled = self.isEnabled()
        track = QColor(theme.ACCENT if on else theme.BORDER_STRONG)
        if not enabled:
            track = theme.mix(track.name(), theme.PANEL, 0.55)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, h / 2, h / 2)
        knob = h - 6
        x = rect.right() - knob - 3 if on else rect.left() + 3
        painter.setBrush(QColor(theme.ACCENT_TEXT if on else theme.TEXT) if enabled else QColor(theme.TEXT_3))
        painter.drawEllipse(QRectF(x, rect.top() + 3, knob, knob))
        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.ACCENT), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), h / 2 + 1, h / 2 + 1)
        painter.end()


class WheelGuard(QObject):
    """The mouse wheel only changes a fader, number box or drop-down once it's been clicked into. Otherwise the
    wheel goes on to the page around it (which scrolls), so scrolling past a control never changes a live level."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Polish and isinstance(obj, (QAbstractSpinBox, QComboBox)):
            _no_wheel_focus(obj)  # (Qt otherwise gives them focus from the wheel, before this filter sees it)
        if event.type() == QEvent.Type.Wheel and isinstance(obj, QWidget):
            control = obj
            for _ in range(3):  # (the wheel can land on a control's inner line edit)
                # Faders, number boxes and drop-downs. Never scroll bars: a scroll area scrolls by handing the wheel
                # to its scroll bar (which never has focus), so stopping them would stop every list scrolling.
                if isinstance(control, (QSlider, QAbstractSpinBox, QComboBox)):
                    break
                control = control.parentWidget()
                if control is None:
                    return False
            else:
                return False
            if not control.hasFocus() and not (control.focusProxy() is not None and control.focusProxy().hasFocus()):
                event.ignore()
                return True  # ignored: Qt passes it on to the parent widgets (the scroll area scrolls)
        return False


_wheel_guard: WheelGuard | None = None
HOLD_SECONDS = 0.6  # holding a Cut / Fade to black button this long does it to everything
HOLD_RELABEL = 0.15  # (its label changes only once it's clearly being held, so a click doesn't flicker it)
# A second click on an overlay button this soon after the first is the same click: a hurried
# double-click. A person correcting an accidental click takes longer than this, so that still works.
DOUBLE_CLICK_GUARD_S = 0.35


def _no_wheel_focus(widget: QWidget) -> None:
    if widget.focusPolicy() == Qt.FocusPolicy.WheelFocus:
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


def install_wheel_guard(app) -> None:
    """Once per application (the main window calls it)."""
    global _wheel_guard
    if _wheel_guard is None:
        _wheel_guard = WheelGuard()
        app.installEventFilter(_wheel_guard)
        for widget in app.allWidgets():  # (the ones that already exist)
            if isinstance(widget, (QAbstractSpinBox, QComboBox)):
                _no_wheel_focus(widget)


class HoldButton(QPushButton):
    """A one-off button with two strengths: a click does one thing (tapped), holding it does the bigger thing
    (held). While it's held it fills up and its label says the bigger thing, which happens when it's full. Shift with
    a click (or with Space / Enter) does the bigger thing at once."""

    tapped = Signal()
    held = Signal()

    def __init__(self, text: str, held_text: str, fill: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.text_normal = text
        self.text_held = held_text
        self._fill = QColor(fill)
        self._pressed_at: float | None = None
        self._fired = False
        self.progress = 0.0  # 0-1 while it's held
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)

    def widest_text(self) -> int:
        fm = self.fontMetrics()
        return max(fm.horizontalAdvance(self.text_normal), fm.horizontalAdvance(self.text_held))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                event.accept()
                self.held.emit()  # Shift-click: the bigger thing, at once
                return
            self._pressed_at = time.monotonic()
            self._fired = False
            self._timer.start()
        super().mousePressEvent(event)

    def _step(self) -> None:
        if self._pressed_at is None:
            self._timer.stop()
            return
        held = time.monotonic() - self._pressed_at
        self.progress = min(1.0, held / HOLD_SECONDS)
        if held >= HOLD_RELABEL and self.text() != self.text_held:
            self.setText(self.text_held)
        self.update()
        if self.progress >= 1.0 and not self._fired:
            self._fired = True
            self._timer.stop()
            self.held.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._pressed_at is not None:
            fired = self._fired
            inside = self.rect().contains(event.position().toPoint())
            self._reset()
            if not fired and inside:
                self.tapped.emit()
        super().mouseReleaseEvent(event)

    def _reset(self) -> None:
        self._timer.stop()
        self._pressed_at = None
        self.progress = 0.0
        self.setText(self.text_normal)
        self.update()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._reset()  # (locked while being held: nothing fires)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.isAutoRepeat():
            event.accept()
            (self.held if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else self.tapped).emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.progress <= 0.0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        box = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setClipRect(QRectF(box.left(), box.top(), box.width() * self.progress, box.height()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._fill)
        painter.drawRoundedRect(box, 7, 7)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(self.font())
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()


class Fader(QSlider):
    """A horizontal 0-100 % fader. Shows the engine's value; emits moved(0-1) when someone moves it."""

    moved = Signal(float)
    released = Signal()

    STEPS = 1000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, self.STEPS)
        self.setSingleStep(10)
        self.setPageStep(100)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._dragging = False
        self._dim = False

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(140, max(22, self.fontMetrics().height() + 4))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(72, max(22, self.fontMetrics().height() + 4))

    def level(self) -> float:
        return self.value() / self.STEPS

    def set_level(self, level: float) -> None:
        """Show a value from the engine (ignored while someone is dragging)."""
        if self._dragging:
            return
        value = round(max(0.0, min(1.0, level)) * self.STEPS)
        if value != self.value():
            blocked = self.blockSignals(True)
            self.setValue(value)
            self.blockSignals(blocked)
            self.update()

    def set_dim(self, dim: bool) -> None:
        """Draw the fill grey (e.g. while the light's switch is off)."""
        if dim != self._dim:
            self._dim = dim
            self.update()

    def is_dragging(self) -> bool:
        return self._dragging

    def _knob_radius(self) -> float:
        return max(6.0, self.height() * 0.32)

    def _value_at(self, x: float) -> int:
        r = self._knob_radius()
        span = max(1.0, self.width() - 2 * r - 2)
        t = (x - r - 1) / span
        return round(max(0.0, min(1.0, t)) * self.STEPS)

    def _user_set(self, value: int) -> None:
        value = max(0, min(self.STEPS, value))
        if value != self.value():
            blocked = self.blockSignals(True)
            self.setValue(value)
            self.blockSignals(blocked)
            self.update()
        self.moved.emit(value / self.STEPS)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            event.ignore()
            return
        self._dragging = True
        self.setSliderDown(True)
        self._user_set(self._value_at(event.position().x()))
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            event.ignore()
            return
        self._user_set(self._value_at(event.position().x()))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            event.ignore()
            return
        self._dragging = False
        self.setSliderDown(False)
        self.released.emit()
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self.isEnabled():
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta:
            self._user_set(self.value() + (20 if delta > 0 else -20))
            self.released.emit()  # each wheel step is a whole change (its own undo step, its own keyframe time)
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        steps = {
            Qt.Key.Key_Right: self.singleStep(),
            Qt.Key.Key_Up: self.singleStep(),
            Qt.Key.Key_Left: -self.singleStep(),
            Qt.Key.Key_Down: -self.singleStep(),
            Qt.Key.Key_PageUp: self.pageStep(),
            Qt.Key.Key_PageDown: -self.pageStep(),
        }
        if event.key() in steps:
            self._user_set(self.value() + steps[event.key()])
        elif event.key() == Qt.Key.Key_Home:
            self._user_set(0)
        elif event.key() == Qt.Key.Key_End:
            self._user_set(self.STEPS)
        else:
            super().keyPressEvent(event)
            return
        self.released.emit()
        event.accept()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self._knob_radius()
        cy = self.height() / 2
        groove = QRectF(r, cy - 2, max(0.0, self.width() - 2 * r), 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BORDER_STRONG))
        painter.drawRoundedRect(groove, 2, 2)
        x = r + 1 + (self.width() - 2 * r - 2) * self.level()
        fill_colour = theme.TEXT_3 if (self._dim or not self.isEnabled()) else theme.ACCENT
        painter.setBrush(QColor(fill_colour))
        painter.drawRoundedRect(QRectF(groove.left(), groove.top(), max(0.0, x - groove.left()), groove.height()), 2, 2)
        knob = QColor(theme.TEXT if self.isEnabled() else theme.TEXT_3)
        painter.setBrush(knob)
        painter.setPen(QPen(QColor(theme.ACCENT if self.hasFocus() else theme.BORDER_STRONG), 1.5))
        painter.drawEllipse(QPointF(x, cy), r - 1, r - 1)
        painter.end()


class StatusDot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = QColor(theme.TEXT_3)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        side = max(8, round(self.fontMetrics().height() * 0.55))
        return QSize(side, side)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def set_colour(self, colour: str) -> None:
        if QColor(colour) != self._colour:
            self._colour = QColor(colour)
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._colour)
        side = min(self.width(), self.height())
        painter.drawEllipse(QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side))
        painter.end()


class StatusChip(QFrame):
    """A pill with a coloured dot and a short status, e.g. "● COM3 · 40/s".

    Compact mode (narrow windows) shows only the dot; the text moves to the tooltip.
    A chip can be clickable (e.g. Remote opens the remote page).
    """

    clicked = Signal()

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Chip")
        self._clickable = False
        self.name = name
        self._dot = StatusDot(self)
        self._text = QLabel(self)
        self._text.setObjectName("ChipText")
        self._compact = False
        self._tooltip = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 12, 3)
        layout.setSpacing(6)
        layout.addWidget(self._dot)
        layout.addWidget(self._text)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setProperty("compact", False)

    def set_status(self, state: str, text: str, tooltip: str = "") -> None:
        self._dot.set_colour(STATE_COLOURS.get(state, theme.TEXT_3))
        if self._text.text() != text:
            self._text.setText(text)
            self.updateGeometry()
        self._tooltip = tooltip
        self._update_tooltip()
        self.setProperty("state", state)

    def text(self) -> str:
        return self._text.text()

    def set_clickable(self, clickable: bool) -> None:
        self._clickable = clickable
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._text.setVisible(not compact)
        margins = (4, 3, 4, 3) if compact else (10, 3, 12, 3)
        self.layout().setContentsMargins(*margins)
        set_property(self, "compact", compact)
        self._update_tooltip()
        self.updateGeometry()

    def full_width(self) -> int:
        """Width with the text showing (used to decide when to go compact)."""
        fm = self._text.fontMetrics()
        return 10 + self._dot.sizeHint().width() + 6 + fm.horizontalAdvance(self._text.text()) + 12 + 2

    def compact_width(self) -> int:
        return 4 + self._dot.sizeHint().width() + 4 + 2

    def _update_tooltip(self) -> None:
        tip = self._tooltip
        if self._compact:
            tip = f"{self._text.text()}\n{tip}" if tip else self._text.text()
        self.setToolTip(tip)


def discard(widget: QWidget | None) -> None:
    """Throw a widget away: hidden and out of the window straight away, freed a moment later.
    (deleteLater() alone leaves it on screen, unplaced, until Qt gets round to it.)"""
    if widget is None:
        return
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


def fader_row(caption: str, tip: str = "") -> tuple[QHBoxLayout, Fader, QLabel]:
    """A small "Caption [fader] 100%" row. Returns (layout, fader, percent label)."""
    label = QLabel(caption)
    label.setObjectName("RowDetail")
    fader = Fader()
    fader.setMaximumWidth(16777215)
    if tip:
        fader.setToolTip(tip)
    percent = QLabel("100%")
    percent.setObjectName("RowDetail")
    percent.setFixedWidth(percent.fontMetrics().horizontalAdvance("100%") + 4)
    percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(label)
    row.addWidget(fader, 1)
    row.addWidget(percent)
    return row, fader, percent


def plain_tip(text: str) -> str:
    """A tooltip that shows its text exactly as it is. Qt shows text that looks like HTML as HTML (and reads any
    picture a tag names from the disk), so the text is escaped and wrapped."""
    import html

    return "<p style='white-space:pre-wrap'>" + html.escape(str(text)) + "</p>"


class Panel(QFrame):
    """A rounded card with a small upper-case title."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 14)
        self.body.setSpacing(8)
        self.title_label: QLabel | None = None
        if title:
            self.title_label = QLabel(title.upper())
            self.title_label.setObjectName("PanelTitle")
            self.body.addWidget(self.title_label)


class FlowLayout(QLayout):
    """Lays widgets out in rows, wrapping to the next row when full (like words in a paragraph)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 10) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            self.invalidate()
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y = area.x(), area.y()
        line_height = 0
        space = self.spacing()
        for item in self._items:
            if item.widget() is not None and not item.widget().isVisibleTo(item.widget().parentWidget()):
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line_height > 0:
                x = area.x()
                y += line_height + space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + space
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class _ResetHandle(QSplitterHandle):
    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        splitter = self.splitter()
        if isinstance(splitter, ResettableSplitter):
            splitter.reset_sizes()
        event.accept()


class ResettableSplitter(QSplitter):
    """A splitter whose handles go back to the default split when double-clicked."""

    def __init__(
        self,
        orientation: Qt.Orientation,
        default_sizes: Callable[[], list[int]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        self._default_sizes = default_sizes
        self._pending: list[int] | None = None  # sizes waiting until the splitter is on screen
        self.setChildrenCollapsible(False)
        self.setHandleWidth(8)

    def apply_sizes(self, sizes: list[int] | None) -> None:
        """Use remembered sizes, or the defaults (None). A splitter that isn't on screen yet (a page
        that hasn't been shown) waits until it is, so the defaults come from its real size."""
        self._pending = list(sizes) if sizes else []
        if self.isVisible():
            self._apply_pending()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._pending is not None:
            QTimer.singleShot(0, self._apply_pending)

    def _apply_pending(self) -> None:
        sizes, self._pending = self._pending, None
        if sizes is None:
            return
        if len(sizes) == self.count() and min(sizes, default=0) > 0:
            self.setSizes(sizes)
        elif self._default_sizes is not None:
            self.setSizes(self._default_sizes())

    def set_default_sizes(self, default_sizes: Callable[[], list[int]]) -> None:
        self._default_sizes = default_sizes

    def createHandle(self) -> QSplitterHandle:  # noqa: N802
        handle = _ResetHandle(self.orientation(), self)
        handle.setToolTip("Drag to resize · double-click to reset")
        return handle

    def reset_sizes(self) -> None:
        if self._default_sizes is not None:
            self.setSizes(self._default_sizes())


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {theme.BORDER};")
    return line
