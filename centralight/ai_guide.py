"""Inputs written by AI, with no API keys.

Two ways in:
1. "Create / Edit with AI": CentraLight writes a prompt for you to paste into any AI chat
   website, and reads the reply you paste back (the JSON can be bare or in a ``` block, with
   words around it). The reply is checked before it's used, and a readable list of problems
   (plus a ready-made "please fix" message) comes back if it isn't right.
2. AI tools working in the show folder (Claude Code, Cursor): they read AI_GUIDE.md (also saved
   as AGENTS.md, which such tools look for) and input.schema.json, and write inputs\\<id>.json;
   the input appears straight away.

Everything here is generated from schema.py, the expression functions and the show's own
lights and globals, so the guide can't disagree with the app. Inputs are data, never code:
expressions use the sandboxed evaluator, and media must be inside the show folder.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .automations import actions_for, conditions_for
from .global_automations import GLOBAL_DIR, ORDER_FILE
from .elements import media_path
from .expressions import AUDIO_NAMES, FUNCTIONS, ExpressionError, compile_expression
from .files import json_text, write_atomic
from .log import log_info
from .links import original_problem
from .model import OVERLAY_MODES, ROLE_COLOUR, TRACK_COUNT, TRANSITION_KINDS, Input, Show, is_device_name, slugify
from .schema import (
    BLEND_MODES,
    COLOUR_TYPES,
    ELEMENT_KINDS,
    MASK_SHAPE,
    SHAPE,
    SHAPE_TYPES,
    TRANSITION,
    Spec,
    element_specs,
)
from .show_io import (
    FORMAT_INPUT,
    INPUT_VERSION,
    MAX_INPUT_BYTES,
    InputFileError,
    input_from_dict,
    input_to_dict,
    safe_relative_file,
)

GUIDE_FILE = "AI_GUIDE.md"
AGENTS_FILE = "AGENTS.md"
SCHEMA_FILE = "input.schema.json"
SKILL_NAME = "centralight-inputs"
SPECIAL_NAMES = {"t", "time", "value", "pi"}
_PICTURE_SETTINGS = ("area", "boost", "smooth.up", "smooth.down", "smooth.snap", "transform.fit")  # (as each kind has them)


class AIReplyError(Exception):
    """A pasted reply that doesn't contain an input, in plain words."""


# ---------------------------------------------------------------- worked examples


def examples(show: Show) -> list[tuple[str, dict]]:
    """(what it shows, an input) — every one passes check_input_data (a test makes sure)."""
    pillars = [fx.id for fx in show.colour_fixtures() if fx.group.casefold() == "pillars"] or [fx.id for fx in show.colour_fixtures()][:2]
    others = [fx.id for fx in show.colour_fixtures() if fx.id not in pillars]
    return [
        ("Only the pillar lights, purple (every other light is left alone, so this works over another input when it's sent to the Overlay)", {
            "format": FORMAT_INPUT, "version": INPUT_VERSION, "id": "pillars-purple", "name": "Pillars purple", "colour": "#7A3CFF",
            "tracks": [[{"id": "E1", "kind": "colour", "fill": {"type": "colour", "colour": "#7A3CFF"}, "mask": {"exclude": others}}], [], [], []],
        }),
        ("A soft white bar sweeping left to right every 5 seconds, over deep blue", {
            "format": FORMAT_INPUT, "version": INPUT_VERSION, "id": "bar-sweep", "name": "Bar sweep", "colour": "#2050FF",
            "transition": {"kind": "fade", "duration": 2},
            "tracks": [
                [{"id": "E1", "kind": "colour", "fill": {"type": "colour", "colour": "#0A1A66"}}],
                [{"id": "E2", "kind": "paint",
                  "shapes": [{"id": "S1", "type": "rect", "x": {"expr": "saw(time, 0.2) * 1.4 - 0.2"}, "y": 0.5, "w": 0.18, "h": 1.2,
                              "softness": 0.5, "colour": "#FFFFFF"}]}],
                [], [],
            ],
        }),
        ("Every light pulsing with the bass of the music (audio.bass is 0 to 1)", {
            "format": FORMAT_INPUT, "version": INPUT_VERSION, "id": "bass-pulse", "name": "Bass pulse", "colour": "#FF6F59",
            "tracks": [[{"id": "E1", "kind": "colour", "fill": {"type": "colour", "colour": "#FF6F59"},
                         "opacity": {"expr": "0.15 + 0.85 * audio.bass"}}], [], [], []],
        }),
        ("Follow the screen (Media Background Pane), but warmer", {
            "format": FORMAT_INPUT, "version": INPUT_VERSION, "id": "warm-screen", "name": "Follow screen, warm", "colour": "#FFB46B",
            "tracks": [
                [{"id": "E1", "kind": "ndi", "source": "Media Background Pane"}],
                [{"id": "E2", "kind": "colour", "fill": {"type": "colour", "colour": "#FFD9A0"}, "blend": "multiply"}],
                [], [],
            ],
        }),
        ("A warm glow drifting slowly from side to side and back, over deep pink (a round gradient whose centre moves)", {
            "format": FORMAT_INPUT, "version": INPUT_VERSION, "id": "drifting-glow", "name": "Drifting glow", "colour": "#FF8A3D",
            "tracks": [[{"id": "E1", "kind": "colour", "fill": {
                "type": "radial", "from": "#FFB347", "to": "#B0306A",
                "x": {"expr": "0.15 + 0.7 * wave(time, 1/20)"}, "y": 0.4, "radius": 0.55}}], [], [], []],
        }),
        ("A timed sequence (12 seconds, then it holds): warm white fades in, the pillars pulse purple from 3 s, and at "
         "8 s everything crosses into deep blue", {
            "format": FORMAT_INPUT, "version": INPUT_VERSION, "id": "welcome-sequence", "name": "Welcome sequence",
            "colour": "#FFB46B", "infinite": False, "length": 12, "end": "hold",
            "tracks": [
                [{"id": "E1", "kind": "colour", "fill": {"type": "colour", "colour": "#FFD9A0"}, "start": 0, "end": 9,
                  "in": {"kind": "fade", "duration": 2}},
                 {"id": "E3", "kind": "colour", "fill": {"type": "colour", "colour": "#0A1A66"}, "start": 8, "end": 12,
                  "in": {"kind": "fade", "duration": 1}}],
                [{"id": "E2", "kind": "colour", "fill": {"type": "colour", "colour": "#7A3CFF"}, "start": 3, "end": 9,
                  "opacity": {"keys": [[0, 0], [1, 1], [5, 1], [6, 0]]}, "mask": {"exclude": others},
                  "out": {"kind": "fade", "duration": 1}}],
                [], [],
            ],
        }),
        ("A slow trip round the rainbow (once every 30 seconds), with the key lights at 40 %", {
            "format": FORMAT_INPUT, "version": INPUT_VERSION, "id": "slow-rainbow", "name": "Slow rainbow", "colour": "#16E1C9",
            "house": {show.house[-1].fixture: 0.4} if show.house else {},
            "locals": [{"name": "turn", "type": "number", "value": {"expr": "time / 30"}}],
            "tracks": [[{"id": "E1", "kind": "colour", "fill": {"type": "linear", "from": {"expr": "hsv(turn)"},
                                                                 "to": {"expr": "hsv(turn + 0.3)"}, "angle": 0}}], [], [], []],
        }),
        ("Warm white revealed light by light, a soft-edged band sweeping across the room every 8 seconds (a mask that "
         "a black rectangle covers, with a white rectangle moving over it)", {
            "format": FORMAT_INPUT, "version": INPUT_VERSION, "id": "reveal-sweep", "name": "Reveal sweep", "colour": "#FFD9A0",
            "tracks": [[{"id": "E1", "kind": "colour", "fill": {"type": "colour", "colour": "#FFD9A0"},
                         "mask": {"shapes": [
                             {"id": "M1", "type": "rect", "grey": 0, "x": 0.5, "y": 0.5, "w": 1.2, "h": 1.2, "softness": 0},
                             {"id": "M2", "type": "rect", "grey": 1, "x": {"expr": "saw(time, 1/8) * 1.6 - 0.3"},
                              "y": 0.5, "w": 0.35, "h": 1.2, "softness": 0.6}]}}], [], [], []],
        }),
        ("Deep blue that fades out across the room from left to right (a mask going from white on the left to black on "
         "the right: a black rectangle with a fully soft edge)", {
            "format": FORMAT_INPUT, "version": INPUT_VERSION, "id": "fade-across", "name": "Fade across", "colour": "#2040C0",
            "tracks": [[{"id": "E1", "kind": "colour", "fill": {"type": "colour", "colour": "#2040C0"},
                         "mask": {"shapes": [
                             {"id": "M1", "type": "rect", "grey": 0, "x": 1.0, "y": 0.5, "w": 2.0, "h": 3.0,
                              "softness": 1}]}}], [], [], []],
        }),
    ]


