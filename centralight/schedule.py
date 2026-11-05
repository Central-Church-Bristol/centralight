"""The show schedule: which show the global automations load when, for the week grid on the Automation tab.

It's worked out from the global automations' "Load a show" actions and their clock conditions (time of day, days,
dates, sunrise / sunset), with the automations runner's own checks, so it agrees with when they'll run:
- A block is a stretch of time when an automation's clock conditions hold. Its show loads at the start (or as many
  seconds later as the action waits), and the block lasts until they stop holding. A "Load a show" under "When it
  stops matching" is a moment at the end of each stretch.
- Other conditions (an NDI feed, sound, the colour lights being black, which show is loaded) can't go on a calendar.
  With All, a block notes them ("if Media Background Pane is sending"). With Any, they could start it at any time,
  so the automation is listed under the grid too, like ones with no clock condition ("When CentraLight starts"), and
  All ones with an event ("The PC starts" lasts a moment, so it's only true then).
- Two automations loading different shows at the same minute clash. A block that starts while another is on is a
  handover, not a clash, and two automations loading the same show don't clash either.
- Only the moments a clock condition can change are checked (midnights, the times in time conditions, each day's
  sunrise and sunset), never every minute, so a week takes a moment to work out.
- Times are the PC's local times (naive datetimes), like everywhere else in CentraLight. A time skipped when the
  clocks go forward (01:00-02:00 on the last Sunday of March in the UK) moves to the first time after it, as the
  runner does; the hour repeated when they go back is shown once (a time runs once).

Plain Python (no Qt): ui/schedule_view.py draws it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .automations import (
    EVENTS,
    TIMED_KINDS,
    AutomationRunner,
    Situation,
    _stop_cancels_waiting,
    change_moments,
    describe_condition,
)
from .model import Automation, Show
from .shows import ShowInfo, find_show

LOAD_SHOW = "load_show"
MINUTE = timedelta(minutes=1)
MAX_WAIT = 86400.0  # seconds: the longest an action can wait first (see check_action)


@dataclass(frozen=True)
class Block:
    """One show on the week grid: when an automation loads it, and for how long its clock conditions hold."""

    automation_id: str
    automation_name: str
    show: str  # the show as the action names it (an id or a name)
    show_label: str  # the show's name (what the action says, if there's no such show)
    start: datetime  # when the show loads (local time, like the rest of automations.py)
    end: datetime  # when the automation's clock conditions stop holding (the same as start: a moment)
    clash: bool = False  # another automation loads a different show at the same minute
    conditional: str = ""  # what else it needs, e.g. "if Media Background Pane is sending" ("" = nothing)
    on_end: bool = False  # loaded by "When it stops matching": a moment (start == end)
    continues_before: bool = False  # it began before the first day shown (start is that day's midnight)
    continues_after: bool = False  # it goes on after the last day shown (end is the midnight after it)
    show_id: str = ""  # the show's id ("" if it isn't one of the shows given)
    clash_with: tuple[str, ...] = ()  # the names of the automations it clashes with
    missing: bool = False  # there's no show by that name, so nothing loads (only said when the shows are known)
    default: bool = False  # the default show (Settings, M14) in the time no automation loads one: drawn lighter

    @property
    def moment(self) -> bool:
        return self.start == self.end


@dataclass(frozen=True)
class Unplaced:
    """A "Load a show" whose trigger can't go on a calendar: "When CentraLight starts" → Morning."""

    automation_id: str
    automation_name: str
    show_label: str
    trigger: str  # e.g. "When CentraLight starts"
    show: str = ""  # the show as the action names it
    show_id: str = ""  # its id ("" if it isn't one of the shows given)
    missing: bool = False  # there's no show by that name (only said when the shows are known)


def monday_of(day: date) -> date:
    """The Monday of the week `day` is in."""
    return day - timedelta(days=day.weekday())


