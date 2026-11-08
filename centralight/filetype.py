"""Opening .clshow files with CentraLight: Windows shows them with CentraLight's document icon (the logo's circles on
a page), and double-clicking one starts CentraLight, or brings the running one forward, to import it.

It's registered for this Windows user only, under HKEY_CURRENT_USER\\Software\\Classes, so no administrator is needed:
- .clshow -> CentraLight.Show
- CentraLight.Show: "CentraLight show", its DefaultIcon (launcher\\clshow.ico) and shell\\open\\command (the same
  CentraLight.exe that starts CentraLight at sign-in, with the file: "%1")

sync() runs at start: it writes only what differs, so a CentraLight folder that has moved is put right, and tells
Explorer only when something changed. Only the real app should call it (startup.managed()): copies run by the tests
and tools/soak.py leave the real entries alone, and the tests use a stand-in key.

Unregistering removes only what this module wrote: a .clshow that has since been given to another program is left as
it is.
"""

from __future__ import annotations

from pathlib import Path

try:
    import winreg
except ImportError:  # (not Windows: there's nothing to register)
    winreg = None  # type: ignore[assignment]

from .startup import ROOT, launch_command

EXTENSION = ".clshow"
PROG_ID = "CentraLight.Show"
TYPE_NAME = "CentraLight show"
BASE = r"Software\Classes"  # under HKEY_CURRENT_USER: this Windows user only
ICON = ROOT / "launcher" / "clshow.ico"
HKEY_CURRENT_USER = 0x80000001  # (winreg.HKEY_CURRENT_USER)
SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000


def open_command() -> str:
    """What Windows runs to open a .clshow file: CentraLight.exe, as at sign-in but not in the tray, with the file."""
    command = launch_command()
    if command.endswith(" --minimized"):
        command = command[: -len(" --minimized")]
    return f'{command} "%1"'


def _place(root_key: int | None, base: str | None) -> tuple[int, str]:
    return (HKEY_CURRENT_USER if root_key is None else root_key), (BASE if base is None else base)


def _wanted(command: str, icon: str | Path) -> dict[str, str]:
    """Each key this module writes (under the base) and the text it holds, in the order they're written: .clshow
    last, so it never points at a CentraLight.Show that isn't complete."""
    return {
        PROG_ID: TYPE_NAME,
        rf"{PROG_ID}\DefaultIcon": str(icon),
        rf"{PROG_ID}\shell\open\command": command,
        EXTENSION: PROG_ID,
    }


def _read(root_key: int, path: str) -> str | None:
    """A key's text (its default value), or None if there isn't one."""
    try:
        with winreg.OpenKey(root_key, path, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, "")
    except OSError:
        return None
    return value if isinstance(value, str) else None


def _exists(root_key: int, path: str) -> bool:
    try:
        with winreg.OpenKey(root_key, path, 0, winreg.KEY_READ):
            return True
    except OSError:
        return False


def _delete_tree(root_key: int, path: str) -> None:
    """Delete a key and everything under it (Windows only deletes keys that are empty)."""
    with winreg.OpenKey(root_key, path, 0, winreg.KEY_READ) as key:
        children = []
        while True:
            try:
                children.append(winreg.EnumKey(key, len(children)))
            except OSError:
                break
    for child in children:
        _delete_tree(root_key, rf"{path}\{child}")
    winreg.DeleteKey(root_key, path)


def _delete_if_empty(root_key: int, path: str) -> None:
    try:
        with winreg.OpenKey(root_key, path, 0, winreg.KEY_READ) as key:
            subkeys, values, _changed = winreg.QueryInfoKey(key)
        if subkeys == 0 and values == 0:
            winreg.DeleteKey(root_key, path)
    except OSError:
        pass


def _notify() -> None:
    """Tell Explorer that file types have changed, so it shows the icon now rather than after a restart."""
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except (AttributeError, OSError):
        pass


def register(command: str, icon: str | Path, *, root_key: int | None = None, base: str | None = None,
             notify: bool = True) -> bool:
    """Open .clshow files with this command, shown with this icon. Writes only what differs; returns True if it
    changed anything (and then, with notify, tells Explorer). Raises OSError if Windows says no."""
    root_key, base = _place(root_key, base)
    changed = False
    for path, text in _wanted(command, icon).items():
        if _read(root_key, rf"{base}\{path}") != text:
            with winreg.CreateKeyEx(root_key, rf"{base}\{path}", 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, text)
            changed = True
    if changed and notify:
        _notify()
    return changed


def unregister(*, root_key: int | None = None, base: str | None = None, notify: bool = True) -> bool:
    """Undo register(): CentraLight.Show goes, and .clshow's link to it (the key too, if nothing else is in it). A
    .clshow that now belongs to another program is left alone. Returns True if it changed anything."""
    root_key, base = _place(root_key, base)
    changed = False
    extension = rf"{base}\{EXTENSION}"
    if _read(root_key, extension) == PROG_ID:
        with winreg.OpenKey(root_key, extension, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "")
        _delete_if_empty(root_key, extension)
        changed = True
    if _exists(root_key, rf"{base}\{PROG_ID}"):
        _delete_tree(root_key, rf"{base}\{PROG_ID}")
        changed = True
    if changed and notify:
        _notify()
    return changed


def registered_command(*, root_key: int | None = None, base: str | None = None) -> str:
    """The command that opens .clshow files, if they open with CentraLight.Show ("" if not, or if they've been given
    to another program)."""
    if winreg is None:
        return ""
    root_key, base = _place(root_key, base)
    if _read(root_key, rf"{base}\{EXTENSION}") != PROG_ID:
        return ""
    return _read(root_key, rf"{base}\{PROG_ID}\shell\open\command") or ""


def sync(enabled: bool, command: str | None = None, icon: str | Path | None = None, *, root_key: int | None = None,
         base: str | None = None, notify: bool = True) -> str:
    """Make the registration match Settings → "Open .clshow files with CentraLight", also putting right a path that's
    out of date (CentraLight's folder moved). Writes only what differs. Returns "" or what went wrong, for the log."""
    if winreg is None:
        return ""
    try:
        if enabled:
            register(command or open_command(), icon or ICON, root_key=root_key, base=base, notify=notify)
        else:
            unregister(root_key=root_key, base=base, notify=notify)
    except OSError as exc:
        return f"Windows wouldn't change how .clshow files open ({exc.strerror or exc})"
    return ""
