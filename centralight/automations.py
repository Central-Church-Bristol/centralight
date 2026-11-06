"""Automations: the lights look after themselves.

An automation runs its actions once when its conditions become true, for example
"Sunday 09:00-12:30 -> Warm welcome on Base, key lights 70 %", and (optionally) other actions
when they stop being true. Conditions combine with All or Any, and each can be inverted.

When it runs:
- States (time of day, days, dates, what's live, variables, NDI, the colour lights being black) are checked every
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

import calendar
import itertools
import math
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

from .colours import is_colour_text, parse_colour
from .countdown import GRACE as COUNTDOWN_GRACE
from .countdown import WAIT as COUNTDOWN_WAIT
from .commands import (
    BLACK_FADE,
    ClearFromOverlay,
    ConsoleRelease,
    ClearOverlay,
    SendToBase,
    SendToOverlay,
    ToBlack,
    SetHouse,
    SetMaster,
    SetVariable,
)
from .log import log_info
from .model import NO_DEFAULT_SHOW, TRANSITION_KINDS, Automation, Show, Transition
from .sun import sun_minutes

# ---------------------------------------------------------------- what there is

CONDITIONS: dict[str, str] = {
    "time": "Time of day",
    "days": "Days of the week",
    "dates": "Dates",
    "sun": "Sunrise / sunset",
    "input_live": "An input is live",
    "variable": "A show variable",
    "ndi": "An NDI feed is there",
    "colour_black": "The colour lights are black",
    "audio": "Sound is loud enough",
    "show_loads": "When this show loads",
    "loaded_show": "This show is loaded",
    "app_started": "CentraLight starts",
    "pc_startup": "The PC starts",
    "pc_woke": "The PC wakes from sleep",
}
EVENTS = ("pc_startup", "app_started", "pc_woke", "show_loads")
ACTIONS: dict[str, str] = {
    "base": "Send an input to Base",
    "overlay": "Send an input to the Overlay",
    "overlay_clear": "Clear an input from the Overlay",
    "overlay_clear_all": "Clear the Overlay",
    "load_show": "Load a show",
    "house": "Festoon or key lights",
    "master": "Master",
    "cut_black": "Cut to black",
    "fade_black": "Fade to black",
    "release_console": "Release the Console",
    "variable": "Set a show variable",
    "layout": "Switch layout",
    "automation": "Turn an automation on or off",
}
# A show's automations, and the global ones (app-wide: they can load a show, but can't touch a show's inputs,
# Overlay, variables or layouts)
SHOW = "show"
GLOBAL = "global"
SHOW_CONDITIONS = ("time", "days", "dates", "sun", "input_live", "variable", "ndi", "colour_black", "audio", "show_loads")
GLOBAL_CONDITIONS = ("time", "days", "dates", "sun", "ndi", "audio", "colour_black", "loaded_show", "app_started",
                     "pc_startup", "pc_woke")
SHOW_ACTIONS = ("base", "overlay", "overlay_clear", "overlay_clear_all", "house", "master", "cut_black", "fade_black",
                "release_console", "variable", "layout",
                "automation")
GLOBAL_ACTIONS = ("load_show", "cut_black", "fade_black", "release_console", "master", "house", "automation")
GUI_ACTIONS = ("layout", "automation", "load_show")  # these change the show (or load another), so the window does them
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKS = ("1st", "2nd", "3rd", "4th", "last")  # Days of the week, which weeks of the month (M14; left out = every week)
WEEK_NAMES = {"1st": "1st", "2nd": "2nd", "3rd": "3rd", "4th": "4th", "last": "Last"}
WHERE = ("base", "overlay", "anywhere")
COMPARES = ("=", "!=", "<", ">", "on", "off")
AUDIO_BANDS = ("level", "bass", "mid", "treble", "beat", "pitch")
SUN_EVENTS = ("sunset", "sunrise")
SUN_WHEN = ("after", "before")
SUN_MINUTES = 180  # how far before or after sunrise / sunset a condition can be moved
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

LOOP_RUNS = 5  # more than this many runs...
LOOP_WINDOW = 30.0  # ...in this many seconds pauses an automation
STARTUP_WINDOW = 600.0  # "The PC starts": CentraLight started within 10 minutes of Windows
NOT_LOOKING = 120.0  # seconds between two checks that mean CentraLight wasn't looking (asleep, off, stuck)
RECENT = 600.0  # a match that ended while CentraLight was closed runs its "when it stops" only if it closed this recently
QUIET_HOLD = 2.0  # seconds of quiet before a sound condition counts as stopped (a gap between songs isn't)
REMEMBER_REMOVED = 3600.0  # a deleted automation's state is kept this long, so Undo doesn't run it again
TIMED_KINDS = ("time", "days", "dates", "sun")  # conditions that depend only on the clock
# conditions that loading a show doesn't change: only an automation with just these hands back to the default show (M14)
STEADY_KINDS = TIMED_KINDS + ("ndi", "audio")
HISTORY_SIZE = 100
EVALUATE_EVERY = 1.0  # seconds
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DAY_MONTH = re.compile(r"^(\d{1,2})-(\d{1,2})$")


def _num(value: Any, default: float) -> float:
    """A number from a file, or the default if it isn't one (a wrong value must never stop anything)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return float(value)


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


def conditions_for(scope: str = SHOW) -> dict[str, str]:
    """The conditions a show's automations (or the global ones) can have, with their names."""
    return {kind: CONDITIONS[kind] for kind in (GLOBAL_CONDITIONS if scope == GLOBAL else SHOW_CONDITIONS)}


def actions_for(scope: str = SHOW) -> dict[str, str]:
    """The actions a show's automations (or the global ones) can do, with their names."""
    names = {kind: ACTIONS[kind] for kind in (GLOBAL_ACTIONS if scope == GLOBAL else SHOW_ACTIONS)}
    if scope == GLOBAL:
        names["automation"] = "Turn a global automation on or off"
    return names


# ---------------------------------------------------------------- checking the data


def check_condition(cond: Mapping[str, Any], scope: str = SHOW) -> list[str]:
    """What's wrong with a condition, in plain words ([] = nothing)."""
    kind = cond.get("type")
    kinds = conditions_for(scope)
    if not isinstance(kind, str) or kind not in kinds:
        where = "a global automation" if scope == GLOBAL else "a show's automation"
        return [f"unknown condition {kind!r} for {where} (use one of: {', '.join(kinds)})"]
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
        weeks = cond.get("weeks")
        if weeks is not None and (not isinstance(weeks, list) or any(w not in WEEKS for w in weeks)):
            problems.append(f'"weeks" must be a list of {", ".join(WEEKS)} (or left out for every week)')
    elif kind == "dates":
        for key in ("from", "to"):
            if parse_day_month(cond.get(key)) is None:
                problems.append(f'dates "{key}" must be month-day like "12-24"')
        year = cond.get("year")
        if year is not None and (not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= 2200):
            problems.append('"year" must be a year like 2026 (or left out for every year)')
    elif kind == "sun":
        if cond.get("event", "sunset") not in SUN_EVENTS:
            problems.append('"event" must be "sunset" or "sunrise"')
        if cond.get("when", "after") not in SUN_WHEN:
            problems.append('"when" must be "after" or "before"')
        minutes = cond.get("minutes", 0)
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not -SUN_MINUTES <= minutes <= SUN_MINUTES:
            problems.append(f'"minutes" must be a whole number from -{SUN_MINUTES} to {SUN_MINUTES}')
    elif kind == "input_live":
        if not isinstance(cond.get("input"), str) or not cond.get("input"):
            problems.append('"input" must be an input id')
        if cond.get("where", "anywhere") not in WHERE:
            problems.append(f'"where" must be one of {", ".join(WHERE)}')
    elif kind == "variable":
        if not isinstance(cond.get("name"), str) or not cond.get("name"):
            problems.append('"name" must be a show variable')
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
    elif kind == "loaded_show":
        if not isinstance(cond.get("show"), str) or not cond.get("show"):
            problems.append('"show" must be a show\'s id (its folder\'s name, e.g. "sunday-morning")')
    return problems


