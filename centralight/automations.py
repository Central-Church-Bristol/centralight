"""Automations: the lights look after themselves.

An automation runs its actions once when its conditions become true, for example
"Sunday 09:00-12:30 -> Warm welcome on Base, key lights 70 %", and (optionally) other actions
when they stop being true. Conditions combine with All or Any, and each can be inverted.

When it runs:
- States (time of day, days, dates, what's live, variables, NDI, Blackout) are checked every
  second and straight away when something changes. Events (PC started, CentraLight started,
  PC woke) are only true at the moment they happen.
- An automation runs once when its combined condition goes from false to true; it doesn't repeat
  while it stays true. At startup and after waking everything is checked straight away, so a PC
  that starts at 10:00 on a Sunday still gets "Sunday 09:00-12:30" (catch-up).
- Automations that fire together run top to bottom, so later actions win. Manual control wins
  until the next automation runs (nothing re-applies an automation's look on its own).
- Loop guard: an automation that runs more than 5 times in 30 s is paused (and logged).
- Clock changes: a time skipped when the clocks go forward fires at the next valid minute; a
  time repeated when they go back fires once.

Everything here is plain Python (no Qt); the engine runs the AutomationRunner on its own thread.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

from .colours import is_colour_text, parse_colour
from .commands import (
    OverlayOff,
    SendToBase,
    SendToOverlay,
    SetBlackout,
    SetHouse,
    SetMaster,
    SetVariable,
)
from .log import log_info
from .model import TRANSITION_KINDS, Automation, Show, Transition

# ---------------------------------------------------------------- what there is

CONDITIONS: dict[str, str] = {
    "time": "Time of day",
    "days": "Days of the week",
    "dates": "Dates",
    "input_live": "An input is live",
    "variable": "A global variable",
    "ndi": "An NDI feed is there",
    "blackout": "Blackout is on",
    "audio": "Sound is loud enough",
    "app_started": "CentraLight starts",
    "pc_startup": "The PC starts",
    "pc_woke": "The PC wakes from sleep",
}
EVENTS = ("pc_startup", "app_started", "pc_woke")
ACTIONS: dict[str, str] = {
    "base": "Send an input to Base",
    "overlay": "An input in an overlay",
    "overlay_off": "Clear an overlay",
    "house": "Festoon or key lights",
    "master": "Master",
    "blackout": "Blackout",
    "variable": "Set a global variable",
    "layout": "Switch layout",
    "automation": "Turn an automation on or off",
}
GUI_ACTIONS = ("layout", "automation")  # these change the show, so the window does them
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WHERE = ("base", "overlay1", "overlay2", "overlay3", "overlay4", "anywhere")
COMPARES = ("=", "!=", "<", ">", "on", "off")
AUDIO_BANDS = ("level", "bass", "mid", "treble", "beat", "pitch")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

LOOP_RUNS = 5  # more than this many runs...
LOOP_WINDOW = 30.0  # ...in this many seconds pauses an automation
STARTUP_WINDOW = 600.0  # "The PC starts": CentraLight started within 10 minutes of Windows
RECENT = 600.0  # saved states older than this are forgotten at startup (so catch-up happens)
HISTORY_SIZE = 100
EVALUATE_EVERY = 1.0  # seconds
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DAY_MONTH = re.compile(r"^(\d{1,2})-(\d{1,2})$")


def parse_time(text: Any) -> int | None:
    """'09:30' -> minutes after midnight (570), or None."""
    match = TIME_PATTERN.match(text.strip()) if isinstance(text, str) else None
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def parse_day_month(text: Any) -> tuple[int, int] | None:
    """'12-24' (month-day) -> (12, 24), or None."""
    match = DAY_MONTH.match(text.strip()) if isinstance(text, str) else None
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    try:
        date(2024, month, day)  # (a leap year, so 02-29 is allowed)
    except ValueError:
        return None
    return month, day


# ---------------------------------------------------------------- checking the data


def check_condition(cond: Mapping[str, Any]) -> list[str]:
    """What's wrong with a condition, in plain words ([] = nothing)."""
    kind = cond.get("type")
    if kind not in CONDITIONS:
        return [f"unknown condition {kind!r} (use one of: {', '.join(CONDITIONS)})"]
    problems = []
    if not isinstance(cond.get("invert", False), bool):
        problems.append('"invert" must be true or false')
    if kind == "time":
        for key in ("from", "to"):
            if parse_time(cond.get(key)) is None:
                problems.append(f'time "{key}" must be like "09:30"')
    elif kind == "days":
        days = cond.get("days")
        if not isinstance(days, list) or not days or any(d not in DAYS for d in days):
            problems.append(f'"days" must be a list of {", ".join(DAYS)}')
    elif kind == "dates":
        for key in ("from", "to"):
            if parse_day_month(cond.get(key)) is None:
                problems.append(f'dates "{key}" must be month-day like "12-24"')
        year = cond.get("year")
        if year is not None and (not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= 2200):
            problems.append('"year" must be a year like 2026 (or left out for every year)')
    elif kind == "input_live":
        if not isinstance(cond.get("input"), str) or not cond.get("input"):
            problems.append('"input" must be an input id')
        if cond.get("where", "anywhere") not in WHERE:
            problems.append(f'"where" must be one of {", ".join(WHERE)}')
    elif kind == "variable":
        if not isinstance(cond.get("name"), str) or not cond.get("name"):
            problems.append('"name" must be a global variable')
        if cond.get("compare", "=") not in COMPARES:
            problems.append(f'"compare" must be one of {" ".join(COMPARES)}')
    elif kind == "ndi":
        if not isinstance(cond.get("source"), str) or not cond.get("source"):
            problems.append('"source" must be an NDI feed name')
    elif kind == "audio":
        if cond.get("band", "level") not in AUDIO_BANDS:
            problems.append(f'"band" must be one of {", ".join(AUDIO_BANDS)}')
        above = cond.get("above", 0.5)
        if not isinstance(above, (int, float)) or isinstance(above, bool) or not 0.0 <= above <= 1.0:
            problems.append('"above" must be a number from 0 to 1')
        seconds = cond.get("for", 0)
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 <= seconds <= 3600:
            problems.append('"for" must be a number of seconds')
    return problems


