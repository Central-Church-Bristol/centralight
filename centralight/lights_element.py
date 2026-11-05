"""The Lights element (M15): a look (one step) or a chase (several steps) set straight onto chosen lights, as the
Console page saves them ("Save look as input", "Save chase as input"). Lights it doesn't set are see-through, so what's
under it shows. Its timing is a chase's: BPM or seconds a step, crossfade, direction, loop or play once, or a step on
each beat it hears; speed and crossfade can have keys or formulas.

In an input file:

    {"id": "E1", "kind": "lights",
     "steps": [{"slimpar-101": [1, 0.6, 0.2, 1], "slimpar-102": [0, 0, 1, 0.8]}, ...],   (red, green, blue, brightness)
     "timing": "bpm", "bpm": 120, "seconds": 1, "crossfade": 0, "direction": "forward", "loop": true, "on_beat": false}
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import numpy as np

from .console import BEAT_JUMP, BEAT_RISE, BPM_RANGE, DIRECTIONS, Chase, ChaseStep, chase_look, finite, step_index, whole_steps
from .params import is_dynamic

KIND = "lights"
LIGHT_SIZE = 0.035  # each lit light's spot in a picture (the Editor, the Dashboard), in screen widths
_chases: dict[int, tuple[Mapping[str, Any], Chase]] = {}  # element id -> (the element, its chase): read once


def element_chase(element: Mapping[str, Any]) -> Chase:
    """The element's steps and timing as a Chase (its own lights: every light any step sets, in the order they
    first come). Speed and crossfade here are the element's starting values (they can change over time)."""
    cached = _chases.get(id(element))
    if cached is not None and cached[0] is element:
        return cached[1]
    steps = []
    lights: list[str] = []
    raw_steps = element.get("steps")
    for raw in raw_steps if isinstance(raw_steps, (list, tuple)) else ():
        looks = {}
        if isinstance(raw, Mapping):
            for light, look in raw.items():
                if isinstance(look, (list, tuple)) and len(look) == 4 and all(finite(v, -1e10) > -1e10 for v in look):
                    looks[str(light)] = tuple(max(0.0, min(1.0, finite(v, 0.0))) for v in look)
                    if str(light) not in lights:
                        lights.append(str(light))
        steps.append(ChaseStep(MappingProxyType(looks)))
    direction = element.get("direction", "forward")
    chase = Chase(lights=tuple(lights), steps=tuple(steps), bpm=_at_start(element, "bpm", 120.0),
                  seconds=_at_start(element, "seconds", 1.0) if element.get("timing") == "seconds" else None,
                  crossfade=max(0.0, min(1.0, _at_start(element, "crossfade", 0.0))),
                  direction=direction if isinstance(direction, str) and direction in dict(DIRECTIONS) else "forward",
                  loop=element.get("loop", True) is not False, on_beat=element.get("on_beat") is True)
    if len(_chases) > 256:
        _chases.clear()
    _chases[id(element)] = (element, chase)
    return chase


def _at_start(element: Mapping[str, Any], name: str, default: float) -> float:
    """A setting at the element's start: a plain number, or keys or a formula worked out at 0 (Load into Console
    starts from there)."""
    value = element.get(name)
    if not is_dynamic(value):
        return _plain(value, default)
    from .elements import param

    try:
        return finite(param(element, name, 0.0, default), default)
    except Exception:  # (a formula that needs what only a running input has: the usual value)
        return default


def _plain(value: Any, default: float) -> float:
    """A setting's value when it's a plain number (keys or a formula: the default here; param() works it out)."""
    return finite(value, default)


def is_moving(element: Mapping[str, Any]) -> bool:
    """A chase changes over time (a look doesn't)."""
    steps = element.get("steps")
    return isinstance(steps, (list, tuple)) and len(steps) > 1


def step_seconds(element: Mapping[str, Any], t: float) -> float:
    from .elements import param

    if element.get("timing") == "seconds":
        return max(0.05, float(param(element, "seconds", t, 1.0)))
    bpm = float(param(element, "bpm", t, 120.0))
    return 60.0 / max(BPM_RANGE[0], min(BPM_RANGE[1], bpm))


def position(element: Mapping[str, Any], ctx: Any) -> float:
    """How many steps have gone by at the element's time (with the fraction into the current one). A speed with keys
    or a formula is added up as time goes (in the running input's memory), so a change of speed never jumps."""
    t = max(0.0, float(ctx.time))
    if element.get("on_beat") is True:
        return _beats(element, ctx, t)
    if ctx.memory is None:
        return t / step_seconds(element, t)
    key = (ctx.element_key, "lights-position")  # (a plain speed too: edited while it plays, it mustn't jump)
    last = ctx.memory.get(key)
    if last is None or t < last[0] or t - last[0] > 5.0:  # (a jump: a seek, or the first time)
        place = t / step_seconds(element, t)
    else:
        place = last[1] + (t - last[0]) / step_seconds(element, t)
    ctx.memory[key] = (t, place)
    return place


