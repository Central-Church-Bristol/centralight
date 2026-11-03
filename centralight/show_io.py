"""Reading and writing a show folder.

A show folder holds:
- show.json: the lights, house lights, show variables and the dashboard order
- inputs\\<id>.json: one file per input (people and AI tools can edit these)
- layouts\\<id>.json: one file per saved map layout
- automations\\<id>.json: one file per automation
- backups\\: zipped copies of the JSON files

Saves are atomic (see files.py). A show written by a newer CentraLight opens read-only.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from fixtures import PROFILES

from .files import forget_written, json_text, loads_json_bytes, read_json, write_atomic, write_json_atomic
from .layouts import ensure_layouts
from .model import (
    COLOUR_PATTERN,
    END_BEHAVIOURS,
    END_HOLD,
    GLOBAL_TYPES,
    ID_PATTERN,
    is_safe_id,
    ROLE_COLOUR,
    ROLE_HOUSE,
    ROLES,
    TRACK_COUNT,
    TRANSITION_KINDS,
    Fixture,
    GlobalVar,
    HouseLight,
    Automation,
    Input,
    Layout,
    MapSpot,
    Show,
    Transition,
    frozen_map,
)

FORMAT_SHOW = "centralight-show"
FORMAT_INPUT = "centralight-input"
FORMAT_LAYOUT = "centralight-layout"
LAYOUT_VERSION = 1
LAYOUTS_DIR = "layouts"
FORMAT_AUTOMATION = "centralight-automation"
AUTOMATION_VERSION = 1
AUTOMATIONS_DIR = "automations"
SHOW_VERSION = 1
INPUT_VERSION = 1

SHOW_FILE = "show.json"
OLD_ROOM_PHOTO = "room.jpg"  # where the room photo lived before saved layouts
INPUTS_DIR = "inputs"
BACKUPS_DIR = "backups"
BACKUP_KEEP_LAST = 20
BACKUP_KEEP_DAYS = 60
MAX_INPUT_BYTES = 2_000_000

DEFAULT_INPUT_COLOUR = "#86D6FF"
NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")

# Ordered migrations: MIGRATIONS[n] turns a version-n show.json dict into version n + 1.
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


class ShowError(Exception):
    """The show can't be opened or saved; the message is meant for people."""


class InputFileError(Exception):
    """An input file can't be used; the message is meant for people (and AI tools)."""


# ---------------------------------------------------------------- plain-data helpers


