"""The header across the top of every page.

Left to right: the logo and wordmark, the page tabs, status chips, Lock, Settings, the
Festoon / Key lights / Master faders, and Blackout. When the window is too narrow for
one line, the faders move to a slim second row; narrower still, the chips shrink to
coloured dots (hover for the details).

The faders show what the engine is actually doing (including fades started by an input
or the Stream Deck), and only send changes when someone moves them.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..engine import EngineFrame, HouseView
from ..output import BUSY, CLOSED, CONNECTED, CONNECTING, MISSING, OutputStatus
from ..settings import OUTPUT_ARTNET, OUTPUT_SIMULATED
from . import icons, theme
from .widgets import (
    DOUBLE_CLICK_GUARD_S,
    STATE_ERROR,
    STATE_OFF,
    STATE_OK,
    STATE_WARN,
    ElidedLabel,
    Fader,
    StatusChip,
    ToggleSwitch,
    set_property,
    discard,
)

PAGES = (("dashboard", "Dashboard"), ("editor", "Editor"), ("layout", "Layout"), ("automation", "Automation"))
BLACKOUT_TEXT = "Blackout"
BLACKOUT_ON_TEXT = "Lights off — click to restore"


class PercentLabel(QLabel):
    """'100%' with a fixed width, so the faders don't jiggle as numbers change."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("0%", parent)
        self.setObjectName("Percent")
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance("100%") + 4, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def set_level(self, level: float) -> None:
        text = f"{round(level * 100)}%"
        if self.text() != text:
            self.setText(text)


class LevelControl(QWidget):
    """A label, a fader and its % readout (plus an on/off switch for house lights)."""

    level_moved = Signal(float)
    switched = Signal(bool)

    def __init__(self, label: str, with_switch: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LevelControl")
        self.caption = ElidedLabel(label.upper())
        self.caption.setObjectName("ControlLabel")
        self.caption.setMaximumWidth(150)
        self.fader = Fader()
        self.fader.setMaximumWidth(240)
        self.fader.setAccessibleName(f"{label} level")
        self.percent = PercentLabel()
        self.switch = ToggleSwitch() if with_switch else None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.caption)
        layout.addWidget(self.fader, 1)
        layout.addWidget(self.percent)
        if self.switch is not None:
            self.switch.setAccessibleName(f"{label} on/off")
            self.switch.setToolTip(f"{label} on / off (fades over half a second)")
            layout.addWidget(self.switch)
            # clicked (not toggled): engine updates set the switch too, and mustn't echo back
            self.switch.clicked.connect(lambda *_: self.switched.emit(self.switch.isChecked()))
        self.fader.moved.connect(self._moved)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _moved(self, level: float) -> None:
        self.percent.set_level(level)
        self.level_moved.emit(level)

    def set_view(self, level: float, on: bool | None = None) -> None:
        if not self.fader.is_dragging():
            self.fader.set_level(level)
            self.percent.set_level(level)
        if self.switch is not None and on is not None:
            if self.switch.isChecked() != on:
                self.switch.setChecked(on)
            self.fader.set_dim(not on)
            set_property(self.percent, "dim", not on)


