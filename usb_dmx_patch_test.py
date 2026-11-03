r"""
Patch test for the Eurolite USB-DMX512 PRO Cable Interface (no NDI, no MagicQ).

Lights the Central Church rig (the patch in fixtures.py, copied from the MagicQ
show "Central Church.shw"): first every light at once (to count what answers),
then one light at a time so you can note where each one is. Notes are saved to
fixture_map.csv next to this script.

Usage:
  .\.venv\Scripts\python.exe usb_dmx_patch_test.py
  .\.venv\Scripts\python.exe usb_dmx_patch_test.py --port COM3

Keys: Left / Right = previous / next light, A = all on, C = change colour,
N = note where the lit light is, Esc = stop (all lights off).
"""

from __future__ import annotations

import argparse
import csv
import msvcrt
import time
from pathlib import Path

from dmx_output import UsbDmxOutput
from fixtures import PATCH, PROFILES, channel_range, set_fixture

MAP_FILE = Path(__file__).with_name("fixture_map.csv")

COLOURS = [
    ("white", (255, 255, 255)),
    ("red", (255, 0, 0)),
    ("green", (0, 255, 0)),
    ("blue", (0, 0, 255)),
]

# Second byte Windows sends after b"\xe0" / b"\x00" for the arrow keys
ARROWS = {b"K": "left", b"M": "right", b"H": "up", b"P": "down"}


def read_key() -> str | None:
    """Next waiting key as a short name ('left', 'esc', 'a', ...), or None."""
    if not msvcrt.kbhit():
        return None
    ch = msvcrt.getch()
    if ch in (b"\x00", b"\xe0"):
        return ARROWS.get(msvcrt.getch(), "")
    if ch == b"\x1b":
        return "esc"
    return ch.decode("latin-1").lower()


def load_notes() -> dict[str, str]:
    """Where-notes saved by an earlier run, keyed by light name."""
    if not MAP_FILE.exists():
        return {}
    with MAP_FILE.open(newline="", encoding="utf-8-sig") as f:
        return {row.get("name", ""): row.get("where", "") for row in csv.DictReader(f)}


def save_notes(notes: dict[str, str]) -> None:
    """Write the whole patch plus notes to fixture_map.csv."""
    try:
        with MAP_FILE.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "group", "start_ch", "channels", "profile", "where"])
            for fx in PATCH:
                writer.writerow([
                    fx["name"],
                    fx["group"],
                    fx["start_ch"],
                    PROFILES[fx["profile"]]["channels"],
                    fx["profile"],
                    notes.get(fx["name"], ""),
                ])
    except OSError as exc:
        print(f"WARNING: could not save {MAP_FILE.name} ({exc}). Close it in Excel and press N again.")


def print_patch(notes: dict[str, str]) -> None:
    print(f"  {'Ch':<7} {'Light':<12} {'Group':<10} Where")
    for fx in PATCH:
        print(f"  {channel_range(fx):<7} {fx['name']:<12} {fx['group'] or '-':<10} {notes.get(fx['name'], '')}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Light the MagicQ patch one fixture at a time via the Eurolite")
    p.add_argument("--port", help="COM port (default: auto-detect FTDI 0403:6001)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    print("=== Patch test (Eurolite USB, no NDI, no MagicQ) ===")
    try:
        out = UsbDmxOutput(args.port)
    except ConnectionError as exc:
        print(f"ERROR: {exc}")
        return

    notes = load_notes()
    print(f"Port: {out.port}  Patch: {len(PATCH)} lights (fixtures.py)\n")
    print_patch(notes)
    print("\nStarting with ALL lights on (white). Count how many come on.")
    print("  Left / Right  one light at a time (previous / next)")
    print("  A             all lights on")
    print("  C             change colour (white, red, green, blue)")
    print(f"  N             note where the lit light is (saved to {MAP_FILE.name})")
    print("  Esc           stop (all lights off)\n")

    current = None  # index into PATCH, or None = all lights on
    colour_i = 0
    last_status = ""

    try:
        while True:
            key = read_key()
            if key == "esc":
                break
            if key in ("right", "down"):
                current = 0 if current is None else (current + 1) % len(PATCH)
            elif key in ("left", "up"):
                current = len(PATCH) - 1 if current is None else (current - 1) % len(PATCH)
            elif key == "a":
                current = None
            elif key == "c":
                colour_i = (colour_i + 1) % len(COLOURS)
            elif key == "n":
                print()
                if current is None:
                    print("Press Left / Right to pick one light first.")
                else:
                    fx = PATCH[current]
                    # The dongle keeps repeating the last frame, so the light stays on while you type
                    where = input(f"Where is {fx['name']}? (e.g. 'front left', Enter = keep): ").strip()
                    if where:
                        notes[fx["name"]] = where
                        save_notes(notes)
                last_status = ""

            colour_name, rgb = COLOURS[colour_i]
            dmx = bytearray(512)
            for i, fx in enumerate(PATCH):
                if current is None or i == current:
                    set_fixture(dmx, fx, rgb)
            out.send(dmx)

            if current is None:
                status = f"ALL {len(PATCH)} lights on, {colour_name}"
            else:
                fx = PATCH[current]
                status = (
                    f"[{current + 1}/{len(PATCH)}] {fx['name']} ({fx['group'] or '-'})"
                    f"  ch {channel_range(fx)}  {colour_name}"
                )
                if notes.get(fx["name"]):
                    status += f"  where: {notes[fx['name']]}"
            if status != last_status:
                print("\r" + status.ljust(79), end="", flush=True)
                last_status = status
            time.sleep(0.05)
    finally:
        print("\n\nStopping… (all lights off)")
        out.close()
        if notes:
            print(f"\nNotes so far ({MAP_FILE.name}):")
            print_patch(notes)


if __name__ == "__main__":
    main()
