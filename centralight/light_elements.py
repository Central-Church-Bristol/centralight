"""Each element's colour at each light: the lights' own maths, used by the engine and for
the colours shown in the Editor and on the Dashboard's tiles.

- Colour and Paint are worked out exactly at each light's dot: a Colour element's fill (a colour
  or a gradient; an older Paint element's background is the same), Paint's brush strokes (read
  at the dot) and its shapes (a distance field, so a shape's Softness decides whether a light
  fades or switches as an edge passes).
- Image, Video and NDI average a square patch of the element's own picture around each dot,
  as big as the element's Area (lights.area_half_size). A summed-area table of the picture
  (cv2.integral) makes that a few lookups per light, whatever the size: 0 % is the colour
  under the dot, 100 % the whole picture's average. Parts of a patch outside the screen are
  left out; parts on the screen that the picture doesn't cover count as transparent.
  Then Colour boost (more saturation, the same hue) and, for Video and NDI, Smoothing (each
  light glides to its new colour, with separate times for getting brighter and darker).
- Then the element's opacity, and its mask: where it starts (shown, or hidden everywhere),
  the painted mask read at each dot, the mask's shapes in order (each shows or hides, by its
  softness at the dot), and the lights it leaves alone (0 there) on top.

The element's transform (move, scale, rotate) is undone at the dots: each dot is looked up
where it falls in the element's own, unmoved picture (outside it, the element is
transparent). Layers are (4, lights), premultiplied (see lights.py).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .compositor import direction_values, is_identity, radial_values
from .elements import (
    _LRU,
    RenderContext,
    _fit_of,
    _is_map,
    _painted,
    element_is_dynamic,
    fill_kind,
    load_image,
    mask_hides,
    mask_shapes,
    media_path,
    ndi_source_name,
    param,
)
from .lights import ASPECT, DEFAULT_AREA, area_half_size

PICTURE_KINDS = ("image", "video", "ndi")  # averaged over their Area; with Colour boost
SMOOTHED_KINDS = ("video", "ndi")  # moving pictures: they can be smoothed over time
TABLE_MAX = (320, 180)  # pictures are shrunk to at most this before their summed-area table is made
BOOST_RANGE = 3.0  # Colour boost 100 % multiplies a light's saturation by up to 1 + this
SNAP_CHANGE = 0.25  # a light whose target moved this much (0-1, any channel) in one step has changed a lot...
SNAP_SHARE = 0.5  # ...and when more than this share of an element's lights do at once, it's a cut: no gliding
SMOOTH_RESTART = 1.0  # seconds without being worked out: smoothing starts afresh (no glide from old colours)


# ---------------------------------------------------------------- where the dots fall in the element's own picture


def _transform(element: Mapping[str, Any], t: float) -> tuple[float, float, float, float]:
    return (param(element, "transform.x", t, 0.5), param(element, "transform.y", t, 0.5),
            max(1e-4, param(element, "transform.scale", t, 1.0)), param(element, "transform.rotation", t, 0.0))


def unmove(x: np.ndarray, y: np.ndarray, transform: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Screen points (0-1 across and down) -> where they fall in the element's own picture before its transform
    (the inverse of compositor.warp, which turns about the middle, scales, then moves the middle to (x, y))."""
    tx, ty, scale, rotation = transform
    if is_identity(tx, ty, scale, rotation):
        return x, y
    angle = math.radians(rotation)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    dx = (x - tx)  # from the moved middle, in screen widths
    dy = (y - ty) * ASPECT
    u = (dx * cos_a + dy * sin_a) / scale  # turned back and unscaled
    v = (dy * cos_a - dx * sin_a) / scale
    return u + 0.5, v / ASPECT + 0.5