def check_action(action: Mapping[str, Any]) -> list[str]:
    kind = action.get("do")
    if kind not in ACTIONS:
        return [f"unknown action {kind!r} (use one of: {', '.join(ACTIONS)})"]
    problems = []
    delay = action.get("delay", 0)
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 86400:
        problems.append('"delay" must be seconds from 0 to 86400')
    if kind in ("base", "overlay") and (not isinstance(action.get("input"), str) or not action.get("input")):
        problems.append('"input" must be an input id')
    if kind == "base":
        transition = action.get("transition")
        if transition is not None and transition != "cut" and not (
            isinstance(transition, dict) and transition.get("kind") in TRANSITION_KINDS
            and isinstance(transition.get("duration", 1.0), (int, float))
        ):
            problems.append('"transition" must be "cut" or {"kind": "fade", "duration": 2}')
    if kind in ("overlay", "overlay_off") and action.get("slot") not in (1, 2, 3, 4):
        problems.append('"slot" must be 1, 2, 3 or 4')
    if kind == "overlay" and action.get("state", "toggle") not in ("on", "off", "toggle"):
        problems.append('"state" must be on, off or toggle')
    if kind == "house" and (not isinstance(action.get("light"), str) or not action.get("light")):
        problems.append('"light" must be a house light id (e.g. dimmer-4)')
    for key in ("level", "fade"):
        value = action.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            problems.append(f'"{key}" must be a number from 0')
    if kind == "master" and not isinstance(action.get("level"), (int, float)):
        problems.append('"level" must be a number from 0 to 1')
    if kind == "blackout" and not isinstance(action.get("on", True), bool):
        problems.append('"on" must be true or false')
    if kind == "variable" and (not isinstance(action.get("name"), str) or "value" not in action):
        problems.append('a variable action needs "name" and "value"')
    if kind == "layout" and not isinstance(action.get("layout"), str):
        problems.append('"layout" must be a saved layout id')
    if kind == "automation" and (not isinstance(action.get("automation"), str) or not isinstance(action.get("enabled", True), bool)):
        problems.append('needs "automation" (an id) and "enabled" (true or false)')
    return problems


