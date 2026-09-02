# CentraLight

Takes the vMix NDI video feed, samples regions of the frame, and sends the average colour of each region over Art-Net to MagicQ so house lights can follow the screen.

Built for [Central Church Bristol](https://github.com/Central-Church-Bristol). Windows only (the scripts use the Windows console Esc key).

## What it does

- Finds an NDI source whose name contains `vmix`
- Averages colour in configured screen regions (default: left half and right half)
- Sends RGB to MagicQ on Art-Net universe 0 (channels 1–3 and 4–6 by default)
- Esc in the terminal window stops the script and closes the connections

`artnet_test.py` is a no-NDI diagnostic: it chases R/G/B/W on those same channels so you can prove MagicQ is receiving Art-Net before you debug video.

## Requirements

- Windows
- [Python 3](https://www.python.org/)
- [NDI Runtime](https://ndi.video/tools/)
- vMix with NDI output enabled (the source name must contain `vmix`)
- MagicQ on the same LAN, Art-Net input enabled

## Install

In this folder:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run pixel mapping

1. Confirm vMix is outputting NDI.
2. Confirm MagicQ is listening for Art-Net on universe 0 (see below).
3. Run:

```bat
.venv\Scripts\python.exe ndi_pixel_mapper.py
```

Press **Esc** in that window to stop.

If it cannot find vMix, it prints the NDI sources it did see. Enable NDI on vMix, or rename the source so it contains `vmix`.

## Art-Net test (no video)

Use this when lights are not following, to check the network and MagicQ first:

```bat
.venv\Scripts\python.exe artnet_test.py
.venv\Scripts\python.exe artnet_test.py --ip 127.0.0.1
.venv\Scripts\python.exe artnet_test.py --ip 192.168.1.16 --broadcast
```

Press **Esc** to stop.

While it runs, MagicQ should show a repeating red / green / blue / white / black chase on channels 1–6.

## MagicQ (service desk)

1. **SETUP → VIEW DMX I/O** — Universe 1: Status **Enabled**, In Type **Art-Net**, In Uni **0**
2. Set **Test** on that universe to **Input** (forces input onto output while testing)
3. **SETUP → VIEW SETTINGS** — Network IP Address = this PC’s LAN IP
4. **OUT → VIEW CHANS → VIEW DMX → VIEW ART-NET** — packets should leave 0
5. Same window → **VIEW INPUTS** — channels 1–6 should move

Default target IP in both scripts is `192.168.1.16`. Change `ARTNET_IP` in `ndi_pixel_mapper.py`, or pass `--ip` to `artnet_test.py`, if MagicQ is on a different address.

## Light mapping

Edit `LIGHTS` at the top of `ndi_pixel_mapper.py`. Each entry is a DMX start channel and a screen region as fractions of the frame (`X`, `Y`, `Width`, `Height` from 0.0 to 1.0):

```python
{"start_ch": 1, "region": (0.0, 0.0, 0.5, 1.0)},  # left half, RGB on ch 1–3
{"start_ch": 4, "region": (0.5, 0.0, 0.5, 1.0)},  # right half, RGB on ch 4–6
```

Each light uses three consecutive channels: red, green, blue.

## Assets

`Assets/` has the CentraLight logo (PNG plus GIMP `.xcf` source). It is branding only; the scripts do not load it.
