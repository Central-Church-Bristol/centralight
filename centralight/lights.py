"""The lights as points.

Each colour light is a dot on the 16:9 map, at (x, y), both 0-1. Its colour is worked out at
that dot, element by element (light_elements.py): Paint exactly at the dot, and Image, Video
and NDI over a square patch of the picture around it, whose size is the element's Area.

Layers worked out per light are premultiplied RGBA as four rows of one value per light,
shape (4, lights): the same layout as a picture's planes, so the blend formulas in
compositor.py work on them unchanged.

Pictures are only for people now (the Dashboard's live view, the Editor's viewports): the
sizes they're drawn at are here, with the Identify colours.
"""

from __future__ import annotations

import random
from collections.abc import Iterable

import numpy as np

from .model import Fixture

CANVAS_W = 160  # the Dashboard's live picture
CANVAS_H = 90
EDITOR_W = 640  # the Editor's viewports
EDITOR_H = 360
ASPECT = 9 / 16  # the screen's height, in screen widths

DEFAULT_AREA = 0.25  # Image, Video and NDI: about the patch every light followed before (16 % of the width across)


def area_half_size(area: float) -> float:
    """Half the width of a light's square patch, in screen widths, for an Area of 0-1. It grows evenly-feeling:
    0 = the colour under the dot, the default (25 %) about 0.06, 50 % a quarter of the screen, and 100 % = 1, which
    covers the whole screen from wherever the light is (so every light shows the whole picture's average)."""
    area = 0.0 if not area > 0.0 else 1.0 if area >= 1.0 else float(area)  # (NaN counts as 0)
    return area * area


# Identify colours: names and RGB 0-1, chosen to look clearly different on RGB LEDs
IDENTIFY_PALETTE: tuple[tuple[str, tuple[float, float, float]], ...] = (
    ("red", (1.0, 0.0, 0.0)),
    ("green", (0.0, 1.0, 0.0)),
    ("blue", (0.0, 0.0, 1.0)),
    ("white", (1.0, 1.0, 1.0)),
    ("yellow", (1.0, 0.8, 0.0)),
    ("cyan", (0.0, 1.0, 1.0)),
    ("magenta", (1.0, 0.0, 1.0)),
    ("orange", (1.0, 0.35, 0.0)),
    ("purple", (0.5, 0.0, 1.0)),
    ("lime", (0.5, 1.0, 0.0)),
    ("pink", (1.0, 0.25, 0.5)),
    ("sky", (0.0, 0.5, 1.0)),
)


def identify_colours(light_ids: Iterable[str], seed: int) -> dict[str, tuple[str, tuple[float, float, float]]]:
    """light id -> (colour name, rgb). The same seed always gives the same colours."""
    order = list(range(len(IDENTIFY_PALETTE)))
    random.Random(seed).shuffle(order)
    return {light_id: IDENTIFY_PALETTE[order[i % len(order)]] for i, light_id in enumerate(light_ids)}


class LightSet:
    """The colour lights on the map, in the show's order: their ids and dots."""

    def __init__(self, fixtures: Iterable[Fixture]) -> None:
        lights = [fx for fx in fixtures if fx.is_colour and fx.map is not None]
        self.ids: tuple[str, ...] = tuple(fx.id for fx in lights)
        self.x = np.array([fx.map.x for fx in lights], dtype=np.float64)  # 0-1 across
        self.y = np.array([fx.map.y for fx in lights], dtype=np.float64)  # 0-1 down
        self.indices = np.arange(len(lights), dtype=np.int32)  # (each light its own patch, for the Random transition)
        self.key = tuple((fx.id, fx.map.x, fx.map.y) for fx in lights)

    @property
    def count(self) -> int:
        return len(self.ids)

    def empty(self) -> np.ndarray:
        """A transparent layer: nothing at any light."""
        return np.zeros((4, len(self.ids)), dtype=np.float32)

    def colours(self, layer: np.ndarray) -> dict[str, tuple[float, float, float]]:
        """A per-light layer -> each light's colour, 0-1 (premultiplied colour is already "over black")."""
        values = np.clip(np.nan_to_num(layer[:3], nan=0.0), 0.0, 1.0)
        return {light_id: (float(values[0, i]), float(values[1, i]), float(values[2, i])) for i, light_id in enumerate(self.ids)}

    def nearest_map(self, width: int, height: int) -> np.ndarray:
        """(height, width): which light each pixel of a picture is nearest to (-1 with no lights). Only for showing a
        Random transition in a picture light by light, like the lights do it."""
        if not self.ids:
            return np.full((height, width), -1, dtype=np.int16)
        xs = (np.arange(width, dtype=np.float32) + 0.5) / width
        ys = (np.arange(height, dtype=np.float32) + 0.5) / height * ASPECT
        best = np.full((height, width), np.inf, dtype=np.float32)
        nearest = np.zeros((height, width), dtype=np.int16)
        for i, (lx, ly) in enumerate(zip(self.x, self.y)):
            distance = (xs[None, :] - np.float32(lx)) ** 2 + (ys[:, None] - np.float32(ly * ASPECT)) ** 2
            closer = distance < best
            best[closer] = distance[closer]
            nearest[closer] = i
        return nearest


def lights_for(fixtures: Iterable[Fixture], previous: LightSet | None = None) -> LightSet:
    """Reuse the previous set if no light moved (or was added or removed), otherwise make a new one."""
    fixtures = list(fixtures)
    key = tuple((fx.id, fx.map.x, fx.map.y) for fx in fixtures if fx.is_colour and fx.map is not None)
    if previous is not None and previous.key == key:
        return previous
    return LightSet(fixtures)
