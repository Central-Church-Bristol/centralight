"""Open Fixture Library lights as light types (M19, 18 Nov).

The Open Fixture Library (open-fixture-library.org, MIT licence) describes hundreds of lights: each mode, and what
each of its channels does. tools/build_light_library.py turns its download (ofl_export_ofl.zip) into the list
CentraLight carries (data/light_library.json.gz) with types_from_fixture() here: CentraLight never goes online for it.

Each mode becomes a light type; a channel's job comes from what its DMX values do:
- Intensity -> Dimmer; a colour's intensity -> Red, Green, Blue, White (warm or cold too), Amber, UV or Lime (cyan,
  magenta, yellow and indigo -> Other);
- ShutterStrobe -> Strobe; a colour preset -> Colour macro; an effect -> Program; an effect's speed -> Program
  speed; anything else (pan, tilt, gobos, fine channels...) -> Other, under its own name.
A channel with several jobs over its range takes the one over most of it ("No function" aside). Its "normally" is
the file's own default value if it has one; a strobe channel's otherwise is where the shutter is open (so the light
shines), anything else's 0. A pixel light's repeated channels each keep their name ("Red 1", "Red 2") and all take
the light's one colour.
Smoke and haze machines, fans and stands are left out, and so is a mode with nothing that gives light.
"""

from __future__ import annotations

import re
from typing import Any

from .light_types import Channel, LightType, LightTypeError, check, make_type

LEFT_OUT = {"Smoke", "Hazer", "Fan", "Stand"}  # categories that aren't lights
COLOURS = {
    "Red": "red", "Green": "green", "Blue": "blue", "White": "white", "Warm White": "white", "Cold White": "white",
    "Amber": "amber", "UV": "uv", "Lime": "lime",
}
EFFECT_SPEED = {"EffectSpeed", "EffectDuration", "Speed"}