def freeze(value: Any) -> Any:
    """dicts -> read-only mappings, lists -> tuples, all the way down."""
    if isinstance(value, Mapping):
        return frozen_map({str(k): freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    """The opposite of freeze, ready for json.dumps."""
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in value]
    return value


def _number(value: Any, what: str, low: float | None = None, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputFileError(f"{what} must be a number, not {value!r}")
    if low is not None and value < low:
        raise InputFileError(f"{what} must be at least {low:g}, not {value:g}")
    if high is not None and value > high:
        raise InputFileError(f"{what} must be at most {high:g}, not {value:g}")
    return float(value)


# ---------------------------------------------------------------- inputs


def transition_to_dict(transition: Transition) -> dict[str, Any]:
    """Only what differs from a plain 1-second fade's defaults (kind and duration always)."""
    default = Transition()
    data: dict[str, Any] = {"kind": transition.kind, "duration": transition.duration}
    if transition.direction != default.direction:
        data["direction"] = transition.direction
    if transition.softness != default.softness:
        data["softness"] = transition.softness
    return data


def transition_from_dict(data: Any) -> Transition:
    if not isinstance(data, dict):
        raise InputFileError('"transition" must be like {"kind": "fade", "duration": 1}')
    kind = data.get("kind", "fade")
    if kind not in TRANSITION_KINDS:
        raise InputFileError(f'transition "kind" must be one of {", ".join(TRANSITION_KINDS)}, not {kind!r}')
    duration = _number(data.get("duration", 1.0), 'transition "duration"', 0, 60)
    direction = _number(data.get("direction", 0.0), 'transition "direction"', -3600, 3600)
    softness = _number(data.get("softness", 0.25), 'transition "softness"', 0, 1)
    return Transition(kind, duration, direction, softness)


def input_to_dict(inp: Input) -> dict[str, Any]:
    """The input as JSON data. Fields at their default are left out."""
    data: dict[str, Any] = {"format": FORMAT_INPUT, "version": INPUT_VERSION, "id": inp.id, "name": inp.name}
    if inp.colour.upper() != DEFAULT_INPUT_COLOUR:
        data["colour"] = inp.colour
    if not inp.infinite:
        data["infinite"] = False
    if not inp.infinite or inp.length != 10.0:  # kept for an infinite one too, for when it's timed again
        data["length"] = inp.length
    if inp.end != END_HOLD:
        data["end"] = inp.end
    if inp.transition != Transition():
        data["transition"] = transition_to_dict(inp.transition)
    if inp.hotkey:
        data["hotkey"] = inp.hotkey
    if inp.ai:
        data["ai"] = True
    if inp.house:
        data["house"] = thaw(inp.house)
    if inp.locals:
        data["locals"] = thaw(inp.locals)
    data["tracks"] = thaw(inp.tracks)
    return data


def input_from_dict(data: Any, expected_id: str | None = None) -> tuple[Input, list[str]]:
    """Check and convert input JSON data. Raises InputFileError with a readable reason."""
    warnings: list[str] = []
    if not isinstance(data, dict):
        raise InputFileError("an input must be a JSON object ({ ... })")
    fmt = data.get("format", FORMAT_INPUT)
    if fmt != FORMAT_INPUT:
        raise InputFileError(f'"format" must be "{FORMAT_INPUT}", not {fmt!r}')
    version = data.get("version", INPUT_VERSION)
    if not isinstance(version, int) or version < 1:
        raise InputFileError(f'"version" must be a whole number, not {version!r}')
    if version > INPUT_VERSION:
        raise InputFileError(f"this input was made by a newer CentraLight (version {version})")

    input_id = data.get("id", expected_id)
    if not is_safe_id(input_id):
        raise InputFileError(
            f'"id" must be lower-case letters, digits and dashes (like "pillars-pulse"), and not a name Windows keeps '
            f'for a device (like com3 or aux), not {input_id!r}'
        )
    if expected_id is not None and input_id != expected_id:
        raise InputFileError(f'the file is called "{expected_id}.json" but its "id" is "{input_id}"; they must match')

    name = data.get("name", input_id)
    if not isinstance(name, str) or not name.strip():
        raise InputFileError('"name" must be some text')

    colour = data.get("colour", DEFAULT_INPUT_COLOUR)
    if not isinstance(colour, str) or not COLOUR_PATTERN.match(colour):
        warnings.append(f'"colour" {colour!r} isn\'t like "#86D6FF", so the default was used')
        colour = DEFAULT_INPUT_COLOUR

    infinite = data.get("infinite", True)
    if not isinstance(infinite, bool):
        raise InputFileError('"infinite" must be true or false')
    length = _number(data.get("length", 10.0), '"length"', 0.1, 3600)
    end = data.get("end", END_HOLD)
    if end not in END_BEHAVIOURS:
        raise InputFileError(f'"end" must be one of {", ".join(END_BEHAVIOURS)}, not {end!r}')

    transition = transition_from_dict(data.get("transition", {}))

    hotkey = data.get("hotkey", "")
    if not isinstance(hotkey, str):
        raise InputFileError('"hotkey" must be text like "F5"')
    ai = data.get("ai", False)
    if not isinstance(ai, bool):
        raise InputFileError('"ai" must be true or false')

    house = data.get("house", {})
    if not isinstance(house, dict):
        raise InputFileError('"house" must be an object of fixture id -> level')
    local_vars = data.get("locals", [])
    if not isinstance(local_vars, list) or not all(isinstance(v, dict) for v in local_vars):
        raise InputFileError('"locals" must be a list of objects')

    tracks_data = data.get("tracks", [])
    if not isinstance(tracks_data, list) or len(tracks_data) > TRACK_COUNT:
        raise InputFileError(f'"tracks" must be a list of up to {TRACK_COUNT} lists of elements')
    tracks: list[tuple[Any, ...]] = []
    for index, track in enumerate(tracks_data, start=1):
        if not isinstance(track, list):
            raise InputFileError(f"track {index} must be a list of elements")
        for element in track:
            if not isinstance(element, dict) or not isinstance(element.get("kind"), str):
                raise InputFileError(f'every element on track {index} must be an object with a "kind"')
        if any("blur" in element for element in track):  # (from before 4 Nov: each picture element's Area replaces it)
            track = [{k: v for k, v in element.items() if k != "blur"} for element in track]
            warnings.append(f'unknown field "blur" on track {index} was ignored (Area does that job now)')
        tracks.append(freeze(track))
    while len(tracks) < TRACK_COUNT:
        tracks.append(())

    known = {
        "format", "version", "id", "name", "colour", "infinite", "length", "end", "transition",
        "hotkey", "ai", "house", "locals", "tracks",
    }
    for key in data:
        if key not in known:
            warnings.append(f'unknown field "{key}" was ignored')

    inp = Input(
        id=input_id,
        name=name.strip(),
        colour=colour,
        infinite=infinite,
        length=length,
        end=end,
        transition=transition,
        hotkey=hotkey,
        ai=ai,
        house=freeze(house),
        locals=freeze(local_vars),
        tracks=tuple(tracks),
    )
    return inp, warnings


def input_file(folder: Path, input_id: str) -> Path:
    return Path(folder) / INPUTS_DIR / f"{input_id}.json"


def read_input_file(path: Path) -> tuple[Input, list[str]]:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise InputFileError(f"the file is too big (over {MAX_INPUT_BYTES // 1_000_000} MB)")
        raw = path.read_bytes()
    except InputFileError:
        raise
    except OSError as exc:
        raise InputFileError(f"it couldn't be read ({exc.strerror or exc})") from exc
    return parse_input_bytes(raw, path)


def parse_input_bytes(raw: bytes, path: Path) -> tuple[Input, list[str]]:
    """An input file's bytes -> (input, warnings). Raises InputFileError (only) when it can't be used."""
    if len(raw) > MAX_INPUT_BYTES:
        raise InputFileError(f"the file is too big (over {MAX_INPUT_BYTES // 1_000_000} MB)")
    try:
        data = loads_json_bytes(raw)
    except ValueError as exc:  # (UnicodeDecodeError is one)
        raise InputFileError(f"it isn't valid JSON ({exc})") from exc
    try:
        return input_from_dict(data, expected_id=Path(path).stem)
    except InputFileError:
        raise
    except Exception as exc:  # a value of a type nothing expected: this file is left out, never the whole show
        raise InputFileError(f"something in it couldn't be read ({type(exc).__name__}: {exc})") from exc


def load_inputs(folder: Path) -> tuple[dict[str, Input], dict[str, str], list[str]]:
    """All inputs\\*.json files: (inputs, errors by id, warnings)."""
    inputs: dict[str, Input] = {}
    errors: dict[str, str] = {}
    warnings: list[str] = []
    directory = Path(folder) / INPUTS_DIR
    if not directory.is_dir():
        return inputs, errors, warnings
    for path in sorted(directory.glob("*.json")):
        try:
            inp, input_warnings = read_input_file(path)
        except InputFileError as exc:
            errors[path.stem] = str(exc)
            continue
        except Exception as exc:  # never let one file stop the show opening
            errors[path.stem] = f"something in it couldn't be read ({type(exc).__name__}: {exc})"
            continue
        inputs[inp.id] = inp
        warnings.extend(f"{path.name}: {w}" for w in input_warnings)
    return inputs, errors, warnings


def save_input(folder: Path, inp: Input) -> None:
    write_json_atomic(input_file(folder, inp.id), input_to_dict(inp))


def delete_input_file(folder: Path, input_id: str) -> None:
    path = input_file(folder, input_id)
    if path.exists():
        path.unlink()
    forget_written(path)  # (put back later, even exactly as it was, it's a change)


# ---------------------------------------------------------------- layouts\<id>.json


def _spot_to_dict(spot: MapSpot) -> dict[str, float]:
    return {"x": round(spot.x, 4), "y": round(spot.y, 4)}


def _spot_from_dict(raw: Any) -> MapSpot:
    """Positions only: an "r" (light size) from an older CentraLight is ignored."""
    raw = raw if isinstance(raw, dict) else {}
    return MapSpot(
        x=min(1.0, max(0.0, _soft_number(raw.get("x"), 0.5))),
        y=min(1.0, max(0.0, _soft_number(raw.get("y"), 0.5))),
    )


def safe_relative_file(value: Any) -> str:
    """A file name inside the show folder ("media/room.jpg"), or "" if it points anywhere else."""
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip().replace("\\", "/")
    parts = text.split("/")
    if text.startswith("/") or ":" in text or any(part in ("", ".", "..") for part in parts):
        return ""
    return text


def layout_to_dict(layout: Layout) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": FORMAT_LAYOUT,
        "version": LAYOUT_VERSION,
        "id": layout.id,
        "name": layout.name,
        "positions": {fid: _spot_to_dict(spot) for fid, spot in sorted(layout.positions.items())},
    }
    if layout.backdrop:
        data["backdrop"] = layout.backdrop
    if abs(layout.backdrop_opacity - 0.55) > 1e-9:
        data["backdrop_opacity"] = round(layout.backdrop_opacity, 3)
    return data


def layout_from_dict(data: Any, expected_id: str | None = None) -> tuple[Layout, list[str]]:
    """Check and convert a layout file's data. Raises InputFileError with a readable reason."""
    if not isinstance(data, dict):
        raise InputFileError("it must be a JSON object")
    if data.get("format", FORMAT_LAYOUT) != FORMAT_LAYOUT:
        raise InputFileError(f'"format" must be "{FORMAT_LAYOUT}"')
    version = data.get("version", LAYOUT_VERSION)
    if isinstance(version, int) and not isinstance(version, bool) and version > LAYOUT_VERSION:
        raise InputFileError("it was saved by a newer CentraLight")
    layout_id = data.get("id", expected_id)
    if not is_safe_id(layout_id):
        raise InputFileError(f"bad id {layout_id!r}")
    if expected_id is not None and layout_id != expected_id:
        raise InputFileError(f'its id "{layout_id}" doesn\'t match the file name "{expected_id}.json"')
    warnings: list[str] = []
    positions: dict[str, MapSpot] = {}
    raw_positions = data.get("positions", {})
    if not isinstance(raw_positions, dict):
        warnings.append('"positions" must be an object, so it was ignored')
        raw_positions = {}
    for fixture_id, raw in raw_positions.items():
        if isinstance(fixture_id, str) and ID_PATTERN.match(fixture_id) and isinstance(raw, dict):
            positions[fixture_id] = _spot_from_dict(raw)
        else:
            warnings.append(f"position {fixture_id!r} was left out")
    backdrop = safe_relative_file(data.get("backdrop", ""))
    if data.get("backdrop") and not backdrop:
        warnings.append("the room photo must be a file inside the show folder, so it was left out")
    opacity = min(1.0, max(0.0, _soft_number(data.get("backdrop_opacity"), 0.55)))
    name = data.get("name")
    name = " ".join(name.split()) if isinstance(name, str) and name.strip() else layout_id
    return Layout(layout_id, name, frozen_map(positions), backdrop, opacity), warnings


def layout_file(folder: Path, layout_id: str) -> Path:
    return Path(folder) / LAYOUTS_DIR / f"{layout_id}.json"


def load_layouts(folder: Path) -> tuple[list[Layout], list[str]]:
    """All layouts\\*.json files: (layouts, warnings). A broken file is left out with a warning."""
    layouts: list[Layout] = []
    warnings: list[str] = []
    directory = Path(folder) / LAYOUTS_DIR
    if not directory.is_dir():
        return layouts, warnings
    for path in sorted(directory.glob("*.json")):
        where = f"{LAYOUTS_DIR}\\{path.name}"
        try:
            if path.stat().st_size > MAX_INPUT_BYTES:
                raise InputFileError("the file is too big")
            layout, layout_warnings = layout_from_dict(read_json(path), expected_id=path.stem)
        except InputFileError as exc:
            warnings.append(f"{where} was left out: {exc}")
            continue
        except ValueError as exc:
            warnings.append(f"{where} was left out: it isn't valid JSON ({exc})")
            continue
        except OSError as exc:
            warnings.append(f"{where} was left out: it couldn't be read ({exc.strerror or exc})")
            continue
        except Exception as exc:  # never let one file stop the show opening
            warnings.append(f"{where} was left out: something in it couldn't be read ({type(exc).__name__}: {exc})")
            continue
        layouts.append(layout)
        warnings.extend(f"{where}: {w}" for w in layout_warnings)
    return layouts, warnings


# ---------------------------------------------------------------- automations\<id>.json


def automation_to_dict(automation: Automation) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": FORMAT_AUTOMATION,
        "version": AUTOMATION_VERSION,
        "id": automation.id,
        "name": automation.name,
    }
    if not automation.enabled:
        data["enabled"] = False
    data["logic"] = automation.logic
    data["conditions"] = thaw(automation.conditions)
    data["actions"] = thaw(automation.actions)
    if automation.on_end:
        data["on_end"] = thaw(automation.on_end)
    return data


