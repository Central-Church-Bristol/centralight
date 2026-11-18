"""The Light type window and the New light type window (M19, 18 Nov), opened from the Layout tab.

LightTypeDialog: pick a type for the chosen lights (or for a new light) from the types this show's lights use, yours,
CentraLight's own and the library (the Open Fixture Library), with a search; the picked type's channels show on the
right. New… and Edit a copy… open LightTypeEditor; Edit… changes one of this show's or your types in place.

LightTypeEditor: a type's maker, model and mode, and its channels (each one's job, name and "normally"). While it's
open, the lights it's for use what's typed (like an open colour picker's Preview: no undo step and nothing saved;
Cancel puts them back, Save makes it one step), so its Red, Green, Blue, White and Off buttons show straight away
whether the channels are right.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import light_types
from ..automations import MONTHS
from ..light_library import LibraryModel, library
from ..light_types import COLOUR, MAX_CHANNELS, ROLE_LABELS, ROLES, Channel, LightType, LightTypeError, check, make_type, type_name
from ..own_types import load_own, remove_own
from . import theme

RGB = tuple[float, float, float]
DATA = Qt.ItemDataRole.UserRole
SHOW, YOURS, BUILT_IN, LIBRARY = "show", "yours", "builtin", "library"
SECTION_NAMES = {SHOW: "In this show", YOURS: "Yours", BUILT_IN: "Built in", LIBRARY: "Library · Open Fixture Library"}
TRY_COLOURS = (("Red", (1.0, 0.0, 0.0)), ("Green", (0.0, 1.0, 0.0)), ("Blue", (0.0, 0.0, 1.0)),
               ("White", (1.0, 1.0, 1.0)))
JOB_TIPS = {
    "dimmer": "The light's brightness: CentraLight sets it",
    "red": "CentraLight sets it from the colour",
    "green": "CentraLight sets it from the colour",
    "blue": "CentraLight sets it from the colour",
    "white": "White LEDs: they take the white in the colour",
    "amber": "Amber LEDs: off unless the Console sets them",
    "uv": "UV LEDs: off unless the Console sets them",
    "lime": "Lime LEDs: off unless the Console sets them",
    "macro": "Ready-made colours built into the light",
    "strobe": "Flashing (some lights only shine with it at a certain value: set that as Normally)",
    "program": "The light's own programs (colour jump, fade, sound)",
    "program_speed": "How fast the light's own programs go",
    "dimmer_speed": "How fast the light's dimmer follows",
    "other": "Any other channel: it stays at its Normally",
}


def normally(kind: LightType, channel: Channel) -> str:
    """What the Normally column says: the value it's sent, or what CentraLight fills it in from."""
    if kind.automatic(channel):
        return "colour" if channel.role in (*COLOUR, "white") and kind.has_colour else "brightness"
    return str(channel.default)


def channel_table(parent: QWidget | None = None) -> QTableWidget:
    table = QTableWidget(0, 4, parent)
    table.setHorizontalHeaderLabels(["Ch", "Job", "Name", "Normally"])
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
    table.setColumnWidth(1, 150)
    table.setColumnWidth(3, 110)
    table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 12)
    return table


def fill_channels(table: QTableWidget, kind: LightType | None) -> None:
    """A type's channels, read-only: number, job, name and normally."""
    rows = kind.channels if kind is not None else ()
    table.setRowCount(len(rows))
    for row, ch in enumerate(rows):
        texts = (str(row + 1), ROLE_LABELS.get(ch.role, ch.role), ch.label, normally(kind, ch))
        for column, text in enumerate(texts):
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if column == 3 and kind.automatic(ch):
                item.setForeground(QBrush(QColor(theme.TEXT_3)))
            if column == 1:
                item.setToolTip(JOB_TIPS.get(ch.role, ""))
            table.setItem(row, column, item)


def _day(iso: str) -> str:
    """"2026-11-18" -> "18 Nov 2026" ("" if it isn't a date)."""
    try:
        day = datetime.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def count_text(count: int) -> str:
    return f"{count} channel" + ("" if count == 1 else "s")


def lights_text(count: int) -> str:
    return f"{count} light" + ("" if count == 1 else "s")