# ---------------------------------------------------------------- JSON Schema


def _value_schema(spec: Spec) -> dict[str, Any]:
    text = spec.title + (f" ({spec.help})" if spec.help else "")
    if spec.type == "number":
        unit = {"%": " 0-1 (shown as %)", "°": " in degrees", "s": " in seconds", "×": " (1 = normal)"}.get(spec.unit, "")
        plain: dict[str, Any] = {"type": "number"}
        text += f". A number{unit}, from {spec.minimum:g} to {spec.maximum:g}; default {spec.default!r}."
    elif spec.type == "colour":
        plain = {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"}
        text += f'. A colour "#RRGGBB"; default "{spec.default}".'
    elif spec.type == "choice":
        return {"enum": [value for value, _label in spec.choices], "description": f"{text}. Default {json.dumps(spec.default)}."}
    else:
        return {"type": "string", "description": f"{text}. Default {spec.default!r}."}
    if not spec.animatable:
        return {**plain, "description": text}
    return {"description": text, "anyOf": [plain, {"$ref": "#/$defs/keyframes"}, {"$ref": "#/$defs/expression"}]}


def _nested(specs: tuple[Spec, ...]) -> dict[str, Any]:
    """Dotted paths ("fill.colour") as nested JSON Schema objects."""
    root: dict[str, Any] = {}
    for spec in specs:
        parts = spec.path.split(".")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {"type": "object", "properties": {}})["properties"]
        node[parts[-1]] = _value_schema(spec)
    return root


def input_schema(show: Show) -> dict[str, Any]:
    """input.schema.json: the format of inputs\\<id>.json (editors use it for checking and autocomplete)."""
    lights = [fx.id for fx in show.colour_fixtures()]
    houses = [h.fixture for h in show.house]
    element_kinds = []
    for kind, label in ELEMENT_KINDS:
        properties = {
            "id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,31}$", "description": "Unique within the input, e.g. E1"},
            "kind": {"const": kind, "description": label},
            "mask": {"type": "object", "description": "A black-and-white picture over the element (white shows it, black "
                                                      "hides it, grey shows it partly; white everywhere to start with): "
                                                      "a painting, then shapes in order, then lights it leaves alone",
                     "properties": {"exclude": {"type": "array", "items": {"enum": lights}, "uniqueItems": True},
                                    "paint": {"type": "string", "pattern": "^masks/[0-9a-f]{12}\\.png$",
                                              "description": "A mask painted in the Editor (white shows, black hides)"},
                                    "shapes": {"type": "array", "description": "Mask shapes, in order: each puts its grey "
                                                                               "over the mask inside it",
                                               "items": {"type": "object", "required": ["id", "type"],
                                                         "properties": {"id": {"type": "string"}, **_nested(MASK_SHAPE)}}}}},
            "start": {"type": "number", "minimum": 0, "description": "Timed inputs: when it starts (seconds of the input)"},
            "end": {"type": "number", "minimum": 0, "description": "Timed inputs: when it ends"},
            "in": {"$ref": "#/$defs/element_transition", "description": "Timed inputs: how it appears"},
            "out": {"$ref": "#/$defs/element_transition", "description": "Timed inputs: how it goes"},
            **_nested(element_specs(kind, background=True)),  # (an older Paint element's fill: its Background)
        }
        if kind in ("image", "video"):
            properties["original"] = {
                "type": "string", "maxLength": 1000,
                "description": "The file this one was copied from, anywhere on this PC (a full path, e.g. "
                               "G:\\My Drive\\Worship\\candles.mp4). CentraLight plays its own copy (\"file\") and copies "
                               "changes in from here, once someone at the PC has allowed it."}
        if kind == "lights":
            properties["steps"] = {
                "type": "array", "minItems": 1, "description": "Its looks, played in turn (one: a still look). Each sets "
                "some lights: [red, green, blue, brightness], 0-1; a light it doesn't name keeps the step before's look, and a "
                "light no step names is see-through",
                "items": {"type": "object", "propertyNames": {"enum": lights}, "additionalProperties": {
                    "type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number", "minimum": 0, "maximum": 1}}}}
        if kind == "paint":
            properties["shapes"] = {"type": "array", "items": {"type": "object", "required": ["id", "type"],
                                                               "properties": {"id": {"type": "string"}, **_nested(SHAPE)}}}
            properties["strokes"] = {"type": "string", "pattern": "^paint/[0-9a-f]{12}\\.png$",
                                     "description": "Brush painting made in the Editor (under the shapes)"}
        element_kinds.append({"type": "object", "required": ["id", "kind"], "properties": properties})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://centralight.local/input.schema.json",
        "title": "CentraLight input",
        "description": "One look or sequence for the church's lights. Save as inputs/<id>.json in the show folder.",
        "type": "object",
        "required": ["id", "name", "tracks"],
        "properties": {
            "format": {"const": FORMAT_INPUT},
            "version": {"const": INPUT_VERSION},
            "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$", "description": "Lower case, digits and hyphens; also the file name"},
            "name": {"type": "string", "description": "Shown on its Dashboard tile"},
            "colour": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$", "description": "Its tile's colour tag"},
            "infinite": {"type": "boolean", "description": "true (the default): plays until something replaces it; false: timed"},
            "length": {"type": "number", "exclusiveMinimum": 0, "description": "Timed inputs: how long it lasts, in seconds"},
            "end": {"enum": ["hold", "loop", "stop"], "description": "Timed inputs: what happens at the end"},
            "transition": {"type": "object", "description": "How it appears when sent to Base or the Overlay (on the Overlay it also leaves this way)",
                           "properties": _nested(TRANSITION)},
            "overlay": {"enum": list(OVERLAY_MODES), "description": "What its Send to Overlay button does: \"stays\" (the "
                        "default, also when it's left out: each press adds a copy that stays until it's cleared) or "
                        "\"held\" (the copy shows only while the button is held down)"},
            "hotkey": {"type": "string", "description": "A key that sends it to Base, e.g. F5"},
            "ai": {"type": "boolean", "description": "Made by AI (CentraLight sets this)"},
            "house": {"type": "object", "description": "House-light levels (0-1) while it's on Base",
                      "propertyNames": {"enum": houses}, "additionalProperties": {"anyOf": [{"type": "number"}, {"$ref": "#/$defs/keyframes"}, {"$ref": "#/$defs/expression"}]}},
            "locals": {"type": "array", "description": "Its own variables, for expressions",
                       "items": {"type": "object", "required": ["name", "value"],
                                 "properties": {"name": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,31}$"},
                                                "type": {"enum": ["number", "colour"]}, "value": {}}}},
            "tracks": {"type": "array", "maxItems": TRACK_COUNT, "description": "Up to 4 tracks; the first is at the bottom, the last on top. "
                       "An infinite input has at most one element per track.",
                       "items": {"type": "array", "items": {"oneOf": element_kinds}}},
        },
        "$defs": {
            "element_transition": {"type": "object", "properties": {
                "kind": {"enum": [k for k in TRANSITION_KINDS if k != "cut"]},
                "duration": {"type": "number", "exclusiveMinimum": 0}}},
            "keyframes": {"type": "object", "required": ["keys"], "properties": {
                "keys": {"type": "array", "items": {"type": "array", "minItems": 2, "maxItems": 3,
                                                    "description": "[seconds, value, \"smooth\" | \"linear\" | \"hold\"]"}},
                "expr": {"type": "string"}}},
            "expression": {"type": "object", "required": ["expr"], "properties": {
                "expr": {"type": "string", "description": "A formula, e.g. \"sin(time) * 0.5 + 0.5\" (see AI_GUIDE.md)"},
                "value": {"description": "The plain value, which the formula can use as `value`"}}},
        },
    }


