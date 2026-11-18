"""The light library (M19, 18 Nov): the Open Fixture Library's lights as light types, for the Light type window.

data/light_library.json.gz is made by tools/build_light_library.py from the library's download (open-fixture-library.org,
MIT licence: data/OPEN_FIXTURE_LIBRARY.txt), so CentraLight never goes online for it. It's read the first time
something needs it (a fraction of a second), then kept. A type is only registered (light_types.register) when it's
put on a light.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .light_types import Channel, LightType, LightTypeError, check, make_type, type_to_dict
from .log import log_exception

LIBRARY_FILE = Path(__file__).resolve().parent / "data" / "light_library.json.gz"
FORMAT = "centralight-light-library"
VERSION = 1


@dataclass(frozen=True)
class LibraryModel:
    """One light in the library: its maker, its name, and a light type for each of its modes."""

    maker: str
    name: str
    key: str  # where it is in the library ("chauvet-dj/slimpar-q12-bt")
    categories: tuple[str, ...] = ()
    modes: tuple[LightType, ...] = ()


@dataclass(frozen=True)
class Library:
    models: tuple[LibraryModel, ...] = ()
    source: str = ""
    url: str = ""
    made: str = ""  # when it was made from the download (YYYY-MM-DD)
    problem: str = ""  # why it couldn't be read ("" = fine)
    by_maker: dict[str, tuple[LibraryModel, ...]] = field(default_factory=dict, compare=False)

    def makers(self) -> list[str]:
        return list(self.by_maker)


def library_data(models: list[LibraryModel], *, source: str, url: str, made: str) -> dict[str, Any]:
    """The file form (the build tool writes it): makers in alphabetical order, each with its lights and their modes."""
    makers: dict[str, list[LibraryModel]] = {}
    for model in sorted(models, key=lambda m: (m.maker.casefold(), m.name.casefold())):
        makers.setdefault(model.maker, []).append(model)
    return {
        "format": FORMAT,
        "version": VERSION,
        "source": source,
        "url": url,
        "made": made,
        "makers": [
            {"name": maker, "models": [
                {"name": m.name, "key": m.key, **({"categories": list(m.categories)} if m.categories else {}),
                 "modes": [{"name": t.name, "mode": t.mode, "channels": type_to_dict(t)["channels"]} for t in m.modes]}
                for m in items]}
            for maker, items in makers.items()
        ],
    }


def library_from_data(data: Any) -> Library:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return Library(problem="it isn't a CentraLight light library")
    models: list[LibraryModel] = []
    for maker_item in data.get("makers") or []:
        maker = str(maker_item.get("name") or "")
        for item in maker_item.get("models") or []:
            name = str(item.get("name") or "")
            modes = []
            for mode in item.get("modes") or []:
                try:
                    channels = [Channel(str(c["role"]), str(c.get("name", "")), int(c.get("default", 0)))
                                for c in mode["channels"]]
                    kind = make_type(str(mode["name"]), channels, short=name, maker=maker, model=name,
                                     mode=str(mode.get("mode", "")))
                    check(kind)
                except (KeyError, TypeError, ValueError, LightTypeError):
                    continue  # (a mode this version can't use: the others still can be)
                modes.append(kind)
            if modes:
                models.append(LibraryModel(maker, name, str(item.get("key") or ""),
                                           tuple(str(c) for c in item.get("categories") or []), tuple(modes)))
    by_maker: dict[str, list[LibraryModel]] = {}
    for model in models:
        by_maker.setdefault(model.maker, []).append(model)
    return Library(tuple(models), str(data.get("source") or ""), str(data.get("url") or ""), str(data.get("made") or ""),
                   by_maker={maker: tuple(items) for maker, items in by_maker.items()})


def load(path: Path) -> Library:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            return library_from_data(json.load(file))
    except (OSError, ValueError, EOFError) as exc:
        log_exception("reading the light library", exc)
        return Library(problem=f"couldn't read {path.name}: {exc}")


@lru_cache(maxsize=1)
def library() -> Library:
    """The library CentraLight carries (read once)."""
    return load(LIBRARY_FILE)
