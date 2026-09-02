"""
Art-Net receive diagnostic for MagicQ (no NDI).

Sends a repeating RGB chase on channels 1-6 so you can confirm MagicQ
sees UDP Art-Net before debugging the NDI pipeline.

Usage:
  .\.venv\Scripts\python.exe artnet_test.py
  .\.venv\Scripts\python.exe artnet_test.py --ip 127.0.0.1
  .\.venv\Scripts\python.exe artnet_test.py --ip 192.168.1.16 --broadcast

Press Esc in this terminal to stop.
"""

from __future__ import annotations

import argparse
import msvcrt
import socket
import time

from stupidArtnet import StupidArtnet

# Fixed chase: full R, then G, then B on lights 1 and 2 (ch 1-3 and 4-6)
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
    p = argparse.ArgumentParser(description="Send test Art-Net RGB chase to MagicQ")
    p.add_argument("--ip", default="192.168.1.16", help="Target IP (LAN NIC or 127.0.0.1)")
    p.add_argument("--universe", type=int, default=0, help="Art-Net universe (MagicQ In Uni)")
    p.add_argument("--fps", type=int, default=10, help="Packet rate")
    p.add_argument(
        "--broadcast",
        action="store_true",
        help="Enable UDP broadcast (also try 255.255.255.255 as --ip)",
    )
    p.add_argument("--hold", type=float, default=1.0, help="Seconds per chase step")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    print("=== Art-Net test (no NDI) ===")
    print(f"Target: {args.ip}:6454  Universe: {args.universe}  Broadcast: {args.broadcast}")
    print("MagicQ checklist while this runs:")
    print("  1. SETUP > VIEW DMX I/O > Universe 1: Status Enabled, In Type Art-Net, In Uni 0")
    print("  2. Set Test column on that universe to Input (forces input onto output)")
    print("  3. SETUP > VIEW SETTINGS > Network IP Address = this PC's LAN IP")
    print("  4. OUT > VIEW CHANS > VIEW DMX > VIEW ART-NET: pkts should leave 0")
    print("  5. Same window > VIEW INPUTS: channels 1-6 should chase R/G/B/W")
    print("Press Esc in this window to stop.\n")

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.sendto(b"ping", (args.ip, 6454))
        probe.close()
    except OSError as exc:
        print(f"WARNING: could not send UDP to {args.ip}:6454 — {exc}")

    artnet = StupidArtnet(
        args.ip,
        args.universe,
        512,
        fps=args.fps,
        broadcast=args.broadcast,
    )
    # Do NOT call artnet.start() — that spawns a background sender.
    # We send explicitly so values stay in lockstep with the chase.

    step_i = 0
    next_step = time.monotonic()
    packets = 0

    try:
        while not esc_pressed():
            now = time.monotonic()
            if now >= next_step:
                step_i = (step_i + 1) % len(STEPS)
                next_step = now + args.hold
                r, g, b = STEPS[step_i]
                print(f"Step {step_i + 1}/{len(STEPS)}: RGB=({r},{g},{b})  packets={packets}")

            r, g, b = STEPS[step_i]
            artnet.set_rgb(1, r, g, b)
            artnet.set_rgb(4, r, g, b)
            artnet.show()
            packets += 1
            time.sleep(1.0 / args.fps)
    finally:
        print("\nStopping…")
        artnet.blackout()
        artnet.close()


if __name__ == "__main__":
    main()