def check_action(action: Mapping[str, Any], scope: str = SHOW) -> list[str]:
    kind = action.get("do")
    kinds = actions_for(scope)
    if not isinstance(kind, str) or kind not in kinds:
        where = "a global automation" if scope == GLOBAL else "a show's automation"
        return [f"unknown action {kind!r} for {where} (use one of: {', '.join(kinds)})"]
    problems = []
    delay = action.get("delay", 0)
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 86400:
        problems.append('"delay" must be seconds from 0 to 86400')
    if kind in ("base", "overlay", "overlay_clear") and (not isinstance(action.get("input"), str) or not action.get("input")):
        problems.append('"input" must be an input id')
    if kind == "overlay" and ("slot" in action or "state" in action):  # (Overlays 1-4, before M14: not misread)
        problems.append('there is one Overlay now: "slot" and "state" are from before M14 (use Send to Overlay or '
                        'Clear from Overlay)')
    if kind == "base":
        transition = action.get("transition")
        if transition is not None and transition != "cut" and not (
            isinstance(transition, dict) and transition.get("kind") in TRANSITION_KINDS
            and isinstance(transition.get("duration", 1.0), (int, float))
        ):
            problems.append('"transition" must be "cut" or {"kind": "fade", "duration": 2}')
    if kind == "house" and scope == GLOBAL:
        if not isinstance(action.get("label"), str) or not action.get("label", "").strip():
            problems.append('"label" must be a house light\'s name (e.g. "Festoon" or "Key lights")')
    elif kind == "house" and (not isinstance(action.get("light"), str) or not action.get("light")):
        problems.append('"light" must be a house light id (e.g. dimmer-4)')
    if kind == "load_show" and (not isinstance(action.get("show"), str) or not action.get("show", "").strip()):
        problems.append('"show" must be a show\'s id (its folder\'s name, e.g. "sunday-morning")')
    for key in ("level", "fade"):
        value = action.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            problems.append(f'"{key}" must be a number from 0')
    if kind == "master" and not isinstance(action.get("level"), (int, float)):
        problems.append('"level" must be a number from 0 to 1')
    if kind in ("cut_black", "fade_black") and not isinstance(action.get("all", False), bool):
        problems.append('"all" must be true or false (true: the Festoon and Key lights too)')
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


def describe_condition(cond: Mapping[str, Any], show: Show | None = None, shows: Mapping[str, str] | None = None) -> str:
    """shows: show id -> name (for "This show is loaded")."""
    kind = cond.get("type")
    text = CONDITIONS.get(str(kind), str(kind))
    if kind == "time":
        start, end = parse_time(cond.get("from")), parse_time(cond.get("to"))
        if start is not None and end is not None:
            text = f"{_hhmm(start)}–{_hhmm(end)}"
    elif kind == "days":
        chosen = cond.get("days")
        days = [d for d in DAYS if isinstance(chosen, (list, tuple)) and d in chosen]
        text = "every day" if len(days) == 7 else ", ".join(DAY_NAMES[DAYS.index(d)] for d in days) or "no days"
        weeks = chosen_weeks(cond)
        if weeks and days:  # "1st Wed", "1st & 3rd Sun", "Last Fri"
            which = " & ".join(WEEK_NAMES[w] for w in weeks)
            text = f"{which} week of the month" if len(days) == 7 else f"{which} {text}"
    elif kind == "dates":
        a, b = parse_day_month(cond.get("from")), parse_day_month(cond.get("to"))
        if a and b:
            text = f"{a[1]} {MONTHS[a[0] - 1]} – {b[1]} {MONTHS[b[0] - 1]}" + (f" {cond['year']}" if cond.get("year") else "")
    elif kind == "sun":
        minutes = cond.get("minutes", 0)
        minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else 0
        text = f"{cond.get('when', 'after')} {cond.get('event', 'sunset')}"
        if minutes:
            text += f" {'+' if minutes > 0 else '−'} {abs(minutes)} min"
    elif kind == "input_live":
        wanted = cond.get("input", "")
        inp = show.inputs.get(wanted) if show and isinstance(wanted, str) else None
        name = inp.name if inp else cond.get("input", "?")
        where = cond.get("where", "anywhere")
        place = {"base": "on Base", "overlay": "in the Overlay"}.get(where, "live")
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
        text = (f"sound ({band}) above {round(_num(cond.get('above'), 0.5) * 100)} %"
                + (f" for {seconds:g} s" if isinstance(seconds, (int, float)) and seconds > 0 else ""))
    elif kind == "loaded_show":
        wanted = str(cond.get("show", "?"))
        text = f"{(shows or {}).get(wanted, wanted)} is loaded"
    elif kind == "show_loads":
        text = "this show loads"
    return f"not ({text})" if cond.get("invert") else text


def _transition_text(transition: Any) -> str:
    if transition == "cut":
        return " (cut)"
    if isinstance(transition, Mapping):
        return f" ({transition.get('kind')} {transition.get('duration', 1)} s)"
    return ""


def describe_action(action: Mapping[str, Any], show: Show | None = None, scope: str = SHOW,
                    shows: Mapping[str, str] | None = None) -> str:
    """show: the show (or, for a global automation, the global automations: "turn on" names one of them).
    shows: show id -> name (for "Load a show")."""
    kind = action.get("do")

    def input_name(input_id: Any) -> str:
        inp = show.inputs.get(input_id) if show and isinstance(input_id, str) else None
        return inp.name if inp else str(input_id)

    if kind == "load_show":
        wanted = str(action.get("show", "?"))
        text = f"load the show {(shows or {}).get(wanted, wanted)}"
    elif kind == "close_show":
        text = "close the show (lights off)"
    elif kind == "house" and scope == GLOBAL:
        parts = []
        if action.get("level") is not None:
            parts.append(f"{round(_num(action['level'], 0.0) * 100)} %")
        if action.get("on") is not None:
            parts.append("on" if action["on"] else "off")
        text = f"{action.get('label', '?')} {' '.join(parts) or 'unchanged'}"
    elif kind == "base":
        text = f"{input_name(action.get('input'))} to Base{_transition_text(action.get('transition'))}"
    elif kind == "overlay":
        text = f"{input_name(action.get('input'))} to the Overlay"
    elif kind == "overlay_clear":
        text = f"clear {input_name(action.get('input'))} from the Overlay"
    elif kind == "overlay_clear_all":
        text = "clear the Overlay"
    elif kind == "release_console":
        text = "release the Console's lights (they fade back)"
    elif kind == "house":
        label = show.house_label(action.get("light", "")) if show else action.get("light", "?")
        parts = []
        if action.get("level") is not None:
            parts.append(f"{round(_num(action['level'], 0.0) * 100)} %")
        if action.get("on") is not None:
            parts.append("on" if action["on"] else "off")
        text = f"{label} {' '.join(parts) or 'unchanged'}"
    elif kind == "master":
        text = f"Master {round(_num(action.get('level'), 1.0) * 100)} %"
    elif kind in ("cut_black", "fade_black"):
        text = "cut to black" if kind == "cut_black" else f"fade to black ({_num(action.get('fade'), BLACK_FADE):g} s)"
        if action.get("all"):
            text += ", Festoon and Key lights too"
    elif kind == "variable":
        text = f"{action.get('name')} = {action.get('value')}"
    elif kind == "layout":
        layout = show.layout(action.get("layout")) if show else None
        text = f"layout {layout.name if layout else action.get('layout')}"
    elif kind == "automation":
        other_id = action.get("automation", "")
        other = show.automations.get(other_id) if show and isinstance(other_id, str) else None
        text = f"{'turn on' if action.get('enabled', True) else 'turn off'} “{other.name if other else action.get('automation')}”"
    else:
        text = str(kind)
    delay = _num(action.get("delay"), 0.0)
    return f"after {delay:g} s: {text}" if delay else text