def week_schedule(automations: Show | Iterable[Automation], shows: Mapping[str, str] | None, week_start: date,
                  days: int = 7, local_time: Callable[[float], datetime] = datetime.fromtimestamp,
                  default_show: str = "") -> tuple[list[Block], list[Unplaced]]:
    """The shows the automations load from week_start's midnight for `days` days (blocks, in time order), and the
    "Load a show" actions whose trigger can't go on a calendar (in the automations' order).
    automations: the global automations (their Show, or a list in their run order). shows: show id -> name.
    local_time: the PC's clock (seconds since 1970 -> local time), to find a time the clocks skip.
    default_show: its id (Settings, M14): it fills the time between the automations' blocks (first in the list)."""
    ordered = automations.ordered_automations() if isinstance(automations, Show) else list(automations)
    names = dict(shows or {})
    infos = [ShowInfo(show_id, name, show_id) for show_id, name in names.items()]
    first = datetime.combine(week_start, time())
    last = first + timedelta(days=max(1, int(days)))
    gaps = skipped_times(first, last, local_time)
    found: list[tuple[int, Block]] = []
    unplaced: list[Unplaced] = []
    for order, automation in enumerate(ordered):
        loads = _loads(automation, infos) if automation.enabled else []
        if not loads:
            continue
        conditions = list(automation.conditions)
        timed = [c for c in conditions if c.get("type") in TIMED_KINDS]
        others = [c for c in conditions if c.get("type") not in TIMED_KINDS]
        states = [c for c in others if not _momentary(c)]  # how things are (a feed, sound...), not an event
        is_all = automation.logic == "all"  # (the runner treats anything else as Any)
        joiner = " and " if is_all else " or "

        def words(items: list, joiner: str = joiner) -> str:
            return joiner.join(_lower_the(describe_condition(c, shows=names)) for c in items)

        # With All, an event ("The PC starts") is only true for a moment, so the clock can't say when it runs
        placed = bool(timed) and not (is_all and len(states) < len(others))
        if placed:
            conditional = (f"if {words(states)}" if is_all else f"unless {words(states)}") if states else ""
            spans = _spans(automation, timed, first, last, gaps)
            found += [(order, block) for block in _blocks(automation, loads, spans, conditional, last)]
        if not conditions:
            trigger = "Only when run by hand"
        elif not placed:
            trigger = f"When {words(conditions)}"
        elif not is_all and others:
            trigger = f"When {words(others)}"  # (Any: these can start it whatever the time)
        else:
            continue
        for action, on_end, show_id, label, missing in loads:
            if on_end and not conditions:
                continue  # (Run now only does "Do this")
            text = f"When it stops matching: {words(conditions)}" if on_end else trigger
            unplaced.append(Unplaced(automation.id, automation.name, label, text, _wanted(action), show_id, missing))
    found.sort(key=lambda item: (item[1].start, item[1].on_end, item[0]))
    blocks = _mark_clashes([block for _order, block in found])
    if default_show:
        blocks = _default_blocks(blocks, default_show, names, first, last) + blocks
    return blocks, unplaced


def _default_blocks(blocks: list[Block], default_show: str, names: Mapping[str, str], first: datetime,
                    last: datetime) -> list[Block]:
    """The default show (M14) in the gaps: the time no automation's block covers (a show that isn't there loads
    nothing, so it leaves a gap too). It's what loads at the PC's start and when a schedule ends."""
    spans = sorted((b.start, b.end) for b in blocks if not b.moment and not b.missing)
    gaps, at = [], first
    for start, end in spans:
        if start > at:
            gaps.append((at, start))
        at = max(at, end)
    if at < last:
        gaps.append((at, last))
    known = default_show in names
    return [Block("", "The default show", default_show, names.get(default_show, default_show), start, end,
                  continues_before=start <= first, continues_after=end >= last, show_id=default_show if known else "",
                  missing=bool(names) and not known, default=True)
            for start, end in gaps if end - start >= MINUTE]


