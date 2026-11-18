"""Light types (M19, 18 Nov): what each of a light's DMX channels does, so any light can be patched.

A light type is a list of channels in DMX order. Each channel has a job (its role: Dimmer, Red, Strobe, ...), a name
people see, and the value it's sent when nothing sets it, its "normally" (a strobe channel that must be at 255 for
the light to shine, say). CentraLight sets the colour and brightness channels itself; the Console's Advanced faders
reach the rest.

- Built-in types keep the ids show files already use (slimpar9, lpc018, dimmer).
- Any other type's id is made from what it is (a hash of its name and channels), so one id always means the same
  channels, in every show; changing a type makes a new id. A show file carries the types its lights use
  ("light_types" in show.json), so a show (or a .clshow) brings its own.
- get() finds a type by its id: the built-in ones, and every one a show, the library or the user's own list has
  registered (register()).

Colour: red, green and blue carry the light's colour. A White channel takes the white in it (the smallest of the
three, taken out of them), so whites use the white LEDs. A light with a Dimmer channel gets its brightness there; one
without has its colour scaled instead. A light without red, green and blue (a white par, a dimmer, a UV bar) shows the
brightest of the colour as a level: on its Dimmer channel if it has one (its other LEDs full), else on its LEDs.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any

# Each job a channel can have, with its name, in the order the Console's Advanced faders list them
ROLE_LABELS = {
    "red": "Red",
    "green": "Green",
    "blue": "Blue",
    "white": "White",
    "amber": "Amber",
    "uv": "UV",
    "lime": "Lime",
    "macro": "Colour macro",
    "strobe": "Strobe",
    "program": "Program",
    "program_speed": "Program speed",
    "dimmer": "Dimmer",
    "dimmer_speed": "Dimmer speed",
    "other": "Other",
}
ROLES = tuple(ROLE_LABELS)
COLOUR = ("red", "green", "blue")
TINTS = ("white", "amber", "uv", "lime")  # LEDs besides red, green and blue
LIGHT_ROLES = COLOUR + TINTS  # the channels that give light (with the Dimmer)
SHARED = frozenset({"dimmer", *LIGHT_ROLES})  # repeated (each part of a pixel bar has its own): all take one value
MAX_NAME = 80  # characters in a type's name
MAX_TEXT = 60  # in its maker, model, mode and short name
MAX_CHANNEL_NAME = 40
MAX_CHANNELS = 512


class LightTypeError(ValueError):
    """A light type that can't be used, and why (readable)."""


def to_byte(value: float) -> int:
    """0-1 -> 0-255, rounded (so a 0-255 value survives the round trip exactly). NaN -> 0."""
    if value >= 1.0:
        return 255
    if not value > 0.0:
        return 0
    return int(value * 255.0 + 0.5)


def _raw(value: float) -> int:
    """A 0-255 value the Console sends as it is (as the encoder always has: cut to a whole number, then held in)."""
    try:
        return max(0, min(255, int(value)))
    except (OverflowError, ValueError):
        return 0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


@dataclass(frozen=True)
class Channel:
    """One DMX channel of a light type."""

    role: str  # its job: one of ROLES
    name: str = ""  # what people see ("" = the job's own name)
    default: int = 0  # what it's sent when nothing sets it, 0-255 (colour and brightness channels: unused)

    @property
    def label(self) -> str:
        return self.name or ROLE_LABELS.get(self.role, self.role)