def summary(automation: Automation, show: Show | None = None, scope: str = SHOW, shows: Mapping[str, str] | None = None) -> str:
    """One line for the list: 'Sun, 09:00–12:30 → Warm welcome to Base, Key lights 70 %'."""
    joiner = " and " if automation.logic == "all" else " or "
    when = joiner.join(describe_condition(c, show, shows) for c in automation.conditions) or "only when run by hand"
    actions = ", ".join(describe_action(a, show, scope, shows) for a in automation.actions) or "nothing yet"
    return f"{when} → {actions}"


# ---------------------------------------------------------------- what the conditions look at


@dataclass(frozen=True)
class Situation:
    """Everything conditions can look at, gathered by the engine for one check."""

    now: float  # wall clock, seconds since 1970
    local: datetime  # local time (UK, with the clock changes)
    base: str = ""  # input on Base
    overlay: frozenset[str] = frozenset()  # inputs with a copy on the Overlay (while its switch is on)
    variables: Mapping[str, Any] = field(default_factory=dict)
    ndi: Mapping[str, bool] = field(default_factory=dict)  # feed name (lower case) -> sending now
    colour_black: bool = False  # nothing on Base, the Overlay or the Console (after a Cut / Fade to black, say)
    audio: Mapping[str, float] = field(default_factory=dict)  # audio.level and friends, now
    events: frozenset[str] = frozenset()  # app_started, pc_startup, pc_woke happening now
    previous_local: datetime | None = None  # local time at the last check (to spot clock changes)
    previous_now: float | None = None  # wall clock at the last check (a clock change moves local time, not this)
    ndi_known: bool = True  # False just after starting: a feed that isn't sending yet may still be connecting
    quit_dark_since: float | None = None  # wall time of a "Turn lights off" quit whose hold is still on, else None
    show: str = ""  # the loaded show's id (for the global automations' "This show is loaded")
    show_names: frozenset[str] = frozenset()  # ...and what else it can be called: its folder's name and its name


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


def _skipped_start(start: int, previous: datetime | None, local: datetime, wall_jump: float | None = None) -> bool:
    """True if the clocks went forward (summer time) over this start time since the last check. After a sleep both
    clocks jump together: that isn't a clock change, and a range that ended meanwhile mustn't run."""
    if previous is None or wall_jump is None or local <= previous:
        return False
    if (local - previous) - timedelta(seconds=max(0.0, wall_jump)) < timedelta(minutes=30):
        return False
    probe = previous.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while probe <= local:
        if probe.hour * 60 + probe.minute == start:
            return True
        probe += timedelta(minutes=1)
    return False


def ends_on_its_own(automation: Automation) -> bool:
    """Does its own "when it stops matching" load a show, or Cut / Fade to black (with any delay)? Then nothing is loaded
    after it when it stops (M14): not the default show, and not a longer schedule's show either (loading one runs its
    "When this show loads" automations, which would light the room again)."""
    return any(isinstance(a, Mapping) and a.get("do") in ("load_show", "cut_black", "fade_black") for a in automation.on_end)


def schedules_a_show(automation: Automation) -> bool:
    """Does this automation load a show for as long as it matches (a schedule, a feed being lost …)? Not one that runs
    at a moment ("When CentraLight starts"): that never "stops matching" in a way that means anything. Nor one with a
    condition that loading a show changes (a show variable, an input being live, which show is loaded, the colour
    lights being black): its own load could make it stop matching, and the default show would come back, again and
    again."""
    return (any(isinstance(a, Mapping) and a.get("do") == "load_show" for a in automation.actions)
            and all(c.get("type") in STEADY_KINDS for c in automation.conditions))


def chosen_weeks(cond: Mapping[str, Any]) -> list[str]:
    """A Days of the week condition's weeks of the month, in order ([] = every week)."""
    weeks = cond.get("weeks")
    return [w for w in WEEKS if isinstance(weeks, (list, tuple)) and w in weeks]


def in_weeks(weeks: list[str], day: date) -> bool:
    """Is the day in one of these weeks of its month? 1st is the 1st to the 7th (so it holds the month's first Monday,
    first Tuesday …), 2nd the 8th to the 14th, 3rd the 15th to the 21st, 4th the 22nd to the 28th, and Last the month's
    last seven days: the last Wednesday is always "Last", whether it's the 4th or the 5th. No weeks: every week."""
    if not weeks:
        return True
    nth = (day.day - 1) // 7 + 1
    last = day.day + 7 > calendar.monthrange(day.year, day.month)[1]
    return any(last if week == "last" else WEEKS.index(week) + 1 == nth for week in weeks)


def range_day(conditions: Iterable[Mapping[str, Any]], local: datetime) -> date:
    """The day Days and Dates conditions look at: while an overnight time range (e.g. 23:00-01:00) is in its
    after-midnight part, the day it started; otherwise today."""
    minutes = local.hour * 60 + local.minute
    for cond in conditions:
        if cond.get("type") != "time":
            continue
        start, end = parse_time(cond.get("from")), parse_time(cond.get("to"))
        if start is not None and end is not None and start > end and minutes < end:
            return local.date() - timedelta(days=1)
    return local.date()


def change_moments(timed: list, first_day: datetime, days: int) -> list[datetime]:
    """Every moment from first_day (midnight) over `days` days at which these clock-only conditions can change:
    midnights, the times in time conditions, and each day's sunrise / sunset moment."""
    minutes = {0} | {m for c in timed if c.get("type") == "time"
                     for m in (parse_time(c.get("from")), parse_time(c.get("to"))) if m is not None}
    moments = {first_day + timedelta(days=d, minutes=m) for d in range(days + 1) for m in minutes}
    for d in range(days + 1):  # sunrise and sunset move a little every day
        day = (first_day + timedelta(days=d)).date()
        moments |= {first_day + timedelta(days=d, minutes=m) for c in timed if c.get("type") == "sun"
                    for m in (sun_threshold(c, day),) if m is not None and m < 24 * 60}
    return sorted(moments)


def timed_continuous(automation: Automation, start: datetime, end: datetime) -> bool:
    """Were the automation's clock-only conditions true all the way from `start` to `end`? If not, the occurrence
    it ran for is over and a new one may have begun (after a sleep, or while CentraLight was closed)."""
    timed = [c for c in automation.conditions if c.get("type") in TIMED_KINDS]
    if not timed:
        return True  # nothing about the clock says otherwise
    if end <= start:
        return True
    if end - start > timedelta(days=60):
        return False
    probe = Automation(automation.id, automation.name, True, automation.logic, tuple(timed))

    def true_at(moment: datetime) -> bool:
        return AutomationRunner().matches(probe, Situation(now=0.0, local=moment))[0]  # fresh: no memory

    first_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for moment in change_moments(timed, first_day, (end - first_day).days + 1):
        if start < moment <= end and not true_at(moment):
            return False
    return true_at(end)


STATE_KINDS = ("input_live", "variable", "ndi", "colour_black", "audio", "loaded_show")  # how things are right now


