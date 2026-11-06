"""The show in memory: lights, house lights, show variables ("globals" in the files) and inputs.

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

from .fixtures import PROFILES

ROLE_COLOUR = "colour"
ROLE_HOUSE = "house"
ROLES = (ROLE_COLOUR, ROLE_HOUSE)

TRACK_COUNT = 4
END_HOLD = "hold"
END_LOOP = "loop"
END_STOP = "stop"
END_BEHAVIOURS = (END_HOLD, END_LOOP, END_STOP)
# what an input's Send to Overlay button does (M18): each press adds a copy that stays until it's cleared, or the copy
# shows only while the button is held down (letting go clears it)
OVERLAY_STAYS = "stays"
OVERLAY_HELD = "held"
OVERLAY_MODES = (OVERLAY_STAYS, OVERLAY_HELD)
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


# Names Windows keeps for devices, with any extension: a file called "com3.json" (or a folder "COM3.x") is the serial
# port COM3, where the Eurolite is. No file or folder CentraLight makes may have one.
DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$", "clock$"}
                         | {f"{kind}{n}" for kind in ("com", "lpt") for n in "0123456789¹²³"})


def is_device_name(name: str) -> bool:
    """Would Windows take this file or folder name for a device? (Its part before the first dot or colon, trailing
    spaces ignored, any case: "COM3", "aux.json", "Con .txt", "COM3:", "nul:x".)"""
    return re.split(r"[.:]", str(name), maxsplit=1)[0].rstrip(" .").casefold() in DEVICE_NAMES


def safe_text(text: str) -> str:
    """Text from a file, made safe to show: "<" and ">" become "‹" and "›". Qt shows text that looks like HTML as HTML,
    and reads any picture an HTML tag names from the disk: a name like <img src="//./COM3"> would open the Eurolite's
    port just by being shown."""
    return text.replace("<", "‹").replace(">", "›")


def no_markup(value: Any, keep: tuple[str, ...] = ("expr",)) -> Any:
    """safe_text for every string in a file's data (dicts and lists all the way down), except the values of `keep`
    keys: formulas ("a < b") are never shown as HTML, so they keep theirs."""
    if isinstance(value, str):
        return safe_text(value)
    if isinstance(value, Mapping):
        return {k: (v if k in keep else no_markup(v, keep)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [no_markup(v, keep) for v in value]
    return value


NOTES_LIMIT = 2000  # characters in a show's notes


def clean_notes(value: object) -> str:
    """A show's notes as saved: plain text (tabs and line breaks kept, other control characters dropped), at most
    NOTES_LIMIT characters, no blank lines at the ends."""
    if not isinstance(value, str):
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or (ord(ch) >= 32 and ord(ch) != 127))
    return text.strip("\n ")[:NOTES_LIMIT].rstrip()


def is_safe_id(value: object) -> bool:
    """An id that can be a file name: lower-case letters, digits and hyphens, and not a device name."""
    return isinstance(value, str) and bool(ID_PATTERN.match(value)) and not is_device_name(value)


def slugify(text: str, fallback: str = "input") -> str:
    """'Pillars pulse!' -> 'pillars-pulse' (a safe id and file name; 'COM3' -> 'com3-input', never a device's name)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = (slug or fallback)[:64].strip("-") or fallback
    return slug if not is_device_name(slug) else f"{slug}-{fallback}"[:64]


END_EPSILON = 1e-6  # seconds: "hold" shows the input this close to its end (elements end at the length)


@dataclass(frozen=True)
class MapSpot:
    """Where a colour light sits on the 16:9 map: x, y from 0 to 1. A light is just its dot: how much of a picture
    it averages around the dot is each Image, Video or NDI element's Area."""

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
class LightGroup:
    """A group of colour lights saved on the Console page (M15): a button that chooses them, besides the patch's."""

    name: str
    lights: tuple[str, ...]


SAVED_COLOURS_MAX = 24  # the Console's saved colours a show keeps (the oldest goes first)


@dataclass(frozen=True)
class GlobalVar:
    name: str
    type: str = "number"
    default: Any = 0.0  # its starting value
    min: float = 0.0
    max: float = 1.0
    dashboard: bool = False
    on_load: str = "keep"  # when the show loads (M14): "keep" its last value, or "reset" to its starting value


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
    overlay: str = OVERLAY_STAYS  # its Send to Overlay button (M18): "stays" (until cleared) or "held" (while held)
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
    backdrop: str = ""  # the room photo, relative to the show folder ("" = none): CentraLight's own copy
    backdrop_opacity: float = 0.55
    backdrop_original: str = ""  # the photo it was copied from, anywhere on this PC ("" = not linked; see links.py)


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
    notes: str = ""  # a few lines about the show (what it's for), shown in the show menu, the Dashboard and the phone page
    groups: tuple[LightGroup, ...] = ()  # the Console's saved groups of lights (M15), besides the patch's
    saved_colours: tuple[str, ...] = ()  # the Console's saved colours, "#RRGGBB" (M15)
    # Not saved: where the show was loaded from and what was wrong with it
    folder: str = ""
    read_only: bool = False
    no_show: bool = False  # the stand-in while no show is loaded (M14): see NO_SHOW
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


# No show loaded (M14, the Default show "None (lights off)"): the engine sends every channel at 0, the global automations
# carry on, and the window shows "No show loaded". A stand-in show, read-only, with nothing in it.
NO_SHOW = Show(name="None", no_show=True, read_only=True)
NO_DEFAULT_SHOW = "_none"  # Settings → Default show: None (lights off) ("" is Last loaded; a show id, that show)
