"""Your own light types (M19, 18 Nov): the ones made or changed in the New light type window, kept next to the shows
in "_Light types" (one <id>.json each, the form show.json's "light_types" uses), so every show can use them.

A show also carries the types its lights use, so it never needs this folder: it's the list the Light type window
offers under "Yours". A file that can't be read is left out (and logged); nothing here is ever changed by itself.
"""

from __future__ import annotations

from pathlib import Path

from .files import read_json, write_json_atomic
from .light_types import LightType, LightTypeError, type_from_dict, type_to_dict
from .log import log_exception

FOLDER = "_Light types"  # in the shows folder (it isn't a show: it has no show.json)


def folder(root: Path) -> Path:
    return Path(root) / FOLDER


def load_own(directory: Path | None) -> list[LightType]:
    """Your light types, by name."""
    if directory is None or not Path(directory).is_dir():
        return []
    found: dict[str, LightType] = {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            kind = type_from_dict(read_json(path))
        except (OSError, ValueError, LightTypeError) as exc:
            log_exception(f"reading the light type {path.name}", exc)
            continue
        found.setdefault(kind.id, kind)
    return sorted(found.values(), key=lambda k: k.name.casefold())


def save_own(directory: Path, kind: LightType, replaces: str = "") -> Path:
    """Keep a type in your list (replaces: the id of the one it's a changed copy of, which goes)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind.id}.json"
    write_json_atomic(path, type_to_dict(kind))
    if replaces and replaces != kind.id:
        remove_own(directory, replaces)
    return path


def remove_own(directory: Path, type_id: str) -> bool:
    """Take a type off your list (lights and shows that use it keep it). True if there was one to take off."""
    path = Path(directory) / f"{type_id}.json"
    if path.parent != Path(directory) or not path.is_file():
        return False
    path.unlink()
    return True
