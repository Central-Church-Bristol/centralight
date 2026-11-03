"""The dashboard's input grid: one tile per input.

A tile shows a mini light map (the live colours while the input is on Base, otherwise
the colours it showed last time), its name, and small icons for timed, sound-driven and
AI-made inputs. A coral border means it's on Base.

Click: send to Base with its transition. Shift-click: cut. Right-click: more.
Tiles are laid out in rows of equal width that wrap like words, and the grid scrolls
instead of squashing them.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
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
from .widgets import ElidedLabel, set_property, discard

TILE_MIN = 200  # wide enough for the bottom row: overlays 1-4, pencil and sparkle
TILE_MAX = 270


class MiniLightMap(QWidget):
    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show = show
        self._colours: Mapping[str, RGB] = {}
        self._live = False
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
            radius = max(2.5, fx.map.r * canvas.width() * 0.45)
            rgb = self._colours.get(fx.id)
            if rgb is None:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
            else:
                colour = rgb_colour((rgb[0] * dim, rgb[1] * dim, rgb[2] * dim))
                painter.setBrush(colour)
                painter.setPen(QPen(QColor(theme.BORDER_STRONG) if max(rgb) < 0.1 else colour.lighter(130), 1))
            painter.drawEllipse(QRectF(centre_x - radius, centre_y - radius, radius * 2, radius * 2))
        painter.end()


class InputTile(QFrame):
    clicked = Signal(str, bool)  # input id, cut (Shift)
    menu_requested = Signal(str, QPoint)  # input id, where (global)
    overlay_requested = Signal(str, int)  # input id, overlay slot 1-4
    edit_requested = Signal(str)
    ai_requested = Signal(str)  # input id: edit it with AI

    def __init__(self, inp: Input, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.input = inp
        self.map = MiniLightMap(show)
        self.overlay_buttons: list[QToolButton] = []
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(3)
        for slot in range(1, 5):
            button = QToolButton()
            button.setObjectName("TileButton")
            button.setText(str(slot))
            button.setToolTip(f"Overlay {slot}: put this input on top (click again to take it off)")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c=False, s=slot: self.overlay_requested.emit(self.input.id, s))
            button.setProperty("inSlot", False)
            bottom.addWidget(button)
            self.overlay_buttons.append(button)
        bottom.addStretch(1)
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
        bottom.addWidget(self.edit_button)
        bottom.addWidget(self.ai_button)
        self.bottom = bottom
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
        name_row.setSpacing(6)
        name_row.addWidget(self.name, 1)
        name_row.addWidget(self.badges)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.map)
        layout.addLayout(name_row)
        layout.addLayout(bottom)
        # Preferred (not Fixed): the grid decides the width, so tiles fill their columns
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setProperty("onBase", False)
        self._pressed = False
        self.set_input(inp)

    def set_input(self, inp: Input) -> None:
        self.input = inp
        self.name.setText(inp.name)
        self.setToolTip(f"{inp.name}\nClick: send to Base · Shift-click: cut · Right-click: more")
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

    def set_overlays(self, slots: set[int]) -> None:
        """Which overlay slots this input is in (their buttons turn lime)."""
        for slot, button in enumerate(self.overlay_buttons, start=1):
            set_property(button, "inSlot", slot in slots)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        margins = self.layout().contentsMargins()
        inner = width - margins.left() - margins.right()
        name_height = max(self.name.sizeHint().height(), self.badges.sizeHint().height())
        bottom_height = max(self.edit_button.sizeHint().height(), self.overlay_buttons[0].sizeHint().height())
        spacing = self.layout().spacing()
        return margins.top() + self.map.heightForWidth(inner) + spacing + name_height + spacing + bottom_height + margins.bottom()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_MIN, self.heightForWidth(TILE_MIN))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_MIN, self.heightForWidth(TILE_MIN))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            if self.rect().contains(event.position().toPoint()):
                cut = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self.clicked.emit(self.input.id, cut)
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addStretch(1)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.ai_button, 0, Qt.AlignmentFlag.AlignHCenter)
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
    overlay_requested = Signal(str, int)  # input id, slot
    edit_requested = Signal(str)
    new_requested = Signal()
    ai_requested = Signal(str)  # input id ("" = a new one)

    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScrollContent")
        self._problems: dict[str, str] = {}
        self._show = show
        self._layout = TileGridLayout(self)
        self.tiles: dict[str, InputTile] = {}
        self._last_colours: dict[str, Mapping[str, RGB]] = {}
        self._base_id = ""
        self._overlay_ids: tuple[str, ...] = ()
        self._locked = False
        self.new_tile = NewInputTile(self)
        self.new_tile.new_requested.connect(self._new)
        self.new_tile.ai_requested.connect(lambda: self._locked or self.ai_requested.emit(""))
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
                tile.clicked.connect(self._clicked)
                tile.menu_requested.connect(self.menu_requested.emit)
                tile.overlay_requested.connect(self._overlay)
                tile.edit_requested.connect(self.edit_requested.emit)
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
        self.set_live(self._base_id, self._overlay_ids, {})

    def set_problems(self, problems: dict) -> None:
        """Input files that can't be used (from the folder watcher): their tiles get a red badge."""
        self._problems = dict(problems)
        for input_id, tile in self.tiles.items():
            tile.set_problem(self._problems.get(input_id, ""))

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        for tile in self.tiles.values():
            tile.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.PointingHandCursor)
            for button in tile.overlay_buttons:
                button.setEnabled(not locked)
        self.new_tile.setEnabled(not locked)

    def set_live(self, base_id: str, overlay_ids: tuple[str, ...], colours: Mapping[str, RGB]) -> None:
        """Mark the tile on Base (coral) and the overlay slots each input is in (lime).
        The Base tile shows the live colours; the others keep their preview."""
        self._base_id = base_id
        self._overlay_ids = tuple(overlay_ids)
        if base_id and colours:
            self._last_colours[base_id] = colours
        for input_id, tile in self.tiles.items():
            live = input_id == base_id
            tile.set_on_base(live)
            tile.set_overlays({slot for slot, other in enumerate(self._overlay_ids, start=1) if other == input_id})
            tile.map.set_colours(self._last_colours.get(input_id, {}), live)

    def set_base(self, base_id: str, colours: Mapping[str, RGB]) -> None:
        self.set_live(base_id, self._overlay_ids, colours)

    def _overlay(self, input_id: str, slot: int) -> None:
        if not self._locked:
            self.overlay_requested.emit(input_id, slot)

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
