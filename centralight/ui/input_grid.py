"""The dashboard's input grid: one tile per input.

A tile shows a mini light map (the live colours while the input is on Base, otherwise
the colours it showed last time), its name, and small icons for timed, sound-driven and
AI-made inputs. A coral border means it's on Base.

Its buttons (M14): Send to Base, and Send to Overlay (each press a new copy on top; held
down, the copy shows only while it's held). A red box under them for each copy on the
Overlay: click one to clear that copy. Clicking the tile itself does nothing; dragging it
moves it. Right-click: more.
Tiles are laid out in rows of equal width that wrap like words, and the grid scrolls
instead of squashing them.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Mapping

from PySide6.QtCore import QMimeData, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..model import Input, Show
from ..output import RGB
from . import icons, theme
from .light_view import rgb_colour
from .widgets import ElidedLabel, discard, set_property

TILE_MIN = 228  # wide enough for Send to Base and Send to Overlay side by side
TILE_MIME = "application/x-centralight-input"  # a tile being dragged (its input's id)
TILE_MAX = 270


class MiniLightMap(QWidget):
    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show = show
        self._colours: Mapping[str, RGB] = {}
        self._live = False
        self.progress: float | None = None  # how far through a timed input that's playing (0-1)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumSize(96, 54)

    def set_show(self, show: Show) -> None:
        self._show = show
        self.update()

    def set_colours(self, colours: Mapping[str, RGB], live: bool) -> None:
        if colours != self._colours or live != self._live:
            self._colours = colours
            self._live = live
            self.update()

    def set_progress(self, progress: float | None) -> None:
        if progress != self.progress:
            self.progress = progress
            self.update()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return round(width * 9 / 16)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_MIN, round(TILE_MIN * 9 / 16))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        area = QRectF(self.rect())
        width = min(area.width(), area.height() * 16 / 9)
        height = width * 9 / 16
        canvas = QRectF(area.left() + (area.width() - width) / 2, area.top() + (area.height() - height) / 2, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.WELL))
        painter.drawRoundedRect(canvas, 6, 6)
        dim = 1.0 if self._live else 0.55
        for fx in self._show.colour_fixtures():
            if fx.map is None:
                continue
            centre_x = canvas.left() + fx.map.x * canvas.width()
            centre_y = canvas.top() + fx.map.y * canvas.height()
            radius = max(2.5, canvas.width() * 0.036)  # (how big the dots look)
            rgb = self._colours.get(fx.id)
            if rgb is None:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
            else:
                colour = rgb_colour((rgb[0] * dim, rgb[1] * dim, rgb[2] * dim))
                painter.setBrush(colour)
                painter.setPen(QPen(QColor(theme.BORDER_STRONG) if max(rgb) < 0.1 else colour.lighter(130), 1))
            painter.drawEllipse(QRectF(centre_x - radius, centre_y - radius, radius * 2, radius * 2))
        if self.progress is not None:  # a timed input playing: how far through it is
            bar = QRectF(canvas.left() + 6, canvas.bottom() - 7, canvas.width() - 12, 3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 40))
            painter.drawRoundedRect(bar, 1.5, 1.5)
            painter.setBrush(QColor(theme.LIVE))
            painter.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * min(1.0, max(0.0, self.progress)), bar.height()), 1.5, 1.5)
        painter.end()


HOLD_S = 0.5  # seconds: Send to Overlay held longer than this is a flash (its copy clears when it's let go)
COPY_BOX_HEIGHT = 10
_tags = itertools.count(1)


class HoldButton(QPushButton):
    """A button that acts as soon as it's pressed (went_down), then says how long it was held when the mouse button
    comes up, wherever the mouse is by then (let_go). The keyboard (Space) clicks it as usual (clicked)."""

    went_down = Signal()
    let_go = Signal(float)  # seconds held

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._down_at: float | None = None
        self._enabled_after = True  # (it stays usable while it's held, so letting go always arrives)

    def is_held(self) -> bool:
        return self._down_at is not None

    def set_usable(self, usable: bool) -> None:
        """Enabled or not; while it's held, that waits for it to be let go."""
        self._enabled_after = usable
        if self._down_at is None:
            self.setEnabled(usable)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._down_at = time.monotonic()
            self.setDown(True)
            self.went_down.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._down_at is not None:
            event.accept()  # (still held, even outside the button)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._down_at is not None:
            held = time.monotonic() - self._down_at
            self._down_at = None
            self.setDown(False)
            self.setEnabled(self._enabled_after)
            self.let_go.emit(held)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CopyBoxes(QWidget):
    """A tile's copies on the Overlay (M14): a red box for each, a quarter of the tile wide, oldest on the left. A
    timed copy's box fills as its timeline plays; an infinite one's is full. Clicking a box clears that copy."""

    clear_requested = Signal(int)  # copy number

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._copies: tuple[tuple[int, float | None], ...] = ()  # (number, progress)
        self._dim = False
        self.setFixedHeight(COPY_BOX_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def set_copies(self, copies: tuple[tuple[int, float | None], ...], dim: bool = False) -> None:
        """dim: the Overlay is switched off (its copies are kept, hidden)."""
        copies = tuple(copies)
        if copies != self._copies or dim != self._dim:
            self._copies = copies
            self._dim = dim
            self.setCursor(Qt.CursorShape.PointingHandCursor if copies else Qt.CursorShape.ArrowCursor)
            self.setToolTip("Click a box to clear that copy from the Overlay" if copies else "")
            self.update()

    def copies(self) -> tuple[tuple[int, float | None], ...]:
        return self._copies

    def box_rects(self) -> list[QRectF]:
        gap = 3.0
        width = (self.width() - gap * 3) / 4
        return [QRectF(i * (width + gap), 0.0, width, float(self.height())) for i in range(min(4, len(self._copies)))]

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._copies:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        red = QColor(theme.DANGER)
        if self._dim:
            painter.setOpacity(0.45)
        for rect, (_number, progress) in zip(self.box_rects(), self._copies):
            box = rect.adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(red, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 2.5, 2.5)
            filled = 1.0 if progress is None else max(0.0, min(1.0, progress))
            if filled > 0.0:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(red)
                painter.drawRoundedRect(QRectF(box.left(), box.top(), box.width() * filled, box.height()), 2.5, 2.5)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            at = event.position()
            for rect, (number, _progress) in zip(self.box_rects(), self._copies):
                if rect.adjusted(-1.5, -3.0, 1.5, 3.0).contains(at):
                    self.clear_requested.emit(number)
                    event.accept()
                    return
        super().mousePressEvent(event)


class InputTile(QFrame):
    base_requested = Signal(str)  # input id: Send to Base
    overlay_pressed = Signal(str, str)  # input id, tag: Send to Overlay pressed (a copy goes on at once)
    overlay_held = Signal(str, str)  # input id, tag: let go after holding it (that copy clears)
    copy_clear_requested = Signal(int)  # copy number: its box was clicked
    drag_requested = Signal(str)  # input id: pressed and moved, to move the tile
    menu_requested = Signal(str, QPoint)  # input id, where (global)
    edit_requested = Signal(str)
    ai_requested = Signal(str)  # input id: edit it with AI

    def __init__(self, inp: Input, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Tile")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.input = inp
        self.map = MiniLightMap(show)
        self.base_button = QPushButton("Send to Base")
        self.base_button.setObjectName("TileSend")
        self.base_button.setProperty("target", "base")
        self.base_button.setToolTip("Put this input on Base, with its transition")
        self.base_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.base_button.clicked.connect(lambda: self.base_requested.emit(self.input.id))
        self.overlay_button = HoldButton("Send to Overlay")
        self.overlay_button.setObjectName("TileSend")
        self.overlay_button.setProperty("target", "overlay")
        self.overlay_button.setToolTip("A copy of this input on top of what's showing (up to 4 at once).\n"
                                       "Hold it down: the copy shows only while it's held, like a flash button")
        self.overlay_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_button.went_down.connect(self._overlay_down)
        self.overlay_button.let_go.connect(self._overlay_up)
        self.overlay_button.clicked.connect(lambda: self.overlay_pressed.emit(self.input.id, ""))  # (Space)
        for button in (self.base_button, self.overlay_button):
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._hold_tag = ""
        self.boxes = CopyBoxes()
        self.boxes.clear_requested.connect(self.copy_clear_requested.emit)
        self.edit_button = QToolButton()
        self.edit_button.setObjectName("TileIcon")
        self.edit_button.setIcon(icons.line_icon(icons.draw_pencil, theme.TEXT_2, theme.ACCENT))
        self.edit_button.setToolTip("Edit this input")
        self.edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self.input.id))
        self.ai_button = QToolButton()
        self.ai_button.setObjectName("TileIcon")
        self.ai_button.setIcon(icons.line_icon(icons.draw_sparkle, theme.TEXT_2, theme.ACCENT))
        self.ai_button.setToolTip("Edit with AI: describe a change, and let any AI chat write it")
        self.ai_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_button.clicked.connect(lambda: self.ai_requested.emit(self.input.id))
        self.name = ElidedLabel(inp.name)
        self.name.setObjectName("TileName")
        self.badges = QWidget()
        badge_layout = QHBoxLayout(self.badges)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        badge_layout.setSpacing(4)
        self._badge_labels: dict[str, QLabel] = {}
        for key, draw, tip, colour in (
            ("problem", icons.draw_warning, "Its file has a problem", theme.DANGER),
            ("timed", icons.draw_clock, "Timed: plays for a set length", theme.TEXT_2),
            ("audio", icons.draw_wave, "Follows the sound", theme.TEXT_2),
            ("ai", icons.draw_sparkle, "Made with AI", theme.TEXT_2),
        ):
            label = QLabel()
            label.setToolTip(tip)
            label.setPixmap(icons.line_icon(draw, colour).pixmap(14, 14))
            label.setVisible(False)
            badge_layout.addWidget(label)
            self._badge_labels[key] = label
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(4)
        name_row.addWidget(self.name, 1)
        name_row.addWidget(self.badges)
        name_row.addSpacing(6)  # (the badges, then the buttons)
        name_row.addWidget(self.edit_button)
        name_row.addWidget(self.ai_button)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(4)
        bottom.addWidget(self.base_button, 1)
        bottom.addWidget(self.overlay_button, 1)
        self.bottom = bottom
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.map)
        layout.addLayout(name_row)
        layout.addLayout(bottom)
        layout.addWidget(self.boxes)
        # Preferred (not Fixed): the grid decides the width, so tiles fill their columns
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setProperty("onBase", False)
        self._press_at: QPoint | None = None
        self._locked = False
        self._full = False
        self.set_input(inp)

    # ---------------------------------------------------------------- Send to Overlay, click or hold

    def _overlay_down(self) -> None:
        self._hold_tag = f"{self.input.id}#{next(_tags)}"
        self.overlay_pressed.emit(self.input.id, self._hold_tag)

    def _overlay_up(self, held: float) -> None:
        tag, self._hold_tag = self._hold_tag, ""
        if tag and held >= HOLD_S:
            self.overlay_held.emit(self.input.id, tag)  # (held: a flash, so its copy clears now)

    # ---------------------------------------------------------------- what it shows

    def set_input(self, inp: Input) -> None:
        self.input = inp
        self.name.setText(inp.name)
        self.setToolTip(f"{inp.name}\nDrag to move it · Right-click: more")
        self._badge_labels["timed"].setVisible(not inp.infinite)
        self._badge_labels["ai"].setVisible(inp.ai)
        self._badge_labels["audio"].setVisible(_uses_audio(inp))

    def set_show(self, show: Show) -> None:
        self.map.set_show(show)

    def set_problem(self, problem: str) -> None:
        """A red badge when its file (changed outside CentraLight) can't be used; the tooltip says why."""
        badge = self._badge_labels["problem"]
        badge.setVisible(bool(problem))
        badge.setToolTip(f"Its file has a problem: {problem}" if problem else "")

    def set_on_base(self, on_base: bool) -> None:
        set_property(self, "onBase", on_base)

    def set_copies(self, copies: tuple[tuple[int, float | None], ...], dim: bool = False) -> None:
        """Its copies on the Overlay: (number, progress) each. With 4, Send to Overlay waits until one goes."""
        self.boxes.set_copies(copies, dim)
        self._full = len(copies) >= 4
        self._sync_buttons()

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.OpenHandCursor)
        self.boxes.setEnabled(not locked)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.base_button.setEnabled(not self._locked)
        self.overlay_button.set_usable(not self._locked and not self._full)
        self.overlay_button.setToolTip(
            "It has 4 copies on the Overlay: clear one (its red box) to send another" if self._full and not self._locked
            else "A copy of this input on top of what's showing (up to 4 at once).\n"
                 "Hold it down: the copy shows only while it's held, like a flash button")

    # ---------------------------------------------------------------- size

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        margins = self.layout().contentsMargins()
        inner = width - margins.left() - margins.right()
        name_height = max(self.name.sizeHint().height(), self.badges.sizeHint().height(), self.edit_button.sizeHint().height())
        bottom_height = max(self.base_button.sizeHint().height(), self.overlay_button.sizeHint().height())
        spacing = self.layout().spacing()
        return (margins.top() + self.map.heightForWidth(inner) + spacing + name_height + spacing + bottom_height + spacing
                + COPY_BOX_HEIGHT + margins.bottom())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_MIN, self.heightForWidth(TILE_MIN))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_MIN, self.heightForWidth(TILE_MIN))

    # ---------------------------------------------------------------- the mouse: clicking does nothing; drag to move

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_at = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (self._press_at is not None and event.buttons() & Qt.MouseButton.LeftButton and not self._locked
                and (event.position().toPoint() - self._press_at).manhattanLength() >= QApplication.startDragDistance()):
            self._press_at = None
            self.drag_requested.emit(self.input.id)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_at = None  # (a click on a tile does nothing now: its buttons send it)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.menu_requested.emit(self.input.id, event.globalPos())