def _beats(element: Mapping[str, Any], ctx: Any, t: float) -> float:
    """Step on each beat (audio.beat): a step for each new beat heard, gliding by how far into the gap it is."""
    beat = float((ctx.audio or {}).get("beat", 0.0) or 0.0)
    if ctx.memory is None:
        return 0.0
    key = (ctx.element_key, "lights-beats")
    beats, level, last_at, gap, last_t = ctx.memory.get(key, (0, 0.0, None, step_seconds(element, t), t))
    if t < last_t - 1e-6:  # (its time went back: a loop, or it came round again: from the first step)
        beats, last_at = 0, None
    if beat >= BEAT_RISE and beat > level + BEAT_JUMP:  # (as the Console's chase: a quick rise is a new beat)
        if last_at is not None and 0.15 < t - last_at < 3.0:
            gap = 0.6 * gap + 0.4 * (t - last_at)
        beats, last_at = beats + 1, t
    ctx.memory[key] = (beats, beat, last_at, gap, t)
    if last_at is None:
        return float(beats)
    return beats + min(0.999, max(0.0, (t - last_at) / max(0.05, gap)))


def looks_at(element: Mapping[str, Any], ctx: Any) -> dict[str, tuple[float, float, float, float]]:
    """Each light's (r, g, b, brightness) now, for the lights the element sets."""
    from .elements import param

    chase = element_chase(element)
    if not chase.steps:
        return {}
    place = position(element, ctx) if len(chase.steps) > 1 else 0.0
    crossfade = float(param(element, "crossfade", ctx.time, chase.crossfade))
    seed = str(element.get("id", ""))
    looks = {}
    for light in chase.lights:
        look = chase_look(chase, light, place, seed, crossfade)
        if look is not None:
            looks[light] = look
    return looks


def current_step(element: Mapping[str, Any], ctx: Any) -> int:
    chase = element_chase(element)
    return step_index(chase, whole_steps(position(element, ctx)), str(element.get("id", ""))) if len(chase.steps) > 1 else 0


def light_layer(element: Mapping[str, Any], ctx: Any) -> np.ndarray:
    """At each light (4, lights), before opacity and mask: its colour times its brightness, solid; see-through where
    the element doesn't set the light."""
    lights = ctx.lights
    layer = lights.empty()
    looks = looks_at(element, ctx)
    for index, light in enumerate(lights.ids):
        look = looks.get(light)
        if look is not None:
            r, g, b, level = look
            layer[:, index] = (r * level, g * level, b * level, 1.0)
    return layer


def picture_layer(element: Mapping[str, Any], ctx: Any) -> np.ndarray:
    """A picture of it (the Editor, the Dashboard): a soft spot of each light's colour where the light is."""
    width, height = ctx.width, ctx.height
    layer = np.zeros((4, height, width), dtype=np.float32)
    lights = ctx.lights
    if lights is None or width <= 0 or height <= 0:
        return layer
    looks = looks_at(element, ctx)
    radius = max(2.0, LIGHT_SIZE * width)
    reach = 1.5 * radius  # (beyond this a spot covers nothing: only its box is worked out)
    for index, light in enumerate(lights.ids):
        look = looks.get(light)
        if look is None:
            continue
        cx, cy = float(lights.x[index]) * width, float(lights.y[index]) * height
        x0, x1 = max(0, int(cx - reach)), min(width, int(cx + reach) + 2)
        y0, y1 = max(0, int(cy - reach)), min(height, int(cy + reach) + 2)
        if x0 >= x1 or y0 >= y1:
            continue
        ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        distance = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        cover = np.clip(1.5 - distance / radius, 0.0, 1.0)  # (solid in the middle, soft at the edge)
        r, g, b, level = look
        box = layer[:, y0:y1, x0:x1]
        for plane, value in enumerate((r * level, g * level, b * level)):
            box[plane] = box[plane] * (1.0 - cover) + value * cover
        box[3] = box[3] * (1.0 - cover) + cover
    return layer


def lights_element(chase: Chase, element_id: str = "E1", name: str = "") -> dict[str, Any]:
    """A Lights element for an input, from the Console's look or chase (Save look / chase as input)."""
    element: dict[str, Any] = {
        "id": element_id,
        "kind": KIND,
        "steps": [{light: [round(float(v), 4) for v in look] for light, look in step.looks.items()} for step in chase.steps],
        "timing": "seconds" if chase.seconds is not None else "bpm",
        "bpm": round(float(chase.bpm), 2),
        "seconds": round(float(chase.seconds if chase.seconds is not None else chase.step_seconds()), 3),
        "crossfade": round(float(chase.crossfade), 3),
        "direction": chase.direction,
        "loop": chase.loop,
        "on_beat": chase.on_beat,
    }
    if name:
        element["name"] = name
    return element
