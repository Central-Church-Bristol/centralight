"""The Console page (M15): a hands-on lighting desk. Choose lights on the room map (or by group), then set them: what
you set takes over those lights until Release, like a desk's programmer.

Left: the room map, with the buttons that choose lights. Right: the Look (brightness, colour, saved colours, fan,
fade), the Advanced switch's raw channels, and a chase. Every control is a named action (select a group,
brightness, speed, tap, flash, release), so a physical controller can be mapped to them later.
"""

from __future__ import annotations

import colorsys
from dataclasses import replace
from collections.abc import Mapping

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..console import (
    COLOUR,
    choose,
    colours_across,
    hue_colour,
    odd_even,
    one_at_a_time,
    step_from,
    DIRECTIONS,
    STROBE_NOTE,
    Chase,
    ChaseView,
    ConsoleView,
    channels_of,
)
from ..model import Show
from . import icons, theme
from .light_view import LightView
from .widgets import ElidedLabel, Fader, FlowLayout, Panel, ResettableSplitter, ToggleSwitch

ORDERS = (("clicked", "As clicked"), ("left", "Left to right"), ("right", "Right to left"))
FANS = (("", "None"), ("rainbow", "Rainbow"), ("colours", "From one colour to another"), ("brightness", "Brightness from … to …"))
SAVED_SIDE = 24  # a saved colour's swatch, in logical pixels
STEP_SIDE = 64  # a chase step's card
NO_CHASE_LIGHTS = "On: no lights yet (choose some, then Add step)"


