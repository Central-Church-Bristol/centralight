"""Which part of the picture each light follows.

The picture (the canvas) is 16:9. Each colour light sits on it at (x, y), both 0-1,
with a radius r measured in screen widths. A pixel belongs to the nearest light whose
circle it's inside (nearest in radii, so a bigger light claims more): that's the owner
map, and it means two lights never share a pixel. Inside its patch, pixels near the
middle count most: weight = 1 - 0.75·d², with d = distance / radius. Each light's
weights add up to 1, so its colour is the weighted average of its patch:

    lights = W @ pixels

A light whose patch is empty (tiny, or off the edge) takes the colour under its centre.
Lights on exactly the same spot share one patch.

This file also has the Identify colours: 12 colours that look different on LED lights.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping

import numpy as np

from .model import Fixture

CANVAS_W = 160
CANVAS_H = 90
EDITOR_W = 640
EDITOR_H = 360

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


class Sampler:
    def __init__(self, fixtures: Iterable[Fixture], width: int = CANVAS_W, height: int = CANVAS_H) -> None:
        self.width = width
        self.height = height
        lights = [fx for fx in fixtures if fx.is_colour and fx.map is not None]
        self.ids: tuple[str, ...] = tuple(fx.id for fx in lights)
        self.key = tuple((fx.id, fx.map.x, fx.map.y, fx.map.r) for fx in lights)
        self._keep_cache: dict[frozenset[str], np.ndarray] = {}
        self.owner, self.weights = self._build(lights)

    # ------------------------------------------------------------ building

    def _build(self, lights: list[Fixture]) -> tuple[np.ndarray, np.ndarray]:
        w, h = self.width, self.height
        count = len(lights)
        owner = np.full((h, w), -1, dtype=np.int16)
        # float64, so a flat colour comes out exactly (float32 gives 0.99999994 for white)
        weights = np.zeros((count, h * w), dtype=np.float64)
        if count == 0:
            return owner, weights

        # Lights on exactly the same spot form one group that shares a patch
        groups: list[list[int]] = []
        spot_index: dict[tuple[float, float], int] = {}
        for i, fx in enumerate(lights):
            spot = (round(fx.map.x, 6), round(fx.map.y, 6))
            if spot not in spot_index:
                spot_index[spot] = len(groups)
                groups.append([])
            groups[spot_index[spot]].append(i)

        xs = (np.arange(w, dtype=np.float32) + 0.5) / w  # pixel centres, in screen widths
        ys = (np.arange(h, dtype=np.float32) + 0.5) / h  # pixel centres, 0-1 of the height
        aspect = h / w  # converts heights into widths
        distances = np.empty((len(groups), h, w), dtype=np.float32)
        for g, members in enumerate(groups):
            fx = lights[members[0]]
            radius = max(max(lights[i].map.r for i in members), 1e-6)
            dx = xs[None, :] - fx.map.x
            dy = (ys[:, None] - fx.map.y) * aspect
            distances[g] = np.sqrt(dx * dx + dy * dy) / radius

        nearest = np.argmin(distances, axis=0)
        nearest_d = np.take_along_axis(distances, nearest[None], axis=0)[0]
        inside = nearest_d <= 1.0
        group_owner = np.where(inside, nearest, -1)

        for g, members in enumerate(groups):
            mask = group_owner == g
            owner[mask] = members[0]
            row = np.where(mask, 1.0 - 0.75 * distances[g].astype(np.float64) ** 2, 0.0).ravel()
            total = float(row.sum())
            if total <= 0.0:
                row = self._bilinear(lights[members[0]].map.x, lights[members[0]].map.y)
            else:
                row /= total
            for i in members:
                weights[i] = row
        return owner, weights

    def _bilinear(self, x: float, y: float) -> np.ndarray:
        """Weights picking the colour under one point (for a light with no patch)."""
        w, h = self.width, self.height
        px = min(max(x * w - 0.5, 0.0), w - 1.0)
        py = min(max(y * h - 0.5, 0.0), h - 1.0)
        x0, y0 = int(np.floor(px)), int(np.floor(py))
        x1, y1 = min(x0 + 1, w - 1), min(y0 + 1, h - 1)
        fx, fy = px - x0, py - y0
        row = np.zeros(h * w, dtype=np.float64)
        for xx, yy, weight in ((x0, y0, (1 - fx) * (1 - fy)), (x1, y0, fx * (1 - fy)), (x0, y1, (1 - fx) * fy), (x1, y1, fx * fy)):
            row[yy * w + xx] += weight
        return row

    # ------------------------------------------------------------ using it

    def sample(self, canvas: np.ndarray) -> np.ndarray:
        """A planar canvas (4, h, w) of floats 0-1 -> (lights, 3) colours. Alpha is ignored
        (premultiplied colour is already 'over black')."""
        if not self.ids:
            return np.zeros((0, 3), dtype=np.float32)
        planes = canvas[:3].reshape(3, -1).astype(np.float64)
        # rounded far below one DMX step, so a flat colour comes out exactly (not 0.9999999999999998)
        return np.round((planes @ self.weights.T).T, 9)

    def colours(self, canvas: np.ndarray) -> dict[str, tuple[float, float, float]]:
        values = np.clip(self.sample(canvas), 0.0, 1.0)
        return {light_id: (float(r), float(g), float(b)) for light_id, (r, g, b) in zip(self.ids, values)}

    def keep(self, excluded: Iterable[str]) -> np.ndarray:
        """(h, w) mask: 1 everywhere except the patches of the excluded lights (0 there).
        Cached, and rebuilt with the sampler, so exclusions follow lights when they move."""
        key = frozenset(excluded)
        cached = self._keep_cache.get(key)
        if cached is None:
            cached = np.ones((self.height, self.width), dtype=np.float32)
            for i, light_id in enumerate(self.ids):
                if light_id in key:
                    cached[self.owner == i] = 0.0
            self._keep_cache[key] = cached
        return cached

    def owner_ids(self) -> Mapping[int, str]:
        return dict(enumerate(self.ids))


def sampler_for(fixtures: Iterable[Fixture], previous: Sampler | None, width: int = CANVAS_W, height: int = CANVAS_H) -> Sampler:
    """Reuse the previous sampler if no light moved, otherwise build a new one."""
    fixtures = list(fixtures)
    key = tuple((fx.id, fx.map.x, fx.map.y, fx.map.r) for fx in fixtures if fx.is_colour and fx.map is not None)
    if previous is not None and previous.key == key and previous.width == width and previous.height == height:
        return previous
    return Sampler(fixtures, width, height)