# ---------------------------------------------------------------- the guide


def _table(rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    out += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows[1:]]
    return "\n".join(out)


def _spec_rows(specs: tuple[Spec, ...]) -> list[list[str]]:
    rows = [["Setting", "What it is", "Values", "Default"]]
    for spec in specs:
        if spec.type == "number":
            unit = {"%": " (0-1, shown as %)", "°": " degrees", "s": " seconds", "×": " (1 = normal)"}.get(spec.unit, "")
            values = f"{spec.minimum:g} to {spec.maximum:g}{unit}"
        elif spec.type == "colour":
            values = '"#RRGGBB"'
        elif spec.type == "choice":
            values = ", ".join(f"{json.dumps(v)} ({label})" for v, label in spec.choices)
        else:
            values = "text"
        animatable = "" if spec.animatable else " (fixed: no expression)"
        rows.append([f"`{spec.path}`", (spec.title + (f": {spec.help}" if spec.help else "")) + animatable, values, f"`{json.dumps(spec.default)}`"])
    return rows


def _lights_table(show: Show) -> str:
    rows = [["Light id", "Name", "Group", "Where on the map (across, down; 0-1)"]]
    for fx in show.colour_fixtures():
        spot = fx.map
        where = f"{spot.x:.2f}, {spot.y:.2f}" + (f" ({_region(spot.x, spot.y)})" if spot else "") if spot else "not on the map"
        rows.append([f"`{fx.id}`", fx.name, fx.group or "", where])
    return _table(rows)


def _region(x: float, y: float) -> str:
    across = "left" if x < 0.34 else "right" if x > 0.66 else "middle"
    down = "top" if y < 0.34 else "bottom" if y > 0.66 else "middle"
    return "centre" if across == down == "middle" else f"{down} {across}" if down != "middle" else across


def _globals_text(show: Show) -> str:
    if not show.globals:
        return "This show has no show variables yet (they're made in the Editor's Show variables tab)."
    rows = [["Name", "Type", "Range / default"]]
    for g in show.globals:
        detail = f"{g.min:g} to {g.max:g}, starts at {g.default}" if g.type == "number" else f"starts at {g.default}"
        if g.on_load == "reset":  # (M14; the default keeps its last value: show.json's "on_load": "reset" is this)
            detail += ", and goes back to it each time the show loads"
        rows.append([f"`{g.name}`", g.type, detail])
    return _table(rows)