def _capabilities(channel: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(channel.get("capability"), dict):
        return [{**channel["capability"], "dmxRange": [0, 255]}]
    caps = channel.get("capabilities")
    return [c for c in caps if isinstance(c, dict)] if isinstance(caps, list) else []


def _span(cap: dict[str, Any]) -> int:
    low, high = (cap.get("dmxRange") or [0, 0])[:2]
    return int(high) - int(low) + 1


def role_of(channel: dict[str, Any]) -> str:
    """A channel's job, from its capabilities (the one over most of its range, "No function" aside)."""
    caps = [c for c in _capabilities(channel) if c.get("type") != "NoFunction"]
    if not caps:
        return "other"
    totals: dict[str, int] = {}
    for cap in caps:
        kind = cap.get("type", "")
        if kind == "ColorIntensity":
            kind = f"ColorIntensity:{cap.get('color', '')}"
        totals[kind] = totals.get(kind, 0) + _span(cap)
    main = max(totals, key=lambda k: (totals[k], -list(totals).index(k)))
    if main == "Intensity":
        return "dimmer"
    if main.startswith("ColorIntensity:"):
        return COLOURS.get(main.partition(":")[2], "other")
    if main == "ShutterStrobe":
        return "strobe"
    if main == "ColorPreset":
        return "macro"
    if main in ("Effect", "SoundSensitivity"):
        return "program"
    if main in EFFECT_SPEED:
        return "program_speed"
    return "other"


def _resolution(channel: dict[str, Any]) -> int:
    """How many bytes the channel's DMX values count in (its fine channels'), 1 for 8-bit."""
    if channel.get("dmxValueResolution") == "8bit":
        return 1
    return 1 + len(channel.get("fineChannelAliases") or [])


def _value(raw: Any, resolution: int) -> int | None:
    """A DMX value in the channel's resolution: a number, or "50%" of the most it can be."""
    top = 256 ** resolution - 1
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return max(0, min(top, int(raw)))
    if isinstance(raw, str) and (m := re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*%\s*", raw)):
        return max(0, min(top, round(float(m.group(1)) / 100 * top)))
    return None


def default_of(channel: dict[str, Any], role: str) -> int:
    """Where the channel sits, in its own resolution: the file's default, else an open shutter, else 0."""
    resolution = _resolution(channel)
    value = _value(channel.get("defaultValue"), resolution)
    if value is not None:
        return value
    if role == "strobe":
        for cap in _capabilities(channel):
            if cap.get("type") == "ShutterStrobe" and cap.get("shutterEffect") == "Open":
                low, high = (int(v) for v in (cap.get("dmxRange") or [0, 0])[:2])
                return 0 if low == 0 else (low + high) // 2  # (the middle of its range: safe from either end)
    return 0


def _byte(value: int, resolution: int, part: int = 0) -> int:
    """One byte of a value counted in `resolution` bytes: part 0 is the coarse channel, 1 the first fine one (0 for a
    fine channel finer than the value is counted in)."""
    if part >= resolution:
        return 0
    return (value >> (8 * (resolution - 1 - part))) & 0xFF


class _Fixture:
    """Finds a mode's channels in a fixture file: plain ones, fine ones, switching ones and pixel ones."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.available: dict[str, dict] = {k: v for k, v in (data.get("availableChannels") or {}).items()
                                           if isinstance(v, dict)}
        self.templates: dict[str, dict] = {k: v for k, v in (data.get("templateChannels") or {}).items()
                                           if isinstance(v, dict)}
        self.fine: dict[str, tuple[str, int]] = {}  # fine alias -> (its channel's key, 1 for the first fine one)
        for table in (self.available, self.templates):
            for key, channel in table.items():
                for index, alias in enumerate(channel.get("fineChannelAliases") or [], start=1):
                    self.fine[alias] = (key, index)
        self.switches: dict[str, str] = {}  # switching alias -> the channel it is when its controller is at default
        for key, channel in self.available.items():
            at = _value(channel.get("defaultValue"), _resolution(channel)) or 0
            for cap in _capabilities(channel):
                low, high = (int(v) for v in (cap.get("dmxRange") or [0, 0])[:2])
                for alias, target in (cap.get("switchChannels") or {}).items():
                    if alias not in self.switches or low <= at <= high:
                        self.switches[alias] = target

    def channel(self, key: str, name: str | None = None) -> Channel:
        """One entry of a mode's channel list as a CentraLight channel."""
        label = name or key
        if key in self.switches and key not in self.available:
            target = self.switches[key]
            return Channel("other", label, 0) if target is None else self.channel(target, label)
        found = self.available.get(key)
        fine = self.fine.get(key) if found is None else None
        if found is None and fine is None:
            fine = self._fine_template_for(key)
        if found is None and fine is not None:  # (a fine channel: the low byte of its coarse one's "normally")
            coarse_key, part = fine
            coarse = self.available.get(coarse_key) or self.templates.get(coarse_key) or self._template_for(coarse_key)
            if coarse is None:
                return Channel("other", label, 0)
            return Channel("other", label, _byte(default_of(coarse, role_of(coarse)), _resolution(coarse), part))
        if found is None:
            found = self._template_for(key)
        if found is None:
            raise LightTypeError(f'a mode uses "{key}", which the file doesn\'t describe')
        role = role_of(found)
        return Channel(role, found.get("name") if isinstance(found.get("name"), str) and not name else label,
                       _byte(default_of(found, role), _resolution(found)))

    @staticmethod
    def _matches(template: str, resolved: str) -> bool:
        return re.fullmatch(re.escape(template).replace(re.escape("$pixelKey"), ".+?"), resolved) is not None

    def _fine_template_for(self, resolved: str) -> tuple[str, int] | None:
        """A pixel's fine channel ("Red 1A fine" from the fine alias "Red $pixelKey fine"): (its template, part)."""
        for alias, found in self.fine.items():
            if "$pixelKey" in alias and self._matches(alias, resolved):
                return found
        return None

    def _template_for(self, resolved: str) -> dict | None:
        """The template a pixel channel's name comes from ("Red 1A" from "Red $pixelKey"): the longest that fits."""
        fitting = [template for template in self.templates if self._matches(template, resolved)]
        return self.templates[max(fitting, key=len)] if fitting else None

    def pixel_keys(self, repeat: Any) -> list[str]:
        if isinstance(repeat, list):
            return [str(key) for key in repeat]
        matrix = self.data.get("matrix") or {}
        if repeat == "eachPixelGroup":
            return list((matrix.get("pixelGroups") or {}).keys())
        keys: list[str] = []
        raw = matrix.get("pixelKeys")
        if isinstance(raw, list):
            for plane in raw:
                for row in plane if isinstance(plane, list) else []:
                    keys += [str(k) for k in (row if isinstance(row, list) else []) if k is not None]
        else:
            count = matrix.get("pixelCount") or [1, 1, 1]
            total = 1
            for size in count:
                total *= max(1, int(size)) if isinstance(size, int) else 1
            keys = [str(n) for n in range(1, total + 1)]
        if repeat == "eachPixelABC":
            keys.sort(key=lambda k: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", k)])
        return keys

    def mode_channels(self, mode: dict[str, Any]) -> list[Channel]:
        channels: list[Channel] = []
        for entry in mode.get("channels") or []:
            if entry is None:
                channels.append(Channel("other", "Not used", 0))
            elif isinstance(entry, str):
                channels.append(self.channel(entry))
            elif isinstance(entry, dict) and entry.get("insert") == "matrixChannels":
                pixels = self.pixel_keys(entry.get("repeatFor"))
                templates = [t for t in entry.get("templateChannels") or [] if isinstance(t, str)]
                pairs = ([(p, t) for p in pixels for t in templates] if entry.get("channelOrder") != "perChannel"
                         else [(p, t) for t in templates for p in pixels])
                for pixel, template in pairs:
                    resolved = template.replace("$pixelKey", pixel)
                    channels.append(self.channel(resolved))
            else:
                raise LightTypeError("a mode has a channel entry CentraLight can't read")
        return channels


def types_from_fixture(data: dict[str, Any], maker: str) -> tuple[list[LightType], list[str]]:
    """Each mode of one library fixture as a light type, and why any mode was left out. A smoke machine or another
    thing that isn't a light gives none."""
    skipped: list[str] = []
    if set(data.get("categories") or []) & LEFT_OUT:
        return [], [f"{maker} {data.get('name')}: not a light ({', '.join(data.get('categories') or [])})"]
    fixture = _Fixture(data)
    model = str(data.get("name") or "").strip()
    found: list[LightType] = []
    for mode in data.get("modes") or []:
        mode_name = str(mode.get("name") or mode.get("shortName") or "").strip()
        try:
            channels = fixture.mode_channels(mode)
            if not channels:
                raise LightTypeError("it has no channels")
            name = f"{maker} {model} ({mode_name or f'{len(channels)} channels'})"
            kind = make_type(name, channels, short=model, maker=maker, model=model, mode=mode_name)
            check(kind)
        except (LightTypeError, TypeError, ValueError) as exc:
            skipped.append(f"{maker} {model} ({mode_name}): {exc}")
            continue
        found.append(kind)
    return found, skipped
