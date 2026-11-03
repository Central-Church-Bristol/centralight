import cv2                 # OpenCV: Shrinks each video frame so averaging is fast
import msvcrt              # Windows console: detect Esc to stop
import numpy as np          # NumPy: Fast math to average the screen colors
import NDIlib as ndi       # NDIlib: Connects to the Media Background Pane NDI feed
import time                # Time: Handles pauses and connection delays

from dmx_output import ArtNetOutput, UsbDmxOutput  # Sends the DMX: USB dongle or Art-Net
from fixtures import fixture, set_fixture           # The church lights and their DMX channels


def esc_pressed() -> bool:
    """True when Esc was pressed (Windows console)."""
    while msvcrt.kbhit():
        ch = msvcrt.getch()
        if ch == b"\x1b":
            return True
        if ch in (b"\x00", b"\xe0") and msvcrt.kbhit():
            msvcrt.getch()
    return False

# --- CONFIGURATION ---
NDI_SOURCE = "Media Background Pane"  # Follow the NDI feed whose name contains this (not case-sensitive)
OUTPUT = "usb"         # "usb" = Eurolite USB-DMX512 PRO dongle, "artnet" = Art-Net (e.g. to MagicQ)
USB_PORT = None          # None = find the Eurolite automatically, or set it, e.g. "COM3"
ARTNET_IP = "192.168.1.16"  # Art-Net target (only used when OUTPUT = "artnet")
UNIVERSE = 0             # Art-Net universe (only used when OUTPUT = "artnet")
SEND_FPS = 30            # DMX updates per second (the USB dongle can only manage about 40)

# Mapping layout: which light follows which part of the screen.
# Light names come from fixtures.py. Region = (X_start, Y_start, Width, Height)
# from 0.0 (left/top) to 1.0 (right/bottom).
# For now every light follows the whole screen; give each one its own region
# once fixture_map.csv says where the lights are.
WHOLE = (0.0, 0.0, 1.0, 1.0)
LIGHTS = [
    {"name": "SlimPAR 101", "region": WHOLE},  # Top Wash
    {"name": "SlimPAR 102", "region": WHOLE},  # Top Wash
    {"name": "SlimPAR 103", "region": WHOLE},  # Top Wash
    {"name": "SlimPAR 104", "region": WHOLE},  # Top Wash
    {"name": "SlimPAR 105", "region": WHOLE},  # Side Fill
    {"name": "SlimPAR 106", "region": WHOLE},  # Pillars
    {"name": "SlimPAR 107", "region": WHOLE},  # Pillars
    {"name": "SlimPAR 108", "region": WHOLE},  # Side Fill
]

# Lights that aren't RGB, so they don't follow the screen. This script sends every
# DMX channel, so while it runs they sit at the level set here (0 = off, 255 = full).
STATIC_LEVELS = {
    "Dimmer 1": 0,  # Festoon
    "Dimmer 4": 0,  # Both key lights
}

SAMPLE_SIZE = (160, 90)  # Each frame is shrunk to this many pixels before averaging


def screen_to_dmx(frame: np.ndarray, dmx: bytearray) -> None:
    """Average each light's screen region and write that colour into dmx."""
    # INTER_AREA averages pixels as it shrinks, so the small frame keeps the true colours
    small = cv2.resize(frame, SAMPLE_SIZE, interpolation=cv2.INTER_AREA)
    height, width = small.shape[:2]

    for light in LIGHTS:
        rx, ry, rw, rh = light["region"]

        # Convert our percentage coordinates to pixel locations (at least 1 pixel)
        x1 = min(int(rx * width), width - 1)
        y1 = min(int(ry * height), height - 1)
        x2 = max(x1 + 1, int((rx + rw) * width))
        y2 = max(y1 + 1, int((ry + rh) * height))

        # Average colour of every pixel in this light's zone
        zone = small[y1:y2, x1:x2, :3]
        blue, green, red = zone.reshape(-1, 3).mean(axis=0)  # NDI gives Blue-Green-Red (BGR)

        # Red/green/blue into the light's own channels (fixtures.py knows the layout)
        set_fixture(dmx, fixture(light["name"]), (int(red + 0.5), int(green + 0.5), int(blue + 0.5)))