def _updated_condition(cond: dict, warnings: list[str], scope: str = "show") -> dict:
    """A condition from an older CentraLight: "Blackout is on" is now "The colour lights are black"; and in a show's
    automation, The PC starts, CentraLight starts and The PC wakes are now "When this show loads" (they're the global
    automations' now)."""
    from .automations import CONDITIONS, MOVED_EVENTS

    if cond.get("type") == "blackout":
        warnings.append('the condition "Blackout is on" is now "The colour lights are black"')
        return {**cond, "type": "colour_black"}
    if scope == "show" and cond.get("type") in MOVED_EVENTS:
        warnings.append(f'the condition "{CONDITIONS[cond["type"]]}" is now "When this show loads" (the PC starting, '
                        "CentraLight starting and the PC waking are for global automations now)")
        return {k: v for k, v in cond.items() if k != "type"} | {"type": "show_loads"}
    return cond


def _updated_action(action: Any, warnings: list[str]) -> Any:
    """An action from before Cut / Fade to black: Blackout on is now Fade to black (everything, as Blackout did);
    Blackout off has nothing to turn off now, so it's left out (the next input lights things again)."""
    if not isinstance(action, dict) or action.get("do") != "blackout":
        return action
    if action.get("on", True) is True:
        warnings.append('"Blackout on" is now "Fade to black" (the Festoon and Key lights too)')
        updated = {"do": "fade_black", "fade": 0.5, "all": True}
        if "delay" in action:
            updated["delay"] = action["delay"]
        return updated
    warnings.append('"Blackout off" was left out: there\'s no Blackout to turn off now (the next input lights things again)')
    return None


