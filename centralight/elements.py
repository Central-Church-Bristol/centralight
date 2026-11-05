"""Element renderers: each element of an input becomes a premultiplied RGBA picture
(four planes, see compositor.py). Pictures are for people (the Dashboard, the Editor): the
lights' own colours are worked out at their dots by light_elements.py, which a context with
per_light set uses instead.

Kinds:
- "colour": a solid colour, or a straight / round gradient, across the screen (M14)
- "paint": brush painting and shapes (rectangles and ellipses with position, size, rotation,
  softness and colour), see-through until something is painted. (An older Paint element can
  also have a fill underneath, its "Background": a Colour element's settings.)
- "image": a picture from the show's media folder, filling or fitting the screen
- "ndi":   a live NDI feed (default "Media Background Pane"), filling or fitting
- "video": arrives in M5 (draws nothing until then)

Every element also has opacity, a blend mode, a transform (move, scale, rotate) and a
mask: where it starts (shown, or hidden everywhere), a painted mask (it hides), shapes that
show or hide the element inside them (M14), and lights it leaves alone ("exclude": only the
lights see that).
Unknown kinds draw nothing, so a show from a newer CentraLight still plays.

Elements that never change (no keyframes or expressions, not a live feed) are drawn once
and cached. Shapes are only worked out inside their own bounding box.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

import numpy as np

from .compositor import direction_ramp, from_image, over, radial_ramp, scale_alpha, solid_layer, transparent, warp
from .ndi_source import SourceFrame
from .params import evaluate, is_dynamic

DEFAULT_NDI_SOURCE = "Media Background Pane"
MEDIA_CACHE_SIZE = 32
VIDEOS_KEPT = 6  # videos whose newest picture a drawing keeps (each new version of a linked video is a new file)
_MAP_TYPES = (dict, MappingProxyType)
_NUMBER_TYPES = (int, float)


def _is_map(value: Any) -> bool:
    return type(value) in _MAP_TYPES or isinstance(value, Mapping)


class Sources(Protocol):
    def frame(self, name: str) -> SourceFrame | None: ...


class _LRU(OrderedDict):
    """Keeps the most recently used entries, up to a count and (for pictures) a memory budget."""

    def __init__(self, size: int, max_bytes: int | None = None) -> None:
        super().__init__()
        self.size = size
        self.max_bytes = max_bytes
        self.bytes = 0

    @staticmethod
    def _bytes_of(value) -> int:
        item = value[1] if isinstance(value, tuple) and len(value) == 2 else value
        return int(getattr(item, "nbytes", 0))

    def get_fresh(self, key):
        value = self.get(key)
        if value is not None:
            self.move_to_end(key)
        return value

    def put(self, key, value) -> None:
        if key in self:
            self.bytes -= self._bytes_of(self.pop(key))
        self[key] = value
        self.bytes += self._bytes_of(value)
        while len(self) > self.size or (self.max_bytes is not None and self.bytes > self.max_bytes and len(self) > 1):
            _old_key, old = self.popitem(last=False)
            self.bytes -= self._bytes_of(old)


STATIC_CACHE_BYTES = 32_000_000  # drawn static layers kept per render context


@dataclass
class RenderContext:
    width: int
    height: int
    sources: Sources | None = None
    time: float = 0.0  # element time, seconds
    folder: str = ""  # the show folder (for media files)
    lights: Any = None  # the LightSet: where the lights are (lights.py)
    per_light: bool = False  # work out each light's colour (layers (4, lights)) instead of drawing a picture
    cache: dict[Any, Any] = field(default_factory=dict)
    faults: dict[str, str] = field(default_factory=dict)  # "input/element" -> what went wrong
    globals: Mapping[str, Any] = MappingProxyType({})  # global variables' current values
    audio: Mapping[str, float] = MappingProxyType({})  # audio.level and friends (M8)
    static: _LRU = field(default_factory=lambda: _LRU(512, STATIC_CACHE_BYTES))  # drawn static elements
    video_threaded: bool = True  # videos decode ahead in their own thread (the engine); False: exact frames (Editor, tests)
    waiting: bool = False  # an NDI feed or video in this picture hasn't got its first picture yet
    memory: dict | None = None  # the running input's memory (its formulas' cycles, its elements' smoothing)
    element_key: str = ""  # "input/element" being worked out
    now: float = 0.0  # the clock the running input's time comes from (smoothing measures time with it)
    smoothing: bool = True  # Video and NDI smoothing applies (not for a one-off preview, which has no clock)


# ---------------------------------------------------------------- helpers


def param(element: Mapping[str, Any], path: str, t: float, default: Any, kind: str = "number") -> Any:
    current: Any = element
    for part in path.split("."):
        if _is_map(current) and part in current:
            current = current[part]
        else:
            return default
    if kind == "number" and type(current) in _NUMBER_TYPES:
        return float(current)  # the common case: a fixed number
    return evaluate(current, t, default, kind)


_dynamic: _LRU = _LRU(1024)


def element_is_dynamic(element: Mapping[str, Any]) -> bool:
    """True if the element can look different from one moment to the next."""
    cached = _dynamic.get_fresh(id(element))
    if cached is not None and cached[0] is element:
        return cached[1]

    def walk(value: Any) -> bool:
        if _is_map(value):
            return is_dynamic(value) or any(walk(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(walk(v) for v in value)
        return False

    result = element.get("kind") in ("ndi", "video") or walk(element)
    _dynamic.put(id(element), (element, result))  # holding the element keeps its id from being reused
    return result


def fill_resize(image: np.ndarray, width: int, height: int) -> np.ndarray:
    """Scale an image (h, w, channels) to cover width x height, cropping the overflow evenly."""
    import cv2

    src_h, src_w = image.shape[:2]
    scale = max(width / src_w, height / src_h)
    new_w = max(width, round(src_w * scale))
    new_h = max(height, round(src_h * scale))
    if (new_w, new_h) != (src_w, src_h):
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    x = (new_w - width) // 2
    y = (new_h - height) // 2
    return image[y:y + height, x:x + width]


def fit_resize(image: np.ndarray, width: int, height: int) -> tuple[np.ndarray, int, int]:
    """Scale an image to fit inside width x height. Returns (image, x, y) for centring it."""
    import cv2

    src_h, src_w = image.shape[:2]
    scale = min(width / src_w, height / src_h)
    new_w = max(1, min(width, round(src_w * scale)))
    new_h = max(1, min(height, round(src_h * scale)))
    if (new_w, new_h) != (src_w, src_h):
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return image, (width - new_w) // 2, (height - new_h) // 2


def media_layer(image: np.ndarray, ctx: RenderContext, fit: str) -> np.ndarray:
    """RGB(A) uint8 picture (h, w, 3 or 4) -> a layer that fills or fits the screen."""
    if fit == "fit":
        scaled, x, y = fit_resize(image, ctx.width, ctx.height)
        layer = transparent(ctx.height, ctx.width)
        h, w = scaled.shape[:2]
        layer[:, y:y + h, x:x + w] = from_image(np.ascontiguousarray(scaled))
        return layer
    return from_image(np.ascontiguousarray(fill_resize(image, ctx.width, ctx.height)))


def _fit_of(element: Mapping[str, Any]) -> str:
    transform = element.get("transform")
    return str(transform.get("fit", "fill")) if _is_map(transform) else "fill"


# ---------------------------------------------------------------- paint


def fill_kind(element: Mapping[str, Any]) -> str:
    """A Colour element's fill (an older Paint element's background): "colour", "linear", "radial" or "none". A Colour
    element always shows something; a Paint element without a fill is see-through."""
    fill = element.get("fill")
    if _is_map(fill):
        kind = str(fill.get("type", "colour"))
        if element.get("kind") == "colour" and kind not in ("colour", "linear", "radial"):
            return "colour"
        return kind
    return "colour" if element.get("kind") == "colour" else "none"


def fill_layer(element: Mapping[str, Any], ctx: RenderContext) -> np.ndarray:
    """A Colour element's picture (or an older Paint element's background): a colour, a gradient, or nothing."""
    t = ctx.time
    width, height = ctx.width, ctx.height
    kind = fill_kind(element)
    if kind == "colour":
        layer = solid_layer(height, width, param(element, "fill.colour", t, (0.53, 0.84, 1.0), "colour"))
    elif kind in ("linear", "radial"):
        a = param(element, "fill.from", t, (1.0, 0.44, 0.35), "colour")
        b = param(element, "fill.to", t, (0.0, 0.72, 1.0), "colour")
        if kind == "linear":
            ramp = direction_ramp(width, height, param(element, "fill.angle", t, 0.0))
        else:
            ramp = radial_ramp(width, height, param(element, "fill.x", t, 0.5), param(element, "fill.y", t, 0.5),
                               param(element, "fill.radius", t, 0.5))
        layer = np.empty((4, height, width), dtype=np.float32)
        for plane in range(3):
            np.multiply(ramp, np.float32(b[plane] - a[plane]), out=layer[plane])
            layer[plane] += np.float32(a[plane])
        layer[3].fill(1.0)
    else:
        layer = transparent(height, width)
    return layer


def render_colour(element: Mapping[str, Any], ctx: RenderContext) -> np.ndarray | None:
    """A Colour element: a solid colour or a gradient (what a Paint element's fill was before M14)."""
    return fill_layer(element, ctx)


def render_paint(element: Mapping[str, Any], ctx: RenderContext, strokes_layer: np.ndarray | None = None) -> np.ndarray | None:
    """An older Paint element's background (if it has one), then brush strokes, then shapes. strokes_layer: strokes
    being painted right now (the Editor's live preview), instead of the element's strokes file."""
    layer = fill_layer(element, ctx)
    strokes = element.get("strokes")  # brush strokes, bucket fills and gradients painted in the Editor
    if strokes_layer is not None:
        layer = over(layer, strokes_layer)
    elif isinstance(strokes, str) and strokes:
        painted = painted_layer(media_path(ctx.folder, strokes), ctx)
        if painted is not None:
            layer = over(layer, painted)
    shapes = element.get("shapes")
    if isinstance(shapes, Sequence) and not isinstance(shapes, str):
        for shape in shapes:
            if _is_map(shape):
                drawn = _shape_box(shape, ctx)
                if drawn is not None:
                    coverage, (y0, y1, x0, x1), rgb = drawn
                    region = layer[:, y0:y1, x0:x1]  # a view: written in place
                    keep = 1.0 - coverage
                    for plane in range(3):
                        region[plane] = coverage * np.float32(rgb[plane]) + region[plane] * keep
                    region[3] = coverage + region[3] * keep
    return layer


def _shape_box(shape: Mapping[str, Any], ctx: RenderContext):
    """A rectangle or ellipse: (coverage within its box, (y0, y1, x0, x1), colour), or None.

    Coverage comes from a distance field (sharp at any size; softness widens the edge),
    and opacity is included."""
    t = ctx.time
    width, height = ctx.width, ctx.height
    opacity = min(1.0, max(0.0, param(shape, "opacity", t, 1.0)))
    half_w = abs(param(shape, "w", t, 0.3)) * width / 2.0
    half_h = abs(param(shape, "h", t, 0.3)) * height / 2.0
    if opacity <= 0.0 or half_w < 0.05 or half_h < 0.05:
        return None
    cx = param(shape, "x", t, 0.5) * width
    cy = param(shape, "y", t, 0.5) * height
    angle = math.radians(param(shape, "rotation", t, 0.0))
    softness = min(1.0, max(0.0, param(shape, "softness", t, 0.0)))
    ellipse = shape.get("type", "rect") == "ellipse"
    feather = max(1.0, softness * min(half_w, half_h))
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    margin = feather + 1.0
    if ellipse:  # the ellipse's distance estimate stretches along its long side
        margin = feather * max(half_w, half_h) / max(min(half_w, half_h), 1e-3) + 1.0
    extent_x = abs(half_w * cos_a) + abs(half_h * sin_a) + margin
    extent_y = abs(half_w * sin_a) + abs(half_h * cos_a) + margin
    x0, x1 = max(0, int(math.floor(cx - extent_x))), min(width, int(math.ceil(cx + extent_x)))
    y0, y1 = max(0, int(math.floor(cy - extent_y))), min(height, int(math.ceil(cy + extent_y)))
    if x0 >= x1 or y0 >= y1:
        return None
    dx = (np.arange(x0, x1, dtype=np.float32) + np.float32(0.5 - cx))[None, :]
    dy = (np.arange(y0, y1, dtype=np.float32) + np.float32(0.5 - cy))[:, None]
    u = dx * np.float32(cos_a) + dy * np.float32(sin_a)  # the pixel in the shape's own (turned) frame
    v = dy * np.float32(cos_a) - dx * np.float32(sin_a)
    if ellipse:
        distance = (np.sqrt((u / np.float32(half_w)) ** 2 + (v / np.float32(half_h)) ** 2) - 1.0) * np.float32(min(half_w, half_h))
    else:
        qx = np.abs(u) - np.float32(half_w)
        qy = np.abs(v) - np.float32(half_h)
        outside = np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2)
        distance = outside + np.minimum(np.maximum(qx, qy), 0.0)
    coverage = np.clip(0.5 - distance / np.float32(feather), 0.0, 1.0)
    if opacity < 1.0:
        coverage *= np.float32(opacity)
    return coverage.astype(np.float32, copy=False), (y0, y1, x0, x1), param(shape, "colour", t, (1.0, 1.0, 1.0), "colour")