# ---------------------------------------------------------------- describing them


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def describe_condition(cond: Mapping[str, Any], show: Show | None = None) -> str:
    kind = cond.get("type")
    text = CONDITIONS.get(str(kind), str(kind))
    if kind == "time":
        start, end = parse_time(cond.get("from")), parse_time(cond.get("to"))
        if start is not None and end is not None:
            text = f"{_hhmm(start)}–{_hhmm(end)}"
    elif kind == "days":
        days = [d for d in DAYS if d in (cond.get("days") or ())]
        text = "every day" if len(days) == 7 else ", ".join(DAY_NAMES[DAYS.index(d)] for d in days) or "no days"
    elif kind == "dates":
        a, b = parse_day_month(cond.get("from")), parse_day_month(cond.get("to"))
        if a and b:
            text = f"{a[1]} {MONTHS[a[0] - 1]} – {b[1]} {MONTHS[b[0] - 1]}" + (f" {cond['year']}" if cond.get("year") else "")
    elif kind == "input_live":
        inp = show.inputs.get(cond.get("input", "")) if show else None
        name = inp.name if inp else cond.get("input", "?")
        where = cond.get("where", "anywhere")
        place = {"base": "on Base", "anywhere": "live"}.get(where, f"in Overlay {where[-1]}")
        text = f"{name} is {place}"
    elif kind == "variable":
        compare = cond.get("compare", "=")
        name = cond.get("name", "?")
        text = f"{name} is {compare}" if compare in ("on", "off") else f"{name} {compare} {cond.get('value')}"
    elif kind == "ndi":
        text = f"{cond.get('source', '?')} is sending"
    elif kind == "audio":
        band = cond.get("band", "level")
        seconds = cond.get("for", 0)
        text = (f"sound ({band}) above {round(float(cond.get('above', 0.5)) * 100)} %"
                + (f" for {seconds:g} s" if isinstance(seconds, (int, float)) and seconds > 0 else ""))
    return f"not ({text})" if cond.get("invert") else text


def _transition_text(transition: Any) -> str:
    if transition == "cut":
        return " (cut)"
    if isinstance(transition, Mapping):
        return f" ({transition.get('kind')} {transition.get('duration', 1)} s)"
    return ""


def describe_action(action: Mapping[str, Any], show: Show | None = None) -> str:
    kind = action.get("do")

    def input_name(input_id: Any) -> str:
        inp = show.inputs.get(input_id) if show and isinstance(input_id, str) else None
        return inp.name if inp else str(input_id)

    if kind == "base":
        text = f"{input_name(action.get('input'))} to Base{_transition_text(action.get('transition'))}"
    elif kind == "overlay":
        state = action.get("state", "toggle")
        text = f"{input_name(action.get('input'))} {'in' if state == 'on' else 'out of' if state == 'off' else 'on / off in'} Overlay {action.get('slot')}"
    elif kind == "overlay_off":
        text = f"clear Overlay {action.get('slot')}"
    elif kind == "house":
        label = show.house_label(action.get("light", "")) if show else action.get("light", "?")
        parts = []
        if action.get("level") is not None:
            parts.append(f"{round(float(action['level']) * 100)} %")
        if action.get("on") is not None:
            parts.append("on" if action["on"] else "off")
        text = f"{label} {' '.join(parts) or 'unchanged'}"
    elif kind == "master":
        text = f"Master {round(float(action.get('level', 1.0)) * 100)} %"
    elif kind == "blackout":
        text = "Blackout on" if action.get("on", True) else "Blackout off"
    elif kind == "variable":
        text = f"{action.get('name')} = {action.get('value')}"
    elif kind == "layout":
        layout = show.layout(action.get("layout")) if show else None
        text = f"layout {layout.name if layout else action.get('layout')}"
    elif kind == "automation":
        other = show.automations.get(action.get("automation", "")) if show else None
        text = f"{'turn on' if action.get('enabled', True) else 'turn off'} “{other.name if other else action.get('automation')}”"
    else:
        text = str(kind)
    delay = action.get("delay", 0)
    return f"after {delay:g} s: {text}" if delay else text


def summary(automation: Automation, show: Show | None = None) -> str:
    """One line for the list: 'Sun, 09:00–12:30 → Warm welcome to Base, Key lights 70 %'."""
    joiner = " and " if automation.logic == "all" else " or "
    when = joiner.join(describe_condition(c, show) for c in automation.conditions) or "only when run by hand"
    actions = ", ".join(describe_action(a, show) for a in automation.actions) or "nothing yet"
    return f"{when} → {actions}"


# ---------------------------------------------------------------- what the conditions look at


@dataclass(frozen=True)
class Situation:
    """Everything conditions can look at, gathered by the engine for one check."""

    now: float  # wall clock, seconds since 1970
    local: datetime  # local time (UK, with the clock changes)
    base: str = ""  # input on Base
    overlays: tuple[str, ...] = ("", "", "", "")  # input showing in each overlay ("" = none)
    variables: Mapping[str, Any] = field(default_factory=dict)
    ndi: Mapping[str, bool] = field(default_factory=dict)  # feed name (lower case) -> sending now
    blackout: bool = False
    audio: Mapping[str, float] = field(default_factory=dict)  # audio.level and friends, now
    events: frozenset[str] = frozenset()  # app_started, pc_startup, pc_woke happening now
    previous_local: datetime | None = None  # local time at the last check (to spot clock changes)