def automation_from_dict(data: Any, expected_id: str | None = None, scope: str = "show") -> tuple[Automation, list[str]]:
    """Check and convert an automation file's data (scope: a show's automation, or a "global" one). Raises
    InputFileError for a file that can't be used at all. A broken condition switches the automation off (dropping it
    could make the automation run when it shouldn't); a broken action is left out."""
    from .automations import check_action, check_condition

    if not isinstance(data, dict):
        raise InputFileError("it must be a JSON object")
    if data.get("format", FORMAT_AUTOMATION) != FORMAT_AUTOMATION:
        raise InputFileError(f'"format" must be "{FORMAT_AUTOMATION}"')
    version = data.get("version", AUTOMATION_VERSION)
    if isinstance(version, int) and not isinstance(version, bool) and version > AUTOMATION_VERSION:
        raise InputFileError("it was saved by a newer CentraLight")
    automation_id = data.get("id", expected_id)
    if not is_safe_id(automation_id):
        raise InputFileError(f"bad id {automation_id!r}")
    if expected_id is not None and automation_id != expected_id:
        raise InputFileError(f'its id "{automation_id}" doesn\'t match the file name "{expected_id}.json"')
    warnings: list[str] = []
    name = data.get("name")
    name = " ".join(name.split()) if isinstance(name, str) and name.strip() else automation_id
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise InputFileError('"enabled" must be true or false')
    logic = data.get("logic", "all")
    if logic not in ("all", "any"):
        raise InputFileError('"logic" must be "all" or "any"')
    conditions = data.get("conditions", [])
    if not isinstance(conditions, list) or not all(isinstance(c, dict) for c in conditions):
        raise InputFileError('"conditions" must be a list of objects')
    updated: list[str] = []  # notes about bringing an older file up to date (not problems: it stays switched on)
    conditions = [_updated_condition(cond, updated, scope) for cond in conditions]
    for index, cond in enumerate(conditions, start=1):
        for problem in check_condition(cond, scope):
            warnings.append(f"condition {index}: {problem}")
    if warnings and enabled:
        enabled = False
        warnings.append("it was switched off until its conditions are fixed")
    kept: dict[str, list] = {}
    for key in ("actions", "on_end"):
        actions = data.get(key, [])
        if not isinstance(actions, list):
            raise InputFileError(f'"{key}" must be a list of objects')
        kept[key] = []
        actions = [new for action in actions if (new := _updated_action(action, updated)) is not None]
        for index, action in enumerate(actions, start=1):
            problems = check_action(action, scope) if isinstance(action, dict) else ["it must be an object"]
            if problems:
                warnings.append(f"{key.replace('_', ' ')} {index} was left out: {'; '.join(problems)}")
            else:
                kept[key].append(action)
    automation = Automation(
        id=automation_id, name=name, enabled=enabled, logic=logic, conditions=freeze(conditions),
        actions=freeze(kept["actions"]), on_end=freeze(kept["on_end"]),
    )
    return automation, updated + warnings


