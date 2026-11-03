"""Shows: each show is a folder in Documents\\CentraLight Shows\\ with everything for one kind of service: its
lights (the patch) and layouts, inputs, show variables, show automations, media and AI guide.

This module finds the shows there are, and makes, copies and deletes them (loading one is the window's and the
engine's job). Global automations live next to the shows, in "_Global automations", which isn't a show.

A show's id (for the Stream Deck: /api/show/<id>) comes from its folder's name, and its name from show.json:
renaming a show changes only its name, so Stream Deck keys that load it keep working.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .elements import media_path
from .files import read_json, write_json_atomic
from .global_automations import GLOBAL_DIR
from .log import log_info
from .model import Show, is_device_name, slugify
from .show_io import BACKUPS_DIR, SHOW_FILE, ShowError, save_whole_show

NAME_LIMIT = 60  # characters in a show's name (and its folder's)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # characters a Windows folder name can't have


@dataclass(frozen=True)
class ShowInfo:
    """One show in the list: its id (from the folder), its name (from show.json) and its folder."""

    id: str
    name: str
    folder: str


def show_id(folder: str | Path) -> str:
    """'C:\\...\\CentraLight Shows\\Sunday Morning' -> 'sunday-morning'."""
    return slugify(Path(folder).name, "show")


def clean_name(name: str) -> str:
    """A show's name: one line, single spaces, not too long."""
    return " ".join(str(name).split())[:NAME_LIMIT].strip()


def read_show_name(folder: Path) -> str:
    """The name in a show's show.json (its folder's name if it can't be read)."""
    try:
        data = read_json(Path(folder) / SHOW_FILE)
    except Exception:
        data = None
    name = data.get("name") if isinstance(data, dict) else None
    return clean_name(name) if isinstance(name, str) and clean_name(name) else Path(folder).name


def list_shows(root: Path) -> list[ShowInfo]:
    """Every show (a folder with a show.json) in the shows folder, by name."""
    found: list[ShowInfo] = []
    try:
        entries = sorted(Path(root).iterdir())
    except OSError:
        return found
    for entry in entries:
        try:
            if entry.name.startswith("_") or not entry.is_dir() or not (entry / SHOW_FILE).is_file():
                continue
        except OSError:
            continue
        found.append(ShowInfo(show_id(entry), read_show_name(entry), str(entry)))
    found.sort(key=lambda info: (info.name.casefold(), info.id))
    return found


def find_show(root: Path, wanted: str, shows: list[ShowInfo] | None = None) -> ShowInfo | None:
    """A show by its id, its folder's name or its name (ignoring case): what /api/show/<id or name> asks for."""
    shows = list_shows(root) if shows is None else shows
    text = str(wanted).strip()
    if not text:
        return None
    key = slugify(text, "")
    for test in (lambda s: s.id == text, lambda s: Path(s.folder).name.casefold() == text.casefold(),
                 lambda s: s.name.casefold() == text.casefold(), lambda s: bool(key) and (s.id == key or slugify(s.name, "") == key)):
        match = next((s for s in shows if test(s)), None)
        if match is not None:
            return match
    return None


