"""CentraLight show files (.clshow): one show, or everything, in a single file to keep, send or move to another PC
(like DaVinci Resolve's .drfx files for macros).

A .clshow file is a zip holding:
- manifest.json: what's inside (the kind of file, its shows, whether the pictures and videos came, when it was made)
- shows/1/, shows/2/...: each show as its folder has it: show.json (with the show's notes), inputs\\, layouts\\,
  automations\\, paint\\ and masks\\ (painted pictures), media\\ (CentraLight's own copies of the pictures and videos,
  unless "Leave out pictures and videos" was ticked, and links.json, where each copy came from) and room.jpg (the room
  photo of a show from before saved layouts). Room photos always come, even without the pictures and videos.
- globals/: the global automations and global.json, their order (Export everything)
- settings.json: CentraLight's settings (Export everything)

Not included: backups (they belong to the PC that made them) and the files for AI tools (AI_GUIDE.md, AGENTS.md,
input.schema.json), which CentraLight writes again when the show is opened.

A .clshow file can come from anyone, so opening one is careful, the way an input from an AI is:
- Everything is read and checked before anything is written. It's only ever data: nothing in it is run, and every
  expression must pass the sandboxed expression checker.
- Every name inside must be a plain name inside a show folder: no "..", no "C:" or "\\", no names Windows keeps for
  devices (COM3 is the lights' USB port: even asking whether such a file exists opens the port), no links, and no two
  names that differ only in capitals. Names are checked as text, and nothing named in the file is looked for on disk.
- Only the files a show has (above) are used. Anything else is left out, with a note.
- Sizes are limited, from the zip's own list and again while reading, so a file can't claim to be small and then
  turn out huge.
- show.json, inputs, layouts and automations go through the same checks as when a show opens. A file that fails is
  left out (an automation with a bad condition is switched off), and the summary says why.
- Links to original pictures and videos stay as text. Nothing looks at them until someone at this PC says OK
  (links.py: they aren't added to the allowed links here).

Importing writes a new show folder from the checked data (JSON is written afresh, never copied), first into a hidden
folder that's renamed into place at the end, so a failure leaves nothing behind. An imported show isn't loaded.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import struct
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .ai_guide import AGENTS_FILE, GUIDE_FILE, SCHEMA_FILE, SPECIAL_NAMES
from .assets import MASKS_DIR, PAINT_DIR
from .expressions import ExpressionError, compile_expression
from .files import forget_written, json_text, loads_json_bytes, write_json_atomic
from .global_automations import FORMAT_GLOBAL, GLOBAL_DIR, GLOBAL_VERSION, ORDER_FILE
from .input_edit import MEDIA_DIR
from .links import FORMAT_LINKS, LINKS_FILE, PICTURE_TYPES, VIDEO_TYPES, link_key, original_problem, safe_media_file
from .log import log_info
from .model import EMPTY, Automation, Input, Layout, Show, frozen_map, is_device_name, is_safe_id, slugify
from .settings import Settings
from .show_io import (
    AUTOMATIONS_DIR,
    BACKUPS_DIR,
    INPUTS_DIR,
    LAYOUTS_DIR,
    MAX_INPUT_BYTES,
    OLD_ROOM_PHOTO,
    SHOW_FILE,
    InputFileError,
    ShowError,
    automation_file,
    automation_from_dict,
    automation_to_dict,
    input_from_dict,
    input_to_dict,
    layout_from_dict,
    layout_to_dict,
    migrate_show,
    safe_relative_file,
    show_from_dict,
    show_to_dict,
)
from .shows import (
    NAME_LIMIT,
    ShowInfo,
    clean_name,
    folder_name,
    list_shows,
    new_folder,
    same_folder,
    show_id,
    to_recycle_bin,
)

EXTENSION = ".clshow"
FORMAT_PACKAGE = "centralight-package"
PACKAGE_VERSION = 1
KIND_SHOW = "show"  # Show menu → Export…: one show
KIND_EVERYTHING = "everything"  # Export everything…: every show, the global automations and the settings
MANIFEST = "manifest.json"
SHOWS_DIR = "shows"
GLOBALS_DIR = "globals"
SETTINGS_FILE = "settings.json"
MEDIA_TYPES = PICTURE_TYPES | VIDEO_TYPES

# Limits. A real show is far inside every one; they stop a damaged or harmful file filling the disk, the memory or the
# processor (ProPresenter runs on this PC too).
MAX_ENTRIES = 20_000  # files in one .clshow (a show has tens to a few hundred)
MAX_LIST_BYTES = 4_000_000  # the zip's list of its files, checked before Python reads that list into memory
MAX_JSON_BYTES = MAX_INPUT_BYTES  # each JSON file: the limit an input file has anyway (2 MB)
MAX_JSON_TOTAL = 8_000_000  # all the JSON files together (they're read into memory to be checked)
MAX_JSON_RATIO = 50  # unpacked : packed for a JSON file over JSON_RATIO_FROM (CentraLight's own pack about 5-10 times)
JSON_RATIO_FROM = 64_000
MAX_EXPRESSIONS = 1000  # in one input (each is compiled to be checked; a real input has tens)
MAX_PAINTED_BYTES = 4_000_000  # a painted picture (a 640x360 PNG is under 1 MB even unpacked)
MAX_MEDIA_BYTES = 4_000_000_000  # a picture or video (about an hour of 1080p video)
MAX_TOTAL_BYTES = 64_000_000_000  # everything in the file, unpacked
MAX_RATIO = 100  # unpacked size : packed size, for any file over SMALL_FILE (pictures and videos are stored as they are)
SMALL_FILE = MAX_INPUT_BYTES  # (small files can be very repetitive, and each has its own limit)
MAX_NAME_CHARS = 150  # a file's name inside its show (Windows limits how long a whole path can be)
MAX_ENTRY_CHARS = 255
MAX_SHOWS = 500
SPARE_SPACE = 256_000_000  # left free on the disk after an import
CHUNK = 1024 * 1024

# Names in the zip
_SHOW_PATH = re.compile(r"^shows/[1-9][0-9]{0,3}$")
_SHOW_ENTRY = re.compile(r"^shows/[1-9][0-9]{0,3}/")
_ID_FILE = re.compile(r"^([a-z0-9][a-z0-9-]{0,63})\.json$")
_PAINTED = re.compile(rf"^(?:{PAINT_DIR}|{MASKS_DIR})/[0-9a-f]{{12}}\.png$")
_NOT_IN_NAMES = set('<>"|?*')  # (characters Windows can't have in a file name)
_SHORT_NAME = re.compile(r"~\d")  # Windows' short names ("CANDLE~1.JPG") can stand for another file in the same folder
_QUIET = {BACKUPS_DIR, GUIDE_FILE, AGENTS_FILE, SCHEMA_FILE, "desktop.ini", "thumbs.db"}  # never exported, no note
NOT_A_SHOW_FILE = "It isn't a CentraLight show file"

# The end of a zip, where it says how long its list of files is (read before Python's zipfile reads that list): the
# end record, and for a big zip the zip64 end record and the locator after it
_END = struct.Struct("<4s4H2LH")
_END64_LOCATOR = struct.Struct("<4sLQL")
_END64 = struct.Struct("<4sQ2H2L4Q")

Progress = Callable[[int, int], None]  # (bytes done, bytes in all); raise from it to stop (nothing is left behind)


class PackageError(Exception):
    """A .clshow file can't be made or opened, or a show in it can't be imported. The message is for people."""


# ---------------------------------------------------------------- what's in a .clshow file


@dataclass(frozen=True)
class PackedFile:
    """A file copied into the show as it is: a picture, a video or a painted picture."""

    entry: str  # its name in the .clshow ("shows/1/media/candles.mp4")
    name: str  # its name in the show folder ("media/candles.mp4")
    size: int
    crc: int


@dataclass(frozen=True)
class PackagedShow:
    """One show in a .clshow file, checked. If it can't be imported, show is None and refused says why."""

    index: int  # its place in Package.shows
    name: str  # its name (from its show.json)
    id: str  # its id on the PC it came from (its folder's name): Stream Deck keys and "Load a show" use it
    folder: str  # that folder's name (text: an import reuses it, made safe, if it's free here)
    show: Show | None = None  # show.json, checked: lights, house lights, show variables, notes, orders
    inputs: Mapping[str, Input] = EMPTY
    layouts: tuple[Layout, ...] = ()
    automations: Mapping[str, Automation] = EMPTY
    files: tuple[PackedFile, ...] = ()  # pictures, videos and painted pictures
    links: Mapping[str, Any] | None = None  # media\links.json, checked: where each local copy came from
    originals: tuple[str, ...] = ()  # the original files it links to: text only, followed only after an OK here
    problems: tuple[str, ...] = ()  # what was left out (or switched off), and why
    notes: tuple[str, ...] = ()  # smaller things: an old setting brought up to date, an unknown field ignored...
    refused: str = ""  # why it can't be imported at all ("" = it can)

    @property
    def ok(self) -> bool:
        return self.show is not None and not self.refused

    @property
    def media(self) -> tuple[PackedFile, ...]:
        """Its pictures and videos (room photos included)."""
        return tuple(f for f in self.files if f.name.startswith(f"{MEDIA_DIR}/") or f.name == OLD_ROOM_PHOTO)

    @property
    def media_bytes(self) -> int:
        return sum(f.size for f in self.media)

    def describe(self) -> str:
        """For the summary: "12 inputs, 3 automations, 2 layouts, 5 pictures and videos (120 MB)"."""
        if not self.ok:
            return f"can't be imported: {self.refused}"
        parts = [_count(len(self.inputs), "input"), _count(len(self.automations), "automation"),
                 _count(len(self.layouts), "layout")]
        if self.media:
            pictures = _count(len(self.media), "picture or video", "pictures and videos")
            parts.append(f"{pictures} ({size_text(self.media_bytes)})")
        return ", ".join(parts)


