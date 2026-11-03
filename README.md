# CentraLight

Takes the Media Background Pane NDI feed, samples regions of the frame, and sends the average colour of each region to the house lights over DMX, so the lights follow the screen.

Built for [Central Church Bristol](https://github.com/Central-Church-Bristol). Windows only (the scripts use the Windows console Esc key).

## The CentraLight app (new, being tested)

Double-click `CentraLight.exe` (or `run.bat`, which also sets things up the first time) to start the CentraLight app: a window with the **Festoon**, **Key lights** and **Master** faders, **Blackout**, a map of the lights, and *inputs* (looks) such as **Follow screen**, where the lights follow Media Background Pane like `ndi_pixel_mapper.py` does.

- The first time, `run.bat` installs what the app needs into `.venv` and copies the church fonts from `G:\My Drive\Video Projection\Brand\Fonts` into `fonts\`.
- The app starts with **Simulated** output: it shows what it would send, but doesn't touch the lights. To drive the lights, click the gear (top right) → **Output** → **Eurolite USB-DMX512 PRO**. Close the console scripts and MagicQ first: only one program can use the dongle at a time.
- **Layout** tab: drag each dot to where its light is, click a dot to flash that light, and turn on **Identify** to give every light its own colour. A light is just its dot: it shows Paint exactly at the dot, and a picture (NDI, Image or Video) averaged over a patch around it, as big as that element's **Area** in the Editor (raise it for calmer colours; **Smoothing** makes changes glide). Notes about where each light is go into `fixture_map.csv` (the same file `usb_dmx_patch_test.py` uses).
- **Editor** tab: build inputs from Paint (colours, gradients, shapes), Image and NDI elements on four tracks; untick lights an element should leave alone; pick a transition. **=** next to a setting gives it an *expression*, a small formula such as `wave(time, 0.25)` or `mix(#FF0000, accent, 0.5)`. *Global variables* (Editor → Globals tab) can be shown on the Dashboard and set from the Stream Deck. Untick **Infinite** for a timed sequence: elements placed in time on the timeline (move, trim, split, in / out transitions), keyframes (the ◆ next to a setting) and Video elements. Paint elements can be painted on (brush, eraser, bucket, gradient, light colour), any element's mask can be painted, and pictures have a gizmo to move, scale and turn them.
- **Sound**: Settings → Audio → listen to this PC's output (or an input), then use `audio.level`, `audio.bass`, `audio.mid`, `audio.treble`, `audio.beat` or `audio.pitch` (all 0–1) in any expression, e.g. opacity `0.2 + 0.8 * audio.bass`.
- **Automation** tab: "when these conditions are met, do this", e.g. Sundays 09:00–12:30 → Warm white on Base and the key lights at 70 %, or "after sunset − 30 min" (Bristol's times) → festoon on. Closing the window keeps CentraLight running in the tray by the clock, so automations and the Stream Deck keep working; **Quit** is in the tray icon's menu.
- **Inputs made by AI**: **Create with AI** on the **+ New input** tile (or the sparkle on a tile, to edit one). Describe the look, click **Copy prompt**, paste it into any AI chat (ChatGPT, Claude, Gemini, Copilot), and paste the reply back: it's checked and previewed before you add it. AI tools such as Claude Code or Cursor can also work in the show folder (Settings → AI → **Open show folder**): they read `AI_GUIDE.md` there, and inputs they write appear straight away (Ctrl+Z undoes them).
- The show (lights, layout, inputs) is in `Documents\CentraLight Shows\Central Church`. Settings and the session log (`session.log`, for when something goes wrong) are in `%LOCALAPPDATA%\CentraLight`.
- `docs\BUILD_LOG.md` lists what's done; `docs\WEDNESDAY_TESTS.md` is the checklist for trying it on the lights.

### Always running

- Closing the window keeps CentraLight running in the tray (by the clock), so automations, the Stream Deck and the
  lights carry on. **Quit** is in the tray icon's menu.
- **Start CentraLight when I sign in to Windows** (Settings → Starting and quitting) starts it in the tray at
  sign-in. It stays off until it has been tried on the lights (`docs\WEDNESDAY_TESTS.md`, step 1b).
- After a restart that nobody signs in to (e.g. Windows Update in the night), CentraLight only starts once someone
  signs in. For it to come back by itself, Windows has to sign in automatically. That's your choice, and a Windows
  setting that CentraLight never changes: press Win+R, type `netplwiz`, untick **Users must enter a user name and
  password to use this computer**, and type the account's password. (No tick box? Settings → Accounts → Sign-in
  options → turn off **Require Windows Hello sign-in for Microsoft accounts**, then try again.) Anyone who switches
  the PC on then gets the desktop without a password.
- Windows Update's **active hours** (Settings → Update & Security → Change active hours) stop it restarting the PC
  during services.

### Stream Deck (and phones)

While the app runs, every action has a web address on this PC, so a Stream Deck key can fire it:

1. In the **Elgato Stream Deck** app, drag a **Website** action (System) onto a key.
2. Paste a URL, e.g. `http://127.0.0.1:8746/api/input/warm-white`, and tick **GET request in background**
   (otherwise a browser window opens each time).
3. Bitfocus **Companion** works too: **Generic HTTP → GET** with the same URL.

Open <http://127.0.0.1:8746/> in a browser for the full list (with buttons for every input), or click the
**Remote** chip at the top of the app. The Editor's **Copy Stream Deck URLs** button copies one input's URLs.
Some useful ones:

| What | URL |
|---|---|
| Send an input to Base | `/api/input/warm-white` (add `?transition=fade&duration=2` for another transition) |
| Cut to it | `/api/input/warm-white/cut` |
| Overlay 1 on / off | `/api/input/pillars-purple/overlay/1` |
| Set a global variable | `/api/var/speed?value=0.7` (colours: `?value=FF6F59`; switches: `on`, `off`, `toggle`) |
| Key lights | `/api/house/key-lights?level=0.6&fade=3`, `/api/house/key-lights/toggle` |
| Master, Blackout | `/api/master?level=0.8`, `/api/blackout/toggle` |

A Stream Deck **Multi Action** can pair a vMix key with a lights URL. The remote only answers this PC unless
Settings → Remote → **Allow other computers and phones** is ticked; they then need the key shown there
(`?key=…`). Requests that a web page in a browser makes for another website are always refused.

The console scripts below still work, and stay the fallback until the app has been tested on the lights.

Run the app's tests (about five minutes; they never touch the lights):

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

## What it does

- Finds the NDI source from **Media Background Pane** (any source whose name contains `NDI_SOURCE` in `ndi_pixel_mapper.py`)
- Averages the colour of each light's screen region (default: all 8 SlimPARs follow the whole screen; the festoon and key lights don't follow it)
- Sends DMX straight out of a Eurolite USB-DMX512 PRO Cable Interface — no MagicQ or lighting desk needed. Art-Net is still available as an option.
- Esc in the terminal window stops the script and turns the lights off

## Hardware

PC USB → Eurolite USB-DMX512 PRO Cable Interface → 3-pin XLR → lights.

The Eurolite shows up as a COM port (FTDI chip, VID 0403 / PID 6001). The scripts find it automatically, and Windows installs the FTDI driver itself. Close MagicQ, QLC+ or any other lighting software first: only one program can use the dongle at a time.

## The lights

`fixtures.py` holds the patch, from the MagicQ show `Central Church.shw` (DMX universe 1) and checked in the room on 2 Nov 2026:

| Start channel | Light | Type | Group |
|---|---|---|---|
| 1 | Dimmer 1 | 1-channel dimmer | Festoon |
| 4 | Dimmer 4 | 1-channel dimmer | Key Lights (both) |
| 5, 14, 23, 32 | SlimPAR 101–104 | Chauvet SlimPAR Q12 USB, 9-channel mode | Top Wash |
| 41, 68 | SlimPAR 105, 108 | Chauvet SlimPAR Q12 USB, 9-channel mode | Side Fill |
| 50, 59 | SlimPAR 106, 107 | Chauvet SlimPAR Q12 USB, 9-channel mode | Pillars |

Nothing answers on channels 2 and 3, so they're left out.

In 9-channel mode a SlimPAR's channels are red, green, blue, amber, colour macro, strobe, program, **dimmer**, dimmer speed. The scripts set the dimmer (8th channel) to full and leave the effect channels at 0, so only red, green and blue change. If a light is re-addressed, change `fixtures.py`: the test scripts and the mapper all read it.

## Requirements

- Windows
- [Python 3](https://www.python.org/)
- [NDI Runtime](https://ndi.video/tools/)
- Media Background Pane running (it sends the NDI feed the lights follow)
- Eurolite USB-DMX512 PRO Cable Interface

## Install

In this folder:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Test the lights (no video)

```bat
.venv\Scripts\python.exe usb_dmx_patch_test.py
```

It turns every light on white so you can count them, then **Left / Right** goes through them one at a time. **C** changes colour, **N** saves a note of where the lit light is to `fixture_map.csv`, and **Esc** stops (lights off).

`usb_dmx_test.py` is a lower-level check of the dongle and cable: it chases red / green / blue / white on raw channels 1–6.

## Run pixel mapping

1. Start Media Background Pane.
2. Plug in the Eurolite and close any other lighting software (including the test scripts).
3. Run:

```bat
.venv\Scripts\python.exe ndi_pixel_mapper.py
```

Press **Esc** in that window to stop; the lights go off.

If it cannot find the feed, it prints the NDI sources it did see. Start Media Background Pane, or if its NDI name was changed in its settings, set `NDI_SOURCE` in `ndi_pixel_mapper.py` to match.

## Light mapping

Edit `LIGHTS` near the top of `ndi_pixel_mapper.py`. Each entry is a light name from `fixtures.py` and the screen region it follows, as fractions of the frame (`X`, `Y`, `Width`, `Height` from 0.0 to 1.0):

```python
{"name": "SlimPAR 101", "region": (0.0, 0.0, 0.5, 1.0)},  # left half of the screen
{"name": "SlimPAR 108", "region": (0.5, 0.0, 0.5, 1.0)},  # right half
```

The festoon and key lights aren't RGB, so they don't follow the screen. The script still sends every DMX channel, so while it runs they sit at the level in `STATIC_LEVELS`: 0 (off, the default) to 255 (full).

## Art-Net / MagicQ (optional)

Set `OUTPUT = "artnet"` in `ndi_pixel_mapper.py` to send Art-Net instead of using the USB dongle, for example to MagicQ or an Art-Net node. The target is `ARTNET_IP` (default `192.168.1.16`), universe `UNIVERSE` (default 0).

`artnet_test.py` is a no-NDI check of that route: it chases R/G/B/W on channels 1–6 over Art-Net.

```bat
.venv\Scripts\python.exe artnet_test.py
.venv\Scripts\python.exe artnet_test.py --ip 127.0.0.1
.venv\Scripts\python.exe artnet_test.py --ip 192.168.1.16 --broadcast
```

MagicQ setup for Art-Net in:

1. **SETUP → VIEW DMX I/O** — Universe 1: Status **Enabled**, In Type **Art-Net**, In Uni **0**
2. Set **Test** on that universe to **Input** (forces input onto output while testing)
3. **SETUP → VIEW SETTINGS** — Network IP Address = this PC’s LAN IP
4. **OUT → VIEW CHANS → VIEW DMX → VIEW ART-NET** — packets should leave 0
5. Same window → **VIEW INPUTS** — channels should move

## Assets

`Assets/` has the CentraLight logo (PNG plus GIMP `.xcf` source). It is branding only; the scripts do not load it.
