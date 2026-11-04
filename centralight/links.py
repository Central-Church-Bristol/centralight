"""Links to original pictures and videos (M13).

An Image or Video element (and a layout's room photo) can remember its **original** file, wherever it is: a Google
Drive folder, Pictures, Videos. CentraLight never plays the original: it plays its own copy in the show's media
folder, so a slow or offline drive can't touch playback. A background thread (link_worker.py) checks each original's
size and modified time and copies a changed one in.

This module holds the rules that need no disk at all, plus two small files:
- original_problem(): is a link usable? Checked as plain text, before anything looks at the disk: a name Windows keeps
  for a device ("COM3", "aux.jpg") opens that device even when only asked whether it exists, and COM3 is the
  Eurolite's port.
- AllowedLinks: the originals someone at this PC has chosen (Choose…, Allow, an import summary or the AI paste
  dialog). A link is followed only once it's here, so a link an AI tool wrote, or one inside an imported .clshow,
  waits for that OK (network paths too: \\\\server\\... would make Windows connect to that server). Kept in app data,
  never in a show, so a show (or an AI tool) can't allow its own links.
- media\\links.json in each show: for each copy CentraLight made, the original it came from and that original's size
  and modified time then (so a change is noticed without reading the original). Only link_worker.py writes it.
"""

from __future__ import annotations

import ntpath
import re
import threading
from pathlib import Path
from typing import Any

from .files import read_json, write_json_atomic
from .log import log_exception
from .model import is_device_name

PICTURE_TYPES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv"}
LINKS_FILE = "links.json"  # in a show's media folder
FORMAT_LINKS = "centralight-media-links"
ALLOWED_FILE = "allowed_links.json"  # in app data
FORMAT_ALLOWED = "centralight-allowed-links"
ALLOWED_LIMIT = 5000  # originals remembered (the oldest are forgotten first)
PATH_LIMIT = 1000  # characters in a link
_DRIVE = re.compile(r"^[A-Za-z]:\\")
_UNC = re.compile(r"^\\\\[^\\]+\\[^\\]+")
_stamps_lock = threading.Lock()


def media_types(kind: str) -> set[str]:
    """The file endings an element of this kind can link to ("image" or "video")."""
    return PICTURE_TYPES if kind == "image" else VIDEO_TYPES


def _windows_text(text: str) -> str:
    return text.strip().replace("/", "\\")


def original_problem(original: Any, kind: str = "") -> str:
    """Why a link can't be used ("" = it can), from its text alone: nothing here touches the disk. kind: "image" or
    "video" also checks the file ending."""
    if not isinstance(original, str) or not original.strip():
        return "there's no file"
    if len(original) > PATH_LIMIT:
        return "the path is too long"
    if any(ord(ch) < 32 for ch in original):
        return "the path has a control character in it"
    text = _windows_text(original)
    if text.startswith(("\\\\?\\", "\\\\.\\")):
        return "it's a special Windows path, not a file"
    if not (_DRIVE.match(text) or _UNC.match(text)):
        return "it isn't a full path (like C:\\Pictures\\candles.jpg)"
    if any(ch in text for ch in '*?"<>|') or ":" in text[2:]:
        return "the path has a character a file name can't have"
    parts = [p for p in text.split("\\") if p] + [p for p in ntpath.normpath(text).split("\\") if p]
    if any(is_device_name(part) for part in parts):
        return "a part of it is a name Windows keeps for a device (like COM3), so it's never opened"
    if kind and ntpath.splitext(text)[1].lower() not in media_types(kind):
        wanted = "picture" if kind == "image" else "video"
        return f"it isn't a {wanted} file ({', '.join(sorted(media_types(kind)))})"
    return ""


def is_network_path(original: str) -> bool:
    """\\\\server\\share\\... (Windows would connect to that server just to look at it)."""
    return isinstance(original, str) and _windows_text(original).startswith("\\\\")


def link_key(original: str) -> str:
    """The same original however it's written ("G:/Drive/a.jpg" and "g:\\drive\\A.JPG")."""
    return ntpath.normcase(ntpath.normpath(_windows_text(original)))


def file_name(original: str) -> str:
    """The original's file name, for messages ("candles.mp4")."""
    return ntpath.basename(_windows_text(original)) if isinstance(original, str) else ""


# ---------------------------------------------------------------- who chose which originals