@dataclass(frozen=True)
class PackagedGlobals:
    """The global automations in an Export everything file, checked."""

    automations: Mapping[str, Automation] = EMPTY
    order: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def ordered(self) -> list[Automation]:
        return Show(automations=self.automations, automation_order=self.order).ordered_automations()


@dataclass(frozen=True)
class PackagedSettings:
    """The settings in an Export everything file, checked (bad values fixed, as when CentraLight starts)."""

    settings: Settings
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Package:
    """A .clshow file, read and checked (nothing has been written). Import from it with import_show(),
    import_globals() and import_settings()."""

    path: Path
    kind: str  # KIND_SHOW or KIND_EVERYTHING
    made: str  # when it was made ("2026-11-03T21:15:00")
    media: bool  # its pictures and videos are inside (False: "Leave out pictures and videos" was ticked)
    shows: tuple[PackagedShow, ...]
    globals: PackagedGlobals | None = None
    settings: PackagedSettings | None = None
    problems: tuple[str, ...] = ()  # about the file as a whole (things in it that were left out)

    @property
    def has_links(self) -> bool:
        """Does any show in it link to original files (which wait for an OK at this PC)?"""
        return any(item.originals for item in self.shows)

    def all_problems(self) -> list[str]:
        """Every problem in the file, each saying where ("Morning: inputs\\x.json was left out: ...")."""
        lines = list(self.problems)
        for item in self.shows:
            lines += [f"{item.name}: {item.refused}"] if item.refused else [f"{item.name}: {p}" for p in item.problems]
        if self.globals is not None:
            lines += [f"Global automations: {p}" for p in self.globals.problems]
        return lines


@dataclass
class ExportReport:
    """What an export put in the file, and what it left out (and why)."""

    target: Path
    media: bool = True
    shows: list[str] = field(default_factory=list)  # the names of the shows inside
    files: int = 0  # files inside (not counting manifest.json)
    bytes: int = 0  # their size before packing
    size: int = 0  # the .clshow file's size
    globals: int = 0  # global automations inside (Export everything)
    settings: bool = False  # the settings are inside (Export everything)
    skipped: list[str] = field(default_factory=list)  # "Morning: media\\notes.txt isn't a picture or video, so ..."


@dataclass(frozen=True)
class GlobalsImported:
    """What import_globals() did, by automation name."""

    added: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()  # already here (the same id), so left as they are


# ---------------------------------------------------------------- small helpers


def size_text(size: int) -> str:
    """1234567 -> "1.2 MB"."""
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if size >= scale:
            value = size / scale
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{size} bytes"