def shape_coverage(shape: Mapping[str, Any], ctx: RenderContext) -> tuple[np.ndarray | None, tuple[float, float, float]]:
    """How much of each pixel of the whole picture a shape covers (0-1), and its colour."""
    drawn = _shape_box(shape, ctx)
    if drawn is None:
        return None, (0.0, 0.0, 0.0)
    coverage, (y0, y1, x0, x1), rgb = drawn
    full = np.zeros((ctx.height, ctx.width), dtype=np.float32)
    full[y0:y1, x0:x1] = coverage
    return full, rgb


# ---------------------------------------------------------------- media


_image_cache = _LRU(MEDIA_CACHE_SIZE)


def has_device_name(text: str) -> bool:
    """Does any part of this path have a name Windows keeps for a device ("media/COM3.jpg", "aux.png")? Even
    resolving such a path opens the device (COM3 is the Eurolite's port), so it's checked before anything else."""
    from .model import is_device_name

    return any(is_device_name(part) for part in str(text).replace("\\", "/").split("/") if part.strip())


def media_path(folder: str, file: Any) -> Path | None:
    """A media file's full path, only if it's inside the show folder (for safety), and never a device's name. The name
    is checked as text first: a full path or a network path (\\\\server\\...) is never looked at here, as even asking
    Windows about one can open a device or connect to that server."""
    from .show_io import safe_relative_file

    if not folder or not isinstance(file, str) or not file.strip() or has_device_name(file):
        return None
    relative = safe_relative_file(file)
    if not relative:
        return None
    root = Path(folder).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def load_image(path: Path) -> np.ndarray | None:
    """RGB or RGBA uint8 (h, w, 3 or 4), at most 1280 wide (cached by path and modification time)."""
    import cv2

    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(path), stamp)
    image = _image_cache.get_fresh(key)
    if image is None:
        data = np.fromfile(str(path), dtype=np.uint8)  # works with any characters in the path
        decoded = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if decoded is None:
            return None
        if decoded.ndim == 2:
            decoded = cv2.cvtColor(decoded, cv2.COLOR_GRAY2RGB)
        elif decoded.shape[2] == 4:
            decoded = cv2.cvtColor(decoded, cv2.COLOR_BGRA2RGBA)
        else:
            decoded = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
        if decoded.dtype != np.uint8:
            decoded = (decoded / 257).astype(np.uint8)  # 16-bit PNGs
        if decoded.shape[1] > 1280:
            scale = 1280 / decoded.shape[1]
            decoded = cv2.resize(decoded, (1280, max(1, round(decoded.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        image = np.ascontiguousarray(decoded)
        _image_cache.put(key, image)
    return image


def render_image(element: Mapping[str, Any], ctx: RenderContext) -> np.ndarray | None:
    path = media_path(ctx.folder, element.get("file"))
    if path is None:
        return None
    image = load_image(path)
    if image is None:
        return None
    return media_layer(image, ctx, _fit_of(element))


def render_video(element: Mapping[str, Any], ctx: RenderContext) -> np.ndarray | None:
    """A video file, looping for as long as the element lasts; `speed` plays it faster or slower."""
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
            ctx.waiting = True  # (never cleared here: an NDI feed in the same picture may still be waiting)
        return None
    fit = _fit_of(element)
    key = ("video", str(path), fit, ctx.width, ctx.height)
    cached = ctx.cache.get(key)
    if cached is None or cached[0] is not frame:
        if cached is None:  # (a video this drawing hasn't had yet: the oldest others go, so they can't pile up)
            older = [k for k in ctx.cache if isinstance(k, tuple) and k[:1] == ("video",)]
            for old in older[:max(0, len(older) - VIDEOS_KEPT + 1)]:
                del ctx.cache[old]
        cached = (frame, media_layer(frame, ctx, fit))  # holding the picture keeps it from being mistaken for another
        ctx.cache[key] = cached
    return cached[1]


def ndi_source_name(element: Mapping[str, Any]) -> str:
    name = element.get("source")
    return name if isinstance(name, str) and name.strip() else DEFAULT_NDI_SOURCE


def render_ndi(element: Mapping[str, Any], ctx: RenderContext) -> np.ndarray | None:
    name = ndi_source_name(element)
    frame = ctx.sources.frame(name) if ctx.sources is not None else None
    if frame is None:
        ctx.waiting = True
        return None
    fit = _fit_of(element)
    key = ("ndi", name.lower(), fit, frame.seq, ctx.width, ctx.height)
    cached = ctx.cache.get(key)
    if cached is None:
        cached = media_layer(frame.image, ctx, fit)
        for old in [k for k in ctx.cache if isinstance(k, tuple) and k[:3] == key[:3]]:
            del ctx.cache[old]  # only keep the newest frame of each feed
        ctx.cache[key] = cached
    return cached


RENDERERS = {"ndi": render_ndi, "colour": render_colour, "paint": render_paint, "image": render_image, "video": render_video}


# ---------------------------------------------------------------- one element, start to finish


def render_content(element: Mapping[str, Any], ctx: RenderContext) -> np.ndarray | None:
    """The element's own picture, before its transform, opacity and mask."""
    renderer = RENDERERS.get(str(element.get("kind")))
    if renderer is None:
        return None
    if element_is_dynamic(element):
        return renderer(element, ctx)
    key = (id(element), ctx.width, ctx.height, ctx.folder)
    cached = ctx.static.get_fresh(key)
    if cached is not None and cached[0] is element:
        return cached[1]
    layer = renderer(element, ctx)
    if layer is not None:
        ctx.static.put(key, (element, layer))  # holding the element keeps its id from being reused
    return layer


def render_element(element: Mapping[str, Any], ctx: RenderContext, use_mask: bool = True) -> np.ndarray | None:
    """The element's picture, ready to blend: content, then transform, opacity and mask (or, with per_light, its
    colour at each light: light_elements.py). Elements that never change are finished once and remembered."""
    if ctx.per_light:
        from .light_elements import light_element

        return light_element(element, ctx, use_mask)
    if element_is_dynamic(element):
        return _finish_element(element, ctx, use_mask)
    key = ("finished", id(element), ctx.width, ctx.height, ctx.folder, use_mask)
    cached = ctx.static.get_fresh(key)
    if cached is not None and cached[0] is element:
        return cached[1]
    layer = _finish_element(element, ctx, use_mask)
    if layer is not None:
        ctx.static.put(key, (element, layer))
    return layer


def _finish_element(element: Mapping[str, Any], ctx: RenderContext, use_mask: bool) -> np.ndarray | None:
    layer = render_content(element, ctx)
    if layer is None:
        return None
    return finish_layer(element, layer, ctx, use_mask)


def finish_layer(element: Mapping[str, Any], layer: np.ndarray, ctx: RenderContext, use_mask: bool = False) -> np.ndarray:
    """An element's own picture made ready to blend: moved / scaled / turned, its opacity, its painted mask."""
    t = ctx.time
    x = param(element, "transform.x", t, 0.5)
    y = param(element, "transform.y", t, 0.5)
    scale = param(element, "transform.scale", t, 1.0)
    rotation = param(element, "transform.rotation", t, 0.0)
    layer = warp(layer, x, y, scale, rotation)
    opacity = min(1.0, max(0.0, param(element, "opacity", t, 1.0)))
    if opacity < 1.0:
        layer = scale_alpha(layer, opacity)
    if use_mask:
        keep = element_keep(element, ctx)
        if keep is not None:
            layer = scale_alpha(layer, keep)
    return layer


def mask_shapes(mask: Any) -> list[Mapping[str, Any]]:
    """A mask's shapes (M14), in order: each shows or hides the element inside it."""
    shapes = mask.get("shapes") if _is_map(mask) else None
    if not isinstance(shapes, Sequence) or isinstance(shapes, str):
        return []
    return [shape for shape in shapes if _is_map(shape)]


def mask_hides(shape: Mapping[str, Any]) -> bool:
    """A mask shape hides the element inside it (the default), or shows it ("mode": "show")."""
    return shape.get("mode", "hide") != "show"


def element_keep(element: Mapping[str, Any], ctx: RenderContext) -> np.ndarray | None:
    """The element's mask in a picture (1 = show, 0 = hidden), or None for no mask: where it starts (shown, or hidden
    everywhere), then its painted strokes (they hide), then its shapes in order, each showing or hiding. (The lights it
    leaves alone are only left alone by the lights themselves: the light dots show that, not the picture.)"""
    mask = element.get("mask")
    if not _is_map(mask):
        return None
    hidden = mask.get("start") == "hidden"
    shapes = mask_shapes(mask)
    painted = mask.get("paint")
    keep = None
    if not hidden and isinstance(painted, str) and painted:  # (strokes only hide: on a hidden mask they change nothing)
        keep = painted_mask(media_path(ctx.folder, painted), ctx)
    if not hidden and not shapes:
        return keep
    if hidden:
        keep = np.zeros((ctx.height, ctx.width), dtype=np.float32)
    else:
        keep = keep.copy() if keep is not None else np.ones((ctx.height, ctx.width), dtype=np.float32)
    for shape in shapes:
        cover, _rgb = shape_coverage(shape, ctx)
        if cover is None:
            continue
        if mask_hides(shape):
            keep *= 1.0 - cover
        else:
            keep += cover * (1.0 - keep)
    return keep


_painted: _LRU = _LRU(64, 64_000_000)


def _painted_key(path: Path, ctx: RenderContext, what: str):
    try:
        return (what, str(path), path.stat().st_mtime_ns, ctx.width, ctx.height)
    except OSError:
        return None


def painted_layer(path: Path | None, ctx: RenderContext) -> np.ndarray | None:
    """Brush strokes (paint/<code>.png) as a layer this size (remembered)."""
    import cv2

    from .assets import load_strokes

    key = _painted_key(path, ctx, "strokes") if path is not None else None
    if key is None:
        return None
    cached = _painted.get_fresh(key)
    if cached is not None:
        return cached
    image = load_strokes(path)
    if image is None:
        return None
    if image.shape[:2] != (ctx.height, ctx.width):
        image = cv2.resize(image, (ctx.width, ctx.height), interpolation=cv2.INTER_AREA)
    layer = from_image(np.ascontiguousarray(image))
    _painted.put(key, layer)
    return layer


def painted_mask(path: Path | None, ctx: RenderContext) -> np.ndarray | None:
    """A painted mask (masks/<code>.png) as 0-1 at this size (remembered)."""
    import cv2

    from .assets import load_mask

    key = _painted_key(path, ctx, "mask") if path is not None else None
    if key is None:
        return None
    cached = _painted.get_fresh(key)
    if cached is not None:
        return cached
    grey = load_mask(path)
    if grey is None:
        return None
    if grey.shape != (ctx.height, ctx.width):
        grey = cv2.resize(grey, (ctx.width, ctx.height), interpolation=cv2.INTER_AREA)
    keep = grey.astype(np.float32) * np.float32(1.0 / 255.0)
    _painted.put(key, keep)
    return keep


def blend_mode(element: Mapping[str, Any]) -> str:
    mode = element.get("blend", "normal")
    return mode if mode in ("normal", "add", "multiply", "screen") else "normal"


def ndi_sources_used(tracks) -> set[str]:
    names = set()
    for track in tracks:
        for element in track:
            if element.get("kind") == "ndi":
                names.add(ndi_source_name(element))
    return names


def empty_canvas(ctx: RenderContext) -> np.ndarray:
    return transparent(ctx.height, ctx.width)
