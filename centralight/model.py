"""The show in memory: lights, house lights, global variables and inputs.

Everything here is immutable (frozen dataclasses, tuples, read-only mappings), so the
engine can keep using one snapshot while the screen builds the next one.

Words used throughout CentraLight:
- fixture: one DMX light (or dimmer) from the patch
- colour light: an RGB fixture on the 16:9 map (the SlimPARs)
- house light: a dimmer that isn't on the map (the festoon, the key lights)
- input: a look or sequence, built from elements on up to 4 tracks
- layout: a saved arrangement of the map (where each light sits, and the room photo)
- automation: "when these conditions are met, do this" (e.g. Sunday 09:00-12:30 -> Warm welcome)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from fixtures import PROFILES

ROLE_COLOUR = "colour"
ROLE_HOUSE = "house"
ROLES = (ROLE_COLOUR, ROLE_HOUSE)

TRACK_COUNT = 4
END_HOLD = "hold"
END_LOOP = "loop"
END_STOP = "stop"
END_BEHAVIOURS = (END_HOLD, END_LOOP, END_STOP)
TRANSITION_KINDS = ("cut", "fade", "dip", "wipe", "iris", "random")
GLOBAL_TYPES = ("number", "colour", "switch")

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
COLOUR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

EMPTY: Mapping[str, Any] = MappingProxyType({})


def frozen_map(data: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """A read-only copy of a dict."""
    return MappingProxyType(dict(data or {}))


def element_span(element: Any, inp: Any) -> tuple[float, float]:
    """(start, end) of an element in seconds of the input. Infinite inputs: the whole time."""
    if inp.infinite:
        return 0.0, float("inf")
    start = element.get("start", 0.0)
    end = element.get("end", inp.length)
    start = float(start) if isinstance(start, (int, float)) and not isinstance(start, bool) else 0.0
    end = float(end) if isinstance(end, (int, float)) and not isinstance(end, bool) else float(inp.length)
    return start, max(start, end)


def input_time(inp: Any, elapsed: float) -> float:
    """Where a running input is in its own time, `elapsed` seconds after it was sent. Infinite: just that.
    Timed: "loop" goes round again, "hold" stays on its last moment, "stop" runs on past the end (where
    nothing plays, and the engine takes it off)."""
    if inp.infinite:
        return elapsed
    length = max(float(inp.length), 1e-3)
    if inp.end == END_LOOP:
        return elapsed % length
    if inp.end == END_HOLD:
        return min(elapsed, length - END_EPSILON)
    return elapsed


def input_progress(inp: Any, elapsed: float) -> float | None:
    """How far through a timed input is (0-1), or None for an infinite one."""
    if inp.infinite:
        return None
    length = max(float(inp.length), 1e-3)
    return min(1.0, max(0.0, (elapsed % length if inp.end == END_LOOP else elapsed) / length))


def has_finished(inp: Any, elapsed: float) -> bool:
    """A timed input that stops at its end has got there."""
    return not inp.infinite and inp.end == END_STOP and elapsed >= inp.length


def slugify(text: str, fallback: str = "input") -> str:
    """'Pillars pulse!' -> 'pillars-pulse' (a safe id and file name)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or fallback)[:64].strip("-") or fallback


PATCH_RADIUS = 0.08  # every light follows the same-sized patch of the picture: this radius, in screen widths
END_EPSILON = 1e-6  # seconds: "hold" shows the input this close to its end (elements end at the length)


@dataclass(frozen=True)
class MapSpot:
    """Where a colour light sits on the 16:9 map: x, y from 0 to 1. Every light follows the same-sized patch of
    the picture around its dot (PATCH_RADIUS); how much picture the lights average is each element's Blur."""

    x: float = 0.5
    y: float = 0.5


@dataclass(frozen=True)
class Fixture:
    id: str
    name: str
    profile: str
    address: int  # DMX start channel, 1-512
    group: str = ""
    role: str = ROLE_COLOUR
    map: MapSpot | None = None

    @property
    def channels(self) -> int:
        return int(PROFILES[self.profile]["channels"])

    @property
    def last_channel(self) -> int:
        return self.address + self.channels - 1

    @property
    def is_colour(self) -> bool:
        return self.role == ROLE_COLOUR

    def channel_range(self) -> str:
        """'5-13' for a 9-channel light, '1' for a 1-channel dimmer."""
        last = self.last_channel
        return str(self.address) if last == self.address else f"{self.address}-{last}"


@dataclass(frozen=True)
class HouseLight:
    fixture: str
    label: str


@dataclass(frozen=True)
class GlobalVar:
    name: str
    type: str = "number"
    default: Any = 0.0
    min: float = 0.0
    max: float = 1.0
    dashboard: bool = False