class LightTypeDialog(QDialog):
    """Light type: pick one for the chosen lights, or for a new light (adding), or look and make new ones."""

    def __init__(self, parent: QWidget | None, show, lights: Sequence[str], *, own_dir: Path | None = None,
                 current: str = "", adding: bool = False,
                 edit: Callable[[LightType | None, str], tuple[LightType | None, bool]] | None = None) -> None:
        """lights: the lights it's for ("Use for 2 lights"); adding: for Add a light instead. edit(base, how) opens
        the New light type window ("new", "copy", or "edit" in place) and returns (the type saved or None, whether the
        lights already have it)."""
        super().__init__(parent)
        self.setWindowTitle("Light type")
        self.setObjectName("LightTypeDialog")
        self.resize(900, 580)
        self._show = show
        self._lights = list(lights)
        self._own_dir = own_dir
        self._adding = adding
        self._edit = edit
        self._kind: LightType | None = None
        self._source = ""
        self._model: LibraryModel | None = None
        self._own_ids: set[str] = set()
        self.chosen: LightType | None = None
        self.applied = False  # (the New light type window already gave the lights their type)

        if adding:
            heading = "For a new light"
        elif len(self._lights) == 1 and show.fixture(self._lights[0]) is not None:
            heading = f"For {show.fixture(self._lights[0]).name}"
        elif self._lights:
            heading = f"For {lights_text(len(self._lights))}"
        else:
            heading = "No lights chosen: pick one to look at it, or make a new type"
        self.heading = QLabel(heading)
        self.heading.setObjectName("Caption")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search makers and models")
        self.search.setClearButtonEnabled(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumWidth(280)

        self.title = QLabel("")
        self.title.setObjectName("RowName")
        self.title.setWordWrap(True)
        self.mode_label = QLabel("Mode")
        self.mode = QComboBox()
        self.mode.setToolTip("The mode the light is set to on its own menu (it says how many channels it uses)")
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_label)
        mode_row.addWidget(self.mode, 1)
        self.channels = channel_table()
        self.note = QLabel("")
        self.note.setObjectName("Hint")
        self.note.setWordWrap(True)

        left = QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.tree, 1)
        right = QVBoxLayout()
        right.addWidget(self.title)
        right.addLayout(mode_row)
        right.addWidget(self.channels, 1)
        right.addWidget(self.note)
        columns = QGridLayout()
        columns.addLayout(left, 0, 0)
        columns.addLayout(right, 0, 1)
        columns.setColumnStretch(0, 2)
        columns.setColumnStretch(1, 3)

        self.new_button = QPushButton("New…")
        self.new_button.setToolTip("Make a light type from the light's manual")
        self.edit_button = QPushButton("Edit a copy…")
        self.remove_button = QPushButton("Remove")
        self.remove_button.setToolTip("Take it off your light types (lights and shows that use it keep it)")
        self.cancel_button = QPushButton("Cancel")
        self.use_button = QPushButton("Add a light" if adding else f"Use for {lights_text(len(self._lights))}")
        self.use_button.setDefault(True)
        buttons = QHBoxLayout()
        for button in (self.new_button, self.edit_button, self.remove_button, self.cancel_button):
            button.setAutoDefault(False)  # (Enter is Use)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.use_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addLayout(columns, 1)
        layout.addLayout(buttons)

        self.search.textChanged.connect(self._filter)
        self.tree.currentItemChanged.connect(self._picked)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.mode.currentIndexChanged.connect(self._mode_picked)
        self.new_button.clicked.connect(lambda: self._run_editor(None, "new"))
        self.edit_button.clicked.connect(self._edit_clicked)
        self.remove_button.clicked.connect(self._remove)
        self.cancel_button.clicked.connect(self.reject)
        self.use_button.clicked.connect(self._use)
        self.new_button.setEnabled(edit is not None)
        self._fill()
        if not self._select(current, SHOW):
            self._picked(None)

    # ------------------------------------------------------------ the list

    def _section(self, key: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.tree, [SECTION_NAMES[key]])
        item.setData(0, DATA, ("section", key))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setForeground(0, QBrush(QColor(theme.TEXT_3)))
        return item

    def _fill(self) -> None:
        self.tree.clear()
        used: list[LightType] = []
        for fx in self._show.fixtures:
            kind = light_types.get(fx.profile)
            if kind is not None and kind not in used:
                used.append(kind)
        own = load_own(self._own_dir) if self._own_dir is not None else []
        self._own_ids = {kind.id for kind in own}
        for key, kinds in ((SHOW, used), (YOURS, own), (BUILT_IN, list(light_types.BUILT_IN.values()))):
            section = self._section(key)
            for kind in kinds:
                item = QTreeWidgetItem(section, [kind.name])
                item.setData(0, DATA, (key, kind))
                item.setToolTip(0, f"{kind.name}\n{count_text(kind.channel_count)}")
            if not kinds:
                empty = QTreeWidgetItem(section, ["None yet" if key == YOURS else "None"])
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                empty.setData(0, DATA, None)
            section.setExpanded(True)
        lib = library()
        section = self._section(LIBRARY)
        if lib.problem or not lib.models:
            empty = QTreeWidgetItem(section, ["Couldn't be read (the session log says why)"])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setData(0, DATA, None)
        for maker, models in lib.by_maker.items():
            maker_item = QTreeWidgetItem(section, [maker])
            maker_item.setData(0, DATA, ("maker", maker))
            maker_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            for model in models:
                item = QTreeWidgetItem(maker_item, [model.name])
                item.setData(0, DATA, (LIBRARY, model))
                item.setToolTip(0, f"{maker} {model.name}\n" + ", ".join(k.mode or count_text(k.channel_count)
                                                                         for k in model.modes))
        section.setExpanded(True)
        self._filter(self.search.text())

    def _select(self, type_id: str, prefer: str) -> bool:
        """Pick a type in the list (in the section given, if it's there)."""
        if not type_id:
            return False
        found = None
        for i in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(i)
            for j in range(section.childCount()):
                child = section.child(j)
                data = child.data(0, DATA)
                if isinstance(data, tuple) and isinstance(data[1], LightType) and data[1].id == type_id:
                    if found is None or data[0] == prefer:
                        found = child
        if found is not None:
            self.tree.setCurrentItem(found)
            self.tree.scrollToItem(found)
        return found is not None

    def _filter(self, text: str) -> None:
        words = text.casefold().split()

        def fits(*parts: str) -> bool:
            joined = " ".join(parts).casefold()
            return all(word in joined for word in words)

        for i in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(i)
            key = section.data(0, DATA)[1]
            shown = 0
            for j in range(section.childCount()):
                child = section.child(j)
                data = child.data(0, DATA)
                if key == LIBRARY and isinstance(data, tuple):
                    maker = child.text(0)
                    maker_fits = fits(maker)
                    count = 0
                    for k in range(child.childCount()):
                        model = child.child(k)
                        ok = not words or maker_fits or fits(maker, model.text(0))
                        model.setHidden(not ok)
                        count += ok
                    child.setHidden(count == 0)
                    child.setExpanded(bool(words) and 0 < count <= 60)
                    shown += count
                else:
                    ok = (not words) if data is None else (not words or fits(child.text(0)))
                    child.setHidden(not ok)
                    shown += ok
            section.setHidden(bool(words) and shown == 0)

    # ------------------------------------------------------------ the picked type

    def _picked(self, item: QTreeWidgetItem | None, _previous=None) -> None:
        data = item.data(0, DATA) if item is not None else None
        self._kind, self._source, self._model = None, "", None
        blocked = self.mode.blockSignals(True)
        self.mode.clear()
        if isinstance(data, tuple) and data[0] == LIBRARY and isinstance(data[1], LibraryModel):
            self._model, self._source = data[1], LIBRARY
            for kind in self._model.modes:
                self.mode.addItem(kind.mode or count_text(kind.channel_count), kind.id)
            self._kind = self._model.modes[0] if self._model.modes else None
        elif isinstance(data, tuple) and isinstance(data[1], LightType):
            self._source, self._kind = data
        self.mode.blockSignals(blocked)
        has_modes = self._model is not None
        self.mode.setVisible(has_modes)
        self.mode_label.setVisible(has_modes)
        self._show_kind()

    def _mode_picked(self, index: int) -> None:
        if self._model is not None and 0 <= index < len(self._model.modes):
            self._kind = self._model.modes[index]
            self._show_kind()

    def _show_kind(self) -> None:
        kind = self._kind
        if self._model is not None:
            self.title.setText(f"{self._model.maker} {self._model.name}")
        else:
            self.title.setText(kind.name if kind is not None else "Pick a light type")
        fill_channels(self.channels, kind)
        if kind is None:
            self.note.setText("")
        elif self._source == LIBRARY:
            made = _day(library().made)
            self.note.setText(f"{count_text(kind.channel_count)}. From the Open Fixture Library"
                              + (f" ({made})" if made else ""))
            self.note.setToolTip("Set the light to the same mode on its own menu. The library is made by people who "
                                 "use these lights: check the channels against the light's manual.")
        else:
            users = sum(1 for fx in self._show.fixtures if fx.profile == kind.id)
            self.note.setText(count_text(kind.channel_count) + (f". Used by {lights_text(users)} here" if users else ""))
            self.note.setToolTip("")
        in_place = self._source in (SHOW, YOURS)
        self.edit_button.setText("Edit…" if in_place else "Edit a copy…")
        self.edit_button.setToolTip("Change it for every light of this type in this show" if self._source == SHOW
                                    else "Change it in your light types" if self._source == YOURS
                                    else "Start a new light type from this one")
        self.edit_button.setEnabled(kind is not None and self._edit is not None)
        self.remove_button.setVisible(self._source == YOURS)
        can_use = kind is not None and (self._adding or bool(self._lights))
        self.use_button.setEnabled(can_use)
        self.use_button.setToolTip("" if self._adding or self._lights else "Choose lights on the Layout tab first")

    def picked(self) -> LightType | None:
        return self._kind

    # ------------------------------------------------------------ buttons

    def _double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, DATA)
        if isinstance(data, tuple) and data[0] in (SHOW, YOURS, BUILT_IN, LIBRARY) and self.use_button.isEnabled():
            self._use()

    def _edit_clicked(self) -> None:
        if self._kind is not None:
            self._run_editor(self._kind, "edit" if self._source in (SHOW, YOURS) else "copy")

    def _run_editor(self, base: LightType | None, how: str) -> None:
        if self._edit is None:
            return
        kind, applied = self._edit(base, how)
        if kind is None:
            return
        if applied:
            self.chosen, self.applied = kind, True
            self.accept()
            return
        self._fill()
        self._select(kind.id, YOURS if kind.id in self._own_ids else SHOW)

    def _remove(self) -> None:
        kind = self._kind
        if kind is None or self._source != YOURS or self._own_dir is None:
            return
        answer = QMessageBox.question(self, "Remove from your light types",
                                      f"Take {kind.name} off your light types?\n\nLights and shows that use it keep it.")
        if answer == QMessageBox.StandardButton.Yes:
            remove_own(self._own_dir, kind.id)
            self._fill()

    def _use(self) -> None:
        if self._kind is not None:
            self.chosen = self._kind
            self.accept()