def _stop_cancels_waiting(automation: Automation, before: list[bool] | None, after: list[bool] | None) -> bool:
    """Does this stop cancel the waiting "after N s" steps of the automation's run? Yes if a condition about how
    things are (a feed, sound, a variable, an input, the colour lights being black) is what went false: the wait was
    a grace period ("feed lost for 10 s"). Yes if it has a "when it stops" to do instead, unless it's about an event
    such as "CentraLight starts", which only lasts a moment. No when only the clock moved on ("at 10:30 do this,
    then 2 minutes later that"), even if it also looks at a feed."""
    went_false = {c.get("type") for c, was, now in zip(automation.conditions, before or (), after or ()) if was and not now}
    if went_false & set(STATE_KINDS):
        return True
    return bool(automation.on_end) and not any(c.get("type") in EVENTS for c in automation.conditions)


def _comes_on_at_a_set_time(cond: Mapping[str, Any]) -> bool:
    """A time range (not all day), or a sunrise / sunset condition that comes on at the sun's time ("after sunset",
    "not before sunrise"). Days, dates and "before sunrise" come on at midnight instead."""
    kind = cond.get("type")
    if kind == "time":
        start, end = parse_time(cond.get("from")), parse_time(cond.get("to"))
        return start is not None and end is not None and start != end
    if kind == "sun":
        return (cond.get("when") == "before") == bool(cond.get("invert", False))
    return False


def began_after(automation: Automation, since: datetime, local: datetime) -> bool:
    """Is one of the automation's time ranges (or an "after sunset"-style time) on now, having come on after `since`?
    Only such a run lights things after a "Turn lights off" quit: Sunday 09:00-12:30 after a quit on
    Saturday. Never a whole day (Sundays, 1-31 Dec), a feed coming back at 03:00, or "CentraLight starts"."""
    if any(c.get("type") in EVENTS for c in automation.conditions):
        return False
    for cond in automation.conditions:
        if not _comes_on_at_a_set_time(cond):
            continue
        probe = Automation(automation.id, automation.name, True, "all", (cond,))

        def on_at(moment: datetime) -> bool:
            return AutomationRunner().matches(probe, Situation(now=0.0, local=moment))[0]  # fresh: no memory

        if on_at(local) and not (on_at(since) and timed_continuous(probe, since, local)):
            return True
    return False


def _ndi_true(source: Any, ndi: Mapping[str, bool]) -> bool:
    """A feed is there if a connected feed's name contains the one asked for, or the other way round (so the
    drop-down's full "PC (Media Background Pane)" and an input's "Media Background Pane" both work)."""
    wanted = str(source or "").strip().lower()
    return bool(wanted) and any(sending and (wanted in name or name in wanted) for name, sending in ndi.items() if name)


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


def sun_threshold(cond: Mapping[str, Any], day: date) -> int | None:
    """Minutes after midnight on `day` where a sunrise / sunset condition changes (0-1440), or None."""
    moment = sun_minutes(day, "sunrise" if cond.get("event") == "sunrise" else "sunset")
    minutes = cond.get("minutes", 0)
    if moment is None or not isinstance(minutes, int) or isinstance(minutes, bool):
        return None
    return max(0, min(24 * 60, moment + minutes))


def _sun_true(cond: Mapping[str, Any], local: datetime) -> bool:
    """After sunset = from sunset until midnight; before sunrise = from midnight until sunrise (Bristol's times).
    "At night" is Any(after sunset, before sunrise); "daytime" is All(after sunrise, before sunset)."""
    threshold = sun_threshold(cond, local.date())
    if threshold is None:
        return False
    minutes = local.hour * 60 + local.minute
    return minutes < threshold if cond.get("when") == "before" else minutes >= threshold


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
    restored: bool = False  # matched when CentraLight last stopped (checked against the clock at the first look)


@dataclass
class Expected:
    """A run the clock will start soon whose countdown has started early (so it lands on time)."""

    key: tuple[str, str, str]  # (automation id, "actions" / "on_end", the minute it runs)
    id: str  # "x3": the banner's buttons name it by this
    automation: str
    kind: str
    action: dict  # its counted step
    run_due: float  # engine time the run starts
    due: float  # engine time the counted step happens (the banner's zero)
    seen: float  # engine time it was last promised by the clock