def automation_file(folder: Path, automation_id: str, subdir: str = AUTOMATIONS_DIR) -> Path:
    """A show's automations are in its automations folder; the global ones straight in theirs (subdir "")."""
    return Path(folder) / subdir / f"{automation_id}.json"


def load_automations(folder: Path, subdir: str = AUTOMATIONS_DIR, scope: str = "show",
                     skip: tuple[str, ...] = ()) -> tuple[dict[str, Automation], list[str]]:
    """All automations\\*.json files: (automations, warnings). A broken file is left out with a warning.
    subdir "" and scope "global": the global automations' folder (skip: its other files, e.g. global.json)."""
    automations: dict[str, Automation] = {}
    warnings: list[str] = []
    directory = Path(folder) / subdir
    if not directory.is_dir():
        return automations, warnings
    for path in sorted(directory.glob("*.json")):
        if path.name.casefold() in {name.casefold() for name in skip}:
            continue
        where = f"{subdir}\\{path.name}" if subdir else path.name
        try:
            if path.stat().st_size > MAX_INPUT_BYTES:
                raise InputFileError("the file is too big")
            automation, problems = automation_from_dict(read_json(path), expected_id=path.stem, scope=scope)
        except InputFileError as exc:
            warnings.append(f"{where} was left out: {exc}")
            continue
        except ValueError as exc:
            warnings.append(f"{where} was left out: it isn't valid JSON ({exc})")
            continue
        except OSError as exc:
            warnings.append(f"{where} was left out: it couldn't be read ({exc.strerror or exc})")
            continue
        except Exception as exc:  # never let one file stop the show opening
            warnings.append(f"{where} was left out: something in it couldn't be read ({type(exc).__name__}: {exc})")
            continue
        automations[automation.id] = automation
        warnings.extend(f"{where}: {w}" for w in problems)
    return automations, warnings