def guide_text(show: Show, full: bool = True) -> str:
    """AI_GUIDE.md (full) or the shorter version that goes into chat prompts."""
    lights = _lights_table(show)
    house = ", ".join(f"`{h.fixture}` ({h.label})" for h in show.house) or "none"
    functions = "\n".join(f"- `{f.help.split(':', 1)[0]}`:{f.help.split(':', 1)[1]}" if ":" in f.help else f"- {f.help}" for f in FUNCTIONS.values())
    parts = [
        f"# Writing CentraLight inputs ({show.name})",
        "CentraLight drives a church's coloured lights. **The main idea:** an input paints a 16:9 picture (like a "
        "screen), and each coloured light shows the colour of the picture under its dot on a map of the room. "
        "So to light the left of the room red, paint the left of the picture red.",
        *([f"## About this show\n\nThe show's own notes (what it's for):\n\n" + "\n".join(f"> {line}" for line in show.notes.splitlines())]
          if show.notes else []),
        "## Files",
        "- Each input is one JSON file: `inputs/<id>.json` in the show folder. The file name must match its `id`.\n"
        "- `id`: lower case letters, digits and hyphens (e.g. `pillars-purple`). It never changes, even if the name does.\n"
        "- CentraLight notices new and changed files straight away (a broken file keeps the last good version, and its "
        "tile says what's wrong). Ctrl+Z in CentraLight undoes a change made outside it.\n"
        "- Pictures and videos must be inside the show folder (put them in `media/` and refer to them as `media/name.jpg`).\n"
        "- A new input appears at the end of the Dashboard by itself. `show.json` holds the lights and settings: don't "
        "edit it.",
        "## An input",
        "```json\n" + json_text(examples(show)[0][1]) + "```",
        "- `tracks`: up to 4 lists of elements. The first track is at the bottom, the last is drawn on top. An "
        "infinite input (the default) has at most one element per track.\n"
        "- `transition`: how it appears when sent to Base or the Overlay (a copy on the Overlay also leaves this way): "
        "`kind` is one of "
        + ", ".join(f'"{k}"' for k in TRANSITION_KINDS) + "; `duration` in seconds; wipe also has `direction` (degrees, "
        "0 = left to right) and wipe / iris `softness` (0-1).\n"
        f"- `house`: house-light levels (0-1). When the input goes on Base they fade to these levels over its "
        f"transition (at once on a cut), and they stay there when it leaves. The Overlay doesn't change house lights. "
        f"House lights: {house}.\n"
        "- `colour`: its tile's colour on the Dashboard. `name`: shown on the tile.\n"
        "- Optional: `hotkey`, a key such as `\"F5\"` that sends it to Base while CentraLight's window is in front. "
        "Leave out `ai` (CentraLight sets it).\n"
        "- Optional: `overlay`, what its Send to Overlay button does: `\"stays\"` (the default, also when it's left "
        "out: each press adds a copy on top that stays until it's cleared) or `\"held\"` (the copy shows only while "
        "the button is held down, like a flash button). Automations always add a copy that stays.\n"
        "- **Infinite or timed.** Inputs are infinite unless they say otherwise: each track holds one element, which "
        "plays until the input is replaced. A timed input has `\"infinite\": false`, a `length` in seconds and an "
        "`end`: \"hold\" (stay on the last moment; the default), \"loop\" or \"stop\" (leave Base) - on the Overlay a timed "
        "input plays once and then leaves. "
        "Each of its elements has a `start` and an `end` (seconds of the input), and can appear and go with its own "
        "transitions: `\"in\": {\"kind\": \"fade\", \"duration\": 1}`, `\"out\": {...}` (kinds as for "
        "`transition`). Where two elements on a track overlap, the later one crosses in over the earlier one (its `in` "
        "kind, over the overlap). Sending a timed input again starts it again.",
        "## Elements",
        "Every element has an `id` (unique in the input, e.g. `E1`), a `kind`, and these shared settings:",
        _table(_spec_rows(element_specs("")))
        + "\n\n`blend`: " + ", ".join(f'"{v}" ({label})' for v, label in BLEND_MODES) + ".\n\n"
        "`transform` moves, scales and turns the element's whole picture (a Colour element too); whatever moves in from "
        "outside is transparent. So to slide a gradient across the room, move a round gradient's centre (`fill.x`), "
        "or scale the element up first (e.g. `transform.scale` 3) and then move it.",
        "### Colour (`\"kind\": \"colour\"`): a block of colour or a gradient",
        "A solid colour or a gradient across the whole screen (`fill.type`: "
        + ", ".join(f'"{v}"' for v, _ in COLOUR_TYPES) + "). For a plain colour, use this.",
        _table(_spec_rows(element_specs("colour")[:8]))
        + "\n\nWhich settings each kind uses: `colour` uses `fill.colour`. `linear` goes from `fill.from` to "
        "`fill.to` across the whole picture, in the direction `fill.angle`. `radial` is `fill.from` at its centre "
        "(`fill.x`, `fill.y`), turning into `fill.to` at `fill.radius` and beyond.",
        "### Paint (`\"kind\": \"paint\"`): shapes and painting",
        "Editable shapes (and brush painting made in the Editor). It's see-through wherever nothing is drawn, so the "
        "tracks below show through: for shapes over a colour, put a Colour element on a track below. (Paint elements "
        "made before Colour elements can have a `fill` under their shapes, as a Colour element's; leave it out of new "
        "ones.)",
        "Shapes (`shapes`, drawn in order, each with an `id` like `S1` and `type` "
        + " or ".join(f'"{v}"' for v, _ in SHAPE_TYPES) + "):",
        _table(_spec_rows(SHAPE[1:])),
        "### NDI (`\"kind\": \"ndi\"`): a live video feed",
        "`source`: any NDI source whose name contains this text. `\"Media Background Pane\"` is the church's own "
        "screen feed (what's on the projector).",
        _table(_spec_rows(tuple(s for s in element_specs("ndi") if s.path in _PICTURE_SETTINGS + ("source",)))),
        "### Image (`\"kind\": \"image\"`)",
        "`file`: a picture inside the show folder, e.g. `media/sunrise.jpg`. `original` (optional): the picture it was "
        "copied from, anywhere on this PC (a full path like `G:\\\\My Drive\\\\Worship\\\\sunrise.jpg`). CentraLight always "
        "plays its own copy (`file`), and copies changes in from the original once someone at the PC has allowed the link "
        "(a link you write shows \"Not checked yet\" until then). To use a picture from elsewhere, give its full path as "
        "`original` and leave `file` out: the person adds it with \"Copy into the show\".",
        _table(_spec_rows(tuple(s for s in element_specs("image") if s.path in _PICTURE_SETTINGS + ("file",)))),
        "### Video (`\"kind\": \"video\"`)",
        "`file`: a video inside the show folder, e.g. `media/candles.mp4`, and `original` (optional) the video it was "
        "copied from, as for Image. It loops for as long as the element lasts; `speed` plays it faster or slower.",
        _table(_spec_rows(tuple(s for s in element_specs("video") if s.path in _PICTURE_SETTINGS + ("file", "speed")))),
        "### Lights (`\"kind\": \"lights\"`): lights set straight, a look or a chase",
        "What the Console page saves (Save look / Save chase as input). `steps` is a list of looks played in turn; "
        "each names some lights by id with `[red, green, blue, brightness]`, 0-1, e.g. "
        "`\"steps\": [{\"slimpar-101\": [1, 0.5, 0, 1]}, {\"slimpar-101\": [0, 0, 1, 0.6]}]`. A light a step "
        "doesn't name keeps the look it had in the step before (so to turn a light off in a step, give it brightness "
        "0); a light no step names is see-through (the tracks under it show). One step is a still look. It sets "
        "colour and brightness only. With more steps:",
        _table(_spec_rows(element_specs("lights")[:7])),
        "How lights read a picture (NDI, Image and Video): each light shows the average of a square patch of this "
        "element's picture around its dot, as big as `area` (0 = just the colour under the dot, 0.25 = a small patch, "
        "the usual, 1 = the whole picture's average, the same for every light; bigger is calmer). `boost` puts back "
        "the colour that averaging a busy picture loses. NDI and Video can also glide: `smooth.up` and `smooth.down` "
        "are how many seconds each light takes to reach a brighter or darker colour (0 = at once), and with "
        "`smooth.snap` (on unless it's `false`) a slide change or a cut still lands at once. These only change this "
        "element, so a sharp Paint shape on another track stays sharp.",
        "### Painting (made in the Editor)",
        "A Paint element can have `strokes`: `paint/<code>.png`, brush painting made in the Editor, drawn under its "
        "shapes. Any element can have a painted mask, `\"mask\": {\"paint\": \"masks/<code>.png\"}` (white "
        "shows the element, black hides it), as well as `exclude`. These files are made by CentraLight: keep them as "
        "they are when you edit an input, and don't make up new names.",
        "### Leaving lights alone (masks)",
        "`\"mask\": {\"exclude\": [\"light-id\", ...]}` on any element: those lights ignore that element (they show the "
        "tracks below, or whatever is underneath on the Overlay). This is how \"only the pillars\" works.",
        "A mask is a black-and-white picture over the element: white shows it, black hides it, and greys show it "
        "partly. It's white everywhere to start with. Besides its painting it can have mask shapes: rectangles and "
        "ellipses (`\"shapes\"`), each with a `grey` (0 black to 1 white) and `x`, `y`, `w`, `h`, `rotation`, `softness` and "
        "`opacity` like a Paint shape's (any of them can have keyframes or expressions). Each light works its mask out at "
        "its dot: white, then the painting, then the shapes in order (each putting its grey over what's under it, by its "
        "softness), then `exclude` on top. A black rectangle over everything and a white one on top means \"only "
        "there\"; a moving one reveals the lights as it passes, and a black rectangle with `softness` 1 fades the element "
        "out across the room:\n\n"
        "```json\n\"mask\": {\"shapes\": [{\"id\": \"M1\", \"type\": \"rect\", \"grey\": 0, \"x\": 0.5, \"y\": 0.5, "
        "\"w\": 1.2, \"h\": 1.2},\n  {\"id\": \"M2\", \"type\": \"rect\", \"grey\": 1, \"x\": {\"expr\": \"saw(time, 1/8) * 1.6 - 0.3\"}, "
        "\"y\": 0.5, \"w\": 0.35, \"h\": 1.2, \"softness\": 0.6}]}\n```",
        "## The lights",
        lights + "\n\nEach light is a point (its dot). Colour and Paint are read exactly at the dot, so a shape's `softness` decides "
        "whether a light fades or switches as an edge passes it; pictures are averaged around the dot (`area`).",
        "## Settings that change: keyframes and expressions",
        "Any number or colour setting can be:\n"
        "- fixed: `0.5` or `\"#FF8800\"`\n"
        "- an expression (a formula, worked out 40 times a second): `{\"expr\": \"sin(time) * 0.5 + 0.5\"}`\n"
        "- an expression with a plain value it can use as `value`: `{\"value\": 0.8, \"expr\": \"value * audio.level\"}`\n"
        "- keyframes: `{\"keys\": [[0, 0.1], [5, 0.9, \"smooth\"]]}` (seconds, value, then \"smooth\", \"linear\" or \"hold\" "
        "for how it gets to the next key; it holds after the last key). The seconds count from the element's own "
        "`start` (in an infinite input, from when the input starts).",
        "### Expressions",
        "Python-style maths: `+ - * / % **`, comparisons, `and` / `or` / `not`, `a if condition else b`, and the "
        "functions below. Colours are written `#RRGGBB` and can be added, mixed and multiplied; a number used as a "
        "colour is a grey. Nothing else is allowed (no text, lists or attributes).\n\n"
        "Names: `time` (seconds since the input started), `t` (the element's own time; the same in infinite inputs), "
        "`value` (the setting's own plain value), " + ", ".join(f"`audio.{n}`" for n in AUDIO_NAMES)
        + " (0-1, from the music), the input's local variables, and the show variables below.\n\n"
        "Cycles: `wave`, `pulse`, `saw`, `tri` and `square` take a time (usually `time`) and a rate in cycles per second, "
        "so `wave(time, 0.25)` breathes once every 4 seconds and `saw(time, 0.2)` sweeps every 5. The rate can be a "
        "variable, e.g. `wave(time, speed)` with a number show variable called speed: changing it changes the pace smoothly, "
        "without a jump. `noise(t)` drifts to a new random value each time `t` passes a whole number, so "
        "`noise(time * 0.2)` wanders every 5 seconds.\n\nFunctions:\n" + functions,
        "### Show variables",
        _globals_text(show) + "\n\nThey're set from the Dashboard and the Stream Deck, so an input can respond live, e.g. "
        "`{\"expr\": \"mix(#FF0000, accent, 0.5)\"}`.",
        "### Local variables",
        "An input's own named values, worked out before its elements (and able to use each other):\n"
        "`\"locals\": [{\"name\": \"width\", \"type\": \"number\", \"value\": {\"expr\": \"0.1 + audio.bass * 0.3\"}}]`. "
        "`type` is \"number\" or \"colour\". Names: letters, digits and `_`, not the same as a function.",
        "## Examples",
    ]
    for text, example in examples(show)[1:] if not full else examples(show):
        parts.append(f"**{text}:**\n\n```json\n{json_text(example)}```")
    parts.append(
        "## Rules (CentraLight checks these)\n"
        "- The JSON must follow `input.schema.json`; unknown settings are ignored with a warning.\n"
        "- Light ids in `mask.exclude` and house-light ids in `house` must be from the tables above.\n"
        "- Every expression must be valid and use only the names above.\n"
        "- Media files must exist inside the show folder.\n"
        f"- At most {TRACK_COUNT} tracks; files up to {MAX_INPUT_BYTES // 1_000_000} MB."
    )
    if full:
        parts.append(
            "## Automations\n"
            "`automations/<id>.json` files make the lights look after themselves, e.g. \"Sunday 09:00-12:30: Warm welcome "
            "on Base, key lights 70 %\". An automation runs its `actions` once when its `conditions` become true (`logic`: "
            "\"all\" or \"any\"; any condition can have `\"invert\": true`), and its optional `on_end` actions when they stop "
            "being true. Each action can wait first (`\"delay\"`: seconds).\n\n"
            "Conditions (`type`): " + ", ".join(f"`{k}` ({v.lower()})" for k, v in conditions_for("show").items()) + ". "
            "`show_loads` happens when this show is loaded (by a person, the Stream Deck or a global automation) and when "
            "the PC starts with it, not when CentraLight reopens after a quit or a crash.\n\n"
            "Actions (`do`): " + ", ".join(f"`{k}` ({v.lower()})" for k, v in actions_for("show").items()) + ".\n\n"
            "The PC starting, CentraLight starting, the PC waking and loading another show are for global automations: "
            f"they're in `..\\{GLOBAL_DIR}` (its AI_GUIDE.md says how).\n\n"
            "```json\n" + json_text({
                "format": "centralight-automation", "version": 1, "id": "sunday-welcome", "name": "Sunday welcome", "logic": "all",
                "conditions": [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "09:00", "to": "12:30"}],
                "actions": [{"do": "base", "input": "warm-white" if "warm-white" in show.inputs
                             else next(iter(show.input_order), "follow-screen")},
                            {"do": "house", "light": show.house[-1].fixture if show.house else "dimmer-4", "level": 0.7, "fade": 2}],
            }) + "```\n\n"
            "Days are `mon` ... `sun`. " + WEEKS_TEXT + " Times `\"HH:MM\"` (overnight ranges like 22:00-02:00 are fine); "
            "dates `\"MM-DD\"` with an optional `year`. Sunrise / sunset (Bristol's times): `{\"type\": \"sun\", \"when\": \"after\", "
            "\"event\": \"sunset\", \"minutes\": -30}` is true from half an hour before sunset until midnight "
            "(`when` after / before, `event` sunset / sunrise, `minutes` -180 to 180)."
        )
    return "\n\n".join(parts) + "\n"


