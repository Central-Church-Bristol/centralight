"""Saved layouts: named arrangements of the map that you can switch between.

A layout holds where each colour light sits (and how big its patch is) plus the room photo
behind the map and how strongly it shows. The patch (addresses, types) isn't part of a layout.

Each fixture's map is the working copy of the active layout's positions, so anything that
moves, adds or removes lights calls sync_active_layout() to keep the two the same. Like
show_edit, every function takes a Show and returns a new one.
"""

from __future__ import annotations

from dataclasses import replace

from .model import ID_PATTERN, ROLE_COLOUR, Layout, MapSpot, Show, frozen_map, slugify

MAIN_ID = "main"
MAIN_NAME = "Main"
DEFAULT_BACKDROP_OPACITY = 0.55


def current_positions(show: Show) -> dict[str, MapSpot]:
    """Where each colour light is right now (from the fixtures)."""
    return {fx.id: fx.map for fx in show.fixtures if fx.role == ROLE_COLOUR and fx.map is not None}


def sorted_layouts(show: Show) -> list[Layout]:
    """Layouts in the order the dropdown lists them: by name."""
    return sorted(show.layouts, key=lambda layout: (layout.name.casefold(), layout.id))


def _unique_id(show: Show, name: str) -> str:
    taken = {layout.id for layout in show.layouts}
    base = slugify(name, "layout")
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def unique_name(show: Show, name: str, ignore: str = "") -> str:
    """name, or "name 2", "name 3"... if another layout already has it."""
    name = " ".join(name.split()) or MAIN_NAME
    taken = {layout.name.casefold() for layout in show.layouts if layout.id != ignore}
    candidate, n = name, 2
    while candidate.casefold() in taken:
        candidate = f"{name} {n}"
        n += 1
    return candidate


def _replace_layout(show: Show, layout_id: str, **changes) -> Show:
    return replace(show, layouts=tuple(replace(lay, **changes) if lay.id == layout_id else lay for lay in show.layouts))


def sync_active_layout(show: Show) -> Show:
    """Copy the lights' current positions into the active layout."""
    active = show.layout()
    if active is None:
        return show
    positions = current_positions(show)
    if dict(active.positions) == positions:
        return show
    return _replace_layout(show, active.id, positions=frozen_map(positions))


def ensure_layouts(show: Show, backdrop: str = "") -> tuple[Show, list[str]]:
    """Make sure the show has an active layout (old shows and new ones get "Main", made from the
    lights' current positions). The fixtures' positions always win, as they're the working copy.
    Returns (show, warnings)."""
    warnings: list[str] = []
    if show.layout() is None:
        if show.layouts:
            warnings.append("The layout that was in use couldn't be found, so the lights' positions were kept as a new layout.")
        name = MAIN_NAME if not any(lay.name.casefold() == MAIN_NAME.casefold() for lay in show.layouts) else f"{MAIN_NAME} (recovered)"
        if ID_PATTERN.match(show.active_layout):
            layout_id = show.active_layout  # the id show.json names, so they match next time
        else:
            layout_id = MAIN_ID if show.layout(MAIN_ID) is None else _unique_id(show, name)
        layout = Layout(layout_id, name, frozen_map(current_positions(show)), backdrop, DEFAULT_BACKDROP_OPACITY)
        show = replace(show, layouts=show.layouts + (layout,), active_layout=layout_id)
    return sync_active_layout(show), warnings


def switch_layout(show: Show, layout_id: str) -> Show:
    """Use another saved layout: the lights move to its positions straight away. Lights the
    layout doesn't know about (added since it was saved) stay where they are."""
    target = show.layout(layout_id)
    if target is None or layout_id == show.active_layout:
        return show
    show = sync_active_layout(show)
    fixtures = tuple(
        replace(fx, map=target.positions[fx.id]) if fx.role == ROLE_COLOUR and fx.id in target.positions else fx
        for fx in show.fixtures
    )
    return sync_active_layout(replace(show, fixtures=fixtures, active_layout=layout_id))


def save_layout_as(show: Show, name: str) -> tuple[Show, str]:
    """A new layout from the current positions and photo; it becomes the active one. Returns (show, its id)."""
    show = sync_active_layout(show)
    name = unique_name(show, name)
    active = show.layout()
    layout = Layout(
        id=_unique_id(show, name),
        name=name,
        positions=frozen_map(current_positions(show)),
        backdrop=active.backdrop if active else "",
        backdrop_opacity=active.backdrop_opacity if active else DEFAULT_BACKDROP_OPACITY,
    )
    return replace(show, layouts=show.layouts + (layout,), active_layout=layout.id), layout.id


def rename_layout(show: Show, layout_id: str, name: str) -> Show:
    if show.layout(layout_id) is None or not name.strip():
        return show
    return _replace_layout(show, layout_id, name=unique_name(show, name, ignore=layout_id))


def delete_layout(show: Show, layout_id: str) -> Show:
    """Delete a layout. The last one can't go. Deleting the active one switches to the one
    listed before it (or after it, if it was first)."""
    if show.layout(layout_id) is None or len(show.layouts) <= 1:
        return show
    if layout_id == show.active_layout:
        ordered = sorted_layouts(show)
        index = next(i for i, lay in enumerate(ordered) if lay.id == layout_id)
        neighbour = ordered[index - 1] if index > 0 else ordered[index + 1]
        show = switch_layout(show, neighbour.id)
    return replace(show, layouts=tuple(lay for lay in show.layouts if lay.id != layout_id))


def set_backdrop(show: Show, file: str) -> Show:
    """The room photo behind the map for the active layout ("" = none)."""
    active = show.layout()
    if active is None or active.backdrop == file:
        return show
    return _replace_layout(show, active.id, backdrop=file)


def set_backdrop_opacity(show: Show, opacity: float) -> Show:
    active = show.layout()
    opacity = round(min(1.0, max(0.0, float(opacity))), 3)
    if active is None or abs(active.backdrop_opacity - opacity) < 1e-9:
        return show
    return _replace_layout(show, active.id, backdrop_opacity=opacity)


def forget_lights(show: Show, fixture_ids: set[str]) -> Show:
    """Removed lights leave every layout too."""
    layouts = []
    for lay in show.layouts:
        if any(fid in lay.positions for fid in fixture_ids):
            lay = replace(lay, positions=frozen_map({k: v for k, v in lay.positions.items() if k not in fixture_ids}))
        layouts.append(lay)
    return replace(show, layouts=tuple(layouts))
