"""Changes to inputs, as plain functions: each takes a Show and returns a new one.

The Editor and the Dashboard turn each of these into an undo step; tests call them directly.
In infinite mode (the default, and all there is until M5) each track holds one element.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from .model import TRACK_COUNT, Input, Show, slugify
from .params import with_base_value
from .schema import set_path
from .show_io import freeze, thaw

TAG_COLOURS = ("#86D6FF", "#FF6F59", "#D6FF79", "#16E1C9", "#C04DFF", "#FFB547", "#FF6FB5", "#7A9CFF")
MEDIA_DIR = "media"


def _replace_input(show: Show, inp: Input) -> Show:
    inputs = dict(show.inputs)
    inputs[inp.id] = inp
    order = show.input_order if inp.id in show.input_order else show.input_order + (inp.id,)
    return show.with_inputs(inputs, order)


def unique_input_id(show: Show, name: str) -> str:
    base = slugify(name, "input")
    candidate, n = base, 2
    while candidate in show.inputs or candidate in show.input_errors:
        candidate = f"{base[:60]}-{n}"
        n += 1
    return candidate


def unique_name(show: Show, name: str) -> str:
    names = {inp.name for inp in show.inputs.values()}
    if name not in names:
        return name
    n = 2
    while f"{name} {n}" in names:
        n += 1
    return f"{name} {n}"


def new_input(show: Show, name: str = "New input") -> tuple[Show, str]:
    """An empty input (4 empty tracks, infinite, 1 s fade), added at the end of the dashboard."""
    name = unique_name(show, name.strip() or "New input")
    input_id = unique_input_id(show, name)
    colour = TAG_COLOURS[len(show.inputs) % len(TAG_COLOURS)]
    return _replace_input(show, Input(id=input_id, name=name, colour=colour)), input_id


def duplicate_input(show: Show, input_id: str) -> tuple[Show, str]:
    source = show.inputs[input_id]
    name = unique_name(show, f"{source.name} copy")
    new_id = unique_input_id(show, name)
    copy = replace(source, id=new_id, name=name, hotkey="")
    order = list(show.input_order)
    position = order.index(input_id) + 1 if input_id in order else len(order)
    order.insert(position, new_id)
    inputs = dict(show.inputs)
    inputs[new_id] = copy
    return show.with_inputs(inputs, tuple(order)), new_id


def delete_input(show: Show, input_id: str) -> Show:
    inputs = {k: v for k, v in show.inputs.items() if k != input_id}
    return show.with_inputs(inputs, tuple(i for i in show.input_order if i != input_id))


def update_input(show: Show, input_id: str, **changes: Any) -> Show:
    """Change an input's own settings (name, colour, transition, house, ...)."""
    inp = show.inputs[input_id]
    if "name" in changes:
        changes["name"] = str(changes["name"]).strip() or inp.name
    if "house" in changes:
        changes["house"] = freeze(changes["house"])
    return _replace_input(show, replace(inp, **changes))


def set_transition(show: Show, input_id: str, **changes: Any) -> Show:
    inp = show.inputs[input_id]
    return _replace_input(show, replace(inp, transition=replace(inp.transition, **changes)))


def set_house_level(show: Show, input_id: str, fixture_id: str, level: float | None) -> Show:
    """level None: the input leaves that house light alone."""
    inp = show.inputs[input_id]
    house = thaw(inp.house)
    if level is None:
        house.pop(fixture_id, None)
    else:  # an expression on the level stays
        house[fixture_id] = with_base_value(house.get(fixture_id), round(min(1.0, max(0.0, float(level))), 4))
    return _replace_input(show, replace(inp, house=freeze(house)))


def set_house_param(show: Show, input_id: str, fixture_id: str, param: Any) -> Show:
    """Set a house level as a full parameter (e.g. with an expression)."""
    inp = show.inputs[input_id]
    house = thaw(inp.house)
    house[fixture_id] = thaw(param)
    return _replace_input(show, replace(inp, house=freeze(house)))


# ---------------------------------------------------------------- local variables

LOCAL_DEFAULTS = {"number": 0.0, "colour": "#FFFFFF"}