def hex_of(rgb) -> str:
    r, g, b = (max(0, min(255, round(float(v) * 255))) for v in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


# ---------------------------------------------------------------- the room map, for choosing lights


class ConsoleMap(LightView):
    """The room map, as on the Dashboard, for choosing lights: click one, Ctrl- or Shift-click to add or take away,
    or drag a box round several. Chosen lights have a ring and their number in the order; lights the Console holds
    have an amber mark."""

    selection_changed = Signal(list)  # light ids, in their order

    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(show, parent)
        self.setObjectName("ConsoleMap")
        self._selection: list[str] = []
        self._held: frozenset[str] = frozenset()
        self._band: tuple[QPointF, QPointF] | None = None
        self._band_adds = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def selection(self) -> list[str]:
        return list(self._selection)

    def set_selection(self, ids: list[str]) -> None:
        ids = [i for i in dict.fromkeys(ids) if any(fx.id == i for fx in self._show.colour_fixtures())]
        if ids != self._selection:
            self._selection = ids
            self.update()

    def set_held(self, ids) -> None:
        ids = frozenset(ids)
        if ids != self._held:
            self._held = ids
            self.update()

    # ------------------------------------------------------------ mouse

    def fixture_at(self, point: QPointF):  # noqa: D102 (LightView's, but a light is its dot and a little round it)
        canvas = self.canvas_rect()
        best = None
        for fx in self._show.colour_fixtures():
            if fx.map is None:
                continue
            centre, radius = self.dot_geometry(fx, canvas)
            reach = max(2.5, radius * 0.32) + 10  # (the dot, not its whole glow: a box can start between lights)
            distance = ((centre.x() - point.x()) ** 2 + (centre.y() - point.y()) ** 2) ** 0.5
            if distance <= reach and (best is None or distance < best[0]):
                best = (distance, fx)
        return best[1] if best else None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        adding = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        fx = self.fixture_at(event.position())
        if fx is not None:
            if adding:
                chosen = [i for i in self._selection if i != fx.id] if fx.id in self._selection else self._selection + [fx.id]
            else:
                chosen = [fx.id]
            self._choose(chosen)
            return
        self._band = (event.position(), event.position())
        self._band_adds = adding
        if not adding and self._selection:
            self._choose([])

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._band is not None:
            self._band = (self._band[0], event.position())
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._band is None:
            return super().mouseReleaseEvent(event)
        start, end = self._band
        self._band = None
        box = QRectF(start, end).normalized()
        if box.width() < 3 and box.height() < 3:
            self.update()
            return
        canvas = self.canvas_rect()
        inside = [fx for fx in self._show.colour_fixtures()
                  if fx.map is not None and box.contains(self.dot_geometry(fx, canvas)[0])]
        inside.sort(key=lambda fx: (fx.map.x, fx.map.y))  # (a box chooses left to right)
        chosen = list(self._selection) if self._band_adds else []
        chosen += [fx.id for fx in inside if fx.id not in chosen]
        self._choose(chosen)

    def _choose(self, ids: list[str]) -> None:
        self._selection = ids
        self.update()
        self.selection_changed.emit(list(ids))

    # ------------------------------------------------------------ painting

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        canvas = self.canvas_rect()
        if canvas.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = theme.ui_font(11, QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        by_id = {fx.id: fx for fx in self._show.colour_fixtures() if fx.map is not None}
        for light_id in self._held:  # (an amber mark at the top left: the Console holds this light)
            fx = by_id.get(light_id)
            if fx is None:
                continue
            centre, radius = self.dot_geometry(fx, canvas)
            core = max(2.5, radius * 0.32)
            mark = QPointF(centre.x() - core - 4, centre.y() - core - 4)
            painter.setPen(QPen(QColor(theme.WINDOW), 1.5))
            painter.setBrush(QColor(theme.WARN))
            painter.drawEllipse(mark, 4.5, 4.5)
        for number, light_id in enumerate(self._selection, start=1):
            fx = by_id.get(light_id)
            if fx is None:
                continue
            centre, radius = self.dot_geometry(fx, canvas)
            core = max(2.5, radius * 0.32)
            ring = core + 6
            painter.setPen(QPen(QColor(theme.ACCENT), 2.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, ring, ring)
            text = str(number)
            side = max(16, fm.horizontalAdvance(text) + 8)
            badge = QRectF(centre.x() + ring * 0.55, centre.y() - ring - side * 0.35, side, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(badge, 8, 8)
            painter.setPen(QColor(theme.ACCENT_TEXT))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        if self._band is not None:
            box = QRectF(*self._band).normalized()
            painter.setPen(QPen(QColor(theme.ACCENT), 1, Qt.PenStyle.DashLine))
            fill = QColor(theme.ACCENT)
            fill.setAlpha(40)
            painter.setBrush(fill)
            painter.drawRect(box)
        painter.end()


# ---------------------------------------------------------------- small parts


class ColourButton(QAbstractButton):
    """A rounded swatch that's a button: a saved colour, the Look's colour, or a fan's end."""

    def __init__(self, colour: str = "#FFFFFF", side: int = SAVED_SIDE, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = QColor(colour)
        self._side = side
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(QColor(colour).name().upper())

    def colour(self) -> str:
        return self._colour.name().upper()

    def set_colour(self, colour: str) -> None:
        self._colour = QColor(colour)
        self.setToolTip(self._colour.name().upper())
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(round(self._side * 1.6) if self._side > SAVED_SIDE else self._side, self._side)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(theme.ACCENT if self.underMouse() else theme.BORDER_STRONG), 1.2))
        painter.setBrush(self._colour)
        painter.drawRoundedRect(rect, 5, 5)
        painter.end()


class StepCard(QAbstractButton):
    """One step of a chase: its number and each light's colour in it (in the chase's order)."""

    def __init__(self, number: int, colours: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.number = number
        self.colours = colours
        self.current = False
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(f"Step {number}: click to choose it (Copy, Remove, move it)")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(STEP_SIDE, STEP_SIDE)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        edge = theme.ACCENT if self.isChecked() else theme.LIVE if self.current else theme.BORDER
        painter.setPen(QPen(QColor(edge), 2 if (self.isChecked() or self.current) else 1))
        painter.setBrush(QColor(theme.CARD))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(QColor(theme.TEXT_2))
        painter.setFont(theme.ui_font(11))
        painter.drawText(rect.adjusted(6, 3, -4, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, str(self.number))
        count = len(self.colours)
        if count:
            columns = min(4, count)
            rows = (count + columns - 1) // columns
            area = rect.adjusted(6, 20, -6, -6)
            cell = min(area.width() / columns, area.height() / rows)
            for index, colour in enumerate(self.colours):
                x = area.left() + (index % columns) * cell
                y = area.top() + (index // columns) * cell
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(colour))
                painter.drawRoundedRect(QRectF(x + 1, y + 1, cell - 2, cell - 2), 2, 2)
        painter.end()


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Caption")
    return label


def _value_label(text: str = "", width_text: str = "mixed") -> QLabel:
    label = QLabel(text)
    label.setObjectName("Caption")
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    label.setMinimumWidth(label.fontMetrics().horizontalAdvance(width_text) + 6)
    return label


# ---------------------------------------------------------------- the page


class ConsolePage(QWidget):
    """The Console tab. The window feeds it the show and the engine's ConsoleView; what people do comes out as named
    actions (the signals below), each for the chosen lights in their order."""

    selection_changed = Signal(list)  # light ids, in order
    brightness_set = Signal(list, float, float)  # lights, level 0-1, fade (seconds)
    colour_set = Signal(list, object, float)  # lights, (r, g, b) 0-1, fade
    fan_set = Signal(list, dict, float)  # lights, {"kind": ..., ...}, fade
    channel_set = Signal(list, str, int, float)  # lights, attribute, 0-255, fade
    release_requested = Signal(list)  # the chosen lights
    release_all_requested = Signal()
    group_save_requested = Signal(list)  # the chosen lights (the window asks for a name)
    saved_colour_added = Signal(str)  # "#RRGGBB"
    look_save_requested = Signal(list)  # Save look as input…
    chase_play_requested = Signal(object)  # the Chase
    chase_pause_requested = Signal(bool)
    chase_next_requested = Signal()
    chase_tap_requested = Signal()
    chase_edited = Signal(object)  # the Chase, changed on the page
    chase_save_requested = Signal(object)  # Save chase as input…
    advanced_switched = Signal(bool)
    look_restore = Signal(dict, list)  # Cancel in the colour picker: values to hold again, lights to let go of colour
    saved_colour_removed = Signal(str)

    def __init__(self, show: Show, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConsolePage")
        self.show_data = show
        self._view = ConsoleView()
        self._locked = False
        self._colour = "#FFFFFF"
        self._chase = Chase()
        self._chosen_step = -1
        self._showing = False  # (filling the chase's controls from a chase: not a change someone made)
        self._saved_colours: list[str] = []
        self.master = 1.0  # Master now (the window keeps it up to date): Add step takes it back out of the colours

        # --- left: the lights
        lights = Panel("Lights")
        self.choose_box = QWidget()
        self.choose_flow = FlowLayout(self.choose_box, spacing=6)
        self.choose_buttons: dict[str, QPushButton] = {}
        for key, text, tip in (("all", "All", "Choose every light"), ("none", "None", "Choose no lights"),
                               ("invert", "Invert", "Choose the lights that aren't chosen, and not the ones that are"),
                               ("odd", "Odd", "Every other light, from the first (in the order below)"),
                               ("even", "Even", "Every other light, from the second")):
            self.choose_buttons[key] = self._choose_button(text, tip)
            self.choose_buttons[key].clicked.connect(lambda _checked=False, k=key: self.choose(k))
        self.group_buttons: dict[str, QPushButton] = {}  # "patch:<group>" or "saved:<name>" -> its button
        self.save_group_button = QPushButton("Save as group…")
        self.save_group_button.setObjectName("ToolToggle")
        self.save_group_button.setToolTip("Keep the chosen lights as a group of this show, with a button of its own")
        self.save_group_button.clicked.connect(lambda: self.group_save_requested.emit(self.map.selection()))
        lights.body.addWidget(self.choose_box)
        self.map = ConsoleMap(show)
        self.map.selection_changed.connect(self._map_chose)
        lights.body.addWidget(self.map, 1)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(_caption("Order"))
        self.order = QComboBox()
        for key, text in ORDERS:
            self.order.addItem(text, key)
        self.order.setToolTip("The order fans, chases and Odd / Even go through the chosen lights")
        self.order.currentIndexChanged.connect(lambda _i: self._reorder())
        bottom.addWidget(self.order)
        self.summary = ElidedLabel("No lights chosen")
        self.summary.setObjectName("Caption")
        bottom.addWidget(self.summary, 1)
        self.legend = QLabel(f'<span style="color:{theme.ACCENT}">◯</span> chosen, in order&nbsp;&nbsp;&nbsp;&nbsp;'
                             f'<span style="color:{theme.WARN}">●</span> held by the Console')
        self.legend.setObjectName("Hint")
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        bottom.addWidget(self.legend)
        lights.body.addLayout(bottom)

        # --- right: Advanced, Release, then the Look, the channels and the chase
        top = QHBoxLayout()
        top.setSpacing(8)
        self.advanced = ToggleSwitch()
        self.advanced.setToolTip("Every channel of the chosen lights as a fader (for people who know lighting desks)")
        self.advanced.toggled.connect(self._advanced_switched)
        top.addWidget(self.advanced)
        advanced_label = QLabel("Advanced")
        top.addWidget(advanced_label)
        top.addStretch(1)
        self.release_button = QPushButton("Release")
        self.release_button.setToolTip("Hand the chosen lights back to CentraLight's inputs (they fade back)")
        self.release_button.clicked.connect(lambda: self.release_requested.emit(self.map.selection()))
        self.release_all_button = QPushButton("Release all")
        self.release_all_button.setProperty("danger", True)
        self.release_all_button.setToolTip("Hand every light the Console holds back to CentraLight's inputs")
        self.release_all_button.clicked.connect(self.release_all_requested.emit)
        top.addWidget(self.release_button)
        top.addWidget(self.release_all_button)

        self.look = self._look_panel()
        self.channels_panel = self._channels_panel()
        self.chase_panel = self._chase_panel()
        column = QWidget()
        column.setObjectName("ScrollContent")
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 4, 0)
        column_layout.setSpacing(12)
        column_layout.addWidget(self.look)
        column_layout.addWidget(self.channels_panel)
        column_layout.addWidget(self.chase_panel)
        column_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(column)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # never narrower than its widest row and the scroll bar beside it (nothing is cut off on the right)
        scroll.setMinimumWidth(column.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 2)
        self.scroll = scroll
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addLayout(top)
        right_layout.addWidget(scroll, 1)
        right.setMinimumWidth(380)

        self.splitter = ResettableSplitter(Qt.Orientation.Horizontal, self.default_split)
        self.splitter.setObjectName("ConsoleSplitter")
        self.splitter.addWidget(lights)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        lights.setMinimumWidth(420)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(self.splitter)

        self.channels_panel.setVisible(False)
        self.set_show(show)

    # ------------------------------------------------------------ building the panels

    def _choose_button(self, text: str, tip: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("ToolToggle")
        button.setToolTip(tip)
        return button

    def _look_panel(self) -> Panel:
        panel = Panel("Look")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        self.brightness = Fader()
        self.brightness.setToolTip("The chosen lights' brightness (their own dimmer)")
        self.brightness_value = _value_label("100%")
        self.brightness.set_level(1.0)
        self.brightness.moved.connect(self._brightness_moved)
        grid.addWidget(_caption("Brightness"), 0, 0)
        grid.addWidget(self.brightness, 0, 1)
        grid.addWidget(self.brightness_value, 0, 2)

        self.colour_button = ColourButton("#FFFFFF", 30)
        self.colour_button.setToolTip("The chosen lights' colour: click to pick one (they follow as you pick)")
        self.colour_button.clicked.connect(self.pick_colour)
        self.colour_text = QLabel("#FFFFFF")
        self.colour_text.setObjectName("Caption")
        colour_row = QHBoxLayout()
        colour_row.setSpacing(8)
        colour_row.addWidget(self.colour_button)
        colour_row.addWidget(self.colour_text)
        colour_row.addStretch(1)
        grid.addWidget(_caption("Colour"), 1, 0)
        grid.addLayout(colour_row, 1, 1, 1, 2)

        self.saved_box = QWidget()
        self.saved_flow = FlowLayout(self.saved_box, spacing=6)
        self.saved_buttons: list[ColourButton] = []
        self.add_saved = QPushButton("+")
        self.add_saved.setObjectName("ToolToggle")
        self.add_saved.setToolTip("Save the colour above with this show")
        self.add_saved.clicked.connect(lambda: self.saved_colour_added.emit(self._colour))
        grid.addWidget(_caption("Saved"), 2, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.saved_box, 2, 1, 1, 2)

        self.fan = QComboBox()
        for key, text in FANS:
            self.fan.addItem(text, key)
        self.fan.setToolTip("Spread values across the chosen lights, in their order")
        self.fan.currentIndexChanged.connect(lambda _i: (self._fan_changed(), self.apply_fan()))
        grid.addWidget(_caption("Fan"), 3, 0)
        grid.addWidget(self.fan, 3, 1, 1, 2)
        self.fan_extra = QWidget()
        extra = QHBoxLayout(self.fan_extra)
        extra.setContentsMargins(0, 0, 0, 0)
        extra.setSpacing(8)
        self.fan_from = ColourButton("#FF3B30", SAVED_SIDE)
        self.fan_to = ColourButton("#1E3AFF", SAVED_SIDE)
        self.fan_low = QSpinBox()
        self.fan_low.setRange(0, 100)
        self.fan_low.setSuffix(" %")
        self.fan_high = QSpinBox()
        self.fan_high.setRange(0, 100)
        self.fan_high.setValue(100)
        self.fan_high.setSuffix(" %")
        self.fan_from_label = _caption("from")
        self.fan_to_label = _caption("to")
        for widget in (self.fan_from_label, self.fan_from, self.fan_low, self.fan_to_label, self.fan_to, self.fan_high):
            extra.addWidget(widget)
        self.fan_from.clicked.connect(lambda: self._pick_fan_end(self.fan_from))
        self.fan_to.clicked.connect(lambda: self._pick_fan_end(self.fan_to))
        self.fan_low.valueChanged.connect(lambda _v: self.apply_fan())
        self.fan_high.valueChanged.connect(lambda _v: self.apply_fan())
        extra.addStretch(1)
        grid.addWidget(self.fan_extra, 4, 1, 1, 2)
        self.fan_extra.setVisible(False)

        self.fade = QDoubleSpinBox()
        self.fade.setRange(0.0, 60.0)
        self.fade.setDecimals(1)
        self.fade.setSingleStep(0.5)
        self.fade.setSuffix(" s")
        self.fade.setToolTip("How long changes take to fade in (0: at once)")
        fade_row = QHBoxLayout()
        fade_row.setSpacing(8)
        fade_row.addWidget(self.fade)
        fade_hint = QLabel("for each change")
        fade_hint.setObjectName("Hint")
        fade_row.addWidget(fade_hint)
        fade_row.addStretch(1)
        grid.addWidget(_caption("Fade"), 5, 0)
        grid.addLayout(fade_row, 5, 1, 1, 2)
        panel.body.addLayout(grid)

        self.save_look = QPushButton("Save look as input…")
        self.save_look.setToolTip("Make an input of the chosen lights' look, for the Dashboard, automations and the "
                                  "Stream Deck (colour and brightness)")
        self.save_look.clicked.connect(lambda: self.look_save_requested.emit(self.map.selection()))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.save_look)
        panel.body.addLayout(row)
        return panel

    def _channels_panel(self) -> Panel:
        panel = Panel("Channels")
        hint = QLabel("Each channel of the chosen lights, 0 to 255. “mixed”: the lights differ.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        panel.body.addWidget(hint)
        self.channel_grid = QGridLayout()
        self.channel_grid.setHorizontalSpacing(10)
        self.channel_grid.setVerticalSpacing(8)
        self.channel_grid.setColumnStretch(1, 1)
        panel.body.addLayout(self.channel_grid)
        self.channel_rows: dict[str, tuple[QLabel, Fader, QLabel]] = {}
        self.strobe_note = QLabel(f"⚠  {STROBE_NOTE}")
        self.strobe_note.setObjectName("Hint")
        self.strobe_note.setWordWrap(True)
        for row, (attr, name) in enumerate(channels_of("slimpar9")):
            caption = _caption(name)
            fader = Fader()
            fader.setToolTip(f"{name}, 0 to 255")
            value = _value_label("0")
            fader.moved.connect(lambda level, a=attr: self._channel_moved(a, level))
            self.channel_grid.addWidget(caption, row * 2, 0)
            self.channel_grid.addWidget(fader, row * 2, 1)
            self.channel_grid.addWidget(value, row * 2, 2)
            self.channel_rows[attr] = (caption, fader, value)
            if attr == "strobe":
                self.channel_grid.addWidget(self.strobe_note, row * 2 + 1, 0, 1, 3)
        return panel

    def _chase_panel(self) -> Panel:
        panel = Panel("Chase")
        self.chase_lights = ElidedLabel(NO_CHASE_LIGHTS)  # "On: Pillars" (the lights the chase runs on)
        self.chase_lights.setObjectName("Caption")
        panel.body.addWidget(self.chase_lights)
        self.steps_box = QWidget()
        self.steps_row = QHBoxLayout(self.steps_box)
        self.steps_row.setContentsMargins(0, 0, 0, 0)
        self.steps_row.setSpacing(6)
        self.steps_row.addStretch(1)
        steps_scroll = QScrollArea()
        steps_scroll.setWidgetResizable(True)
        steps_scroll.setWidget(self.steps_box)
        steps_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        steps_scroll.setFixedHeight(STEP_SIDE + 22)
        self.steps_scroll = steps_scroll
        panel.body.addWidget(steps_scroll)
        self.step_cards: list[StepCard] = []

        edit_box = QWidget()  # (wraps onto two lines when the column is narrow)
        edit = FlowLayout(edit_box, spacing=6)
        self.add_step = QPushButton("+ Add step")
        self.add_step.setToolTip("Add a step: the chosen lights as they look now")
        self.builders = QPushButton("Quick")
        self.builders.setToolTip("Make the steps for you")
        menu = QMenu(self.builders)
        menu.addAction("Colours across the lights", lambda: self.build_chase("across")).setToolTip(
            "The saved colours along the chosen lights, moving on a light each step")
        menu.addAction("One light at a time", lambda: self.build_chase("running")).setToolTip(
            "A running light in the colour above, in the chosen lights' order")
        menu.addAction("Odd / even", lambda: self.build_chase("odd_even")).setToolTip(
            "Every other light in the colour above, then the others")
        menu.setToolTipsVisible(True)
        self.builders.setMenu(menu)
        self.add_step.clicked.connect(self.add_chase_step)
        edit.addWidget(self.add_step)
        edit.addWidget(self.builders)
        self.step_back = QPushButton("◀")
        self.step_back.setToolTip("Move the chosen step earlier")
        self.step_forward = QPushButton("▶")
        self.step_forward.setToolTip("Move the chosen step later")
        self.copy_step = QPushButton("Copy")
        self.copy_step.setToolTip("Copy the chosen step (the copy goes after it)")
        self.remove_step = QPushButton("Remove")
        self.remove_step.setToolTip("Remove the chosen step")
        for button in (self.step_back, self.step_forward, self.copy_step, self.remove_step):
            button.setObjectName("ToolToggle")
            edit.addWidget(button)
        self.step_back.clicked.connect(lambda: self.move_step(-1))
        self.step_forward.clicked.connect(lambda: self.move_step(1))
        self.copy_step.clicked.connect(self.copy_chosen_step)
        self.remove_step.clicked.connect(self.remove_chosen_step)
        panel.body.addWidget(edit_box)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(10.0, 300.0)
        self.speed.setDecimals(0)
        self.speed.setValue(120)
        self.speed.setToolTip("How fast the steps go")
        self.speed_unit = QComboBox()
        self.speed_unit.addItem("BPM (a step a beat)", "bpm")
        self.speed_unit.addItem("seconds a step", "seconds")
        self.tap = QPushButton("Tap")
        self.tap.setToolTip("Tap in time (four taps or so) to set the speed")
        self.tap.clicked.connect(self.tap_tempo)
        self._taps: list[float] = []
        speed_row = QHBoxLayout()
        speed_row.setSpacing(8)
        speed_row.addWidget(self.speed)
        speed_row.addWidget(self.speed_unit)
        speed_row.addWidget(self.tap)
        speed_row.addStretch(1)
        grid.addWidget(_caption("Speed"), 0, 0)
        grid.addLayout(speed_row, 0, 1, 1, 2)
        self.crossfade = Fader()
        self.crossfade.setToolTip("0 %: each step snaps; 100 %: it glides all the way from the step before")
        self.crossfade_value = _value_label("0%")
        self.crossfade.moved.connect(lambda level: (self.crossfade_value.setText(f"{round(level * 100)}%"), self._timing_changed()))
        grid.addWidget(_caption("Crossfade"), 1, 0)
        grid.addWidget(self.crossfade, 1, 1)
        grid.addWidget(self.crossfade_value, 1, 2)
        self.direction = QComboBox()
        for key, text in DIRECTIONS:
            self.direction.addItem(text, key)
        self.repeat = QComboBox()
        self.repeat.addItem("Loop", True)
        self.repeat.addItem("Play once", False)
        way_row = QHBoxLayout()
        way_row.setSpacing(8)
        way_row.addWidget(self.direction)
        way_row.addWidget(self.repeat)
        way_row.addStretch(1)
        grid.addWidget(_caption("Direction"), 2, 0)
        grid.addLayout(way_row, 2, 1, 1, 2)
        self.on_beat = QCheckBox("Step on each beat it hears (Settings → Audio)")
        grid.addWidget(self.on_beat, 3, 1, 1, 2)
        panel.body.addLayout(grid)
        self.speed.valueChanged.connect(lambda _v: self._timing_changed())
        self.speed_unit.currentIndexChanged.connect(lambda _i: self._unit_changed())
        self.direction.currentIndexChanged.connect(lambda _i: self._timing_changed())
        self.repeat.currentIndexChanged.connect(lambda _i: self._timing_changed())
        self.on_beat.toggled.connect(lambda _on: self._timing_changed())

        transport = QHBoxLayout()
        transport.setSpacing(6)
        self.play = QPushButton(" Play")
        self.play.setIcon(icons.line_icon(icons.draw_play, theme.ACCENT_TEXT))
        self.play.setProperty("accent", True)
        self.play.setToolTip("Play the chase on its lights (the Console holds them until Release)")
        self.play.clicked.connect(lambda: self.chase_play_requested.emit(self._chase))
        self.pause = QPushButton(" Pause")
        self.pause.setIcon(icons.line_icon(icons.draw_pause, theme.TEXT_2))
        self.pause.setCheckable(True)
        self.pause.toggled.connect(self.chase_pause_requested.emit)
        self.next_step = QPushButton("Next step")
        self.next_step.clicked.connect(self.chase_next_requested.emit)
        transport.addWidget(self.play)
        transport.addWidget(self.pause)
        transport.addWidget(self.next_step)
        transport.addStretch(1)
        panel.body.addLayout(transport)
        self.save_chase = QPushButton("Save chase as input…")
        self.save_chase.setToolTip("Make an input of the chase (colour and brightness), for the Dashboard, automations "
                                   "and the Stream Deck")
        self.save_chase.clicked.connect(lambda: self.chase_save_requested.emit(self._chase))
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self.save_chase)
        panel.body.addLayout(save_row)
        return panel

    # ------------------------------------------------------------ splits

    def default_split(self) -> list[int]:
        total = max(1, self.splitter.width())
        right = min(560, max(400, round(total * 0.36)))
        return [total - right, right]

    def apply_split(self, sizes) -> None:
        self.splitter.apply_sizes(sizes)

    def splits(self) -> list[int]:
        return self.splitter.sizes()

    # ------------------------------------------------------------ what it shows

    def set_show(self, show: Show) -> None:
        """Another show (or this one changed): its lights, groups and saved colours."""
        self.show_data = show
        self.map.set_show(show)
        self.map.set_selection(self.map.selection())
        self._fill_choose_buttons()
        if list(show.saved_colours) != self._saved_colours or not self.saved_buttons and self.saved_flow.count() == 0:
            self.set_saved_colours(list(show.saved_colours))
        self._summarise()
        if hasattr(self, "chase_lights"):  # (its lights' names and groups may have changed)
            self._show_chase_lights(self._view.chase or ChaseView(self._chase))

    def groups(self) -> dict[str, tuple[str, list[str]]]:
        """Each group button's key -> (its name, its lights in map order): the patch's groups, then the saved ones."""
        lights = self.map_order()
        found: dict[str, tuple[str, list[str]]] = {}
        by_id = {fx.id: fx for fx in self.show_data.colour_fixtures()}
        for light in lights:
            group = by_id[light].group
            if group:
                found.setdefault(f"patch:{group}", (group, []))[1].append(light)
        for saved in self.show_data.groups:
            found[f"saved:{saved.name}"] = (saved.name, [light for light in lights if light in saved.lights])
        return found

    def _fill_choose_buttons(self) -> None:
        groups = self.groups()
        key = [(k, name) for k, (name, _lights) in groups.items()]
        if key == getattr(self, "_groups_key", None):
            return
        self._groups_key = key
        while self.choose_flow.count():
            self.choose_flow.takeAt(0)
        for button in self.group_buttons.values():
            button.hide()
            button.deleteLater()
        self.group_buttons = {}
        for button in self.choose_buttons.values():
            self.choose_flow.addWidget(button)
        for group_key, (name, _lights) in groups.items():
            saved = group_key.startswith("saved:")
            button = self._choose_button(name, f"Choose the lights of {name}" + (" (a group saved with this show)" if saved else ""))
            button.clicked.connect(lambda _checked=False, k=group_key: self.choose_group(k))
            self.group_buttons[group_key] = button
            self.choose_flow.addWidget(button)
        self.choose_flow.addWidget(self.save_group_button)
        self.choose_box.updateGeometry()

    def map_order(self) -> list[str]:
        """Every colour light on the map, left to right (top to bottom where they line up)."""
        lights = [fx for fx in self.show_data.colour_fixtures() if fx.map is not None]
        lights.sort(key=lambda fx: (round(fx.map.x, 3), fx.map.y))
        return [fx.id for fx in lights]

    def choose(self, kind: str) -> None:
        """All, None, Invert, Odd or Even (named actions, for a controller later too)."""
        self._apply_choice(choose(kind, self.map_order(), self.map.selection()))

    def choose_group(self, key: str) -> None:
        group = self.groups().get(key)
        if group is not None:
            self._apply_choice(group[1])

    def _apply_choice(self, ids: list[str]) -> None:
        self.map.set_selection(self._ordered(ids))
        self._map_chose(self.map.selection())

    def _ordered(self, ids: list[str]) -> list[str]:
        """The lights in the Order chosen below the map (as clicked: as they are)."""
        key = self.order.currentData()
        if key == "clicked":
            return list(ids)
        place = {light: index for index, light in enumerate(self.map_order())}
        return sorted(ids, key=lambda light: place.get(light, len(place)), reverse=key == "right")

    def set_saved_colours(self, colours: list[str]) -> None:
        self._saved_colours = list(colours)
        while self.saved_flow.count():
            self.saved_flow.takeAt(0)
        for button in self.saved_buttons:
            button.hide()
            button.deleteLater()
        self.saved_buttons = []
        for colour in colours:
            button = ColourButton(colour)
            button.setToolTip(f"{colour}: click to give the chosen lights this colour (right-click: Remove)")
            button.clicked.connect(lambda _checked=False, c=colour: self.use_colour(c))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda _pos, c=colour, b=button: self._saved_menu(c, b))
            self.saved_buttons.append(button)
            self.saved_flow.addWidget(button)
        self.saved_flow.addWidget(self.add_saved)

    def _saved_menu(self, colour: str, button: QWidget) -> None:
        menu = QMenu(self)
        menu.addAction(f"Remove {colour}", lambda: self.saved_colour_removed.emit(colour))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def set_selection(self, ids: list[str]) -> None:
        self.map.set_selection(ids)
        self._summarise()
        self._fill_channels()

    def set_look(self, brightness: float, colour: str) -> None:
        """The Look's controls (from the chosen lights)."""
        self.brightness.set_level(brightness)
        self.brightness_value.setText(f"{round(brightness * 100)}%")
        self._colour = colour.upper()
        self.colour_button.set_colour(self._colour)
        self.colour_text.setText(self._colour)

    def set_fan(self, kind: str) -> None:
        index = self.fan.findData(kind)
        self.fan.setCurrentIndex(max(0, index))
        self._fan_changed()

    def set_view(self, view: ConsoleView) -> None:
        """The engine's ConsoleView: which lights it holds, and every channel's value now (each frame: only what
        changed is redrawn)."""
        if view == self._view:
            return
        old, self._view = self._view, view
        if view.held != old.held:
            self.map.set_held(light for light, attrs in view.held.items() if attrs)
            self._summarise()
        if view.values != old.values or view.held != old.held:
            self._fill_channels()
        if view.chase != old.chase and view.chase is not None:
            if old.chase is None or view.chase.chase != old.chase.chase:
                self._chase = view.chase.chase
                self._show_chase(view.chase)
            else:
                self._mark_step(view.chase)
        elif view.chase is None and old.chase is not None:
            self._show_chase(ChaseView(self._chase))

    def _mark_step(self, view: ChaseView) -> None:
        """Just which step is playing (and Pause), and the lights it's on, without rebuilding the cards."""
        self._show_chase_lights(view)
        for number, card in enumerate(self.step_cards):
            current = view.playing and view.step == number
            if card.current != current:
                card.current = current
                card.update()
        if self.pause.isChecked() != view.paused:
            blocked = self.pause.blockSignals(True)
            self.pause.setChecked(view.paused)
            self.pause.blockSignals(blocked)

    def set_levels(self, colours: Mapping, house: Mapping) -> None:
        self.map.set_levels(colours, house)

    def set_chase(self, chase: Chase) -> None:
        """The chase being made on the page."""
        self._chase = chase
        self._show_chase(ChaseView(chase))

    def _show_chase(self, view: ChaseView) -> None:
        self._showing = True
        try:
            self._fill_chase(view)
        finally:
            self._showing = False

    def on_text(self, lights) -> str:
        """"On: SlimPAR 101, Pillars": the lights, with a whole group's name instead of its lights (the patch's groups
        first, then the saved ones), in the order the lights come."""
        names = self._light_names()
        lights = [light for light in lights if light in names]
        if not lights:
            return NO_CHASE_LIGHTS
        if len(lights) > 1 and set(lights) == set(names):
            return f"On: every light ({len(lights)})"
        chosen = set(lights)
        whole = [(name, members) for name, members in self.groups().values() if members and set(members) <= chosen]
        named: dict[str, str] = {}  # light -> the group it's named by
        for name, members in whole:
            if not any(light in named for light in members):
                named.update((light, name) for light in members)
        words: list[str] = []
        for light in lights:
            word = named.get(light, names[light])
            if word not in words:
                words.append(word)
        return "On: " + ", ".join(words)

    def _light_names(self) -> dict[str, str]:
        return {fx.id: fx.name for fx in self.show_data.colour_fixtures()}

    def _show_chase_lights(self, view: ChaseView) -> None:
        lights = view.lights if view.lights is not None else view.chase.lights
        text = self.on_text(lights)
        if text != self.chase_lights.text():
            self.chase_lights.setText(text)
            names = self._light_names()
            names = [names[light] for light in lights if light in names]
            self.chase_lights.setToolTip("The lights the chase runs on" + (": " + ", ".join(names) if names else ""))

    def _fill_chase(self, view: ChaseView) -> None:
        chase = view.chase
        self._show_chase_lights(view)
        for card in self.step_cards:
            card.hide()
            card.deleteLater()
        self.step_cards = []
        while self.steps_row.count():
            self.steps_row.takeAt(0)
        for number, step in enumerate(chase.steps, start=1):
            colours = [hex_of(step.looks.get(light, (0.0, 0.0, 0.0, 0.0))) for light in chase.lights]
            card = StepCard(number, colours)
            card.clicked.connect(lambda _checked=False, index=number - 1: self.choose_step(index))
            card.current = view.playing and view.step == number - 1
            card.setChecked(number - 1 == self._chosen_step)
            self.step_cards.append(card)
            self.steps_row.addWidget(card)
        self.steps_row.addStretch(1)
        self.speed_unit.setCurrentIndex(1 if chase.seconds is not None else 0)
        self._speed_range()
        self.speed.setValue(chase.seconds if chase.seconds is not None else chase.bpm)
        self.crossfade.set_level(chase.crossfade)
        self.crossfade_value.setText(f"{round(chase.crossfade * 100)}%")
        self.direction.setCurrentIndex(max(0, self.direction.findData(chase.direction)))
        self.repeat.setCurrentIndex(0 if chase.loop else 1)
        self.on_beat.setChecked(chase.on_beat)
        blocked = self.pause.blockSignals(True)
        self.pause.setChecked(view.paused)
        self.pause.blockSignals(blocked)

    def choose_step(self, index: int) -> None:
        self._chosen_step = index
        for number, card in enumerate(self.step_cards):
            card.setChecked(number == index)

    # ------------------------------------------------------------ making the chase

    def chase(self) -> Chase:
        return self._chase

    def _edit_chase(self, chase: Chase, chosen: int) -> None:
        self._chase = chase
        self._chosen_step = chosen
        playing = self._view.chase
        self._show_chase(ChaseView(chase, playing=playing is not None, paused=bool(playing and playing.paused),
                                   step=playing.step if playing is not None else 0))
        self.chase_edited.emit(chase)

    def add_chase_step(self) -> None:
        """Add step: the chosen lights as they look now, after the chosen step (or at the end)."""
        lights = self._lights_or_say()
        if not lights:
            return
        profiles = {fx.id: fx.profile for fx in self.show_data.colour_fixtures()}
        step = step_from(lights, self._view.values, profiles, self.master)
        if not step.looks:
            return
        chase = self._chase
        steps = list(chase.steps)
        at = self._chosen_step + 1 if 0 <= self._chosen_step < len(steps) else len(steps)
        steps.insert(at, step)
        lights_now = list(chase.lights) + [light for light in lights if light not in chase.lights]
        self._edit_chase(replace(chase, lights=tuple(lights_now), steps=tuple(steps)), at)

    def build_chase(self, kind: str) -> None:
        """A quick chase on the chosen lights (it replaces the steps there were)."""
        lights = self._lights_or_say()
        if not lights:
            return
        level = self.brightness.level()
        colour = _rgb(self._colour)
        if kind == "across":
            colours = [_rgb(c) for c in self._saved_colours] or [hue_colour(i / 4) for i in range(4)]
            steps = colours_across(lights, colours, level)
        elif kind == "running":
            steps = one_at_a_time(lights, colour, level)
        else:
            steps = odd_even(lights, colour, level)
        self._edit_chase(replace(self._chase, lights=tuple(lights), steps=steps), -1)

    def move_step(self, delta: int) -> None:
        steps = list(self._chase.steps)
        index, other = self._chosen_step, self._chosen_step + delta
        if not (0 <= index < len(steps) and 0 <= other < len(steps)):
            return
        steps[index], steps[other] = steps[other], steps[index]
        self._edit_chase(replace(self._chase, steps=tuple(steps)), other)

    def copy_chosen_step(self) -> None:
        steps = list(self._chase.steps)
        if not 0 <= self._chosen_step < len(steps):
            return
        steps.insert(self._chosen_step + 1, steps[self._chosen_step])
        self._edit_chase(replace(self._chase, steps=tuple(steps)), self._chosen_step + 1)

    def remove_chosen_step(self) -> None:
        steps = list(self._chase.steps)
        if not 0 <= self._chosen_step < len(steps):
            return
        del steps[self._chosen_step]
        self._edit_chase(replace(self._chase, steps=tuple(steps)), min(self._chosen_step, len(steps) - 1))

    def tap_tempo(self) -> None:
        """Tap: the speed the last few taps make, in BPM (playing or not: a chase that's playing takes it at once)."""
        import time

        from ..console import tap_tempo

        self._taps, bpm = tap_tempo(self._taps, time.monotonic())
        if bpm is None:
            return
        if self.speed_unit.currentData() != "bpm":
            blocked = self.speed_unit.blockSignals(True)
            self.speed_unit.setCurrentIndex(self.speed_unit.findData("bpm"))
            self.speed_unit.blockSignals(blocked)
            self._speed_range()
        self.speed.setValue(round(bpm))  # (its change sends the chase on, if it's playing)

    def _speed_range(self) -> None:
        if self.speed_unit.currentData() == "seconds":
            self.speed.setRange(0.05, 60.0)
            self.speed.setDecimals(2)
            self.speed.setSingleStep(0.05)
        else:
            self.speed.setRange(10.0, 300.0)
            self.speed.setDecimals(0)
            self.speed.setSingleStep(1.0)

    def _unit_changed(self) -> None:
        """BPM or seconds a step: the same speed either way."""
        if self._showing:
            return
        seconds = self.speed_unit.currentData() == "seconds"
        value = self._chase.step_seconds() if seconds else 60.0 / self._chase.step_seconds()
        blocked = self.speed.blockSignals(True)
        self._speed_range()
        self.speed.setValue(value)
        self.speed.blockSignals(blocked)
        self._timing_changed()

    def _timing_changed(self) -> None:
        if self._showing:
            return
        seconds = self.speed_unit.currentData() == "seconds"
        self._chase = replace(self._chase, bpm=self._chase.bpm if seconds else self.speed.value(),
                              seconds=self.speed.value() if seconds else None, crossfade=self.crossfade.level(),
                              direction=self.direction.currentData(), loop=bool(self.repeat.currentData()),
                              on_beat=self.on_beat.isChecked())
        self.chase_edited.emit(self._chase)

    def set_advanced(self, on: bool) -> None:
        if self.advanced.isChecked() != on:
            self.advanced.setChecked(on)
        self.channels_panel.setVisible(on)

    def set_locked(self, locked: bool) -> None:
        """The padlock: nothing on the page can be changed."""
        self._locked = locked
        self.setEnabled(not locked)

    # ------------------------------------------------------------ inside

    def _map_chose(self, ids: list[str]) -> None:
        ordered = self._ordered(ids)
        if ordered != ids:
            self.map.set_selection(ordered)
            ids = ordered
        self._summarise()
        self._fill_channels()
        self._follow_look()
        self.selection_changed.emit(ids)

    def _follow_look(self) -> None:
        """The Look's controls show the first chosen light: what the Console holds, else what it shows now."""
        chosen = self.map.selection()
        if not chosen:
            return
        light = chosen[0]
        held = self._view.levels.get(light, {})
        sent = self._view.values.get(light, ())
        names = [attr for attr, _name in channels_of("slimpar9")]

        def value(attr: str, usual: int) -> int:
            if attr in held:
                return int(held[attr])
            return int(sent[names.index(attr)]) if len(sent) == len(names) else usual

        self.set_look(value("dimmer", 255) / 255, hex_of(tuple(value(attr, 255) / 255 for attr in COLOUR)))

    # ------------------------------------------------------------ the Look's actions (each for the chosen lights)

    def _lights_or_say(self) -> list[str]:
        lights = self.map.selection()
        if not lights:
            self.summary.setText("Choose lights first: click them on the map, or a button above it")
        return lights

    def use_colour(self, colour: str) -> None:
        """A saved colour (or one picked): the chosen lights take it, over the Fade time."""
        lights = self._lights_or_say()
        if not lights:
            return
        self.set_look(self.brightness.level(), colour)
        self.colour_set.emit(lights, _rgb(colour), self.fade.value())

    def pick_colour(self) -> None:
        """The colour picker: with Preview the lights follow as you pick (at once); OK keeps it, Cancel puts back what
        they had (the Console's colour, or the inputs')."""
        lights = self._lights_or_say()
        if not lights:
            return
        from PySide6.QtWidgets import QDialog

        from .colour_picker import LiveColourDialog, preview_choice

        held = self._view.levels
        restore = {light: {attr: held[light][attr] for attr in COLOUR}
                   for light in lights if light in held and all(attr in held[light] for attr in COLOUR)}
        let_go = [light for light in lights if light not in restore]
        dialog = LiveColourDialog(self._colour, preview_choice.wanted(), live=True, parent=self)
        dialog.changed.connect(lambda colour: self.colour_set.emit(lights, _rgb(colour), 0.0))
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        preview_choice.remember(dialog.preview.isChecked())
        if accepted:
            colour = dialog.colour()
            self.set_look(self.brightness.level(), colour)
            if not (dialog.preview.isChecked() and dialog.tried() and colour == dialog._shown):
                self.colour_set.emit(lights, _rgb(colour), self.fade.value())
        elif dialog.tried():
            self.look_restore.emit(restore, let_go)
        dialog.deleteLater()

    def apply_fan(self) -> None:
        """The fan chosen, across the chosen lights in their order (over the Fade time)."""
        kind = self.fan.currentData()
        lights = self.map.selection()
        if not kind or not lights:
            return
        how = {"kind": kind, "start": _rgb(self.fan_from.colour()), "end": _rgb(self.fan_to.colour()),
               "low": self.fan_low.value() / 100.0, "high": self.fan_high.value() / 100.0}
        self.fan_set.emit(lights, how, self.fade.value())

    def _pick_fan_end(self, button: ColourButton) -> None:
        from PySide6.QtWidgets import QColorDialog

        colour = QColorDialog.getColor(QColor(button.colour()), self, "Fan colour")
        if colour.isValid():
            button.set_colour(colour.name().upper())
            self.apply_fan()

    def _reorder(self) -> None:
        if self.order.currentData() != "clicked":
            self._apply_choice(self.map.selection())

    def _summarise(self) -> None:
        chosen = len(self.map.selection())
        held = self._view.held_count
        parts = [f"{chosen} light{'s' if chosen != 1 else ''} chosen" if chosen else "No lights chosen"]
        if held:
            parts.append(f"{held} held by the Console")
        self.summary.setText(" · ".join(parts))
        self.release_button.setEnabled(chosen > 0 and any(i in self._view.held for i in self.map.selection()))
        self.release_all_button.setEnabled(held > 0)

    def _fill_channels(self) -> None:
        chosen = [i for i in self.map.selection() if i in self._view.values]
        for index, (attr, (caption, fader, value)) in enumerate(self.channel_rows.items()):
            levels = {self._view.values[i][index] for i in chosen if index < len(self._view.values[i])}
            if not levels:
                fader.set_level(0.0)
                value.setText("–")
                fader.set_dim(True)
            elif len(levels) == 1:
                level = next(iter(levels))
                fader.set_level(level / 255)
                value.setText(str(level))
                fader.set_dim(False)
            else:
                fader.set_level(sum(levels) / len(levels) / 255)
                value.setText("mixed")
                fader.set_dim(True)

    def _brightness_moved(self, level: float) -> None:
        self.brightness_value.setText(f"{round(level * 100)}%")
        self.brightness_set.emit(self.map.selection(), level, 0.0)  # (a fader follows the hand: no fade)

    def _channel_moved(self, attr: str, level: float) -> None:
        value = round(level * 255)
        self.channel_rows[attr][2].setText(str(value))
        self.channel_set.emit(self.map.selection(), attr, value, 0.0)

    def _fan_changed(self) -> None:
        kind = self.fan.currentData()
        colours, levels = kind == "colours", kind == "brightness"
        self.fan_extra.setVisible(colours or levels)
        for widget in (self.fan_from, self.fan_to):
            widget.setVisible(colours)
        for widget in (self.fan_low, self.fan_high):
            widget.setVisible(levels)

    def _advanced_switched(self, on: bool) -> None:
        self.channels_panel.setVisible(on)
        self.advanced_switched.emit(on)


def _rgb(colour: str) -> tuple[float, float, float]:
    value = QColor(colour)
    return (value.redF(), value.greenF(), value.blueF())


def rainbow(count: int) -> list[str]:
    """count colours round the colour wheel (a Fan: Rainbow)."""
    return [hex_of(colorsys.hsv_to_rgb(i / max(1, count), 1.0, 1.0)) for i in range(count)]


__all__ = ["COLOUR", "ConsoleMap", "ConsolePage", "rainbow"]
