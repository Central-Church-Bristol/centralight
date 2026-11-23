# CentraLight for Companion

A Bitfocus Companion module (Companion 5.1 or later) for the Stream Deck. It keeps a live connection to CentraLight,
so its buttons light up with what's happening, the moment it happens: the input on Base, what's on the Overlay, the
show loaded, black, a countdown, paused automations, the Festoon and Key lights, show variables, and the Console
holding lights. Pressing a button does what CentraLight's Stream Deck URLs do (see the remote page).

## Putting it in Companion (once)

1. Open Companion's admin page (the Companion window's **Launch GUI** button).
2. Go to **Modules** and click **Import module package**. Pick `centralight-1.3.0.tgz` from this `companion` folder.
3. Go to **Connections**, add **CentraLight**, and save it as it is: **CentraLight PC** `127.0.0.1` and **Port**
   `8746` (the port in CentraLight's Settings → Remote, if it was changed).
4. The connection turns green once CentraLight is running. Then the **Presets** tab has numbered keys for any show
   (below), a button for each input and show, the four Cut / Fade to black buttons, and the Console's Release all and
   Tap: drag them onto the Stream Deck.

A newer version: import its `.tgz` the same way.

## Keys for any show (1.2.0; as each input says, 1.3.0)

The **Numbered inputs** presets work in every show. **Input 1** is the first tile on the Dashboard of the show that's
loaded, **Input 2** the second, and so on, up to 32. Each key shows its input's name, which changes when another show
loads, and it's blank where the show has no input with that number.

- **Numbered inputs (as each input says)**: the key goes where its input's **Numbered key** setting says (in
  CentraLight's Editor, the input's Stream Deck panel): **Sends it to Base** (the default), or **Sends it to the
  Overlay**, as its Overlay button says. Its name is lime when it goes to the Overlay. Each show has its own copy of
  an input, so the same input can go to the Overlay in one show and to Base in another. It needs CentraLight from 23
  Nov or later; an older one sends everything to Base.
- **Numbered inputs to Base**: always sends the input to Base with its own transition. Coral while it's on Base.
- **Numbered inputs to the Overlay**: always the Overlay. An input set to **Only while held** is on the Overlay while
  the key is held down; any other turns on with one press and off with the next. Lime while it's on.

To move an input to another key, drag its tile on the Dashboard. To have an input on the same key in every show, put
it in the same place on each show's Dashboard. The keys made for one input (the other presets) only work in shows
that have that input.

An input set to **Only while held** (its Send to Overlay, in CentraLight's Editor) shows on the Overlay only while its
key is held down. Its key needs **Release (held inputs)** on its release step: the Overlay presets have it, so a key
made from one does it. A key dragged from an Overlay preset before this version hasn't got it: drag the preset again,
or add the action to the key's release. (The Stream Deck app's own Website action only presses, so it can't do this.)

From another computer: tick "Allow other computers" in CentraLight's Settings → Remote, and put that PC's address and
the key from there in the connection.

## If a button does nothing

- The connection is red or says "Disconnected": CentraLight isn't running, or its port isn't the one in the connection.
  It reconnects by itself once CentraLight is back.
- Companion's log (the connection's **Log** button) says why CentraLight refused a press, for example an input that
  isn't in the show loaded now.

## For whoever works on it

- `centralight\main.js` is the module, `centralight\lib.js` its actions, feedbacks, variables and presets.
- Tests: `npm test` in `centralight` (Node 22 or later; nothing to install), and `tests\test_live_m16.py`, which also
  runs every action against a real CentraLight remote on a test port.
- Building the `.tgz`: `npm install` in `centralight` once (it fetches Bitfocus's `@companion-module/base` and
  `esbuild` from the npm registry), then `.venv\Scripts\python.exe tools\build_companion.py`.
- Companion won't import a package whose version it already has. For a new one, raise the version in both
  `centralight\package.json` and `centralight\companion\manifest.json` (the build checks they match), or remove the
  old module in Companion first.
- CentraLight's side is `/api/ws` in `centralight/remote.py` and `centralight/live_socket.py`.