def _uses_audio(inp: Input) -> bool:
    text = repr(inp.tracks) + repr(inp.locals) + repr(inp.house)
    return "audio." in text


class NewInputTile(QFrame):
    """The last tile: start a new input (and, from M4, ask an AI to make one)."""

    new_requested = Signal()
    ai_requested = Signal()
    import_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NewTile")
        self.button = QPushButton("+ New input")
        self.button.setProperty("accent", True)
        self.button.setToolTip("Start an empty input and open it in the Editor")
        self.button.clicked.connect(self.new_requested.emit)
        self.ai_button = QPushButton("Create with AI")
        self.ai_button.setIcon(icons.line_icon(icons.draw_sparkle, theme.TEXT_2))
        self.ai_button.setToolTip("Describe a look and let any AI chat write it (copy, paste)")
        self.ai_button.clicked.connect(self.ai_requested.emit)
        self.import_button = QPushButton("From another show…")
        self.import_button.setToolTip("Import inputs from another show (with their pictures and videos)")
        self.import_button.clicked.connect(self.import_requested.emit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addStretch(1)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.ai_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.import_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._sample: InputTile | None = None

    def match(self, sample: InputTile | None) -> None:
        self._sample = sample

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        if self._sample is not None:
            return self._sample.heightForWidth(width)
        return round(width * 9 / 16) + 60

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_MIN, self.heightForWidth(TILE_MIN))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_MIN, max(self.button.sizeHint().height() + 24, 80))


