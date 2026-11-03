"""Painting on a Paint element's strokes, or on any element's mask, at 640x360.

A Canvas holds one picture while you paint (premultiplied RGBA floats for strokes, 0-1 floats for a mask).
Brush dabs use a soft round kernel (remembered for each size and softness), spaced a quarter of the brush apart
along the pointer's path. When a stroke ends, the Editor saves the picture as a new file (assets.save_asset),
so undo just goes back to the file before.

Tools: brush and eraser (strokes); show and hide brushes (masks); bucket (fills the area of similar colour
around a point); gradient (the brush colour fading to clear, straight or round); light colour (fills the
patch of the picture one light follows).
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from .assets import ASSET_H, ASSET_W

RGB = tuple[float, float, float]


@lru_cache(maxsize=64)
def brush_kernel(radius: float, softness: float) -> np.ndarray:
    """Coverage 0-1 of a round brush ((2r+1) x (2r+1)): solid in the middle, fading over its outer `softness`."""
    r = max(0.5, float(radius))
    size = int(math.ceil(r)) * 2 + 1
    c = size // 2
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    d = np.sqrt((xs - c) ** 2 + (ys - c) ** 2) / r
    inner = max(0.0, 1.0 - min(1.0, max(0.0, softness)))
    if inner >= 1.0:
        kernel = (d <= 1.0).astype(np.float32)
    else:
        f = np.clip((1.0 - d) / (1.0 - inner), 0.0, 1.0)
        kernel = f * f * (3.0 - 2.0 * f)
    kernel.setflags(write=False)
    return kernel


class Canvas:
    """One picture being painted: strokes (mask=False) or a mask (mask=True)."""

    def __init__(self, mask: bool = False, start: np.ndarray | None = None) -> None:
        self.mask = mask
        # what was there, and which pixels have been painted: untouched pixels go back into the file exactly as
        # they were (colours divided back out of see-through pixels could otherwise drift a step each save)
        self._start8 = None if start is None else np.array(start, dtype=np.uint8)
        self._touched = np.zeros((ASSET_H, ASSET_W), dtype=bool)
        if mask:
            self.data = np.ones((ASSET_H, ASSET_W), dtype=np.float32) if start is None else start.astype(np.float32) / 255.0
        elif start is None:
            self.data = np.zeros((ASSET_H, ASSET_W, 4), dtype=np.float32)
        else:  # straight RGBA uint8 -> premultiplied floats
            data = start.astype(np.float32) / 255.0
            data[..., :3] *= data[..., 3:4]
            self.data = data
        self._last: tuple[float, float] | None = None
        self.changed = False

    # ------------------------------------------------------------ brushes

    def dab(self, x: float, y: float, radius: float, softness: float, colour: RGB = (1.0, 1.0, 1.0),
            strength: float = 1.0, mode: str = "paint") -> None:
        """One dab at (x, y) in picture pixels. mode: "paint" / "erase" (strokes), "show" / "hide" (masks)."""
        kernel = brush_kernel(radius, softness)
        size = kernel.shape[0]
        half = size // 2
        cx, cy = int(round(x)), int(round(y))
        x0, y0 = cx - half, cy - half
        x1, y1 = x0 + size, y0 + size
        kx0, ky0 = max(0, -x0), max(0, -y0)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(ASSET_W, x1), min(ASSET_H, y1)
        if x0 >= x1 or y0 >= y1:
            return
        a = kernel[ky0:ky0 + (y1 - y0), kx0:kx0 + (x1 - x0)] * np.float32(min(1.0, max(0.0, strength)))
        self._touched[y0:y1, x0:x1] |= a > 0
        if self.mask:
            region = self.data[y0:y1, x0:x1]
            if mode == "hide":
                region *= 1.0 - a
            else:
                region += (1.0 - region) * a
        else:
            region = self.data[y0:y1, x0:x1]
            if mode == "erase":
                region *= (1.0 - a)[..., None]
            else:
                keep = (1.0 - a)[..., None]
                region *= keep
                region[..., 0] += a * np.float32(colour[0])
                region[..., 1] += a * np.float32(colour[1])
                region[..., 2] += a * np.float32(colour[2])
                region[..., 3] += a
        self.changed = True

    def stroke_to(self, x: float, y: float, radius: float, softness: float, colour: RGB = (1.0, 1.0, 1.0),
                  strength: float = 1.0, mode: str = "paint") -> None:
        """Dabs from the last point to (x, y), a quarter of the brush apart (the first call makes one dab)."""
        last = self._last
        self._last = (x, y)
        if last is None:
            self.dab(x, y, radius, softness, colour, strength, mode)
            return
        distance = math.hypot(x - last[0], y - last[1])
        spacing = max(1.0, radius * 0.25)
        steps = int(distance // spacing)
        for i in range(1, steps + 1):
            f = i * spacing / distance
            self.dab(last[0] + (x - last[0]) * f, last[1] + (y - last[1]) * f, radius, softness, colour, strength, mode)
        if steps == 0:
            self._last = last  # not far enough for another dab yet

    def end_stroke(self) -> None:
        self._last = None

    # ------------------------------------------------------------ fills

    def bucket(self, x: float, y: float, colour: RGB, tolerance: float = 0.12) -> None:
        """Fill the area of similar colour (and see-through-ness) around (x, y) with the colour."""
        import cv2

        if self.mask:
            return
        cx, cy = int(round(x)), int(round(y))
        if not (0 <= cx < ASSET_W and 0 <= cy < ASSET_H):
            return
        alpha = self.data[..., 3]
        straight = self.data[..., :3] / np.maximum(alpha, 1e-4)[..., None]
        seed_alpha = float(alpha[cy, cx])
        if seed_alpha < 0.02:  # clicked somewhere clear: the clear area around it
            similar = alpha < max(0.02, tolerance)
        else:  # clicked some paint: the paint of about the same colour around it
            seed = straight[cy, cx]
            similar = (np.max(np.abs(straight - seed), axis=2) <= tolerance) & (np.abs(alpha - seed_alpha) <= tolerance)
        _count, labels = cv2.connectedComponents(similar.astype(np.uint8), connectivity=4)
        area = labels == labels[cy, cx]
        self.data[area] = (colour[0], colour[1], colour[2], 1.0)
        self._touched |= area
        self.changed = True

    def gradient(self, x0: float, y0: float, x1: float, y1: float, colour: RGB, radial: bool = False) -> None:
        """The colour at (x0, y0) fading to clear at (x1, y1): along the line, or round (x0, y0)."""
        if self.mask:
            return
        ys, xs = np.mgrid[0:ASSET_H, 0:ASSET_W].astype(np.float32)
        dx, dy = x1 - x0, y1 - y0
        length = max(1.0, math.hypot(dx, dy))
        if radial:
            f = np.sqrt((xs - x0) ** 2 + (ys - y0) ** 2) / length
        else:
            f = ((xs - x0) * dx + (ys - y0) * dy) / (length * length)
        a = 1.0 - np.clip(f, 0.0, 1.0)
        self._touched |= a > 0
        keep = (1.0 - a)[..., None]
        self.data *= keep
        for plane in range(3):
            self.data[..., plane] += a * np.float32(colour[plane])
        self.data[..., 3] += a
        self.changed = True

    def fill_area(self, area: np.ndarray, colour: RGB) -> None:
        """Fill an area (a (360, 640) bool map, e.g. one light's patch) with the colour."""
        if self.mask or not area.any():
            return
        self.data[area] = (colour[0], colour[1], colour[2], 1.0)
        self._touched |= area
        self.changed = True

    # ------------------------------------------------------------ out

    def to_asset(self) -> np.ndarray:
        """Ready for assets.save_asset: straight RGBA uint8 (strokes) or grey uint8 (mask)."""
        if self.mask:
            out = np.clip(self.data * 255.0 + 0.5, 0, 255).astype(np.uint8)
        else:
            alpha = self.data[..., 3:4]
            straight = np.where(alpha > 1e-6, self.data[..., :3] / np.maximum(alpha, 1e-6), 0.0)
            out = np.clip(np.concatenate([straight, alpha], axis=2) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if self._start8 is not None and self._start8.shape == out.shape:
            out[~self._touched] = self._start8[~self._touched]
        return out

    def premultiplied8(self) -> np.ndarray:
        """Premultiplied RGBA uint8, for showing while painting (strokes only)."""
        return np.clip(self.data * 255.0 + 0.5, 0, 255).astype(np.uint8)

    def is_empty(self) -> bool:
        return bool(np.all(self.data >= 0.999)) if self.mask else not bool(np.any(self.data[..., 3] > 1e-4))