def _in_range(start: int, end: int, minutes: int) -> bool:
    if start == end:
        return True  # 09:00-09:00 = all day
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end  # overnight, e.g. 22:00-02:00


def range_instance(start: int, end: int, local: datetime) -> tuple[date, int]:
    """Which occurrence of a time range local falls in: (the day it started, its start)."""
    minutes = local.hour * 60 + local.minute
    if start > end and minutes < end:
        return (local.date() - timedelta(days=1), start)
    return (local.date(), start)


def _skipped_start(start: int, previous: datetime | None, local: datetime) -> bool:
    """True if the clocks jumped forward over this start time since the last check."""
    if previous is None or local <= previous or local - previous < timedelta(minutes=30):
        return False
    probe = previous.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while probe <= local:
        if probe.hour * 60 + probe.minute == start:
            return True
        probe += timedelta(minutes=1)
    return False


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _variable_true(cond: Mapping[str, Any], current: Any) -> bool:
    compare = cond.get("compare", "=")
    if current is None:
        return False
    truthy = any(v != 0 for v in current) if isinstance(current, tuple) else bool(current)
    if compare == "on":
        return truthy
    if compare == "off":
        return not truthy
    target = cond.get("value")
    if isinstance(current, tuple):  # a colour
        text = "#" + str(target).strip().lstrip("#")
        wanted = parse_colour(text) if is_colour_text(text) else None
        same = wanted is not None and all(abs(a - b) < 0.003 for a, b in zip(current, wanted))
        return same if compare == "=" else not same if compare == "!=" else False
    number, wanted_number = _number(current), _number(target)
    if number is None or wanted_number is None:
        return False
    if compare == "=":
        return abs(number - wanted_number) < 1e-6
    if compare == "!=":
        return abs(number - wanted_number) >= 1e-6
    if compare == "<":
        return number < wanted_number
    return number > wanted_number


def _dates_true(cond: Mapping[str, Any], today: date) -> bool:
    start, end = parse_day_month(cond.get("from")), parse_day_month(cond.get("to"))
    if not start or not end:
        return False
    key = (today.month, today.day)
    wraps = start > end  # e.g. 1 Dec - 6 Jan
    inside = (start <= key or key <= end) if wraps else (start <= key <= end)
    year = cond.get("year")
    if inside and isinstance(year, int):
        started = today.year if not wraps or key >= start else today.year - 1  # the year the range began
        return started == year
    return inside


# ---------------------------------------------------------------- the runner


@dataclass
class AutomationState:
    matched: bool = False
    since: float = 0.0  # wall time it last changed
    last_run: float | None = None  # wall time
    runs: deque = field(default_factory=lambda: deque(maxlen=LOOP_RUNS + 1))  # engine times of recent runs
    tripped: bool = False  # paused by the loop guard
    seen: dict = field(default_factory=dict)  # condition index -> (range day, start) last entered
    inside: dict = field(default_factory=dict)  # condition index -> inside that time range at the last check


@dataclass(frozen=True)
class HistoryEntry:
    when: float  # wall time
    automation: str  # id
    name: str
    what: str  # "ran", "stopped matching", "run by hand", "paused by the loop guard"
    detail: str = ""  # which conditions matched, or why


@dataclass(frozen=True)
class AutomationStatus:
    """One automation as the screen shows it."""

    matched: bool = False
    last_run: float | None = None
    tripped: bool = False


