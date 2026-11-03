"""What every element setting is: its name, type, default and range.

This one list drives the Editor's details panel and (from M4) the AI guide and the
input.schema.json file, so they can never disagree with each other.

Paths are dotted, e.g. "transform.x" is element["transform"]["x"].
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .lights import DEFAULT_AREA
from .show_io import freeze, thaw

BLEND_MODES = (("normal", "Normal"), ("add", "Add (brighter)"), ("multiply", "Multiply (darker)"), ("screen", "Screen (lighter)"))
FIT_MODES = (("fill", "Fill (crop the edges)"), ("fit", "Fit (show it all)"))
FILL_TYPES = (("none", "None"), ("colour", "Colour"), ("linear", "Straight gradient"), ("radial", "Round gradient"))
SHAPE_TYPES = (("rect", "Rectangle"), ("ellipse", "Ellipse"))
SNAP_CHOICES = ((True, "Snap"), (False, "Glide"))
TRANSITION_CHOICES = (("cut", "Cut"), ("fade", "Fade"), ("dip", "Dip to black"), ("wipe", "Wipe"), ("iris", "Iris"), ("random", "Random by light"))
ELEMENT_KINDS = (("paint", "Paint"), ("image", "Image"), ("video", "Video"), ("ndi", "NDI"))


@dataclass(frozen=True)
class Spec:
    path: str
    label: str
    type: str  # number | colour | choice | text | source | file
    default: Any
    minimum: float = 0.0
    maximum: float = 1.0
    step: float = 0.01
    unit: str = ""  # "%" shows 0-1 as 0-100 %, "°" degrees, "s" seconds, "×" scale
    choices: tuple[tuple[str, str], ...] = ()
    animatable: bool = True
    help: str = ""
    show_if: tuple[str, tuple[str, ...]] | None = None  # (path, values): only shown when that setting is one of these
    group: str = ""  # shown under a small heading of this name, with the settings next to it in the same group
    long_label: str = ""  # its full name, where the short label would be unclear on its own

    @property
    def title(self) -> str:
        """The setting's full name, for undo steps, the expression editor and the AI guide."""
        if self.long_label:
            return self.long_label
        return f"{self.group} ({self.label.lower()})" if self.group else self.label


COMMON = (
    Spec("opacity", "Opacity", "number", 1.0, unit="%", help="How see-through the element is (100 % = solid)"),
    Spec("blend", "Blend", "choice", "normal", choices=BLEND_MODES, animatable=False, help="How it mixes with the tracks below"),
    Spec("transform.x", "Across", "number", 0.5, -0.5, 1.5, unit="%", help="Where its centre is, left (0 %) to right (100 %)"),
    Spec("transform.y", "Down", "number", 0.5, -0.5, 1.5, unit="%", help="Where its centre is, top (0 %) to bottom (100 %)"),
    Spec("transform.scale", "Size", "number", 1.0, 0.05, 4.0, 0.01, unit="×", help="1 = the normal size"),
    Spec("transform.rotation", "Rotation", "number", 0.0, -360.0, 360.0, 1.0, unit="°"),
)
MEDIA_FIT = Spec("transform.fit", "Fit", "choice", "fill", choices=FIT_MODES, animatable=False, help="Fill crops the edges; Fit shows the whole picture")

PAINT = (
    Spec("fill.type", "Fill", "choice", "colour", choices=FILL_TYPES, animatable=False),
    Spec("fill.colour", "Colour", "colour", "#86D6FF", show_if=("fill.type", ("colour",))),
    Spec("fill.from", "From", "colour", "#FF6F59", show_if=("fill.type", ("linear", "radial"))),
    Spec("fill.to", "To", "colour", "#00B7FF", show_if=("fill.type", ("linear", "radial"))),
    Spec("fill.angle", "Direction", "number", 0.0, -360.0, 360.0, 1.0, unit="°", help="0° = left to right, 90° = top to bottom", show_if=("fill.type", ("linear",))),
    Spec("fill.x", "Centre across", "number", 0.5, 0.0, 1.0, unit="%", show_if=("fill.type", ("radial",))),
    Spec("fill.y", "Centre down", "number", 0.5, 0.0, 1.0, unit="%", show_if=("fill.type", ("radial",))),
    Spec("fill.radius", "Radius", "number", 0.5, 0.01, 2.0, unit="%", help="In screen widths", show_if=("fill.type", ("radial",))),
)