def same_folder(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (TypeError, ValueError):
        return False


def unique_name(root: Path, name: str, ignore: str | Path = "") -> str:
    """name, or "name 2", "name 3"... if another show already has it."""
    name = clean_name(name) or "New show"
    taken = {s.name.casefold() for s in list_shows(root) if not (ignore and same_folder(s.folder, ignore))}
    candidate, n = name, 2
    while candidate.casefold() in taken:
        candidate = f"{name[:NAME_LIMIT - 4]} {n}"
        n += 1
    return candidate


def new_folder(root: Path, name: str) -> Path:
    """A new, unused folder for a show called name, made safe for Windows before anything on disk is looked at: no
    character Windows can't have, no leading "_" (the global automations' folder starts with one, and the list of
    shows leaves such folders out), and never a device's name ("COM3.x" would be the Eurolite's port)."""
    base = _UNSAFE.sub("", clean_name(name)).strip(" .").lstrip("_").strip(" .") or "Show"
    if is_device_name(base):
        base = f"Show {base}"
    taken_ids = {s.id for s in list_shows(root)} | {slugify(GLOBAL_DIR, "show")}
    candidate, n = base, 2
    while (Path(root) / candidate).exists() or slugify(candidate, "show") in taken_ids:
        candidate = f"{base} {n}"
        n += 1
    return Path(root) / candidate


def set_name_in_file(folder: Path, name: str) -> None:
    """Give the show in a folder a new name (in its show.json, keeping everything else as it is)."""
    path = Path(folder) / SHOW_FILE
    data = read_json(path)
    if not isinstance(data, dict):
        raise ShowError(f"{SHOW_FILE} must be a JSON object")
    data["name"] = clean_name(name)
    write_json_atomic(path, data)


def new_show(current: Show, root: Path, name: str) -> Path:
    """A new show with the current show's lights and layouts (room photos too), "Follow screen", and nothing else:
    no other inputs, show variables or automations. Returns its folder."""
    from .new_show import add_starter_inputs
    from .show_io import load_show

    name = unique_name(root, name)
    folder = new_folder(root, name)
    folder.mkdir(parents=True)
    photos = {lay.backdrop for lay in current.layouts if lay.backdrop}
    for relative in sorted(photos):  # (each layout's room photo comes too, at the same place in the new folder)
        source = media_path(current.folder, relative)
        if source is not None and source.is_file():
            target = folder / source.relative_to(Path(current.folder).resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    show = Show(name=name, identify_seed=current.identify_seed, fixtures=current.fixtures, house=current.house,
                layouts=current.layouts, active_layout=current.active_layout)
    save_whole_show(show, folder)
    add_starter_inputs(load_show(folder))
    log_info(f"New show “{name}” in {folder}")
    return folder


def duplicate_show(source: Path, root: Path, name: str) -> Path:
    """A copy of a whole show folder (media included; not its backups) under a new name. Everything in it keeps its
    id, so Stream Deck keys for its inputs work in both. Returns the new folder."""
    name = unique_name(root, name)
    folder = new_folder(root, name)
    shutil.copytree(Path(source), folder, ignore=shutil.ignore_patterns(BACKUPS_DIR))
    set_name_in_file(folder, name)
    log_info(f"Show {Path(source).name} copied as “{name}” ({folder})")
    return folder


# ---------------------------------------------------------------- the Recycle Bin


def to_recycle_bin(path: Path) -> None:
    """Move a file or folder to the Recycle Bin (so it can be put back). Raises OSError if Windows won't."""
    path = Path(path).resolve()
    if sys.platform != "win32":
        raise OSError("the Recycle Bin is only on Windows")
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    fo_delete = 0x0003
    # to the Recycle Bin (allow undo), without its own "are you sure?" (CentraLight asked), a progress window or an
    # error box; but if something can't go to the Recycle Bin (too big, a network drive), Windows asks before it would
    # be deleted for good ("want nuke warning")
    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x4000
    operation = SHFILEOPSTRUCTW(None, fo_delete, str(path) + "\0", None, flags, False, None, None)  # (ends in two nulls)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(f"Windows couldn't move {path.name} to the Recycle Bin (code {result})")
    if path.exists():
        raise OSError(f"{path.name} is still there (is a file in it open in another program?)")


def delete_show(folder: Path, loaded: str | Path, trash=to_recycle_bin) -> None:
    """Move a show's folder to the Recycle Bin (not the loaded show's)."""
    folder = Path(folder)
    if same_folder(folder, loaded):
        raise ShowError("the loaded show can't be deleted: load another show first")
    if not (folder / SHOW_FILE).is_file():
        raise ShowError(f"{folder} isn't a show")
    trash(folder)
    log_info(f"Show {folder.name} moved to the Recycle Bin")