@dataclass(frozen=True)
class LightType:
    """A kind of light: its channels, in DMX order."""

    id: str
    name: str  # "Betopper LPC018 (7 channels)"
    channels: tuple[Channel, ...]
    short: str = ""  # new lights of this type are named after it: "LPC018 3"
    maker: str = ""
    model: str = ""
    mode: str = ""

    @property
    def channel_count(self) -> int:
        return len(self.channels)

    @cached_property
    def roles(self) -> frozenset[str]:
        return frozenset(ch.role for ch in self.channels)

    @property
    def has_colour(self) -> bool:
        """It has red, green and blue: it shows colours (else it shows a level)."""
        return all(role in self.roles for role in COLOUR)

    @property
    def has_dimmer(self) -> bool:
        return "dimmer" in self.roles

    @property
    def gives_light(self) -> bool:
        return bool(self.roles & {"dimmer", *LIGHT_ROLES})

    @cached_property
    def automatic_roles(self) -> frozenset[str]:
        """The jobs CentraLight fills in from the colour and brightness (they have no "normally")."""
        if self.has_colour:
            return frozenset({"dimmer", *COLOUR, "white"})
        return frozenset({"dimmer", *LIGHT_ROLES})

    def automatic(self, channel: Channel) -> bool:
        return channel.role in self.automatic_roles

    @cached_property
    def keys(self) -> tuple[str, ...]:
        """Each channel's attribute, what the Console holds: its job ("strobe"), "other:<its name>" for an Other
        channel, a number after a repeated one ("strobe_2"). A pixel light's repeated colour and dimmer channels share
        theirs: they all take one value."""
        keys: list[str] = []
        seen: dict[str, int] = {}
        for ch in self.channels:
            if ch.role in SHARED:
                keys.append(ch.role)
                continue
            base = ch.role if ch.role != "other" else "other:" + (_slug(ch.label) or "channel")
            seen[base] = seen.get(base, 0) + 1
            keys.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
        return tuple(keys)

    @cached_property
    def attributes(self) -> tuple[tuple[str, str], ...]:
        """What the Console can set on a light of this type, in DMX order: (attribute, the name people see). Colour
        (red, green, blue) on a colour light, brightness ("dimmer", even without a Dimmer channel: then it scales the
        colour), and each channel CentraLight doesn't fill in itself. White follows the colour, so it isn't one."""
        found: dict[str, str] = {}
        for ch, key in zip(self.channels, self.keys):
            if key in found:
                continue
            if self.automatic(ch):
                if ch.role == "dimmer" or (ch.role in COLOUR and self.has_colour):
                    found[key] = ROLE_LABELS[ch.role]
                continue
            found[key] = attribute_label(key, ch.label)
        found.setdefault("dimmer", ROLE_LABELS["dimmer"])
        return tuple(found.items())

    def default_of(self, key: str) -> float:
        """An attribute's value when nothing sets it: full brightness, no colour, else its channel's "normally"."""
        if key == "dimmer":
            return 255.0
        if key in COLOUR:
            return 0.0
        for ch, k in zip(self.channels, self.keys):
            if k == key:
                return float(ch.default)
        return 0.0

    @cached_property
    def _plan(self) -> tuple:
        """Worked out once: where each kind of channel is (the encoder runs 40 times a second)."""
        automatic = self.automatic_roles
        fixed = tuple((i, _raw(ch.default)) for i, ch in enumerate(self.channels)
                      if ch.role not in automatic and _raw(ch.default))
        parts = (*COLOUR, "white")
        colour = tuple((i, parts.index(ch.role)) for i, ch in enumerate(self.channels) if ch.role in parts)
        dimmers = tuple(i for i, ch in enumerate(self.channels) if ch.role == "dimmer")
        lit = tuple(i for i, ch in enumerate(self.channels) if ch.role in automatic and ch.role != "dimmer")
        slots: dict[str, list[int]] = {}
        for i, key in enumerate(self.keys):
            slots.setdefault(key, []).append(i)
        tints = frozenset(i for i, ch in enumerate(self.channels) if ch.role in TINTS)
        return fixed, colour, dimmers, lit, {k: tuple(v) for k, v in slots.items()}, tints

    def write(self, dmx: bytearray, base: int, rgb: Sequence[float], extras: Mapping[str, float] | None = None) -> None:
        """Write the light's channels into a universe from `base` (0 = channel 1) for a colour (red, green, blue, 0-1,
        Master already in) and what the Console sets besides (attribute -> 0-255: brightness, strobe and so on). A
        light without colour gets the brightest of the three as its level (the Console's brightness is in that colour).
        Colour, brightness and what the Console sets are always written; another channel only if its "normally" isn't
        0, so lights patched over each other mix as they always have."""
        fixed, colour, dimmers, lit, slots, tints = self._plan
        for index, value in fixed:
            dmx[base + index] = value
        r, g, b = rgb
        r = 0.0 if not r > 0.0 else 1.0 if r >= 1.0 else float(r)  # (0-1; NaN -> 0)
        g = 0.0 if not g > 0.0 else 1.0 if g >= 1.0 else float(g)
        b = 0.0 if not b > 0.0 else 1.0 if b >= 1.0 else float(b)
        held = extras.get("dimmer") if extras else None
        dimmer = 255 if held is None else _raw(held)
        scaled = False
        if colour and self.has_colour:
            white = 0.0
            if "white" in self.roles:
                white = min(r, g, b)
                r, g, b = r - white, g - white, b - white
            scaled = not dimmers
            if scaled:
                scale = dimmer / 255.0
                r, g, b, white = r * scale, g * scale, b * scale, white * scale
            parts = (r, g, b, white)
            for index, part in colour:
                dmx[base + index] = to_byte(parts[part])
            for index in dimmers:
                dmx[base + index] = dimmer
        else:
            level = to_byte(max(r, g, b))
            for index in dimmers:
                dmx[base + index] = level
            for index in lit:
                dmx[base + index] = 255 if dimmers else level
        if extras:  # (as it is, on any channel, as it always has: but on a colour light without a Dimmer channel its
            for key, value in extras.items():  # brightness scales the colour, amber, UV and lime instead)
                indexes = slots.get(key)
                if not indexes or (scaled and key == "dimmer"):
                    continue
                raw = _raw(value)
                for index in indexes:
                    dmx[base + index] = round(raw * dimmer / 255) if scaled and index in tints else raw

    def channel_values(self, rgb: Sequence[float], extras: Mapping[str, float] | None = None) -> list[int]:
        """The light's channels (0-255, in DMX order) for a colour and the Console's values (see write)."""
        values = bytearray(len(self.channels))
        self.write(values, 0, rgb, extras)
        return list(values)

    def house_values(self, level: float) -> list[int]:
        """A house light's channels at a level (0-1): white on a light with colour, else the level."""
        return self.channel_values((level, level, level))

    def read_back(self, values: Sequence[int]) -> dict[str, int]:
        """What each attribute was sent as, from the light's channels as sent: the colour with its white put back in,
        and its brightness (on a colour light without a Dimmer channel the colour carries it, so that's full). {} if
        these aren't this type's channels."""
        if len(values) != len(self.channels):
            return {}
        found: dict[str, int] = {}
        for value, key in zip(values, self.keys):
            found.setdefault(key, int(value))
        if self.has_colour:
            white = found.pop("white", 0)
            for role in COLOUR:
                found[role] = min(255, found.get(role, 0) + white)
            found.setdefault("dimmer", 255)
            return found
        lit = [found[role] for role in LIGHT_ROLES if role in found]
        level = found.get("dimmer", 0) if self.has_dimmer else max(lit, default=0)
        for role in LIGHT_ROLES:
            found.pop(role, None)
        found["dimmer"] = level
        return found