def describe_block(block: Block) -> str:
    """A block's tooltip: which show loads when, from which automation, and anything else it needs."""
    if block.default:
        return (f"{block.show_label}: the default show (Settings), when no automation loads another\n"
                f"{block.start:%a %d %b %H:%M} – {block.end:%a %d %b %H:%M}"
                + ("\nThere's no show by that name, so the last show stays" if block.missing else ""))
    if block.on_end:
        lines = [f"Load {block.show_label} when it stops matching", f"{block.start:%a %d %b %H:%M}"]
    else:
        start, end = f"{block.start:%a %d %b %H:%M}", f"{block.end:%a %d %b %H:%M}"
        if block.continues_before and block.continues_after:
            when = "All week (from the week before, into the week after)"
        elif block.continues_before:
            when = f"From the week before – {end}"
        elif block.continues_after:
            when = f"{start} – into the week after"
        elif block.moment:
            when = start
        elif block.end.date() == block.start.date():
            when = f"{start} – {block.end:%H:%M}"
        elif block.end.time() == time() and block.end.date() == block.start.date() + timedelta(days=1):
            when = f"{start} – 24:00"
        else:
            when = f"{start} – {end}"
        lines = [f"Load {block.show_label}", when]
    lines.append(f"Automation: {block.automation_name}")
    if block.conditional:
        lines.append(block.conditional[:1].upper() + block.conditional[1:])
    if block.missing:
        lines.append(f"There's no show called “{block.show}”, so nothing loads")
    if block.clash:
        others = ", ".join(f"“{name}”" for name in block.clash_with)
        lines.append(f"Clash: {others} {'loads' if len(block.clash_with) == 1 else 'load'} a different show at the same minute")
    lines.append("Click to open the automation")
    return "\n".join(lines)


def skipped_times(first: datetime, last: datetime, local_time: Callable[[float], datetime] = datetime.fromtimestamp,
                  ) -> list[tuple[datetime, datetime]]:
    """The local times the clocks skip when they go forward, between first and last: (the first time skipped, the
    first time after it), e.g. (01:00, 02:00) on the last Sunday of March in the UK. Found by stepping through the real
    time an hour at a time (then to the second where the clocks jump), so it's quick."""
    step = 3600
    try:
        epoch = int(first.replace(tzinfo=timezone.utc).timestamp()) - 15 * 3600  # (local time is within 14 h of UTC)
        end = int(last.replace(tzinfo=timezone.utc).timestamp()) + 15 * 3600
        gaps = []
        before = local_time(epoch)
        while epoch < end:
            after = local_time(epoch + step)
            if after - before > timedelta(seconds=step):  # the clocks went forward in this hour: find the second
                low, high = epoch, epoch + step
                while high - low > 1:
                    middle = (low + high) // 2
                    if local_time(middle) - local_time(low) > timedelta(seconds=middle - low):
                        high = middle
                    else:
                        low = middle
                gaps.append((local_time(low) + timedelta(seconds=1), local_time(high)))
            epoch, before = epoch + step, after
        return gaps
    except (OverflowError, OSError, ValueError, TypeError):
        return []  # (a clock that can't say: nothing is skipped)


# ---------------------------------------------------------------- the parts


def _wanted(action: Mapping[str, Any]) -> str:
    return str(action.get("show", "") or "").strip()


def _loads(automation: Automation, infos: list[ShowInfo]) -> list[tuple[Mapping[str, Any], bool, str, str, bool]]:
    """The automation's "Load a show" actions: (action, in "When it stops matching", show id or "", its label, there's
    no such show). The show is found as the window finds it when it loads one: by id, folder name or name. (If the
    shows couldn't be listed, none is called missing.)"""
    found = []
    for actions, on_end in ((automation.actions, False), (automation.on_end, True)):
        for action in actions:
            if not isinstance(action, Mapping) or action.get("do") != LOAD_SHOW or not _wanted(action):
                continue  # (an empty one loads nothing)
            info = find_show(Path(), _wanted(action), infos)
            found.append((action, on_end, info.id if info else "", info.name if info else _wanted(action),
                          bool(infos) and info is None))
    return found


def _momentary(cond: Mapping[str, Any]) -> bool:
    """An event (The PC starts, CentraLight starts...): true only at the moment it happens."""
    return cond.get("type") in EVENTS and not cond.get("invert", False)


def _lower_the(text: str) -> str:
    """'The PC starts' -> 'the PC starts' (it follows "When" or "if")."""
    text = text.replace("(The ", "(the ")
    return "the " + text[4:] if text.startswith("The ") else text