def _delay(action: Mapping[str, Any]) -> float:
    delay = action.get("delay", 0)
    return float(delay) if isinstance(delay, (int, float)) and not isinstance(delay, bool) and delay > 0 else 0.0


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
        # actions waiting: (due engine time, order, automation id, action, why, run); "why" is "conditions" or "by hand"
        self.pending: list[tuple[float, int, str, dict, str, int]] = []
        self._order = 0
        self._runs = itertools.count(1)
        # the countdown before automatic changes (countdown.py): the engine sets these before each check
        self.countdown = 0.0  # seconds (0 = no countdown now: it's off, CentraLight has just started, or it's paused)
        self.counted: Callable[[Mapping[str, Any]], bool] = lambda _action: False  # which actions get one
        self.needless: Callable[[Mapping[str, Any]], bool] = lambda _action: False  # ...but not now: it changes nothing
        self.expected: dict[tuple[str, str, str], Expected] = {}  # runs the clock will start soon (their banner starts early)
        self._decisions: dict[tuple[str, str, str], tuple[str, float]] = {}  # Skip / Wait / Now pressed before a run came
        self._expected_numbers = itertools.count(1)
        self._last_check = -1e9
        self._audio_since: dict[tuple[str, int], float] = {}  # (automation, condition) -> when the sound went above
        self._audio_quiet: dict[tuple[str, int], float] = {}  # (automation, condition) -> when it went quiet
        self._last_local: datetime | None = None
        self._last_now: float | None = None
        self._removed: dict[str, tuple[AutomationState, float, Automation | None]] = {}  # deleted ones (for Undo)
        self._definitions: dict[str, Automation] = {}  # each automation as last checked
        self._results: dict[str, list[bool]] = {}  # each automation's conditions at the last check
        self._held_events: dict[str, frozenset[str]] = {}  # start events kept for automations waiting for the feeds
        self._quiet: set[str] = set()  # just imported: taken as they are at the next check, without running
        self._stopped_at: float | None = None  # when the last CentraLight saved its state (from live.json)
        # the default show (M14, global automations): it loads when the automation that loaded a show stops matching
        self.default_show = ""  # its id ("" = none: as before)
        self.loader: str | None = None  # the automation whose run loaded the show now (None: a person loaded one since)
        self._before: list[str] = []  # the automations whose shows the loader's had replaced, oldest first (M14)
        self._rerun: str | None = None  # (it runs again at its first check if it still matches: see rerun_loader)

    # --- saving and restoring (live.json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "paused_since": self.paused_since,
            **({"loader": self.loader} if self.loader else {}),
            **({"before": list(self._before)} if self._before else {}),
            "states": {
                aid: {"matched": s.matched, "since": s.since, "last_run": s.last_run, "tripped": s.tripped}
                for aid, s in self.states.items()
            },
            "history": [
                {"when": h.when, "automation": h.automation, "name": h.name, "what": h.what, "detail": h.detail}
                for h in self.history
            ],
        }

    def restore(self, data: Mapping[str, Any] | None, now: float, saved_at: float | None = None) -> None:
        """Pick up where the last run left off. An automation that was matched stays matched (so a restart in the
        middle of a service doesn't run it again over what someone chose since), unless the clock says its
        occurrence ended meanwhile: the first check sees to that, and catches up a new one."""
        if not isinstance(data, Mapping):
            return
        self._stopped_at = saved_at if isinstance(saved_at, (int, float)) and saved_at > 0 else None
        self.paused = bool(data.get("paused", False))
        self.loader = str(data["loader"]) if isinstance(data.get("loader"), str) and data["loader"] else None
        before = data.get("before")
        self._before = [str(a) for a in before if isinstance(a, str) and a] if isinstance(before, list) else []
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
                state.matched = bool(saved.get("matched")) and 0.0 < stamp <= now
                state.restored = state.matched
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

    def manual_load(self) -> None:
        """A person loaded a show (the picker, the Stream Deck, the phone page): it stays when the automation that
        loaded the one before stops matching, and a default show waiting for its countdown doesn't load (M14)."""
        self.loader = None
        self._before = []
        self._drop_defaults()

    def _drop_defaults(self) -> None:
        """A show waiting to load because a schedule ended (the default show, or a longer schedule's show) won't."""
        self.pending = [p for p in self.pending if not p[3].get("_default")]

    def rerun_loader(self) -> None:
        """The PC has just started and the default show opened instead of the last one (M14): the automation that had
        loaded the last show runs again at its first check if it still matches (as a catch-up does), so its show comes
        back. One that stopped meanwhile does what it does when it stops; a show someone had loaded by hand stays given
        up to the default show (no automation takes over from it)."""
        self._rerun = self.loader

    def set_paused(self, paused: bool, now: float) -> None:
        if paused != self.paused:
            self.paused = paused
            self.paused_since = now if paused else None
            if paused:
                self.pending.clear()  # nothing waiting runs while paused (a countdown included)
                self.expected.clear()
                self._decisions.clear()
            log_info("Automations paused" if paused else "Automations resumed")

    def quiet(self, automation_ids) -> None:
        """These were just imported: at the next check they take how things are as their start (nothing runs)."""
        self._quiet.update(automation_ids)

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

    def condition_true(self, automation: Automation, index: int, cond: Mapping[str, Any], situation: Situation,
                       day: date | None = None) -> bool:
        kind = cond.get("type")
        local = situation.local
        day = local.date() if day is None else day
        if kind in EVENTS:
            result = kind in situation.events
        elif kind == "time":
            result = self._time_true(automation, index, cond, situation)
        elif kind == "days":
            days = cond.get("days")
            result = isinstance(days, (list, tuple)) and DAYS[day.weekday()] in days and in_weeks(chosen_weeks(cond), day)
        elif kind == "dates":
            result = _dates_true(cond, day)
        elif kind == "sun":
            result = _sun_true(cond, local)
        elif kind == "input_live":
            wanted, where = cond.get("input"), cond.get("where", "anywhere")
            if where == "base":
                result = situation.base == wanted
            elif where == "overlay":
                result = wanted in situation.overlay
            else:
                result = wanted == situation.base or wanted in situation.overlay
        elif kind == "variable":
            result = _variable_true(cond, situation.variables.get(cond.get("name", "")))
        elif kind == "ndi":
            result = _ndi_true(cond.get("source"), situation.ndi)
        elif kind == "colour_black":
            result = situation.colour_black
        elif kind == "audio":
            result = self._audio_true(automation, index, cond, situation)
        elif kind == "loaded_show":
            result = show_matches(cond.get("show"), situation.show, situation.show_names)
        else:
            result = False
        return result != bool(cond.get("invert", False))

    def _condition_safely(self, automation: Automation, index: int, cond: Mapping[str, Any], situation: Situation,
                          day: date | None = None) -> bool:
        """A condition that can't be worked out (a wrong value in its file) is simply not true: it must never stop
        the engine, which checks the automations in its own thread."""
        try:
            return self.condition_true(automation, index, cond, situation, day)
        except Exception as exc:
            log_info(f"Automation “{automation.name}”: condition {index + 1} couldn't be checked ({type(exc).__name__}: {exc})",
                     key=f"automation-condition-{automation.id}-{index}")
            return False

    def _audio_true(self, automation: Automation, index: int, cond: Mapping[str, Any], situation: Situation) -> bool:
        """Sound above a level, for at least N seconds (so a single loud moment doesn't count). Once it counts, it
        stops counting after QUIET_HOLD seconds below the level (so a moment's quiet doesn't stop and start it)."""
        key = (automation.id, index)
        value = _num(situation.audio.get(str(cond.get("band", "level"))), 0.0)
        held = _num(cond.get("for"), 0.0)
        if value > _num(cond.get("above"), 0.5):
            self._audio_quiet.pop(key, None)
            since = self._audio_since.setdefault(key, situation.now)
            return situation.now - since >= held
        since = self._audio_since.get(key)
        if since is None:
            return False
        quiet = self._audio_quiet.setdefault(key, situation.now)
        if quiet - since >= held and situation.now - quiet < QUIET_HOLD:
            return True  # it had counted: a moment's quiet doesn't stop it
        self._audio_since.pop(key, None)
        self._audio_quiet.pop(key, None)
        return False

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
            wall_jump = situation.now - situation.previous_now if situation.previous_now is not None else None
            return _skipped_start(start, situation.previous_local, local, wall_jump)
        occurrence = range_instance(start, end, local)
        if not state.inside.get(index, False) and state.seen.get(index) == occurrence:
            return False  # the clocks went back into a range that already ran today: once is enough
        if not state.inside.get(index, False) or state.seen.get(index) != occurrence:
            state.seen[index] = occurrence  # (a new day's occurrence after a sleep counts as entering it)
        state.inside[index] = True
        return True

    def matches(self, automation: Automation, situation: Situation) -> tuple[bool, list[str]]:
        """(combined result, descriptions of the conditions that were true)."""
        if not automation.conditions:
            return False, []
        day = range_day(automation.conditions, situation.local)
        results = [self._condition_safely(automation, i, c, situation, day) for i, c in enumerate(automation.conditions)]
        self._results[automation.id] = results
        true = [describe_condition(c) for c, r in zip(automation.conditions, results) if r]
        combined = all(results) if automation.logic == "all" else any(results)
        return combined, true

    def check(self, show: Show, situation: Situation, mono_now: float, show_for_text: Show | None = None) -> list[tuple[str, dict]]:
        """One check of every automation, top to bottom. Returns actions to do now, in order
        (delayed ones are kept, and come back from due() when it's time)."""
        self._last_check = mono_now
        previous_now = self._last_now
        if situation.previous_local is None and self._last_local is not None:
            situation = replace(situation, previous_local=self._last_local, previous_now=previous_now)
        self._last_local, self._last_now = situation.local, situation.now
        # CentraLight wasn't looking for a while (asleep, closed, stuck): see whether new occurrences began meanwhile
        away_since = previous_now if previous_now is not None and situation.now - previous_now > NOT_LOOKING else None
        now_actions: list[tuple[str, dict]] = []
        ended: Automation | None = None  # the automation that loaded the show, if it stopped matching in this check
        loading = False  # an automation started loading a show in this check
        known = set(show.automations)
        for aid in [a for a in self.states if a not in known]:  # deleted: kept a while, in case Undo brings it back
            self._removed[aid] = (self.states.pop(aid), situation.now, self._definitions.pop(aid, None))
            self._drop_pending(aid, everything=True)
            self._drop_expected(aid)
            self._forget_sound(aid)
        for aid in [a for a, entry in self._removed.items() if situation.now - entry[1] > REMEMBER_REMOVED]:
            del self._removed[aid]
        for automation in show.ordered_automations():
            if automation.id not in self.states and automation.id in self._removed:
                old_state, _when, definition = self._removed.pop(automation.id)
                if definition == automation:  # the same automation back (Undo): its state comes back too
                    self.states[automation.id] = old_state
            edited = self._definitions.get(automation.id, automation) != automation
            self._definitions[automation.id] = automation
            state = self.states.setdefault(automation.id, AutomationState())
            if automation.id in self._quiet:  # (just imported: how things are now is where it starts from)
                self._quiet.discard(automation.id)
                if automation.enabled and not state.tripped:
                    state.matched = self.matches(automation, situation)[0]
                    state.since = situation.now
                continue
            if not automation.enabled or state.tripped:
                state.matched = False  # turned on again later while true = it runs then
                state.restored = False
                self._drop_pending(automation.id)
                self._drop_expected(automation.id)
                self._forget_sound(automation.id)
                continue
            if not situation.ndi_known and any(c.get("type") == "ndi" and not _ndi_true(c.get("source"), situation.ndi)
                                               for c in automation.conditions):
                # just started: a feed that isn't sending yet may still be connecting, so "feed lost" isn't true (nor
                # false) yet. A start event happening now is kept for its first real check.
                self._held_events[automation.id] = self._held_events.get(automation.id, frozenset()) | situation.events
                continue
            before = None if edited else self._results.get(automation.id)  # each condition at the last check
            held = self._held_events.pop(automation.id, None)
            if held:
                situation_now = replace(situation, events=situation.events | held)
                matched, true = self.matches(automation, situation_now)
            else:
                matched, true = self.matches(automation, situation)
            restored, state.restored = state.restored, False
            start = state.since if restored else away_since
            if matched and state.matched and start is not None and not self._kept_by_untimed(automation):
                start_local = situation.local - timedelta(seconds=max(0.0, situation.now - start))
                if not timed_continuous(automation, start_local, situation.local):
                    state.matched = False  # a new occurrence began while CentraLight wasn't looking: it runs again
            if restored and state.matched and not matched and (
                    self._stopped_at is None or situation.now - self._stopped_at > RECENT):
                state.matched = False  # it ended while CentraLight was closed, a while ago: no "when it stops" now
                state.since = situation.now
                continue
            if automation.id == self._rerun and matched and state.matched:
                state.matched = False  # (the PC started and the default show opened over its show: it runs again)
            if matched == state.matched:
                continue
            state.matched = matched
            state.since = situation.now
            if matched or _stop_cancels_waiting(automation, before, self._results.get(automation.id)):
                self._drop_pending(automation.id)  # (a new run replaces whatever was waiting)
            if self.paused:
                continue  # paused: keep track quietly, run nothing
            if matched:
                if any(isinstance(a, Mapping) and a.get("do") == "load_show" for a in automation.actions):
                    loading = True  # (its show wins over a default or a longer schedule's show, waiting or not: M14)
                    self._drop_defaults()
                    self._before = [a for a in self._before if a != automation.id] + (
                        [self.loader] if self.loader and self.loader != automation.id else [])
                    # the default show (or a longer schedule's) comes back when it stops; one about the loaded show, the
                    # lights being black and so on is like a person's choice: nothing comes back over it
                    self.loader = automation.id if schedules_a_show(automation) else None
                now_actions += self._run(automation, list(automation.actions), mono_now, situation.now, "ran", ", ".join(true),
                                         lifts=self._lifts_quit_dark(automation, situation))
            else:
                if automation.on_end:
                    now_actions += self._run(automation, list(automation.on_end), mono_now, situation.now, "stopped matching",
                                             count=False)
                if automation.id == self.loader:
                    ended = automation
        if self._rerun not in self._held_events:  # (only at its first real check: not while it waits for the feeds)
            self._rerun = None
        if ended is not None:  # (after them all: another automation may have started loading a show in this check)
            if self.loader == ended.id:
                self.loader = None
            if not loading:
                now_actions += self._hand_back(ended, show, mono_now, situation.now)
        return now_actions

    def _hand_back(self, ended: Automation, container: Show, mono_now: float, wall_now: float) -> list[tuple[str, dict]]:
        """The automation that loaded the show has stopped matching (M14). What loads now: nothing if its own "when it
        stops matching" loads a show or sends the lights to black (that wins, and nothing comes back over it later
        either). Else a longer schedule that still matches gets its show back: the one
        whose show it had replaced, or failing that the lowest in the list (as when several start together, the lowest
        one's load comes last); the default show then waits for that one. Else the default show, if there is one. (An
        automation that started loading a show in this check wins over all of these: see check.)"""
        self._drop_defaults()  # (one still waiting, for a schedule that's ended since, doesn't load after this)
        if ends_on_its_own(ended):
            self._before = []
            return []
        matching = [a for a in container.ordered_automations()
                    if a.id != ended.id and a.enabled and schedules_a_show(a) and self.states.get(a.id, AutomationState()).matched]
        ids = [a.id for a in matching]
        earlier = [aid for aid in self._before if aid in ids]
        other = matching[ids.index(earlier[-1])] if earlier else (matching[-1] if matching else None)
        if other is not None:
            self._before = [aid for aid in self._before if aid in ids and aid != other.id]
            self.loader = other.id
            shows = [a.get("show") for a in other.actions if isinstance(a, Mapping) and a.get("do") == "load_show"]
            return self._run(other, [{"do": "load_show", "show": shows[-1], "_default": True}], mono_now, wall_now,
                             f"still matches after “{ended.name}” stopped: its show loads again", count=False)
        self._before = []
        if not self.default_show:
            return []
        if self.default_show == NO_DEFAULT_SHOW:  # (None: the lights fade off and the show closes, M14)
            return self._run(ended, [{"do": "close_show", "_default": True}], mono_now, wall_now,
                             "stopped matching: the show closes (the default is no show)", count=False)
        return self._run(ended, [{"do": "load_show", "show": self.default_show, "_default": True}], mono_now, wall_now,
                         "stopped matching: the default show loads", count=False)

    @staticmethod
    def _lifts_quit_dark(automation: Automation, situation: Situation) -> bool:
        """Are the lights held off after a "Turn lights off" quit, and did one of this automation's time ranges begin
        after it?"""
        if situation.quit_dark_since is None:
            return False
        since = situation.local - timedelta(seconds=max(0.0, situation.now - situation.quit_dark_since))
        return began_after(automation, since, situation.local)

    def _kept_by_untimed(self, automation: Automation) -> bool:
        """An "any" automation that's true through something other than the clock right now (a variable, an input
        on Base...): a sleep or a restart doesn't make it a new occurrence."""
        if automation.logic != "any":
            return False
        results = self._results.get(automation.id) or []
        return any(r for c, r in zip(automation.conditions, results) if c.get("type") not in TIMED_KINDS + EVENTS)

    def _forget_sound(self, automation_id: str) -> None:
        """A sound condition starts afresh when its automation is turned on again (not from how loud it was then)."""
        for table in (self._audio_since, self._audio_quiet):
            for key in [k for k in table if k[0] == automation_id]:
                del table[key]

    def _drop_pending(self, automation_id: str, everything: bool = False) -> None:
        """Forget an automation's waiting actions, a countdown's included (Run now's stay, unless `everything`)."""
        if any(p[2] == automation_id for p in self.pending):
            self.pending = [p for p in self.pending if p[2] != automation_id or (p[4] == "by hand" and not everything)]

    def run_now(self, automation: Automation, mono_now: float, wall_now: float) -> list[tuple[str, dict]]:
        """The Run now button (and the API): its actions, whatever the conditions, with no countdown. Whatever of it was
        waiting (a countdown, an "after N s" step) is replaced by this run."""
        self._drop_pending(automation.id)
        self._drop_expected(automation.id)
        if any(isinstance(a, Mapping) and a.get("do") == "load_show" for a in automation.actions):
            self.manual_load()  # (a person chose this show: the default show doesn't replace it, M14)
        return self._run(automation, list(automation.actions), mono_now, wall_now, "run by hand", count=False, why="by hand")

    def _run(self, automation: Automation, actions: list, mono_now: float, wall_now: float, what: str,
             detail: str = "", count: bool = True, why: str = "conditions", lifts: bool = False) -> list[tuple[str, dict]]:
        """lifts: this run may light things after a "Turn lights off" quit (see began_after). It goes with each
        action as "_lifts", the waiting ones too.

        The countdown (countdown.py): the run's first change that has one (a show load, Cut / Fade to black) is its
        counted step; it and everything in the run that comes with it or after it wait for the countdown, in the
        runner's queue like any "after N s" step (so switching the automation off, the loop guard, pausing or the
        automation stopping drop them as they drop those). A run the clock started, whose banner started early, doesn't
        wait: it lands on time. Skip, Wait 10 min and Now pressed on that early banner are applied here."""
        state = self.states.setdefault(automation.id, AutomationState())
        if count:
            state.runs.append(mono_now)
            recent = [t for t in state.runs if mono_now - t <= LOOP_WINDOW]
            if len(recent) > LOOP_RUNS:
                state.tripped = True
                state.matched = False
                self.pending = [p for p in self.pending if p[2] != automation.id]
                self._drop_expected(automation.id)
                self.history.append(HistoryEntry(wall_now, automation.id, automation.name, "paused by the loop guard",
                                                 f"it ran {len(recent)} times in {LOOP_WINDOW:g} s"))
                log_info(f"Automation “{automation.name}” ran {len(recent)} times in {LOOP_WINDOW:g} s, so it's been paused (loop guard)")
                return []
        steps = [({**dict(action), "_lifts": lifts}, _delay(action)) for action in actions if isinstance(action, Mapping)]
        counted_at = None
        if self.countdown > 0.0 and why != "by hand":  # (a change due now that wouldn't change anything doesn't count)
            counted_at = next((delay for action, delay in steps
                               if self.counted(action) and not (delay == 0.0 and self.needless(action))), None)
        hold, decision = 0.0, ""
        if counted_at is not None:
            kind = "on_end" if what == "stopped matching" else "actions"
            early = self._take_expected(automation.id, kind, mono_now)
            decision = early or ""
            if early is None and counted_at < self.countdown:
                hold = self.countdown - counted_at  # (nothing saw it coming: it waits for its countdown)
            if decision == "wait":
                hold += COUNTDOWN_WAIT
        if decision == "skip":
            what, detail = f"{what}: skipped from the countdown", ""
        elif decision == "now":
            what, detail = f"{what}: done already (Now on the countdown)", ""
        state.last_run = wall_now
        self.history.append(HistoryEntry(wall_now, automation.id, automation.name, what, detail))
        log_info(f"Automation “{automation.name}” {what}" + (f" ({detail})" if detail else "")
                 + (f" (it waits {hold:g} s for its countdown)" if hold > 0 else ""))
        run = next(self._runs)
        now_actions = []
        marked = False
        for action, delay in steps:
            held = counted_at is not None and delay >= counted_at
            if held and decision in ("skip", "now"):
                continue
            if held and not marked and delay == counted_at and self.counted(action) and not (delay == 0.0 and self.needless(action)):
                action = {**action, "_countdown": True}  # (the step the banner counts down to)
                marked = True
            wait = delay + (hold if held else 0.0)
            if wait > 0:
                self._order += 1
                self.pending.append((mono_now + wait, self._order, automation.id, action, why, run))
            else:
                now_actions.append((automation.id, {k: v for k, v in action.items() if k != "_countdown"}))
        return now_actions

    def due(self, mono_now: float) -> list[tuple[str, dict]]:
        """Delayed actions whose time has come, in order."""
        if not self.pending:
            return []
        ready = sorted(p for p in self.pending if p[0] <= mono_now)
        if ready:
            self.pending = [p for p in self.pending if p[0] > mono_now]
        return [(aid, {k: v for k, v in action.items() if k != "_countdown"}) for _due, _order, aid, action, _why, _run in ready]

    # --- the countdown before automatic changes (M13; countdown.py says what it is)

    def expect(self, automation_id: str, kind: str, moment: datetime, run_due: float, action: Mapping[str, Any],
               delay: float, mono_now: float) -> None:
        """The clock will start this automation's run (kind: its "actions", or "on_end" when it stops matching) at
        `moment` (engine time run_due): its banner starts early, so it lands on time. Called at every check while it's
        coming (settle_expected drops one that isn't said again)."""
        key = (automation_id, kind, moment.strftime("%Y-%m-%d %H:%M"))
        found = self.expected.get(key)
        if found is None:
            found = Expected(key, f"x{next(self._expected_numbers)}", automation_id, kind, dict(action), run_due,
                             run_due + delay, mono_now)
            self.expected[key] = found
            log_info(f"Automation {automation_id}: its countdown starts early (it runs at {moment:%H:%M})",
                     key=f"countdown-early-{automation_id}-{key[2]}")
        found.seen = mono_now

    def settle_expected(self, mono_now: float) -> None:
        """After a check's expect() calls: a run the clock no longer promises goes (it was switched off, changed, or
        something else it needs went), and so does one whose time passed without it running."""
        for key, found in list(self.expected.items()):
            if (found.seen < mono_now and mono_now < found.run_due - 1.0) or mono_now > found.run_due + COUNTDOWN_GRACE:
                del self.expected[key]
        for key, (_what, run_due) in list(self._decisions.items()):
            if mono_now > run_due + COUNTDOWN_GRACE:
                del self._decisions[key]

    def _take_expected(self, automation_id: str, kind: str, mono_now: float) -> str | None:
        """This run was expected (its banner started early): "" to go ahead on time, or what was pressed on its banner
        ("skip", "wait" or "now"). None if it wasn't expected."""
        result = None
        for key, found in list(self.expected.items()):
            if found.automation == automation_id and found.kind == kind and abs(found.run_due - mono_now) <= COUNTDOWN_GRACE:
                del self.expected[key]
                result = ""
        for key, (what, run_due) in list(self._decisions.items()):
            if key[0] == automation_id and key[1] == kind and abs(run_due - mono_now) <= COUNTDOWN_GRACE:
                del self._decisions[key]
                result = what
        return result

    def _drop_expected(self, automation_id: str) -> None:
        for key in [k for k in self.expected if k[0] == automation_id]:
            del self.expected[key]
        for key in [k for k in self._decisions if k[0] == automation_id]:
            del self._decisions[key]

    def countdown_items(self) -> list[tuple[str, str, dict, float]]:
        """(id, automation id, the counted step, engine time it happens) for each countdown under way: runs waiting in
        the queue, and runs the clock will start soon whose banner has started early."""
        items = [(f"r{run}", aid, action, due) for due, _order, aid, action, why, run in self.pending
                 if action.get("_countdown") and why != "by hand"]
        items += [(found.id, found.automation, found.action, found.due) for found in self.expected.values()
                  if found.key not in self._decisions]
        return items

    def countdown_pressed(self, what: str, item: str, mono_now: float, wall_now: float) -> tuple[str, str] | None:
        """Load now ("now"), Wait 10 min ("wait") or Skip ("skip") on a countdown. A run that's waiting is moved (or
        dropped) from its counted step on. For a run the clock hasn't started yet, the choice is kept for when it does;
        for "now" this returns (automation id, kind) so the engine does that run at once (as Run now does)."""
        for found in self.expected.values():
            if found.id == item:
                self._decisions[found.key] = (what, found.run_due)
                log_info(f"Countdown: {what} pressed for automation {found.automation}, before it ran")
                return (found.automation, found.kind) if what == "now" else None
        if not item.startswith("r") or not item[1:].isdigit():
            return None
        run = int(item[1:])
        counted = next((p for p in self.pending if p[5] == run and p[3].get("_countdown")), None)
        if counted is None:
            return None
        start = counted[0]
        shift = COUNTDOWN_WAIT if what == "wait" else (mono_now - start if what == "now" else 0.0)
        kept = []
        for entry in self.pending:
            if entry[5] == run and entry[0] >= start:
                if what == "skip":
                    continue
                entry = (entry[0] + shift, *entry[1:])
            kept.append(entry)
        self.pending = kept
        name = self._definitions.get(counted[2])
        self.history.append(HistoryEntry(wall_now, counted[2], name.name if name is not None else counted[2],
                                         {"skip": "skipped from the countdown", "wait": "waits 10 minutes (countdown)",
                                          "now": "done now (countdown)"}.get(what, what)))
        log_info(f"Countdown: {what} pressed for automation {counted[2]}")
        return None

    def run_early(self, automation: Automation, kind: str, mono_now: float, wall_now: float) -> list[tuple[str, dict]]:
        """Now, on a banner that started early: the run the clock was about to start, done now (as Run now does)."""
        actions = list(automation.on_end if kind == "on_end" else automation.actions)
        return self._run(automation, actions, mono_now, wall_now, "run early (Now on the countdown)", count=False,
                         why="by hand")

    def drop_countdowns(self, why: str) -> None:
        """Runs waiting for a countdown, and early banners, go (the PC slept: what they waited for is stale)."""
        runs = {p[5] for p in self.pending if p[3].get("_countdown") and p[4] != "by hand"}
        starts = {p[5]: p[0] for p in self.pending if p[3].get("_countdown")}
        before = len(self.pending)
        self.pending = [p for p in self.pending if not (p[5] in runs and p[0] >= starts[p[5]])]
        dropped = before - len(self.pending) + len(self.expected)
        self.expected.clear()
        self._decisions.clear()
        if dropped:
            log_info(f"Countdowns dropped ({why})")

    def cap_countdowns(self, mono_now: float, seconds: float) -> None:
        """The countdown was made shorter (Settings): runs waiting for it happen within the new length."""
        for p in [p for p in self.pending if p[3].get("_countdown") and p[0] > mono_now + seconds]:
            shift = p[0] - (mono_now + seconds)
            self.pending = [(e[0] - shift, *e[1:]) if e[5] == p[5] and e[0] >= p[0] else e for e in self.pending]