def main() -> None:
    # Catch typos in LIGHTS / STATIC_LEVELS before anything starts
    try:
        for name in [light["name"] for light in LIGHTS] + list(STATIC_LEVELS):
            fixture(name)
    except KeyError as exc:
        print(f"Error: {exc.args[0]} (check LIGHTS and STATIC_LEVELS)")
        return

    # --- OPEN THE DMX OUTPUT ---
    if OUTPUT == "usb":
        print("Opening the Eurolite USB DMX dongle...")
        try:
            output = UsbDmxOutput(USB_PORT)
        except ConnectionError as exc:
            print(f"Error: {exc}")
            return
        print(f"Sending DMX on {output.port}")
    else:
        print(f"Sending Art-Net to {ARTNET_IP}, universe {UNIVERSE}")
        output = ArtNetOutput(ARTNET_IP, UNIVERSE)

    # Fixed-level lights come on straight away; screen lights start dark
    dmx = bytearray(512)
    for name, level in STATIC_LEVELS.items():
        set_fixture(dmx, fixture(name), (level, level, level))
    output.send(dmx)

    # --- INITIALIZE NDI ---
    # Create an NDI finder to scan the network
    ndi_find = ndi.find_create_v2()
    print("Searching for NDI streams (waiting 2 seconds)...")
    time.sleep(2)
    sources = ndi.find_get_current_sources(ndi_find)

    # If no streams at all are found, stop
    if not sources:
        print("Error: No NDI streams found on the network!")
        output.close()
        ndi.find_destroy(ndi_find)
        return

    # Loop through all found sources to locate our feed
    feed = None
    for source in sources:
        if NDI_SOURCE.lower() in source.ndi_name.lower():
            feed = source
            break

    # If we found other streams (like cameras) but not ours:
    if feed is None:
        print(f"Error: Found NDI streams, but none of them contain '{NDI_SOURCE}' in the name!")
        print("Here are the streams I did find. Make sure Media Background Pane is running,")
        print("or change NDI_SOURCE if its NDI name was changed in its settings:")
        for s in sources:
            print(f" - {s.ndi_name}")
        output.close()
        ndi.find_destroy(ndi_find)
        return

    # Connect to the NDI feed
    print(f"Connected successfully to: {feed.ndi_name}")
    ndi_recv_create = ndi.RecvCreateV3()
    ndi_recv_create.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
    ndi_recv = ndi.recv_create_v3(ndi_recv_create)
    ndi.recv_connect(ndi_recv, feed)

    # --- MAIN RUNNING LOOP ---
    print("Pixel mapping is active! Press Esc in this window to stop.")
    last_send = 0.0

    try:
        while not esc_pressed():
            # Video only (audio frames would need freeing); short timeout so Esc is checked often
            frame_type, video_frame, _, _ = ndi.recv_capture_v2(
                ndi_recv, 100, want_audio=False, want_metadata=False
            )
            if frame_type != ndi.FRAME_TYPE_VIDEO:
                continue

            # Only work out new colours as often as we send DMX
            now = time.monotonic()
            if now - last_send >= 1.0 / SEND_FPS:
                frame = np.copy(video_frame.data)
                screen_to_dmx(frame, dmx)
                output.send(dmx)
                last_send = now

            # Free up the frame from computer memory
            ndi.recv_free_video_v2(ndi_recv, video_frame)
    finally:
        print("\nStopping script and closing connections...")
        # Always clean up so the lights go off and ports don't get locked
        output.close()
        ndi.recv_destroy(ndi_recv)
        ndi.find_destroy(ndi_find)


if __name__ == "__main__":
    main()