def save_automation(folder: Path, automation: Automation, subdir: str = AUTOMATIONS_DIR) -> None:
    write_json_atomic(automation_file(folder, automation.id, subdir), automation_to_dict(automation))


def delete_automation_file(folder: Path, automation_id: str, subdir: str = AUTOMATIONS_DIR) -> None:
    path = automation_file(folder, automation_id, subdir)
    if path.exists():
        path.unlink()
    forget_written(path)


def save_layout(folder: Path, layout: Layout) -> None:
    write_json_atomic(layout_file(folder, layout.id), layout_to_dict(layout))


def delete_layout_file(folder: Path, layout_id: str) -> None:
    path = layout_file(folder, layout_id)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------- show.json


def fixture_to_dict(fx: Fixture) -> dict[str, Any]:
    data: dict[str, Any] = {"id": fx.id, "name": fx.name, "profile": fx.profile, "address": fx.address}
    if fx.group:
        data["group"] = fx.group
    data["role"] = fx.role
    if fx.map is not None and fx.role == ROLE_COLOUR:
        data["map"] = _spot_to_dict(fx.map)
    return data


def show_to_dict(show: Show) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": FORMAT_SHOW,
        "version": SHOW_VERSION,
        "name": show.name,
        "identify_seed": show.identify_seed,
        "fixtures": [fixture_to_dict(fx) for fx in show.fixtures],
        "house": [{"fixture": h.fixture, "label": h.label} for h in show.house],
        "globals": [_global_to_dict(g) for g in show.globals],
        "input_order": list(show.input_order),
    }
    if show.active_layout:
        data["active_layout"] = show.active_layout
    if show.automation_order or show.automations:
        data["automation_order"] = [a.id for a in show.ordered_automations()] if show.automations else list(show.automation_order)
    return data


def _global_to_dict(g: GlobalVar) -> dict[str, Any]:
    data: dict[str, Any] = {"name": g.name, "type": g.type, "default": g.default}
    if g.type == "number":
        data["min"] = g.min
        data["max"] = g.max
    if g.dashboard:
        data["dashboard"] = True
    return data


def show_from_dict(data: Any) -> tuple[Show, list[str]]:
    """Check and convert show.json data. Raises ShowError with a readable reason."""
    if not isinstance(data, dict):
        raise ShowError("show.json must be a JSON object")
    if data.get("format", FORMAT_SHOW) != FORMAT_SHOW:
        raise ShowError(f'show.json "format" must be "{FORMAT_SHOW}"')
    warnings: list[str] = []

    fixtures: list[Fixture] = []
    seen: set[str] = set()
    fixture_data = data.get("fixtures", [])
    if not isinstance(fixture_data, list):
        raise ShowError('show.json "fixtures" must be a list of lights')
    for index, item in enumerate(fixture_data, start=1):
        try:
            fx = _fixture_from_dict(item)
        except (ShowError, InputFileError) as exc:
            raise ShowError(f"light {index} in show.json: {exc}") from exc
        if fx.id in seen:
            raise ShowError(f'two lights in show.json have the id "{fx.id}"')
        seen.add(fx.id)
        fixtures.append(fx)

    by_id = {fx.id: fx for fx in fixtures}
    house: list[HouseLight] = []
    house_data = data.get("house", [])
    if not isinstance(house_data, list):
        warnings.append('show.json "house" isn\'t a list, so the house lights were left out')
        house_data = []
    for item in house_data:
        if not isinstance(item, dict) or not isinstance(item.get("fixture"), str) or item.get("fixture") not in by_id:
            warnings.append(f"house light {item!r} doesn't match a light, so it was left out")
            continue
        if by_id[item["fixture"]].role != ROLE_HOUSE:
            warnings.append(f'house light "{item["fixture"]}" isn\'t a house-role light, so it was left out')
            continue
        house.append(HouseLight(item["fixture"], str(item.get("label") or by_id[item["fixture"]].name)))

    globals_: list[GlobalVar] = []
    globals_data = data.get("globals", [])
    if not isinstance(globals_data, list):
        warnings.append('show.json "globals" isn\'t a list, so the show variables were left out')
        globals_data = []
    for item in globals_data:
        try:
            globals_.append(_global_from_dict(item))
        except (ShowError, TypeError, ValueError) as exc:
            warnings.append(f"global variable left out: {exc}")

    order = data.get("input_order", [])
    if not isinstance(order, list):
        order = []
    order = [i for i in order if isinstance(i, str)]

    seed = data.get("identify_seed", 7)
    active_layout = data.get("active_layout", "")
    if not isinstance(active_layout, str) or not ID_PATTERN.match(active_layout):
        active_layout = ""
    automation_order = data.get("automation_order", [])
    automation_order = [i for i in automation_order if isinstance(i, str)] if isinstance(automation_order, list) else []
    show = Show(
        name=str(data.get("name") or "Show"),
        identify_seed=int(seed) if isinstance(seed, int) and not isinstance(seed, bool) else 7,
        fixtures=tuple(fixtures),
        house=tuple(house),
        globals=tuple(globals_),
        input_order=tuple(dict.fromkeys(order)),
        active_layout=active_layout,
        automation_order=tuple(dict.fromkeys(automation_order)),
    )
    return show, warnings


