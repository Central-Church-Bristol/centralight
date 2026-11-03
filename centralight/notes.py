"""Where each light is: fixture_map.csv next to main.py.

It's the same file, in the same format, as usb_dmx_patch_test.py writes (name, group,
start_ch, channels, profile, where), so notes made in either place show up in both.
Notes are matched to lights by name.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .files import write_atomic
from .model import Show
from .paths import ROOT

NOTES_FILE = ROOT / "fixture_map.csv"
COLUMNS = ["name", "group", "start_ch", "channels", "profile", "where"]


def load_notes(path: Path = NOTES_FILE) -> dict[str, str]:
    """light name -> where it is."""
    if not path.exists():
        return {}
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return {row.get("name", ""): row.get("where", "") or "" for row in csv.DictReader(handle) if row.get("name")}
    except (OSError, csv.Error, UnicodeDecodeError):
        return {}


def save_notes(show: Show, notes: dict[str, str], path: Path = NOTES_FILE) -> None:
    """Write every light, with its note, keeping notes for names not in this show."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    written = set()
    for fx in show.fixtures:
        writer.writerow([fx.name, fx.group, fx.address, fx.channels, fx.profile, notes.get(fx.name, "")])
        written.add(fx.name)
    for name, where in notes.items():
        if name not in written and where:
            writer.writerow([name, "", "", "", "", where])
    # utf-8-sig so Excel shows any accents properly (like the patch test)
    write_atomic(path, buffer.getvalue().encode("utf-8-sig"))
