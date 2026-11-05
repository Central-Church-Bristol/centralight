"""Changes to a show, as plain functions: each takes a Show and returns a new one.

The window turns each of these into an undo step; tests call them directly. Anything that
moves, adds or removes colour lights keeps the active saved layout in step (layouts.py).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .fixtures import PROFILES

from .layouts import forget_lights, sync_active_layout
from .model import (
    ROLE_COLOUR,
    ROLE_HOUSE,
    SAVED_COLOURS_MAX,
    Fixture,
    GlobalVar,
    HouseLight,
    LightGroup,
    MapSpot,
    Show,
    safe_text,
    slugify,
)

DEFAULT_COLOUR_PROFILE = "slimpar9"
PROFILE_LABELS = {
    "slimpar9": "SlimPAR Q12 (9 channels)",
    "dimmer": "Dimmer (1 channel)",
}


def profile_label(profile: str) -> str:
    return PROFILE_LABELS.get(profile, f"{profile} ({PROFILES[profile]['channels']} ch)")


def _replace_fixture(show: Show, fixture_id: str, **changes: Any) -> Show:
    fixtures = tuple(replace(fx, **changes) if fx.id == fixture_id else fx for fx in show.fixtures)
    return replace(show, fixtures=fixtures)


def move_lights(show: Show, spots: dict[str, MapSpot]) -> Show:
    """New map spots for some colour lights (x, y clamped to the map)."""
    fixtures = []
    for fx in show.fixtures:
        spot = spots.get(fx.id)
        if spot is not None and fx.role == ROLE_COLOUR:
            spot = MapSpot(min(1.0, max(0.0, spot.x)), min(1.0, max(0.0, spot.y)))
            fx = replace(fx, map=spot)
        fixtures.append(fx)
    return sync_active_layout(replace(show, fixtures=tuple(fixtures)))


def rename_light(show: Show, fixture_id: str, name: str) -> Show:
    name = name.strip()
    if not name:
        return show
    fx = show.fixture(fixture_id)
    if fx is None:
        return show
    show = _replace_fixture(show, fixture_id, name=name)
    if fx.role == ROLE_HOUSE:  # the house label follows the name
        house = tuple(HouseLight(h.fixture, name) if h.fixture == fixture_id else h for h in show.house)
        show = replace(show, house=house)
    return show


def set_group(show: Show, fixture_id: str, group: str) -> Show:
    return _replace_fixture(show, fixture_id, group=group.strip())


def set_address(show: Show, fixture_id: str, address: int) -> Show:
    fx = show.fixture(fixture_id)
    if fx is None:
        return show
    highest = 512 - fx.channels + 1
    return _replace_fixture(show, fixture_id, address=max(1, min(highest, int(address))))


def set_profile(show: Show, fixture_id: str, profile: str) -> Show:
    fx = show.fixture(fixture_id)
    if fx is None or profile not in PROFILES:
        return show
    highest = 512 - PROFILES[profile]["channels"] + 1
    return _replace_fixture(show, fixture_id, profile=profile, address=min(fx.address, highest))


def next_free_address(show: Show, channels: int) -> int:
    """The first start channel after which `channels` channels are all unused (or 1)."""
    used = set()
    for fx in show.fixtures:
        used.update(range(fx.address, fx.last_channel + 1))
    for start in range(1, 512 - channels + 2):
        if not any(ch in used for ch in range(start, start + channels)):
            # prefer the gap after the last light, so new lights go at the end
            if start > max(used, default=0):
                return start
    for start in range(1, 512 - channels + 2):
        if not any(ch in used for ch in range(start, start + channels)):
            return start
    return 1


def unique_id(show: Show, base: str) -> str:
    taken = {fx.id for fx in show.fixtures}
    candidate = slugify(base, "light")
    n = 2
    while candidate in taken:
        candidate = f"{slugify(base, 'light')}-{n}"
        n += 1
    return candidate


def add_colour_light(show: Show, x: float = 0.5, y: float = 0.5) -> tuple[Show, str]:
    """A new SlimPAR after the last used channel, in the middle of the map. Returns (show, its id)."""
    numbers = [int(m.group(1)) for fx in show.fixtures if (m := re.match(r"SlimPAR (\d+)$", fx.name))]
    name = f"SlimPAR {max(numbers, default=100) + 1}"
    channels = PROFILES[DEFAULT_COLOUR_PROFILE]["channels"]
    fixture = Fixture(
        id=unique_id(show, name),
        name=name,
        profile=DEFAULT_COLOUR_PROFILE,
        address=next_free_address(show, channels),
        role=ROLE_COLOUR,
        map=MapSpot(x, y),
    )
    return sync_active_layout(replace(show, fixtures=show.fixtures + (fixture,))), fixture.id


def remove_lights(show: Show, fixture_ids: set[str]) -> Show:
    fixtures = tuple(fx for fx in show.fixtures if fx.id not in fixture_ids)
    house = tuple(h for h in show.house if h.fixture not in fixture_ids)
    groups = tuple(LightGroup(g.name, kept) for g in show.groups  # (a saved group loses them; an empty one goes)
                   if (kept := tuple(light for light in g.lights if light not in fixture_ids)))
    return sync_active_layout(forget_lights(replace(show, fixtures=fixtures, house=house, groups=groups), fixture_ids))


# ---------------------------------------------------------------- the Console's groups and colours (M15)


def save_group(show: Show, name: str, lights: list[str]) -> Show:
    """Save the lights as a group with this name (one with the same name, in any case, is replaced where it was).
    Only colour lights; with none (or no name), nothing changes."""
    name = safe_text(" ".join(str(name).split())[:40])
    colour = {fx.id for fx in show.colour_fixtures()}
    kept = tuple(dict.fromkeys(light for light in lights if light in colour))
    if not name or not kept:
        return show
    groups = list(show.groups)
    at = next((i for i, g in enumerate(groups) if g.name.casefold() == name.casefold()), None)
    if at is None:
        groups.append(LightGroup(name, kept))
    else:
        groups[at] = LightGroup(name, kept)
    return replace(show, groups=tuple(groups))


def remove_group(show: Show, name: str) -> Show:
    return replace(show, groups=tuple(g for g in show.groups if g.name != name))


def add_saved_colour(show: Show, colour: str) -> Show:
    """Save a colour ("#RRGGBB") with the show, at the end (one already there moves to the end; past
    SAVED_COLOURS_MAX the oldest goes)."""
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(colour)):
        return show
    colour = str(colour).upper()
    colours = [c for c in show.saved_colours if c != colour] + [colour]
    return replace(show, saved_colours=tuple(colours[-SAVED_COLOURS_MAX:]))


def remove_saved_colour(show: Show, colour: str) -> Show:
    return replace(show, saved_colours=tuple(c for c in show.saved_colours if c != str(colour).upper()))


def set_identify_seed(show: Show, seed: int) -> Show:
    return replace(show, identify_seed=int(seed))


def channel_conflicts(show: Show) -> dict[str, list[str]]:
    """fixture id -> descriptions of the lights whose channels overlap it."""
    conflicts: dict[str, list[str]] = {}
    fixtures = list(show.fixtures)
    for i, a in enumerate(fixtures):
        for b in fixtures[i + 1:]:
            if a.address <= b.last_channel and b.address <= a.last_channel:
                conflicts.setdefault(a.id, []).append(f"{b.name} (ch {b.channel_range()})")
                conflicts.setdefault(b.id, []).append(f"{a.name} (ch {a.channel_range()})")
    return conflicts


# ---------------------------------------------------------------- global variables

GLOBAL_DEFAULTS = {"number": 0.5, "colour": "#86D6FF", "switch": False}


def unique_variable_name(taken: set[str], base: str) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}{n}"
        n += 1
    return candidate


def add_global(show: Show, kind: str = "number") -> tuple[Show, str]:
    """A new global variable (named global1, global2...). Returns (show, its name)."""
    kind = kind if kind in GLOBAL_DEFAULTS else "number"
    name = unique_variable_name({g.name for g in show.globals}, "global")
    var = GlobalVar(name=name, type=kind, default=GLOBAL_DEFAULTS[kind])
    return replace(show, globals=show.globals + (var,)), name


def update_global(show: Show, name: str, **changes: Any) -> Show:
    """Change a global's type, default, range, "show on dashboard" or what happens when the show loads ("keep" its last
    value, or "reset" to its starting value). A new type resets the default."""
    found = next((g for g in show.globals if g.name == name), None)
    if found is None:
        return show
    var = replace(found, **{k: v for k, v in changes.items() if k in ("type", "default", "min", "max", "dashboard", "on_load")})
    if var.type not in GLOBAL_DEFAULTS or var.on_load not in ("keep", "reset"):
        return show
    if var.type != found.type:
        var = replace(var, default=GLOBAL_DEFAULTS[var.type])
    if var.type == "number":
        low, high = float(var.min), float(var.max)
        if high <= low:
            high = low + 1.0
        default = var.default if isinstance(var.default, (int, float)) and not isinstance(var.default, bool) else low
        var = replace(var, min=low, max=high, default=min(high, max(low, float(default))))
    elif var.type == "switch":
        var = replace(var, default=bool(var.default))
    return replace(show, globals=tuple(var if g.name == name else g for g in show.globals))


def rename_global(show: Show, name: str, new_name: str) -> Show:
    """Rename a global (expressions that use the old name need changing by hand)."""
    if new_name == name or any(g.name == new_name for g in show.globals):
        return show
    return replace(show, globals=tuple(replace(g, name=new_name) if g.name == name else g for g in show.globals))


def remove_global(show: Show, name: str) -> Show:
    return replace(show, globals=tuple(g for g in show.globals if g.name != name))