class AutomationRunner:
    """Checks the automations and says which actions to do (the engine does them)."""

    def __init__(self) -> None:
        self.paused = False
        self.paused_since: float | None = None
        self.states: dict[str, AutomationState] = {}
        self.history: deque[HistoryEntry] = deque(maxlen=HISTORY_SIZE)
        self.pending: list[tuple[float, int, str, dict]] = []  # (due engine time, order, automation id, action)
        self._order = 0
        self._last_check = -1e9
        self._audio_since: dict[tuple[str, int], float] = {}  # (automation, condition) -> when the sound went above
        self._last_local: datetime | None = None

    # --- saving and restoring (live.json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "paused_since": self.paused_since,
            "states": {
                aid: {"matched": s.matched, "since": s.since, "last_run": s.last_run, "tripped": s.tripped}
                for aid, s in self.states.items()
            },
            "history": [
                {"when": h.when, "automation": h.automation, "name": h.name, "what": h.what, "detail": h.detail}
                for h in self.history
            ],
        }

    def restore(self, data: Mapping[str, Any] | None, now: float) -> None:
        """Pick up where the last run left off. Matched states older than RECENT are forgotten,
        so a long gap (the PC was off) catches up; a quick restart doesn't run things again."""
        if not isinstance(data, Mapping):
            return
        self.paused = bool(data.get("paused", False))
        since = data.get("paused_since")
        self.paused_since = float(since) if isinstance(since, (int, float)) and self.paused else None
        states = data.get("states")
        if isinstance(states, Mapping):
            for aid, saved in states.items():
                if not isinstance(saved, Mapping):
                    continue
                state = AutomationState()
                stamp = saved.get("since")
                stamp = float(stamp) if isinstance(stamp, (int, float)) else 0.0
                state.matched = bool(saved.get("matched")) and now - stamp < RECENT
                state.since = stamp
                last = saved.get("last_run")
                state.last_run = float(last) if isinstance(last, (int, float)) else None
                state.tripped = bool(saved.get("tripped"))
                self.states[str(aid)] = state
        for item in data.get("history") or ():
            if isinstance(item, Mapping) and isinstance(item.get("when"), (int, float)):
                self.history.append(HistoryEntry(float(item["when"]), str(item.get("automation", "")), str(item.get("name", "")),
                                                 str(item.get("what", "")), str(item.get("detail", ""))))

    # --- control

    def set_paused(self, paused: bool, now: float) -> None:
        if paused != self.paused:
            self.paused = paused
            self.paused_since = now if paused else None
            if paused:
                self.pending.clear()  # nothing waiting runs while paused
            log_info("Automations paused" if paused else "Automations resumed")

    def reset(self, automation_id: str) -> None:
        """Let an automation the loop guard paused run again."""
        state = self.states.get(automation_id)
        if state is not None:
            state.tripped = False
            state.runs.clear()
            state.matched = False

    def statuses(self) -> dict[str, AutomationStatus]:
        return {aid: AutomationStatus(s.matched, s.last_run, s.tripped) for aid, s in self.states.items()}

    def wants_check(self, mono_now: float) -> bool:
        return mono_now - self._last_check >= EVALUATE_EVERY

    # --- checking

    def condition_true(self, automation: Automation, index: int, cond: Mapping[str, Any], situation: Situation) -> bool:
        kind = cond.get("type")
        local = situation.local
        if kind in EVENTS:
            result = kind in situation.events
        elif kind == "time":
            result = self._time_true(automation, index, cond, situation)
        elif kind == "days":
            result = DAYS[local.weekday()] in (cond.get("days") or ())
        elif kind == "dates":
            result = _dates_true(cond, local.date())
        elif kind == "input_live":
            wanted, where = cond.get("input"), cond.get("where", "anywhere")
            if where == "base":
                result = situation.base == wanted
            elif where in WHERE[1:5]:
                result = situation.overlays[int(where[-1]) - 1] == wanted
            else:
                result = wanted == situation.base or wanted in situation.overlays
        elif kind == "variable":
            result = _variable_true(cond, situation.variables.get(cond.get("name", "")))
        elif kind == "ndi":
            result = bool(situation.ndi.get(str(cond.get("source", "")).lower()))
        elif kind == "blackout":
            result = situation.blackout
        elif kind == "audio":
            result = self._audio_true(automation, index, cond, situation)
        else:
            result = False
        return result != bool(cond.get("invert", False))

    def _audio_true(self, automation: Automation, index: int, cond: Mapping[str, Any], situation: Situation) -> bool:
        """Sound above a level, for at least N seconds (so a single loud moment doesn't count)."""
        key = (automation.id, index)
        value = float(situation.audio.get(str(cond.get("band", "level")), 0.0) or 0.0)
        if value <= float(cond.get("above", 0.5)):
            self._audio_since.pop(key, None)
            return False
        since = self._audio_since.setdefault(key, situation.now)
        return situation.now - since >= float(cond.get("for", 0) or 0)

    def _time_true(self, automation: Automation, index: int, cond: Mapping[str, Any], situation: Situation) -> bool:
        start, end = parse_time(cond.get("from")), parse_time(cond.get("to"))
        if start is None or end is None:
            return False
        state = self.states.setdefault(automation.id, AutomationState())
        local = situation.local
        minutes = local.hour * 60 + local.minute
        inside = _in_range(start, end, minutes)
        if not inside:
            state.inside[index] = False
            # clocks went forward over the start: run at the next valid minute (just this once)
            return _skipped_start(start, situation.previous_local, local)
        occurrence = range_instance(start, end, local)
        if not state.inside.get(index, False) and state.seen.get(index) == occurrence:
            return False  # the clocks went back into a range that already ran today: once is enough
        if not state.inside.get(index, False):
            state.seen[index] = occurrence
        state.inside[index] = True
        return True

    def matches(self, automation: Automation, situation: Situation) -> tuple[bool, list[str]]:
        """(combined result, descriptions of the conditions that were true)."""
        if not automation.conditions:
            return False, []
        results = [self.condition_true(automation, i, c, situation) for i, c in enumerate(automation.conditions)]
        true = [describe_condition(c) for c, r in zip(automation.conditions, results) if r]
        combined = all(results) if automation.logic == "all" else any(results)
        return combined, true

    def check(self, show: Show, situation: Situation, mono_now: float, show_for_text: Show | None = None) -> list[tuple[str, dict]]:
        """One check of every automation, top to bottom. Returns actions to do now, in order
        (delayed ones are kept, and come back from due() when it's time)."""
        self._last_check = mono_now
        if situation.previous_local is None and self._last_local is not None:
            situation = replace(situation, previous_local=self._last_local)
        self._last_local = situation.local
        now_actions: list[tuple[str, dict]] = []
        known = set(show.automations)
        for aid in [a for a in self.states if a not in known]:
            del self.states[aid]
        for automation in show.ordered_automations():
            state = self.states.setdefault(automation.id, AutomationState())
            if not automation.enabled or state.tripped:
                state.matched = False  # turned on again later while true = it runs then
                continue
            matched, true = self.matches(automation, situation)
            if matched == state.matched:
                continue
            state.matched = matched
            state.since = situation.now
            if self.paused:
                continue  # paused: keep track quietly, run nothing
            if matched:
                now_actions += self._run(automation, list(automation.actions), mono_now, situation.now, "ran", ", ".join(true))
            elif automation.on_end:
                now_actions += self._run(automation, list(automation.on_end), mono_now, situation.now, "stopped matching")
        return now_actions

    def run_now(self, automation: Automation, mono_now: float, wall_now: float) -> list[tuple[str, dict]]:
        """The Run now button (and the API): its actions, whatever the conditions."""
        return self._run(automation, list(automation.actions), mono_now, wall_now, "run by hand", count=False)

    def _run(self, automation: Automation, actions: list, mono_now: float, wall_now: float, what: str,
             detail: str = "", count: bool = True) -> list[tuple[str, dict]]:
        state = self.states.setdefault(automation.id, AutomationState())
        if count:
            state.runs.append(mono_now)
            recent = [t for t in state.runs if mono_now - t <= LOOP_WINDOW]
            if len(recent) > LOOP_RUNS:
                state.tripped = True
                state.matched = False
                self.pending = [p for p in self.pending if p[2] != automation.id]
                self.history.append(HistoryEntry(wall_now, automation.id, automation.name, "paused by the loop guard",
                                                 f"it ran {len(recent)} times in {LOOP_WINDOW:g} s"))
                log_info(f"Automation “{automation.name}” ran {len(recent)} times in {LOOP_WINDOW:g} s, so it's been paused (loop guard)")
                return []
        state.last_run = wall_now
        self.history.append(HistoryEntry(wall_now, automation.id, automation.name, what, detail))
        log_info(f"Automation “{automation.name}” {what}" + (f" ({detail})" if detail else ""))
        now_actions = []
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            delay = action.get("delay", 0)
            delay = float(delay) if isinstance(delay, (int, float)) and not isinstance(delay, bool) else 0.0
            if delay > 0:
                self._order += 1
                self.pending.append((mono_now + delay, self._order, automation.id, dict(action)))
            else:
                now_actions.append((automation.id, dict(action)))
        return now_actions

    def due(self, mono_now: float) -> list[tuple[str, dict]]:
        """Delayed actions whose time has come, in order."""
        if not self.pending:
            return []
        ready = sorted(p for p in self.pending if p[0] <= mono_now)
        if ready:
            self.pending = [p for p in self.pending if p[0] > mono_now]
        return [(aid, action) for _due, _order, aid, action in ready]