SHAPE = (
    Spec("type", "Shape", "choice", "rect", choices=SHAPE_TYPES, animatable=False),
    Spec("x", "Across", "number", 0.5, -0.5, 1.5, unit="%"),
    Spec("y", "Down", "number", 0.5, -0.5, 1.5, unit="%"),
    Spec("w", "Width", "number", 0.3, 0.0, 3.0, unit="%", help="In screen widths"),
    Spec("h", "Height", "number", 0.3, 0.0, 3.0, unit="%", help="In screen heights"),
    Spec("rotation", "Rotation", "number", 0.0, -360.0, 360.0, 1.0, unit="°"),
    Spec("softness", "Softness", "number", 0.0, 0.0, 1.0, unit="%", help="0 = a crisp edge, 100 % = fades all the way in"),
    Spec("colour", "Colour", "colour", "#FFFFFF"),
    Spec("opacity", "Opacity", "number", 1.0, unit="%"),
)

# Image, Video and NDI: how each light reads the picture (worked out at the lights' dots, light_elements.py)
AREA = Spec("area", "Area", "number", DEFAULT_AREA, unit="%",
            help="How much of the picture around each light it averages: 0 % = just the colour under its dot, "
                 "100 % = the whole picture's average (bigger is calmer)")
BOOST = Spec("boost", "Boost", "number", 0.0, unit="%", long_label="Colour boost",
             help="Colour boost: puts back the colour that averaging a busy picture loses (more saturation, the same hue)")
SMOOTHING = (
    Spec("smooth.up", "Brighter", "number", 0.0, 0.0, 5.0, 0.05, unit="s", group="Smoothing",
         help="How long each light takes to glide to a brighter colour (0 = straight away)"),
    Spec("smooth.down", "Darker", "number", 0.0, 0.0, 5.0, 0.05, unit="s", group="Smoothing",
         help="How long each light takes to glide to a darker colour (0 = straight away)"),
    Spec("smooth.snap", "On cuts", "choice", True, choices=SNAP_CHOICES, animatable=False, group="Smoothing",
         help="When most of the picture changes at once (a slide change, a cut): Snap = the lights jump straight there, "
              "Glide = they glide like any other change"),
)
NDI = (Spec("source", "NDI source", "source", "Media Background Pane", animatable=False, help="Any NDI source whose name contains this"),)
IMAGE = (Spec("file", "Picture", "file", "", animatable=False, help="A picture in the show's media folder"),)
VIDEO = (Spec("file", "Video", "file", "", animatable=False, help="A video in the show's media folder"),
         Spec("speed", "Speed", "number", 1.0, 0.1, 4.0, 0.05, unit="×"))

TRANSITION = (
    Spec("kind", "Transition in", "choice", "fade", choices=TRANSITION_CHOICES, animatable=False),
    Spec("duration", "Length", "number", 1.0, 0.0, 30.0, 0.1, unit="s", animatable=False),
    Spec("direction", "Direction", "number", 0.0, -360.0, 360.0, 5.0, unit="°", animatable=False, help="0° = left to right", show_if=("kind", ("wipe",))),
    Spec("softness", "Softness", "number", 0.25, 0.0, 1.0, unit="%", animatable=False, help="How soft the moving edge is", show_if=("kind", ("wipe", "iris"))),
)


def element_specs(kind: str) -> tuple[Spec, ...]:
    """Every setting of an element of this kind, in the order the details panel shows them."""
    if kind == "paint":
        return PAINT + COMMON
    if kind == "image":
        return IMAGE + (AREA, BOOST) + COMMON + (MEDIA_FIT,)
    if kind == "video":
        return VIDEO + (AREA, BOOST) + SMOOTHING + COMMON + (MEDIA_FIT,)
    if kind == "ndi":
        return NDI + (AREA, BOOST) + SMOOTHING + COMMON + (MEDIA_FIT,)
    return COMMON


def get_path(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return default
    return current


def set_path(data: Mapping[str, Any], path: str, value: Any) -> Mapping[str, Any]:
    """A copy of data with one setting changed (value None removes it). Returns frozen data."""
    plain = thaw(data)
    parts = path.split(".")
    target = plain
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            child = {}
            target[part] = child
        target = child
    if value is None:
        target.pop(parts[-1], None)
    else:
        target[parts[-1]] = thaw(value)
    return freeze(plain)


def visible(spec: Spec, data: Mapping[str, Any], specs: tuple[Spec, ...]) -> bool:
    if spec.show_if is None:
        return True
    path, values = spec.show_if
    default = next((s.default for s in specs if s.path == path), None)
    return get_path(data, path, default) in values
