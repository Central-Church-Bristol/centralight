"""Plain-English names for undo steps, as the History panel shows them:
"Paint E2: opacity 80 % → 60 %", "Ellipse S1: softness 0.2 → 0.4", "Key lights: off → 40 %".
"""

from __future__ import annotations

from typing import Any, Mapping

from .colours import is_colour_text, parse_colour, to_hex
from .params import base_value
from .schema import Spec

KIND_NAMES = {"colour": "Colour", "paint": "Paint", "image": "Image", "video": "Video", "ndi": "NDI"}
SHAPE_NAMES = {"rect": "Rectangle", "ellipse": "Ellipse"}
TEXT_MAX = 28


def lower_first(text: str) -> str:
    """'Opacity' -> 'opacity', but 'NDI source' stays as it is."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def element_name(element: Mapping[str, Any]) -> str:
    kind = str(element.get("kind", ""))
    return f"{KIND_NAMES.get(kind, kind.title() or 'Element')} {element.get('id', '')}".strip()


def shape_name(shape: Mapping[str, Any]) -> str:
    return f"{SHAPE_NAMES.get(str(shape.get('type', 'rect')), 'Shape')} {shape.get('id', '')}".strip()


def mask_shape_name(shape: Mapping[str, Any]) -> str:
    """'Mask rectangle M1' (M14: a shape of an element's mask)."""
    return f"Mask {SHAPE_NAMES.get(str(shape.get('type', 'rect')), 'Shape').lower()} {shape.get('id', '')}".strip()


def _number(value: float) -> str:
    return f"{round(value, 3):g}"


def describe(spec: Spec, raw: Any) -> str:
    """A setting's value as the details panel shows it ("80 %", "1.5 s", "#FF8800")."""
    kind = "colour" if spec.type == "colour" else "number" if spec.type == "number" else "raw"
    value = base_value(raw, 0.0, spec.default, kind)
    if spec.type == "number":
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if spec.unit == "%":
            return f"{round(number * 100)} %"
        if spec.unit == "s":
            return f"{_number(number)} s"
        if spec.unit in ("°", "×"):
            return f"{_number(number)}{spec.unit}"
        return _number(number)
    if spec.type == "colour":
        if isinstance(value, (tuple, list)):
            return to_hex(tuple(value)[:3])
        return value.upper() if is_colour_text(value) else to_hex(parse_colour(value))
    if spec.type == "choice":
        return dict(spec.choices).get(value, str(value))
    text = str(value) if value not in (None, "") else "none"
    return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 1] + "…"


def change_name(subject: str, spec: Spec, old: Any, new: Any) -> str:
    """'Paint E2: opacity 80 % → 60 %'."""
    return f"{subject}: {lower_first(spec.title)} {describe(spec, old)} → {describe(spec, new)}"


def level_name(level: Any) -> str:
    """A house-light level for a step name: 'off' or '40 %'."""
    if level is None:
        return "off"
    try:
        return f"{round(float(base_value(level, 0.0, 0.0, 'number')) * 100)} %"
    except (TypeError, ValueError):
        return "set"


def lights_change_name(subject: str, old: set[str], new: set[str], names: Mapping[str, str]) -> str:
    """A change to an element's light checklist: 'Paint E2: leave out SlimPAR 101'."""
    left_out, included = sorted(new - old), sorted(old - new)

    def who(ids: list[str]) -> str:
        return names.get(ids[0], ids[0]) if len(ids) == 1 else f"{len(ids)} lights"

    if left_out and not included:
        return f"{subject}: leave out {who(left_out)}"
    if included and not left_out:
        return f"{subject}: include {who(included)}"
    return f"{subject}: change lights"
