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
COLOUR_TYPES = FILL_TYPES[1:]  # a Colour element always shows something
SHAPE_TYPES = (("rect", "Rectangle"), ("ellipse", "Ellipse"))
MASK_STARTS = (("shown", "Shown everywhere"), ("hidden", "Hidden everywhere"))
MASK_MODES = (("show", "Show"), ("hide", "Hide"))
SNAP_CHOICES = ((True, "Snap"), (False, "Glide"))
TRANSITION_CHOICES = (("cut", "Cut"), ("fade", "Fade"), ("dip", "Dip to black"), ("wipe", "Wipe"), ("iris", "Iris"), ("random", "Random by light"))
ELEMENT_KINDS = (("colour", "Colour"), ("paint", "Paint"), ("image", "Image"), ("video", "Video"), ("ndi", "NDI"))


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

def _fill(choices: tuple[tuple[str, str], ...], group: str = "", label: str = "Fill",
          help_text: str = "") -> tuple[Spec, ...]:
    return (
        Spec("fill.type", label, "choice", "colour", choices=choices, animatable=False, group=group, help=help_text),
        Spec("fill.colour", "Colour", "colour", "#86D6FF", show_if=("fill.type", ("colour",)), group=group),
        Spec("fill.from", "From", "colour", "#FF6F59", show_if=("fill.type", ("linear", "radial")), group=group),
        Spec("fill.to", "To", "colour", "#00B7FF", show_if=("fill.type", ("linear", "radial")), group=group),
        Spec("fill.angle", "Direction", "number", 0.0, -360.0, 360.0, 1.0, unit="°", help="0° = left to right, 90° = top to bottom",
             show_if=("fill.type", ("linear",)), group=group),
        Spec("fill.x", "Centre across", "number", 0.5, 0.0, 1.0, unit="%", show_if=("fill.type", ("radial",)), group=group),
        Spec("fill.y", "Centre down", "number", 0.5, 0.0, 1.0, unit="%", show_if=("fill.type", ("radial",)), group=group),
        Spec("fill.radius", "Radius", "number", 0.5, 0.01, 2.0, unit="%", help="In screen widths", show_if=("fill.type", ("radial",)),
             group=group),
    )


# A Colour element (M14): a solid colour or a gradient across the screen (what a Paint element's fill was)
COLOUR = _fill(COLOUR_TYPES, help_text="A solid colour, or a straight or round gradient across the screen")
# An older Paint element that has a fill under its painting: its "Background" (setting it to None makes it an ordinary
# Paint element; Split into Colour + Paint moves it to a Colour element of its own)
BACKGROUND = _fill(FILL_TYPES, group="Background", label="Fill",
                   help_text="From before Colour elements: the colour under this element's painting and shapes")

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

# Masks (M14): where a mask starts, and its shapes (each shows or hides the element inside it). Then the painted
# strokes (they hide), the shapes in order, and the lights it leaves alone ("exclude") on top.
MASK_START = Spec("mask.start", "Start with", "choice", "shown", choices=MASK_STARTS, animatable=False,
                  help="Shown everywhere: the mask's Hide shapes and painting hide parts of the element. Hidden "
                       "everywhere: it shows only inside Show shapes")
MASK_SHAPE = (
    Spec("mode", "Show or hide", "choice", "hide", choices=MASK_MODES, animatable=False,
         help="Show: the element shows inside it. Hide: it's hidden inside it"),
    *(spec for spec in SHAPE if spec.path not in ("colour", "opacity")),
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
IMAGE = (Spec("file", "Picture", "file", "", animatable=False,
              help="CentraLight's own copy of the picture, in the show's media folder. Choose… links it to the original, wherever it is"),)
VIDEO = (Spec("file", "Video", "file", "", animatable=False,
              help="CentraLight's own copy of the video, in the show's media folder. Choose… links it to the original, wherever it is"),
         Spec("speed", "Speed", "number", 1.0, 0.1, 4.0, 0.05, unit="×"))

TRANSITION = (
    Spec("kind", "Transition in", "choice", "fade", choices=TRANSITION_CHOICES, animatable=False),
    Spec("duration", "Length", "number", 1.0, 0.0, 30.0, 0.1, unit="s", animatable=False),
    Spec("direction", "Direction", "number", 0.0, -360.0, 360.0, 5.0, unit="°", animatable=False, help="0° = left to right", show_if=("kind", ("wipe",))),
    Spec("softness", "Softness", "number", 0.25, 0.0, 1.0, unit="%", animatable=False, help="How soft the moving edge is", show_if=("kind", ("wipe", "iris"))),
)


def has_background(element: Mapping[str, Any] | None) -> bool:
    """An older Paint element with a fill under its painting (a new Paint element has none)."""
    if element is None or element.get("kind") != "paint":
        return False
    fill = element.get("fill")
    return isinstance(fill, Mapping) and fill.get("type", "colour") != "none"


def element_specs(kind: str, element: Mapping[str, Any] | None = None, background: bool | None = None) -> tuple[Spec, ...]:
    """Every setting of an element of this kind, in the order the details panel shows them. A Paint element's
    Background settings are there only when it has one (background: say so instead of looking at element)."""
    if kind == "colour":
        return COLOUR + COMMON
    if kind == "paint":
        with_background = has_background(element) if background is None else background
        return (BACKGROUND if with_background else ()) + COMMON
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