def _fixture_from_dict(item: Any) -> Fixture:
    if not isinstance(item, dict):
        raise ShowError("must be an object")
    fixture_id = item.get("id")
    if not isinstance(fixture_id, str) or not ID_PATTERN.match(fixture_id):
        raise ShowError(f"bad id {fixture_id!r}")
    profile = item.get("profile")
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ShowError(f"unknown profile {profile!r} (known: {', '.join(PROFILES)})")
    address = item.get("address")
    if isinstance(address, bool) or not isinstance(address, int) or not 1 <= address <= 512:
        raise ShowError(f"address must be 1-512, not {address!r}")
    if address + PROFILES[profile]["channels"] - 1 > 512:
        raise ShowError(f"address {address} runs past channel 512")
    role = item.get("role", ROLE_COLOUR if "red" in PROFILES[profile] else ROLE_HOUSE)
    if not isinstance(role, str) or role not in ROLES:
        raise ShowError(f"role must be one of {', '.join(ROLES)}")
    spot = None
    if role == ROLE_COLOUR:
        spot = _spot_from_dict(item.get("map"))
    return Fixture(
        id=fixture_id,
        name=str(item.get("name") or fixture_id),
        profile=profile,
        address=address,
        group=str(item.get("group") or ""),
        role=role,
        map=spot,
    )


