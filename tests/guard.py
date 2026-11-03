"""Safety net for tests: no real hardware, no real user files.

- serial.Serial and Art-Net are replaced with functions that raise, so no test can
  ever open COM3 or send DMX to the real lights.
- CENTRALIGHT_APPDATA and CENTRALIGHT_SHOWS point at a temporary folder, so settings,
  logs, live state and shows never touch %LOCALAPPDATA% or Documents.
- CENTRALIGHT_INSTANCE marks this as a test copy: it never changes the real "start with
  Windows" entry (startup.managed()), and its second-launch ping has its own name.

tests/__init__.py calls install(); scripts that tests run in a subprocess call it too.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_installed = False


def _refuse(*_args, **_kwargs):
    raise RuntimeError("Tests must never open a real serial port or send Art-Net (see tests/guard.py).")


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import serial

    serial.Serial = _refuse  # type: ignore[assignment]
    import dmx_output

    dmx_output.StupidArtnet = _refuse  # type: ignore[assignment]

    if not os.environ.get("CENTRALIGHT_TEST_HOME"):
        home = Path(tempfile.mkdtemp(prefix="centralight-tests-"))
        os.environ["CENTRALIGHT_TEST_HOME"] = str(home)
        atexit.register(shutil.rmtree, home, True)
    home = Path(os.environ["CENTRALIGHT_TEST_HOME"])
    os.environ["CENTRALIGHT_APPDATA"] = str(home / "appdata")
    os.environ["CENTRALIGHT_SHOWS"] = str(home / "shows")
    os.environ.setdefault("CENTRALIGHT_INSTANCE", "tests")