def add_local(show: Show, input_id: str, kind: str = "number") -> tuple[Show, str]:
    """A new local variable for one input (local1, local2...). Returns (show, its name)."""
    inp = show.inputs[input_id]
    kind = kind if kind in LOCAL_DEFAULTS else "number"
    taken = {item.get("name") for item in inp.locals} | {g.name for g in show.globals}
    name, n = "local", 1
    while f"{name}{n}" in taken:
        n += 1
    name = f"{name}{n}"
    items = thaw(inp.locals) + [{"name": name, "type": kind, "value": LOCAL_DEFAULTS[kind]}]
    return _replace_input(show, replace(inp, locals=freeze(items))), name


def update_local(show: Show, input_id: str, name: str, **changes: Any) -> Show:
    """Change a local's type or value (a parameter). A new type resets the value."""
    inp = show.inputs[input_id]
    items = thaw(inp.locals)
    for item in items:
        if item.get("name") == name:
            if "type" in changes and changes["type"] in LOCAL_DEFAULTS and changes["type"] != item.get("type"):
                item["type"] = changes["type"]
                item["value"] = LOCAL_DEFAULTS[changes["type"]]
            if "value" in changes:
                item["value"] = thaw(changes["value"])
            break
    return _replace_input(show, replace(inp, locals=freeze(items)))


def rename_local(show: Show, input_id: str, name: str, new_name: str) -> Show:
    inp = show.inputs[input_id]
    items = thaw(inp.locals)
    if new_name == name or any(item.get("name") == new_name for item in items):
        return show
    for item in items:
        if item.get("name") == name:
            item["name"] = new_name
    return _replace_input(show, replace(inp, locals=freeze(items)))


def remove_local(show: Show, input_id: str, name: str) -> Show:
    inp = show.inputs[input_id]
    items = [item for item in thaw(inp.locals) if item.get("name") != name]
    return _replace_input(show, replace(inp, locals=freeze(items)))


# ---------------------------------------------------------------- elements


def _tracks(inp: Input) -> list[list[dict]]:
    tracks = thaw(inp.tracks)
    while len(tracks) < TRACK_COUNT:
        tracks.append([])
    return tracks


def _set_tracks(show: Show, inp: Input, tracks: list[list[dict]]) -> Show:
    return _replace_input(show, replace(inp, tracks=tuple(freeze(track) for track in tracks)))


def new_element_id(inp: Input, prefix: str = "E") -> str:
    used = set()
    for track in inp.tracks:
        for element in track:
            used.add(str(element.get("id", "")))
            for shape in element.get("shapes", ()) or ():
                used.add(str(shape.get("id", "")))
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def default_element(kind: str, element_id: str, ndi_source: str = "Media Background Pane") -> dict:
    if kind == "paint":
        return {"id": element_id, "kind": "paint", "fill": {"type": "colour", "colour": "#86D6FF"}}
    if kind == "ndi":
        return {"id": element_id, "kind": "ndi", "source": ndi_source}
    if kind in ("image", "video"):
        return {"id": element_id, "kind": kind, "file": ""}
    raise ValueError(f"unknown element kind {kind!r}")


def find_element(inp: Input, element_id: str) -> tuple[int, int, Any] | None:
    """(track index, position, element) or None."""
    for t, track in enumerate(inp.tracks):
        for p, element in enumerate(track):
            if element.get("id") == element_id:
                return t, p, element
    return None


def add_element(show: Show, input_id: str, track: int, kind: str, ndi_source: str = "Media Background Pane",
                element: dict | None = None) -> tuple[Show, str]:
    """Put a new element on a track (in infinite mode it replaces what was there)."""
    inp = show.inputs[input_id]
    element_id = new_element_id(inp)
    data = dict(element) if element is not None else default_element(kind, element_id, ndi_source)
    data["id"] = element_id
    tracks = _tracks(inp)
    if inp.infinite:
        tracks[track] = [data]
    else:
        tracks[track].append(data)
    return _set_tracks(show, inp, tracks), element_id


def remove_element(show: Show, input_id: str, element_id: str) -> Show:
    inp = show.inputs[input_id]
    tracks = [[e for e in track if e.get("id") != element_id] for track in _tracks(inp)]
    return _set_tracks(show, inp, tracks)


def move_element(show: Show, input_id: str, element_id: str, to_track: int) -> Show:
    """Move an element to another track (in infinite mode, swapping with what's there)."""
    inp = show.inputs[input_id]
    found = find_element(inp, element_id)
    if found is None or not 0 <= to_track < TRACK_COUNT or found[0] == to_track:
        return show
    tracks = _tracks(inp)
    from_track = found[0]
    element = tracks[from_track].pop(found[1])
    if inp.infinite:
        tracks[from_track] = tracks[to_track]
        tracks[to_track] = [element]
    else:
        tracks[to_track].append(element)
    return _set_tracks(show, inp, tracks)