# ---------------------------------------------------------------- doing the actions


def action_command(action: Mapping[str, Any], show: Show) -> Any | None:
    """The engine command for an action (None for actions the window does, or bad ones)."""
    kind = action.get("do")
    fade = action.get("fade")
    fade = float(fade) if isinstance(fade, (int, float)) and not isinstance(fade, bool) else None
    if kind == "base":
        input_id = action.get("input")
        if input_id not in show.inputs:
            return None
        transition = action.get("transition")
        if transition == "cut":
            return SendToBase(input_id, cut=True)
        if isinstance(transition, Mapping) and transition.get("kind") in TRANSITION_KINDS:
            own = show.inputs[input_id].transition
            duration = transition.get("duration", own.duration)
            return SendToBase(input_id, transition=Transition(transition["kind"], float(duration), own.direction, own.softness))
        return SendToBase(input_id)
    if kind == "overlay":
        if action.get("input") not in show.inputs or action.get("slot") not in (1, 2, 3, 4):
            return None
        state = action.get("state", "toggle")
        return SendToOverlay(action["slot"], action["input"], None if state == "toggle" else state)
    if kind == "overlay_off":
        return OverlayOff(action["slot"]) if action.get("slot") in (1, 2, 3, 4) else None
    if kind == "house":
        light = action.get("light")
        if not any(h.fixture == light for h in show.house):
            return None
        level = action.get("level")
        level = min(1.0, max(0.0, float(level))) if isinstance(level, (int, float)) and not isinstance(level, bool) else None
        on = action.get("on")
        on = on if isinstance(on, bool) else (True if level is not None else None)
        return SetHouse(light, level=level, on=on, fade=fade)
    if kind == "master":
        level = action.get("level", 1.0)
        level = min(1.0, max(0.0, float(level))) if isinstance(level, (int, float)) else 1.0
        return SetMaster(level) if fade is None else SetMaster(level, fade)
    if kind == "blackout":
        return SetBlackout(on=bool(action.get("on", True)))
    if kind == "variable":
        return SetVariable(str(action.get("name", "")), action.get("value"))
    return None