def attribute_label(key: str, channel_label: str = "") -> str:
    """The name the Console shows for an attribute: the job's ("Strobe", "Strobe 2"), or an Other channel's own."""
    base, _sep, number = key.rpartition("_") if re.fullmatch(r".+_\d+", key) else (key, "", "")
    if base.startswith("other:"):
        text = channel_label or base[6:].replace("-", " ").capitalize()
    else:
        text = ROLE_LABELS.get(base, channel_label or key)
    return f"{text} {number}" if number else text


# ---------------------------------------------------------------- making and checking types


def type_id(kind: LightType) -> str:
    """The id a type's name and channels give it: the same type has the same id everywhere."""
    data = [kind.name, kind.short, kind.maker, kind.model, kind.mode,
            [[ch.role, ch.name, ch.default] for ch in kind.channels]]
    digest = hashlib.sha1(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"{_slug(kind.short or kind.model or kind.name)[:30].strip('-') or 'light'}-{digest[:10]}"


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def make_type(name: str, channels: Sequence[Channel], *, short: str = "", maker: str = "", model: str = "",
              mode: str = "") -> LightType:
    """A light type with its id worked out (a channel named as its job is stored without the name; colour and
    brightness channels without a "normally")."""
    draft = LightType("", _clean(name, MAX_NAME), tuple(channels))
    tidy = []
    for ch in channels:
        text = _clean(ch.name, MAX_CHANNEL_NAME)
        if text.casefold() == ROLE_LABELS.get(ch.role, "").casefold():
            text = ""
        tidy.append(Channel(ch.role, text, 0 if draft.automatic(ch) else _raw(ch.default)))
    kind = LightType("", draft.name, tuple(tidy), _clean(short, MAX_TEXT), _clean(maker, MAX_TEXT),
                     _clean(model, MAX_TEXT), _clean(mode, MAX_TEXT))
    return LightType(type_id(kind), kind.name, kind.channels, kind.short, kind.maker, kind.model, kind.mode)


def check(kind: LightType) -> None:
    """Raise LightTypeError if CentraLight can't use this type."""
    if not kind.name:
        raise LightTypeError("a light type needs a name")
    if not 1 <= len(kind.channels) <= MAX_CHANNELS:
        raise LightTypeError(f"a light type has 1 to {MAX_CHANNELS} channels, not {len(kind.channels)}")
    for number, ch in enumerate(kind.channels, start=1):
        if ch.role not in ROLES:
            raise LightTypeError(f'channel {number}: "{ch.role}" isn\'t a job a channel can have')
    if not kind.gives_light:
        raise LightTypeError("it has no channel that gives light (a Dimmer, or Red, Green and Blue, or White)")


def type_to_dict(kind: LightType) -> dict[str, Any]:
    data: dict[str, Any] = {"id": kind.id, "name": kind.name}
    for key in ("short", "maker", "model", "mode"):
        if getattr(kind, key):
            data[key] = getattr(kind, key)
    channels = []
    for ch in kind.channels:
        item: dict[str, Any] = {"role": ch.role}
        if ch.name:
            item["name"] = ch.name
        if ch.default and not kind.automatic(ch):
            item["default"] = ch.default
        channels.append(item)
    data["channels"] = channels
    return data


def type_from_dict(data: Any) -> LightType:
    """A light type from its file form. Its id is worked out again from what it is (an id in the file is only used
    to find which lights use it). Raises LightTypeError with a readable reason."""
    if not isinstance(data, dict):
        raise LightTypeError("a light type must be an object")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise LightTypeError("a light type needs a name")
    raw_channels = data.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels:
        raise LightTypeError(f'"{name}" has no channels')
    channels = []
    for number, item in enumerate(raw_channels, start=1):
        if not isinstance(item, dict):
            raise LightTypeError(f'"{name}" channel {number} must be an object')
        role = item.get("role")
        if role not in ROLES:
            raise LightTypeError(f'"{name}" channel {number}: "{role}" isn\'t a job a channel can have '
                                 f'(they are: {", ".join(ROLES)})')
        label = item.get("name", "")
        default = item.get("default", 0)
        if isinstance(default, bool) or not isinstance(default, int) or not 0 <= default <= 255:
            raise LightTypeError(f'"{name}" channel {number}: "default" must be a whole number from 0 to 255')
        channels.append(Channel(role, label if isinstance(label, str) else "", default))
    texts = {key: data.get(key, "") if isinstance(data.get(key, ""), str) else "" for key in ("short", "maker", "model", "mode")}
    kind = make_type(name, channels, **texts)
    check(kind)
    return kind


# ---------------------------------------------------------------- the built-in types and the registry

SLIMPAR9 = LightType(
    "slimpar9", "SlimPAR Q12 (9 channels)",
    (Channel("red"), Channel("green"), Channel("blue"), Channel("amber"), Channel("macro"), Channel("strobe"),
     Channel("program"), Channel("dimmer"), Channel("dimmer_speed")),
    short="SlimPAR", maker="Chauvet DJ", model="SlimPAR Q12 USB", mode="9-channel")
LPC018 = LightType(
    "lpc018", "Betopper LPC018 (7 channels)",
    (Channel("dimmer"), Channel("red"), Channel("green"), Channel("blue"), Channel("strobe"),
     Channel("program", "Macro"), Channel("program_speed", "Macro speed")),
    short="LPC018", maker="Betopper", model="LPC018", mode="7-channel")
DIMMER = LightType("dimmer", "Dimmer (1 channel)", (Channel("dimmer"),), short="Dimmer", mode="1-channel")

BUILT_IN: dict[str, LightType] = {kind.id: kind for kind in (SLIMPAR9, LPC018, DIMMER)}
DEFAULT_COLOUR_TYPE = SLIMPAR9.id  # what Add a light adds when there's nothing to copy

_known: dict[str, LightType] = dict(BUILT_IN)


def get(type_id_: str) -> LightType | None:
    return _known.get(type_id_)


def require(type_id_: str) -> LightType:
    kind = _known.get(type_id_)
    if kind is None:
        raise KeyError(f"no light type {type_id_!r}")
    return kind


def register(kind: LightType) -> LightType:
    """Make a type findable by its id (an id always means the same channels, so the first one registered stays)."""
    return _known.setdefault(kind.id, kind)


def is_built_in(type_id_: str) -> bool:
    return type_id_ in BUILT_IN


def label(type_id_: str) -> str:
    """A type's name for people, or its id if it isn't known."""
    kind = _known.get(type_id_)
    return kind.name if kind is not None else type_id_


def type_name(maker: str, model: str, mode: str, channel_count: int) -> str:
    """A type's name from its maker, model and mode: "Betopper LPC018 (7-channel)", or the number of channels."""
    title = " ".join(part for part in (_clean(maker, MAX_TEXT), _clean(model, MAX_TEXT)) if part) or "Light"
    mode = _clean(mode, MAX_TEXT)
    count = f"{channel_count} channel" + ("" if channel_count == 1 else "s")
    return f"{title} ({mode or count})"[:MAX_NAME]