# ---------------------------------------------------------------- doing the actions


def show_matches(wanted: Any, show_id: str, names: Iterable[str] = ()) -> bool:
    """Is this the loaded show, as an automation names it? An id ("sunday-morning", lower case: what the app stores)
    is only ever its id, so it can't pick a show whose name happens to look like another show's id; anything else
    ("Sunday Morning") is matched against its folder's name and its name, ignoring case."""
    from .model import ID_PATTERN

    text = str(wanted or "").strip()
    if not text:
        return False
    if ID_PATTERN.match(text):
        return bool(show_id) and text == show_id
    return text.casefold() in {name.casefold() for name in names}


def house_by_label(show: Show, label: Any) -> str | None:
    """A house light of the show by its name, as a global automation names it ("Festoon", "Key lights")."""
    from .model import slugify

    if not isinstance(label, str) or not label.strip():
        return None
    for item in show.house:
        if item.label.casefold() == label.strip().casefold() or slugify(item.label) == slugify(label):
            return item.fixture
    return None


def action_command(action: Mapping[str, Any], show: Show, scope: str = SHOW) -> Any | None:
    """The engine command for an action (None for actions the window does, or bad ones). show: the loaded show.
    A global automation's house lights are found by their names; one the loaded show doesn't have is skipped."""
    kind = action.get("do")
    if kind not in actions_for(scope):
        return None
    fade = action.get("fade")
    fade = float(fade) if isinstance(fade, (int, float)) and not isinstance(fade, bool) else None
    if kind == "house" and scope == GLOBAL:
        light = house_by_label(show, action.get("label"))
        if light is None:
            return None
        action = {**dict(action), "light": light}
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
            return SendToBase(input_id, transition=Transition(transition["kind"], _num(duration, own.duration), own.direction, own.softness))
        return SendToBase(input_id)
    if kind == "overlay":  # (a copy that stays, whatever the input's Send to Overlay says: there's no button to let go)
        return SendToOverlay(action["input"]) if action.get("input") in show.inputs else None
    if kind == "overlay_clear":
        return ClearFromOverlay(input_id=action["input"]) if isinstance(action.get("input"), str) and action.get("input") else None
    if kind == "overlay_clear_all":
        return ClearOverlay()
    if kind == "release_console":  # (M15: everything the Console page holds, with Settings' release fade)
        return ConsoleRelease()
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
    if kind in ("cut_black", "fade_black"):
        seconds = 0.0 if kind == "cut_black" else max(0.0, _num(action.get("fade"), BLACK_FADE))
        return ToBlack(seconds, everything=action.get("all") is True)
    if kind == "variable":
        return SetVariable(str(action.get("name", "")), action.get("value"))
    return None


