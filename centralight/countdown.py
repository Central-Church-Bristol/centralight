"""A countdown before automatic changes (M13).

Before an automation loads a show (a global automation's "Load a show"), or does a Cut to black or Fade to black (any
automation), a banner on the window and the phone page counts down ("Loading Evening in 0:15 · Load now · Wait 10 min
· Skip"). If nobody touches it, the change happens at zero.

- **A run waits as a whole:** the run's first such change is its counted step; it, and everything in the run that comes
  with it or after it, wait in the automation runner's queue like any "after N s" step (automations.py). So whatever
  drops those drops a countdown too: switching the automation off, deleting it, the loop guard, pausing, a new run of
  it, Run now, and the automation stopping (when that cancels what it was waiting to do, as for "feed lost for 10 s").
- **On time:** a run the clock starts (a time range, days, dates, sunrise / sunset), or ends (its "when it stops
  matching"), is seen coming: its banner starts early and the run happens on time. So does an "after N s" step at
  least the countdown's length into its run. Other triggers (a feed lost, a sound level) can't be seen coming: the
  run waits for the countdown.
- **Load now** does it at once. **Skip** skips this run only. **Wait 10 min** moves it 10 minutes later, with the
  countdown again before it. Pressed on a banner that started early, the choice is kept for when the run comes (within
  GRACE seconds of its time).
- **No countdown** for Run now, while CentraLight (or the PC) has just started (nobody's there, and the start-up rules
  apply), while automations are paused, when the length is 0 (Settings), or when it wouldn't change anything (the
  show is loaded already, the colour lights are black already). A countdown from before the PC slept is dropped when
  it wakes; one isn't kept over a restart (the start-up rules apply then).

This module says which actions count, how a banner reads, and when the clock will start or end a run.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Mapping

DEFAULT_SECONDS = 15
MAX_SECONDS = 60
WAIT = 600.0  # seconds "Wait 10 min" moves a change on by
GRACE = 5.0  # seconds either side of its time a run whose banner started early may come
STARTUP_QUIET = 60.0  # seconds after CentraLight starts with no countdowns (the start-up rules apply)
COUNTED = ("cut_black", "fade_black")  # actions with a countdown from any automation (and a global "Load a show")


@dataclass(frozen=True)
class CountdownView:
    """The countdown the banner shows (the soonest one), for the window and the phone page."""

    id: str  # "g:r12" (a run waiting in the global automations' queue), "s:x3" (a show automation's run, coming)
    text: str  # "Loading evening", "Fading to black"
    seconds: float  # left
    automation: str  # its automation's name
    more: int = 0  # other countdowns under way too
    show: str = ""  # a "Load a show": the show as the action names it (the window and phone page show its name)

    @property
    def clock(self) -> str:
        """"0:15"."""
        whole = max(0, int(self.seconds + 0.999))
        return f"{whole // 60}:{whole % 60:02d}"


def counts_down(action: Mapping[str, Any], scope: str) -> bool:
    """Does this automation action get a countdown? A global "Load a show" (and the show closing, when the default show
    is None: M14), and Cut / Fade to black from any."""
    do = action.get("do")
    return (do in ("load_show", "close_show") and scope == "global") or do in COUNTED


def describe(action: Mapping[str, Any], show_label: str = "") -> str:
    """"Loading Evening", "Fading to black", "Cutting everything to black"."""
    do = action.get("do")
    if do == "load_show":
        return f"Loading {show_label or str(action.get('show', '')).strip() or 'another show'}"
    if do == "close_show":
        return "Closing the show (lights off)"
    everything = " everything" if action.get("all") is True else ""
    return f"Cutting{everything} to black" if do == "cut_black" else f"Fading{everything} to black"


def _clock_only(automation: Any) -> bool:
    from .automations import EVENTS, TIMED_KINDS

    conditions = automation.conditions
    return bool(conditions) and not any(c.get("type") in EVENTS for c in conditions) \
        and any(c.get("type") in TIMED_KINDS for c in conditions)


def _change_at(automation: Any, situation: Any, seconds: float, becomes: bool) -> datetime | None:
    """The first whole minute within `seconds` at which the automation's conditions are `becomes` by the clock, with
    everything else as it is now; None if not, or if they're that way already."""
    from .automations import AutomationRunner

    if not _clock_only(automation):
        return None
    local = situation.local

    def at(moment: datetime, ahead: float) -> bool:
        later = replace(situation, local=moment, now=situation.now + ahead, events=frozenset(), previous_local=None,
                        previous_now=None)
        return AutomationRunner().matches(automation, later)[0]  # (a fresh runner: no memory of earlier checks)

    try:
        if at(local, 0.0) == becomes:
            return None  # (that way by the clock already: whatever holds it isn't the clock)
        moment = local.replace(second=0, microsecond=0) + timedelta(minutes=1)
        while (moment - local).total_seconds() <= seconds:
            ahead = (moment - local).total_seconds()
            if at(moment, ahead) == becomes:
                return moment
            moment += timedelta(minutes=1)
    except Exception:  # (a condition that can't be worked out: nothing is predicted)
        return None
    return None


def next_fire(automation: Any, situation: Any, seconds: float) -> datetime | None:
    """When, within the next `seconds`, this automation's conditions come true by the clock alone, or None (its own
    runner says it isn't matched now)."""
    return _change_at(automation, situation, seconds, True)


def next_stop(automation: Any, situation: Any, seconds: float) -> datetime | None:
    """When, within the next `seconds`, a matched automation's conditions stop being true by the clock alone (its
    "when it stops matching" runs then), or None."""
    return _change_at(automation, situation, seconds, False)