def _delay(action: Mapping[str, Any]) -> timedelta:
    """How long an action waits first, read as the runner reads it (anything but a number is no wait)."""
    delay = action.get("delay", 0)
    seconds = float(delay) if isinstance(delay, (int, float)) and not isinstance(delay, bool) else 0.0
    return timedelta(seconds=min(MAX_WAIT, max(0.0, seconds))) if math.isfinite(seconds) else timedelta(0)


def _after_gap(moment: datetime, gaps: list[tuple[datetime, datetime]]) -> datetime:
    """A time the clocks skip becomes the first time after it (the runner runs a skipped start then)."""
    for skipped_from, skipped_to in gaps:
        if skipped_from <= moment < skipped_to:
            return skipped_to
    return moment


def _spans(automation: Automation, timed: list, first: datetime, last: datetime,
           gaps: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime, bool, bool]]:
    """The stretches between first and last when the automation's clock conditions hold (all of them with All, any
    with Any): (start, end, began before first, goes on after last). The runner itself checks them, a fresh one each
    time (no memory), at each moment they can change: between those moments nothing changes."""
    probe = Automation(automation.id, automation.name, True, automation.logic, tuple(timed))

    def holds(moment: datetime) -> bool:
        return AutomationRunner().matches(probe, Situation(now=0.0, local=moment))[0]

    spans = []
    began = first if holds(first - MINUTE) else None
    earlier = began is not None
    for moment in change_moments(timed, first, (last - first).days):
        if not first <= moment < last or holds(moment) == (began is not None):
            continue
        if began is None:
            began, earlier = _after_gap(moment, gaps), False
        else:
            spans.append((began, min(last, _after_gap(moment, gaps)), earlier, False))
            began = None
    if began is not None:
        spans.append((began, last, earlier, holds(last)))
    return spans


def _blocks(automation: Automation, loads: list, spans: list, conditional: str, last: datetime) -> list[Block]:
    """For each stretch: a block per "Load a show" in "Do this", and a moment per one in "When it stops matching"."""
    blocks = []
    cancels = _stop_cancels_waiting(automation, None, None)  # does stopping (the clock moved on) drop what's waiting?
    for index, (start, end, earlier, later) in enumerate(spans):
        next_start = spans[index + 1][0] if index + 1 < len(spans) else None
        for action, on_end, show_id, label, missing in loads:
            wait = _delay(action)
            if on_end:
                if later:
                    continue  # it stops after the last day shown
                at = end + wait
            elif earlier:
                if start == end:
                    continue  # it stopped just as the first day shown began
                at, wait = start, timedelta(0)  # (its show loaded before then)
            else:
                at = start + wait
                if wait and at >= end and cancels:
                    continue  # it stops before the wait is over, and that drops the waiting load
            if at >= last or (wait and next_start is not None and next_start <= at):
                continue  # after the last day shown, or it starts again first (a new run drops what was waiting)
            blocks.append(Block(automation_id=automation.id, automation_name=automation.name, show=_wanted(action),
                                show_label=label, start=at, end=at if on_end else max(at, end), conditional=conditional,
                                on_end=on_end, continues_before=earlier and not on_end,
                                continues_after=later and not on_end, show_id=show_id, missing=missing))
    return blocks


def _show_key(block: Block) -> str:
    return block.show_id or block.show.casefold()


def _mark_clashes(blocks: list[Block]) -> list[Block]:
    """Mark where another automation loads a different show at the same minute. (A block that began before the first
    day shown loaded its show before then, so it can't clash here.)"""
    by_minute: dict[datetime, list[int]] = {}
    for index, block in enumerate(blocks):
        if not block.continues_before:
            by_minute.setdefault(block.start.replace(second=0, microsecond=0), []).append(index)
    marked = list(blocks)
    for indexes in by_minute.values():
        for i in indexes:
            mine = blocks[i]
            others = [blocks[j] for j in indexes
                      if blocks[j].automation_id != mine.automation_id and _show_key(blocks[j]) != _show_key(mine)]
            if others:
                marked[i] = replace(mine, clash=True, clash_with=tuple(dict.fromkeys(o.automation_name for o in others)))
    return marked
