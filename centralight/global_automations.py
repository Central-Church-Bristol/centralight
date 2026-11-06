"""Global automations: app-wide automations, next to the shows in
Documents\\CentraLight Shows\\_Global automations\\ (one <id>.json each, the same format as a show's
automations, and global.json for the order they're listed and run in).

They're for things that aren't about one show: load a show at a set time ("Sunday 08:30: load Morning"),
cut or fade to black, Master, the Festoon and Key lights (found in the loaded show by their names), and
turning another global automation on or off. They can't touch a show's inputs, the Overlay, variables or
layouts. Their conditions: the PC starting, CentraLight starting, the PC waking, shutting down or going to
sleep (M18), time, days, dates,
sunrise / sunset, an NDI feed, sound, the colour lights being black, and which show is loaded. Each second
they're checked before the loaded show's automations.

In memory they're a Show holding just them (its automations and automation_order), so the same editing
functions and the same undo and autosave work for them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .files import read_json, write_json_atomic
from .model import Show, frozen_map
from .show_io import load_automations

GLOBAL_DIR = "_Global automations"  # in the shows folder (it isn't a show: it has no show.json)
ORDER_FILE = "global.json"
FORMAT_GLOBAL = "centralight-global-automations"
GLOBAL_VERSION = 1
GLOBAL_NAME = "Global automations"


def global_folder(shows_root: Path) -> Path:
    return Path(shows_root) / GLOBAL_DIR


def load_global_automations(folder: Path) -> Show:
    """The global automations in their folder (none if it isn't there yet). A file that can't be used is left out,
    with a warning in the Show's warnings."""
    folder = Path(folder)
    automations, warnings = load_automations(folder, subdir="", scope="global", skip=(ORDER_FILE,))
    order: tuple[str, ...] = ()
    path = folder / ORDER_FILE
    if path.is_file():
        try:
            data = read_json(path)
        except (OSError, ValueError) as exc:
            data = None
            warnings.append(f"{ORDER_FILE} couldn't be read ({exc}), so the global automations are in name order")
        raw = data.get("order") if isinstance(data, dict) else None
        if isinstance(raw, list):
            order = tuple(item for item in raw if isinstance(item, str))
        elif data is not None:
            warnings.append(f'{ORDER_FILE}: "order" must be a list of automation ids, so they\'re in name order')
    return Show(name=GLOBAL_NAME, folder=str(folder), automations=frozen_map(automations), automation_order=order,
                warnings=tuple(warnings))


def order_data(container: Show) -> dict[str, Any]:
    """global.json: the global automations' order."""
    return {"format": FORMAT_GLOBAL, "version": GLOBAL_VERSION, "order": [a.id for a in container.ordered_automations()]}


def save_global_order(container: Show) -> None:
    write_json_atomic(Path(container.folder) / ORDER_FILE, order_data(container))