def _inside(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return ((u >= 0.0) & (u <= 1.0) & (v >= 0.0) & (v <= 1.0)).astype(np.float32)


def bilinear(image: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """A picture (h, w, channels) read at points (0-1 across and down) between its pixels. -> (channels, n)."""
    h, w = image.shape[:2]
    px = np.clip(u * w - 0.5, 0.0, w - 1.0)
    py = np.clip(v * h - 0.5, 0.0, h - 1.0)
    x0 = np.minimum(np.floor(px).astype(np.int64), max(0, w - 2))
    y0 = np.minimum(np.floor(py).astype(np.int64), max(0, h - 2))
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (px - x0)[:, None]
    fy = (py - y0)[:, None]
    top = image[y0, x0] * (1 - fx) + image[y0, x1] * fx
    bottom = image[y1, x0] * (1 - fx) + image[y1, x1] * fx
    values = top * (1 - fy) + bottom * fy
    return np.ascontiguousarray(values.reshape(len(u), -1).T)


# ---------------------------------------------------------------- paint


def shape_cover(shape: Mapping[str, Any], t: float, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray | None, tuple]:
    """How much a rectangle or ellipse covers each point (0-1, its opacity included), and its colour. A crisp edge
    (Softness 0) is fully in or out at a dot; Softness fades over that share of the shape's smaller half-size."""
    opacity = min(1.0, max(0.0, param(shape, "opacity", t, 1.0)))
    half_w = abs(param(shape, "w", t, 0.3)) / 2.0  # screen widths
    half_h = abs(param(shape, "h", t, 0.3)) / 2.0 * ASPECT
    colour = param(shape, "colour", t, (1.0, 1.0, 1.0), "colour")
    if opacity <= 0.0 or half_w < 1e-6 or half_h < 1e-6:
        return None, colour
    cx = param(shape, "x", t, 0.5)
    cy = param(shape, "y", t, 0.5) * ASPECT
    angle = math.radians(param(shape, "rotation", t, 0.0))
    softness = min(1.0, max(0.0, param(shape, "softness", t, 0.0)))
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    dx = u - cx
    dy = v * ASPECT - cy
    along = dx * cos_a + dy * sin_a  # in the shape's own (turned) frame
    across = dy * cos_a - dx * sin_a
    if shape.get("type", "rect") == "ellipse":
        distance = (np.sqrt((along / half_w) ** 2 + (across / half_h) ** 2) - 1.0) * min(half_w, half_h)
    else:
        qx = np.abs(along) - half_w
        qy = np.abs(across) - half_h
        distance = np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2) + np.minimum(np.maximum(qx, qy), 0.0)
    feather = softness * min(half_w, half_h)
    if feather > 1e-9:
        cover = np.clip(0.5 - distance / feather, 0.0, 1.0)
    else:
        cover = (distance < 0.0).astype(np.float64)
    return cover * opacity, colour


def light_fill(element: Mapping[str, Any], ctx: RenderContext, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """A Colour element (or an older Paint element's background) at points of its own picture."""
    t = ctx.time
    n = len(u)
    layer = np.zeros((4, n), dtype=np.float64)
    kind = fill_kind(element)
    if kind == "colour":
        r, g, b = param(element, "fill.colour", t, (0.53, 0.84, 1.0), "colour")
        layer[:3] = np.array([[r], [g], [b]])
        layer[3] = 1.0
    elif kind in ("linear", "radial"):
        a = np.array(param(element, "fill.from", t, (1.0, 0.44, 0.35), "colour"), dtype=np.float64)
        b = np.array(param(element, "fill.to", t, (0.0, 0.72, 1.0), "colour"), dtype=np.float64)
        if kind == "linear":
            ramp = direction_values(u, v, param(element, "fill.angle", t, 0.0))
        else:
            ramp = radial_values(u, v, param(element, "fill.x", t, 0.5), param(element, "fill.y", t, 0.5),
                                 param(element, "fill.radius", t, 0.5))
        layer[:3] = a[:, None] + (b - a)[:, None] * ramp[None, :]
        layer[3] = 1.0
    return layer


def light_paint(element: Mapping[str, Any], ctx: RenderContext, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """A Paint element at points of its own picture: an older one's background, then brush strokes, then shapes."""
    t = ctx.time
    layer = light_fill(element, ctx, u, v)
    strokes = element.get("strokes")
    if isinstance(strokes, str) and strokes:
        painted = strokes_picture(media_path(ctx.folder, strokes))
        if painted is not None:
            above = bilinear(painted, u, v)
            layer = above + layer * (1.0 - above[3])
    shapes = element.get("shapes")
    if isinstance(shapes, Sequence) and not isinstance(shapes, str):
        for shape in shapes:
            if not _is_map(shape):
                continue
            cover, (r, g, b) = shape_cover(shape, t, u, v)
            if cover is None:
                continue
            keep = 1.0 - cover
            layer[0] = cover * r + layer[0] * keep
            layer[1] = cover * g + layer[1] * keep
            layer[2] = cover * b + layer[2] * keep
            layer[3] = cover + layer[3] * keep
    return layer


def strokes_picture(path: Path | None) -> np.ndarray | None:
    """Brush strokes (paint/<code>.png) as premultiplied RGBA floats (360, 640, 4), remembered."""
    from .assets import load_strokes

    if path is None:
        return None
    key = _painted_key_at(path, "strokes-points")
    if key is None:
        return None
    cached = _painted.get_fresh(key)
    if cached is not None:
        return cached
    image = load_strokes(path)
    if image is None:
        return None
    data = image.astype(np.float32) * np.float32(1.0 / 255.0)
    data[..., :3] *= data[..., 3:4]
    _painted.put(key, data)
    return data


def mask_picture(path: Path | None) -> np.ndarray | None:
    """A painted mask (masks/<code>.png) as 0-1 floats (360, 640, 1), remembered."""
    from .assets import load_mask

    if path is None:
        return None
    key = _painted_key_at(path, "mask-points")
    if key is None:
        return None
    cached = _painted.get_fresh(key)
    if cached is not None:
        return cached
    grey = load_mask(path)
    if grey is None:
        return None
    data = (grey.astype(np.float32) * np.float32(1.0 / 255.0))[..., None]
    _painted.put(key, data)
    return data


def _painted_key_at(path: Path, what: str):
    try:
        return (what, str(path), path.stat().st_mtime_ns)
    except OSError:
        return None


# ---------------------------------------------------------------- pictures: Area


TABLES_BYTES = 48_000_000  # pictures' tables kept for the lights (the newest used; a picture's is remade if needed)


class AreaTable:
    """A picture ready to be averaged anywhere: its summed-area table. A solid picture (every NDI and video frame,
    most images) is summed as the 8-bit picture it is, which is fast and exact; one with see-through parts is
    premultiplied first, with its alpha summed too."""

    def __init__(self, image: np.ndarray) -> None:
        import cv2

        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        h, w = image.shape[:2]
        limit_w, limit_h = TABLE_MAX
        if w > limit_w or h > limit_h:
            scale = min(limit_w / w, limit_h / h)
            image = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
            h, w = image.shape[:2]
        self.width = w
        self.height = h
        self.solid = image.shape[2] != 4
        self.nbytes = 0  # (set below: what it takes in memory)
        if self.solid:
            self.table = cv2.integral(np.ascontiguousarray(image[..., :3]), sdepth=cv2.CV_32S)  # (h + 1, w + 1, 3)
            self.unit = 1.0 / 255.0
        else:
            rgba = image.astype(np.float32) * np.float32(1.0 / 255.0)
            rgba[..., :3] *= rgba[..., 3:4]  # premultiplied
            self.table = cv2.integral(rgba, sdepth=cv2.CV_64F)  # (h + 1, w + 1, 4)
            self.unit = 1.0
        self.nbytes = int(self.table.nbytes)



class BoxPlan:
    """Boxes in a picture of one size (frame pixels, any fractions, already inside the picture), worked out once:
    which table entries to read for each and how much each counts. Then each new frame of the picture is a handful
    of lookups. The table is read between its entries, which is exact for a picture made of flat-coloured pixels."""

    def __init__(self, x0: np.ndarray, y0: np.ndarray, x1: np.ndarray, y1: np.ndarray, width: int, height: int) -> None:
        xs = np.stack((x1, x0, x1, x0))  # each box's four corners (4, n): the sum is + - - +
        ys = np.stack((y1, y1, y0, y0))
        signs = np.array([1.0, -1.0, -1.0, 1.0])[:, None]
        ix = np.minimum(xs.astype(np.int64), width - 1)  # (they're >= 0, so the int part is the floor)
        iy = np.minimum(ys.astype(np.int64), height - 1)
        fx = xs - ix
        fy = ys - iy
        stride = width + 1
        top_left = iy * stride + ix
        self.index = np.stack((top_left, top_left + 1, top_left + stride, top_left + stride + 1))  # (4, 4, n)
        self.weight = np.stack(((1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy)) * signs[None]
        self.size = (x1 - x0) * (y1 - y0)  # each box's area in pixels

    def sums(self, table: AreaTable) -> np.ndarray:
        """Each box's premultiplied RGBA sum -> (4, n)."""
        flat = table.table.reshape(-1, table.table.shape[2])
        sums = np.einsum("abnc,abn->cn", flat[self.index], self.weight) * table.unit
        if not table.solid:
            return sums
        return np.vstack((sums, self.size[None, :]))  # solid: every pixel counts fully


_tables = _LRU(96, TABLES_BYTES)


def patch_boxes(x: np.ndarray, y: np.ndarray, area: float, minimum: float = 0.0) -> tuple[np.ndarray, ...]:
    """Each light's square patch on the screen for an Area, cut to the screen: (x0, y0, x1, y1) in screen widths
    (the screen is 1 wide and 9/16 high). minimum: the smallest half-size (half a pixel of the picture)."""
    half = max(area_half_size(area), minimum)
    sx = x
    sy = y * ASPECT
    return (np.clip(sx - half, 0.0, 1.0), np.clip(sy - half, 0.0, ASPECT),
            np.clip(sx + half, 0.0, 1.0), np.clip(sy + half, 0.0, ASPECT))


def light_picture(element: Mapping[str, Any], ctx: RenderContext, table: AreaTable) -> np.ndarray:
    """A picture element averaged over its Area around each light (see the module's notes)."""
    t = ctx.time
    lights = ctx.lights
    area = param(element, "area", t, DEFAULT_AREA)
    fit = _fit_of(element)
    transform = _transform(element, t)
    key = ("area-plan", table.width, table.height, fit, transform, area, lights.key)
    cached = ctx.static.get_fresh(key)  # (where the patches fall only changes with these)
    if cached is None:
        cached = _plan(table.width, table.height, fit, transform, area, lights)
        ctx.static.put(key, cached)
    plan, pixels = cached
    return np.clip(plan.sums(table) / pixels[None, :], 0.0, 1.0)


def _plan(w: int, h: int, fit: str, transform: tuple, area: float, lights) -> tuple[BoxPlan, np.ndarray]:
    """Where each light's patch falls in a picture this size: the boxes, and each patch's area in picture pixels."""
    # how the picture sits on the (unmoved) screen: filling it (cropping the overflow) or fitting inside it
    k = min(1.0 / w, ASPECT / h) if fit == "fit" else max(1.0 / w, ASPECT / h)  # screen widths per pixel
    ox = (1.0 - w * k) / 2.0
    oy = (ASPECT - h * k) / 2.0
    scale = transform[2]
    x0, y0, x1, y1 = patch_boxes(lights.x, lights.y, area, minimum=0.5 * k * scale)
    covered = (x1 - x0) * (y1 - y0)  # the patch's area on the screen
    # the patch, seen in the element's own picture: its middle undone through the transform, its size unscaled (a
    # turned element is read through a straight box, which is close enough)
    middle_u, middle_v = unmove((x0 + x1) / 2.0, (y0 + y1) / ASPECT / 2.0, transform)
    half_x = (x1 - x0) / 2.0 / scale
    half_y = (y1 - y0) / 2.0 / scale
    cx = middle_u
    cy = middle_v * ASPECT
    # what of the picture shows (Fill crops the overflow): moved, shrunk or turned, the cropped strips stay hidden
    left, right = max(0.0, -ox / k), min(float(w), (1.0 - ox) / k)
    top, bottom = max(0.0, -oy / k), min(float(h), (ASPECT - oy) / k)
    fx0 = np.clip((cx - half_x - ox) / k, left, right)
    fx1 = np.clip((cx + half_x - ox) / k, left, right)
    fy0 = np.clip((cy - half_y - oy) / k, top, bottom)
    fy1 = np.clip((cy + half_y - oy) / k, top, bottom)
    pixels = covered / (scale * scale * k * k)  # the patch's area in picture pixels (off the picture: transparent)
    return BoxPlan(fx0, fy0, fx1, fy1, w, h), np.maximum(pixels, 1e-12)


def colour_boost(layer: np.ndarray, amount: float) -> np.ndarray:
    """More saturation for each light, keeping its hue and brightness and staying in range (0 = as it is)."""
    if not amount > 0.0:
        return layer
    alpha = layer[3]
    visible = alpha > 1e-9
    rgb = np.zeros((3, layer.shape[1]), dtype=np.float64)
    rgb[:, visible] = layer[:3, visible] / alpha[visible]
    high = rgb.max(axis=0)
    low = rgb.min(axis=0)
    spread = high - low
    coloured = (spread > 1e-9) & visible
    if not coloured.any():
        return layer
    saturation = np.zeros_like(high)
    saturation[coloured] = spread[coloured] / high[coloured]
    boosted = np.minimum(1.0, saturation * (1.0 + BOOST_RANGE * min(1.0, float(amount))))
    factor = np.ones_like(high)
    factor[coloured] = (high[coloured] * boosted[coloured]) / spread[coloured]  # the new spread over the old
    out = layer.copy()
    out[:3, coloured] = (high[coloured] - (high[coloured] - rgb[:, coloured]) * factor[coloured]) * alpha[coloured]
    return out


def smooth(layer: np.ndarray, ctx: RenderContext, brighter: float, darker: float, snap: bool) -> np.ndarray:
    """Each light glides to its new colour (Video and NDI): c += (target - c)·(1 - exp(-dt/τ)), with τ the brighter
    time while the light's brightness rises and the darker time while it falls (the same for all its channels, so its
    hue doesn't wander mid-glide). Snap on cuts: when most of the lights change a lot at once, they jump. It starts at
    the first colours (no fade up from black), and afresh when the input is sent again."""
    memory = ctx.memory
    if memory is None or not ctx.per_light or not ctx.smoothing:
        return layer
    states = memory.setdefault("smoothing", {})
    key = ctx.element_key
    if not (brighter > 0.0 or darker > 0.0):
        states.pop(key, None)
        return layer
    now = ctx.now
    state = states.get(key)
    if state is None or state[1].shape != layer.shape or not 0.0 <= now - state[0] <= SMOOTH_RESTART:
        states[key] = (now, layer.copy(), layer.copy())
        return layer
    then, current, previous = state
    dt = now - then
    if dt <= 0.0:
        if np.array_equal(layer, previous):
            return current  # (worked out twice at the same moment)
        states[key] = (now, layer.copy(), layer.copy())  # no time passing (the Editor paused): a change shows at once
        return layer
    if snap:
        change = np.abs(layer[:3] - previous[:3]).max(axis=0)
        if layer.shape[1] and float((change > SNAP_CHANGE).mean()) > SNAP_SHARE:
            states[key] = (now, layer.copy(), layer.copy())
            return layer
    rising = layer[:3].max(axis=0) > current[:3].max(axis=0)
    tau = np.where(rising, max(0.0, brighter), max(0.0, darker))
    step = np.where(tau > 0.0, 1.0 - np.exp(-dt / np.maximum(tau, 1e-9)), 1.0)
    current = current + (layer - current) * step[None, :]
    states[key] = (now, current, layer.copy())
    return current


def _frame_table(element: Mapping[str, Any], ctx: RenderContext) -> AreaTable | None:
    """The summed-area table of the element's picture now (remembered per picture)."""
    kind = element.get("kind")
    if kind == "image":
        path = media_path(ctx.folder, element.get("file"))
        if path is None:
            return None
        try:
            key = ("image", str(path), path.stat().st_mtime_ns)
        except OSError:
            return None
        table = _tables.get_fresh(key)
        if table is None:
            image = load_image(path)
            if image is None:
                return None
            table = AreaTable(image)
            _tables.put(key, table)
        return table
    if kind == "video":
        from .video_source import videos

        path = media_path(ctx.folder, element.get("file"))
        if path is None or not path.is_file():
            return None
        clip = videos.clip(path, threaded=ctx.video_threaded)
        if clip.duration <= 0:
            return None
        speed = max(0.0, float(param(element, "speed", ctx.time, 1.0)))
        frame = clip.frame_at(max(0.0, ctx.time) * speed)
        if frame is None:
            if not clip.error:
                ctx.waiting = True
            return None
        key = ("video", str(path), ctx.video_threaded)
        cached = _tables.get_fresh(key)
        if cached is None or cached[0] is not frame:
            cached = (frame, AreaTable(frame))  # (the newest frame of each video: the frame tells them apart)
            _tables.put(key, cached)
        return cached[1]
    if kind == "ndi":
        name = ndi_source_name(element)
        frame = ctx.sources.frame(name) if ctx.sources is not None else None
        if frame is None:
            ctx.waiting = True
            return None
        key = ("area-ndi", name.lower())
        cached = ctx.cache.get(key)
        if cached is None or cached[0] != frame.seq:
            cached = (frame.seq, AreaTable(frame.image))
            ctx.cache[key] = cached
        return cached[1]
    return None


# ---------------------------------------------------------------- one element, start to finish


def light_mask(element: Mapping[str, Any], ctx: RenderContext) -> np.ndarray | None:
    """The element's mask at each light (1 = show, 0 = hidden), or None for no mask: where it starts (shown, or hidden
    everywhere), the mask painted in the Editor read at each dot (it hides), the mask's shapes in order (each shows or
    hides, by its softness at the dot), and on top, 0 for the lights it leaves alone (M14)."""
    mask = element.get("mask")
    if not _is_map(mask):
        return None
    lights = ctx.lights
    hidden = mask.get("start") == "hidden"
    keep = np.zeros(len(lights.ids), dtype=np.float64) if hidden else None
    painted = mask.get("paint")
    if not hidden and isinstance(painted, str) and painted:  # (strokes only hide: on a hidden mask they change nothing)
        grey = mask_picture(media_path(ctx.folder, painted))
        if grey is not None:
            keep = bilinear(grey, lights.x, lights.y)[0].astype(np.float64)
    shapes = mask_shapes(mask)
    if shapes:
        if keep is None:
            keep = np.ones(len(lights.ids), dtype=np.float64)
        for shape in shapes:
            cover, _colour = shape_cover(shape, ctx.time, lights.x, lights.y)
            if cover is not None:
                keep = keep * (1.0 - cover) if mask_hides(shape) else keep + cover * (1.0 - keep)
    excluded = mask.get("exclude")
    if isinstance(excluded, Sequence) and not isinstance(excluded, str) and excluded:
        left_alone = {str(e) for e in excluded}
        ticks = np.array([0.0 if light_id in left_alone else 1.0 for light_id in lights.ids], dtype=np.float64)
        keep = ticks if keep is None else keep * ticks
    return keep


def light_content(element: Mapping[str, Any], ctx: RenderContext) -> np.ndarray | None:
    """The element at each light before its opacity and mask: (4, lights), or None if it shows nothing."""
    kind = element.get("kind")
    lights = ctx.lights
    if kind in ("paint", "colour"):
        u, v = unmove(lights.x, lights.y, _transform(element, ctx.time))
        drawn = light_paint(element, ctx, u, v) if kind == "paint" else light_fill(element, ctx, u, v)
        return drawn * _inside(u, v)[None, :]
    if kind in PICTURE_KINDS:
        table = _frame_table(element, ctx)
        if table is None:
            return None
        t = ctx.time
        layer = colour_boost(light_picture(element, ctx, table), param(element, "boost", t, 0.0))
        if kind in SMOOTHED_KINDS:
            snap = element.get("smooth", {}).get("snap", True) if _is_map(element.get("smooth")) else True
            layer = smooth(layer, ctx, param(element, "smooth.up", t, 0.0), param(element, "smooth.down", t, 0.0), snap is not False)
        return layer
    return None


def light_element(element: Mapping[str, Any], ctx: RenderContext, use_mask: bool = True) -> np.ndarray | None:
    """The element at each light, ready to blend: its content, then its opacity and mask. Elements that never
    change (no keyframes or expressions, not a live feed or video) are worked out once and remembered."""
    if not element_is_dynamic(element):
        key = ("lights", id(element), ctx.lights.key, ctx.folder, use_mask)
        cached = ctx.static.get_fresh(key)
        if cached is not None and cached[0] is element:
            return cached[1]
        layer = _finish(element, ctx, use_mask)
        if layer is not None:
            ctx.static.put(key, (element, layer))  # holding the element keeps its id from being reused
        return layer
    return _finish(element, ctx, use_mask)


def _finish(element: Mapping[str, Any], ctx: RenderContext, use_mask: bool) -> np.ndarray | None:
    layer = light_content(element, ctx)
    if layer is None:
        return None
    opacity = min(1.0, max(0.0, param(element, "opacity", ctx.time, 1.0)))
    if opacity < 1.0:
        layer = layer * opacity
    if use_mask:
        keep = light_mask(element, ctx)
        if keep is not None:
            layer = layer * keep[None, :]
    return layer.astype(np.float32)
