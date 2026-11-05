"""The Console page's live control (M15): what it holds, light by light, and its chases.

Like a lighting desk's programmer: what someone sets on the Console wins, per light and per attribute, until it's
released (with a fade). Untouched attributes keep following CentraLight's inputs. Brightness is the light's own Dimmer
channel, colour its Red, Green and Blue, and the Advanced switch reaches the rest as raw values (Amber, the colour
macro, Strobe, the light's own programs, Dimmer speed).

Qt-free: the engine keeps a ConsoleState and the window shows a ConsoleView.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Iterable, Mapping
from functools import lru_cache
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

EMPTY: Mapping[str, Any] = MappingProxyType({})

# Each light profile's channels, in DMX order (the offset is the place in the tuple): (attribute, the name people see)
CHANNELS: dict[str, tuple[tuple[str, str], ...]] = {
    "slimpar9": (
        ("red", "Red"),
        ("green", "Green"),
        ("blue", "Blue"),
        ("amber", "Amber"),
        ("macro", "Colour macro"),
        ("strobe", "Strobe"),
        ("program", "Program"),
        ("dimmer", "Dimmer"),
        ("dimmer_speed", "Dimmer speed"),
    ),
    "dimmer": (("dimmer", "Dimmer"),),
}
COLOUR = ("red", "green", "blue")  # set together by the colour picker
BRIGHTNESS = "dimmer"
SNAP_BACK = frozenset({"macro", "strobe", "program", "dimmer_speed"})  # set and released at once, never faded
RAW = ("dimmer", "amber", "macro", "strobe", "program", "dimmer_speed")  # sent as they are (Master scales the colour)
DEFAULT_VALUES = {"dimmer": 255.0}  # what CentraLight sends on an attribute nobody holds (the rest: 0)
CHASED = ("red", "green", "blue", "dimmer")  # what a chase sets: colour and brightness
RELEASE_FADE = 2.0  # seconds: Release and Release all hand lights back over this long (Settings changes it)
STROBE_NOTE = ("Strobe flashes the lights quickly. Flashing light can trigger seizures in people with photosensitive "
               "epilepsy: use it sparingly, and only when people have been told.")

# Chases
DIRECTIONS = (("forward", "Forward"), ("backward", "Backward"), ("bounce", "Bounce"), ("random", "Random"))
BPM_RANGE = (10.0, 300.0)
TAP_GAP = 2.5  # seconds: taps further apart than this start a new count
TAPS_KEPT = 5
TAP_BOUNCE = 0.06  # seconds: a tap this soon after the last is the same tap (a double press, or two in one tick)
# a beat (audio.beat): it jumps to 1 and falls away within a sound block or two, so a tick may only see it on its way
# down: a reading this high that's this much above the tick before is a new beat
BEAT_RISE = 0.6
BEAT_JUMP = 0.2
_SEQ = itertools.count(1)  # when each change was made: between a Live change and a chase, the later one wins
TINTED = ("red", "green", "blue", "amber")  # what the light's dimmer scales on the light itself
_EPS = 1e-6  # a step's millionth: a position worked out as 2.9999999999 is step 3 (clocks are big numbers)


def finite(value: Any, default: float) -> float:
    """A plain number from a file: never a bool, an overflow, NaN or infinity (default then)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return default
    return number if math.isfinite(number) else default


def whole_steps(position: float) -> int:
    """The steps gone by at this position (with a hair's allowance for rounding)."""
    return int(position + _EPS)


def channels_of(profile: str) -> tuple[tuple[str, str], ...]:
    return CHANNELS.get(profile, ())


@dataclass(frozen=True)
class ChaseStep:
    """One step of a chase: each of its lights' colour and brightness, 0-1 (a light it leaves out keeps the step
    before's)."""

    looks: Mapping[str, tuple[float, float, float, float]] = EMPTY  # light id -> (red, green, blue, brightness)


@dataclass(frozen=True)
class Chase:
    """A chase on the Console page: steps on chosen lights, one per beat (or every so many seconds)."""

    lights: tuple[str, ...] = ()
    steps: tuple[ChaseStep, ...] = ()
    bpm: float = 120.0  # one step per beat
    seconds: float | None = None  # or a step every this many seconds (None: by BPM)
    crossfade: float = 0.0  # 0: each step snaps; 1: it glides all the way from the step before
    direction: str = "forward"
    loop: bool = True  # else it plays once and holds its last step
    on_beat: bool = False  # step on each beat the audio hears (Settings → Audio), not the clock

    def step_seconds(self) -> float:
        if self.seconds is not None:
            return max(0.05, float(self.seconds))
        return 60.0 / max(BPM_RANGE[0], min(BPM_RANGE[1], float(self.bpm)))


@dataclass(frozen=True)
class ChaseView:
    """A chase as the page shows it: the chase, and where it's got to."""

    chase: Chase = field(default_factory=Chase)
    playing: bool = False
    paused: bool = False
    step: int = 0  # the step showing now (0-based)
    lights: tuple[str, ...] | None = None  # the lights it runs on now (None: the chase's own; Release takes some away)