GUIDE_MARKER = "<!-- Made by CentraLight: it rewrites this file when the show changes. Keep your own notes in another file. -->"
WEEKS_TEXT = ("A Days condition can also pick weeks of the month: `\"weeks\": [\"1st\", \"3rd\"]` (any of `1st`, `2nd`, "
              "`3rd`, `4th` and `last`; left out, every week). `{\"type\": \"days\", \"days\": [\"wed\"], \"weeks\": "
              "[\"1st\"]}` is the first Wednesday of each month; `last` is the last one in the month, whether that's the 4th "
              "or the 5th. The weeks apply to every day ticked (for the 1st Wednesday and the 3rd Sunday, use two "
              "automations, or two Days conditions with `\"logic\": \"any\"`).")


def _ours(data: bytes) -> bool:
    """A guide file CentraLight wrote: its marker, or the heading versions before the marker started with."""
    text = data.decode("utf-8", errors="replace").lstrip("\ufeff")
    return text.startswith(GUIDE_MARKER[:30]) or text.startswith("# Writing CentraLight inputs (")


def write_guide_files(show: Show) -> list[str]:
    """Write AI_GUIDE.md, AGENTS.md and input.schema.json into the show folder if they've changed.
    Returns the files written."""
    if not show.folder or show.read_only:
        return []
    folder = Path(show.folder)
    guide = GUIDE_MARKER + "\n" + guide_text(show)
    contents = {GUIDE_FILE: guide, AGENTS_FILE: guide, SCHEMA_FILE: json_text(input_schema(show))}
    written = []
    for name, text in contents.items():
        path = folder / name
        data = text.encode("utf-8")
        try:
            if path.is_file():
                old = path.read_bytes()
                if old == data:
                    continue
                if name != SCHEMA_FILE and not _ours(old):  # someone's own AGENTS.md (or notes): left alone
                    log_info(f"{name} in the show folder wasn't written by CentraLight, so it was left as it is",
                             key=f"guide-not-ours-{name}")
                    continue
            write_atomic(path, data)
            written.append(name)
        except OSError:
            continue
    return written


