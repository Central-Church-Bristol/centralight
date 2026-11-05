"""A new show made from fixtures.PATCH (the patch checked in the room on 2 Nov 2026).

The SlimPARs become colour lights on the map, spread out by group so they don't
overlap; drag them to where they really are on the Layout page. The two dimmers
become the Festoon and Key lights house controls.
"""

from __future__ import annotations

from pathlib import Path

from .fixtures import PATCH, PROFILES

from .layouts import ensure_layouts
from .model import ROLE_COLOUR, ROLE_HOUSE, Fixture, HouseLight, Input, MapSpot, Show, slugify
from .show_io import SHOW_FILE, ShowError, freeze, load_show, save_input, save_show_file, save_whole_show

FOLLOW_SCREEN_ID = "follow-screen"


def follow_screen_input() -> Input:
    """The starter input: every light follows Media Background Pane's picture."""
    tracks = freeze([[{"id": "E1", "kind": "ndi", "source": "Media Background Pane"}], [], [], []])
    return Input(id=FOLLOW_SCREEN_ID, name="Follow screen", tracks=tracks)


def add_starter_inputs(show: Show) -> Show:
    """A show with no inputs at all gets "Follow screen" (saved to its inputs folder)."""
    if show.inputs or show.read_only or not show.folder:
        return show
    inp = follow_screen_input()
    save_input(Path(show.folder), inp)
    order = (inp.id, *[i for i in show.input_order if i != inp.id])
    show = show.with_inputs({inp.id: inp}, order)
    save_show_file(show)
    return show

# House-light labels by MagicQ group
HOUSE_LABELS = {"Festoon": "Festoon", "Key Lights": "Key lights"}

# Starting spots (x, y from 0 to 1) for each group, used in patch order
GROUP_SPOTS = {
    "Top Wash": [(0.2, 0.2), (0.4, 0.2), (0.6, 0.2), (0.8, 0.2)],
    "Side Fill": [(0.07, 0.62), (0.93, 0.62)],
    "Pillars": [(0.3, 0.75), (0.7, 0.75)],
}


def fixture_id(name: str) -> str:
    """'SlimPAR 101' -> 'slimpar-101'."""
    return slugify(name, "light")


def show_from_patch(patch: list[dict] | None = None, name: str = "Central Church") -> Show:
    patch = PATCH if patch is None else patch
    fixtures: list[Fixture] = []
    house: list[HouseLight] = []
    used: dict[str, int] = {}
    spare = 0
    for item in patch:
        profile = PROFILES[item["profile"]]
        group = item.get("group", "")
        fid = fixture_id(item["name"])
        if "red" in profile:
            spots = GROUP_SPOTS.get(group, [])
            index = used.get(group, 0)
            used[group] = index + 1
            if index < len(spots):
                x, y = spots[index]
            else:  # a light from an unknown group: line them up along the bottom
                x, y = 0.1 + 0.1 * (spare % 9), 0.92
                spare += 1
            fixtures.append(
                Fixture(fid, item["name"], item["profile"], item["start_ch"], group, ROLE_COLOUR, MapSpot(x, y))
            )
        else:
            label = HOUSE_LABELS.get(group, group or item["name"])
            fixtures.append(Fixture(fid, label, item["profile"], item["start_ch"], group, ROLE_HOUSE, None))
            house.append(HouseLight(fid, label))
    show, _warnings = ensure_layouts(Show(name=name, fixtures=tuple(fixtures), house=tuple(house)))
    return show  # with one layout, "Main"


def create_show(folder: Path, name: str = "Central Church") -> Show:
    """Make a new show folder from the patch and open it."""
    folder = Path(folder)
    if (folder / SHOW_FILE).exists():
        raise ShowError(f"{folder} already has a show in it")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "inputs").mkdir(exist_ok=True)
    save_whole_show(show_from_patch(name=name), folder)
    return add_starter_inputs(load_show(folder))


def open_or_create_show(folder: Path, name: str | None = None) -> Show:
    folder = Path(folder)
    if (folder / SHOW_FILE).exists():
        show = load_show(folder)
        if not show.inputs and not show.input_errors:
            show = add_starter_inputs(show)
        return show
    return create_show(folder, name or folder.name)