@dataclass(frozen=True)
class ConsoleView:
    """What the Console holds, for the window: each light's held attributes and every channel's value now."""

    held: Mapping[str, frozenset[str]] = EMPTY  # light id -> the attributes the Console holds
    values: Mapping[str, tuple[int, ...]] = EMPTY  # light id -> each channel's value now (0-255, in DMX order)
    chase: ChaseView | None = None
    levels: Mapping[str, Mapping[str, int]] = EMPTY  # light id -> attribute -> the value held (0-255, before Master)

    @property
    def held_count(self) -> int:
        return sum(1 for attrs in self.held.values() if attrs)


# ---------------------------------------------------------------- the engine's side: what's held, and the chase


@dataclass
class _Held:
    """An attribute the Console holds: its value (0-255), faded in from what it showed before."""

    value: float
    origin: float
    start: float
    fade: float
    seq: int

    def at(self, now: float) -> float:
        if self.fade <= 0.0 or now >= self.start + self.fade:
            return self.value
        progress = max(0.0, (now - self.start) / self.fade)
        return self.origin + (self.value - self.origin) * progress


@dataclass
class _Releasing:
    """A released attribute on its way back to what the inputs say: how much of where it was still shows."""

    origin: float
    start: float
    fade: float

    def weight(self, now: float) -> float:
        if self.fade <= 0.0:
            return 0.0
        return max(0.0, 1.0 - (now - self.start) / self.fade)


# ---------------------------------------------------------------- a chase's steps (the Console's and the Lights element's)


def pass_length(chase: Chase) -> int:
    """Step times in one pass (bounce: there and back, without playing either end twice)."""
    count = len(chase.steps)
    if chase.direction == "bounce" and count > 1:
        return 2 * count - 2
    return max(1, count)


def last_index(chase: Chase) -> int:
    """Play once: the step time it stops at (bounce ends back on the first step)."""
    return pass_length(chase) if chase.direction == "bounce" and len(chase.steps) > 1 else pass_length(chase) - 1


@lru_cache(maxsize=512)
def _shuffled(seed: str, number: int, count: int) -> tuple[int, ...]:
    order = list(range(count))
    random.Random(f"{seed}:{number}").shuffle(order)
    return tuple(order)


@lru_cache(maxsize=512)
def _random_pass(seed: str, number: int, count: int) -> tuple[int, ...]:
    if count <= 2:
        return tuple(range(count))
    order = list(_shuffled(seed, number, count))
    if number > 0 and order[0] == _shuffled(seed, number - 1, count)[-1]:  # (a pass's last is never swapped: count > 2)
        order[0], order[1] = order[1], order[0]
    return tuple(order)


def random_pass(seed: str, number: int, count: int) -> list[int]:
    """Random: the number'th pass's order, each step once, and never the step the pass before ended on first (two
    steps simply take turns). The same seed and pass always give the same order (worked out once: it's every tick)."""
    return list(_random_pass(seed, number, count))


