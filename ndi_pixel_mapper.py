import cv2                 # OpenCV: Handles the video processing and cropping
import msvcrt              # Windows console: detect Esc to stop
import numpy as np          # NumPy: Fast math to average the screen colors
import NDIlib as ndi       # NDIlib: Connects to the vMix video feed
from stupidArtnet import StupidArtnet  # StupidArtnet: Sends data to MagicQ
import time                # Time: Handles pauses and connection delays


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
ARTNET_IP = "192.168.1.16"  # "Loopback" IP tells it to send directly to MagicQ on this PC
UNIVERSE = 0             # Art-Net Universe 0 (which maps to MagicQ Input Universe 0)
PACKET_SIZE = 512        # A standard DMX universe has 512 channels

# Mapping layout: (X_start, Y_start, Width, Height) from 0.0 (left/top) to 1.0 (right/bottom)
LIGHTS = [
    # Light 1 starts at DMX Channel 1, samples the Left half of the video
    {"start_ch": 1, "region": (0.0, 0.0, 0.5, 1.0)},  
    
    # Light 2 starts at DMX Channel 4, samples the Right half of the video
    {"start_ch": 4, "region": (0.5, 0.0, 0.5, 1.0)}   
]
# --- INITIALIZE ART-NET ---
print("Starting Art-Net transmission...")
artnet = StupidArtnet(ARTNET_IP, UNIVERSE, PACKET_SIZE, fps=30)
artnet.start()

# --- INITIALIZE NDI ---
# Create an NDI finder to scan the network
ndi_find = ndi.find_create_v2()
print("Searching for NDI streams (waiting 2 seconds)...")
time.sleep(2) 
sources = ndi.find_get_current_sources(ndi_find)

# If no streams at all are found, stop
if not sources:
    print("Error: No NDI streams found on the network!")
    artnet.stop()
    exit()

# Loop through all found sources to locate the vMix stream
vmix_source = None
for source in sources:
    if "vmix" in source.ndi_name.lower():
        vmix_source = source
        break

# If we found other streams (like ProPresenter) but not vMix:
if vmix_source is None:
    print("Error: Found NDI streams, but none of them contain 'vmix' in the name!")
    print("Here are the streams I did find. Make sure vMix NDI is enabled:")
    for s in sources:
        print(f" - {s.ndi_name}")
    artnet.stop()
    exit()

# Connect to the vMix NDI source
print(f"Connected successfully to: {vmix_source.ndi_name}")
ndi_recv_create = ndi.RecvCreateV3()
ndi_recv_create.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
ndi_recv = ndi.recv_create_v3(ndi_recv_create)
ndi.recv_connect(ndi_recv, vmix_source)

# --- MAIN RUNNING LOOP ---
print("Pixel mapping is active! Press Esc in this window to stop.")

try:
    while not esc_pressed():
        # Short timeout so Esc is checked often
        frame_type, video_frame, _, _ = ndi.recv_capture_v2(ndi_recv, 100)

        if frame_type == ndi.FRAME_TYPE_VIDEO:
            # 1. Convert raw NDI data into a frame OpenCV can read
            frame = np.copy(video_frame.data)
            height, width, _ = frame.shape

            # 2. Loop through each light we configured
            for light in LIGHTS:
                rx, ry, rw, rh = light["region"]

                # Convert our percentage coordinates to actual pixel locations
                x1, y1 = int(rx * width), int(ry * height)
                x2, y2 = int((rx + rw) * width), int((ry + rh) * height)

                # Crop the video frame to just this light's target zone
                zone = frame[y1:y2, x1:x2]

                # Calculate the average color of all pixels in this cropped zone
                avg_color_per_row = np.average(zone, axis=0)
                avg_color = np.average(avg_color_per_row, axis=0)

                # NDI outputs colors as Blue-Green-Red (BGR)
                blue = int(avg_color[0])
                green = int(avg_color[1])
                red = int(avg_color[2])

                # 3. Assign these RGB values to the correct DMX channels
                ch = light["start_ch"]
                artnet.set_single_value(ch, red)       # Red channel
                artnet.set_single_value(ch + 1, green) # Green channel
                artnet.set_single_value(ch + 2, blue)  # Blue channel

            # 4. Ship the complete Art-Net packet off to MagicQ
            artnet.show()

            # Free up the frame from computer memory
            ndi.recv_free_video_v2(ndi_recv, video_frame)
finally:
    print("\nStopping script and closing connections...")
    # Always clean up connections when shutting down so ports don't get locked
    artnet.stop()
    ndi.recv_destroy(ndi_recv)
    ndi.find_destroy(ndi_find)