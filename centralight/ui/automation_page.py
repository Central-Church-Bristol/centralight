"""The Automation tab: "when these conditions are met, do this".

- Left: two lists (tick = on), each with what its automations do and when they last ran: the
  Global automations (app-wide: they can load a show, cut or fade to black, set Master and the
  house lights; they're checked first), and This show's. Then what's coming up in the next 7 days,
  from both (the global ones labelled).
- Right: the selected automation: its name, on / off and Run now; "Run when All / Any of these
  are true" with a row per condition (each can be inverted); "Do this", a row per action (each
  can wait first); and, optionally, "When it stops matching, do this". Below, the history of
  recent runs (both lists').
- Top: pause every automation, global ones too (also the header's Automations chip), and Undo /
  Redo / History for changes to the list in use.

Edits go through the show's document, or the global automations' (one undo step each), and are
saved to automations\\<id>.json in the show, or to _Global automations\\<id>.json.
"""

from __future__ import annotations

import calendar
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from PySide6.QtCore import QDate, Qt, QTime, Signal
from PySide6.QtGui import QColor
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
    QTabWidget,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..automations import (
    DAY_NAMES,
    DAYS,
    GLOBAL,
    MONTHS,
    SHOW,
    SUN_MINUTES,
    WEEK_NAMES,
    WEEKS,
    actions_for,
    add_automation,
    conditions_for,
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
from ..sun import sun_times
from .document import ShowDocument
from .schedule_view import ScheduleView
from .undo_bar import UndoBar
from .widgets import ElidedLabel, FlowLayout, Panel, ResettableSplitter, ToggleSwitch, discard

EVENT_HINTS = {
    "pc_startup": "When Windows starts CentraLight at sign-in (Start with Windows), or CentraLight starts within 10 minutes of the PC starting.",
    "app_started": "Every time CentraLight starts (restarts too).",
    "pc_woke": "When the PC wakes from sleep.",
    # (M18) what they can't catch, too
    "pc_shutdown": ("When Windows shuts down, restarts or signs out. Its actions happen at once (no countdown) and "
                    "Windows waits for a fade of up to 4 s; a step that waits first comes too late. It can't catch a "
                    "power cut, holding the power button down, or a crash."),
    "pc_sleep": ("When the PC goes to sleep. Its actions happen at once, with fades of 1 s at most (Windows won't wait "
                 "longer). It can't catch a power cut, holding the power button down, or a crash."),
    "colour_black": "While nothing is on Base, the Overlay or the Console (after Cut or Fade to black, say).",
    "show_loads": ("When this show is loaded (by you, the Stream Deck or a global automation), and when the PC starts with "
                   "it. Not when CentraLight reopens after a quit or a crash: then it carries on as it was."),
}
SUN_WHEN_LABELS = (("after", "After"), ("before", "Before"))
SUN_EVENT_LABELS = (("sunset", "sunset"), ("sunrise", "sunrise"))
AUDIO_BAND_LABELS = (("level", "loudness"), ("bass", "bass"), ("mid", "middle"), ("treble", "treble"), ("beat", "beat"), ("pitch", "pitch"))
WHERE_LABELS = (("anywhere", "anywhere"), ("base", "on Base"), ("overlay", "in the Overlay"))
COMPARE_LABELS = (("=", "is"), ("!=", "is not"), ("<", "is below"), (">", "is above"), ("on", "is on"), ("off", "is off"))
TRANSITION_LABELS = (("", "its own transition"), ("cut", "a cut")) + tuple((k, k.title()) for k in TRANSITION_KINDS if k != "cut")


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def _sun_today() -> str:
    """Today's times, so the condition is easy to picture."""
    times = sun_times(date.today())
    if times is None:
        return ""
    return (f"Today in Bristol: sunrise {times[0]:%H:%M}, sunset {times[1]:%H:%M}. After sunset lasts until midnight; "
            "for all night, add \"Before sunrise\" with Any.")


def _plain(value: Any) -> Any:
    """Frozen show data and a row's own dict, made comparable (tuples and lists alike)."""
    if hasattr(value, "items"):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _num(value: Any, default: float) -> float:
    """A number from the automation's data, or the default if a file holds something else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        return default
    return float(value)


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
        kinds = actions_for(page.scope) if is_action else conditions_for(page.scope)
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
        self._no_days_note: QLabel | None = None
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
        page = self.page
        fresh = (default_action(kind, page.container(), page.scope, page.show_data, page.show_choices()) if self.is_action
                 else default_condition(kind, page.container(), page.show_choices()))
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
                chosen = d.get("days") if isinstance(d.get("days"), (list, tuple)) else ()
                for code, name in zip(DAYS, DAY_NAMES):
                    button = QPushButton(name)
                    button.setObjectName("ToolToggle")
                    button.setCheckable(True)
                    button.setChecked(code in chosen)
                    button.toggled.connect(lambda on, c=code: self._toggle_day(c, on))
                    self._add(button)
                self._no_days_note = _hint("Tick at least one day: with none, this automation never runs.")
                self._no_days_note.setVisible(not chosen)
                self.notes.addWidget(self._no_days_note)
                # which weeks of the month (M14): every week, or any of 1st-4th and Last
                weeks = [w for w in WEEKS if w in (d.get("weeks") if isinstance(d.get("weeks"), (list, tuple)) else ())]
                self._every_week = QPushButton("Every week")
                self._every_week.setObjectName("ToolToggle")
                self._every_week.setCheckable(True)
                self._every_week.setChecked(not weeks)
                self._every_week.setToolTip("Every week of the month, as before")
                self._every_week.toggled.connect(self._toggle_every_week)
                self._week_buttons: dict[str, QPushButton] = {}
                for code in WEEKS:
                    button = QPushButton(WEEK_NAMES[code])
                    button.setObjectName("ToolToggle")
                    button.setCheckable(True)
                    button.setChecked(code in weeks)
                    button.setToolTip("The last of these days in each month, whether it's the 4th or the 5th" if code == "last"
                                      else f"The {code} of these days in each month (e.g. the {code} Wednesday)")
                    button.toggled.connect(lambda on, c=code: self._toggle_week(c, on))
                    self._week_buttons[code] = button
                self._add(self._text("weeks:"), self._every_week, *self._week_buttons.values())
            elif kind == "dates":
                for key in ("from", "to"):
                    month_day = parse_day_month(d.get(key)) or (12, 1)
                    day = QSpinBox()
                    day.setObjectName("NumberSpin")
                    day.setRange(1, calendar.monthrange(2024, month_day[0])[1])
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
            elif kind == "sun":
                when = _combo(SUN_WHEN_LABELS, d.get("when", "after"))
                when.currentIndexChanged.connect(lambda _i: self._set("when", when.currentData()))
                event = _combo(SUN_EVENT_LABELS, d.get("event", "sunset"))
                event.currentIndexChanged.connect(lambda _i: self._set("event", event.currentData()))
                minutes = _spin(-SUN_MINUTES, SUN_MINUTES, _num(d.get("minutes"), 0.0), " min", 0)
                minutes.setToolTip("Earlier (−) or later (+) than sunrise / sunset, in minutes")
                minutes.valueChanged.connect(lambda v: self._set("minutes", int(round(v))))
                self._add(when, event, self._text("moved by"), minutes)
                self._note(_sun_today())
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
                    self._note("Add show variables in the Editor's Show variables tab first.")
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
                above = _spin(0.0, 100.0, _num(d.get("above"), 0.6) * 100, " %", 0)
                above.valueChanged.connect(lambda v: self._set("above", round(v / 100, 3)))
                hold = _spin(0.0, 600.0, _num(d.get("for"), 5.0), " s", 0)
                hold.setToolTip("How long it has to stay that loud first (so one loud moment doesn't count)")
                hold.valueChanged.connect(lambda v: self._set("for", v))
                self._add(band, self._text("above"), above, self._text("for"), hold)
                self._note("Needs sound: Settings → Audio. It counts as stopped after 2 s of quiet.")
            elif kind == "loaded_show":
                pick = _combo(self.page.show_choices(), d.get("show"))
                pick.currentIndexChanged.connect(lambda _i: self._set("show", pick.currentData()))
                self._add(pick, self._text("is loaded"))
            else:
                self._note(EVENT_HINTS.get(kind, ""))
            return
        # actions
        if kind == "load_show":
            pick = _combo(self.page.show_choices(), d.get("show"))
            pick.currentIndexChanged.connect(lambda _i: self._set("show", pick.currentData()))
            self._add(pick)
            self._note("The lights hold what they show until one of its inputs is sent; its automations run then.")
        elif kind == "house" and self.page.scope == GLOBAL:
            label = QComboBox()
            label.setEditable(True)
            for name in dict.fromkeys([h.label for h in show.house] + ["Festoon", "Key lights"]):
                label.addItem(name)
            label.setCurrentText(str(d.get("label", "")))
            label.setToolTip("The house light's name: it's found in whichever show is loaded (skipped if that show has none)")
            label.currentTextChanged.connect(lambda text: self._set("label", " ".join(text.split())))
            level = _spin(0.0, 100.0, _num(d.get("level", 1.0), 0.0) * 100, " %", 0)
            level.valueChanged.connect(lambda v: self._set("level", round(v / 100, 3)))
            switch = _combo(((True, "switch on"), (False, "switch off"), (None, "leave the switch")), d.get("on", True if d.get("level") is not None else None))
            switch.currentIndexChanged.connect(lambda _i: self._set("on", switch.currentData()) if switch.currentData() is not None else self._drop("on"))
            self._add(label, level, switch, *self._fade_widgets())
        elif kind == "base":
            pick = _combo(inputs, d.get("input"))
            pick.currentIndexChanged.connect(lambda _i: self._set("input", pick.currentData()))
            transition = d.get("transition")
            current = transition if transition == "cut" else (transition.get("kind") if hasattr(transition, "get") else "")
            how = _combo(TRANSITION_LABELS, current)
            duration = _spin(0.0, 60.0, _num(transition.get("duration"), 1.0) if hasattr(transition, "get") else 1.0, " s")
            duration.setVisible(how.currentData() not in ("", "cut"))
            how.currentIndexChanged.connect(lambda _i: self._set_transition(how, duration))
            duration.valueChanged.connect(lambda _v: self._set_transition(how, duration))
            self._add(pick, self._text("with"), how, duration)
        elif kind in ("overlay", "overlay_clear"):  # (Send to Overlay: a copy on top; Clear: that input's copies)
            pick = _combo(inputs, d.get("input"))
            pick.currentIndexChanged.connect(lambda _i: self._set("input", pick.currentData()))
            self._add(pick)
        elif kind == "overlay_clear_all":
            self._add(self._text("every copy goes, the way it came in"))
        elif kind == "release_console":
            self._add(self._text("every light the Console holds fades back to the inputs (Settings: Release fades over)"))
        elif kind == "house":
            light = _combo([(h.fixture, h.label) for h in show.house], d.get("light"))
            light.currentIndexChanged.connect(lambda _i: self._set("light", light.currentData()))
            level = _spin(0.0, 100.0, _num(d.get("level", 1.0), 0.0) * 100, " %", 0)
            level.valueChanged.connect(lambda v: self._set("level", round(v / 100, 3)))
            switch = _combo(((True, "switch on"), (False, "switch off"), (None, "leave the switch")), d.get("on", True if d.get("level") is not None else None))
            switch.currentIndexChanged.connect(lambda _i: self._set("on", switch.currentData()) if switch.currentData() is not None else self._drop("on"))
            self._add(light, level, switch, *self._fade_widgets())
        elif kind == "master":
            level = _spin(0.0, 100.0, _num(d.get("level"), 1.0) * 100, " %", 0)
            level.valueChanged.connect(lambda v: self._set("level", round(v / 100, 3)))
            self._add(level, *self._fade_widgets())
        elif kind in ("cut_black", "fade_black"):
            widgets = []
            if kind == "fade_black":
                seconds = _spin(0.0, 60.0, _num(d.get("fade"), 2.0), " s", 1)
                seconds.setToolTip("How long the fade to black takes")
                seconds.valueChanged.connect(lambda v: self._set("fade", round(v, 2)))
                widgets.append(seconds)
            everything = QCheckBox("Festoon and Key lights too")
            everything.setChecked(d.get("all") is True)
            everything.setToolTip("Off: only the colour lights go to black. On: the Festoon and Key lights too "
                                  "(their switches go off; their levels stay)")
            everything.toggled.connect(lambda on: self._set("all", True) if on else self._drop("all"))
            widgets.append(everything)
            self._add(*widgets)
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
            others = [(a.id, a.name) for a in self.page.container().ordered_automations() if a.id != self.page.selected]
            pick = _combo(others, d.get("automation"))
            pick.currentIndexChanged.connect(lambda _i: self._set("automation", pick.currentData()))
            on = _combo(((True, "on"), (False, "off")), bool(d.get("enabled", False)))
            on.currentIndexChanged.connect(lambda _i: self._set("enabled", on.currentData()))
            self._add(self._text("turn"), pick, on)
        delay = _spin(0.0, 3600.0, _num(d.get("delay"), 0.0), " s", 0)
        delay.setToolTip("Wait this long first (0 = straight away)")
        delay.valueChanged.connect(lambda v: self._set("delay", v) if v else self._drop("delay"))
        self._add(self._text("after"), delay)

    def _fade_widgets(self) -> list[QWidget]:
        fade = _spin(0.0, 600.0, _num(self.data.get("fade"), 0.0), " s fade", 1)
        fade.valueChanged.connect(lambda v: self._set("fade", v) if v else self._drop("fade"))
        return [fade]

    def _toggle_day(self, code: str, on: bool) -> None:
        days = [d for d in DAYS if d in (self.data.get("days") or ()) and d != code] + ([code] if on else [])
        self._set("days", [d for d in DAYS if d in days])
        if self._no_days_note is not None:
            self._no_days_note.setVisible(not days)

    def _toggle_week(self, code: str, on: bool) -> None:
        """A week of the month on or off (none: every week, and the key is left out, as before)."""
        weeks = [w for w in WEEKS if w in (self.data.get("weeks") or ()) and w != code] + ([code] if on else [])
        weeks = [w for w in WEEKS if w in weeks]
        if weeks:
            self._set("weeks", weeks)
        else:
            self._drop("weeks")
        blocked = self._every_week.blockSignals(True)
        self._every_week.setChecked(not weeks)
        self._every_week.blockSignals(blocked)

    def _toggle_every_week(self, on: bool) -> None:
        if on:  # every week: the picked weeks go
            for button in self._week_buttons.values():
                blocked = button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(blocked)
            self._drop("weeks")
        elif not self.data.get("weeks"):  # (it stays on until a week is picked)
            blocked = self._every_week.blockSignals(True)
            self._every_week.setChecked(True)
            self._every_week.blockSignals(blocked)

    def _set_date(self, key: str, day: QSpinBox, month: QComboBox) -> None:
        last = calendar.monthrange(2024, int(month.currentData()))[1]  # (a leap year: 29 Feb is allowed)
        if day.maximum() != last:
            day.setMaximum(last)  # e.g. 31 Mar -> April makes it 30 Apr, never "04-31"
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
    scope_changed = Signal()  # the selection moved between the global automations and the show's (undo follows it)
    import_requested = Signal()  # Import from another show… (show automations)

    def __init__(self, document: ShowDocument, engine: Any, sources_provider: Callable[[], list[str]] | None = None,
                 parent: QWidget | None = None, *, global_document: ShowDocument | None = None,
                 shows_provider: Callable[[], list] | None = None) -> None:
        """global_document: the global automations' document (None: no global list). shows_provider: the shows there
        are ([ShowInfo]), for "Load a show" and "This show is loaded"."""
        super().__init__(parent)
        self.setObjectName("AutomationPage")
        self.document = document
        self.global_document = global_document
        self.engine = engine
        self._sources = sources_provider or (lambda: [])
        self._shows = shows_provider or (lambda: [])
        self.default_show = ""  # Settings' default show (M14: the week schedule fills the gaps with it)
        self.scope = SHOW  # which list the selected automation is in
        self.selected = ""
        self._shown_key: tuple | None = None  # what refresh() last showed
        self._structure: tuple = ()
        self._view = AutomationsView()
        self._global_view = AutomationsView()
        self._list_keys: dict[str, tuple] = {SHOW: (), GLOBAL: ()}
        self._last_upcoming = 0.0
        self._choices: tuple[float, list[tuple[str, str]]] = (-1e9, [])

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

        # --- left: the global list, the show's list, and what's coming up
        self.global_panel = Panel("Global automations")
        self.global_list = self._make_list("GlobalAutomationList", GLOBAL)
        self.global_list.setToolTip("App-wide automations: they can load a show, cut or fade to black, and set Master and "
                                    "the Festoon and Key lights. Each second they're checked before the show's own.")
        self.global_panel.body.addWidget(self.global_list, 1)
        self.new_global_button = QPushButton("+ New global automation")
        self.new_global_button.setProperty("accent", True)
        self.new_global_button.clicked.connect(lambda: self.new_automation(GLOBAL))
        self.global_up = QPushButton("Up")
        self.global_up.setToolTip("Earlier in the list (runs before the ones below when they fire together)")
        self.global_up.clicked.connect(lambda: self.move(-1, GLOBAL))
        self.global_down = QPushButton("Down")
        self.global_down.clicked.connect(lambda: self.move(1, GLOBAL))
        self.global_panel.body.addWidget(self._button_row(self.new_global_button, self.global_up, self.global_down))
        self.global_panel.setVisible(global_document is not None)

        self.show_panel = Panel()
        self.show_title = ElidedLabel("THIS SHOW")  # "This show (Morning)": a long name is shortened with "…"
        self.show_title.setObjectName("PanelTitle")
        self.show_panel.body.addWidget(self.show_title)
        self.list = self._make_list("AutomationList", SHOW)
        self.show_panel.body.addWidget(self.list, 1)
        self.new_button = QPushButton("+ New automation")
        self.new_button.setProperty("accent", True)
        self.new_button.clicked.connect(lambda: self.new_automation(SHOW))
        self.up_button = QPushButton("Up")
        self.up_button.setToolTip("Earlier in the list (runs before the ones below when they fire together)")
        self.up_button.clicked.connect(lambda: self.move(-1, SHOW))
        self.down_button = QPushButton("Down")
        self.down_button.clicked.connect(lambda: self.move(1, SHOW))
        self.import_button = QPushButton("From another show…")
        self.import_button.setToolTip("Import automations from another show (and the inputs they send)")
        self.import_button.clicked.connect(self.import_requested.emit)
        self.show_panel.body.addWidget(self._button_row(self.new_button, self.up_button, self.down_button, self.import_button))

        upcoming_panel = Panel("Coming up (next 7 days)")
        self.upcoming = QListWidget()
        self.upcoming.setObjectName("UpcomingList")
        self.upcoming.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.upcoming.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.upcoming.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        upcoming_panel.body.addWidget(self.upcoming, 1)
        self.left = ResettableSplitter(Qt.Orientation.Vertical, self._default_left_split)
        self.left.addWidget(self.global_panel)
        self.left.addWidget(self.show_panel)
        self.left.addWidget(upcoming_panel)
        for index, stretch in enumerate((1, 2, 1)):
            self.left.setStretchFactor(index, stretch)

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
        # the week schedule of shows (M13): read-only, worked out from the global automations' "Load a show"; clicking a
        # block opens the automation behind it
        self.schedule = ScheduleView()
        self.schedule.setToolTip("When each show is loaded, from the global automations' “Load a show” (read-only). "
                                 "Click a block to open the automation behind it.")
        self.schedule.automation_clicked.connect(self._schedule_clicked)
        self.right_tabs = QTabWidget()
        self.right_tabs.setObjectName("AutomationTabs")
        self.right_tabs.addTab(self.editor_scroll, "Automation")
        self.right_tabs.addTab(self.schedule, "Week schedule")
        self.right_tabs.setTabToolTip(1, "Which show is loaded when, this week and the weeks ahead (from the global "
                                         "automations)")
        self.right_tabs.setTabVisible(1, global_document is not None)
        self.right_tabs.currentChanged.connect(lambda _index: self._fill_schedule())
        self.right = ResettableSplitter(Qt.Orientation.Vertical, self._default_right_split)
        self.right.addWidget(self.right_tabs)
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
        document.changed.connect(lambda _show: self.refresh())
        if global_document is not None:
            global_document.changed.connect(lambda _show: self.refresh())
        self.refresh()

    def _make_list(self, name: str, scope: str) -> QListWidget:
        view = QListWidget()
        view.setObjectName(name)
        view.setWordWrap(False)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.currentItemChanged.connect(lambda item, _previous=None, s=scope: self._picked(item, s))
        view.itemChanged.connect(lambda item, s=scope: self._ticked(item, s))
        return view

    @staticmethod
    def _button_row(*buttons: QPushButton) -> QWidget:
        row = QWidget()
        flow = FlowLayout(row, spacing=6)
        for button in buttons:
            flow.addWidget(button)
        return row

    # ------------------------------------------------------------ splits

    def _default_split(self) -> list[int]:
        total = max(1, self.splitter.width())
        left = min(420, max(300, total // 3))
        return [left, total - left]

    def _default_left_split(self) -> list[int]:
        total = max(1, self.left.height())
        coming = max(120, total // 4)
        global_part = max(140, (total - coming) // 3) if self.global_document is not None else 0
        return [global_part, total - coming - global_part, coming]

    def _default_right_split(self) -> list[int]:
        total = max(1, self.right.height())
        return [total - max(130, total // 4), max(130, total // 4)]

    def apply_split(self, sizes: dict | None) -> None:
        sizes = sizes if isinstance(sizes, dict) else {}
        for splitter, key in ((self.splitter, "outer"), (self.left, "left"), (self.right, "right")):
            saved = sizes.get(key)
            if splitter is self.left and isinstance(saved, list) and len(saved) != self.left.count():
                saved = None  # (saved before the global list was there)
            splitter.apply_sizes(saved)

    def splits(self) -> dict:
        return {"outer": self.splitter.sizes(), "left": self.left.sizes(), "right": self.right.sizes()}

    # ------------------------------------------------------------ which list

    @property
    def show_data(self) -> Show:
        """The loaded show (its inputs, variables, layouts and house lights, for the rows)."""
        return self.document.show

    @property
    def active_document(self) -> ShowDocument:
        """The document of the list the selected automation is in."""
        return self.global_document if self.scope == GLOBAL and self.global_document is not None else self.document

    def container(self, scope: str | None = None) -> Show:
        """The show (or, for the global list, the global automations) holding a list's automations."""
        scope = self.scope if scope is None else scope
        if scope == GLOBAL and self.global_document is not None:
            return self.global_document.show
        return self.document.show

    def _document_for(self, scope: str) -> ShowDocument:
        return self.global_document if scope == GLOBAL and self.global_document is not None else self.document

    def global_selected(self) -> bool:
        return self.scope == GLOBAL and self.global_document is not None

    def show_choices(self) -> list[tuple[str, str]]:
        """(id, name) of each show, for "Load a show" and "This show is loaded" (read at most every few seconds)."""
        now = time.monotonic()
        if now - self._choices[0] > 3.0:
            try:
                shows = [(info.id, info.name) for info in self._shows()]
            except Exception:
                shows = []
            self._choices = (now, shows)
        return self._choices[1]

    def _show_names(self) -> dict[str, str]:
        return dict(self.show_choices())

    def ndi_sources(self) -> list[str]:
        try:
            return list(self._sources())
        except Exception:
            return []

    def current(self) -> Automation | None:
        return self.container().automations.get(self.selected)

    def _select(self, scope: str, automation_id: str) -> None:
        moved = scope != self.scope
        self.scope = scope
        self.selected = automation_id
        if moved:
            self.scope_changed.emit()

    # ------------------------------------------------------------ showing

    def refresh(self) -> None:
        show = self.document.show
        globals_ = self.global_document.show if self.global_document is not None else None
        if self.selected not in self.container().automations:
            # the selected one has gone (deleted, or another show loaded): the first of its list, or of the other
            first = next(iter(self.container().ordered_automations()), None)
            if first is None:
                other = GLOBAL if self.scope == SHOW else SHOW
                first_other = next(iter(self.container(other).ordered_automations()), None)
                if first_other is not None and (other == SHOW or globals_ is not None):
                    self._select(other, first_other.id)
                else:
                    self.selected = ""
            else:
                self.selected = first.id
        key = (show.automations, show.automation_order, globals_.automations if globals_ else None,
               globals_.automation_order if globals_ else None, tuple((i.id, i.name) for i in show.ordered_inputs()),
               tuple(g.name for g in show.globals), tuple((lay.id, lay.name) for lay in show.layouts), show.house,
               show.name, show.folder, show.read_only, self.scope, self.selected)
        if key == self._shown_key:
            return  # nothing this tab shows has changed (e.g. an edit in the Editor)
        self._shown_key = key
        if show.no_show:  # (M14: the global automations above still run, and stay editable)
            self.show_title.setText("THIS SHOW (NO SHOW LOADED)")
            self.show_title.setToolTip("No show is loaded, so there are no show automations (the global ones still run)")
        else:
            self.show_title.setText(f"THIS SHOW ({show.name.upper()})")
            self.show_title.setToolTip(f"The automations of the loaded show, {show.name}")
        self._fill_lists()
        self._build_editor()
        self._fill_upcoming(force=True)
        self._fill_schedule()
        for widget in (self.new_button, self.up_button, self.down_button, self.import_button):
            widget.setEnabled(not show.read_only)
        globals_read_only = globals_ is None or globals_.read_only
        for widget in (self.new_global_button, self.global_up, self.global_down):
            widget.setEnabled(not globals_read_only)
        self.editor.setEnabled(not self.active_document.read_only)

    def set_show(self, show: Show) -> None:
        """(The show changed: the window and tests call this; it refreshes from the documents.)"""
        self.refresh()

    def _fill_lists(self) -> None:
        self._fill_list(SHOW, self.list, self._view)
        if self.global_document is not None:
            self._fill_list(GLOBAL, self.global_list, self._global_view)

    def _fill_list(self, scope: str, view: QListWidget, engine_view: AutomationsView) -> None:
        container = self.container(scope)
        shows = self._show_names()
        statuses = engine_view.statuses
        rows = []
        for automation in container.ordered_automations():
            status = statuses.get(automation.id)
            last = status.last_run if status else None
            state = " · paused by the loop guard" if status and status.tripped else ""
            rows.append((automation.id, automation.name, automation.enabled,
                         summary(automation, self.show_data if scope == SHOW else container, scope, shows),
                         f"last ran {_when(last)}{state}", bool(status and status.tripped)))
        selected = self.selected if self.scope == scope else ""
        key = tuple(rows) + (selected,)
        if key == self._list_keys[scope]:
            return
        self._list_keys[scope] = key
        blocked = view.blockSignals(True)
        ids = [row[0] for row in rows]
        if rows and [view.item(i).data(Qt.ItemDataRole.UserRole) for i in range(view.count())] == ids:
            # The same automations in the same order: the rows change in place. (A rebuild scrolled the list back to
            # the top, and on a click it came between the press and the release, so the release picked whatever was
            # under the pointer by then: the wrong automation, 6 Nov.)
            for index, (automation_id, name, enabled, text, last, tripped) in enumerate(rows):
                self._show_row(view.item(index), name, enabled, text, last, tripped)
                if automation_id == selected and view.currentItem() is not view.item(index):
                    view.setCurrentItem(view.item(index))
        else:
            scrolled = view.verticalScrollBar().value()
            view.clear()
            for automation_id, name, enabled, text, last, tripped in rows:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, automation_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                self._show_row(item, name, enabled, text, last, tripped)
                view.addItem(item)
                if automation_id == selected:
                    view.setCurrentItem(item)
            view.verticalScrollBar().setValue(scrolled)  # (kept: an automation added or moved doesn't jump it to the top)
        if not rows:
            empty = ("No global automations yet.\nThey can load a show at a set time, for example."
                     if scope == GLOBAL else "No show is loaded.\nLoad one from the Show menu to see its automations."
                     if self.show_data.no_show else "No automations in this show yet.\nClick + New automation to make one.")
            item = QListWidgetItem(empty)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            view.addItem(item)
        if not selected:
            view.clearSelection()
            view.setCurrentItem(None)
        view.blockSignals(blocked)
        read_only = container.read_only
        for button in ((self.up_button, self.down_button) if scope == SHOW else (self.global_up, self.global_down)):
            button.setEnabled(bool(selected) and not read_only)

    def _fill_upcoming(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_upcoming < 30:
            return
        self._last_upcoming = now
        self.upcoming.clear()
        found = [(moment, automation, others, False) for moment, automation, others in upcoming(self.show_data, datetime.now())]
        if self.global_document is not None:
            found += [(moment, automation, others, True) for moment, automation, others in upcoming(self.global_document.show, datetime.now())]
        found.sort(key=lambda item: item[0])
        for moment, automation, others, is_global in found[:30]:
            extra = ""
            if others:  # with Any, its other conditions being true already means it's running: no new start
                extra = (" (unless its other conditions are already true)" if automation.logic == "any"
                         else " (if its other conditions are true then)")
            label = "Global: " if is_global else ""
            self._add_line(self.upcoming, f"{moment:%a %d %b %H:%M}  {label}{automation.name}{extra}")
        if not found:
            self._add_line(self.upcoming, "Nothing in the next 7 days.")

    @staticmethod
    def _add_line(view: QListWidget, text: str) -> None:
        item = QListWidgetItem(text)
        item.setToolTip(text)  # shortened with "…" when the column is narrow; the tooltip has it all
        view.addItem(item)

    def _structure_key(self, automation: Automation | None) -> tuple:
        if automation is None:
            return ("none", self.scope)
        kinds = tuple((key, tuple(item.get("do") if key != "conditions" else item.get("type") for item in getattr(automation, key)))
                      for key in ("conditions", "actions", "on_end"))
        show = self.show_data
        names = (tuple(i.id for i in show.ordered_inputs()), tuple(g.name for g in show.globals),
                 tuple(lay.id for lay in show.layouts), tuple(self.container().automations), tuple(h.label for h in show.house),
                 tuple(self.show_choices()))
        return (self.scope, automation.id, automation.logic, kinds, names)

    def _build_editor(self, force: bool = False) -> None:
        automation = self.current()
        key = self._structure_key(automation)
        if key == self._structure and not force and self._rows_match(automation):
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
                                       "Warm welcome on Base, key lights 70 %”. Click + New automation to make one in this "
                                       "show, or + New global automation for one that loads a show at a set time."))
            self.editor_layout.addWidget(panel)
            self.editor_layout.addStretch(1)
            return
        is_global = self.scope == GLOBAL
        about = Panel("Global automation" if is_global else "Automation")
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
        scope = self.scope
        run = QPushButton("Run now")
        run.setToolTip("Do its actions now, whatever its conditions")
        run.clicked.connect(lambda: self.engine.submit(RunAutomation(automation.id, scope)))
        duplicate = QPushButton("Duplicate")
        duplicate.clicked.connect(self.duplicate)
        delete = QPushButton("Delete")
        delete.setProperty("danger", True)
        delete.clicked.connect(self.delete)
        self.reset_button = QPushButton("Let it run again")
        self.reset_button.setToolTip("The loop guard paused it after it ran too often; this lets it run again")
        self.reset_button.clicked.connect(lambda: self.engine.submit(ResetAutomation(automation.id, scope)))
        for button in (run, duplicate, delete, self.reset_button):
            flow.addWidget(button)
        about.body.addWidget(buttons)
        self.summary = QLabel(self._summary(automation))
        self.summary.setObjectName("Hint")
        self.summary.setWordWrap(True)
        about.body.addWidget(self.summary)
        if is_global:
            about.body.addWidget(_hint("Global automations are app-wide: they can load a show, cut or fade to black, and "
                                       "set Master and the Festoon and Key lights (found by name in whichever show is "
                                       "loaded). Each second they're checked before the loaded show's own."))
        self.editor_layout.addWidget(about)

        conditions = Panel("Run when")
        logic_row = QHBoxLayout()
        self.logic = _combo((("all", "all"), ("any", "any")), automation.logic)
        self.logic.currentIndexChanged.connect(lambda _i: self.change("Run when: " + self.logic.currentData(), logic=self.logic.currentData()))
        logic_row.addWidget(self.logic)
        logic_row.addWidget(QLabel("of these are true:"), 1)
        conditions.body.addLayout(logic_row)
        self._rule_rows(conditions, automation, "conditions")
        self._add_button(conditions, "+ Add a condition  ▾", conditions_for(self.scope), "conditions")
        conditions.body.addWidget(_hint("It runs once when this becomes true (not over and over while it stays true). "
                                        "At startup and after sleep it checks straight away, so it catches up."))
        self.editor_layout.addWidget(conditions)

        actions = Panel("Do this")
        self._rule_rows(actions, automation, "actions")
        self._add_button(actions, "+ Add an action  ▾", actions_for(self.scope), "actions")
        self.editor_layout.addWidget(actions)

        ending = Panel("When it stops matching, do this (optional)")
        self._rule_rows(ending, automation, "on_end")
        self._add_button(ending, "+ Add an action  ▾", actions_for(self.scope), "on_end")
        self.editor_layout.addWidget(ending)
        self.editor_layout.addStretch(1)
        self._refresh_editor(automation)

    def _summary(self, automation: Automation) -> str:
        container = self.container()
        return summary(automation, self.show_data if self.scope == SHOW else container, self.scope, self._show_names())

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

    def _rows_match(self, automation: Automation | None) -> bool:
        """Do the rows show what the automation holds? (Not after Undo, Redo or an outside edit: rebuild then,
        or a row's own copy would put the old values back with the next change.)"""
        if automation is None:
            return True
        for key in ("conditions", "actions", "on_end"):
            items, rows = list(getattr(automation, key)), self.rows.get(key, [])
            if len(items) != len(rows) or any(_plain(row.data) != _plain(item) for row, item in zip(rows, items)):
                return False
        return True

    def _refresh_editor(self, automation: Automation | None) -> None:
        if automation is None or not hasattr(self, "summary"):
            return
        self.summary.setText(self._summary(automation))
        if not self.name_edit.hasFocus():
            self.name_edit.setText(automation.name)
        if self.enabled_switch.isChecked() != automation.enabled:
            self.enabled_switch.setChecked(automation.enabled)
        view = self._global_view if self.scope == GLOBAL else self._view
        status = view.statuses.get(automation.id)
        self.reset_button.setVisible(bool(status and status.tripped))

    # ------------------------------------------------------------ engine frames

    @staticmethod
    def _show_row(item: QListWidgetItem, name: str, enabled: bool, text: str, last: str, tripped: bool) -> None:
        item.setText(f"{name}  ·  {last}\n{text}")
        item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        item.setToolTip(f"{name}\n{text}\n{last}\nTick = on")
        item.setData(Qt.ItemDataRole.ForegroundRole, QColor(Qt.GlobalColor.red) if tripped else None)

    def set_frame(self, frame: EngineFrame) -> None:
        view, global_view = frame.automations, frame.global_automations
        if view is self._view and global_view is self._global_view:
            return
        self._view, self._global_view = view, global_view
        if self.pause_switch.isChecked() != view.paused:
            self.pause_switch.setChecked(view.paused)
        self.pause_hint.setText(f"Paused since {_when(view.paused_since)}: nothing runs by itself until you resume"
                                if view.paused else "Automations run by themselves when their conditions are met")
        self.history.clear()
        entries = [(entry, False) for entry in view.history] + [(entry, True) for entry in global_view.history]
        entries.sort(key=lambda pair: pair[0].when, reverse=True)
        for entry, is_global in entries[:100]:
            detail = f" ({entry.detail})" if entry.detail else ""
            label = "Global: " if is_global else ""
            self._add_line(self.history, f"{_when(entry.when)}  {label}{entry.name} {entry.what}{detail}")
        if not entries:
            self._add_line(self.history, "Nothing has run yet.")
        self._fill_lists()
        self._refresh_editor(self.current())
        self._fill_upcoming()

    # ------------------------------------------------------------ edits (one undo step each)

    def change(self, text: str, merge_key: str | None = None, **changes: Any) -> bool:
        if not self.selected:
            return False
        return self.active_document.apply(update_automation(self.container(), self.selected, **changes), text, merge_key)

    def _renamed(self) -> None:
        automation = self.current()
        name = self.name_edit.text().strip()
        if automation is not None and name and name != automation.name:
            self.change(f"Rename “{automation.name}” → {name}", name=name)

    def _picked(self, item, scope: str) -> None:
        if item is None:
            return
        automation_id = item.data(Qt.ItemDataRole.UserRole)
        if automation_id and (automation_id != self.selected or scope != self.scope):
            self._select(scope, automation_id)
            self._build_editor()
            self._list_keys = {SHOW: (), GLOBAL: ()}
            self._fill_lists()
            self.right_tabs.setCurrentIndex(0)

    def _schedule_clicked(self, automation_id: str) -> None:
        """A block on the week schedule: open the global automation behind it."""
        if self.global_document is None or automation_id not in self.global_document.show.automations:
            return
        self._select(GLOBAL, automation_id)
        self._build_editor()
        self._list_keys = {SHOW: (), GLOBAL: ()}
        self._fill_lists()
        self.right_tabs.setCurrentIndex(0)

    def _fill_schedule(self) -> None:
        """The week schedule follows the global automations (only worked out while it's on screen)."""
        if self.global_document is not None and self.right_tabs.currentWidget() is self.schedule:
            self.schedule.set_data(self.global_document.show, self._show_names(), self.default_show)

    def set_default_show(self, show_id: str) -> None:
        """Settings' default show (M14): the week schedule fills the gaps with it."""
        self.default_show = show_id or ""
        self._fill_schedule()

    def _ticked(self, item, scope: str) -> None:
        automation_id = item.data(Qt.ItemDataRole.UserRole)
        container = self.container(scope)
        automation = container.automations.get(automation_id) if automation_id else None
        if automation is None:
            return
        on = item.checkState() == Qt.CheckState.Checked
        if on != automation.enabled:
            self._document_for(scope).apply(update_automation(container, automation_id, enabled=on),
                                            f"Turn “{automation.name}” {'on' if on else 'off'}")

    def new_automation(self, scope: str | None = None) -> str:
        scope = self.scope if scope is None else scope
        if scope == GLOBAL and self.global_document is None:
            return ""
        container = self.container(scope)
        show, automation_id = add_automation(container, "New global automation" if scope == GLOBAL else "New automation")
        self._select(scope, automation_id)
        made = self._document_for(scope).apply(show, "New global automation" if scope == GLOBAL else "New automation")
        return automation_id if made else ""

    def duplicate(self) -> None:
        automation = self.current()
        if automation is not None:
            show, new_id = duplicate_automation(self.container(), automation.id)
            self.selected = new_id
            self.active_document.apply(show, f"Duplicate “{automation.name}”")

    def delete(self) -> None:
        automation = self.current()
        if automation is None:
            return
        scope, document, container = self.scope, self.active_document, self.container()
        answer = QMessageBox.question(self, "Delete automation", f"Delete “{automation.name}”?\n\nCtrl+Z brings it back.")
        if answer == QMessageBox.StandardButton.Yes and automation.id in container.automations:
            document.apply(remove_automation(self.container(scope), automation.id), f"Delete “{automation.name}”")

    def move(self, step: int, scope: str | None = None) -> None:
        scope = self.scope if scope is None else scope
        if scope != self.scope:
            return  # (the selected automation is in the other list)
        automation = self.current()
        if automation is not None:
            self.active_document.apply(move_automation(self.container(), automation.id, step),
                                       f"Move “{automation.name}” {'up' if step < 0 else 'down'}")

    def add_rule(self, key: str, kind: str) -> None:
        automation = self.current()
        if automation is None:
            return
        if key != "conditions":
            item = default_action(kind, self.container(), self.scope, self.show_data, self.show_choices())
            label = actions_for(self.scope)[kind]
        else:
            item = default_condition(kind, self.container(), self.show_choices())
            label = conditions_for(self.scope)[kind]
        items = [dict(x) for x in getattr(automation, key)] + [item]
        self.change(f"“{automation.name}”: add {label.lower()}", **{key: items})

    def set_rule(self, key: str, index: int, data: dict) -> None:
        automation = self.current()
        if automation is None or index >= len(getattr(automation, key)):
            return
        items = [dict(x) for x in getattr(automation, key)]
        items[index] = data
        shows = self._show_names()
        what = (describe_condition(data, self.show_data, shows) if key == "conditions"
                else describe_action(data, self.show_data if self.scope == SHOW else self.container(), self.scope, shows))
        self.change(f"“{automation.name}”: {what}", f"automation/{self.scope}/{automation.id}/{key}/{index}", **{key: items})

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