# ---------------------------------------------------------------- what's coming up


def upcoming(show: Show, start: datetime, days: int = 7, limit: int = 50) -> list[tuple[datetime, Automation, bool]]:
    """When enabled automations with time, day or date conditions will next start matching,
    over the next few days: (local time, automation, other conditions also have to be true).
    Only midnights and the times in time conditions can start a match, so only those are checked."""
    found: list[tuple[datetime, Automation, bool]] = []
    timed_kinds = ("time", "days", "dates")
    first_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for automation in show.ordered_automations():
        timed = [c for c in automation.conditions if c.get("type") in timed_kinds]
        if not automation.enabled or not timed:
            continue
        others = len(timed) < len(automation.conditions)
        probe = Automation(automation.id, automation.name, True, automation.logic, tuple(timed))
        minutes = {0} | {m for c in timed if c.get("type") == "time" for m in (parse_time(c.get("from")), parse_time(c.get("to"))) if m is not None}
        moments = sorted(first_day + timedelta(days=d, minutes=m) for d in range(days + 1) for m in minutes)
        for moment in moments:
            if moment <= start or moment > start + timedelta(days=days):
                continue
            runner = AutomationRunner()  # fresh each time: no memory of other moments
            before, _ = runner.matches(probe, Situation(now=0.0, local=moment - timedelta(minutes=1)))
            after, _ = runner.matches(probe, Situation(now=0.0, local=moment))
            if after and not before:
                found.append((moment, automation, others))
    found.sort(key=lambda item: item[0])
    return found[:limit]


def system_uptime() -> float:
    """Seconds since Windows started (for "The PC starts")."""
    try:
        import ctypes

        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:
        return time.monotonic()


# ---------------------------------------------------------------- editing (each returns a new Show)


def default_condition(kind: str, show: Show) -> dict[str, Any]:
    """A sensible starting condition of each kind."""
    if kind == "time":
        return {"type": "time", "from": "09:00", "to": "12:00"}
    if kind == "days":
        return {"type": "days", "days": ["sun"]}
    if kind == "dates":
        return {"type": "dates", "from": "12-01", "to": "12-31"}
    if kind == "input_live":
        first = next(iter(show.ordered_inputs()), None)
        return {"type": "input_live", "input": first.id if first else "", "where": "anywhere"}
    if kind == "variable":
        first = show.globals[0].name if show.globals else ""
        return {"type": "variable", "name": first, "compare": "=", "value": 1}
    if kind == "ndi":
        return {"type": "ndi", "source": "Media Background Pane"}
    if kind == "audio":
        return {"type": "audio", "band": "level", "above": 0.6, "for": 5}
    return {"type": kind}


