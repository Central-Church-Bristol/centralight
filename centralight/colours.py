"""Colour helpers: '#RRGGBB' text <-> (r, g, b) from 0 to 1."""

from __future__ import annotations

from typing import Any

Colour = tuple[float, float, float]


def parse_colour(value: Any, default: Colour = (0.0, 0.0, 0.0)) -> Colour:
    """'#RRGGBB' (or a 0-1 grey number) -> (r, g, b) from 0 to 1."""
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            return (int(value[1:3], 16) / 255.0, int(value[3:5], 16) / 255.0, int(value[5:7], 16) / 255.0)
        except ValueError:
            return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        grey = min(1.0, max(0.0, float(value)))
        return (grey, grey, grey)
    return default


def to_hex(rgb: Colour) -> str:
    """(r, g, b) from 0 to 1 -> '#RRGGBB'."""
    return "#" + "".join(f"{min(255, max(0, round(v * 255))):02X}" for v in rgb)


def is_colour_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 7 and value.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in value[1:])
