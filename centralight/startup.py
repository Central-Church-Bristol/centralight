"""Start with Windows: an entry under HKCU\\...\\CurrentVersion\\Run that starts CentraLight in
the tray when you sign in (the same mechanism Media Background Pane uses).

Only the real app touches the real entry. Copies started by the tests or tools/soak.py run with
CENTRALIGHT_INSTANCE set and leave it alone (see managed()).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CentraLight"
ROOT = Path(__file__).resolve().parents[1]


def managed() -> bool:
    """True if this copy of the app may change the real "start with Windows" entry."""
    return not os.environ.get("CENTRALIGHT_INSTANCE") and sys.platform == "win32"


def launch_command() -> str:
    """What Windows runs at sign-in: the app's pythonw.exe with main.py, minimised to the tray."""
    venv = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    python = venv if venv.is_file() else Path(sys.executable).with_name("pythonw.exe")
    if not python.is_file():
        python = Path(sys.executable)
    return f'"{python}" "{ROOT / "main.py"}" --minimized'


def is_enabled(key_path: str = RUN_KEY, value_name: str = VALUE_NAME) -> bool:
    return read_command(key_path, value_name) != ""


def read_command(key_path: str = RUN_KEY, value_name: str = VALUE_NAME) -> str:
    """The command in the entry ('' if there isn't one)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
            return str(value)
    except OSError:
        return ""


def set_enabled(on: bool, key_path: str = RUN_KEY, value_name: str = VALUE_NAME, command: str | None = None) -> None:
    """Add (or update) the entry, or remove it. Raises OSError if Windows says no."""
    import winreg

    if on:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command or launch_command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except FileNotFoundError:
        pass


def sync(on: bool, key_path: str = RUN_KEY, value_name: str = VALUE_NAME) -> str:
    """Make the entry match the setting (also fixing an out-of-date path). Returns '' or what went wrong."""
    try:
        wanted = launch_command()
        current = read_command(key_path, value_name)
        if on and current != wanted:
            set_enabled(True, key_path, value_name, wanted)
        elif not on and current:
            set_enabled(False, key_path, value_name)
    except OSError as exc:
        return f"Windows wouldn't change the start-up setting ({exc.strerror or exc})"
    return ""