def default_action(kind: str, show: Show) -> dict[str, Any]:
    """A sensible starting action of each kind."""
    first_input = next(iter(show.ordered_inputs()), None)
    input_id = first_input.id if first_input else ""
    if kind == "base":
        return {"do": "base", "input": input_id}
    if kind == "overlay":
        return {"do": "overlay", "input": input_id, "slot": 1, "state": "on"}
    if kind == "overlay_off":
        return {"do": "overlay_off", "slot": 1}
    if kind == "house":
        light = show.house[-1].fixture if show.house else ""
        return {"do": "house", "light": light, "level": 0.7, "fade": 2}
    if kind == "master":
        return {"do": "master", "level": 1.0, "fade": 2}
    if kind == "blackout":
        return {"do": "blackout", "on": True}
    if kind == "variable":
        var = show.globals[0] if show.globals else None
        return {"do": "variable", "name": var.name if var else "", "value": var.default if var else 0}
    if kind == "layout":
        return {"do": "layout", "layout": show.active_layout}
    if kind == "automation":
        other = next(iter(show.ordered_automations()), None)
        return {"do": "automation", "automation": other.id if other else "", "enabled": False}
    return {"do": kind}


def _unique_automation_id(show: Show, name: str) -> str:
    from .model import slugify

    base = slugify(name, "automation")
    candidate, n = base, 2
    while candidate in show.automations:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _unique_automation_name(show: Show, name: str) -> str:
    taken = {a.name.casefold() for a in show.automations.values()}
    candidate, n = name, 2
    while candidate.casefold() in taken:
        candidate = f"{name} {n}"
        n += 1
    return candidate


def _put(show: Show, automation: Automation, order: tuple[str, ...] | None = None) -> Show:
    return show.with_automations({**show.automations, automation.id: automation}, order)


def add_automation(show: Show, name: str = "New automation") -> tuple[Show, str]:
    """A new automation (Sunday 09:00-12:00, with nothing to do yet), at the bottom of the list."""
    name = _unique_automation_name(show, name)
    automation_id = _unique_automation_id(show, name)
    new = Automation(automation_id, name, True, "all", (
        _frozen(default_condition("days", show)), _frozen(default_condition("time", show))))
    order = tuple(a.id for a in show.ordered_automations()) + (automation_id,)
    return _put(show, new, order), automation_id


def duplicate_automation(show: Show, automation_id: str) -> tuple[Show, str]:
    original = show.automations[automation_id]
    name = _unique_automation_name(show, f"{original.name} copy")
    new_id = _unique_automation_id(show, name)
    order = [a.id for a in show.ordered_automations()]
    order.insert(order.index(automation_id) + 1, new_id)
    from dataclasses import replace as _replace

    return _put(show, _replace(original, id=new_id, name=name), tuple(order)), new_id


def update_automation(show: Show, automation_id: str, **changes: Any) -> Show:
    """Change an automation's name, enabled, logic, conditions, actions or on_end."""
    from dataclasses import replace as _replace

    current = show.automations.get(automation_id)
    if current is None:
        return show
    if "name" in changes:
        changes["name"] = " ".join(str(changes["name"]).split()) or current.name
    for key in ("conditions", "actions", "on_end"):
        if key in changes:
            changes[key] = tuple(_frozen(item) for item in changes[key])
    return _put(show, _replace(current, **changes))


def remove_automation(show: Show, automation_id: str) -> Show:
    automations = {k: v for k, v in show.automations.items() if k != automation_id}
    return show.with_automations(automations, tuple(i for i in show.automation_order if i != automation_id))


def move_automation(show: Show, automation_id: str, step: int) -> Show:
    """Up (-1) or down (+1) the list, which is also the order they run in."""
    order = [a.id for a in show.ordered_automations()]
    if automation_id not in order:
        return show
    index = order.index(automation_id)
    target = max(0, min(len(order) - 1, index + step))
    order.insert(target, order.pop(index))
    return show.with_automations(dict(show.automations), tuple(order))


def _frozen(item: Mapping[str, Any]) -> Mapping[str, Any]:
    from types import MappingProxyType

    def freeze(value: Any) -> Any:
        if isinstance(value, Mapping):
            return MappingProxyType({k: freeze(v) for k, v in value.items()})
        if isinstance(value, (list, tuple)):
            return tuple(freeze(v) for v in value)
        return value

    return freeze(item)