@dataclass(frozen=True)
class Transition:
    kind: str = "fade"
    duration: float = 1.0
    direction: float = 0.0  # wipe: degrees, 0 = left to right
    softness: float = 0.25  # wipe and iris: how soft the moving edge is (0-1)


@dataclass(frozen=True)
class Input:
    """One input: a look or sequence. Elements stay as plain data until the editor milestones."""

    id: str
    name: str
    colour: str = "#86D6FF"
    infinite: bool = True
    length: float = 10.0
    end: str = END_HOLD
    transition: Transition = Transition()
    hotkey: str = ""
    ai: bool = False
    house: Mapping[str, Any] = EMPTY  # fixture id -> level Param
    locals: tuple[Mapping[str, Any], ...] = ()
    tracks: tuple[tuple[Mapping[str, Any], ...], ...] = ((),) * TRACK_COUNT


@dataclass(frozen=True)
class Layout:
    """A saved map layout: where each colour light sits, plus the room photo behind the map.
    The active layout's positions are also each fixture's map (the working copy)."""

    id: str
    name: str
    positions: Mapping[str, MapSpot] = EMPTY  # fixture id -> spot
    backdrop: str = ""  # the room photo, relative to the show folder ("" = none)
    backdrop_opacity: float = 0.55


@dataclass(frozen=True)
class Automation:
    """Runs its actions once when its conditions become true (see automations.py).
    Conditions and actions stay as plain data, like an input's elements."""

    id: str
    name: str
    enabled: bool = True
    logic: str = "all"  # "all" (every condition) or "any" (at least one)
    conditions: tuple[Mapping[str, Any], ...] = ()  # {"type": ..., "invert": bool, ...settings}
    actions: tuple[Mapping[str, Any], ...] = ()  # {"do": ..., "delay": seconds, ...}
    on_end: tuple[Mapping[str, Any], ...] = ()  # actions when it stops matching


@dataclass(frozen=True)
class Show:
    name: str = "Central Church"
    identify_seed: int = 7
    fixtures: tuple[Fixture, ...] = ()
    house: tuple[HouseLight, ...] = ()
    globals: tuple[GlobalVar, ...] = ()
    input_order: tuple[str, ...] = ()
    inputs: Mapping[str, Input] = EMPTY
    layouts: tuple[Layout, ...] = ()
    active_layout: str = ""
    automations: Mapping[str, Automation] = EMPTY
    automation_order: tuple[str, ...] = ()  # the order they're listed and run in
    # Not saved: where the show was loaded from and what was wrong with it
    folder: str = ""
    read_only: bool = False
    warnings: tuple[str, ...] = ()
    input_errors: Mapping[str, str] = EMPTY  # input id -> why its file couldn't be read

    def fixture(self, fixture_id: str) -> Fixture | None:
        for fx in self.fixtures:
            if fx.id == fixture_id:
                return fx
        return None

    def colour_fixtures(self) -> tuple[Fixture, ...]:
        return tuple(fx for fx in self.fixtures if fx.role == ROLE_COLOUR)

    def house_fixtures(self) -> tuple[Fixture, ...]:
        return tuple(fx for fx in self.fixtures if fx.role == ROLE_HOUSE)

    def layout(self, layout_id: str | None = None) -> Layout | None:
        """A saved layout by id (the active one if no id is given)."""
        wanted = self.active_layout if layout_id is None else layout_id
        for layout in self.layouts:
            if layout.id == wanted:
                return layout
        return None

    def house_label(self, fixture_id: str) -> str:
        for item in self.house:
            if item.fixture == fixture_id:
                return item.label
        fx = self.fixture(fixture_id)
        return fx.name if fx else fixture_id

    def ordered_inputs(self) -> list[Input]:
        """Inputs in dashboard order; inputs missing from the order go at the end."""
        seen: set[str] = set()
        result: list[Input] = []
        for input_id in self.input_order:
            inp = self.inputs.get(input_id)
            if inp is not None and input_id not in seen:
                result.append(inp)
                seen.add(input_id)
        for input_id in sorted(self.inputs):
            if input_id not in seen:
                result.append(self.inputs[input_id])
        return result

    def ordered_automations(self) -> list[Automation]:
        """Automations in their run order; any missing from the order go at the end."""
        seen = [a for a in (self.automations.get(i) for i in dict.fromkeys(self.automation_order)) if a is not None]
        ids = {a.id for a in seen}
        return seen + [self.automations[i] for i in sorted(self.automations) if i not in ids]

    def with_automations(self, automations: Mapping[str, Automation], order: tuple[str, ...] | None = None) -> Show:
        return replace(self, automations=frozen_map(automations), automation_order=order if order is not None else self.automation_order)

    def with_inputs(self, inputs: Mapping[str, Input], order: tuple[str, ...] | None = None) -> Show:
        return replace(self, inputs=frozen_map(inputs), input_order=order if order is not None else self.input_order)
