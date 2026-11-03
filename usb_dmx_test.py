r"""
USB DMX diagnostic for the Eurolite USB-DMX512 PRO Cable Interface (no NDI, no MagicQ).

Sends a repeating RGB chase on channels 1-6 straight out of the USB dongle so you
can prove PC -> Eurolite -> XLR -> fixtures works before debugging the NDI pipeline.
This drives raw channels; to test the Central Church lights properly, use
usb_dmx_patch_test.py instead.

The Eurolite is an FTDI FT232R chip (VID 0403, PID 6001) that shows up as a COM
port (see dmx_output.py). It keeps re-sending the last frame on its own, so this
script sends a blackout on exit.

Usage:
  .\.venv\Scripts\python.exe usb_dmx_test.py
  .\.venv\Scripts\python.exe usb_dmx_test.py --port COM3
  .\.venv\Scripts\python.exe usb_dmx_test.py --starts 1 4 7
  .\.venv\Scripts\python.exe usb_dmx_test.py --flood

Press Esc in this terminal to stop.
"""

from __future__ import annotations

import argparse
import msvcrt
import time

from dmx_output import UsbDmxOutput

# Fixed chase: full R, then G, then B, then white, then off
STEPS = [
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 255),
    (0, 0, 0),
]


def esc_pressed() -> bool:
    """True when Esc was pressed (Windows console)."""
    while msvcrt.kbhit():
        ch = msvcrt.getch()
        if ch == b"\x1b":
            return True
        # Discard second byte of special keys (arrows, function keys, etc.)
        if ch in (b"\x00", b"\xe0") and msvcrt.kbhit():
            msvcrt.getch()
    return False


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send test RGB chase out of the Eurolite USB DMX dongle")
    p.add_argument("--port", help="COM port (default: auto-detect FTDI 0403:6001)")
    p.add_argument("--fps", type=int, default=10, help="DMX frames sent per second")
    p.add_argument("--hold", type=float, default=1.0, help="Seconds per chase step")
    p.add_argument(
        "--starts",
        type=int,
        nargs="+",
        default=[1, 4],
        help="DMX start address of each RGB fixture (default: 1 4)",
    )
    p.add_argument(
        "--flood",
        action="store_true",
        help="Repeat R,G,B across all 512 channels (careful: puts SlimPARs into strobe / sound mode)",
    )
    p.add_argument("--seconds", type=float, default=0, help="Stop after N seconds (0 = until Esc)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    for start in args.starts:
        if not 1 <= start <= 510:
            raise SystemExit(f"--starts {start} is out of range (1-510)")

    print("=== USB DMX test (Eurolite, no NDI, no MagicQ) ===")
    try:
        out = UsbDmxOutput(args.port)
    except ConnectionError as exc:
        print(f"ERROR: {exc}")
        return

    target = "all 512 channels (flood)" if args.flood else "start addresses " + ", ".join(map(str, args.starts))
    print(f"Port: {out.port}  250000 baud 8N2  Rate: {args.fps} fps  Target: {target}")
    print("Each RGB fixture (3-channel mode) should chase red / green / blue / white / off.")
    print("Press Esc in this window to stop.\n")

    dmx = bytearray(512)
    step_i = -1
    next_step = time.monotonic()
    stop_at = next_step + args.seconds if args.seconds > 0 else None
    frames = 0

    try:
        while not esc_pressed():
            now = time.monotonic()
            if stop_at is not None and now >= stop_at:
                break
            if now >= next_step:
                step_i = (step_i + 1) % len(STEPS)
                next_step = now + args.hold
                r, g, b = STEPS[step_i]
                print(f"Step {step_i + 1}/{len(STEPS)}: RGB=({r},{g},{b})  frames={frames}")
                if args.flood:
                    dmx[:] = (bytes((r, g, b)) * 171)[:512]
                else:
                    for start in args.starts:
                        dmx[start - 1 : start + 2] = bytes((r, g, b))

            out.send(dmx)
            frames += 1
            time.sleep(1.0 / args.fps)
    finally:
        print(f"\nStopping… (blackout, {frames} frames sent)")
        out.close()


if __name__ == "__main__":
    main()