class TileGridLayout(QLayout):
    """Rows of equally wide tiles (between TILE_MIN and TILE_MAX), wrapping to new rows."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 12) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

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
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        margins = self.contentsMargins()
        height = max((item.widget().heightForWidth(TILE_MIN) for item in self._visible()), default=0)
        return QSize(TILE_MIN + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def _visible(self) -> list[QLayoutItem]:
        return [item for item in self._items if item.widget() is not None and not item.widget().isHidden()]

    def columns(self, width: int) -> tuple[int, int]:
        """(columns, tile width) for an area this wide."""
        space = self.spacing()
        columns = max(1, (width + space) // (TILE_MIN + space))
        tile = min(TILE_MAX, (width - space * (columns - 1)) // columns)
        return columns, max(TILE_MIN if columns == 1 and width >= TILE_MIN else 0, tile)

    def _arrange(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        items = self._visible()
        if not items:
            return margins.top() + margins.bottom()
        columns, tile = self.columns(max(1, area.width()))
        tile = max(1, min(tile, area.width()))
        space = self.spacing()
        y = area.y()
        for row_start in range(0, len(items), columns):
            row = items[row_start:row_start + columns]
            row_height = max(item.widget().heightForWidth(tile) for item in row)
            for column, item in enumerate(row):
                if apply:
                    item.setGeometry(QRect(area.x() + column * (tile + space), y, tile, row_height))
            y += row_height + space
        return y - space - rect.y() + margins.bottom()


class InputGrid(QWidget):
    send_requested = Signal(str, bool)  # input id, cut
    menu_requested = Signal(str, QPoint)
    overlay_pressed = Signal(str, str)  # input id, tag: Send to Overlay
    overlay_held = Signal(str, str)  # input id, tag: let go after holding it
    copy_clear_requested = Signal(int)  # copy number
    reorder_requested = Signal(str, int)  # input id, its new place among the tiles
    edit_requested = Signal(str)
    new_requested = Signal()
    ai_requested = Signal(str)  # input id ("" = a new one)
    import_requested = Signal()  # Import from another show…

    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScrollContent")
        self._problems: dict[str, str] = {}
        self._show = show
        self._layout = TileGridLayout(self)
        self.tiles: dict[str, InputTile] = {}
        self._last_colours: dict[str, Mapping[str, RGB]] = {}
        self._base_id = ""
        self._overlay = None  # the engine's OverlayView: each input's copies there
        self._locked = False
        self._drop_index: int | None = None  # while a tile is dragged over the grid: where it would go
        self.setAcceptDrops(True)
        self.new_tile = NewInputTile(self)
        self.new_tile.new_requested.connect(self._new)
        self.new_tile.ai_requested.connect(lambda: self._locked or self.ai_requested.emit(""))
        self.new_tile.import_requested.connect(lambda: self._locked or self.import_requested.emit())
        self.set_show(show)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout.heightForWidth(width)

    def set_show(self, show: Show) -> None:
        self._show = show
        wanted = show.ordered_inputs()
        wanted_ids = [inp.id for inp in wanted]
        for input_id in list(self.tiles):
            if input_id not in wanted_ids:
                tile = self.tiles.pop(input_id)
                self._layout.removeWidget(tile)
                discard(tile)
        for inp in wanted:
            tile = self.tiles.get(inp.id)
            if tile is None:
                tile = InputTile(inp, show, self)
                tile.base_requested.connect(lambda input_id: self._clicked(input_id, False))
                tile.overlay_pressed.connect(lambda input_id, tag: self._locked or self.overlay_pressed.emit(input_id, tag))
                tile.overlay_held.connect(self.overlay_held.emit)  # (letting go always counts, so a flash never sticks)
                tile.copy_clear_requested.connect(lambda number: self._locked or self.copy_clear_requested.emit(number))
                tile.drag_requested.connect(self._drag)
                tile.menu_requested.connect(self.menu_requested.emit)
                tile.edit_requested.connect(self.edit_requested.emit)
                tile.set_locked(self._locked)
                tile.ai_requested.connect(lambda input_id: self._locked or self.ai_requested.emit(input_id))
                tile.set_problem(self._problems.get(inp.id, ""))
                self.tiles[inp.id] = tile
            else:
                tile.set_input(inp)
                tile.set_show(show)
        # keep the layout in dashboard order, with the New input tile last
        while self._layout.count():
            self._layout.takeAt(0)
        for input_id in wanted_ids:
            self._layout.addWidget(self.tiles[input_id])
            self.tiles[input_id].show()
        self.new_tile.match(next(iter(self.tiles.values()), None))
        self._layout.addWidget(self.new_tile)
        self.new_tile.show()
        self.updateGeometry()
        self.set_live(self._base_id, self._overlay, {})

    def set_problems(self, problems: dict) -> None:
        """Input files that can't be used (from the folder watcher): their tiles get a red badge."""
        self._problems = dict(problems)
        for input_id, tile in self.tiles.items():
            tile.set_problem(self._problems.get(input_id, ""))

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        for tile in self.tiles.values():
            tile.set_locked(locked)
        self.new_tile.setEnabled(not locked)

    def set_live(self, base_id: str, overlay, colours: Mapping[str, RGB]) -> None:
        """Mark the tile on Base (coral), and each tile's copies on the Overlay (overlay: the engine's OverlayView, or
        None). The Base tile shows the live colours; the others keep their preview."""
        self._base_id = base_id
        self._overlay = overlay
        if base_id and colours:
            self._last_colours[base_id] = colours
        copies = tuple(getattr(overlay, "copies", ()) or ())
        dim = overlay is not None and not overlay.on
        for input_id, tile in self.tiles.items():
            live = input_id == base_id
            tile.set_on_base(live)
            tile.set_copies(tuple((c.number, c.progress) for c in copies if c.input_id == input_id), dim)
            tile.map.set_colours(self._last_colours.get(input_id, {}), live)

    def set_playing(self, playing: Mapping[str, float]) -> None:
        """Timed inputs on Base or the Overlay: a progress bar on their tiles."""
        for input_id, tile in self.tiles.items():
            tile.map.set_progress(playing.get(input_id))

    def set_base(self, base_id: str, colours: Mapping[str, RGB]) -> None:
        self.set_live(base_id, self._overlay, colours)

    # ---------------------------------------------------------------- dragging a tile to move it

    def _drag(self, input_id: str) -> None:
        tile = self.tiles.get(input_id)
        if tile is None or self._locked:
            return
        mime = QMimeData()
        mime.setData(TILE_MIME, input_id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        picture = tile.grab()
        drag.setPixmap(picture.scaledToWidth(max(1, picture.width() * 2 // 3), Qt.TransformationMode.SmoothTransformation))
        drag.setHotSpot(QPoint(drag.pixmap().width() // 2, drag.pixmap().height() // 2))
        drag.exec(Qt.DropAction.MoveAction)
        self._set_drop_index(None)

    def drop_index(self, at: QPoint) -> int:
        """Where a tile dropped at this point goes among the tiles (0 = first)."""
        ordered = [self.tiles[i] for i in (inp.id for inp in self._show.ordered_inputs()) if i in self.tiles]
        for index, tile in enumerate(ordered):
            rect = tile.geometry()
            same_row = rect.top() - self._layout.spacing() / 2 <= at.y() < rect.bottom() + self._layout.spacing() / 2
            if (same_row and at.x() < rect.center().x()) or at.y() < rect.top() - self._layout.spacing() / 2:
                return index
        return len(ordered)

    def _set_drop_index(self, index: int | None) -> None:
        if index != self._drop_index:
            self._drop_index = index
            self.update()

    @staticmethod
    def _dragged_id(event) -> str:
        mime = event.mimeData()
        return bytes(mime.data(TILE_MIME)).decode("utf-8", "replace") if mime is not None and mime.hasFormat(TILE_MIME) else ""

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._dragged_id(event) in self.tiles and not self._locked:
            event.acceptProposedAction()
            self._set_drop_index(self.drop_index(event.position().toPoint()))
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragged_id(event) in self.tiles and not self._locked:
            event.acceptProposedAction()
            self._set_drop_index(self.drop_index(event.position().toPoint()))
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_drop_index(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        input_id = self._dragged_id(event)
        index = self.drop_index(event.position().toPoint())
        self._set_drop_index(None)
        if input_id in self.tiles and not self._locked:
            event.acceptProposedAction()
            self.reorder_requested.emit(input_id, index)
        else:
            event.ignore()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._drop_index is None:
            return
        ordered = [self.tiles[i] for i in (inp.id for inp in self._show.ordered_inputs()) if i in self.tiles]
        if not ordered:
            return
        if self._drop_index < len(ordered):
            rect = ordered[self._drop_index].geometry()
            x = rect.left() - self._layout.spacing() / 2
        else:
            rect = ordered[-1].geometry()
            x = rect.right() + self._layout.spacing() / 2
        painter = QPainter(self)
        painter.setPen(QPen(QColor(theme.ACCENT), 3.0))
        painter.drawLine(QPoint(round(x), rect.top()), QPoint(round(x), rect.bottom()))
        painter.end()

    def _new(self) -> None:
        if not self._locked:
            self.new_requested.emit()

    def set_preview(self, input_id: str, colours: Mapping[str, RGB]) -> None:
        """What an input that isn't on air would look like (from the Previewer)."""
        if input_id == self._base_id or not colours:
            return
        self._last_colours[input_id] = colours
        tile = self.tiles.get(input_id)
        if tile is not None:
            tile.map.set_colours(colours, False)

    @property
    def base_id(self) -> str:
        return self._base_id

    def _clicked(self, input_id: str, cut: bool) -> None:
        if not self._locked:
            self.send_requested.emit(input_id, cut)