# ---------------------------------------------------------------- the New light type window


class _Normally(QStackedWidget):
    """The Normally cell: a number to send, or what CentraLight fills it in from."""

    def __init__(self, value: int) -> None:
        super().__init__()
        self.spin = QSpinBox()
        self.spin.setRange(0, 255)
        self.spin.setValue(value)
        self.spin.setToolTip("What it's sent when nothing sets it (0 to 255)")
        self.spin.setAccessibleName("Normally")
        self.label = QLabel("")
        self.label.setObjectName("Hint")
        self.label.setToolTip("CentraLight sets it")
        self.addWidget(self.spin)
        self.addWidget(self.label)

    def show_text(self, text: str | None) -> None:
        if text is None:
            self.setCurrentWidget(self.spin)
        else:
            self.label.setText(text)
            self.setCurrentWidget(self.label)


class LightTypeEditor(QDialog):
    """New light type (or Edit): its maker, model, mode and channels, tried on the lights it's for while it's open."""

    def __init__(self, parent: QWidget | None, *, base: LightType | None = None, heading: str = "New light type",
                 lights: Sequence[str] = (), preview: Callable[[LightType], None] | None = None,
                 try_colour: Callable[[RGB | None], None] | None = None) -> None:
        """base: the type it starts from (None: Dimmer, Red, Green, Blue). preview(type): the lights it's for use this
        type now. try_colour(colour): light those lights in a test colour (None: stop)."""
        super().__init__(parent)
        self.setWindowTitle(heading)
        self.setObjectName("LightTypeEditor")
        self.resize(760, 600)
        self._lights = list(lights)
        self._preview = preview
        self._try = try_colour
        self._building = False
        self._draft: LightType | None = None
        self.result_type: LightType | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._send_preview)

        self.maker = QLineEdit(base.maker if base else "")
        self.maker.setPlaceholderText("Betopper")
        self.model = QLineEdit(base.model if base else "")
        self.model.setPlaceholderText("LPC018")
        self.mode = QLineEdit(base.mode if base else "")
        self.mode.setPlaceholderText("7-channel")
        self.mode.setToolTip("The mode the light is set to on its own menu")
        fields = QGridLayout()
        for column, (label, edit) in enumerate((("Maker", self.maker), ("Model", self.model), ("Mode", self.mode))):
            caption = QLabel(label)
            caption.setObjectName("Hint")
            fields.addWidget(caption, 0, column)
            fields.addWidget(edit, 1, column)
        self.name_label = QLabel("")
        self.name_label.setObjectName("Hint")

        self.table = channel_table()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.add_button = QPushButton("+ Channel")
        self.add_button.setToolTip("Add a channel after the chosen one")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("↑")
        self.up_button.setToolTip("Move the chosen channel up")
        self.down_button = QPushButton("↓")
        self.down_button.setToolTip("Move the chosen channel down")
        row_buttons = QHBoxLayout()
        for button in (self.add_button, self.remove_button, self.up_button, self.down_button):
            button.setAutoDefault(False)  # (Enter is Save)
            row_buttons.addWidget(button)
        row_buttons.addStretch(1)
        self.problem = QLabel("")
        self.problem.setObjectName("ExpressionProblem")
        self.problem.setWordWrap(True)

        self.try_label = QLabel(f"Try on {lights_text(len(self._lights))}" if self._lights else "Try it")
        self.try_label.setObjectName("Hint")
        self.try_buttons: dict[str, QPushButton] = {}
        try_row = QHBoxLayout()
        try_row.addWidget(self.try_label)
        for name, colour in (*TRY_COLOURS, ("Off", None)):
            button = QPushButton(name)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _c=False, rgb=colour: self._try_colour(rgb))
            button.setEnabled(bool(self._lights) and try_colour is not None)
            if not self._lights:
                button.setToolTip("Choose lights on the Layout tab to try it on them")
            try_row.addWidget(button)
            self.try_buttons[name] = button
        try_row.addStretch(1)
        self.buttons = QDialogButtonBox()
        self.save_button = self.buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setDefault(True)
        self.save_button.setToolTip("Keep it in your light types" + (", and use it for these lights" if preview else ""))
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        try_row.addWidget(self.buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(fields)
        layout.addWidget(self.name_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(row_buttons)
        layout.addWidget(self.problem)
        layout.addLayout(try_row)

        for edit in (self.maker, self.model, self.mode):
            edit.textChanged.connect(self._changed)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        start = list(base.channels) if base else [Channel("dimmer"), Channel("red"), Channel("green"), Channel("blue")]
        self._set_rows(start, 0)

    # ------------------------------------------------------------ rows

    def _set_rows(self, channels: Sequence[Channel], select: int) -> None:
        self._building = True
        try:
            self.table.setRowCount(0)
            for row, channel in enumerate(channels):
                self.table.insertRow(row)
                number = QTableWidgetItem(str(row + 1))
                number.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, 0, number)
                job = QComboBox()
                for role in ROLES:
                    job.addItem(ROLE_LABELS[role], role)
                    job.setItemData(job.count() - 1, JOB_TIPS.get(role, ""), Qt.ItemDataRole.ToolTipRole)
                job.setCurrentIndex(max(0, job.findData(channel.role)))
                job.setAccessibleName(f"Channel {row + 1} job")
                job.currentIndexChanged.connect(self._changed)
                name = QLineEdit(channel.name)
                name.setPlaceholderText(ROLE_LABELS.get(channel.role, ""))
                name.setAccessibleName(f"Channel {row + 1} name")
                name.textChanged.connect(self._changed)
                normal = _Normally(channel.default)
                normal.spin.valueChanged.connect(self._changed)
                for widget in (job, name, normal.spin):  # (clicking into a row's box makes it the chosen row)
                    widget.setProperty("row", row)
                    widget.installEventFilter(self)
                self.table.setCellWidget(row, 1, job)
                self.table.setCellWidget(row, 2, name)
                self.table.setCellWidget(row, 3, normal)
            if channels:
                self.table.selectRow(max(0, min(select, len(channels) - 1)))
        finally:
            self._building = False
        self._changed()

    def channels(self) -> list[Channel]:
        """The channels as typed."""
        found = []
        for row in range(self.table.rowCount()):
            job, name, normal = (self.table.cellWidget(row, column) for column in (1, 2, 3))
            if job is None or name is None or normal is None:
                continue
            found.append(Channel(str(job.currentData()), name.text(), normal.spin.value()))
        return found

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.FocusIn and isinstance(watched.property("row"), int):
            row = watched.property("row")
            if 0 <= row < self.table.rowCount() and self.table.currentRow() != row:
                self.table.selectRow(row)
        return super().eventFilter(watched, event)

    def _current(self) -> int:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else self.table.rowCount() - 1

    def _add(self) -> None:
        channels = self.channels()
        if len(channels) >= MAX_CHANNELS:
            return
        at = self._current() + 1
        channels.insert(at, Channel("other"))
        self._set_rows(channels, at)

    def _remove(self) -> None:
        channels = self.channels()
        at = self._current()
        if 0 <= at < len(channels):
            del channels[at]
            self._set_rows(channels, at)

    def _move(self, step: int) -> None:
        channels = self.channels()
        at = self._current()
        to = at + step
        if 0 <= at < len(channels) and 0 <= to < len(channels):
            channels[at], channels[to] = channels[to], channels[at]
            self._set_rows(channels, to)

    # ------------------------------------------------------------ the type as typed

    def draft(self) -> tuple[LightType | None, str]:
        """The type as typed, or why it can't be used yet."""
        channels = self.channels()
        model = " ".join(self.model.text().split())
        maker = " ".join(self.maker.text().split())
        mode = " ".join(self.mode.text().split())
        if not channels:
            return None, "Add its channels"
        if not model:
            return None, "Type the light's model (it's on the light, or in its manual)"
        kind = make_type(type_name(maker, model, mode, len(channels)), channels, short=model, maker=maker,
                         model=model, mode=mode)
        try:
            check(kind)
        except LightTypeError:
            return None, "It needs a channel that gives light: a Dimmer, or Red, Green and Blue, or White"
        return kind, ""

    def _changed(self, *_args) -> None:
        if self._building:
            return
        channels = self.channels()
        shape = LightType("", "", tuple(channels))  # (for which channels CentraLight fills in)
        for row, channel in enumerate(channels):
            normal = self.table.cellWidget(row, 3)
            name = self.table.cellWidget(row, 2)
            if isinstance(normal, _Normally):
                normal.show_text(normally(shape, channel) if shape.automatic(channel) else None)
            if isinstance(name, QLineEdit):
                name.setPlaceholderText(ROLE_LABELS.get(channel.role, ""))
        kind, problem = self.draft()
        self._draft = kind
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))
        self.save_button.setEnabled(kind is not None)
        self.name_label.setText(f"Shows as {kind.name}" if kind is not None else "")
        if kind is not None and self._preview is not None:
            self._timer.start()

    def _send_preview(self) -> None:
        if self._draft is not None and self._preview is not None:
            self._preview(self._draft)

    def _try_colour(self, colour: RGB | None) -> None:
        if self._timer.isActive():  # (what's typed reaches the lights first)
            self._timer.stop()
            self._send_preview()
        if self._try is not None:
            self._try(colour)

    def _save(self) -> None:
        kind, _problem = self.draft()
        if kind is None:
            return
        self.result_type = kind
        self.accept()

    def done(self, result: int) -> None:  # noqa: D102
        self._timer.stop()
        if self._try is not None:
            self._try(None)  # (the test colour goes off with the window)
        super().done(result)
