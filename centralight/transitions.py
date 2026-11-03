"""Transitions: how one picture turns into another.

Each kind gives a "mix map": for every pixel, 0 = still the old picture, 1 = the new one.
The same maps are used when an input is sent to Base, when an overlay turns on or off,
and (from M5) for element transitions. Time is eased with t²(3 − 2t).

- cut:    switches at once
- fade:   the whole picture mixes evenly
- dip:    the old picture fades to black, then the new one fades up
- wipe:   a soft edge sweeps across (direction in degrees, 0 = left to right)
- iris:   a soft circle opens from the middle
- random: each light changes at its own moment, in a fixed shuffled order for the input
"""

from __future__ import annotations

import math
import random as _random
import zlib

import numpy as np

from .compositor import direction_ramp, ease, ease_array, lerp, radial_ramp

KINDS = ("cut", "fade", "dip", "wipe", "iris", "random")
RANDOM_SPREAD = 0.6  # how much of the transition the lights' start times are spread over
_IRIS_RADIUS = math.hypot(0.5, 0.5 * 9 / 16)  # middle to corner, in screen widths


def sweep(t: float, ramp: np.ndarray, softness: float) -> np.ndarray:
    """A soft edge moving along a 0-1 ramp: p(x) = clip((t(1+s) − u(x)) / s)."""
    s = max(softness, 1e-3)
    return np.clip((ease(t) * (1.0 + s) - ramp) / s, 0.0, 1.0)


def random_ranks(count: int, seed: int) -> np.ndarray:
    """Each light's place in the shuffled order, 0 (first) to just under 1."""
    order = list(range(count))
    _random.Random(seed).shuffle(order)
    ranks = np.empty(count, dtype=np.float32)
    for place, index in enumerate(order):
        ranks[index] = place / max(1, count)
    return ranks


def seed_for(text: str) -> int:
    return zlib.crc32(text.encode("utf-8"))


def mix_map(
    kind: str,
    t: float,
    width: int,
    height: int,
    *,
    direction: float = 0.0,
    softness: float = 0.25,
    owner: np.ndarray | None = None,
    seed: int = 0,
    cache: dict | None = None,
) -> float | np.ndarray:
    """How far each pixel has changed at time t (0-1): a number, or an (h, w) map."""
    t = 0.0 if t <= 0.0 else 1.0 if t >= 1.0 else t
    if kind == "cut":
        return 1.0 if t > 0.0 else 0.0
    if kind in ("fade", "dip") or kind not in KINDS:
        return ease(t)
    if kind == "wipe":
        key = ("wipe-ramp", width, height, round(direction, 3))
        ramp = cache.get(key) if cache is not None else None
        if ramp is None:
            ramp = direction_ramp(width, height, direction)
            if cache is not None:
                cache[key] = ramp
        return sweep(t, ramp, softness)
    if kind == "iris":
        key = ("iris-ramp", width, height)
        ramp = cache.get(key) if cache is not None else None
        if ramp is None:
            ramp = radial_ramp(width, height, 0.5, 0.5, _IRIS_RADIUS)
            if cache is not None:
                cache[key] = ramp
        return sweep(t, ramp, softness)
    # random: every light's patch starts at its own moment; pixels no light owns just fade
    if owner is None:
        return ease(t)
    count = int(owner.max()) + 1 if owner.size else 0
    if count <= 0:
        return ease(t)
    ranks = random_ranks(count, seed)
    local = ease_array((t - ranks * RANDOM_SPREAD) / (1.0 - RANDOM_SPREAD))
    values = np.append(local, ease(t)).astype(np.float32)  # index -1 -> the last entry
    return values[owner]


def mix(
    kind: str,
    old: np.ndarray,
    new: np.ndarray,
    t: float,
    *,
    direction: float = 0.0,
    softness: float = 0.25,
    owner: np.ndarray | None = None,
    seed: int = 0,
    cache: dict | None = None,
) -> np.ndarray:
    """The picture part-way (t from 0 to 1) from old to new."""
    if t >= 1.0:
        return new
    if kind == "dip":
        if t < 0.5:
            return old * (1.0 - ease(2.0 * t))
        return new * ease(2.0 * t - 1.0)
    height, width = new.shape[1:]
    amount = mix_map(kind, t, width, height, direction=direction, softness=softness, owner=owner, seed=seed, cache=cache)
    return lerp(old, new, amount)  # a number, or an (h, w) map applied to every plane


def reveal(
    kind: str,
    layer: np.ndarray,
    t: float,
    *,
    direction: float = 0.0,
    softness: float = 0.25,
    owner: np.ndarray | None = None,
    seed: int = 0,
    cache: dict | None = None,
) -> np.ndarray:
    """An overlay appearing (t 0 -> 1) with a transition. Dip appears as a fade."""
    if t >= 1.0:
        return layer
    if kind == "dip":
        kind = "fade"
    height, width = layer.shape[1:]
    amount = mix_map(kind, t, width, height, direction=direction, softness=softness, owner=owner, seed=seed, cache=cache)
    return layer * amount