class AllowedLinks:
    """The originals someone at this PC has chosen (app data, never a show). Safe to use from any thread."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from .paths import app_data_dir

            path = app_data_dir() / ALLOWED_FILE
        self.path = Path(path)
        self._lock = threading.Lock()
        self._keys: dict[str, None] = {}  # in the order they were allowed (oldest first)
        try:
            data = read_json(self.path) if self.path.is_file() else {}
        except (OSError, ValueError) as exc:
            log_exception("reading the list of allowed links", exc)
            data = {}
        items = data.get("allowed", []) if isinstance(data, dict) and data.get("format") == FORMAT_ALLOWED else []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str) and not original_problem(item):
                self._keys[link_key(item)] = None

    def allowed(self, original: Any) -> bool:
        if original_problem(original):
            return False
        with self._lock:
            return link_key(original) in self._keys

    def allow(self, originals) -> list[str]:
        """Allow these originals (those that can be used at all). Returns the ones newly allowed."""
        added: list[str] = []
        with self._lock:
            for original in originals:
                if original_problem(original):
                    continue
                key = link_key(original)
                if key in self._keys:
                    self._keys.pop(key)  # (to the newest end)
                else:
                    added.append(original)
                self._keys[key] = None
            while len(self._keys) > ALLOWED_LIMIT:
                self._keys.pop(next(iter(self._keys)))
            keys = list(self._keys)
        try:
            write_json_atomic(self.path, {"format": FORMAT_ALLOWED, "version": 1, "allowed": keys})
        except OSError as exc:
            log_exception("saving the list of allowed links", exc)
        return added


# ---------------------------------------------------------------- media\links.json


def safe_media_file(file: Any) -> str:
    """A copy's name if it's a file in the show's media folder ("media/candles.mp4"), else "" (a name from a show's files
    is checked as text before it's ever looked for: never outside the show, never a device's name, and never a name
    Windows would take for another file: "candles.jpg." and "CANDLE~1.JPG" are both candles.jpg)."""
    from .show_io import safe_relative_file

    text = safe_relative_file(file)
    if not text.startswith("media/") or text.count("/") != 1:
        return ""
    name = text.split("/", 1)[1]
    if not name or name != name.strip() or name.endswith(".") or "~" in name:
        return ""
    return text


def stamps_path(folder: str | Path) -> Path:
    return Path(folder) / "media" / LINKS_FILE


def read_stamps(folder: str | Path, strict: bool = False) -> dict[str, dict[str, Any]]:
    """{copy ("media/candles.mp4"): {"original", "size", "modified", "made"}} for the copies CentraLight made in a show.
    strict: a file that's there but can't be read (locked for a moment, damaged) raises OSError instead of reading as
    empty (so update_stamps never writes over it with nearly nothing)."""
    try:
        data = read_json(stamps_path(folder))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if strict:
            raise OSError(f"media\\{LINKS_FILE} couldn't be read ({exc})") from exc
        return {}
    copies = data.get("copies") if isinstance(data, dict) and data.get("format") == FORMAT_LINKS else None
    result: dict[str, dict[str, Any]] = {}
    for file, stamp in (copies.items() if isinstance(copies, dict) else ()):
        if (safe_media_file(file) == file and isinstance(stamp, dict) and isinstance(stamp.get("original"), str)
                and isinstance(stamp.get("size"), int) and isinstance(stamp.get("modified"), int)):
            made = stamp.get("made", 0)
            result[file] = {"original": stamp["original"], "size": stamp["size"], "modified": stamp["modified"],
                            "made": float(made) if isinstance(made, (int, float)) and not isinstance(made, bool) else 0.0}
    return result


def update_stamps(folder: str | Path, change) -> dict[str, dict[str, Any]]:
    """Read media\\links.json, let change(stamps) edit it, and write it back (one at a time). Raises OSError, and
    writes nothing, if the file is there but can't be read."""
    with _stamps_lock:
        stamps = read_stamps(folder, strict=True)
        before = {k: dict(v) for k, v in stamps.items()}
        change(stamps)
        if stamps != before:
            write_json_atomic(stamps_path(folder), {"format": FORMAT_LINKS, "version": 1, "copies": stamps})
        return stamps


def carry_record(from_folder: str | Path, file: str, to_folder: str | Path, new_file: str) -> None:
    """A copy taken to another show (a new show's room photo, an imported layout's) keeps its record of where it came
    from, so its original isn't copied in again at the next check."""
    stamp = read_stamps(from_folder).get(file)
    if stamp is not None and safe_media_file(new_file):
        try:
            update_stamps(to_folder, lambda stamps: stamps.__setitem__(new_file, dict(stamp)))
        except OSError as exc:
            log_exception("keeping a copy's record", exc)


def made_copies(folder: str | Path) -> set[str]:
    """The files in a show's media folder that CentraLight copied from an original (the only ones it ever tidies)."""
    return set(read_stamps(folder))