def _count(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _shown(name: str) -> str:
    """A name from a file, safe to show in a message (control characters as "?", not too long)."""
    text = "".join(ch if ord(ch) >= 32 and ord(ch) != 127 else "?" for ch in str(name))
    return f'"{text[:80]}…"' if len(text) > 80 else f'"{text}"'


def _where(name: str) -> str:
    """"media/candles.mp4" -> "media\\candles.mp4" (as Windows shows it)."""
    return name.replace("/", "\\")


def _text(value: Any, limit: int = 255) -> str:
    return value if isinstance(value, str) and len(value) <= limit else ""


def export_file_name(name: str) -> str:
    """The file name a save dialog suggests: "Morning.clshow" (made safe for Windows, never a device's name)."""
    return folder_name(name) + EXTENSION


def name_problem(name: str) -> str:
    """Why a name inside a .clshow can't be used ("" = it can). Plain text checks: nothing here looks at the disk."""
    if not isinstance(name, str) or not name:
        return "it has no name"
    if len(name) > MAX_ENTRY_CHARS:
        return "its name is too long"
    if "\\" in name:
        return 'its name has a "\\" in it'
    if ":" in name:
        return 'its name has a ":" in it (a drive, like C:, or a hidden part of a file)'
    if name.startswith("/"):
        return "its name starts at the top of a drive"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return "its name has a control character in it"
    if any(ch in _NOT_IN_NAMES for ch in name):
        return "its name has a character Windows can't have in a file name"
    if _SHORT_NAME.search(name):
        return 'its name looks like a Windows short name (like "CANDLE~1.JPG"), which can stand for another file'
    for part in (name[:-1] if name.endswith("/") else name).split("/"):
        if part in ("", ".", ".."):
            return 'its name steps outside its folder (".." or an empty part)'
        if part != part.strip() or part.endswith("."):
            return "a part of its name ends in a space or a dot"
        if is_device_name(part):
            return f'"{part}" is a name Windows keeps for a device (like COM3)'
    return ""


def _role(rel: str) -> str:
    """What a file in a show folder is, from its name there ("" = not one of a show's files)."""
    path = PurePosixPath(rel)
    if rel == SHOW_FILE:
        return "show"
    if len(path.parts) == 2:
        folder, name = path.parts
        match = _ID_FILE.match(name)
        if match and is_safe_id(match.group(1)):
            if folder == INPUTS_DIR:
                return "input"
            if folder == LAYOUTS_DIR:
                return "layout"
            if folder == AUTOMATIONS_DIR:
                return "automation"
        if folder == MEDIA_DIR:
            if name == LINKS_FILE:
                return "links"
            if path.suffix.lower() in MEDIA_TYPES:
                return "media"
        if _PAINTED.match(rel):
            return "painted"
    if rel == OLD_ROOM_PHOTO:
        return "photo"
    return ""


def name_taken(shows_root: str | Path, name: str) -> ShowInfo | None:
    """The show here that already has this name (ignoring capitals), if one does."""
    wanted = clean_name(name).casefold()
    return next((s for s in list_shows(Path(shows_root)) if s.name.casefold() == wanted), None)


def keep_both_name(shows_root: str | Path, name: str) -> str:
    """For "Keep both": "Morning (2)", or (3)... whichever no show here has."""
    base = re.sub(r" \(\d+\)$", "", clean_name(name)) or "Show"
    taken = {s.name.casefold() for s in list_shows(Path(shows_root))}
    n = 2
    while True:
        candidate = f"{base[:NAME_LIMIT - 7].rstrip()} ({n})"
        if candidate.casefold() not in taken:
            return candidate
        n += 1


# ---------------------------------------------------------------- exporting


@dataclass
class _Item:
    """One file to put in the .clshow: JSON (packed) or a file copied as it is (stored: it's packed already)."""

    entry: str
    data: bytes | None = None
    source: Path | None = None
    size: int = 0
    label: str = ""  # for a note if it can't be read after all ("Morning: media\\candles.mp4")


def export_show(folder: str | Path, target: str | Path, *, media: bool = True,
                progress: Progress | None = None) -> ExportReport:
    """Show menu → Export…: one show in a .clshow file. media=False leaves out the pictures and videos (not the room
    photos). Nothing in the show folder changes. Raises PackageError if the show can't be read or the file saved."""
    folder = Path(folder)
    report = ExportReport(Path(target), media=media)
    items, listed = _show_items(folder, f"{SHOWS_DIR}/1/", media, report.skipped)
    report.shows.append(listed["name"])
    _write_package(report, _manifest_data(KIND_SHOW, media, [listed]), items, progress)
    log_info(f"Show “{listed['name']}” exported to {report.target} ({report.files} files, {size_text(report.size)})")
    return report


def export_everything(shows_root: str | Path, target: str | Path, *, media: bool = True,
                      settings_file: str | Path | None = None, progress: Progress | None = None) -> ExportReport:
    """Show menu → Export everything…: every show, the global automations and (given its file) the settings, for
    moving to a new PC. A show whose show.json can't be read is left out with a note, rather than stopping the rest."""
    root = Path(shows_root)
    report = ExportReport(Path(target), media=media)
    items: list[_Item] = []
    listed: list[dict[str, Any]] = []
    for info in list_shows(root):
        try:
            show_items, entry = _show_items(Path(info.folder), f"{SHOWS_DIR}/{len(listed) + 1}/", media, report.skipped)
        except PackageError as exc:
            report.skipped.append(str(exc))
            continue
        items += show_items
        listed.append(entry)
        report.shows.append(entry["name"])
    for path in _files_in(root / GLOBAL_DIR, GLOBAL_DIR, report.skipped):
        if path.name.casefold() in {q.casefold() for q in _QUIET}:
            continue  # (the AI guide: written again there)
        is_order = path.name.casefold() == ORDER_FILE.casefold()
        match = _ID_FILE.match(path.name)
        if not is_order and not (match and is_safe_id(match.group(1))):
            report.skipped.append(f"{GLOBAL_DIR}\\{path.name} isn't a global automation, so it was left out")
            continue
        raw = _small_json(path, f"{GLOBAL_DIR}\\{path.name}", report.skipped)
        if raw is not None:
            items.append(_Item(f"{GLOBALS_DIR}/{ORDER_FILE if is_order else path.name}", raw, size=len(raw)))
            report.globals += 0 if is_order else 1
    if settings_file is not None:
        raw = _small_json(Path(settings_file), "settings", report.skipped)
        if raw is not None:
            settings = Settings()
            data = loads_json_bytes(raw)
            if isinstance(data, dict):
                settings.update(data)  # (only the settings CentraLight knows, with bad values fixed)
                settings.remote_key = ""  # (the phone page's key stays on this PC: a file can be passed around)
                text = json_text(settings.to_dict()).encode("utf-8")
                items.append(_Item(SETTINGS_FILE, text, size=len(text)))
                report.settings = True
            else:
                report.skipped.append("settings: the file isn't a list of settings, so it was left out")
    globals_inside = any(item.entry.startswith(f"{GLOBALS_DIR}/") for item in items)
    manifest = _manifest_data(KIND_EVERYTHING, media, listed, globals_inside=globals_inside, settings_inside=report.settings)
    _write_package(report, manifest, items, progress)
    log_info(f"Everything exported to {report.target}: {len(report.shows)} shows, {report.globals} global automations"
             f"{', the settings' if report.settings else ''} ({size_text(report.size)})")
    return report


def _manifest_data(kind: str, media: bool, shows: list[dict[str, Any]], globals_inside: bool = False,
                   settings_inside: bool = False) -> dict[str, Any]:
    return {
        "format": FORMAT_PACKAGE,
        "version": PACKAGE_VERSION,
        "kind": kind,
        "made": datetime.now().isoformat(timespec="seconds"),
        "made_by": "CentraLight",
        "media": media,
        "shows": shows,
        "globals": globals_inside,
        "settings": settings_inside,
    }


def _files_in(directory: Path, where: str, skipped: list[str]) -> list[Path]:
    """The files in one of a show's folders, by name. A name that can't go in a .clshow is noted and never looked at
    (a device's name, like COM3, would open the device); folders inside are noted too."""
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
    except FileNotFoundError:
        return []
    except OSError as exc:
        skipped.append(f"{where} couldn't be read ({exc.strerror or exc})")
        return []
    found: list[Path] = []
    for entry in entries:
        if entry.name.startswith("."):  # (a save in progress, or a hidden file)
            continue
        problem = name_problem(entry.name)
        if problem:
            skipped.append(f"{where}\\{_shown(entry.name)} was left out: {problem}")
            continue
        try:
            if entry.is_dir():
                skipped.append(f"{where}\\{entry.name} is a folder, so it was left out")
                continue
        except OSError:
            continue
        found.append(entry)
    return found


def _small_json(path: Path, where: str, skipped: list[str]) -> bytes | None:
    """A JSON file's bytes, if it can be read, isn't too big and is JSON (else a note, and None)."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
    except OSError as exc:
        skipped.append(f"{where} couldn't be read ({exc.strerror or exc}), so it was left out")
        return None
    if len(raw) > MAX_JSON_BYTES:
        skipped.append(f"{where} is too big (over {MAX_JSON_BYTES // 1_000_000} MB), so it was left out")
        return None
    try:
        loads_json_bytes(raw)
    except Exception as exc:
        skipped.append(f"{where} isn't valid JSON ({type(exc).__name__}), so it was left out")
        return None
    return raw


def _show_items(folder: Path, prefix: str, media: bool, skipped: list[str]) -> tuple[list[_Item], dict[str, Any]]:
    """Everything of one show that goes in a .clshow, plus its line in the manifest. Only reads the show folder."""
    try:
        with open(folder / SHOW_FILE, "rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
        data = loads_json_bytes(raw) if len(raw) <= MAX_JSON_BYTES else None
    except (OSError, ValueError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        raise PackageError(f"{folder.name}: its show.json can't be read ({reason}), so it wasn't exported") from exc
    if not isinstance(data, dict):
        raise PackageError(f"{folder.name}: its show.json isn't a show (or is too big), so it wasn't exported")
    name = clean_name(data["name"]) if isinstance(data.get("name"), str) and clean_name(data["name"]) else folder.name
    notes: list[str] = []
    items = [_Item(prefix + SHOW_FILE, raw, size=len(raw))]
    photos: set[str] = set()  # room photos (they come even when the pictures and videos don't)
    for sub in (INPUTS_DIR, LAYOUTS_DIR, AUTOMATIONS_DIR):
        for path in _files_in(folder / sub, sub, notes):
            rel = f"{sub}/{path.name}"
            if not _role(rel):
                notes.append(f"{_where(rel)} isn't named like one of CentraLight's files, so it was left out")
                continue
            text = _small_json(path, _where(rel), notes)
            if text is None:
                continue
            if sub == LAYOUTS_DIR:
                layout = loads_json_bytes(text)
                photo = safe_relative_file(layout.get("backdrop")) if isinstance(layout, dict) else ""
                if photo:
                    photos.add(photo.casefold())
            items.append(_Item(prefix + rel, text, size=len(text)))
    for sub in (PAINT_DIR, MASKS_DIR):
        for path in _files_in(folder / sub, sub, notes):
            rel = f"{sub}/{path.name}"
            if _role(rel) != "painted":
                notes.append(f"{_where(rel)} isn't one of CentraLight's painted pictures, so it was left out")
                continue
            _add_file(items, notes, path, prefix, rel, MAX_PAINTED_BYTES, name)
    for path in _files_in(folder / MEDIA_DIR, MEDIA_DIR, notes):
        rel = f"{MEDIA_DIR}/{path.name}"
        role = _role(rel)
        if role == "links":
            text = _small_json(path, _where(rel), notes)
            if text is not None:
                items.append(_Item(prefix + rel, text, size=len(text)))
        elif role == "media":
            if media or rel.casefold() in photos:  # (left out on purpose otherwise: no note)
                _add_file(items, notes, path, prefix, rel, MAX_MEDIA_BYTES, name)
        else:
            notes.append(f"{_where(rel)} isn't a picture or video, so it was left out")
    if (folder / OLD_ROOM_PHOTO).is_file():
        _add_file(items, notes, folder / OLD_ROOM_PHOTO, prefix, OLD_ROOM_PHOTO, MAX_MEDIA_BYTES, name)
    for photo in sorted(photos):
        if photo != OLD_ROOM_PHOTO and not photo.startswith(f"{MEDIA_DIR}/"):
            notes.append(f"the room photo {_where(photo)} isn't in the media folder, so it was left out")
    known = {SHOW_FILE, INPUTS_DIR, LAYOUTS_DIR, AUTOMATIONS_DIR, PAINT_DIR, MASKS_DIR, MEDIA_DIR, OLD_ROOM_PHOTO} | _QUIET
    known = {k.casefold() for k in known}
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if entry.name.casefold() not in known and not entry.name.startswith("."):
            notes.append(f"{_shown(entry.name)} isn't part of a show, so it was left out")
    skipped.extend(f"{name}: {note}" for note in notes)
    listed = {"path": prefix.rstrip("/"), "name": name, "id": show_id(folder), "folder": folder.name}
    return items, listed


def _add_file(items: list[_Item], notes: list[str], path: Path, prefix: str, rel: str, limit: int, show: str) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        notes.append(f"{_where(rel)} couldn't be read ({exc.strerror or exc}), so it was left out")
        return
    if len(rel) > MAX_NAME_CHARS:
        notes.append(f"{_where(rel)}: its name is too long, so it was left out")
    elif size > limit:
        notes.append(f"{_where(rel)} is too big (over {size_text(limit)}), so it was left out")
    else:
        items.append(_Item(prefix + rel, source=path, size=size, label=f"{show}: {_where(rel)}"))


def _write_package(report: ExportReport, manifest: dict[str, Any], items: list[_Item], progress: Progress | None) -> None:
    """Write the .clshow: first to a hidden file next to it, swapped in at the end, so a failed export never leaves
    half a file (or spoils an older one with the same name). JSON is packed; pictures and videos are stored as they
    are (they're packed already, and packing them again would only cost time)."""
    target = report.target
    temp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    total = sum(item.size for item in items) or 1
    done = 0
    try:
        with zipfile.ZipFile(temp, "x", allowZip64=True) as archive:
            for item in items:
                if item.data is not None:
                    archive.writestr(_zip_info(item.entry), item.data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
                    written = len(item.data)
                else:
                    written = _store_file(archive, item, report, lambda n: progress(done + n, total) if progress else None)
                    if written is None:
                        continue
                report.files += 1
                report.bytes += written
                done += item.size
                if progress is not None:
                    progress(min(done, total), total)
            archive.writestr(_zip_info(MANIFEST), json_text(manifest), compress_type=zipfile.ZIP_DEFLATED)
        _replace_file(temp, target)
        report.size = target.stat().st_size
    except OSError as exc:
        raise PackageError(f"The file couldn't be saved ({exc.strerror or exc}).") from exc
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _zip_info(entry: str) -> zipfile.ZipInfo:
    return zipfile.ZipInfo(entry, date_time=time.localtime()[:6])


def _store_file(archive: zipfile.ZipFile, item: _Item, report: ExportReport, moved: Callable[[int], None]) -> int | None:
    """Copy a picture or video into the zip as it is. One that can't be opened is left out with a note (it's opened
    before its entry starts, so the file never holds half of one)."""
    try:
        source = open(item.source, "rb")
    except OSError as exc:
        report.skipped.append(f"{item.label} couldn't be read ({exc.strerror or exc}), so it was left out")
        return None
    with source:
        info = _zip_info(item.entry)
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = item.size
        written = 0
        with archive.open(info, "w", force_zip64=item.size > 1_000_000_000) as out:
            while chunk := source.read(CHUNK):
                out.write(chunk)
                written += len(chunk)
                moved(min(written, item.size))
    return written


def _replace_file(source: Path, target: Path, tries: int = 10) -> None:
    for attempt in range(tries):
        try:
            os.replace(source, target)
            return
        except PermissionError:  # (Explorer's preview or antivirus can hold the old file for a moment)
            if attempt == tries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


# ---------------------------------------------------------------- reading and checking a .clshow file


def read_package(path: str | Path) -> Package:
    """Open a .clshow file and check everything in it; nothing is written. Raises PackageError (with a reason for
    people) if the file can't be opened at all. A show in it that can't be imported says why in .refused; files that
    were left out are in each part's problems (Package.all_problems() lists them all)."""
    path = Path(path)
    try:
        _check_list_size(path)
        with zipfile.ZipFile(path) as archive:
            files = _checked_entries(archive.infolist())
            manifest = _read_manifest(archive, files)
            json_total = sum(info.file_size for name, info in files.items() if name.lower().endswith(".json"))
            if json_total > MAX_JSON_TOTAL:
                raise PackageError(f"{NOT_A_SHOW_FILE} anyone should open: the show data in it comes to "
                                   f"{size_text(json_total)}, far more than any show has.")
            everything = manifest["kind"] == KIND_EVERYTHING
            groups: dict[str, dict[str, zipfile.ZipInfo]] = {item["path"] + "/": {} for item in manifest["shows"]}
            problems: list[str] = []
            for name, info in sorted(files.items()):
                match = _SHOW_ENTRY.match(name)
                if match and match.group(0) in groups:
                    groups[match.group(0)][name[match.end():]] = info
                elif name == MANIFEST or (everything and (name == SETTINGS_FILE or name.startswith(f"{GLOBALS_DIR}/"))):
                    continue
                else:
                    problems.append(f"{_where(name)} isn't part of a CentraLight show file, so it was left out")
            shows = tuple(_read_show(archive, groups[item["path"] + "/"], index, item)
                          for index, item in enumerate(manifest["shows"]))
            globals_part = _read_globals(archive, files) if everything else None
            settings_part = _read_settings(archive, files, problems) if everything else None
    except PackageError:
        raise
    except FileNotFoundError as exc:
        raise PackageError(f"{path.name} isn't there any more.") from exc
    except Exception as exc:  # whatever is wrong with a file from elsewhere: a reason for people, never a crash
        raise PackageError(f"{path.name} couldn't be read: it isn't a CentraLight show file, or it's damaged "
                           f"({type(exc).__name__}: {exc}).") from exc
    return Package(path, manifest["kind"], manifest["made"], manifest["media"], shows, globals_part, settings_part,
                   tuple(problems))


def _check_list_size(path: Path) -> None:
    """Refuse a zip whose list of files is unreasonably long, before Python's zipfile reads that whole list into
    memory. The numbers are where zipfile looks for them: the last end record in the file's last 64 KB, and, if a
    zip64 locator comes just before it, the zip64 record where the locator points or just before the locator (zipfile
    then uses that record's numbers). Every one of them must be within the limits."""
    with open(path, "rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        tail_size = min(size, _END.size + 0x10000)  # (the end record and the longest comment it can have)
        handle.seek(size - tail_size)
        tail = handle.read(tail_size)
        at = tail.rfind(b"PK\x05\x06")
        if at < 0 or len(tail) - at < _END.size:
            raise PackageError(f"{NOT_A_SHOW_FILE} (it isn't a zip file).")
        counts = [_END.unpack_from(tail, at)[4:6]]  # (how many files, and how long their list is)
        end_at = size - tail_size + at
        if end_at >= _END64_LOCATOR.size:
            handle.seek(end_at - _END64_LOCATOR.size)
            locator = handle.read(_END64_LOCATOR.size)
            if locator[:4] == b"PK\x06\x07":
                for place in {_END64_LOCATOR.unpack(locator)[2], end_at - _END64_LOCATOR.size - _END64.size}:
                    if 0 <= place <= size - _END64.size:
                        handle.seek(place)
                        record = handle.read(_END64.size)
                        if record[:4] == b"PK\x06\x06":
                            counts.append(_END64.unpack(record)[7:9])
    if any(entries > MAX_ENTRIES or listed > MAX_LIST_BYTES for entries, listed in counts):
        raise PackageError(f"It holds too many files (more than {MAX_ENTRIES:,}), so it wasn't opened.")


def _checked_entries(infos: list[zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    """Every name and size in the zip's list, checked before anything in it is read. Anything unsafe refuses the
    whole file: CentraLight never makes such a file, so it was made by hand, damaged, or meant to do harm."""
    if len(infos) > MAX_ENTRIES:
        raise PackageError(f"It holds too many files (more than {MAX_ENTRIES:,}), so it wasn't opened.")
    files: dict[str, zipfile.ZipInfo] = {}
    seen: dict[str, str] = {}
    total = 0
    for info in infos:
        for name in {info.orig_filename, info.filename}:  # (on Windows zipfile turns "\" into "/": the original counts)
            problem = name_problem(name)
            if problem:
                raise PackageError(f"It has a file called {_shown(name)}: {problem}. It wasn't opened, to keep this "
                                   "PC safe.")
        name = info.filename
        if stat.S_ISLNK(info.external_attr >> 16):
            raise PackageError(f"{_shown(name)} in it is a link to somewhere else. It wasn't opened, to keep this PC safe.")
        if info.flag_bits & 0x1:
            raise PackageError(f"{_shown(name)} in it is locked with a password, so it can't be opened.")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise PackageError(f"{_shown(name)} in it is packed in a way CentraLight doesn't use, so it can't be opened.")
        key = (name[:-1] if name.endswith("/") else name).upper()  # (Windows doesn't tell capitals apart)
        if key in seen:
            raise PackageError(f"It has two files called {_shown(seen[key])} and {_shown(name)} (the same name to "
                               "Windows). It wasn't opened, to keep this PC safe.")
        seen[key] = name
        if info.is_dir():
            continue
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise PackageError(f"It unpacks to more than {size_text(MAX_TOTAL_BYTES)}, so it wasn't opened.")
        if info.file_size > SMALL_FILE and info.file_size > MAX_RATIO * max(1, info.compress_size):
            raise PackageError(f"{_shown(name)} in it unpacks to far more than it takes up ({size_text(info.file_size)} "
                               f"from {size_text(info.compress_size)}), which only a damaged or harmful file does. "
                               "It wasn't opened.")
        if name.lower().endswith(".json"):
            if info.file_size > JSON_RATIO_FROM and info.file_size > MAX_JSON_RATIO * max(1, info.compress_size):
                raise PackageError(f"{_shown(name)} in it unpacks to far more than it takes up, which only a damaged "
                                   "or harmful file does. It wasn't opened.")
        elif info.compress_type != zipfile.ZIP_STORED and name != MANIFEST:
            # (CentraLight stores pictures, videos and paintings as they are: unpacking one then never writes more
            # than the file holds)
            raise PackageError(f"{_shown(name)} in it is packed in a way CentraLight doesn't use, so it wasn't opened.")
        files[name] = info
    return files


def clean_leftover_imports(shows_root: str | Path, older_than: float = 3600.0) -> list[str]:
    """Remove "_Importing ..." folders an import left behind when CentraLight stopped in the middle of one (only those,
    and only once they're an hour old: an import under way is never touched)."""
    import shutil
    import time

    removed: list[str] = []
    try:
        entries = list(Path(shows_root).iterdir())
    except OSError:
        return removed
    for entry in entries:
        if not entry.name.startswith("_Importing ") or is_device_name(entry.name):
            continue
        try:
            if entry.is_dir() and time.time() - entry.stat().st_mtime > older_than:
                shutil.rmtree(entry)
                removed.append(entry.name)
        except OSError:
            continue
    if removed:
        log_info(f"Removed what unfinished imports left behind: {', '.join(removed)}")
    return removed


def _read_json(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int = MAX_JSON_BYTES) -> Any:
    """A JSON file in the .clshow, its size checked twice: in the zip's list, and while reading (a list that lies
    about a size can't get more past). Raises InputFileError with a reason."""
    if info.file_size > limit:
        raise InputFileError(f"the file is too big (over {limit // 1_000_000} MB)")
    try:
        with archive.open(info) as handle:
            raw = handle.read(limit + 1)
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, ValueError) as exc:  # (zlib.error is an Exception too)
        raise InputFileError(f"it's damaged ({exc})") from exc
    except Exception as exc:
        raise InputFileError(f"it's damaged ({type(exc).__name__})") from exc
    if len(raw) != info.file_size:
        raise InputFileError("it's damaged (it isn't the size the file says)")
    try:
        return loads_json_bytes(raw)
    except ValueError as exc:  # (UnicodeDecodeError is one)
        raise InputFileError(f"it isn't valid JSON ({exc})") from exc


def _read_manifest(archive: zipfile.ZipFile, files: Mapping[str, zipfile.ZipInfo]) -> dict[str, Any]:
    """manifest.json, checked: anything wrong with it refuses the whole file."""
    info = files.get(MANIFEST)
    if info is None:
        raise PackageError(f"{NOT_A_SHOW_FILE} (there's no {MANIFEST} in it).")
    try:
        data = _read_json(archive, info)
    except InputFileError as exc:
        raise PackageError(f"Its {MANIFEST} can't be read: {exc}.") from exc
    except Exception as exc:
        raise PackageError(f"Its {MANIFEST} can't be read ({type(exc).__name__}).") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT_PACKAGE:
        raise PackageError(f"{NOT_A_SHOW_FILE}.")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PackageError(f"Its {MANIFEST} has no version, so it can't be opened.")
    if version > PACKAGE_VERSION:
        raise PackageError("It was made by a newer CentraLight. Update CentraLight on this PC to open it.")
    kind = data.get("kind")
    if kind not in (KIND_SHOW, KIND_EVERYTHING):
        raise PackageError(f"Its {MANIFEST} doesn't say what it holds, so it can't be opened.")
    shows = data.get("shows")
    if not isinstance(shows, list) or len(shows) > MAX_SHOWS or (kind == KIND_SHOW and len(shows) != 1):
        raise PackageError(f"Its {MANIFEST} has a damaged list of shows, so it can't be opened.")
    listed: list[dict[str, str]] = []
    for item in shows:
        place = item.get("path") if isinstance(item, dict) else None
        if not isinstance(place, str) or not _SHOW_PATH.match(place) or any(place == s["path"] for s in listed):
            raise PackageError(f"Its {MANIFEST} has a damaged list of shows, so it can't be opened.")
        listed.append({"path": place, "name": clean_name(_text(item.get("name"))), "id": _text(item.get("id"), 64),
                       "folder": _text(item.get("folder"))})
    return {"kind": kind, "made": _text(data.get("made"), 40), "media": data.get("media") is not False, "shows": listed}


def _read_show(archive: zipfile.ZipFile, members: Mapping[str, zipfile.ZipInfo], index: int,
               listed: Mapping[str, str]) -> PackagedShow:
    """One show's files (by their names in the show folder), checked as when a show opens, plus the AI checks."""
    label = listed["name"] or f"Show {index + 1}"
    known_id = listed["id"] if is_safe_id(listed["id"]) else slugify(listed["folder"] or label, "show")
    base = {"index": index, "name": label, "id": known_id, "folder": listed["folder"]}
    info = members.get(SHOW_FILE)
    if info is None:
        return PackagedShow(**base, refused="it has no show.json")
    try:
        data = _read_json(archive, info)
        if not isinstance(data, dict):
            raise ShowError("show.json must be a JSON object")
        data, read_only = migrate_show(data)
        if read_only:
            raise ShowError("it was made by a newer CentraLight: update CentraLight on this PC to import it")
        show, warnings = show_from_dict(data)
    except (ShowError, InputFileError) as exc:
        return PackagedShow(**base, refused=f"its show.json can't be used: {exc}")
    except Exception as exc:  # (a value of a type nothing expected)
        return PackagedShow(**base, refused=f"its show.json can't be used ({type(exc).__name__}: {exc})")
    base["name"] = clean_name(show.name) or label
    problems: list[str] = []
    notes = [f"show.json: {w}" for w in warnings]
    inputs: dict[str, Input] = {}
    layouts: list[Layout] = []
    automations: dict[str, Automation] = {}
    files: list[PackedFile] = []
    links: dict[str, Any] | None = None
    for rel, info in sorted(members.items()):
        where = _where(rel)
        role = _role(rel)
        if role == "show":
            continue
        if not role:
            notes.append(f"{where} isn't one of a show's files, so it was left out")
            continue
        if len(rel) > MAX_NAME_CHARS:
            notes.append(f"{where}: its name is too long, so it was left out")
            continue
        stem = PurePosixPath(rel).stem
        try:
            if role == "input":
                inp, input_warnings = input_from_dict(_read_json(archive, info), expected_id=stem)
                trouble, more = input_problems(inp, show)
                if trouble:
                    problems.append(f"{where} was left out: " + "; ".join(trouble))
                    continue
                inputs[inp.id] = inp
                notes += [f"{where}: {w}" for w in input_warnings + more]
            elif role == "layout":
                layout, layout_warnings = layout_from_dict(_read_json(archive, info), expected_id=stem)
                layouts.append(layout)
                notes += [f"{where}: {w}" for w in layout_warnings]
            elif role == "automation":
                data = _read_json(archive, info)
                automation, messages = automation_from_dict(data, expected_id=stem, scope="show")
                (problems if _cut_back(data, automation) else notes).extend(f"{where}: {m}" for m in messages)
                automations[automation.id] = automation
            elif role == "links":
                links, dropped = _checked_links(_read_json(archive, info))
                if dropped:
                    notes.append(f"{where}: {_count(dropped, 'link')} that can't be used were left out")
            else:
                limit = MAX_PAINTED_BYTES if role == "painted" else MAX_MEDIA_BYTES
                if info.file_size > limit:
                    problems.append(f"{where} was left out: it's too big (over {size_text(limit)})")
                    continue
                files.append(PackedFile(info.filename, rel, info.file_size, info.CRC))
        except InputFileError as exc:
            problems.append(f"{where} was left out: {exc}")
        except Exception as exc:  # never let one file stop the rest
            problems.append(f"{where} was left out: something in it couldn't be read ({type(exc).__name__})")
    linked = [element.get("original") for inp in inputs.values() for track in inp.tracks for element in track
              if element.get("kind") in ("image", "video")]  # (input_from_dict kept only usable ones)
    linked += [layout.backdrop_original for layout in layouts]
    linked += [stamp["original"] for stamp in (links or {}).get("copies", {}).values()]
    originals: dict[str, str] = {}
    for original in linked:
        if isinstance(original, str) and original:
            originals.setdefault(link_key(original), original)
    return PackagedShow(**base, show=show, inputs=frozen_map(inputs), layouts=tuple(layouts),
                        automations=frozen_map(automations), files=tuple(files), links=links,
                        originals=tuple(sorted(originals.values(), key=str.casefold)), problems=tuple(problems),
                        notes=tuple(notes))


def _expressions(value: Any, where: str = "") -> Iterator[tuple[str, str]]:
    """(where, text) for every expression anywhere in some input data: every {"expr": "..."}."""
    if isinstance(value, Mapping):
        if isinstance(value.get("expr"), str):
            yield where, value["expr"]
        for key, inner in value.items():
            if key != "expr":
                yield from _expressions(inner, f"{where}.{key}" if where else str(key))
    elif isinstance(value, (list, tuple)):
        for index, inner in enumerate(value):
            yield from _expressions(inner, f"{where}[{index}]")


def _named_files(element: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
    """The settings of an element that name a file in the show folder."""
    if element.get("kind") in ("image", "video"):
        yield "file", element.get("file")
    yield "painted strokes", element.get("strokes")
    mask = element.get("mask")
    if isinstance(mask, Mapping):
        yield "painted mask", mask.get("paint")


def input_problems(inp: Input, show: Show) -> tuple[list[str], list[str]]:
    """The checks an input from an AI gets, for an input in a .clshow file: (problems that leave it out, notes).
    Every expression must compile in the sandboxed expression checker, and every file it names must be inside the
    show folder. A variable or house light the show doesn't have is only a note, as CentraLight opens such inputs."""
    problems: list[str] = []
    notes: list[str] = []
    names = {str(item.get("name")) for item in inp.locals} | {g.name for g in show.globals} | SPECIAL_NAMES
    places: list[tuple[str, Any]] = [("its local variables", inp.locals), ("its house lights", inp.house)]
    for number, track in enumerate(inp.tracks, start=1):
        for element in track:
            label = f"track {number}, {element.get('kind')} {element.get('id', '')}".strip()
            places.append((label, element))
            for setting, value in _named_files(element):
                if isinstance(value, str) and value.strip() and not safe_relative_file(value):
                    problems.append(f"{label}: its {setting} {_shown(value)} isn't a file inside the show folder")
    found = [(f"{label}, {where}" if where else label, text) for label, value in places for where, text in _expressions(value)]
    if len(found) > MAX_EXPRESSIONS:
        return problems + [f"it has {len(found):,} expressions, far more than any input needs"], notes
    for place, text in found:
        try:
            compiled = compile_expression(text)
        except ExpressionError as exc:
            problems.append(f"{place}: the expression {_shown(text)} — {exc}")
            continue
        except Exception as exc:  # (anything odd in a file from elsewhere is a problem, never a crash)
            problems.append(f"{place}: the expression {_shown(text)} can't be checked ({type(exc).__name__})")
            continue
        missing = sorted(compiled.names - names)
        if missing:
            notes.append(f'{place}: the expression {_shown(text)} uses "{missing[0]}", which isn\'t a variable in '
                         "this show")
    houses = {h.fixture for h in show.house}
    for fixture_id in inp.house:
        if fixture_id not in houses:
            notes.append(f'its house light "{fixture_id}" isn\'t a house light in this show, so it does nothing')
    return problems, notes


def _cut_back(data: Any, automation: Automation) -> bool:
    """Did checking an automation switch it off, or leave out any of its actions? (Then its messages are problems.)"""
    if not isinstance(data, dict):
        return False
    switched_off = data.get("enabled", True) is True and not automation.enabled
    actions, on_end = data.get("actions") or [], data.get("on_end") or []
    return switched_off or len(automation.actions) < len(actions) or len(automation.on_end) < len(on_end)


def _checked_links(data: Any) -> tuple[dict[str, Any], int]:
    """media\\links.json, checked (as links.read_stamps does, plus each original's text): (the data to write, how many
    links were left out). Originals are only ever text here."""
    copies = data.get("copies") if isinstance(data, dict) and data.get("format") == FORMAT_LINKS else None
    if not isinstance(copies, dict):
        raise InputFileError("it isn't CentraLight's list of where its copies came from")
    kept: dict[str, dict[str, Any]] = {}
    for file, stamp in copies.items():
        ok = (isinstance(file, str) and safe_media_file(file) == file and isinstance(stamp, dict)
              and not original_problem(stamp.get("original"))
              and all(isinstance(stamp.get(k), int) and not isinstance(stamp.get(k), bool) for k in ("size", "modified")))
        if ok:
            kept[file] = {"original": stamp["original"], "size": stamp["size"], "modified": stamp["modified"]}
    return {"format": FORMAT_LINKS, "version": 1, "copies": kept}, len(copies) - len(kept)


def _read_globals(archive: zipfile.ZipFile, files: Mapping[str, zipfile.ZipInfo]) -> PackagedGlobals | None:
    prefix = f"{GLOBALS_DIR}/"
    members = {name[len(prefix):]: info for name, info in files.items() if name.startswith(prefix)}
    if not members:
        return None
    automations: dict[str, Automation] = {}
    order: tuple[str, ...] = ()
    problems: list[str] = []
    notes: list[str] = []
    for rel, info in sorted(members.items()):
        where = f"{GLOBAL_DIR}\\{_where(rel)}"
        match = _ID_FILE.match(rel)
        try:
            if rel == ORDER_FILE:
                data = _read_json(archive, info)
                raw = data.get("order") if isinstance(data, dict) else None
                order = tuple(item for item in raw if isinstance(item, str)) if isinstance(raw, list) else ()
            elif match and is_safe_id(match.group(1)):
                data = _read_json(archive, info)
                automation, messages = automation_from_dict(data, expected_id=match.group(1), scope="global")
                (problems if _cut_back(data, automation) else notes).extend(f"{where}: {m}" for m in messages)
                automations[automation.id] = automation
            else:
                notes.append(f"{where} isn't a global automation, so it was left out")
        except InputFileError as exc:
            problems.append(f"{where} was left out: {exc}")
        except Exception as exc:
            problems.append(f"{where} was left out: something in it couldn't be read ({type(exc).__name__})")
    return PackagedGlobals(frozen_map(automations), order, tuple(problems), tuple(notes))


def _read_settings(archive: zipfile.ZipFile, files: Mapping[str, zipfile.ZipInfo],
                   problems: list[str]) -> PackagedSettings | None:
    info = files.get(SETTINGS_FILE)
    if info is None:
        return None
    try:
        data = _read_json(archive, info)
        if not isinstance(data, dict):
            raise InputFileError("it must be a JSON object")
        settings = Settings()
        settings.update(data)  # (unknown settings ignored, bad values fixed, as when CentraLight starts)
    except InputFileError as exc:
        problems.append(f"{SETTINGS_FILE} was left out: {exc}")
        return None
    except Exception as exc:
        problems.append(f"{SETTINGS_FILE} was left out: something in it couldn't be read ({type(exc).__name__})")
        return None
    return PackagedSettings(settings, ("The USB port, the show folder and the window's size and place stay as they "
                                       "are on this PC.",))


# ---------------------------------------------------------------- importing


def import_show(package: Package, which: int | PackagedShow, shows_root: str | Path, *, name: str,
                replace_folder: str | Path | None = None, loaded: str | Path = "",
                trash: Callable[[Path], None] | None = None, progress: Progress | None = None,
                notes: list[str] | None = None) -> Path:
    """Import one checked show as a new show folder; returns it. It isn't loaded.

    - name: its name here (for "Keep both", keep_both_name() gives "Morning (2)"). With its own name, it gets the
      folder name it had where it was exported if that's free, so its id (Stream Deck keys, "Load a show") still works.
    - replace_folder: "Replace" that show (a folder in shows_root, not the loaded one). The import takes its folder
      name, so its id stays; the old folder goes to the Recycle Bin (trash; tests give their own) only once the new
      one is complete. It's renamed "Morning (replaced <date>)" first, so putting it back never mixes the two.
    - notes: if given, anything worth telling (the old show couldn't go to the Recycle Bin) is added to it.

    Everything is written into a hidden folder first and renamed into place at the end: if anything fails, that folder
    is removed and nothing has changed. Raises PackageError with a reason."""
    item = package.shows[which] if isinstance(which, int) else which
    if not item.ok:
        raise PackageError(f"“{item.name}” can't be imported: {item.refused or 'it has no show.json'}.")
    root = Path(shows_root)
    name = clean_name(name) or item.name
    old = _replaceable(root, Path(replace_folder), loaded) if replace_folder is not None else None
    written = _show_files(item, name)
    needed = sum(len(data) for _rel, data in written) + sum(f.size for f in item.files)
    root.mkdir(parents=True, exist_ok=True)
    _check_space(root, needed)
    temp = _hidden_folder(root)
    try:
        done = 0
        for rel, data in written:
            _write_new(temp, rel, data)
            done += len(data)
        if item.files:
            _copy_files(package, item, temp, progress, done, needed)
        if progress is not None:
            progress(needed, needed)
        if old is not None:
            folder = _swap_in(temp, old, name, trash or to_recycle_bin, notes)
        else:
            folder = _move_in(temp, root, item, name)
    except PackageError:
        _remove(temp)
        raise
    except OSError as exc:
        _remove(temp)
        raise PackageError(f"“{name}” couldn't be imported ({exc.strerror or exc}). Nothing was changed.") from exc
    except BaseException:
        _remove(temp)
        raise
    log_info(f"Show “{name}” imported from {package.path.name} into {folder}"
             + (", replacing the old one" if old is not None else ""))
    return folder


def _show_files(item: PackagedShow, name: str) -> list[tuple[str, bytes]]:
    """The show's JSON files, written afresh from the checked data (so anything the checks left out stays out)."""
    assert item.show is not None
    show = replace(item.show, name=name, input_order=tuple(i for i in item.show.input_order if i in item.inputs))
    show = show.with_automations(item.automations, tuple(i for i in item.show.automation_order if i in item.automations))
    files = [(SHOW_FILE, _json_bytes(show_to_dict(show)))]
    files += [(f"{INPUTS_DIR}/{inp.id}.json", _json_bytes(input_to_dict(inp))) for inp in item.inputs.values()]
    files += [(f"{LAYOUTS_DIR}/{layout.id}.json", _json_bytes(layout_to_dict(layout))) for layout in item.layouts]
    files += [(f"{AUTOMATIONS_DIR}/{a.id}.json", _json_bytes(automation_to_dict(a))) for a in item.automations.values()]
    if item.links is not None:
        files.append((f"{MEDIA_DIR}/{LINKS_FILE}", _json_bytes(item.links)))
    return files


def _json_bytes(data: Any) -> bytes:
    return json_text(data).encode("utf-8")


def _replaceable(root: Path, old: Path, loaded: str | Path) -> Path:
    """The show folder "Replace" would replace, if it may be: a show directly in the shows folder, not the loaded one."""
    if same_folder(old, root) or not same_folder(old.parent, root) or old.name.startswith("_"):
        raise PackageError(f"{old} isn't a show in {root}, so it can't be replaced.")
    if loaded and same_folder(old, loaded):
        raise PackageError(f"“{old.name}” is the loaded show, so it can't be replaced. Load another show first, or "
                           "choose Keep both.")
    if not (old / SHOW_FILE).is_file():
        raise PackageError(f"{old.name} isn't a show, so it can't be replaced.")
    return old


def _check_space(root: Path, needed: int) -> None:
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        return
    if free < needed + SPARE_SPACE:
        raise PackageError(f"There isn't enough free space on the disk: the show needs {size_text(needed)} (and "
                           f"{size_text(SPARE_SPACE)} to spare), and there's {size_text(free)}.")


def _hidden_folder(root: Path) -> Path:
    """A new, empty folder in the shows folder for an import to be written into. Its name starts with "_", so the
    list of shows leaves it out while it's being filled."""
    for _ in range(100):
        folder = root / f"_Importing {secrets.token_hex(4)}"
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise PackageError("A folder for the import couldn't be made.")


def _remove(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


def _target(folder: Path, rel: str) -> Path:
    """Where a file of the show goes in the folder being written. Its name was checked when the file was read, and
    is checked once more here, as text, before anything touches the disk."""
    if name_problem(rel) or not _role(rel) or len(rel) > MAX_NAME_CHARS:
        raise PackageError(f"{_shown(rel)} can't be written.")
    return folder.joinpath(*rel.split("/"))


def _write_new(folder: Path, rel: str, data: bytes) -> None:
    path = _target(folder, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as handle:  # (a new file: never over another)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _copy_files(package: Package, item: PackagedShow, folder: Path, progress: Progress | None, done: int,
                total: int) -> None:
    """Copy the pictures, videos and painted pictures, checking the file again first (it may have changed since it
    was read), and each file's size while it's copied."""
    try:
        _check_list_size(package.path)
        with zipfile.ZipFile(package.path) as archive:
            files = _checked_entries(archive.infolist())
            for packed in item.files:
                info = files.get(packed.entry)
                if info is None or (info.file_size, info.CRC) != (packed.size, packed.crc):
                    raise PackageError(f"{package.path.name} has changed since it was checked. Open it again.")
                path = _target(folder, packed.name)
                path.parent.mkdir(parents=True, exist_ok=True)
                copied = 0
                with archive.open(info) as source, open(path, "xb") as out:
                    while chunk := source.read(CHUNK):
                        copied += len(chunk)
                        if copied > packed.size:
                            break
                        out.write(chunk)
                        if progress is not None:
                            progress(done + copied, total)
                    out.flush()
                    os.fsync(out.fileno())
                if copied != packed.size:
                    raise PackageError(f"{_where(packed.name)} in {package.path.name} is damaged (it isn't the size the "
                                       "file says), so the show wasn't imported.")
                done += packed.size
    except PackageError:
        raise
    except FileNotFoundError as exc:
        raise PackageError(f"{package.path.name} isn't there any more.") from exc
    except (zipfile.BadZipFile, EOFError, RuntimeError, ValueError) as exc:
        raise PackageError(f"Something in {package.path.name} is damaged ({exc}), so the show wasn't imported.") from exc
    except OSError:
        raise
    except Exception as exc:  # (a decompression error, say)
        raise PackageError(f"Something in {package.path.name} is damaged ({type(exc).__name__}), so the show wasn't "
                           "imported.") from exc


def _rename(source: Path, target: Path, tries: int = 20) -> None:
    """Rename a folder (never onto another one), retrying for a few seconds: antivirus or the search indexer can hold
    a newly written file for a moment."""
    for attempt in range(tries):
        try:
            os.rename(source, target)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(min(0.5, 0.05 * (attempt + 1)))


def _new_location(root: Path, item: PackagedShow, name: str) -> Path:
    """Where a new imported show goes: the folder name it had, if it keeps its name and that's free here (worked out as
    text first, never a device's name), else a new folder for its name."""
    if item.folder and clean_name(name).casefold() == clean_name(item.name).casefold():
        base = folder_name(item.folder)
        here = {entry.casefold() for entry in os.listdir(root)}
        taken = {s.id for s in list_shows(root)} | {slugify(GLOBAL_DIR, "show")}
        if base.casefold() not in here and slugify(base, "show") not in taken:
            return root / base
    return new_folder(root, name)


def _move_in(temp: Path, root: Path, item: PackagedShow, name: str) -> Path:
    target = _new_location(root, item, name)
    try:
        _rename(temp, target)
    except FileExistsError:  # (something made a folder of that name meanwhile)
        target = new_folder(root, name)
        _rename(temp, target)
    return target


def _swap_in(temp: Path, old: Path, name: str, trash: Callable[[Path], None], notes: list[str] | None) -> Path:
    """Put the new show where the old one was (its folder name, so its id stays), then bin the old one."""
    stamp = datetime.now().strftime("%Y-%m-%d %H.%M")
    parked = new_folder(old.parent, f"{old.name[:30]} (replaced {stamp})")
    nothing_changed = "Nothing was changed: is one of its files open in another program?"
    try:
        _rename(old, parked)
    except OSError as exc:
        raise PackageError(f"“{old.name}” couldn't be replaced. {nothing_changed} ({exc.strerror or exc})") from exc
    try:
        _rename(temp, old)
    except OSError as exc:
        try:
            _rename(parked, old)
        except OSError:
            raise PackageError(f"“{old.name}” couldn't be replaced, and the old show is now in the folder "
                               f"{parked.name} ({exc.strerror or exc}).") from exc
        raise PackageError(f"“{old.name}” couldn't be replaced. {nothing_changed} ({exc.strerror or exc})") from exc
    try:
        trash(parked)
    except Exception as exc:  # (the import is done: the old show just stays, under its new folder name)
        message = (f"The old “{name}” couldn't be moved to the Recycle Bin ({exc}), so it's still in the shows folder, "
                   f"as {parked.name}.")
        log_info(message)
        if notes is not None:
            notes.append(message)
    return old


def import_globals(package: Package, global_dir: str | Path, *, replace: bool = False) -> GlobalsImported:
    """Add the global automations from an Export everything file to this PC's (global_dir: shows root /
    "_Global automations"). One with the id of one already here is left as it is, unless replace. They're added at
    the end of the order. The files are written as if by hand, so a running CentraLight picks them up (each one an
    undo step); reload the global automations instead to keep the file's order exactly."""
    part = package.globals
    if part is None:
        raise PackageError("This file has no global automations in it.")
    folder = Path(global_dir)
    folder.mkdir(parents=True, exist_ok=True)
    added: list[str] = []
    replaced: list[str] = []
    kept: list[str] = []
    for automation in part.ordered():
        path = automation_file(folder, automation.id, "")  # (its id was checked: never a device's name)
        if path.exists():
            if not replace:
                kept.append(automation.name)
                continue
            replaced.append(automation.name)
        else:
            added.append(automation.name)
        write_json_atomic(path, automation_to_dict(automation))
        forget_written(path)
    order_path = folder / ORDER_FILE
    order: list[str] = []
    try:
        data = loads_json_bytes(order_path.read_bytes()) if order_path.is_file() else {}
        raw = data.get("order") if isinstance(data, dict) else None
        order = [i for i in raw if isinstance(i, str) and is_safe_id(i)] if isinstance(raw, list) else []
    except (OSError, ValueError):
        pass
    on_disk = {p.stem for p in folder.glob("*.json") if p.name.casefold() != ORDER_FILE.casefold()}
    order = list(dict.fromkeys(order + [a.id for a in part.ordered() if a.id in on_disk]))
    write_json_atomic(order_path, {"format": FORMAT_GLOBAL, "version": GLOBAL_VERSION, "order": order})
    log_info(f"Global automations imported from {package.path.name}: {len(added)} added, {len(replaced)} replaced, "
             f"{len(kept)} already here")
    return GlobalsImported(tuple(added), tuple(replaced), tuple(kept))


# what an imported settings file never changes: this PC's own things (where its shows are, its window), and anything
# that would reach outside CentraLight (the output to the lights, the USB port, starting with Windows, the phone page
# being open to the network, the file type)
KEEP_ON_THIS_PC = ("show_folder", "usb_port", "geometry", "maximized", "splitters", "output", "artnet_ip",
                   "artnet_universe", "full_universe", "start_with_windows", "remote_lan", "remote_key", "remote_port",
                   "audio_source", "clshow_files", "page", "ui")


def import_settings(package: Package, settings_path: str | Path) -> Settings:
    """Replace this PC's settings with the ones in an Export everything file (checked), keeping what belongs to this
    PC: the show folder (a path on the other PC is never looked at), the USB port (it differs between PCs) and the
    window's size and place. Returns the settings now in the file. A running CentraLight keeps its own copy and saves
    it, so give it these (or restart)."""
    part = package.settings
    if part is None:
        raise PackageError("This file has no settings in it.")
    path = Path(settings_path)
    current = Settings.load(path)
    settings = Settings()
    settings.update(part.settings.to_dict())  # (checked again: a fresh copy)
    for name in KEEP_ON_THIS_PC:
        if hasattr(settings, name):
            setattr(settings, name, getattr(current, name))
    settings.save(path)
    log_info(f"Settings imported from {package.path.name}")
    return settings
