"""Where CentraLight keeps its files.

- App settings, live state and the session log: ``%LOCALAPPDATA%\\CentraLight``
- Shows: ``Documents\\CentraLight Shows\\<show name>``
- Brand fonts: the ``fonts`` folder next to ``main.py`` (``run.bat`` copies them there)

Tests and trial runs point these somewhere else with the ``CENTRALIGHT_APPDATA`` and
``CENTRALIGHT_SHOWS`` environment variables, so they never touch the real files.
"""

from __future__ import annotations

import ctypes
import os
import uuid
from pathlib import Path

APP_NAME = "CentraLight"
DEFAULT_SHOW_NAME = "Central Church"
ROOT = Path(__file__).resolve().parents[1]  # the CentraLight folder (main.py lives here)

_FOLDERID_DOCUMENTS = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\CentraLight (created if missing)."""
    override = os.environ.get("CENTRALIGHT_APPDATA")
    if override:
        root = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) / APP_NAME if local else Path.home() / "AppData" / "Local" / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def live_state_path() -> Path:
    return app_data_dir() / "live.json"


def session_log_path() -> Path:
    return app_data_dir() / "session.log"


def documents_dir() -> Path:
    """The Windows Documents folder (wherever Windows says it is)."""
    try:
        class _Guid(ctypes.Structure):
            _fields_ = [("bytes", ctypes.c_ubyte * 16)]

        guid = _Guid.from_buffer_copy(_FOLDERID_DOCUMENTS.bytes_le)
        path_ptr = ctypes.c_wchar_p()
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)) == 0:
            try:
                if path_ptr.value:
                    return Path(path_ptr.value)
            finally:
                ole32.CoTaskMemFree(path_ptr)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


def shows_root() -> Path:
    override = os.environ.get("CENTRALIGHT_SHOWS")
    return Path(override) if override else documents_dir() / "CentraLight Shows"


def default_show_dir() -> Path:
    return shows_root() / DEFAULT_SHOW_NAME


def fonts_dir() -> Path:
    return ROOT / "fonts"


def logo_path() -> Path:
    return ROOT / "Assets" / "CentraLight Logo 1024.png"