# ---------------------------------------------------------------- what's coming up


def upcoming(show: Show, start: datetime, days: int = 7, limit: int = 50) -> list[tuple[datetime, Automation, bool]]:
    """When enabled automations with time, day or date conditions will next start matching,
    over the next few days: (local time, automation, it has other (untimed) conditions too).
    Only midnights and the times in time conditions can start a match, so only those are checked."""
    found: list[tuple[datetime, Automation, bool]] = []
    timed_kinds = TIMED_KINDS
    first_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for automation in show.ordered_automations():
        timed = [c for c in automation.conditions if c.get("type") in timed_kinds]
        if not automation.enabled or not timed:
            continue
        others = len(timed) < len(automation.conditions)
        probe = Automation(automation.id, automation.name, True, automation.logic, tuple(timed))
        moments = change_moments(timed, first_day, days)
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

        tick = ctypes.WinDLL("kernel32").GetTickCount64
        tick.restype = ctypes.c_uint64  # (left as a 32-bit int, it goes negative after 25 days)
        return tick() / 1000.0
    except Exception:
        return time.monotonic()


# ---------------------------------------------------------------- editing (each returns a new Show)


def default_condition(kind: str, show: Show, shows: Iterable[tuple[str, str]] = ()) -> dict[str, Any]:
    """A sensible starting condition of each kind. shows: (id, name) of each show (for "This show is loaded")."""
    if kind == "loaded_show":
        first = next(iter(shows), None)
        return {"type": "loaded_show", "show": first[0] if first else ""}
    if kind == "time":
        return {"type": "time", "from": "09:00", "to": "12:00"}
    if kind == "days":
        return {"type": "days", "days": ["sun"]}
    if kind == "dates":
        return {"type": "dates", "from": "12-01", "to": "12-31"}
    if kind == "sun":
        return {"type": "sun", "when": "after", "event": "sunset", "minutes": 0}
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