def _soft_number(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return float(value)


def _global_from_dict(item: Any) -> GlobalVar:
    if not isinstance(item, dict):
        raise ShowError(f"{item!r} must be an object")
    name = item.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise ShowError(f"bad name {name!r} (use letters, digits and _ , starting with a letter)")
    kind = item.get("type", "number")
    if not isinstance(kind, str) or kind not in GLOBAL_TYPES:
        raise ShowError(f'"{name}": type must be one of {", ".join(GLOBAL_TYPES)}')
    default = item.get("default")
    low = _soft_number(item.get("min"), 0.0)
    high = _soft_number(item.get("max"), 1.0)
    if high <= low:
        high = low + 1.0
    if kind == "number":
        default = min(high, max(low, _soft_number(default, low)))
    elif kind == "colour":
        if not isinstance(default, str) or not COLOUR_PATTERN.match(default):
            default = "#FFFFFF"
    else:
        default = bool(default)
    return GlobalVar(name=name, type=kind, default=default, min=low, max=high, dashboard=bool(item.get("dashboard")))


def migrate_show(data: dict) -> tuple[dict, bool]:
    """Bring old show.json data up to date. Returns (data, read_only)."""
    version = data.get("version", SHOW_VERSION)
    if not isinstance(version, int) or version < 1:
        version = 1
    if version > SHOW_VERSION:
        return data, True
    while version < SHOW_VERSION:
        data = MIGRATIONS[version](dict(data))
        version += 1
        data["version"] = version
    return data, False


def load_show(folder: Path) -> Show:
    folder = Path(folder)
    path = folder / SHOW_FILE
    if not path.is_file():
        raise ShowError(f"there's no {SHOW_FILE} in {folder}")
    try:
        data = read_json(path)
    except ValueError as exc:
        raise ShowError(f"{SHOW_FILE} isn't valid JSON ({exc})") from exc
    except OSError as exc:
        raise ShowError(f"{SHOW_FILE} couldn't be read ({exc.strerror or exc})") from exc
    if not isinstance(data, dict):
        raise ShowError(f"{SHOW_FILE} must be a JSON object")
    data, read_only = migrate_show(data)
    show, warnings = show_from_dict(data)
    if read_only:
        warnings.insert(0, "This show was saved by a newer CentraLight, so it's open read-only.")
    inputs, errors, input_warnings = load_inputs(folder)
    layouts, layout_warnings = load_layouts(folder)
    automations, automation_warnings = load_automations(folder)
    # A show from before saved layouts gets "Main", with the old room photo if there is one
    old_photo = OLD_ROOM_PHOTO if not layouts and (folder / OLD_ROOM_PHOTO).is_file() else ""
    show, ensure_warnings = ensure_layouts(replace(show, layouts=tuple(layouts)), backdrop=old_photo)
    return replace(
        show,
        inputs=frozen_map(inputs),
        automations=frozen_map(automations),
        folder=str(folder),
        read_only=read_only,
        warnings=tuple(warnings + input_warnings + layout_warnings + ensure_warnings + automation_warnings),
        input_errors=frozen_map(errors),
    )


def save_show_file(show: Show, folder: Path | None = None) -> None:
    """Write show.json (the inputs are saved one by one with save_input)."""
    if show.read_only:
        raise ShowError("this show is read-only (it was saved by a newer CentraLight)")
    target = Path(folder if folder is not None else show.folder)
    write_json_atomic(target / SHOW_FILE, show_to_dict(show))


def save_whole_show(show: Show, folder: Path | None = None) -> None:
    target = Path(folder if folder is not None else show.folder)
    save_show_file(show, target)
    for inp in show.inputs.values():
        save_input(target, inp)
    for layout in show.layouts:
        save_layout(target, layout)
    for automation in show.automations.values():
        save_automation(target, automation)


# ---------------------------------------------------------------- backups


def _backup_payload(folder: Path) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    show_path = folder / SHOW_FILE
    if show_path.is_file():
        files.append((SHOW_FILE, show_path.read_bytes()))
    for sub in (INPUTS_DIR, LAYOUTS_DIR, AUTOMATIONS_DIR):
        directory = folder / sub
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                files.append((f"{sub}/{path.name}", path.read_bytes()))
    return files


def _backup_time(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.stem, "%Y-%m-%d_%H%M%S")
    except ValueError:
        return None


def list_backups(folder: Path) -> list[tuple[datetime, Path]]:
    """Backups, newest first."""
    directory = Path(folder) / BACKUPS_DIR
    if not directory.is_dir():
        return []
    found = [(_backup_time(p), p) for p in directory.glob("*.zip")]
    return sorted(((t, p) for t, p in found if t is not None), reverse=True)


def backup_show(folder: Path, now: datetime | None = None) -> Path | None:
    """Zip show.json and inputs\\ into backups\\. Skipped when nothing changed since the last one."""
    folder = Path(folder)
    payload = _backup_payload(folder)
    if not payload:
        return None
    digest = hashlib.sha1()
    for name, data in payload:
        digest.update(name.encode("utf-8") + b"\0" + data + b"\0")
    fingerprint = digest.hexdigest().encode("ascii")
    existing = list_backups(folder)
    if existing:
        try:
            with zipfile.ZipFile(existing[0][1]) as old:
                if old.comment == fingerprint:
                    return None
        except (OSError, zipfile.BadZipFile):
            pass
    when = now or datetime.now()
    target = folder / BACKUPS_DIR / f"{when.strftime('%Y-%m-%d_%H%M%S')}.zip"
    while target.exists():  # two backups in one second: never overwrite the first
        when += timedelta(seconds=1)
        target = folder / BACKUPS_DIR / f"{when.strftime('%Y-%m-%d_%H%M%S')}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = fingerprint
        for name, data in payload:
            archive.writestr(name, data)
    write_atomic(target, buffer.getvalue())
    prune_backups(folder, now)
    return target


def prune_backups(
    folder: Path,
    now: datetime | None = None,
    keep_last: int = BACKUP_KEEP_LAST,
    keep_days: int = BACKUP_KEEP_DAYS,
) -> list[Path]:
    """Keep the newest ``keep_last`` backups plus the newest one of each day for ``keep_days`` days."""
    now = now or datetime.now()
    backups = list_backups(folder)
    keep: set[Path] = {p for _, p in backups[:keep_last]}
    days_seen: set[str] = set()
    for stamp, path in backups:
        if now - stamp > timedelta(days=keep_days):
            continue
        day = stamp.strftime("%Y-%m-%d")
        if day not in days_seen:
            days_seen.add(day)
            keep.add(path)
    removed = []
    for _, path in backups:
        if path not in keep:
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                pass
    return removed


def restore_backup(folder: Path, backup: Path) -> None:
    """Put a backup's files back (the current files are backed up first)."""
    folder = Path(folder)
    with zipfile.ZipFile(backup) as archive:  # read it all first, before anything is written
        contents = {name: archive.read(name) for name in archive.namelist()}
    backup_show(folder)
    subfolders = (INPUTS_DIR, LAYOUTS_DIR, AUTOMATIONS_DIR)
    for sub in subfolders:
        directory = folder / sub
        if directory.is_dir():
            for path in directory.glob("*.json"):
                if f"{sub}/{path.name}" not in contents:
                    path.unlink()
    for name, data in contents.items():
        in_folder = any(name.startswith(f"{sub}/") and name.endswith(".json") and "/" not in name[len(sub) + 1:] for sub in subfolders)
        if name == SHOW_FILE or in_folder:
            write_atomic(folder / name, data)


def show_json_text(show: Show) -> str:
    return json_text(show_to_dict(show))


def changed_input_ids(old: Show, new: Show) -> Iterable[str]:
    for input_id, inp in new.inputs.items():
        if old.inputs.get(input_id) != inp:
            yield input_id
