"""The Automation tab: "when these conditions are met, do this".

- Left: the list of automations (tick = on), with what each does and when it last ran, then
  what's coming up in the next 7 days.
- Right: the selected automation: its name, on / off and Run now; "Run when All / Any of these
  are true" with a row per condition (each can be inverted); "Do this", a row per action (each
  can wait first); and, optionally, "When it stops matching, do this". Below, the history of
  recent runs.
- Top: pause every automation (also the header's Automations chip), and Undo / Redo / History
  for changes on this tab.

Edits go through the show document (one undo step each) and are saved to automations\\<id>.json.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from PySide6.QtCore import QDate, Qt, QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..automations import (
    ACTIONS,
    CONDITIONS,
    DAY_NAMES,
    DAYS,
    MONTHS,
    add_automation,
    default_action,
    default_condition,
    describe_action,
    describe_condition,
    duplicate_automation,
    move_automation,
    parse_day_month,
    remove_automation,
    summary,
    update_automation,
    upcoming,
)
from ..commands import ResetAutomation, RunAutomation, SetAutomationsPaused
from ..engine import AutomationsView, EngineFrame
from ..model import TRANSITION_KINDS, Automation, Show
from .document import ShowDocument
from .undo_bar import UndoBar
from .widgets import ElidedLabel, FlowLayout, Panel, ResettableSplitter, ToggleSwitch, discard

EVENT_HINTS = {
    "pc_startup": "When CentraLight starts within 10 minutes of Windows starting.",
    "app_started": "Every time CentraLight starts (restarts too).",
    "pc_woke": "When the PC wakes from sleep.",
    "blackout": "While Blackout is on.",
}
AUDIO_BAND_LABELS = (("level", "loudness"), ("bass", "bass"), ("mid", "middle"), ("treble", "treble"), ("beat", "beat"), ("pitch", "pitch"))
WHERE_LABELS = (("anywhere", "anywhere"), ("base", "on Base")) + tuple((f"overlay{n}", f"in Overlay {n}") for n in range(1, 5))
COMPARE_LABELS = (("=", "is"), ("!=", "is not"), ("<", "is below"), (">", "is above"), ("on", "is on"), ("off", "is off"))
TRANSITION_LABELS = (("", "its own transition"), ("cut", "a cut")) + tuple((k, k.title()) for k in TRANSITION_KINDS if k != "cut")


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def _combo(items: list[tuple[Any, str]] | tuple, current: Any) -> QComboBox:
    combo = QComboBox()
    for value, label in items:
        combo.addItem(label, value)
    index = combo.findData(current)
    if index < 0 and current not in (None, ""):
        combo.addItem(f"{current} (not found)", current)
        index = combo.count() - 1
    combo.setCurrentIndex(max(0, index))
    return combo


def _spin(low: float, high: float, value: float, suffix: str = "", decimals: int = 1) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setObjectName("NumberSpin")
    spin.setRange(low, high)
    spin.setDecimals(decimals)
    spin.setValue(value)
    spin.setSuffix(suffix)
    spin.setKeyboardTracking(False)
    spin.setFixedWidth(spin.fontMetrics().horizontalAdvance(f"{high:.{decimals}f}{suffix}") + 22)
    return spin


def _remove_button(tip: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("RemoveButton")
    button.setText("✕")
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _when(stamp: float | None) -> str:
    if not stamp:
        return "never"
    moment = datetime.fromtimestamp(stamp)
    today = datetime.now().date()
    if moment.date() == today:
        return f"today {moment:%H:%M}"
    return f"{moment:%a %d %b %H:%M}"


# ---------------------------------------------------------------- one condition / action


class RuleRow(QFrame):
    """One condition or action: its kind, its settings (they wrap on narrow windows) and ✕."""

    changed = Signal(object)  # the new data
    removed = Signal()

    def __init__(self, page: AutomationPage, data: dict, is_action: bool) -> None:
        super().__init__()
        self.setObjectName("VariableCard")
        self.page = page
        self.data = dict(data)
        self.is_action = is_action
        kinds = ACTIONS if is_action else CONDITIONS
        key = "do" if is_action else "type"
        self.kind = _combo(list(kinds.items()), self.data.get(key))
        self.kind.setToolTip("What this " + ("does" if is_action else "checks"))
        self.kind.currentIndexChanged.connect(lambda _i: self._kind_changed(key))
        remove = _remove_button("Remove this " + ("action" if is_action else "condition"))
        remove.clicked.connect(self.removed.emit)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self.kind, 1)
        if not is_action:
            self.invert = QCheckBox("Not")
            self.invert.setToolTip("Invert it: true when this is NOT the case")
            self.invert.setChecked(bool(self.data.get("invert")))
            self.invert.toggled.connect(lambda on: self._set("invert", True) if on else self._drop("invert"))
            top.addWidget(self.invert)
        top.addWidget(remove)
        self.settings = QWidget()
        self.flow = FlowLayout(self.settings, spacing=6)
        self.notes = QVBoxLayout()  # hints go here, under the settings, using the row's full width
        self.notes.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.settings)
        layout.addLayout(self.notes)
        self._build()
        self.settings.setVisible(self.flow.count() > 0)

    # --- changes

    def _set(self, key: str, value: Any) -> None:
        if self.data.get(key) != value:
            self.data[key] = value
            self.changed.emit(dict(self.data))

    def _drop(self, key: str) -> None:
        if key in self.data:
            del self.data[key]
            self.changed.emit(dict(self.data))

    def _kind_changed(self, key: str) -> None:
        kind = self.kind.currentData()
        if kind == self.data.get(key):
            return
        fresh = default_action(kind, self.page.show_data) if self.is_action else default_condition(kind, self.page.show_data)
        if self.is_action and self.data.get("delay"):
            fresh["delay"] = self.data["delay"]
        self.changed.emit(fresh)

    def _add(self, *widgets: QWidget) -> None:
        for widget in widgets:
            self.flow.addWidget(widget)

    def _note(self, text: str) -> None:
        if text:
            self.notes.addWidget(_hint(text))

    def _text(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("RowDetail")
        label.setMinimumHeight(self.kind.sizeHint().height())  # level with the boxes beside it
        label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return label

    # --- the settings for each kind

    def _build(self) -> None:
        show = self.page.show_data
        d = self.data
        kind = d.get("do") if self.is_action else d.get("type")
        inputs = [(i.id, i.name) for i in show.ordered_inputs()]
        if not self.is_action:
            if kind == "time":
                start, end = QTimeEdit(), QTimeEdit()
                for edit, key in ((start, "from"), (end, "to")):
                    edit.setDisplayFormat("HH:mm")
                    edit.setTime(QTime.fromString(str(d.get(key, "09:00")), "HH:mm"))
                    edit.timeChanged.connect(lambda t, k=key: self._set(k, t.toString("HH:mm")))
                self._add(self._text("from"), start, self._text("to"), end)
                self._note("Overnight is fine: 22:00 to 02:00.")
            elif kind == "days":
                for code, name in zip(DAYS, DAY_NAMES):
                    button = QPushButton(name)
                    button.setObjectName("ToolToggle")
                    button.setCheckable(True)
                    button.setChecked(code in (d.get("days") or ()))
                    button.toggled.connect(lambda on, c=code: self._toggle_day(c, on))
                    self._add(button)
            elif kind == "dates":
                for key in ("from", "to"):
                    month_day = parse_day_month(d.get(key)) or (12, 1)
                    day = QSpinBox()
                    day.setObjectName("NumberSpin")
                    day.setRange(1, 31)
                    day.setValue(month_day[1])
                    day.setFixedWidth(day.fontMetrics().horizontalAdvance("31") + 24)
                    month = _combo([(i + 1, m) for i, m in enumerate(MONTHS)], month_day[0])
                    day.valueChanged.connect(lambda _v, k=key, a=day, b=month: self._set_date(k, a, b))
                    month.currentIndexChanged.connect(lambda _i, k=key, a=day, b=month: self._set_date(k, a, b))
                    self._add(self._text(key), day, month)
                only = QCheckBox("Only in")
                year = QSpinBox()
                year.setObjectName("NumberSpin")
                year.setRange(2024, 2100)
                year.setValue(int(d.get("year") or QDate.currentDate().year()))
                year.setFixedWidth(year.fontMetrics().horizontalAdvance("2100") + 24)
                only.setChecked(d.get("year") is not None)
                year.setEnabled(only.isChecked())
                only.toggled.connect(lambda on: (year.setEnabled(on), self._set("year", year.value()) if on else self._drop("year")))
                year.valueChanged.connect(lambda v: only.isChecked() and self._set("year", v))
                self._add(only, year)
            elif kind == "input_live":
                pick = _combo(inputs, d.get("input"))
                pick.currentIndexChanged.connect(lambda _i: self._set("input", pick.currentData()))
                where = _combo(WHERE_LABELS, d.get("where", "anywhere"))
                where.currentIndexChanged.connect(lambda _i: self._set("where", where.currentData()))
                self._add(pick, self._text("is"), where)
            elif kind == "variable":
                names = [(g.name, g.name) for g in show.globals]
                pick = _combo(names, d.get("name"))
                pick.currentIndexChanged.connect(lambda _i: self._set("name", pick.currentData()))
                compare = _combo(COMPARE_LABELS, d.get("compare", "="))
                value = QLineEdit(str(d.get("value", "")))
                value.setFixedWidth(value.fontMetrics().horizontalAdvance("#FFFFFF") + 24)
                value.setVisible(compare.currentData() not in ("on", "off"))
                compare.currentIndexChanged.connect(lambda _i: (value.setVisible(compare.currentData() not in ("on", "off")), self._set("compare", compare.currentData())))
                value.editingFinished.connect(lambda: self._set("value", _number_or_text(value.text())))
                if not names:
                    self._note("Add global variables in the Editor's Globals tab first.")
                self._add(pick, compare, value)
            elif kind == "ndi":
                source = QComboBox()
                source.setEditable(True)
                for name in sorted(set(self.page.ndi_sources()) | {"Media Background Pane"}):
                    source.addItem(name)
                source.setCurrentText(str(d.get("source", "Media Background Pane")))
                source.currentTextChanged.connect(lambda text: self._set("source", text.strip()))
                self._add(source, self._text("is sending pictures"))
            elif kind == "audio":
                band = _combo(AUDIO_BAND_LABELS, d.get("band", "level"))
                band.currentIndexChanged.connect(lambda _i: self._set("band", band.currentData()))
                above = _spin(0.0, 100.0, float(d.get("above", 0.6)) * 100, " %", 0)
                above.valueChanged.connect(lambda v: self._set("above", round(v / 100, 3)))
                hold = _spin(0.0, 600.0, float(d.get("for", 5)), " s", 0)
                hold.setToolTip("How long it has to stay that loud first (so one loud moment doesn't count)")
                hold.valueChanged.connect(lambda v: self._set("for", v))
                self._add(band, self._text("above"), above, self._text("for"), hold)
                self._note("Needs sound: Settings → Audio.")
            else:
                self._note(EVENT_HINTS.get(kind, ""))
            return
        # actions
        if kind == "base":
            pick = _combo(inputs, d.get("input"))
            pick.currentIndexChanged.connect(lambda _i: self._set("input", pick.currentData()))
            transition = d.get("transition")
            current = transition if transition == "cut" else (transition.get("kind") if hasattr(transition, "get") else "")
            how = _combo(TRANSITION_LABELS, current)
            duration = _spin(0.0, 60.0, float(transition.get("duration", 1.0)) if hasattr(transition, "get") else 1.0, " s")
            duration.setVisible(how.currentData() not in ("", "cut"))
            how.currentIndexChanged.connect(lambda _i: self._set_transition(how, duration))
            duration.valueChanged.connect(lambda _v: self._set_transition(how, duration))
            self._add(pick, self._text("with"), how, duration)
        elif kind == "overlay":
            pick = _combo(inputs, d.get("input"))
            pick.currentIndexChanged.connect(lambda _i: self._set("input", pick.currentData()))
            slot = _combo([(n, f"Overlay {n}") for n in range(1, 5)], d.get("slot", 1))
            slot.currentIndexChanged.connect(lambda _i: self._set("slot", slot.currentData()))
            state = _combo((("on", "on"), ("off", "off"), ("toggle", "on / off")), d.get("state", "on"))
            state.currentIndexChanged.connect(lambda _i: self._set("state", state.currentData()))
            self._add(pick, self._text("in"), slot, state)
        elif kind == "overlay_off":
            slot = _combo([(n, f"Overlay {n}") for n in range(1, 5)], d.get("slot", 1))
            slot.currentIndexChanged.connect(lambda _i: self._set("slot", slot.currentData()))
            self._add(slot)
        elif kind == "house":
            light = _combo([(h.fixture, h.label) for h in show.house], d.get("light"))
            light.currentIndexChanged.connect(lambda _i: self._set("light", light.currentData()))
            level = _spin(0.0, 100.0, float(d.get("level", 1.0) or 0.0) * 100, " %", 0)
            level.valueChanged.connect(lambda v: self._set("level", round(v / 100, 3)))
            switch = _combo(((True, "switch on"), (False, "switch off"), (None, "leave the switch")), d.get("on", True if d.get("level") is not None else None))
            switch.currentIndexChanged.connect(lambda _i: self._set("on", switch.currentData()) if switch.currentData() is not None else self._drop("on"))
            self._add(light, level, switch, *self._fade_widgets())
        elif kind == "master":
            level = _spin(0.0, 100.0, float(d.get("level", 1.0)) * 100, " %", 0)
            level.valueChanged.connect(lambda v: self._set("level", round(v / 100, 3)))
            self._add(level, *self._fade_widgets())
        elif kind == "blackout":
            on = _combo(((True, "on"), (False, "off")), bool(d.get("on", True)))
            on.currentIndexChanged.connect(lambda _i: self._set("on", on.currentData()))
            self._add(on)
        elif kind == "variable":
            pick = _combo([(g.name, g.name) for g in show.globals], d.get("name"))
            pick.currentIndexChanged.connect(lambda _i: self._set("name", pick.currentData()))
            value = QLineEdit(str(d.get("value", "")))
            value.setFixedWidth(value.fontMetrics().horizontalAdvance("#FFFFFF") + 24)
            value.setToolTip("A number, a colour like #FF6F59, or on / off for a switch")
            value.editingFinished.connect(lambda: self._set("value", _number_or_text(value.text())))
            self._add(pick, self._text("to"), value)
        elif kind == "layout":
            pick = _combo([(lay.id, lay.name) for lay in show.layouts], d.get("layout"))
            pick.currentIndexChanged.connect(lambda _i: self._set("layout", pick.currentData()))
            self._add(pick)
        elif kind == "automation":
            others = [(a.id, a.name) for a in show.ordered_automations() if a.id != self.page.selected]
            pick = _combo(others, d.get("automation"))
            pick.currentIndexChanged.connect(lambda _i: self._set("automation", pick.currentData()))
            on = _combo(((True, "on"), (False, "off")), bool(d.get("enabled", False)))
            on.currentIndexChanged.connect(lambda _i: self._set("enabled", on.currentData()))
            self._add(self._text("turn"), pick, on)
        delay = _spin(0.0, 3600.0, float(d.get("delay", 0) or 0), " s", 0)
        delay.setToolTip("Wait this long first (0 = straight away)")
        delay.valueChanged.connect(lambda v: self._set("delay", v) if v else self._drop("delay"))
        self._add(self._text("after"), delay)

    def _fade_widgets(self) -> list[QWidget]:
        fade = _spin(0.0, 600.0, float(self.data.get("fade", 0) or 0), " s fade", 1)
        fade.valueChanged.connect(lambda v: self._set("fade", v) if v else self._drop("fade"))
        return [fade]

    def _toggle_day(self, code: str, on: bool) -> None:
        days = [d for d in DAYS if d in (self.data.get("days") or ()) and d != code] + ([code] if on else [])
        self._set("days", [d for d in DAYS if d in days])

    def _set_date(self, key: str, day: QSpinBox, month: QComboBox) -> None:
        self._set(key, f"{int(month.currentData()):02d}-{day.value():02d}")

    def _set_transition(self, how: QComboBox, duration: QDoubleSpinBox) -> None:
        kind = how.currentData()
        duration.setVisible(kind not in ("", "cut"))
        if kind == "":
            self._drop("transition")
        elif kind == "cut":
            self._set("transition", "cut")
        else:
            self._set("transition", {"kind": kind, "duration": round(duration.value(), 2)})


def _number_or_text(text: str) -> Any:
    text = text.strip()
    try:
        return float(text) if "." in text or "e" in text.lower() else int(text)
    except ValueError:
        return text


# ---------------------------------------------------------------- the page


class AutomationPage(QWidget):
    def __init__(self, document: ShowDocument, engine: Any, sources_provider: Callable[[], list[str]] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AutomationPage")
        self.document = document
        self.engine = engine
        self._sources = sources_provider or (lambda: [])
        self.selected = ""
        self._structure: tuple = ()
        self._view = AutomationsView()
        self._list_key: tuple = ()
        self._last_upcoming = 0.0

        # --- top bar
        self.pause_switch = ToggleSwitch()
        self.pause_switch.setAccessibleName("Pause all automations")
        self.pause_switch.clicked.connect(lambda *_: self.engine.submit(SetAutomationsPaused(self.pause_switch.isChecked())))
        pause_label = QLabel("Pause all automations")
        self.pause_hint = ElidedLabel("")
        self.pause_hint.setObjectName("Hint")
        self.undo_bar = UndoBar(document.undo_group)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.pause_switch)
        top.addWidget(pause_label)
        top.addWidget(self.pause_hint, 1)
        top.addWidget(self.undo_bar)

        # --- left: the list and what's coming up
        list_panel = Panel("Automations")
        self.list = QListWidget()
        self.list.setObjectName("AutomationList")
        self.list.setWordWrap(False)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._picked)
        self.list.itemChanged.connect(self._ticked)
        list_panel.body.addWidget(self.list, 1)
        buttons = QWidget()
        flow = FlowLayout(buttons, spacing=6)
        self.new_button = QPushButton("+ New automation")
        self.new_button.setProperty("accent", True)
        self.new_button.clicked.connect(self.new_automation)
        self.up_button = QPushButton("Up")
        self.up_button.setToolTip("Earlier in the list (runs before the ones below when they fire together)")
        self.up_button.clicked.connect(lambda: self.move(-1))
        self.down_button = QPushButton("Down")
        self.down_button.clicked.connect(lambda: self.move(1))
        for button in (self.new_button, self.up_button, self.down_button):
            flow.addWidget(button)
        list_panel.body.addWidget(buttons)
        upcoming_panel = Panel("Coming up (next 7 days)")
        self.upcoming = QListWidget()
        self.upcoming.setObjectName("UpcomingList")
        self.upcoming.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.upcoming.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.upcoming.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        upcoming_panel.body.addWidget(self.upcoming, 1)
        self.left = ResettableSplitter(Qt.Orientation.Vertical, self._default_left_split)
        self.left.addWidget(list_panel)
        self.left.addWidget(upcoming_panel)
        self.left.setStretchFactor(0, 2)
        self.left.setStretchFactor(1, 1)

        # --- right: the editor and the history
        self.editor = QWidget()
        self.editor.setObjectName("ScrollContent")
        self.editor_layout = QVBoxLayout(self.editor)
        self.editor_layout.setContentsMargins(0, 0, 6, 0)
        self.editor_layout.setSpacing(12)
        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor_scroll.setWidget(self.editor)
        history_panel = Panel("History")
        self.history = QListWidget()
        self.history.setObjectName("HistoryRuns")
        self.history.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.history.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        history_panel.body.addWidget(self.history, 1)
        self.right = ResettableSplitter(Qt.Orientation.Vertical, self._default_right_split)
        self.right.addWidget(self.editor_scroll)
        self.right.addWidget(history_panel)
        self.right.setStretchFactor(0, 3)  # the editor gets most of the height
        self.right.setStretchFactor(1, 1)

        self.splitter = ResettableSplitter(Qt.Orientation.Horizontal, self._default_split)
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(self.right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.left.setMinimumWidth(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.splitter, 1)

        self.rows: dict[str, list[RuleRow]] = {"conditions": [], "actions": [], "on_end": []}
        document.changed.connect(self.set_show)
        self.set_show(document.show)

    # ------------------------------------------------------------ splits

    def _default_split(self) -> list[int]:
        total = max(1, self.splitter.width())
        left = min(420, max(300, total // 3))
        return [left, total - left]

    def _default_left_split(self) -> list[int]:
        total = max(1, self.left.height())
        return [total - max(140, total // 3), max(140, total // 3)]

    def _default_right_split(self) -> list[int]:
        total = max(1, self.right.height())
        return [total - max(130, total // 4), max(130, total // 4)]

    def apply_split(self, sizes: dict | None) -> None:
        sizes = sizes if isinstance(sizes, dict) else {}
        for splitter, key in ((self.splitter, "outer"), (self.left, "left"), (self.right, "right")):
            splitter.apply_sizes(sizes.get(key))

    def splits(self) -> dict:
        return {"outer": self.splitter.sizes(), "left": self.left.sizes(), "right": self.right.sizes()}

    # ------------------------------------------------------------ showing

    @property
    def show_data(self) -> Show:
        return self.document.show

    def ndi_sources(self) -> list[str]:
        try:
            return list(self._sources())
        except Exception:
            return []

    def current(self) -> Automation | None:
        return self.show_data.automations.get(self.selected)

    def set_show(self, show: Show) -> None:
        if self.selected not in show.automations:
            first = next(iter(show.ordered_automations()), None)
            self.selected = first.id if first else ""
        self._fill_list(show)
        self._build_editor(show)
        self._fill_upcoming(show, force=True)
        read_only = show.read_only
        for widget in (self.new_button, self.up_button, self.down_button):
            widget.setEnabled(not read_only)
        self.editor.setEnabled(not read_only)

    def _fill_list(self, show: Show) -> None:
        statuses = self._view.statuses
        rows = []
        for automation in show.ordered_automations():
            status = statuses.get(automation.id)
            last = status.last_run if status else None
            state = " · paused by the loop guard" if status and status.tripped else ""
            rows.append((automation.id, automation.name, automation.enabled, f"{summary(automation, show)}", f"last ran {_when(last)}{state}",
                         bool(status and status.tripped)))
        key = tuple(rows) + (self.selected,)
        if key == self._list_key:
            return
        self._list_key = key
        blocked = self.list.blockSignals(True)
        self.list.clear()
        for automation_id, name, enabled, text, last, tripped in rows:
            item = QListWidgetItem(f"{name}  ·  {last}\n{text}")
            item.setData(Qt.ItemDataRole.UserRole, automation_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            item.setToolTip(f"{name}\n{text}\n{last}\nTick = on")
            if tripped:
                item.setForeground(Qt.GlobalColor.red)
            self.list.addItem(item)
            if automation_id == self.selected:
                self.list.setCurrentItem(item)
        if not rows:
            item = QListWidgetItem("No automations yet.\nClick + New automation to make one.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        self.list.blockSignals(blocked)
        self.up_button.setEnabled(bool(self.selected) and not show.read_only)
        self.down_button.setEnabled(bool(self.selected) and not show.read_only)

    def _fill_upcoming(self, show: Show, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_upcoming < 30:
            return
        self._last_upcoming = now
        self.upcoming.clear()
        found = upcoming(show, datetime.now())
        for moment, automation, others in found[:30]:
            extra = " (if its other conditions are true then)" if others else ""
            self._add_line(self.upcoming, f"{moment:%a %d %b %H:%M}  {automation.name}{extra}")
        if not found:
            self._add_line(self.upcoming, "Nothing in the next 7 days.")

    @staticmethod
    def _add_line(view: QListWidget, text: str) -> None:
        item = QListWidgetItem(text)
        item.setToolTip(text)  # shortened with "…" when the column is narrow; the tooltip has it all
        view.addItem(item)

    def _structure_key(self, automation: Automation | None) -> tuple:
        if automation is None:
            return ("none",)
        kinds = tuple((key, tuple(item.get("do") if key != "conditions" else item.get("type") for item in getattr(automation, key)))
                      for key in ("conditions", "actions", "on_end"))
        names = (tuple(i.id for i in self.show_data.ordered_inputs()), tuple(g.name for g in self.show_data.globals),
                 tuple(lay.id for lay in self.show_data.layouts), tuple(self.show_data.automations))
        return (automation.id, automation.logic, kinds, names)

    def _build_editor(self, show: Show, force: bool = False) -> None:
        automation = self.current()
        key = self._structure_key(automation)
        if key == self._structure and not force:
            self._refresh_editor(automation)
            return
        self._structure = key
        while self.editor_layout.count():
            item = self.editor_layout.takeAt(0)
            if item.widget() is not None:
                discard(item.widget())
        self.rows = {"conditions": [], "actions": [], "on_end": []}
        if automation is None:
            panel = Panel("Automation")
            panel.body.addWidget(_hint("Automations look after the lights by themselves, for example “Sunday 09:00–12:30 → "
                                       "Warm welcome on Base, key lights 70 %”. Click + New automation to make one."))
            self.editor_layout.addWidget(panel)
            self.editor_layout.addStretch(1)
            return
        about = Panel("Automation")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.name_edit = QLineEdit(automation.name)
        self.name_edit.setToolTip("Its name")
        self.name_edit.editingFinished.connect(self._renamed)
        self.enabled_switch = ToggleSwitch()
        self.enabled_switch.setAccessibleName("On")
        self.enabled_switch.setToolTip("On: it runs by itself when its conditions are met")
        self.enabled_switch.setChecked(automation.enabled)
        self.enabled_switch.clicked.connect(lambda *_: self.change(f"Turn “{automation.name}” {'on' if self.enabled_switch.isChecked() else 'off'}",
                                                                   enabled=self.enabled_switch.isChecked()))
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.enabled_switch)
        about.body.addLayout(row)
        buttons = QWidget()
        flow = FlowLayout(buttons, spacing=6)
        run = QPushButton("Run now")
        run.setToolTip("Do its actions now, whatever its conditions")
        run.clicked.connect(lambda: self.engine.submit(RunAutomation(automation.id)))
        duplicate = QPushButton("Duplicate")
        duplicate.clicked.connect(self.duplicate)
        delete = QPushButton("Delete")
        delete.setProperty("danger", True)
        delete.clicked.connect(self.delete)
        self.reset_button = QPushButton("Let it run again")
        self.reset_button.setToolTip("The loop guard paused it after it ran too often; this lets it run again")
        self.reset_button.clicked.connect(lambda: self.engine.submit(ResetAutomation(automation.id)))
        for button in (run, duplicate, delete, self.reset_button):
            flow.addWidget(button)
        about.body.addWidget(buttons)
        self.summary = QLabel(summary(automation, show))
        self.summary.setObjectName("Hint")
        self.summary.setWordWrap(True)
        about.body.addWidget(self.summary)
        self.editor_layout.addWidget(about)

        conditions = Panel("Run when")
        logic_row = QHBoxLayout()
        self.logic = _combo((("all", "all"), ("any", "any")), automation.logic)
        self.logic.currentIndexChanged.connect(lambda _i: self.change("Run when: " + self.logic.currentData(), logic=self.logic.currentData()))
        logic_row.addWidget(self.logic)
        logic_row.addWidget(QLabel("of these are true:"), 1)
        conditions.body.addLayout(logic_row)
        self._rule_rows(conditions, automation, "conditions")
        self._add_button(conditions, "+ Add a condition  ▾", CONDITIONS, "conditions")
        conditions.body.addWidget(_hint("It runs once when this becomes true (not over and over while it stays true). "
                                        "At startup and after sleep it checks straight away, so it catches up."))
        self.editor_layout.addWidget(conditions)

        actions = Panel("Do this")
        self._rule_rows(actions, automation, "actions")
        self._add_button(actions, "+ Add an action  ▾", ACTIONS, "actions")
        self.editor_layout.addWidget(actions)

        ending = Panel("When it stops matching, do this (optional)")
        self._rule_rows(ending, automation, "on_end")
        self._add_button(ending, "+ Add an action  ▾", ACTIONS, "on_end")
        self.editor_layout.addWidget(ending)
        self.editor_layout.addStretch(1)
        self._refresh_editor(automation)

    def _rule_rows(self, panel: Panel, automation: Automation, key: str) -> None:
        for index, item in enumerate(getattr(automation, key)):
            row = RuleRow(self, dict(item), key != "conditions")
            row.changed.connect(lambda data, k=key, i=index: self.set_rule(k, i, data))
            row.removed.connect(lambda k=key, i=index: self.remove_rule(k, i))
            panel.body.addWidget(row)
            self.rows[key].append(row)

    def _add_button(self, panel: Panel, text: str, kinds: dict, key: str) -> None:
        button = QToolButton()
        button.setObjectName("MenuButton")
        button.setText(text)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        for kind, label in kinds.items():
            menu.addAction(label, lambda k=kind: self.add_rule(key, k))
        button.setMenu(menu)
        panel.body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)

    def _refresh_editor(self, automation: Automation | None) -> None:
        if automation is None or not hasattr(self, "summary"):
            return
        self.summary.setText(summary(automation, self.show_data))
        if not self.name_edit.hasFocus():
            self.name_edit.setText(automation.name)
        if self.enabled_switch.isChecked() != automation.enabled:
            self.enabled_switch.setChecked(automation.enabled)
        status = self._view.statuses.get(automation.id)
        self.reset_button.setVisible(bool(status and status.tripped))

    # ------------------------------------------------------------ engine frames

    def set_frame(self, frame: EngineFrame) -> None:
        view = frame.automations
        if view is self._view:
            return
        self._view = view
        if self.pause_switch.isChecked() != view.paused:
            self.pause_switch.setChecked(view.paused)
        self.pause_hint.setText(f"Paused since {_when(view.paused_since)}: nothing runs by itself until you resume"
                                if view.paused else "Automations run by themselves when their conditions are met")
        self.history.clear()
        for entry in reversed(view.history):
            detail = f" ({entry.detail})" if entry.detail else ""
            self._add_line(self.history, f"{_when(entry.when)}  {entry.name} {entry.what}{detail}")
        if not view.history:
            self._add_line(self.history, "Nothing has run yet.")
        self._fill_list(self.show_data)
        self._refresh_editor(self.current())
        self._fill_upcoming(self.show_data)

    # ------------------------------------------------------------ edits (one undo step each)

    def change(self, text: str, merge_key: str | None = None, **changes: Any) -> bool:
        if not self.selected:
            return False
        return self.document.apply(update_automation(self.show_data, self.selected, **changes), text, merge_key)

    def _renamed(self) -> None:
        automation = self.current()
        name = self.name_edit.text().strip()
        if automation is not None and name and name != automation.name:
            self.change(f"Rename “{automation.name}” → {name}", name=name)

    def _picked(self, item, _previous=None) -> None:
        if item is None:
            return
        automation_id = item.data(Qt.ItemDataRole.UserRole)
        if automation_id and automation_id != self.selected:
            self.selected = automation_id
            self._build_editor(self.show_data)
            self._list_key = ()
            self._fill_list(self.show_data)

    def _ticked(self, item) -> None:
        automation_id = item.data(Qt.ItemDataRole.UserRole)
        automation = self.show_data.automations.get(automation_id) if automation_id else None
        if automation is None:
            return
        on = item.checkState() == Qt.CheckState.Checked
        if on != automation.enabled:
            self.document.apply(update_automation(self.show_data, automation_id, enabled=on),
                                f"Turn “{automation.name}” {'on' if on else 'off'}")

    def new_automation(self) -> str:
        show, automation_id = add_automation(self.show_data)
        self.selected = automation_id
        return automation_id if self.document.apply(show, "New automation") else ""

    def duplicate(self) -> None:
        automation = self.current()
        if automation is not None:
            show, new_id = duplicate_automation(self.show_data, automation.id)
            self.selected = new_id
            self.document.apply(show, f"Duplicate “{automation.name}”")

    def delete(self) -> None:
        automation = self.current()
        if automation is None:
            return
        answer = QMessageBox.question(self, "Delete automation", f"Delete “{automation.name}”?\n\nCtrl+Z brings it back.")
        if answer == QMessageBox.StandardButton.Yes:
            self.document.apply(remove_automation(self.show_data, automation.id), f"Delete “{automation.name}”")

    def move(self, step: int) -> None:
        automation = self.current()
        if automation is not None:
            self.document.apply(move_automation(self.show_data, automation.id, step), f"Move “{automation.name}” {'up' if step < 0 else 'down'}")

    def add_rule(self, key: str, kind: str) -> None:
        automation = self.current()
        if automation is None:
            return
        item = default_action(kind, self.show_data) if key != "conditions" else default_condition(kind, self.show_data)
        items = [dict(x) for x in getattr(automation, key)] + [item]
        label = (CONDITIONS if key == "conditions" else ACTIONS)[kind]
        self.change(f"“{automation.name}”: add {label.lower()}", **{key: items})

    def set_rule(self, key: str, index: int, data: dict) -> None:
        automation = self.current()
        if automation is None or index >= len(getattr(automation, key)):
            return
        items = [dict(x) for x in getattr(automation, key)]
        items[index] = data
        what = describe_condition(data, self.show_data) if key == "conditions" else describe_action(data, self.show_data)
        self.change(f"“{automation.name}”: {what}", f"automation/{automation.id}/{key}/{index}", **{key: items})

    def remove_rule(self, key: str, index: int) -> None:
        automation = self.current()
        if automation is None:
            return
        items = [dict(x) for x in getattr(automation, key)]
        if index < len(items):
            items.pop(index)
            self.change(f"“{automation.name}”: remove a {'condition' if key == 'conditions' else 'action'}", **{key: items})

    def on_leave(self) -> None:
        return None