class BrandBlock(QWidget):
    """The logo and the CentraLight wordmark. The show's name is in the tooltip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.logo = QLabel()
        self.logo.setObjectName("Logo")
        self.wordmark = QLabel("CentraLight")
        self.wordmark.setObjectName("Wordmark")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.logo)
        layout.addWidget(self.wordmark)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._logo_side = 0

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._update_logo()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_logo()

    def _update_logo(self) -> None:
        side = max(20, self.wordmark.sizeHint().height() + 4)
        dpr = self.devicePixelRatioF()
        key = (side, dpr)
        if key != self._logo_side:
            self._logo_side = key
            self.logo.setPixmap(icons.logo_pixmap(side, dpr))
            self.logo.setFixedSize(side, side)


class Header(QFrame):
    page_requested = Signal(str)
    settings_requested = Signal()
    house_changed = Signal(str, object, object)  # fixture id, level (or None), on (or None)
    master_changed = Signal(float)
    blackout_clicked = Signal(bool)  # True = instant (Shift-click)
    lock_toggled = Signal(bool)

    def __init__(self, show_name: str, house: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Header")
        self._one_row: bool | None = None
        self._compact_chips: bool | None = None
        self._arranging = False

        self.brand = BrandBlock()
        self.set_show_name(show_name)

        self.tabs: dict[str, QPushButton] = {}
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self.tab_box = QWidget()
        tab_layout = QHBoxLayout(self.tab_box)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(4)
        for key, label in PAGES:
            button = QPushButton(label)
            button.setObjectName("Tab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, k=key: self.page_requested.emit(k))
            self._tab_group.addButton(button)
            tab_layout.addWidget(button)
            self.tabs[key] = button

        self.chips: dict[str, StatusChip] = {}
        self.chip_box = QWidget()
        chip_layout = QHBoxLayout(self.chip_box)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(6)
        for name in ("engine", "files", "output", "automations", "ndi", "audio", "remote"):
            chip = StatusChip(name)
            chip_layout.addWidget(chip)
            chip.setVisible(name == "output")
            self.chips[name] = chip

        self.lock_button = QToolButton()
        self.lock_button.setObjectName("HeaderIcon")
        self.lock_button.setCheckable(True)
        self.lock_button.setIcon(icons.line_icon(icons.draw_lock, theme.TEXT_2, theme.ACCENT))
        self.lock_button.setToolTip("Lock: stops accidental clicks on the lights while it's on")
        self.lock_button.setAccessibleName("Lock")
        self.lock_button.toggled.connect(self.lock_toggled.emit)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("HeaderIcon")
        self.settings_button.setIcon(icons.line_icon(icons.draw_gear, theme.TEXT_2))
        self.settings_button.setToolTip("Settings")
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.clicked.connect(self.settings_requested.emit)

        self.levels = QWidget()
        self.levels.setObjectName("Levels")
        levels_layout = QHBoxLayout(self.levels)
        levels_layout.setContentsMargins(0, 0, 0, 0)
        levels_layout.setSpacing(22)
        self.house_controls: dict[str, LevelControl] = {}
        for fixture_id, label in house:
            levels_layout.addWidget(self._make_house_control(fixture_id, label))
        self.master = LevelControl("Master", with_switch=False)
        self.master.setToolTip("Master: brightness of the colour lights (not the festoon or key lights)")
        self.master.level_moved.connect(self.master_changed.emit)
        levels_layout.addWidget(self.master)

        self.blackout = QPushButton(BLACKOUT_TEXT)
        self._last_blackout_click = -1e9
        self.blackout.setObjectName("Blackout")
        self.blackout.setCursor(Qt.CursorShape.PointingHandCursor)
        self.blackout.setToolTip("Fade every light to black (Shift-click: instantly)")
        self.blackout.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.blackout.clicked.connect(self._blackout_clicked)
        self.blackout.setProperty("active", False)

        self.row1 = QHBoxLayout()
        self.row1.setContentsMargins(14, 8, 14, 8)
        self.row1.setSpacing(14)
        self.row1.addWidget(self.brand)
        self.row1.addWidget(self.tab_box)
        self.row1.addStretch(1)
        self.row1.addWidget(self.chip_box)
        self.row1.addWidget(self.lock_button)
        self.row1.addWidget(self.settings_button)
        self.row1.addWidget(self.blackout)

        self.row2 = QWidget()
        self.row2.setObjectName("HeaderRow2")
        self.row2_layout = QHBoxLayout(self.row2)
        self.row2_layout.setContentsMargins(14, 6, 14, 6)
        self.row2_layout.setSpacing(14)
        self.row2_layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(self.row1)
        outer.addWidget(self.row2)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._update_blackout_size()
        self._arrange(force=True)

    # ------------------------------------------------------------ updates from the engine

    def set_frame(self, frame: EngineFrame) -> None:
        for fixture_id, control in self.house_controls.items():
            view: HouseView | None = frame.house.get(fixture_id)
            if view is not None:
                control.set_view(view.level, view.on)
        self.master.set_view(frame.master)
        self.set_blackout(frame.blackout)
        self.set_output_status(frame.output)

    def set_blackout(self, on: bool) -> None:
        text = BLACKOUT_ON_TEXT if on else BLACKOUT_TEXT
        if self.blackout.text() != text:
            self.blackout.setText(text)
            self.blackout.setToolTip(
                "The lights are off. Click to bring them back (Shift-click: instantly)"
                if on
                else "Fade every light to black (Shift-click: instantly)"
            )
        set_property(self.blackout, "active", on)

    def set_output_status(self, status: OutputStatus) -> None:
        chip = self.chips["output"]
        rate = f"{status.rate:.0f}/s"
        if status.kind == OUTPUT_SIMULATED:
            chip.set_status(
                STATE_WARN,
                f"Simulated · {rate}",
                "Simulated output: nothing is sent to the lights; the app only shows what it would send.\n"
                "Settings → Output → Eurolite (USB) drives the real lights.",
            )
            return
        name = status.label or ("Art-Net" if status.kind == OUTPUT_ARTNET else "USB")
        if status.kind == OUTPUT_ARTNET:
            name = f"Art-Net {name}"
        if status.state == CONNECTED:
            chip.set_status(STATE_OK, f"{name} · {rate}", f"Sending DMX: {status.sent} frames so far")
        elif status.state == CONNECTING:
            chip.set_status(STATE_WARN, f"{name} · connecting", "Opening the output…")
        elif status.state == BUSY:
            chip.set_status(STATE_ERROR, f"{name} busy", status.message)
        elif status.state == MISSING:
            chip.set_status(STATE_ERROR, "No dongle", status.message)
        elif status.state == CLOSED:
            chip.set_status(STATE_OFF, "Output off", "")
        else:
            chip.set_status(STATE_ERROR, f"{name} error", status.message)
        self._arrange()

    def set_chip(self, name: str, state: str | None, text: str = "", tooltip: str = "") -> None:
        """Show a chip (state from widgets.STATE_*), or hide it with state None."""
        chip = self.chips[name]
        if state is None:
            if chip.isVisible():
                chip.setVisible(False)
                self._arrange(force=True)
            return
        chip.set_status(state, text, tooltip)
        if not chip.isVisibleTo(self):
            chip.setVisible(True)
            self._arrange(force=True)

    def set_page(self, key: str) -> None:
        button = self.tabs.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def set_show_name(self, name: str) -> None:
        # not shown in the header (just the wordmark); hovering the logo names the show
        self.brand.setToolTip(f"Show: {name}")

    def set_house(self, house: list[tuple[str, str]]) -> None:
        """Update the house-light controls after the show changed (renamed, added or removed)."""
        if [fid for fid, _label in house] == list(self.house_controls):
            for fixture_id, label in house:
                control = self.house_controls[fixture_id]
                control.caption.setText(label.upper())
                control.fader.setAccessibleName(f"{label} level")
            return
        levels_layout = self.levels.layout()
        for control in self.house_controls.values():
            levels_layout.removeWidget(control)
            discard(control)
        self.house_controls.clear()
        for index, (fixture_id, label) in enumerate(house):
            control = self._make_house_control(fixture_id, label)
            levels_layout.insertWidget(index, control)
        self._arrange(force=True)

    def _make_house_control(self, fixture_id: str, label: str) -> LevelControl:
        control = LevelControl(label, with_switch=True)
        control.level_moved.connect(lambda level, f=fixture_id: self.house_changed.emit(f, level, True))
        control.switched.connect(lambda on, f=fixture_id: self.house_changed.emit(f, None, on))
        control.setToolTip(f"{label}: drag to set the level (moving it turns the light on)")
        control.setEnabled(not self.lock_button.isChecked())
        self.house_controls[fixture_id] = control
        return control

    def set_locked(self, locked: bool) -> None:
        if self.lock_button.isChecked() != locked:
            self.lock_button.setChecked(locked)
        for control in [*self.house_controls.values(), self.master]:
            control.setEnabled(not locked)
        self.blackout.setEnabled(not locked)

    # ------------------------------------------------------------ layout

    def _blackout_clicked(self) -> None:
        now = time.monotonic()
        if now - self._last_blackout_click < DOUBLE_CLICK_GUARD_S:
            return  # a double-click (in a hurry) is one click, not off-and-on-again
        self._last_blackout_click = now
        instant = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.blackout_clicked.emit(instant)

    def _update_blackout_size(self) -> None:
        """Wide enough for 'Lights off — click to restore', so nothing jumps when it changes."""
        fm = self.blackout.fontMetrics()
        widest = max(fm.horizontalAdvance(BLACKOUT_ON_TEXT), fm.horizontalAdvance(BLACKOUT_TEXT))
        self.blackout.setMinimumWidth(widest + 2 * 14 + 8)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (event.Type.FontChange, event.Type.StyleChange):
            self._update_blackout_size()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._arrange()

    def _visible_chips(self) -> list[StatusChip]:
        return [chip for chip in self.chips.values() if chip.isVisibleTo(self)]

    def _row1_width(self, with_levels: bool, compact_chips: bool) -> int:
        """Width row 1 needs, worked out from the widgets' own size hints."""
        margins = self.row1.contentsMargins()
        spacing = self.row1.spacing()
        chips = self._visible_chips()
        if compact_chips:
            chip_width = sum(chip.compact_width() for chip in chips)
        else:
            chip_width = sum(chip.full_width() for chip in chips)
        chip_width += max(0, len(chips) - 1) * self.chip_box.layout().spacing()
        brand = self.brand.sizeHint().width()
        parts = [
            brand,
            self.tab_box.sizeHint().width(),
            chip_width,
            self.lock_button.sizeHint().width(),
            self.settings_button.sizeHint().width(),
            max(self.blackout.sizeHint().width(), self.blackout.minimumWidth()),
        ]
        if with_levels:
            parts.append(self.levels.sizeHint().width())
        return margins.left() + margins.right() + sum(parts) + spacing * len(parts) + 12

    def _arrange(self, force: bool = False) -> None:
        if self._arranging:
            return
        self._arranging = True
        try:
            width = self.width()
            one_row = self._row1_width(True, False) <= width
            compact = (not one_row) and self._row1_width(False, False) > width
            if force or one_row != self._one_row:
                self._place_levels(one_row)
            if force or compact != self._compact_chips:
                for chip in self.chips.values():
                    chip.set_compact(compact)
            self._one_row = one_row
            self._compact_chips = compact
        finally:
            self._arranging = False

    def _place_levels(self, one_row: bool) -> None:
        self.row1.removeWidget(self.levels)
        self.row2_layout.removeWidget(self.levels)
        if one_row:
            self.row1.insertWidget(self.row1.indexOf(self.blackout), self.levels)
            self.row2.setVisible(False)
        else:
            self.row2_layout.insertWidget(0, self.levels)
            self.row2.setVisible(True)
        self.levels.show()

    @property
    def one_row(self) -> bool:
        return bool(self._one_row)

    @property
    def compact_chips(self) -> bool:
        return bool(self._compact_chips)