def global_guide_text(shows: list[tuple[str, str]], house_labels: list[str]) -> str:
    """AI_GUIDE.md for the global automations' folder. shows: (id, name) of each show."""
    show_rows = _table([["`show` id", "Show"]] + [[f"`{sid}`", name] for sid, name in shows]) if shows else "(no shows yet)"
    first = shows[0][0] if shows else "sunday-morning"
    label = house_labels[-1] if house_labels else "Key lights"
    example = {
        "format": "centralight-automation", "version": 1, "id": "sunday-morning", "name": "Sunday morning",
        "logic": "all",
        "conditions": [{"type": "days", "days": ["sun"]}, {"type": "time", "from": "08:30", "to": "13:00"}],
        "actions": [{"do": "load_show", "show": first}, {"do": "house", "label": label, "level": 0.7, "fade": 2}],
    }
    return "\n\n".join([
        "# CentraLight: global automations",
        "Each `<id>.json` file here is a global automation: app-wide, whichever show is loaded. They're for what isn't "
        "about one show: load a show at a set time, cut or fade to black, Master, the Festoon and Key lights, and turning "
        "another global automation on or off. They can't touch a show's inputs, the Overlay, variables or layouts (a show's "
        "own automations do that: they're in each show's `automations` folder). Each second the global automations are "
        f"checked first, then the loaded show's. `{ORDER_FILE}` is their order (CentraLight keeps it; a new file goes at "
        "the end). The file format is the same as a show's automations.",
        "Conditions (`type`): " + ", ".join(f"`{k}` ({v.lower()})" for k, v in conditions_for("global").items()) + ". "
        '"This show is loaded": `{"type": "loaded_show", "show": "<show id>"}`.',
        "Actions (`do`): " + ", ".join(f"`{k}` ({v.lower()})" for k, v in actions_for("global").items()) + ". "
        'Load a show: `{"do": "load_show", "show": "<show id>"}` (the lights hold what they show until one of its inputs is '
        "sent; its own automations are checked straight away, `show_loads` among them). The Festoon and Key lights are "
        'named, not numbered: `{"do": "house", "label": "Key lights", "level": 0.7, "fade": 2}` sets the loaded show\'s '
        "light of that name (skipped, with a note in the log, if it has none). Before a Load a show, and before any "
        "automation's Cut or Fade to black, CentraLight shows a countdown (15 s unless Settings say otherwise) with Load "
        "now, Wait 10 min and Skip; one that comes from the clock starts early, so the change still happens on time. "
        "If Settings has a default show, it opens when the PC starts, and loads again when the automation that loaded a "
        "show stops matching (unless someone loaded a show by hand since, that automation's `on_end` loads one, or "
        "another automation that loads a show matches then), so a schedule only needs its own times. With the default "
        "None (lights off), no show opens and the show closes instead (every light off until a show loads). "
        "`release_console` hands back every light the Console page holds, with Settings' release fade.",
        "```json\n" + json_text(example) + "```",
        "## The shows\n\n" + show_rows,
        "Days are `mon` ... `sun`. " + WEEKS_TEXT + " Times `\"HH:MM\"` (overnight ranges like 22:00-02:00 are fine); "
        "dates `\"MM-DD\"` with an optional `year`; sunrise / sunset are Bristol's times.",
    ]) + "\n"


def write_global_guide(folder: Path, shows: list[tuple[str, str]], house_labels: list[str]) -> list[str]:
    """Write AI_GUIDE.md into the global automations' folder if it has changed. Returns the files written."""
    path = Path(folder) / GUIDE_FILE
    data = (GUIDE_MARKER + "\n" + global_guide_text(shows, house_labels)).encode("utf-8")
    try:
        if path.is_file():
            old = path.read_bytes()
            if old == data:
                return []
            if not _ours(old):
                return []
        write_atomic(path, data)
    except OSError:
        return []
    return [f"{GLOBAL_DIR}\\{GUIDE_FILE}"]


