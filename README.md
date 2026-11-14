# CentraLight

The lighting app for [Central Church Bristol](https://github.com/Central-Church-Bristol): it drives the church's
lights over DMX, from *inputs* (looks) such as **Follow screen**, where the lights follow the Media Background Pane
picture, to colours, paintings, pictures, videos and timed sequences, with automations, the Stream Deck, and a
hands-on Console. Windows only.

## Starting it

Double-click `CentraLight.exe` (or `run.bat`). The first time, `run.bat` sets up `.venv` with what the app needs
(from `requirements.txt`) and copies the church fonts (below); `CentraLight.exe` runs it for you then. Keep the exe
in the CentraLight folder and put a shortcut to it on the desktop.

`CentraLight.exe` runs Python inside itself (with `.venv`'s packages), so Windows sees CentraLight: one taskbar
button with its pin, and CentraLight in Task Manager and on its notifications. It needs Python 3.14 or later; with an
older one it starts `.venv\Scripts\pythonw.exe` as before (the session log says which).

- It starts with **Simulated** output: it shows what it would send without touching the lights. To drive them:
  the gear (top right) → **Output** → **Eurolite USB-DMX512 PRO** (or **Art-Net**, to MagicQ or an Art-Net node).
  Only one program can use the dongle at a time, so close MagicQ first.
- Closing the window keeps CentraLight running in the tray (by the clock), so automations, the Stream Deck and the
  lights carry on. **Quit** is in the tray icon's menu.
- **Start CentraLight when I sign in to Windows** (Settings → Starting and quitting) starts it in the tray at
  sign-in. After a restart that nobody signs in to (Windows Update in the night, say), it only starts once someone
  signs in; for it to come back by itself, Windows has to sign in automatically. That's your choice, and a Windows
  setting CentraLight never changes: press Win+R, type `netplwiz`, untick **Users must enter a user name and
  password to use this computer**, and type the account's password. (No tick box? Settings → Accounts → Sign-in
  options → turn off **Require Windows Hello sign-in for Microsoft accounts**, then try again.) Anyone who switches
  the PC on then gets the desktop without a password. Windows Update's **active hours** stop it restarting the PC
  during services.
- When the PC has just started, the default show opens (Settings → Shows and automations: **Last loaded**, a show,
  or **None (lights off)**) with every light black; reopening CentraLight carries on as it was.

### On a new PC

1. Install [Python 3](https://www.python.org/downloads/windows/) (it's built and tested on 3.14) with its `py`
   launcher, which is on by default: the first start uses it. For Follow screen, install the
   [NDI Runtime](https://ndi.video/tools/) too.
2. Get CentraLight: `git clone https://github.com/Central-Church-Bristol/centralight.git` (or download it from GitHub
   as a zip and unpack it).
3. Double-click `CentraLight.exe` in that folder. The first time it sets everything up (`.venv` and the packages,
   from the internet: a few minutes, in a console window), then CentraLight starts; after that it starts straight
   away. Each PC has its own settings and shows, so copy `Documents\CentraLight Shows` across if you want the shows.

## What's in it

- **Dashboard:** the inputs as tiles: send one to **Base** (with its transition) or a copy to the **Overlay** on top;
  the Festoon, Key lights and Master faders; **Cut to black** and **Fade to black** (click: the colour lights; hold:
  everything); show variables; the show's notes.
- **Console:** a hands-on lighting desk. Choose lights on the map (click, Ctrl-click, a box, groups), set their
  brightness and colour, fans, every channel in Advanced, and chases (steps, speed with Tap, crossfade, directions,
  a step on each beat). What it sets wins until **Release**; **Save as input** keeps a look or a chase as an input.
- **Editor:** inputs from Colour, Paint, Image, Video, NDI and Lights elements on four tracks; masks as
  black-and-white pictures; expressions (`wave(time, 0.25)`, `0.2 + 0.8 * audio.bass`); timed sequences with
  keyframes; transitions.
- **Layout:** where each light is (drag its dot), Identify, saved layouts, and notes in `fixture_map.csv`.
- **Automation:** "when these conditions are met, do this" (times, days, sunset, the screen feed, sound, variables),
  for the show, and **Global automations** for the whole app (Sundays 08:30 → load the Morning show, say).
- **Sound:** Settings → Audio listens to this PC's output (or an input) for `audio.level`, `audio.bass`, `audio.mid`,
  `audio.treble`, `audio.beat` and `audio.pitch`.

## Shows, files and the AI guide

- A show is everything above (inputs, variables, automations, the lights and layouts, media), in its own folder in
  `Documents\CentraLight Shows`; changes save by themselves. The header's **Show ▾** loads, makes, copies, renames,
  imports and exports them (`.clshow` files). The global automations are in `_Global automations` there.
- Settings, `live.json` (what was playing) and `session.log` (for when something goes wrong) are in
  `%LOCALAPPDATA%\CentraLight`.
- **Inputs made by AI:** **Create with AI** on the **+ New input** tile, or the sparkle on a tile to change one: copy
  the prompt into any AI chat and paste its reply back (it's checked and previewed first). AI tools that work in
  folders (Claude Code, Cursor) can work in the show folder (Settings → AI → **Open show folder**): they read
  `AI_GUIDE.md` there, and what they write appears straight away (Ctrl+Z undoes it).

## Stream Deck, Companion and phones

While CentraLight runs, every action has a web address on this PC. Open <http://127.0.0.1:8746/> (or click the
**Remote** chip) for all of them, with a button for each input: a phone remote too.

- **Bitfocus Companion** (5.1 or later): the CentraLight module in `companion\` lights its keys with what's happening
  (the input on Base, the Overlay, black, countdowns, the Console), with actions for the inputs, the Overlay, shows,
  black, Master, the house lights, variables, automations, the countdown and the Console. Layouts, Identify and the
  Overlay's opacity are URLs (below), for Companion's Generic HTTP.
  `companion\README.md` has the steps: Modules → **Import module package** → `companion\centralight-1.0.0.tgz`.
- **Elgato Stream Deck app:** a **Website** action with a URL such as `http://127.0.0.1:8746/api/input/warm-white`,
  with **GET request in background** ticked.

| What | URL |
|---|---|
| Send an input to Base | `/api/input/warm-white` (add `?transition=fade&duration=2` for another transition) |
| Cut to it | `/api/input/warm-white/cut` |
| Send to the Overlay (a copy on top) | `/api/input/pillars-purple/overlay`; clear its copies: `/api/input/pillars-purple/overlay/clear` |
| The Overlay | `/api/overlay/clear` (Clear all), `/api/overlay/toggle` (or `/on`, `/off`: off hides it without emptying it) |
| Set a show variable | `/api/var/speed?value=0.7` (colours: `?value=FF6F59`; switches: `on`, `off`, `toggle`) |
| Key lights | `/api/house/key-lights?level=0.6&fade=3`, `/api/house/key-lights/toggle` |
| Master, Cut / Fade to black | `/api/master?level=0.8`, `/api/black/cut`, `/api/black/fade` (`?all=1`: the Festoon and Key lights too) |
| Load a show | `/api/show/central-church` (its folder's name, or its name); `/api/shows` lists them |
| A global automation | `/api/global-automation/sunday-morning/run` (or `/enable`, `/disable`) |
| The Console | `/api/live/release` (Release all; `?lights=slimpar-101,slimpar-102`: just those), `/api/live/tap`, `/next`, `/pause`, `/resume`, `/speed?bpm=120` |

Inputs, variables, house lights, automations and layouts are the loaded show's: a URL for something that isn't in it
answers 404 "not in this show". The remote only answers this PC unless Settings → Remote → **Allow other computers
and phones** is ticked; they then need the key shown there (`?key=…`). Requests a web page makes for another website
are always refused.

## The lights

PC USB → Eurolite USB-DMX512 PRO Cable Interface → 3-pin XLR → lights. The Eurolite shows up as a COM port (FTDI,
VID 0403 / PID 6001); CentraLight finds it by itself, and Windows installs the driver.

A new show starts from the church's patch (`centralight\fixtures.py`, from the MagicQ show `Central Church.shw`,
universe 1, checked in the room on 2 Nov 2026); each show keeps its own lights in its `show.json` (the Layout tab):

| Start channel | Light | Type | Group |
|---|---|---|---|
| 1 | Dimmer 1 | 1-channel dimmer | Festoon |
| 4 | Dimmer 4 | 1-channel dimmer | Key Lights (both) |
| 5, 14, 23, 32 | SlimPAR 101–104 | Chauvet SlimPAR Q12 USB, 9-channel mode | Top Wash |
| 41, 68 | SlimPAR 105, 108 | Chauvet SlimPAR Q12 USB, 9-channel mode | Side Fill |
| 50, 59 | SlimPAR 106, 107 | Chauvet SlimPAR Q12 USB, 9-channel mode | Pillars |

Nothing answers on channels 2 and 3. A SlimPAR's nine channels are red, green, blue, amber, colour macro, strobe,
program, dimmer and dimmer speed: inputs set the colour with the dimmer at full; the Console can set them all.

The Layout tab's **Type** also has the **Betopper LPC018** in its 7-channel mode (dimmer, red, green, blue, strobe,
macro, macro speed). On the light's own menu, set `7CH` and its start channel (`A077`, say).

## Fonts

The app uses the church's Futura PT fonts. They aren't in git: `run.bat` copies them once from
`G:\My Drive\Video Projection\Brand\Fonts\Futura PT` into `fonts\`. Without them CentraLight uses Windows' own fonts.

## For whoever works on it

- Requirements: Windows, [Python 3](https://www.python.org/) (it's built and tested on 3.14), the [NDI Runtime](https://ndi.video/tools/) (for
  the screen feed), and Media Background Pane for Follow screen.
- The tests (about ten minutes; they never touch the lights or your files):
  `.venv\Scripts\python.exe -m unittest discover -s tests -t .`
- `docs\BUILD_LOG.md` is the history of how it was built and why; `docs\RIG_CHECKLIST.md` is what to try on the
  lights after a change.
- `tools\`: the soak test (`soak.py`: the whole app for hours, Simulated, watching memory and CPU), the launcher and
  icon builders, the Companion module's builder, and `tick_timing.py`.
- `Assets\` has the logo (`CentraLight Logo.svg` and PNGs at 1024 and 4096 pixels); the app uses the 1024 one, and
  `launcher\icon.ico` and `launcher\clshow.ico` are made from it.
- `CentraLight.exe` is committed, so a fresh copy starts with a double-click. After a change to the logo or to
  `launcher\Program.cs`, rebuild it (`.venv\Scripts\python.exe tools\build_launcher.py`) and commit it again.