def step_index(chase: Chase, index: int, seed: str = "") -> int:
    """Which step (0-based) shows at the index'th step time."""
    count = len(chase.steps)
    if count <= 1:
        return 0
    if not chase.loop:
        index = min(index, last_index(chase))
    if chase.direction == "backward":
        return count - 1 - index % count
    if chase.direction == "bounce":
        length = pass_length(chase)
        place = index % length
        return place if place < count else length - place
    if chase.direction == "random":
        return _random_pass(seed, index // count, count)[index % count]
    return index % count


def finished(chase: Chase, position: float) -> bool:
    return not chase.loop and position + _EPS >= last_index(chase) + 1


def step_look(chase: Chase, step: int, light: str) -> tuple[float, float, float, float] | None:
    """A step's look for the light: its own, or that of the latest step before it that has one."""
    steps = chase.steps
    for back in range(len(steps)):
        look = steps[(step - back) % len(steps)].looks.get(light)
        if look is not None:
            return tuple(float(v) for v in look)  # type: ignore[return-value]
    return None


def chase_look(chase: Chase, light: str, position: float, seed: str = "",
               crossfade: float | None = None) -> tuple[float, float, float, float] | None:
    """The light's (r, g, b, brightness) at this position (steps gone by), crossfading into each step from the one
    before. crossfade: instead of the chase's own (a Lights element's can change over time)."""
    if not chase.steps:
        return None
    index = whole_steps(position)
    now_look = step_look(chase, step_index(chase, index, seed), light)
    if finished(chase, position) or now_look is None:
        return now_look
    fade = chase.crossfade if crossfade is None else max(0.0, min(1.0, crossfade))
    if fade <= 0.0 or index == 0:
        return now_look
    into = max(0.0, position - index)
    if into >= fade:
        return now_look
    before = step_look(chase, step_index(chase, index - 1, seed), light)
    if before is None:
        return now_look
    mix = into / fade
    level = max(before[3], now_look[3])  # (the brightness stays at the higher of the two, the colour does the fading:
    if level <= 0.0:  # what the light gives goes straight from one step to the next, never brighter on the way)
        return (*(a + (b - a) * mix for a, b in zip(before[:3], now_look[:3])), 0.0)  # type: ignore[return-value]
    given = [a * before[3] + (b * now_look[3] - a * before[3]) * mix for a, b in zip(before[:3], now_look[:3])]
    return (*(min(1.0, value / level) for value in given), level)  # type: ignore[return-value]


class ChaseRun:
    """A chase playing: which step shows when, from the clock (or the beats), with pauses, Next and speed changes
    keeping its place."""

    def __init__(self, chase: Chase, now: float) -> None:
        self.chase = chase
        self.seq = next(_SEQ)
        self.anchor = now  # engine time at which it was at step 0 (moved by pauses, Next and speed changes)
        self.paused_at: float | None = None
        self.lights = list(chase.lights)
        self.beats = 0  # beats heard (step on beat)
        self.last_beat_at: float | None = None
        self.beat_gap = chase.step_seconds()
        self._beat_level = 0.0

    # --- where it is

    def position(self, now: float) -> float:
        """How many steps have gone by (with the fraction into the current one)."""
        if self.chase.on_beat:
            if self.last_beat_at is None:
                return float(self.beats)
            moment = self.paused_at if self.paused_at is not None else now
            into = (moment - self.last_beat_at) / max(0.05, self.beat_gap)
            return self.beats + min(0.999, max(0.0, into))
        moment = self.paused_at if self.paused_at is not None else now
        return max(0.0, (moment - self.anchor) / self.chase.step_seconds())

    def pass_length(self) -> int:
        return pass_length(self.chase)

    def last_index(self) -> int:
        return last_index(self.chase)

    def step_of(self, index: int) -> int:
        """Which step (0-based) shows at the index'th step time."""
        return step_index(self.chase, index, str(self.seq))

    def finished(self, now: float) -> bool:
        return finished(self.chase, self.position(now))

    def current(self, now: float) -> int:
        return self.step_of(whole_steps(self.position(now)))

    def look(self, light: str, now: float) -> tuple[float, float, float, float] | None:
        """The light's (r, g, b, brightness) now, crossfading into each step from the one before."""
        if light not in self.lights:
            return None
        return chase_look(self.chase, light, self.position(now), str(self.seq))

    # --- changes while it plays

    def update(self, chase: Chase, now: float) -> None:
        """A change (speed, crossfade, direction, steps) from now on, from the same place."""
        position = self.position(now)
        self.lights = [light for light in chase.lights if light in self.lights or light not in self.chase.lights]
        was_on_beat, self.chase = self.chase.on_beat, chase
        if chase.on_beat and not was_on_beat:  # (from here it waits for beats, on the step it's on)
            self.beats, self.last_beat_at = whole_steps(position), None
        if not chase.on_beat:
            moment = self.paused_at if self.paused_at is not None else now
            self.anchor = moment - position * chase.step_seconds()

    def set_paused(self, paused: bool, now: float) -> None:
        if paused and self.paused_at is None:
            self.paused_at = now
        elif not paused and self.paused_at is not None:
            if not self.chase.on_beat:
                self.anchor += now - self.paused_at
            elif self.last_beat_at is not None:
                self.last_beat_at += now - self.paused_at
            self.paused_at = None

    def next_step(self, now: float) -> None:
        if self.chase.on_beat:
            self.beats += 1
            self.last_beat_at = now
            return
        target = float(whole_steps(self.position(now)) + 1)
        moment = self.paused_at if self.paused_at is not None else now
        self.anchor = moment - target * self.chase.step_seconds()

    def hear(self, beat: float, now: float) -> None:
        """audio.beat this tick: each new beat moves an on-beat chase on a step."""
        rising = beat >= BEAT_RISE and beat > self._beat_level + BEAT_JUMP
        self._beat_level = beat
        if not rising or not self.chase.on_beat or self.paused_at is not None:
            return
        if self.last_beat_at is not None:
            gap = now - self.last_beat_at
            if 0.15 < gap < 3.0:
                self.beat_gap = 0.6 * self.beat_gap + 0.4 * gap
        self.beats += 1
        self.last_beat_at = now

    def to_dict(self, now: float) -> dict[str, Any]:
        return {"chase": chase_to_dict(self.chase), "position": round(self.position(now), 4),
                "paused": self.paused_at is not None, "lights": list(self.lights)}


class ConsoleState:
    """What the Console holds (the engine keeps one): each light's held attributes, the ones fading back after a
    Release, and the chase. merge() works out a light's channels each tick."""

    def __init__(self) -> None:
        self.held: dict[str, dict[str, _Held]] = {}
        self.releasing: dict[str, dict[str, _Releasing]] = {}
        self.chase: ChaseRun | None = None
        self.profiles: dict[str, str] = {}  # light id -> its profile (which channels it has)
        self._last: dict[str, dict[str, float]] = {}  # each light's channels as last worked out (before Master)
        self._taps: list[float] = []
        self._inputs: tuple[Any, Any] | None = None  # (light ids, their colours from the inputs this tick)
        self._index: tuple[Any, dict[str, int]] = (None, {})  # (the inputs' light list, each light's place in it)
        self._kept: dict[str, dict[str, _Held]] | None = None  # (the colour picker: what to put back on Cancel)

    @property
    def active(self) -> bool:
        return bool(self.held or self.releasing or self.chase is not None)

    def set_lights(self, profiles: Mapping[str, str]) -> None:
        """The show's colour lights (light id -> profile). Held lights that aren't there any more are let go."""
        self.profiles = dict(profiles)
        for table in (self.held, self.releasing, self._last):
            for light in [light for light in table if light not in self.profiles]:
                del table[light]
        for table in (self.held, self.releasing):  # (a light with another profile now: only the channels it has)
            for light, attrs in list(table.items()):
                for attr in [attr for attr in attrs if attr not in (self.attributes(light) or COLOUR)]:
                    del attrs[attr]
                if not attrs:
                    del table[light]
        if self.chase is not None:
            self.chase.lights = [light for light in self.chase.lights if light in self.profiles]
            if not self.chase.lights:
                self.chase = None

    def attributes(self, light: str) -> tuple[str, ...]:
        return tuple(attr for attr, _name in channels_of(self.profiles.get(light, "")))

    def note_inputs(self, ids: Any, colours: Any) -> None:
        """The inputs' colours this tick (the engine's ids and its (n, 3) array, before Master): what a light nobody
        holds shows, for a change to fade in from. Kept as they are (no copy): it's every tick."""
        self._inputs = (ids, colours)

    def shown(self, light: str, attr: str) -> float:
        """What the attribute shows now (0-255, before Master): where a change fades from."""
        if light in self.held or light in self.releasing or (self.chase is not None and light in self.chase.lights):
            last = self._last.get(light)
            if last is not None and attr in last:
                return last[attr]
        if attr in COLOUR and self._inputs is not None:
            ids, colours = self._inputs
            if self._index[0] is not ids:
                self._index = (ids, {light_id: index for index, light_id in enumerate(ids)})
            index = self._index[1].get(light)
            if index is not None and index < len(colours):
                return max(0.0, min(1.0, float(colours[index][COLOUR.index(attr)]))) * 255.0
        return DEFAULT_VALUES.get(attr, 0.0)

    def held_lights(self) -> list[str]:
        """The lights the Console holds (Live changes or the chase)."""
        lights = [light for light, attrs in self.held.items() if attrs]
        if self.chase is not None:
            lights += [light for light in self.chase.lights if light not in lights]
        return lights

    # --- Live changes and Release

    def set(self, values: Mapping[str, Mapping[str, float]], fade: float, now: float) -> None:
        """Hold these values (light id -> attribute -> 0-255), fading in over `fade` seconds (strobe, the colour
        macro, programs and dimmer speed at once). Later than any chase on those lights, so they win there."""
        seq = next(_SEQ)
        for light, attrs in values.items():
            if light not in self.profiles:
                continue
            known = self.attributes(light)
            origins = {attr: self.shown(light, attr) for attr in attrs if attr in known}  # (before it's held: from
            held = self.held.setdefault(light, {})  # what it shows now, the inputs' colour if nothing holds it)
            for attr, value in attrs.items():
                if attr not in known:
                    continue
                value = max(0.0, min(255.0, float(value)))
                fade_in = 0.0 if attr in SNAP_BACK else max(0.0, float(fade))
                held[attr] = _Held(value, origins[attr], now, fade_in, seq)
                self.releasing.get(light, {}).pop(attr, None)
            if not held:
                del self.held[light]

    def release(self, lights: Iterable[str] | None, fade: float, now: float, only: Iterable[str] | None = None) -> list[str]:
        """Hand these lights (None: all) back to the inputs, fading over `fade` seconds (strobe, the colour macro,
        programs and dimmer speed snap back). A chase lets them go too, and stops when it has none left. only: just
        these attributes (a chase keeps its lights then). Returns the lights released."""
        if lights is None:
            chosen = set(self.held) | set(self.releasing) | set(self.chase.lights if self.chase is not None else ())
        else:
            chosen = set(lights)
        wanted = set(only) if only is not None else None
        done = []
        for light in sorted(chosen):
            hurried = self._hurry(light, fade, now, wanted)
            held = self.held.get(light, {})
            attrs = {attr for attr in held if wanted is None or attr in wanted}
            for attr in attrs:
                del held[attr]
            if not held:
                self.held.pop(light, None)
            if wanted is None and self.chase is not None and light in self.chase.lights:
                self.chase.lights.remove(light)
                attrs |= set(CHASED) & set(self.attributes(light) or COLOUR)  # (a dimmer has no colour to fade)
            if not attrs:
                if hurried:
                    done.append(light)
                continue
            done.append(light)
            fading = self.releasing.setdefault(light, {})
            for attr in attrs:
                if attr in SNAP_BACK or fade <= 0.0:
                    fading.pop(attr, None)
                else:
                    fading[attr] = _Releasing(self.shown(light, attr), now, float(fade))
            if not fading:
                del self.releasing[light]
        if self.chase is not None and wanted is None and (lights is None or not self.chase.lights):
            self.chase = None
        self._tidy()
        return done

    def _tidy(self) -> None:
        """Nothing held, fading back or chasing: what was last worked out for each light goes (the engine stops working
        it out now, so it would go stale)."""
        if not self.active:
            self._last.clear()

    def keep(self, lights: Iterable[str]) -> None:
        """The colour picker opens: what the Console holds of these lights' colour, as it is (Cancel puts it back)."""
        self._kept = {light: {attr: replace(h) for attr, h in self.held.get(light, {}).items() if attr in COLOUR}
                      for light in lights if light in self.profiles}

    def put_back(self, now: float) -> None:
        """Cancel in the colour picker: the kept lights' colour exactly as it was (when each was set, too, so a chase
        played since still wins where it did); colour that wasn't held follows the inputs again at once."""
        kept, self._kept = self._kept, None
        for light, attrs in (kept or {}).items():
            held = self.held.setdefault(light, {})
            for attr in COLOUR:
                if attr in attrs:
                    held[attr] = attrs[attr]
                else:
                    held.pop(attr, None)
                self.releasing.get(light, {}).pop(attr, None)
            if not held:
                del self.held[light]
            if light in self.releasing and not self.releasing[light]:
                del self.releasing[light]
        self._tidy()

    def _hurry(self, light: str, fade: float, now: float, wanted: set[str] | None) -> bool:
        """The light's attributes already fading back take no longer than `fade` from now (a Cut to black ends them at
        once). True if any changed."""
        fading = self.releasing.get(light)
        changed = False
        for attr, old in list((fading or {}).items()):
            if wanted is not None and attr not in wanted:
                continue
            if fade <= 0.0 or attr in SNAP_BACK:
                del fading[attr]
                changed = True
            elif old.start + old.fade - now > fade:
                fading[attr] = _Releasing(self.shown(light, attr), now, float(fade))
                changed = True
        if fading is not None and not fading:
            del self.releasing[light]
        return changed

    def _let_go(self, lights: Iterable[str], fade: float, now: float) -> None:
        """Lights a chase no longer has fade back to the inputs (what a later Live change holds stays)."""
        for light in lights:
            if light not in self.profiles:
                continue
            held = self.held.get(light, {})
            fading = self.releasing.setdefault(light, {})
            for attr in set(CHASED) & set(self.attributes(light) or COLOUR):
                if attr in held:
                    continue
                if fade > 0.0:
                    fading[attr] = _Releasing(self.shown(light, attr), now, float(fade))
                else:
                    fading.pop(attr, None)
            if not fading:
                del self.releasing[light]

    # --- the chase

    def play(self, chase: Chase, now: float, fade: float = 0.0) -> bool:
        """Play a chase on its lights (in the show), from its first step; lights the chase before had that this one
        hasn't fade back over `fade`. False if it has no steps or lights here."""
        lights = [light for light in chase.lights if light in self.profiles]
        if not lights or not chase.steps:
            return False
        if self.chase is not None:
            self._let_go([light for light in self.chase.lights if light not in lights], fade, now)
        self.chase = ChaseRun(replace(chase, lights=tuple(lights)), now)
        for light in lights:
            for attr in CHASED:
                self.releasing.get(light, {}).pop(attr, None)
        return True

    def update_chase(self, chase: Chase, now: float, fade: float = 0.0) -> None:
        """The chase changed while it plays (speed, crossfade, direction, steps): it carries on from where it is. Lights
        it no longer has fade back over `fade`."""
        if self.chase is not None:
            before = list(self.chase.lights)
            self.chase.update(replace(chase, lights=tuple(light for light in chase.lights if light in self.profiles)), now)
            self._let_go([light for light in before if light not in self.chase.lights], fade, now)

    def stop_chase(self, fade: float, now: float) -> None:
        """The chase stops: its lights fade back over `fade` (what Live changes hold on them stays)."""
        if self.chase is not None:
            lights, self.chase = list(self.chase.lights), None
            self._let_go(lights, fade, now)
            self._tidy()

    def pause_chase(self, paused: bool, now: float) -> None:
        if self.chase is not None:
            self.chase.set_paused(paused, now)

    def next_step(self, now: float) -> None:
        if self.chase is not None:
            self.chase.next_step(now)

    def set_speed(self, bpm: float, now: float) -> None:
        """The playing chase's speed in beats a minute (one step a beat), from where it is (the Stream Deck's)."""
        if self.chase is not None:
            bpm = max(BPM_RANGE[0], min(BPM_RANGE[1], float(bpm)))
            self.chase.update(replace(self.chase.chase, bpm=bpm, seconds=None), now)

    def tap(self, now: float) -> float | None:
        """A tap of the Tap button (the API, or a controller later): the speed (BPM) the last few taps make, once
        there are two (None before). A playing chase takes it at once, from where it is."""
        self._taps, bpm = tap_tempo(self._taps, now)
        if bpm is not None and self.chase is not None:
            self.chase.update(replace(self.chase.chase, bpm=bpm, seconds=None), now)
        return bpm

    # --- each tick

    def hear(self, beat: float, now: float) -> None:
        if self.chase is not None:
            self.chase.hear(beat, now)

    def merge(self, light: str, rgb: tuple[float, float, float], now: float) -> tuple[tuple[float, float, float], dict[str, int]]:
        """The light's colour (0-1, before Master) and its other channels (0-255; only those not at their usual
        value), with what the Console holds on top of what the inputs say."""
        base = {"red": rgb[0] * 255.0, "green": rgb[1] * 255.0, "blue": rgb[2] * 255.0}
        values = dict(base)
        held = self.held.get(light)
        releasing = self.releasing.get(light)
        run = self.chase if self.chase is not None and light in self.chase.lights else None
        look = run.look(light, now) if run is not None else None
        attrs = self.attributes(light) or COLOUR
        level_only = "red" not in attrs  # (a dimmer used as a colour light: its brightness is what its colour gives)
        spans: dict[str, tuple[float, float, float, float]] = {}  # attr -> (from, to, start, seconds) while it fades
        for attr in attrs:
            usual = max(base.values()) if level_only else base.get(attr, DEFAULT_VALUES.get(attr, 0.0))
            value = usual
            hold = held.get(attr) if held else None
            from_chase = None
            if look is not None and attr in CHASED:
                from_chase = look[3] * max(look[:3]) * 255.0 if level_only else look[CHASED.index(attr)] * 255.0
            if hold is not None and (from_chase is None or run is None or hold.seq > run.seq):
                value = hold.at(now)
                if hold.fade > 0.0 and now < hold.start + hold.fade:
                    spans[attr] = (hold.origin, hold.value, hold.start, hold.fade)
            elif from_chase is not None:
                value = from_chase
            elif releasing and attr in releasing:
                weight = releasing[attr].weight(now)
                if weight <= 0.0:
                    del releasing[attr]
                else:
                    value = usual + (releasing[attr].origin - usual) * weight
                    spans[attr] = (releasing[attr].origin, usual, releasing[attr].start, releasing[attr].fade)
            values[attr] = value
        if releasing is not None and not releasing:
            del self.releasing[light]
        if not level_only and BRIGHTNESS in spans:
            self._straight(values, spans, now)
        if light in self.held or light in self.releasing or run is not None:
            self._last[light] = values
        else:
            self._last.pop(light, None)  # (nobody holds it: a later change fades from what the inputs show then)
        if level_only:
            level = max(0.0, min(255.0, values.get(BRIGHTNESS, 0.0))) / 255.0
            return (level, level, level), {}  # (the encoder gives a dimmer the brightest of its colour, Master too)
        raw = {attr: max(0, min(255, round(values[attr]))) for attr in RAW
               if attr in values and round(values[attr]) != round(DEFAULT_VALUES.get(attr, 0.0))}
        return (values["red"] / 255.0, values["green"] / 255.0, values["blue"] / 255.0), raw

    @staticmethod
    def _straight(values: dict[str, float], spans: Mapping[str, tuple[float, float, float, float]], now: float) -> None:
        """While the dimmer fades, what the light gives (colour times dimmer) goes straight from one end to the other:
        the dimmer stays at the higher end and the colour does the fading (two straight fades multiplied bulge: a
        dimmed light would flare on Release or Fade to black)."""
        first, last, start, seconds = spans[BRIGHTNESS]
        top = max(first, last)
        if top <= 0.0:
            return
        progress = max(0.0, min(1.0, (now - start) / seconds)) if seconds > 0.0 else 1.0
        dimmer = values[BRIGHTNESS]
        for attr in TINTED:
            if attr not in values:
                continue
            span = spans.get(attr)
            if span is not None and span[2] == start and span[3] == seconds:  # (faded together)
                given = span[0] * first + (span[1] * last - span[0] * first) * progress
            else:
                given = values[attr] * dimmer
            values[attr] = min(255.0, given / top)
        values[BRIGHTNESS] = top

    def view(self, values: Mapping[str, tuple[int, ...]], now: float) -> ConsoleView:
        """For the window: the held attributes (a chase's lights hold colour and brightness) and the channels."""
        held: dict[str, frozenset[str]] = {light: frozenset(attrs) for light, attrs in self.held.items() if attrs}
        chase = None
        if self.chase is not None:
            for light in self.chase.lights:
                held[light] = held.get(light, frozenset()) | frozenset(CHASED)
            run = self.chase
            chase = ChaseView(run.chase, playing=True, paused=run.paused_at is not None, step=run.current(now),
                              lights=tuple(run.lights))
        levels = {light: MappingProxyType({attr: round(h.value) for attr, h in attrs.items()})
                  for light, attrs in self.held.items() if attrs}
        return ConsoleView(held=MappingProxyType(held), values=MappingProxyType(dict(values)), chase=chase,
                           levels=MappingProxyType(levels))

    # --- live.json

    def to_dict(self, now: float) -> dict[str, Any]:
        data: dict[str, Any] = {}
        held = {light: {attr: round(h.value, 2) for attr, h in attrs.items()} for light, attrs in self.held.items() if attrs}
        if held:
            data["held"] = held
        if self.chase is not None:
            data["chase"] = self.chase.to_dict(now)
            later = {light: sorted(attr for attr, h in attrs.items() if h.seq > self.chase.seq)
                     for light, attrs in self.held.items() if light in self.chase.lights}
            later = {light: attrs for light, attrs in later.items() if attrs}
            if later:
                data["later"] = later  # (held values set after the chase started: they win over it again on a reopen)
        return data

    def restore(self, data: Any, now: float) -> None:
        """What a reopen carries on with (live.json): the held values at once, and the chase from where it was."""
        if not isinstance(data, Mapping):
            return
        held = data.get("held")
        raw_later = data.get("later")
        later = {str(light): {str(attr) for attr in attrs} for light, attrs in raw_later.items()
                 if isinstance(attrs, list)} if isinstance(raw_later, Mapping) else {}
        values = {}
        if isinstance(held, Mapping):
            values = {str(light): {str(a): finite(v, -1.0) for a, v in attrs.items()}
                      for light, attrs in held.items() if isinstance(attrs, Mapping)}
            values = {light: {a: v for a, v in attrs.items() if v >= 0.0} for light, attrs in values.items()}
        self.set({light: {a: v for a, v in attrs.items() if a not in later.get(light, ())}
                  for light, attrs in values.items()}, 0.0, now)
        try:
            self._restore_chase(data.get("chase"), now)
        finally:
            self.set({light: {a: v for a, v in attrs.items() if a in later.get(light, ())}
                      for light, attrs in values.items()}, 0.0, now)

    def _restore_chase(self, saved: Any, now: float) -> None:
        if not isinstance(saved, Mapping):
            return
        chase = chase_from_dict(saved.get("chase"))
        if chase is None:
            return
        kept = saved.get("lights")
        lights = tuple(str(light) for light in kept if str(light) in chase.lights) if isinstance(kept, list) else chase.lights
        if not self.play(replace(chase, lights=lights or chase.lights), now) or self.chase is None:
            return
        position = max(0.0, min(1e6, finite(saved.get("position"), 0.0)))  # (a million steps: more than any reopen needs)
        if chase.on_beat:
            self.chase.beats = whole_steps(position)
        else:
            self.chase.anchor = now - position * chase.step_seconds()
        if saved.get("paused") is True:
            self.chase.set_paused(True, now)


# ---------------------------------------------------------------- choosing lights, fans and quick chases (the page uses these)


def choose(kind: str, lights: list[str], chosen: list[str]) -> list[str]:
    """All, None, Invert, Odd or Even. lights: every light, in the map's order; chosen: the chosen ones, in order.
    Odd and Even take every other one of the chosen lights (of all of them when none are chosen)."""
    if kind == "all":
        return list(lights)
    if kind == "none":
        return []
    if kind == "invert":
        return [light for light in lights if light not in chosen]
    if kind in ("odd", "even"):
        base = chosen or list(lights)
        return base[0::2] if kind == "odd" else base[1::2]
    return list(chosen)


def hue_colour(fraction: float) -> tuple[float, float, float]:
    """A full colour round the colour wheel (0 red, 1/3 green, 2/3 blue)."""
    import colorsys

    return colorsys.hsv_to_rgb(fraction % 1.0, 1.0, 1.0)


def fan(lights: list[str], kind: str, start: tuple[float, float, float] = (1.0, 0.0, 0.0),
        end: tuple[float, float, float] = (0.0, 0.0, 1.0), low: float = 0.0, high: float = 1.0) -> dict[str, dict[str, float]]:
    """Spread values across the lights in their order (light id -> attribute -> 0-255): "rainbow" (round the colour
    wheel), "colours" (from one colour to another) or "brightness" (their dimmers, from low to high, 0-1)."""
    count = len(lights)
    values: dict[str, dict[str, float]] = {}
    for index, light in enumerate(lights):
        place = index / (count - 1) if count > 1 else 0.0
        if kind == "rainbow":
            rgb = hue_colour(index / max(1, count))
        elif kind == "colours":
            rgb = tuple(a + (b - a) * place for a, b in zip(start, end))
        elif kind == "brightness":
            values[light] = {"dimmer": round((low + (high - low) * place) * 255.0, 2)}
            continue
        else:
            continue
        values[light] = {attr: round(v * 255.0, 2) for attr, v in zip(COLOUR, rgb)}
    return values


def colours_across(lights: list[str], colours: list[tuple[float, float, float]], brightness: float = 1.0) -> tuple[ChaseStep, ...]:
    """A quick chase: the colours along the lights, moving on one light each step (as many steps as colours)."""
    if not lights or not colours:
        return ()
    steps = []
    for step in range(len(colours)):
        looks = {light: (*colours[(index + step) % len(colours)], brightness) for index, light in enumerate(lights)}
        steps.append(ChaseStep(MappingProxyType(looks)))
    return tuple(steps)


def one_at_a_time(lights: list[str], colour: tuple[float, float, float], brightness: float = 1.0) -> tuple[ChaseStep, ...]:
    """A quick chase: a running light, one light on at a time in their order, the others off."""
    return tuple(ChaseStep(MappingProxyType({light: (*(colour if index == step else (0.0, 0.0, 0.0)), brightness)
                                             for index, light in enumerate(lights)}))
                 for step in range(len(lights)))


def odd_even(lights: list[str], colour: tuple[float, float, float], brightness: float = 1.0) -> tuple[ChaseStep, ...]:
    """A quick chase: the odd lights on, then the even ones (in their order)."""
    if not lights:
        return ()
    return tuple(ChaseStep(MappingProxyType({light: (*(colour if index % 2 == parity else (0.0, 0.0, 0.0)), brightness)
                                             for index, light in enumerate(lights)}))
                 for parity in (0, 1))


def step_from(lights: list[str], values: Mapping[str, tuple[int, ...]], profiles: Mapping[str, str],
              master: float = 1.0) -> ChaseStep:
    """Add step: the lights as they look now (each light's channels as sent, with Master taken back out of the colour,
    since Master scales the chase again when it plays)."""
    looks = {}
    scale = 1.0 / master if master > 0.01 else 1.0
    for light in lights:
        channels = values.get(light)
        names = [attr for attr, _name in channels_of(profiles.get(light, ""))]
        if not channels or not all(attr in names for attr in (*COLOUR, BRIGHTNESS)):
            continue
        r, g, b = (min(1.0, channels[names.index(attr)] / 255.0 * scale) for attr in COLOUR)
        dimmer = channels[names.index(BRIGHTNESS)] / 255.0
        looks[light] = (round(r, 4), round(g, 4), round(b, 4), round(dimmer, 4))
    return ChaseStep(MappingProxyType(looks))


def tap_tempo(taps: list[float], now: float) -> tuple[list[float], float | None]:
    """Tap tempo: the taps kept (a long gap starts a new count) and the speed they make (BPM), once there are two."""
    if taps and not 0.0 <= now - taps[-1] <= TAP_GAP:
        taps = []
    if taps and now - taps[-1] < TAP_BOUNCE:
        return taps, None  # (the same tap again: no new speed)
    taps = (taps + [now])[-TAPS_KEPT:]
    if len(taps) < 2:
        return taps, None
    gaps = [b - a for a, b in zip(taps, taps[1:])]
    return taps, max(BPM_RANGE[0], min(BPM_RANGE[1], 60.0 / (sum(gaps) / len(gaps))))


def chase_to_dict(chase: Chase) -> dict[str, Any]:
    data: dict[str, Any] = {
        "lights": list(chase.lights),
        "steps": [{light: [round(float(v), 4) for v in look] for light, look in step.looks.items()} for step in chase.steps],
        "bpm": round(float(chase.bpm), 2),
        "crossfade": round(float(chase.crossfade), 3),
        "direction": chase.direction,
        "loop": chase.loop,
    }
    if chase.seconds is not None:
        data["seconds"] = round(float(chase.seconds), 3)
    if chase.on_beat:
        data["on_beat"] = True
    return data


def chase_from_dict(data: Any) -> Chase | None:
    """A chase from a file (or live.json); None if it can't be used. Odd values are put right rather than refused."""
    if not isinstance(data, Mapping):
        return None
    raw_lights = data.get("lights")
    lights = tuple(str(light) for light in raw_lights if isinstance(light, str)) if isinstance(raw_lights, list) else ()
    steps = []
    raw_steps = data.get("steps")
    for raw in raw_steps if isinstance(raw_steps, list) else ():
        if not isinstance(raw, Mapping):
            continue
        looks = {}
        for light, look in raw.items():
            if isinstance(look, (list, tuple)) and len(look) == 4 and all(finite(v, -1e10) > -1e10 for v in look):
                looks[str(light)] = tuple(max(0.0, min(1.0, finite(v, 0.0))) for v in look)
        steps.append(ChaseStep(MappingProxyType(looks)))
    if not lights or not steps:
        return None

    def number(key: str, default: float, low: float, high: float) -> float:
        return max(low, min(high, finite(data.get(key, default), default)))

    seconds = data.get("seconds")
    direction = data.get("direction", "forward")
    return Chase(
        lights=lights, steps=tuple(steps), bpm=number("bpm", 120.0, *BPM_RANGE),
        seconds=number("seconds", 1.0, 0.05, 600.0) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None,
        crossfade=number("crossfade", 0.0, 0.0, 1.0),
        direction=direction if isinstance(direction, str) and direction in dict(DIRECTIONS) else "forward",
        loop=data.get("loop", True) is not False, on_beat=data.get("on_beat") is True)