def export_skill(show: Show, target: Path) -> Path:
    """A Claude skill folder (target/centralight-inputs): SKILL.md, the schema and the examples. It works in
    Claude Code (copy it into .claude/skills) or can be uploaded to Claude."""
    folder = Path(target) / SKILL_NAME
    (folder / "examples").mkdir(parents=True, exist_ok=True)
    front = (
        "---\n"
        f"name: {SKILL_NAME}\n"
        "description: Write and edit CentraLight lighting inputs - JSON files in a CentraLight show's inputs folder that "
        "drive a church's coloured lights. Use when asked to make or change a lighting look, chase or effect for CentraLight.\n"
        "---\n\n"
    )
    write_atomic(folder / "SKILL.md", (front + guide_text(show)).encode("utf-8"))
    write_atomic(folder / SCHEMA_FILE, json_text(input_schema(show)).encode("utf-8"))
    for _text, example in examples(show):
        write_atomic(folder / "examples" / f"{example['id']}.json", json_text(example).encode("utf-8"))
    return folder


# ---------------------------------------------------------------- prompts for an AI chat


def create_prompt(show: Show, description: str, name: str = "") -> str:
    request = description.strip() or "(describe the look here)"
    named = f"\nName it: {name.strip()}" if name.strip() else ""
    return (
        "You are helping a church volunteer make a lighting look for CentraLight, the program that drives their church's "
        "coloured lights. Write ONE CentraLight input, following the guide below exactly.\n\n"
        "Reply with the whole input as a single ```json code block. A short sentence before it is fine.\n\n"
        f"What they want: {request}{named}\n\n"
        "----- CentraLight guide -----\n\n" + guide_text(show, full=False)
    )


def edit_prompt(show: Show, inp: Input, request: str) -> str:
    current = json_text(input_to_dict(inp))
    return (
        "You are helping a church volunteer change a lighting look in CentraLight, the program that drives their church's "
        "coloured lights. Here is the input as it is now. Change it as they ask, following the guide below, and keep "
        f'its "id" ("{inp.id}").\n\n'
        "Reply with the whole changed input as a single ```json code block. A short sentence before it is fine.\n\n"
        f"What should change: {request.strip() or '(describe the change here)'}\n\n"
        f"The input now:\n\n```json\n{current}```\n\n"
        "----- CentraLight guide -----\n\n" + guide_text(show, full=False)
    )


def fix_message(problems: list[str]) -> str:
    """'Copy error for the AI': a ready-to-paste reply asking for a fixed version."""
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        "CentraLight couldn't use that input:\n\n" + listed
        + "\n\nPlease fix these and send the whole corrected input again, as a single ```json code block."
    )


# ---------------------------------------------------------------- reading a reply

FENCE = re.compile(r"```[ \t]*(?:json5?|jsonc|JSON)?[ \t]*\r?\n(.*?)```", re.S)


def _lenient(text: str) -> str:
    """Forgive what chat AIs often add: // comments and trailing commas."""
    out, in_string, escape, i = [], False, False, 0
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _outermost_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else None


def parse_reply(text: str) -> dict:
    """The input in a pasted AI reply: a bare JSON object, or one in a ``` block, with words around it.
    Raises AIReplyError with what to do."""
    if not isinstance(text, str) or not text.strip():
        raise AIReplyError("There's nothing to read: copy the AI's whole reply, then paste it here.")
    if len(text) > MAX_INPUT_BYTES * 2:
        raise AIReplyError("That reply is far too long for one input.")
    candidates = [m.group(1) for m in FENCE.finditer(text)]
    outer = _outermost_object(text)
    candidates += [text.strip()] + ([outer] if outer else [])
    found_list = False
    for candidate in candidates:
        for attempt in (candidate, _lenient(candidate)):
            try:
                data = json.loads(attempt)
            except (ValueError, RecursionError):
                continue
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                found_list = True
    if found_list:
        raise AIReplyError("The reply holds a list, not one input: ask the AI for one input at a time.")
    raise AIReplyError("I couldn't find an input in that reply. Copy the AI's whole answer, including the part between the ``` marks.")


# ---------------------------------------------------------------- checking an input from an AI


def _walk_params(value: Any, where: str):
    """(where, expression text) for every expression inside an element."""
    if isinstance(value, Mapping):
        if isinstance(value.get("expr"), str):
            yield where, value["expr"]
        for key, inner in value.items():
            if key not in ("expr", "keys", "value"):
                yield from _walk_params(inner, f"{where}.{key}" if where else key)
    elif isinstance(value, (list, tuple)):
        for index, inner in enumerate(value):
            yield from _walk_params(inner, f"{where}[{index}]")


def check_input_data(data: Any, show: Show, existing_id: str | None = None) -> tuple[Input | None, list[str], list[str]]:
    """Check an input from an AI (pasted or written into the folder). Returns (the input or None,
    problems that stop it being used, notes). For an edit, existing_id keeps its id."""
    problems: list[str] = []
    notes: list[str] = []
    if not isinstance(data, dict):
        return None, ["The input must be a JSON object { ... }."], notes
    if len(json.dumps(data)) > MAX_INPUT_BYTES:
        return None, [f"The input is too big (over {MAX_INPUT_BYTES // 1_000_000} MB)."], notes
    data = dict(data)
    data.setdefault("format", FORMAT_INPUT)
    data.setdefault("version", INPUT_VERSION)
    if existing_id is not None:
        if data.get("id") != existing_id:
            notes.append(f'Its id stays "{existing_id}".')
        data["id"] = existing_id
    else:
        wanted = data.get("id") if isinstance(data.get("id"), str) else ""
        base = slugify(wanted or str(data.get("name") or "ai-input"), "ai-input")
        new_id, n = base, 2
        while new_id in show.inputs:
            new_id = f"{base}-{n}"
            n += 1
        if wanted and is_device_name(wanted):
            notes.append(f'"{wanted}" is a name Windows keeps for a device, so this one is "{new_id}".')
        elif wanted and new_id != wanted:
            notes.append(f'There\'s already an input called "{wanted}", so this one is "{new_id}".')
        data["id"] = new_id
    try:
        inp, warnings = input_from_dict(data, expected_id=data["id"])
    except InputFileError as exc:
        return None, [str(exc)], notes
    notes.extend(warnings)
    lights = {fx.id for fx in show.fixtures if fx.role == ROLE_COLOUR}
    houses = {h.fixture for h in show.house}
    names = {str(item.get("name")) for item in inp.locals} | {g.name for g in show.globals} | SPECIAL_NAMES
    for fixture_id in inp.house:
        if fixture_id not in houses:
            problems.append(f'"house" names "{fixture_id}", which isn\'t a house light (they are: {", ".join(sorted(houses))}).')
    for item in inp.locals:
        raw = item.get("value")
        if isinstance(raw, Mapping) and isinstance(raw.get("expr"), str):
            problems += _expression_problems(raw["expr"], f'local "{item.get("name")}"', names)
    for setting, raw in inp.house.items():
        if isinstance(raw, Mapping) and isinstance(raw.get("expr"), str):
            problems += _expression_problems(raw["expr"], f'house light "{setting}"', names)
    kinds = {k for k, _ in ELEMENT_KINDS}
    outside = outside_media(data, show)
    for track_number, track in enumerate(inp.tracks, start=1):
        if inp.infinite and len(track) > 1:
            problems.append(f"Track {track_number} has {len(track)} elements; an infinite input has at most one per track.")
        for element in track:
            label = f"track {track_number}, {element.get('kind')} {element.get('id', '')}".strip()
            if element.get("kind") not in kinds:
                notes.append(f'{label}: "{element.get("kind")}" isn\'t an element kind CentraLight knows, so it draws nothing.')
            fill = element.get("fill")
            if "fill" in element and not hasattr(fill, "get") and (
                    element.get("kind") == "colour" or (element.get("kind") == "paint" and fill not in (None, "none"))):
                problems.append(f'{label}: "fill" must be an object, e.g. {{"type": "colour", "colour": "#FF6F59"}}.')
            mask = element.get("mask")
            excluded = mask.get("exclude", ()) if isinstance(mask, Mapping) else ()
            for light in excluded or ():
                if light not in lights:
                    problems.append(f'{label}: mask.exclude names "{light}", which isn\'t a light (see the lights table).')
            if element.get("kind") == "lights":
                steps = element.get("steps")
                if not isinstance(steps, (list, tuple)):
                    problems.append(f'{label}: "steps" must be a list of looks, e.g. [{{"slimpar-101": [1, 0, 0, 1]}}].')
                    steps = ()
                named = {light for step in steps if isinstance(step, Mapping) for light in step}
                for light in sorted(str(light) for light in named):
                    if light not in lights:
                        problems.append(f'{label}: a step names "{light}", which isn\'t a light (see the lights table).')
            for where, text in _walk_params(element, ""):
                problems += _expression_problems(text, f"{label}, {where}", names)
            if element.get("kind") in ("image", "video"):
                file = element.get("file")
                original = element.get("original")
                path = media_path(show.folder, file) if show.folder else None
                if isinstance(file, str) and file in outside:
                    problems.append(f'{label}: "{file}" is outside the show folder. "Copy into the show" copies it in '
                                    "(and links it, so changes to it are copied in too).")
                elif isinstance(original, str) and original in outside:
                    problems.append(f'{label}: it links to "{original}", which isn\'t copied into the show yet. '
                                    '"Copy into the show" copies it in.')
                elif not file:
                    problems.append(f"{label}: it needs a \"file\" (a picture in the show's media folder).")
                elif path is None or not path.is_file():
                    problems.append(f'{label}: the file "{file}" isn\'t in the show folder. Put it in the media folder first, '
                                    f"or leave this element out.")
    from dataclasses import replace

    return (replace(inp, ai=True) if not problems else None), problems, notes


