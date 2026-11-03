"""Combining pictures.

Every picture is premultiplied RGBA float32 stored as four planes, shape (4, h, w):
red, green, blue and alpha, with the colour already multiplied by its alpha (so "over
black" is just the first three planes). Planes make every step a plain array sum
(numpy is about 7× faster that way than with pixels stored as (h, w, 4)). The lights' own
layers are the same with one value per light, shape (4, lights), so everything here works
on them unchanged.

Blend modes (how a track mixes with the tracks below it):
- normal:   above + below·(1 − a_above)
- add:      colours add up (brighter), clipped at 1
- multiply: above·below + above·(1 − a_below) + below·(1 − a_above)   (darker)
- screen:   above + below − above·below                                 (lighter)
"""

from __future__ import annotations

import math

import numpy as np

BLENDS = ("normal", "add", "multiply", "screen")


def transparent(height: int, width: int) -> np.ndarray:
    return np.zeros((4, height, width), dtype=np.float32)


def solid_layer(height: int, width: int, rgb, alpha: float = 1.0) -> np.ndarray:
    layer = np.empty((4, height, width), dtype=np.float32)
    for plane, value in enumerate((rgb[0] * alpha, rgb[1] * alpha, rgb[2] * alpha, alpha)):
        layer[plane].fill(value)
    return layer


def over(below: np.ndarray | None, above: np.ndarray) -> np.ndarray:
    """Put 'above' on top of 'below' (normal blend)."""
    if below is None:
        return above
    return above + below * (1.0 - above[3])


def blend(below: np.ndarray | None, above: np.ndarray, mode: str = "normal") -> np.ndarray:
    if below is None or mode not in BLENDS or mode == "normal":
        return over(below, above)
    sa = above[3]
    da = below[3]
    out = np.empty_like(below)
    if mode == "add":
        np.minimum(above[:3] + below[:3], 1.0, out=out[:3])
    elif mode == "multiply":
        out[:3] = above[:3] * below[:3] + above[:3] * (1.0 - da) + below[:3] * (1.0 - sa)
    else:  # screen
        out[:3] = above[:3] + below[:3] - above[:3] * below[:3]
    out[3] = sa + da - sa * da
    return out


def scale_alpha(layer: np.ndarray, amount: float | np.ndarray) -> np.ndarray:
    """Make a layer more see-through: a number for all of it, or an (h, w) map (a mask)."""
    if isinstance(amount, np.ndarray):
        return layer * amount
    return layer * float(amount)


def is_identity(x: float, y: float, scale: float, rotation: float) -> bool:
    return abs(x - 0.5) < 1e-6 and abs(y - 0.5) < 1e-6 and abs(scale - 1.0) < 1e-6 and abs(rotation % 360.0) < 1e-6


def warp(layer: np.ndarray, x: float, y: float, scale: float, rotation: float) -> np.ndarray:
    """Move the layer's centre to (x, y) (0-1 of the width / height), scale it, and turn it
    clockwise by rotation degrees. Whatever moves in from outside is transparent."""
    import cv2

    if is_identity(x, y, scale, rotation):
        return layer
    height, width = layer.shape[1:]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), -rotation, max(scale, 1e-4))  # cv2 turns anticlockwise
    matrix[0, 2] += (x - 0.5) * width
    matrix[1, 2] += (y - 0.5) * height
    out = np.empty_like(layer)
    for plane in range(4):
        cv2.warpAffine(layer[plane], matrix, (width, height), dst=out[plane], flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return out


def lerp(a: np.ndarray, b: np.ndarray, amount: float | np.ndarray) -> np.ndarray:
    """a when amount is 0, b when it's 1 (amount can be a per-pixel (h, w) map)."""
    return a + (b - a) * amount


def ease(t: float) -> float:
    """Smooth start and end: t²(3 - 2t)."""
    t = 0.0 if t <= 0.0 else 1.0 if t >= 1.0 else t
    return t * t * (3.0 - 2.0 * t)


def ease_array(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def from_image(image: np.ndarray) -> np.ndarray:
    """An RGB or RGBA uint8 picture (h, w, 3 or 4) -> a premultiplied planar layer."""
    planes = np.ascontiguousarray(image.transpose(2, 0, 1)).astype(np.float32) * (1.0 / 255.0)
    if planes.shape[0] == 4:
        planes[:3] *= planes[3]
        return planes
    layer = np.empty((4,) + planes.shape[1:], dtype=np.float32)
    layer[:3] = planes
    layer[3].fill(1.0)
    return layer


def to_rgb8(canvas: np.ndarray) -> np.ndarray:
    """A planar canvas -> RGB uint8 (h, w, 3) over black, for previews."""
    rgb = (np.clip(canvas[:3], 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return np.ascontiguousarray(rgb.transpose(1, 2, 0))


def to_rgba8(canvas: np.ndarray) -> np.ndarray:
    """A planar canvas -> premultiplied RGBA uint8 (h, w, 4), for showing over a checkerboard."""
    rgba = (np.clip(canvas, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return np.ascontiguousarray(rgba.transpose(1, 2, 0))


def direction_ramp(width: int, height: int, angle_degrees: float) -> np.ndarray:
    """(h, w) from 0 to 1 across the picture in a direction (0° = left to right, 90° = top to bottom)."""
    angle = math.radians(angle_degrees)
    dx, dy = math.cos(angle), math.sin(angle)
    xs = np.arange(width, dtype=np.float32) + 0.5 - width / 2.0
    ys = np.arange(height, dtype=np.float32) + 0.5 - height / 2.0
    extent = abs(width / 2.0 * dx) + abs(height / 2.0 * dy)
    projection = xs[None, :] * np.float32(dx) + ys[:, None] * np.float32(dy)
    return np.clip(0.5 + projection / np.float32(2.0 * max(extent, 1e-6)), 0.0, 1.0)


def direction_values(u: np.ndarray, v: np.ndarray, angle_degrees: float) -> np.ndarray:
    """direction_ramp read at points (0-1 across and down), e.g. at the lights' dots."""
    angle = math.radians(angle_degrees)
    dx, dy = math.cos(angle), math.sin(angle)
    extent = abs(8.0 * dx) + abs(4.5 * dy)  # (on a 16 x 9 screen: the same at any size)
    projection = (np.asarray(u) - 0.5) * (16.0 * dx) + (np.asarray(v) - 0.5) * (9.0 * dy)
    return np.clip(0.5 + projection / (2.0 * max(extent, 1e-6)), 0.0, 1.0)


def radial_values(u: np.ndarray, v: np.ndarray, cx: float, cy: float, radius_widths: float) -> np.ndarray:
    """radial_ramp read at points (0-1 across and down)."""
    distance = np.sqrt((np.asarray(u) - cx) ** 2 + ((np.asarray(v) - cy) * (9.0 / 16.0)) ** 2)
    return np.clip(distance / max(radius_widths, 1e-6), 0.0, 1.0)


def radial_ramp(width: int, height: int, cx: float, cy: float, radius_widths: float) -> np.ndarray:
    """(h, w): 0 at (cx, cy), 1 at the radius (in screen widths) and beyond."""
    xs = np.arange(width, dtype=np.float32) + np.float32(0.5 - cx * width)
    ys = np.arange(height, dtype=np.float32) + np.float32(0.5 - cy * height)
    distance = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2)
    return np.clip(distance / np.float32(max(radius_widths * width, 1e-6)), 0.0, 1.0)