def set_element_value(show: Show, input_id: str, element_id: str, path: str, value: Any) -> Show:
    """Change one setting of an element (value None removes it, going back to the default)."""
    inp = show.inputs[input_id]
    found = find_element(inp, element_id)
    if found is None:
        return show
    t, p, element = found
    tracks = [list(track) for track in inp.tracks]
    tracks[t][p] = set_path(element, path, value)
    return _replace_input(show, replace(inp, tracks=tuple(tuple(track) for track in tracks)))


def set_excluded(show: Show, input_id: str, element_id: str, light_ids: list[str]) -> Show:
    """The lights an element leaves alone (its mask). An empty list removes the mask."""
    return set_element_value(show, input_id, element_id, "mask.exclude", sorted(light_ids) if light_ids else None)


# ---------------------------------------------------------------- shapes


def add_shape(show: Show, input_id: str, element_id: str, shape_type: str = "rect",
              x: float = 0.5, y: float = 0.5, w: float = 0.3, h: float = 0.4, colour: str = "#FFFFFF") -> tuple[Show, str]:
    inp = show.inputs[input_id]
    found = find_element(inp, element_id)
    if found is None:
        return show, ""
    shape_id = new_element_id(inp, "S")
    shapes = thaw(found[2].get("shapes", [])) or []
    shapes.append({"id": shape_id, "type": shape_type, "x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4), "colour": colour})
    return set_element_value(show, input_id, element_id, "shapes", shapes), shape_id


def _shapes(show: Show, input_id: str, element_id: str) -> list[dict]:
    found = find_element(show.inputs[input_id], element_id)
    if found is None:
        return []
    return thaw(found[2].get("shapes", [])) or []


def set_shape_value(show: Show, input_id: str, element_id: str, shape_id: str, path: str, value: Any) -> Show:
    shapes = _shapes(show, input_id, element_id)
    for i, shape in enumerate(shapes):
        if shape.get("id") == shape_id:
            shapes[i] = thaw(set_path(shape, path, value))
            return set_element_value(show, input_id, element_id, "shapes", shapes)
    return show


def set_shape_values(show: Show, input_id: str, element_id: str, shape_id: str, values: dict[str, Any]) -> Show:
    """Several settings of one shape at once (a drag moves x and y together)."""
    shapes = _shapes(show, input_id, element_id)
    for i, shape in enumerate(shapes):
        if shape.get("id") == shape_id:
            for path, value in values.items():
                shape = thaw(set_path(shape, path, round(value, 4) if isinstance(value, float) else value))
            shapes[i] = shape
            return set_element_value(show, input_id, element_id, "shapes", shapes)
    return show


def remove_shape(show: Show, input_id: str, element_id: str, shape_id: str) -> Show:
    shapes = [s for s in _shapes(show, input_id, element_id) if s.get("id") != shape_id]
    return set_element_value(show, input_id, element_id, "shapes", shapes or None)


def move_shape(show: Show, input_id: str, element_id: str, shape_id: str, step: int) -> Show:
    """Move a shape up (+1, drawn later, on top) or down (-1) in its element's list."""
    shapes = _shapes(show, input_id, element_id)
    index = next((i for i, s in enumerate(shapes) if s.get("id") == shape_id), None)
    if index is None or not 0 <= index + step < len(shapes):
        return show
    shapes[index], shapes[index + step] = shapes[index + step], shapes[index]
    return set_element_value(show, input_id, element_id, "shapes", shapes)


# ---------------------------------------------------------------- media


def copy_media(show_folder: str, source: Path) -> str:
    """Copy a picture or video into the show's media folder. Returns its path relative to the show."""
    source = Path(source)
    target_dir = Path(show_folder) / MEDIA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9 _.-]+", "", source.stem).strip() or "media"
    target = target_dir / f"{stem}{source.suffix.lower()}"
    n = 2
    while target.exists():
        if target.stat().st_size == source.stat().st_size and target.read_bytes() == source.read_bytes():
            return f"{MEDIA_DIR}/{target.name}"  # the same file is already there
        target = target_dir / f"{stem} {n}{source.suffix.lower()}"
        n += 1
    shutil.copy2(source, target)
    return f"{MEDIA_DIR}/{target.name}"