def default_action(kind: str, show: Show, scope: str = SHOW, loaded: Show | None = None,
                   shows: Iterable[tuple[str, str]] = ()) -> dict[str, Any]:
    """A sensible starting action of each kind. show: the show (or the global automations); loaded: the loaded show
    (a global automation's house lights are named after its); shows: (id, name) of each show (for "Load a show")."""
    first_input = next(iter(show.ordered_inputs()), None)
    input_id = first_input.id if first_input else ""
    if kind == "load_show":
        first = next(iter(shows), None)
        return {"do": "load_show", "show": first[0] if first else ""}
    if kind == "house" and scope == GLOBAL:
        source = loaded if loaded is not None else show
        return {"do": "house", "label": source.house[-1].label if source.house else "Key lights", "level": 0.7, "fade": 2}
    if kind == "base":
        return {"do": "base", "input": input_id}
    if kind in ("overlay", "overlay_clear"):
        return {"do": kind, "input": input_id}
    if kind == "overlay_clear_all":
        return {"do": "overlay_clear_all"}
    if kind == "release_console":
        return {"do": "release_console"}
    if kind == "house":
        light = show.house[-1].fixture if show.house else ""
        return {"do": "house", "light": light, "level": 0.7, "fade": 2}
    if kind == "master":
        return {"do": "master", "level": 1.0, "fade": 2}
    if kind == "cut_black":
        return {"do": "cut_black"}
    if kind == "fade_black":
        return {"do": "fade_black", "fade": BLACK_FADE}
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
