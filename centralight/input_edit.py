"""Changes to inputs, as plain functions: each takes a Show and returns a new one.

The Editor and the Dashboard turn each of these into an undo step; tests call them directly.
In infinite mode (the default) each track holds one element. A timed input has a length, and its
elements sit at times on their tracks ("start" / "end", in seconds of the input); keyframe times
count from the element's own start.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from .colours import to_hex
from .model import END_BEHAVIOURS, TRACK_COUNT, Input, Show, element_span, is_device_name, slugify
from .params import (
    INTERPOLATIONS,
    KEY_TOLERANCE,
    base_value,
    is_keyframed,
    keys_of,
    keys_shifted,
    with_base_value,
    with_key,
    with_key_interpolation,
    with_key_moved,
    without_key,
)
from .schema import set_path
from .show_io import freeze, thaw

TAG_COLOURS = ("#86D6FF", "#FF6F59", "#D6FF79", "#16E1C9", "#C04DFF", "#FFB547", "#FF6FB5", "#7A9CFF")
MEDIA_DIR = "media"
DEFAULT_ELEMENT_LENGTH = 5.0  # seconds: a new element on a timed input's track
MIN_ELEMENT_LENGTH = 0.1  # seconds: trimming stops here
MAX_INPUT_LENGTH = 3600.0


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


def move_input(show: Show, input_id: str, index: int) -> Show:
    """An input moved to another place on the Dashboard (a tile dragged, M14). index counts the tiles as they are
    now, the moved one included: it goes before the tile that was at index (at or past the end: last)."""
    order = [inp.id for inp in show.ordered_inputs()]
    if input_id not in order:
        return show
    old = order.index(input_id)
    order.remove(input_id)
    index = max(0, min(int(index), len(order) + 1))
    if old < index:
        index -= 1
    order.insert(min(index, len(order)), input_id)
    # back into the whole order, which may name inputs whose files couldn't be read: they stay where they were
    tiles, merged, seen = iter(order), [], set()
    for item in show.input_order:
        if item not in seen:
            seen.add(item)
            merged.append(next(tiles) if item in show.inputs else item)
    merged.extend(tiles)
    return show.with_inputs(show.inputs, tuple(merged))


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
            for group in SHAPE_GROUPS:
                for shape in _shape_list(element, group):
                    used.add(str(shape.get("id", "")))
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def default_element(kind: str, element_id: str, ndi_source: str = "Media Background Pane") -> dict:
    if kind == "colour":
        return {"id": element_id, "kind": "colour", "fill": {"type": "colour", "colour": "#86D6FF"}}
    if kind == "paint":
        return {"id": element_id, "kind": "paint"}  # (see-through until something's painted on it, M14)
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
                element: dict | None = None, start: float = 0.0) -> tuple[Show, str]:
    """Put a new element on a track. Infinite mode: it replaces what was there. Timed: it starts at `start`
    and lasts DEFAULT_ELEMENT_LENGTH seconds (less if it would run into the next element or the end)."""
    inp = show.inputs[input_id]
    element_id = new_element_id(inp)
    data = dict(element) if element is not None else default_element(kind, element_id, ndi_source)
    data["id"] = element_id
    tracks = _tracks(inp)
    if inp.infinite:
        for key in ("start", "end", "in", "out"):
            data.pop(key, None)
        tracks[track] = [data]
    else:
        start = min(max(0.0, float(start)), max(0.0, inp.length - MIN_ELEMENT_LENGTH))
        later = [s for s, _e in (element_span(e, inp) for e in tracks[track]) if s > start + 1e-9]
        end = min([start + DEFAULT_ELEMENT_LENGTH, inp.length] + later)
        if end - start < MIN_ELEMENT_LENGTH:
            end = min(inp.length, start + DEFAULT_ELEMENT_LENGTH)
        data["start"], data["end"] = round(start, 3), round(end, 3)
        tracks[track].append(data)
        tracks[track].sort(key=lambda e: element_span(e, inp)[0])
    return _set_tracks(show, inp, tracks), element_id


def background_track(inp: Input, element_id: str) -> tuple[int, list[int]] | None:
    """Split into Colour + Paint: room for the Colour element directly under the Paint element. Returns (the Colour
    element's track, the tracks' new order: order[i] is the track that ends up at i), or None if there's no room.
    The track just below is used when it's free (in a timed input: nothing else there at the element's times);
    otherwise the Paint element and the tracks above it move up into an empty track, or the tracks below it move
    down into one, so everything else keeps its order and looks the same."""
    found = find_element(inp, element_id)
    if found is None:
        return None
    track = found[0]
    start, end = element_span(inp.tracks[track][found[1]], inp)
    tracks = [list(inp.tracks[i]) if i < len(inp.tracks) else [] for i in range(TRACK_COUNT)]
    order = list(range(TRACK_COUNT))
    below = track - 1
    if below >= 0 and (not tracks[below] if inp.infinite else all(
            e_end <= start + 1e-9 or e_start >= end - 1e-9 for e_start, e_end in (element_span(e, inp) for e in tracks[below]))):
        return below, order
    for empty in range(track + 1, TRACK_COUNT):  # the Paint element and the tracks above it move up one
        if not tracks[empty]:
            return track, order[:track] + [empty] + order[track:empty] + order[empty + 1:]
    for empty in range(below - 1, -1, -1):  # or the tracks below it move down one
        if not tracks[empty]:
            return below, order[:empty] + order[empty + 1:track] + [empty] + order[track:]
    return None


def split_background(show: Show, input_id: str, element_id: str) -> tuple[Show, str]:
    """An older Paint element's fill (its Background) moved to a Colour element of its own, directly under it, with
    the same opacity, blend, move / size, mask and timing; the Paint element keeps its painting and shapes. Returns
    (show, the Colour element's id); the show unchanged and "" if there's no room (see background_track)."""
    inp = show.inputs[input_id]
    found = find_element(inp, element_id)
    if found is None:
        return show, ""
    element = thaw(inp.tracks[found[0]][found[1]])
    room = background_track(inp, element_id)
    if element.get("kind") != "paint" or not isinstance(element.get("fill"), dict) or room is None:
        return show, ""
    colour_track, order = room
    colour_id = new_element_id(inp)
    colour = {"id": colour_id, "kind": "colour", "fill": element["fill"]}
    for key in ("opacity", "blend", "transform", "mask", "start", "end", "in", "out"):
        if key in element:
            colour[key] = element[key]
    tracks = _tracks(inp)
    tracks[found[0]][found[1]] = {k: v for k, v in element.items() if k != "fill"}
    tracks = [tracks[i] for i in order]
    tracks[colour_track].append(colour)
    tracks[colour_track].sort(key=lambda e: element_span(e, inp)[0])
    return _set_tracks(show, inp, tracks), colour_id


def split_changes(element: Any, inp: Input | None = None) -> list[str]:
    """What Split into Colour + Paint would change on the lights ([]: nothing, it looks exactly the same). The two
    parts are then see-through and blended one after the other, so they match exactly only at full opacity, with
    Normal blend, and with a mask that's either on or off at each light. inp: the input it's in (in a timed one, it may
    cross the element next to it on its track, which the Colour element on its own track wouldn't)."""
    if not (element.get("shapes") or element.get("strokes")):
        return []  # (nothing painted over the Background)
    changes = []
    opacity = element.get("opacity", 1.0)
    if hasattr(opacity, "get"):
        full = not opacity.get("expr") and not is_keyframed(opacity) and _number(opacity.get("value", 1.0)) >= 1.0
    else:
        full = _number(opacity) >= 1.0
    if not full:
        changes.append("it isn't always at full opacity, so a little of the Background would show through the painting")
    elif element.get("in") or element.get("out"):
        changes.append("while it comes in or goes out, a little of the Background would show through the painting")
    blend = element.get("blend", "normal")
    if blend in ("add", "multiply", "screen"):
        changes.append(f"it blends as {blend.title()}, so where the painting covers the Background the two would "
                       "blend into each other rather than the painting hiding it")
    mask = element.get("mask")
    if hasattr(mask, "get") and (mask.get("paint") or mask.get("shapes")):
        changes.append("where its mask fades softly, the painting would look a little different")
    if inp is not None and not inp.infinite and _crosses(inp, element):
        changes.append("while it crosses the element next to it on its track, the Background would stay full and then "
                       "stop, rather than crossing with it")
    if _uses_random(element.get("fill")):
        changes.append("the Background's formula uses random(), which picks again for the new Colour element")
    return changes


def _crosses(inp: Input, element: Any) -> bool:
    """Does another element on its track overlap it in time (a crossing between them)?"""
    found = find_element(inp, str(element.get("id")))
    if found is None:
        return False
    start, end = element_span(element, inp)
    return any(other is not found[2] and s < end - 1e-9 and e > start + 1e-9
               for other, (s, e) in ((o, element_span(o, inp)) for o in inp.tracks[found[0]]))


def _uses_random(value: Any) -> bool:
    if isinstance(value, str):
        return "random(" in value.replace(" ", "")
    if hasattr(value, "values"):
        return any(_uses_random(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_uses_random(v) for v in value)
    return False


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0  # (a value that isn't a number counts as the default, as it does on the lights)


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
        tracks[to_track].sort(key=lambda e: element_span(e, inp)[0])
    return _set_tracks(show, inp, tracks)


# ---------------------------------------------------------------- timed inputs


def set_timed(show: Show, input_id: str, timed: bool, keep: dict[int, str] | None = None) -> Show:
    """Switch between infinite (each track: one element, playing until the input is replaced) and timed (a
    length, with elements placed in time). Going infinite keeps one element per track: keep[track] (an
    element id), otherwise the first; their times and in / out transitions go. Going timed, each element
    covers the whole input."""
    inp = show.inputs[input_id]
    if timed == (not inp.infinite):
        return show
    tracks = _tracks(inp)
    if timed:
        length = inp.length if inp.length > 0 else 10.0
        tracks = [[{**e, "start": 0.0, "end": round(length, 3)} for e in track] for track in tracks]
        return _set_tracks(show, replace(inp, infinite=False, length=length), tracks)
    kept = []
    for index, track in enumerate(tracks):
        wanted = (keep or {}).get(index)
        chosen = next((e for e in track if e.get("id") == wanted), track[0] if track else None)
        kept.append([{k: v for k, v in chosen.items() if k not in ("start", "end", "in", "out")}] if chosen else [])
    return _set_tracks(show, replace(inp, infinite=True), kept)


def crowded_tracks(inp: Input) -> dict[int, list[Any]]:
    """Tracks with more than one element (going infinite has to keep one of each): {track: elements}."""
    return {i: list(track) for i, track in enumerate(inp.tracks) if len(track) > 1}


def set_length(show: Show, input_id: str, length: float, end: str | None = None) -> Show:
    """A timed input's length (seconds) and what happens at the end ("hold", "loop" or "stop"). Elements past
    the new end are trimmed to it (an element that would start after it is left alone: it just won't play)."""
    inp = show.inputs[input_id]
    length = min(MAX_INPUT_LENGTH, max(MIN_ELEMENT_LENGTH, round(float(length), 3)))
    changes: dict[str, Any] = {"length": length}
    if end in END_BEHAVIOURS:
        changes["end"] = end
    return _replace_input(show, replace(inp, **changes))


def _with_element(show: Show, input_id: str, element_id: str, change) -> Show:
    inp = show.inputs[input_id]
    found = find_element(inp, element_id)
    if found is None:
        return show
    tracks = _tracks(inp)
    track, position, element = found
    tracks[track][position] = change(thaw(element), inp)
    tracks[track].sort(key=lambda e: element_span(e, inp)[0])
    return _set_tracks(show, inp, tracks)


def move_element_to(show: Show, input_id: str, element_id: str, start: float) -> Show:
    """Move an element in time, keeping its length (its keyframes move with it)."""
    def change(element, inp):
        old_start, old_end = element_span(element, inp)
        length = old_end - old_start
        new_start = min(max(0.0, float(start)), max(0.0, inp.length - MIN_ELEMENT_LENGTH))
        element["start"], element["end"] = round(new_start, 3), round(new_start + length, 3)
        return element
    return _with_element(show, input_id, element_id, change)


def trim_element(show: Show, input_id: str, element_id: str, start: float | None = None, end: float | None = None) -> Show:
    """Change where an element starts or ends. Trimming its start leaves its keyframes where they were in the
    input (so the look doesn't jump); at least MIN_ELEMENT_LENGTH is left."""
    def change(element, inp):
        old_start, old_end = element_span(element, inp)
        new_start = old_start if start is None else min(max(0.0, float(start)), old_end - MIN_ELEMENT_LENGTH)
        new_end = old_end if end is None else max(float(end), new_start + MIN_ELEMENT_LENGTH)
        element["start"], element["end"] = round(new_start, 3), round(new_end, 3)
        offset = old_start - new_start
        if offset < -1e-9:  # the start moved later, maybe past keys: keep the look there (as Split does)
            element = _map_params(element, lambda param: _keep_keys(param, -offset, before=False))
        elif offset > 1e-9:  # earlier: the keys just move with it
            element = _map_params(element, lambda param: keys_shifted(param, offset))
        return element
    return _with_element(show, input_id, element_id, change)


def split_element(show: Show, input_id: str, element_id: str, at: float) -> tuple[Show, str]:
    """Cut an element in two at input time `at`. The second part gets a new id; each part keeps the look it
    had (keyframed settings get a key at the cut), the first keeps the in transition, the second the out."""
    inp = show.inputs[input_id]
    found = find_element(inp, element_id)
    if found is None or inp.infinite:
        return show, ""
    track, position, element = found
    start, end = element_span(element, inp)
    if not start + MIN_ELEMENT_LENGTH <= at <= end - MIN_ELEMENT_LENGTH:
        return show, ""
    cut = at - start  # in element time
    first = _map_params(thaw(element), lambda param: _keep_keys(param, cut, before=True))
    second = _map_params(thaw(element), lambda param: _keep_keys(param, cut, before=False))
    first["end"] = round(at, 3)
    first.pop("out", None)
    second_id = new_element_id(inp)
    second.update({"id": second_id, "start": round(at, 3), "end": round(end, 3)})
    second.pop("in", None)
    tracks = _tracks(inp)
    tracks[track][position] = first
    tracks[track].insert(position + 1, second)
    return _set_tracks(show, inp, tracks), second_id


def _keep_keys(param: Any, cut: float, before: bool) -> Any:
    """One side of a split: its keys, plus a key at the cut with the value there; the second side's keys move
    so the cut becomes its time 0."""
    if not is_keyframed(param):
        return param
    keys = keys_of(param)
    kind = "colour" if isinstance(keys[0][1], str) else "number"
    value = base_value(param, cut, (1.0, 1.0, 1.0) if kind == "colour" else 0.0, kind)
    value = to_hex(value) if kind == "colour" else round(float(value), 4)
    param = with_key(param, cut, value)
    if before:
        return keys_shifted(param, 0.0, keep=lambda kt: kt <= cut + KEY_TOLERANCE)
    return keys_shifted(param, -cut, keep=lambda kt: kt >= cut - KEY_TOLERANCE)


def _map_params(element: dict, change) -> dict:
    """Apply change() to every keyframed setting of an element (shapes' settings too)."""
    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            if "keys" in value:
                return change(value)
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value
    return {k: (walk(v) if k not in ("id", "kind", "start", "end", "in", "out") else v) for k, v in element.items()}


def set_element_transition(show: Show, input_id: str, element_id: str, which: str, kind: str | None = None,
                           duration: float | None = None) -> Show:
    """An element's "in" or "out" transition in a timed input (kind "cut", or duration 0, removes it)."""
    if which not in ("in", "out"):
        return show
    def change(element, inp):
        current = dict(element.get(which) or {"kind": "fade", "duration": 1.0})
        if kind is not None:
            current["kind"] = kind
        if duration is not None:
            current["duration"] = round(max(0.0, float(duration)), 3)
        if current.get("kind") == "cut" or current.get("duration", 0) <= 0:
            element.pop(which, None)
        else:
            element[which] = current
        return element
    return _with_element(show, input_id, element_id, change)


# ---------------------------------------------------------------- keyframes


def param_path(shape_id: str, path: str, group: str = "shapes") -> str:
    """Where a setting lives in an element: "opacity", "fill.colour", a shape's "shapes:S1:x", or a mask shape's
    "mask.shapes:M1:x"."""
    return f"{group}:{shape_id}:{path}" if shape_id else path


def _shape_path(where: str) -> tuple[str, str, str] | None:
    """(the shapes' list, shape id, setting) for a shape's param_path(); None for an element's own setting."""
    group, colon, rest = where.partition(":")
    if not colon or group not in SHAPE_GROUPS or ":" not in rest:
        return None
    shape_id, path = rest.split(":", 1)
    return group, shape_id, path


def get_param(element: Any, where: str) -> Any:
    """The raw setting (a value, keyframes or an expression) at a param_path()."""
    from .schema import get_path

    found = _shape_path(where)
    if found is not None:
        group, shape_id, path = found
        shape = next((s for s in _shape_list(element, group) if s.get("id") == shape_id), None)
        return get_path(shape, path) if shape is not None else None
    return get_path(element, where)


def set_param(show: Show, input_id: str, element_id: str, where: str, raw: Any) -> Show:
    found = _shape_path(where)
    if found is not None:
        group, shape_id, path = found
        return set_shape_value(show, input_id, element_id, shape_id, path, raw, group)
    return set_element_value(show, input_id, element_id, where, raw)


def _element_param(show: Show, input_id: str, element_id: str, where: str) -> Any:
    found = find_element(show.inputs[input_id], element_id)
    return get_param(found[2], where) if found is not None else None


def set_key(show: Show, input_id: str, element_id: str, where: str, t: float, value: Any) -> Show:
    """A keyframe at element time t (added, or the one there changed)."""
    param = thaw(_element_param(show, input_id, element_id, where))
    return set_param(show, input_id, element_id, where, with_key(param, t, value))


def remove_key(show: Show, input_id: str, element_id: str, where: str, t: float) -> Show:
    param = thaw(_element_param(show, input_id, element_id, where))
    return set_param(show, input_id, element_id, where, without_key(param, t))


def move_key(show: Show, input_id: str, element_id: str, where: str, t_from: float, t_to: float) -> Show:
    param = thaw(_element_param(show, input_id, element_id, where))
    return set_param(show, input_id, element_id, where, with_key_moved(param, t_from, t_to))


def set_key_interpolation(show: Show, input_id: str, element_id: str, where: str, t: float, interp: str) -> Show:
    if interp not in INTERPOLATIONS:
        return show
    param = thaw(_element_param(show, input_id, element_id, where))
    return set_param(show, input_id, element_id, where, with_key_interpolation(param, t, interp))


def element_keys(element: Any) -> dict[str, list[tuple[float, Any, str]]]:
    """Every keyframed setting of an element (shapes' too), by param_path(): for the timeline's diamonds."""
    found: dict[str, list[tuple[float, Any, str]]] = {}

    def walk(value: Any, path: str) -> None:
        if hasattr(value, "get") and "keys" in value:
            found[path] = keys_of(value)
            return
        if hasattr(value, "items"):
            for k, v in value.items():
                if k not in ("id", "kind", "start", "end", "in", "out", "shapes"):
                    walk(v, f"{path}.{k}" if path else k)

    walk(element, "")
    for group in SHAPE_GROUPS:  # (a Paint element's shapes, and its mask's)
        for shape in _shape_list(element, group):
            for k, v in shape.items():
                if hasattr(v, "get") and "keys" in v:
                    found[param_path(str(shape.get("id")), k, group)] = keys_of(v)
    return found


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

NEW_SHAPE_SOFTNESS = 0.2  # a new shape's edge is a little soft, so a moving bar fades through a light (lights are points)
SHAPE_GROUPS = ("shapes", "mask.shapes")  # a Paint element's shapes, and any element's mask shapes (M14)


def _shape_list(element: Any, group: str) -> list:
    """The shapes in an element's list ("shapes", or its mask's "mask.shapes"), as they are."""
    from .schema import get_path

    shapes = get_path(element, group, ()) if element is not None else ()
    return [s for s in shapes if hasattr(s, "get")] if isinstance(shapes, (list, tuple)) else []


def new_mask_shape_mode(element: Any) -> str:
    """What a new mask shape does: something you can see. It hides the element, on a mask that starts shown; it shows
    it, on a mask that starts hidden everywhere."""
    mask = element.get("mask") if element is not None else None
    return "show" if hasattr(mask, "get") and mask.get("start") == "hidden" else "hide"


def add_shape(show: Show, input_id: str, element_id: str, shape_type: str = "rect",
              x: float = 0.5, y: float = 0.5, w: float = 0.3, h: float = 0.4, colour: str = "#FFFFFF",
              group: str = "shapes", mode: str | None = None) -> tuple[Show, str]:
    """A new shape, last in its list (on top): a Paint element's ("shapes", ids S1, S2 …), or a mask's ("mask.shapes",
    ids M1, M2 …, showing or hiding the element: see new_mask_shape_mode)."""
    inp = show.inputs[input_id]
    found = find_element(inp, element_id)
    if found is None:
        return show, ""
    mask = group == "mask.shapes"
    shape_id = new_element_id(inp, "M" if mask else "S")
    shapes = thaw(_shape_list(found[2], group))
    shape = {"id": shape_id, "type": shape_type}
    if mask:
        shape["mode"] = mode if mode in ("show", "hide") else new_mask_shape_mode(found[2])
    shape.update({"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4), "softness": NEW_SHAPE_SOFTNESS})
    if not mask:
        shape["colour"] = colour
    shapes.append(shape)
    return set_element_value(show, input_id, element_id, group, shapes), shape_id


def _shapes(show: Show, input_id: str, element_id: str, group: str = "shapes") -> list[dict]:
    found = find_element(show.inputs[input_id], element_id)
    if found is None:
        return []
    return thaw(_shape_list(found[2], group))


def set_shape_value(show: Show, input_id: str, element_id: str, shape_id: str, path: str, value: Any,
                    group: str = "shapes") -> Show:
    shapes = _shapes(show, input_id, element_id, group)
    for i, shape in enumerate(shapes):
        if shape.get("id") == shape_id:
            shapes[i] = thaw(set_path(shape, path, value))
            return set_element_value(show, input_id, element_id, group, shapes)
    return show


def set_shape_values(show: Show, input_id: str, element_id: str, shape_id: str, values: dict[str, Any],
                     group: str = "shapes") -> Show:
    """Several settings of one shape at once (a drag moves x and y together)."""
    shapes = _shapes(show, input_id, element_id, group)
    for i, shape in enumerate(shapes):
        if shape.get("id") == shape_id:
            for path, value in values.items():
                shape = thaw(set_path(shape, path, round(value, 4) if isinstance(value, float) else value))
            shapes[i] = shape
            return set_element_value(show, input_id, element_id, group, shapes)
    return show


def set_shape_values_at(show: Show, input_id: str, element_id: str, shape_id: str, values: dict[str, Any], t: float,
                        group: str = "shapes") -> Show:
    """A drag in the Selected view: settings that have keyframes get a key at element time t; the others
    change (keeping an expression, whose `value` they are)."""
    shapes = _shapes(show, input_id, element_id, group)
    for i, shape in enumerate(shapes):
        if shape.get("id") == shape_id:
            for path, value in values.items():
                value = round(value, 4) if isinstance(value, float) else value
                raw = shape.get(path)
                shape = thaw(set_path(shape, path, with_key(raw, t, value) if is_keyframed(raw) else with_base_value(raw, value)))
            shapes[i] = shape
            return set_element_value(show, input_id, element_id, group, shapes)
    return show


def set_element_values_at(show: Show, input_id: str, element_id: str, values: dict[str, Any], t: float) -> Show:
    """Several settings of an element at once (the transform gizmo): keyframed ones get a key at element time
    t, the others change (keeping an expression, whose `value` they are)."""
    from .schema import get_path

    for path, value in values.items():
        found = find_element(show.inputs[input_id], element_id)
        if found is None:
            return show
        raw = thaw(get_path(found[2], path, None))
        value = round(value, 4) if isinstance(value, float) else value
        show = set_element_value(show, input_id, element_id, path, with_key(raw, t, value) if is_keyframed(raw) else with_base_value(raw, value))
    return show


def remove_shape(show: Show, input_id: str, element_id: str, shape_id: str, group: str = "shapes") -> Show:
    shapes = [s for s in _shapes(show, input_id, element_id, group) if s.get("id") != shape_id]
    show = set_element_value(show, input_id, element_id, group, shapes or None)
    found = find_element(show.inputs[input_id], element_id)
    if group == "mask.shapes" and found is not None and found[2].get("mask") == {}:
        show = set_element_value(show, input_id, element_id, "mask", None)  # (nothing left in the mask)
    return show


def move_shape(show: Show, input_id: str, element_id: str, shape_id: str, step: int, group: str = "shapes") -> Show:
    """Move a shape up (+1, drawn later, on top) or down (-1) in its element's list (or its mask's)."""
    shapes = _shapes(show, input_id, element_id, group)
    index = next((i for i, s in enumerate(shapes) if s.get("id") == shape_id), None)
    if index is None or not 0 <= index + step < len(shapes):
        return show
    shapes[index], shapes[index + step] = shapes[index + step], shapes[index]
    return set_element_value(show, input_id, element_id, group, shapes)


# ---------------------------------------------------------------- media


def media_stem(stem: str) -> str:
    """The name a copied picture or video gets in the show's media folder (without its extension): plain letters,
    digits and spaces, and never a name Windows keeps for a device ("Aux 🎄" would be "Aux": COM1 on this PC)."""
    stem = re.sub(r"[^A-Za-z0-9 _.-]+", "", stem).strip(" .") or "media"
    return f"{stem} media" if is_device_name(stem) else stem


def copy_media(show_folder: str, source: Path) -> str:
    """Copy a picture or video into the show's media folder. Returns its path relative to the show."""
    source = Path(source)
    target_dir = Path(show_folder) / MEDIA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = media_stem(source.stem)
    target = target_dir / f"{stem}{source.suffix.lower()}"
    n = 2
    while target.exists():
        if target.stat().st_size == source.stat().st_size and target.read_bytes() == source.read_bytes():
            return f"{MEDIA_DIR}/{target.name}"  # the same file is already there
        target = target_dir / f"{stem} {n}{source.suffix.lower()}"
        n += 1
    shutil.copy2(source, target)
    return f"{MEDIA_DIR}/{target.name}"