def outside_media(data: Any, show: Show) -> dict[str, str]:
    """Pictures and videos an input wants from elsewhere on this PC ({the text as written: the original's path}): a
    "file" that's a full path, or an "original" (a link) with no copy in the show yet. They're copied in, and linked,
    only with an OK at this PC ("Copy into the show"). Nothing here looks at them on the disk: a network path would
    make Windows connect to that server, and a device's name (like COM3) would open the device."""
    found: dict[str, str] = {}
    if not isinstance(data, Mapping) or not show.folder or not isinstance(data.get("tracks"), list):
        return found
    for track in data["tracks"]:
        for element in track if isinstance(track, list) else ():
            if not isinstance(element, Mapping) or element.get("kind") not in ("image", "video"):
                continue
            kind = element["kind"]
            file, original = element.get("file"), element.get("original")
            if isinstance(file, str) and file.strip() and not safe_relative_file(file):
                if not original_problem(file.strip(), kind):
                    found[file] = file.strip()
            elif isinstance(original, str) and original and not original_problem(original, kind):
                inside = media_path(show.folder, file) if isinstance(file, str) and file else None
                if inside is None or not inside.is_file():
                    found[original] = original
    return found


def with_media(data: Any, copied: Mapping[str, str]) -> Any:
    """The input with the pictures and videos copied into the show pointing at their copies, and linked to where they
    came from ({the text as written: "media/..."})."""
    if not copied or not isinstance(data, Mapping) or not isinstance(data.get("tracks"), list):
        return data
    tracks = []
    for track in data["tracks"]:
        if isinstance(track, list):
            moved = []
            for element in track:
                if isinstance(element, Mapping):
                    file, original = element.get("file"), element.get("original")
                    if isinstance(file, str) and file in copied:
                        element = {**element, "file": copied[file], "original": file.strip()}
                    elif isinstance(original, str) and original in copied:
                        element = {**element, "file": copied[original]}
                moved.append(element)
            track = moved
        tracks.append(track)
    return {**data, "tracks": tracks}


def links_of(inp: Input) -> list[str]:
    """The originals an input's pictures and videos are linked to."""
    return [element["original"] for track in inp.tracks for element in track
            if element.get("kind") in ("image", "video") and isinstance(element.get("original"), str) and element["original"]]


def _expression_problems(text: str, where: str, names: set[str]) -> list[str]:
    try:
        compiled = compile_expression(text)
    except ExpressionError as exc:
        return [f'{where}: the expression "{text}" — {exc}.']
    missing = sorted(compiled.names - names)
    if missing:
        return [f'{where}: the expression "{text}" uses "{missing[0]}", which isn\'t a variable here.']
    return []


# ---------------------------------------------------------------- what changed (for edits)


def describe_changes(old: Input, new: Input) -> list[str]:
    """A plain-English list of what an edit changes."""
    changes: list[str] = []
    if old.name != new.name:
        changes.append(f"Name: {old.name} → {new.name}")
    if old.colour != new.colour:
        changes.append(f"Tile colour: {old.colour} → {new.colour}")
    if old.transition != new.transition:
        changes.append(f"Transition: {old.transition.kind} {old.transition.duration:g} s → {new.transition.kind} {new.transition.duration:g} s")
    if old.overlay != new.overlay:  # (M18)
        words = {"stays": "stays until cleared", "held": "only while held"}
        changes.append(f"Send to Overlay: {words.get(old.overlay, old.overlay)} → {words.get(new.overlay, new.overlay)}")
    if dict(old.house) != dict(new.house):
        changes.append("House-light levels changed")
    if [dict(i) for i in old.locals] != [dict(i) for i in new.locals]:
        changes.append("Local variables changed")
    before = {e.get("id"): (n, e) for n, track in enumerate(old.tracks, 1) for e in track}
    after = {e.get("id"): (n, e) for n, track in enumerate(new.tracks, 1) for e in track}
    for element_id, (track, element) in after.items():
        if element_id not in before:
            changes.append(f"New {element.get('kind')} element on track {track}")
        elif before[element_id][1] != element:
            settings = sorted(k for k in set(element) | set(before[element_id][1])
                              if element.get(k) != before[element_id][1].get(k))
            changes.append(f"{element.get('kind', '').title()} {element_id}: " + ", ".join(settings) + " changed")
    for element_id, (track, element) in before.items():
        if element_id not in after:
            changes.append(f"Removed {element.get('kind')} element from track {track}")
    return changes or ["Nothing changed"]
