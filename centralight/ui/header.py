"""The header across the top of every page.

Left to right: the logo and wordmark, the page tabs, the show picker ("Show: Morning ▾"),
status chips, Lock, Settings, the
Festoon / Key lights / Master faders, and Cut to black / Fade to black. When the window is too narrow for
one line, the faders move to a slim second row; narrower still, the chips shrink to
coloured dots (hover for the details).

The faders show what the engine is actually doing (including fades started by an input
or the Stream Deck), and only send changes when someone moves them.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
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
    STATE_ERROR,
    STATE_OFF,
    STATE_OK,
    STATE_WARN,
    ElidedLabel,
    Fader,
    HoldButton,
    StatusChip,
    ToggleSwitch,
    plain_tip,
    set_property,
    discard,
)

PAGES = (("dashboard", "Dashboard"), ("console", "Console"), ("editor", "Editor"), ("layout", "Layout"),
         ("automation", "Automation"))
BLACK_TIP = "Click: colour lights. Hold: everything, including Festoon and Key lights (Shift-click too)."
MASTER_TIP = "Master: brightness of the colour lights (not the festoon or key lights)"
NO_SHOW_TIP = "Load a show first"  # (M14: no show loaded, so the controls that need one are greyed out)
SHOW_NAME_MAX_PX = 190  # longer show names are shortened with "…" on the picker
SHOW_NAME_MIN_PX = 60  # ...and more, down to this, when the header is short of room


class ShowPicker(QToolButton):
    """"Show: Morning ▾", after the tabs: load another show, or make, copy, rename or delete one. Changes save by
    themselves, so there's no Save. The list of shows is read when the menu opens (provider: [ShowInfo])."""

    load_requested = Signal(str)  # a show's folder
    new_requested = Signal()
    duplicate_requested = Signal()
    rename_requested = Signal()
    delete_requested = Signal(str)  # a show's folder (never the loaded one)
    notes_requested = Signal()  # Notes… (the loaded show's)
    variables_requested = Signal()  # Show variables… (the Editor's Show variables, 8 Nov)
    export_requested = Signal(bool)  # Export… (False) or Export everything… (True)
    import_requested = Signal()  # Import… (a .clshow file)
    close_requested = Signal()  # None (lights off): no show from now on (M14)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ShowPicker")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Show")
        self.picker_menu = QMenu(self)
        self.picker_menu.aboutToShow.connect(self.fill_menu)
        self.setMenu(self.picker_menu)
        self._provider: Callable[[], list] = lambda: []
        self._name = ""
        self._folder = ""
        self.notes = ""  # the loaded show's notes (newer than its file while a change waits to be saved)
        self.no_show = False  # no show loaded (M14): "Show: None"
        self._limit = SHOW_NAME_MAX_PX
        self._same: Callable[[str, str], bool] = lambda a, b: a == b
        self.set_show("", "")

    def set_provider(self, provider: Callable[[], list], same_folder: Callable[[str, str], bool] | None = None) -> None:
        self._provider = provider
        if same_folder is not None:
            self._same = same_folder

    def set_show(self, name: str, folder: str) -> None:
        self._name, self._folder = name, folder
        self._update_text()

    def set_text_limit(self, pixels: int) -> None:
        """How wide the name may be (shortened with "…" beyond it): less when the header is short of room."""
        pixels = max(SHOW_NAME_MIN_PX, min(SHOW_NAME_MAX_PX, int(pixels)))
        if pixels != self._limit:
            self._limit = pixels
            self._update_text()

    def _update_text(self) -> None:
        full = f"Show: {self._name or '—'}"
        metrics = self.fontMetrics()
        if metrics.horizontalAdvance(full) <= self._limit:
            text = full
        else:  # short of room: the name alone, shortened ("Show:" goes first)
            text = metrics.elidedText(self._name or "—", Qt.TextElideMode.ElideRight, self._limit)
        if self.text() != f"{text}  ▾":
            self.setText(f"{text}  ▾")
        if self.no_show:
            self.setToolTip(f"{full}\nNo show is loaded, so the lights are off: load one from this menu.")
        else:
            self.setToolTip(f"{full}\nLoad another show, or make, copy, rename or delete one. Changes save by themselves.")

    def fill_menu(self) -> None:
        """The shows (the loaded one ticked), then New / Duplicate / Rename / Delete."""
        menu = self.picker_menu
        menu.clear()
        menu.setToolTipsVisible(True)  # (the shows' notes, in full)
        try:
            shows = list(self._provider())
        except Exception:
            shows = []
        title = menu.addAction("Load a show")
        title.setEnabled(False)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for info in shows:
            loaded = not self.no_show and self._same(info.folder, self._folder)
            action = menu.addAction(info.name)
            action.setCheckable(True)
            action.setChecked(loaded)
            notes = self.notes if loaded else getattr(info, "notes", "")
            action.setToolTip(plain_tip(f"{info.folder}\n\n{notes}" if notes else info.folder))
            action.triggered.connect(lambda _checked=False, folder=info.folder: self.load_requested.emit(folder))
            group.addAction(action)
            if notes:  # under its name: the notes' first line (all of them in the tooltip)
                first = notes.splitlines()[0].replace("\t", " ") if notes.splitlines() else ""
                line = menu.fontMetrics().elidedText(first, Qt.TextElideMode.ElideRight, 320)
                more = " …" if len(notes.splitlines()) > 1 and not line.endswith("…") else ""
                note = menu.addAction(f"      {line}{more}".replace("&", "&&"))  # (a lone "&" would underline a letter)
                note.setEnabled(False)
                note.setToolTip(plain_tip(notes))
        if not shows:
            none = menu.addAction("(no shows found)")
            none.setEnabled(False)
        # no show at all (M14): the lights fade off, then the show closes, as when a schedule ends with the default None
        off = menu.addAction("None (lights off)")
        off.setCheckable(True)
        off.setChecked(self.no_show)
        off.setToolTip("No show: the lights fade off (Fade to black's time, the Festoon and Key lights too), then the "
                       "show closes. The global automations carry on.")
        off.triggered.connect(lambda _checked=False: None if self.no_show else self.close_requested.emit())
        group.addAction(off)
        menu.addSeparator()
        menu.addAction("New show…", self.new_requested.emit)
        if not self.no_show:
            menu.addAction(f"Duplicate “{self._name}”…", self.duplicate_requested.emit)
            menu.addAction(f"Rename “{self._name}”…", self.rename_requested.emit)
            menu.addAction(f"Notes about “{self._name}”…", self.notes_requested.emit)
        variables = menu.addAction("Show variables…", self.variables_requested.emit)
        variables.setToolTip("Add or change this show's variables (the Editor's Show variables tab)")
        variables.setEnabled(not self.no_show)  # (no show loaded: no variables)
        menu.addSeparator()
        if not self.no_show:
            menu.addAction(f"Export “{self._name}”…", lambda: self.export_requested.emit(False))
        menu.addAction("Export everything…", lambda: self.export_requested.emit(True))
        menu.addAction("Import…", self.import_requested.emit)
        menu.addSeparator()
        delete = menu.addMenu("Delete a show")
        others = [info for info in shows if self.no_show or not self._same(info.folder, self._folder)]
        for info in others:
            delete.addAction(f"{info.name}…", lambda folder=info.folder: self.delete_requested.emit(folder))
        if not others:
            nothing = delete.addAction("(only the loaded show: it can't be deleted)")
            nothing.setEnabled(False)


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
    black_requested = Signal(bool, bool)  # (fade: False = cut, everything: the Festoon and Key lights too)
    lock_toggled = Signal(bool)

    def __init__(self, show_name: str, house: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Header")
        self._one_row: bool | None = None
        self._compact_chips: bool | None = None
        self._arranging = False

        self.brand = BrandBlock()
        self.brand.setToolTip(f"Show: {show_name}")

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
        self.show_picker = ShowPicker()
        self.show_picker.set_show(show_name, "")

        self.chips: dict[str, StatusChip] = {}
        self.chip_box = QWidget()
        chip_layout = QHBoxLayout(self.chip_box)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(6)
        for name in ("engine", "files", "console", "output", "automations", "ndi", "audio", "remote"):
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
        self._house_labels: dict[str, str] = {}  # each house light's name (for its tooltip)
        self._locked = False
        self._no_show = False  # (M14: see set_no_show)
        for fixture_id, label in house:
            levels_layout.addWidget(self._make_house_control(fixture_id, label))
        self.master = LevelControl("Master", with_switch=False)
        self.master.setToolTip(MASTER_TIP)
        self.master.level_moved.connect(self.master_changed.emit)
        levels_layout.addWidget(self.master)

        # Cut to black / Fade to black: one-off buttons (no "on" state). Click: the colour lights; hold: everything
        self.cut_black = HoldButton("Cut to black", "Cut everything", theme.DANGER)
        self.fade_black = HoldButton("Fade to black", "Fade everything", theme.DANGER)
        self.black = QWidget()
        black_layout = QHBoxLayout(self.black)
        black_layout.setContentsMargins(0, 0, 0, 0)
        black_layout.setSpacing(6)
        for button, fade in ((self.cut_black, False), (self.fade_black, True)):
            button.setObjectName("ToBlack")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(BLACK_TIP)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.tapped.connect(lambda f=fade: self.black_requested.emit(f, False))
            button.held.connect(lambda f=fade: self.black_requested.emit(f, True))
            black_layout.addWidget(button)
        self.black.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.row1 = QHBoxLayout()
        self.row1.setContentsMargins(14, 8, 14, 8)
        self.row1.setSpacing(14)
        self.row1.addWidget(self.brand)
        self.row1.addWidget(self.tab_box)
        self.row1.addWidget(self.show_picker)
        self.row1.addStretch(1)
        self.row1.addWidget(self.chip_box)
        self.row1.addWidget(self.lock_button)
        self.row1.addWidget(self.settings_button)
        self.row1.addWidget(self.black)

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
        self._update_black_size()
        self._arrange(force=True)

    # ------------------------------------------------------------ updates from the engine

    def set_frame(self, frame: EngineFrame) -> None:
        for fixture_id, control in self.house_controls.items():
            view: HouseView | None = frame.house.get(fixture_id)
            if view is not None:
                control.set_view(view.level, view.on)
        self.master.set_view(frame.master)
        self.set_output_status(frame.output)

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

    def set_show_name(self, name: str, folder: str | None = None) -> None:
        """The loaded show, on the picker (and the logo's tooltip)."""
        self.brand.setToolTip(f"Show: {name}")
        if name != self.show_picker._name or (folder is not None and folder != self.show_picker._folder):
            self.show_picker.set_show(name, self.show_picker._folder if folder is None else folder)
            self._arrange(force=True)

    def set_house(self, house: list[tuple[str, str]]) -> None:
        """Update the house-light controls after the show changed (renamed, added or removed)."""
        if [fid for fid, _label in house] == list(self.house_controls):
            for fixture_id, label in house:
                control = self.house_controls[fixture_id]
                control.caption.setText(label.upper())
                control.fader.setAccessibleName(f"{label} level")
                self._house_labels[fixture_id] = label
            self._update_enabled()  # (the tooltips say the new names)
            return
        levels_layout = self.levels.layout()
        for control in self.house_controls.values():
            levels_layout.removeWidget(control)
            discard(control)
        self.house_controls.clear()
        self._house_labels.clear()
        for index, (fixture_id, label) in enumerate(house):
            control = self._make_house_control(fixture_id, label)
            levels_layout.insertWidget(index, control)
        self._update_enabled()
        self._arrange(force=True)

    def _make_house_control(self, fixture_id: str, label: str) -> LevelControl:
        control = LevelControl(label, with_switch=True)
        self._house_labels[fixture_id] = label
        control.level_moved.connect(lambda level, f=fixture_id: self.house_changed.emit(f, level, True))
        control.switched.connect(lambda on, f=fixture_id: self.house_changed.emit(f, None, on))
        control.setToolTip(f"{label}: drag to set the level (moving it turns the light on)")
        control.setEnabled(not self.lock_button.isChecked())
        self.house_controls[fixture_id] = control
        return control

    def set_locked(self, locked: bool) -> None:
        if self.lock_button.isChecked() != locked:
            self.lock_button.setChecked(locked)
        self._locked = locked
        self._update_enabled()

    def set_no_show(self, no_show: bool) -> None:
        """No show loaded (M14): the house lights, Master and Cut / Fade to black are greyed out ("Load a show first";
        the last show's house lights stay, at their levels); the picker reads "Show: None". Lock, Settings and the
        status chips work as usual."""
        self._no_show = no_show
        if self.show_picker.no_show != no_show:
            self.show_picker.no_show = no_show
            self.show_picker._update_text()
        self._update_enabled()

    @property
    def no_show(self) -> bool:
        return self._no_show

    def _update_enabled(self) -> None:
        """The level controls and Cut / Fade to black: off while locked, or with no show loaded (saying why)."""
        usable = not self._locked and not self._no_show
        tips: list[tuple[QWidget, str]] = [(self.master, MASTER_TIP), (self.cut_black, BLACK_TIP), (self.fade_black, BLACK_TIP)]
        for fixture_id, control in self.house_controls.items():
            label = self._house_labels.get(fixture_id, fixture_id)
            tips.append((control, f"{label}: drag to set the level (moving it turns the light on)"))
            if control.switch is not None:
                tips.append((control.switch, f"{label} on / off (fades over half a second)"))
        for widget, tip in tips:
            widget.setEnabled(usable)
            widget.setToolTip(NO_SHOW_TIP if self._no_show else tip)

    # ------------------------------------------------------------ layout

    def _update_black_size(self) -> None:
        """Wide enough for 'Fade everything', so nothing jumps while a button is held."""
        for button in (self.cut_black, self.fade_black):
            button.setMinimumWidth(button.widest_text() + 2 * 12 + 8)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (event.Type.FontChange, event.Type.StyleChange):
            self._update_black_size()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._arrange()

    def _visible_chips(self) -> list[StatusChip]:
        return [chip for chip in self.chips.values() if chip.isVisibleTo(self)]

    def _brand_width(self, wordmark: bool) -> int:
        """The logo with (or without) the wordmark, whether the wordmark shows now or not."""
        now = self.brand.sizeHint().width()
        words = self.brand.wordmark.sizeHint().width() + self.brand.layout().spacing()
        showing = not self.brand.wordmark.isHidden()
        if wordmark:
            return now if showing else now + words
        return now - words if showing else now

    def _row1_width(self, with_levels: bool, compact_chips: bool, wordmark: bool = True) -> int:
        """Width row 1 needs, worked out from the widgets' own size hints."""
        margins = self.row1.contentsMargins()
        spacing = self.row1.spacing()
        chips = self._visible_chips()
        if compact_chips:
            chip_width = sum(chip.compact_width() for chip in chips)
        else:
            chip_width = sum(chip.full_width() for chip in chips)
        chip_width += max(0, len(chips) - 1) * self.chip_box.layout().spacing()
        brand = self._brand_width(wordmark)
        parts = [
            brand,
            self.tab_box.sizeHint().width(),
            self.show_picker.sizeHint().width(),
            chip_width,
            self.lock_button.sizeHint().width(),
            self.settings_button.sizeHint().width(),
            max(self.black.sizeHint().width(), self.black.minimumSizeHint().width()),
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
            self.show_picker.set_text_limit(SHOW_NAME_MAX_PX)
            one_row = self._row1_width(True, False) <= width
            compact = (not one_row) and self._row1_width(False, False) > width
            wordmark = True
            if compact:  # still short of room with the chips as dots: the show's name shortens to fit
                over = self._row1_width(False, True) - width
                if over > 0:
                    self.show_picker.set_text_limit(SHOW_NAME_MAX_PX - over - 8)  # (… cuts whole letters: some to spare)
                if self._row1_width(False, True) > width:
                    # and still (five tabs on a small screen, M15): the wordmark goes, the logo stays, and the
                    # show's name gets back what that frees (worked out by sums: hiding and showing it to measure,
                    # every frame, made the header lay itself out over and over)
                    wordmark = False
                    self.show_picker.set_text_limit(SHOW_NAME_MAX_PX)
                    over = self._row1_width(False, True, wordmark=False) - width
                    if over > 0:
                        self.show_picker.set_text_limit(SHOW_NAME_MAX_PX - over - 8)
            self._show_wordmark(wordmark)
            if force or one_row != self._one_row:
                self._place_levels(one_row)
            if force or compact != self._compact_chips:
                for chip in self.chips.values():
                    chip.set_compact(compact)
            self._one_row = one_row
            self._compact_chips = compact
        finally:
            self._arranging = False

    def _show_wordmark(self, shown: bool) -> None:
        wordmark = self.brand.wordmark
        if wordmark.isHidden() == shown:
            wordmark.setVisible(shown)
            self.brand.layout().invalidate()
            self.brand.updateGeometry()

    def _place_levels(self, one_row: bool) -> None:
        self.row1.removeWidget(self.levels)
        self.row2_layout.removeWidget(self.levels)
        if one_row:
            self.row1.insertWidget(self.row1.indexOf(self.black), self.levels)
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
