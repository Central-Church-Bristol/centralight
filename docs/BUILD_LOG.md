# CentraLight GUI — build log

Built from the approved plan (`C:\Users\avbri\.claude\plans\let-s-plan-this-gui-precious-garden.md`)
while you're away (2 Nov → Wednesday 4 Nov 2026, 3pm). Newest news first in each section.

**The real lights have not been touched.** The app's output defaults to **Simulated** (nothing is opened or
sent), every test uses fake outputs (a guard makes it impossible for a test to open a COM port), and COM3 has
never been opened by this work. The rig checklist is in `docs/RIG_CHECKLIST.md`.

## Status

| Milestone | State |
|---|---|
| M0 Foundations | **done** (2 Nov, 22:30) |
| M1 Layout + Identify + Follow screen | **done** (2 Nov, 23:00); its two extras (saved layouts; Undo / Redo / History) added 3 Nov, 00:00 |
| M2 Editor v1 (infinite mode) | **done** (3 Nov, 00:10) |
| M3 Variables, expressions, Stream Deck | **done** (3 Nov, 00:35) — 277 tests pass |
| M4 Automations + always running (tray, start with Windows) | **done** (3 Nov, 01:00) |
| M5 AI inputs | **done** (3 Nov, 01:55) |
| Layout positions only + Blur (your change of 3 Nov) | **done** (3 Nov, 02:17) |
| M6 Timed mode + keyframes (+ video) | **done** (3 Nov, 02:38) |
| M7 Painting and masks | **done** (3 Nov, 02:50) |
| M8 Audio variables | **done** (3 Nov, 03:13) |
| M9 Polish (launcher .exe, Diagnostics) | **done** (3 Nov, 03:54) |
| A review of the whole app (five reviewers, 46 problems) | **done** (3 Nov: part 1 at 05:21, part 2 at 06:00; 522 tests pass) |
| Reviews of those fixes (four more rounds, each checking the one before) | **done** (3 Nov, 09:50; 563 tests pass) |
| M10 Per-light colours (your change of 3 Nov) | **done** (3 Nov, 10:31; 570 tests pass; engine 0.16 ms a tick) |
| M11 Cut to black / Fade to black (your change of 3 Nov), and a look at M10 (five fixes) | **done** (3 Nov, 11:20; 587 tests pass) |
| M12 Shows + global automations (your change of 3 Nov) | **done** (3 Nov, 12:25; 620 tests pass) |
| A look at M12 (fourteen findings) | **done** (3 Nov, 13:25; 635 tests pass) |
| A third 3-hour soak (after M10) | **done** (3 Nov, 13:31; flat memory, no errors) |
| A second look at M12 (ten findings) | **done** (3 Nov, 16:40; 648 tests pass) |
| A fourth 3-hour soak (after M12, loading a show every 2 minutes) | **done** (3 Nov, 17:08; 90 loads, no errors; memory up 3 MB) |
| M13 Show extras (your six extras of 3 Nov, evening) | **done** (4 Nov, 00:20; reviewed by three, about 45 findings fixed; 805 tests pass; all 38 deliberate breakages caught) |
| A 10-hour soak (after M12, loading a show every 2 minutes) | **done** (4 Nov, 03:10; 300 loads, no errors; memory up about 0.8 MB an hour, from NDI itself) |
| A 3-hour soak of M13 (show loads, a linked video replaced while it plays, countdowns) | **done** (4 Nov, 04:16; 90 loads, no errors; it found a video cache with no limit: fixed, 806 tests pass twice) |
| M14, your changes after the rig test (4 Nov), in a separate copy of the code: | |
| — 3 tick boxes, 1 rename, 2 Editor opens paused, 7 Delete removes a shape | **done** (4 Nov, 22:15) |
| — 4 one Overlay (copies, red boxes, hold for a flash, drag tiles to move them) | **done** (4 Nov, 23:30) |
| — 9 a Colour element, and Paint without a fill | **done** (5 Nov, 01:05; a test-suite crash found on the way was fixed first; 853 tests pass twice) |
| — 8 shapes in masks (Show / Hide rectangles and ellipses, Start with, Mask mode with Layer mode's tools) | **done** (5 Nov, 01:25; 866 tests pass); rebuilt below |
| — 6 which week of the month (1st, 2nd, 3rd, 4th, Last) in Days of the week | **done** (5 Nov, 01:35; 875 tests pass) |
| — 5 a default show (opens when the PC starts, comes back after a schedule) | **done** (5 Nov, 01:50; 889 tests pass) |
| — 10 remember or reset each show variable when its show loads | **done** (5 Nov, 02:15; 901 tests pass) |
| — the two extras: every quit in the session log; the soak tool's own folders | **done** (5 Nov, 02:20) |
| — a check where 5 and 10 meet: the default show at PC start and the last show's variables | **done** (5 Nov, 02:45; it can't happen in the app, and a test checks it) |
| — 11 the colour picker updates live (Preview, one undo step, Enter / Esc) | **done** (5 Nov, 02:45; 914 tests pass) |
| — the Clean-up: no compatibility with what you never had (old Overlay links, old file conversions) | **done** (5 Nov, 03:00; 911 tests pass) |
| A look at M14 (three reviewers, 21 findings): the default show's nine | **done** (5 Nov, 03:05) |
| — and the Overlay and Dashboard's six (one went with the clean-up) | **done** (5 Nov, 03:10) |
| — and Colour, Split and mask shapes' six | **done** (5 Nov, 03:15) |
| A second look (two reviewers: item 11, the clean-up and those fixes; 16 findings) | **done** (5 Nov, 04:10; 945 tests pass twice) |
| A 3-hour soak of M14 (show loads, video copies on the Overlay, countdowns, a linked video) | **done** (5 Nov, 07:16; no errors; memory level after the first hour, threads flat) |
| — your rule of 5 Nov: a schedule that ends by going black keeps the default show away | **done** (5 Nov, 09:05) |
| — your change of 5 Nov: masks as black-and-white pictures (Paint's tools in greys, White, Black, Invert) | **done** (5 Nov, 09:50) |
| — your change of 5 Nov: the Default show's **Last loaded** and **None (lights off)** (no show loaded) | **done** (5 Nov, 11:07) |
| — a 1-hour soak going to no show and back (15 times), and three small fixes made meanwhile | **done** (5 Nov, 12:57; no errors; 981 tests pass twice) |
| — your new logo of 5 Nov ("soft light"): in the app, and in CentraLight.exe's and .clshow files' icons | **done** (5 Nov, 13:02) |
| **M15: the Console page** (you said go at 13:30 on 5 Nov) — the design check (screenshots with made-up lights): you said yes, and added the chase's "On: …" line | **done** (5 Nov) |
| — 1 and 2: choosing lights, the Look, fans, Advanced, takeover until Release, everywhere, safety, restarts | **done** (5 Nov, 16:58) |
| — 3: chases (Add step, Quick, timing, Tap, Play / Pause / Next, step on each beat) | **done** (5 Nov, 16:58) |
| A look at M15's engine side (a reviewer, 14 findings, all fixed) | **done** (5 Nov, 17:15) |
| — 4: Save look / Save chase as input (a new Lights element), and Load into Console | **done** (5 Nov, 17:26) |
| **M16: the Stream Deck through Companion** — CentraLight's live connection (`/api/ws`, pushing what's happening) | **done** (5 Nov, 17:37) |
| — the CentraLight module for Companion 5.1: actions, feedbacks, variables, presets; built as `companion\centralight-1.0.0.tgz` | **done** (5 Nov, 17:37) |
| A look at the Console page and the Lights element (two reviewers, 28 findings, all fixed) | **done** (5 Nov, 18:08) |
| **M17: the repo clean-up** (the app alone: the console scripts gone, the output and patch code in the package) | **done** (5 Nov, 18:20) |
| The last reviews: of the Console fixes (11 findings) and of M16 and M17 (7), all fixed | **done** (5 Nov, 19:22) |
| A 2-hour soak of M16 (a live connection open all along, as Companion keeps one) | **done** (5 Nov, 19:50; no errors; every command answered; memory level after 40 minutes) |
| The final 3-hour soak, of the version for Sunday (`d5ea6ac`: all of the above) | **done** (5 Nov, 22:44; no errors; every live command answered; memory level after 40 minutes; its higher CPU is NDI's, checked) |
| A check at 1920×1080 (what Media Background Pane sends since its update of 5 Nov) | **done** (6 Nov, 00:10; fine: about 20 % of one core, every picture arriving, every tick on time) |

Run all the tests (about five minutes, including the layout check at every scaling) from the CentraLight folder:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

## Things you need to know

- **Commits are on the branch `claude/centralight-gui`** (you gave the go-ahead on 3 Nov; never `main`, nothing
  pushed). M0–M4 went in as one commit, because they were finished before commits were allowed; each milestone
  after that gets its own. Before them is a commit with the console-script work (the Eurolite driver and scripts).
  Since M14 the work has been done in a separate copy of the code (`.claude\worktrees\m14`), which has the
  branch checked out. Your main folder stays as it was until it's updated with your OK (`RIG_CHECKLIST.md`,
  step 21). Don't switch branches there by hand meanwhile: git would have the branch checked out twice.
- Your own earlier changes to the logo files in `Assets\` aren't in the commits (they're yours to commit); the
  app draws its logo itself if `CentraLight Logo 1024.png` isn't there.
- The logo files in `Assets/` were already changed (and `CentraLight Logo 1024.png` added) before this work
  started. They're left exactly as they were; the app reads `Assets/CentraLight Logo 1024.png` for its icon.
- `dmx_output.py` got one small change: `close(blackout=True)` now takes an optional flag, so the app can quit
  with "leave the lights as they are". The console scripts don't pass it, so they still black out as before.

## A look at M15's engine side (5 Nov, evening)

A reviewer read the Console's engine side and tried it: 14 findings, all fixed, each with a test.
- **Cut to black now cuts everything at once,** lights still fading back from a Release too (they used to carry on
  with their own, longer fade). Fade to black (and a later Release all) also ends such fades no later than its own.
- **A dimmed light never flares any more.** On Release, Fade to black or a faded change, its colour and its dimmer used
  to fade each in a straight line, and the two multiplied bulge: a light at 20 % brightness went up to about 69 % on
  its way down. Now what the light gives goes straight: the dimmer stays at the higher end and the colour fades. A
  chase's crossfade works the same way.
- **Next step while paused always moves on** (rounding on the PC's big clock numbers made it stick about one press in
  three).
- **A change fades from what the light shows now,** not from an old colour after everything had been let go (a pink
  flash).
- **After a reopen, Live changes made after the chase still win over it** (the chase used to win).
- **Step on each beat catches every beat** (it missed about one in seven at 40 ticks a second), on the Console and in
  a Lights element. Switching it on while the chase plays keeps its place.
- **Lights a new chase doesn't have fade back** with the release fade, instead of snapping.
- **Stop** (not on the page yet) keeps what Live changes hold on the chase's lights.
- **"The colour lights are black" counts the Console:** while it holds a lit light they aren't black, so an automatic
  Cut / Fade to black gets its countdown, and closing the show fades the Console's lights instead of cutting them. The
  Automation tab says so.
- **A broken live.json can't stop CentraLight starting** (the Console then starts empty), and odd numbers in a file
  are put right.
- **A dimmer patched as a colour light** works as one: its brightness follows Master and fades properly.
- Two small ones: random chases work out each pass once (they took five times longer a tick), and a cache can't mix up
  two lights' colours.

## The soaks of M15–M17 (5 Nov, evening and night)

The whole app, as it runs for you but with Simulated output and its own temporary settings and shows (never your
lights or files), following a test NDI picture, while the soak tool:
- loaded the other show every 2 minutes;
- put two copies of a linked video on the Overlay every 90 seconds and cleared them 20 seconds later;
- set a show variable every 10 minutes so an automation counted down and faded to black, then came back;
- replaced the linked video's original every 30 minutes;
- **new for M16:** kept a live connection open all along, as Companion's CentraLight module does: it read every state
  CentraLight sent, sent a command over it every 30 seconds (Release the Console, Master at full, a Tap), and every
  minute opened a second connection that read the state and closed, as a Companion restart would.

**A 2-hour soak of M16** (commit `e41c945`, 17:50–19:50):
- **No errors:** the session log has 170 lines, none of them an error. 60 show loads, 156 Overlay copies, 10
  countdowns (20 runs waited for theirs), and the video's original replaced 4 times.
- **The live connection** stayed up the whole two hours (it never had to reconnect): 724 states received, 240 commands
  sent and every one answered, and 120 second connections opened and closed.
- **Memory** rose for the first 40 minutes as the pictures and videos filled their caches (169 MB, then 197, on
  average per 20 minutes), then stayed level: 215, 216.1, 216.3 and 216.4 MB.
- **Threads** stayed between 42 and 57; **CPU** averaged 10.8 % of one core (0.7 % of the PC), at most 29 %.

**The final 3-hour soak** (commit `d5ea6ac`, the version for Sunday, 19:44–22:44):
- **No errors:** the session log has 252 lines, none of them an error. 90 show loads (every one answered), 234 Overlay
  copies (117 clears), 16 countdowns (30 runs waited for theirs), and the video's original replaced 5 times (12 copies
  made and swapped in).
- **The live connection** stayed up all three hours (it never had to reconnect): 1,098 states received, 359 commands
  sent and every one answered, and 179 second connections opened and closed.
- **Memory** rose for the first 40 minutes as before (169 MB, then 197), then stayed level: 215, 216, 216, 216, 216, 216
  and 217 MB, 20 minutes by 20 minutes (the slow rise of under 1 MB an hour that every soak shows; it's NDI's own).
- **Threads** stayed between 41 and 57 (47 at the end).
- **CPU** averaged 14.8 % of one core (0.9 % of the PC), at most 25 %, against the 2-hour soak's 10.8 %. That
  difference is NDI's, not the app's. On this PC, NDI hands the test picture over in one of two ways and switches
  between them by itself: cheaply (receiving takes well under 1 % of one core) or by decoding it (about 11 % at
  960×540). Either way the picture arrives in full, 24 times a second (checked every 5 seconds). The 2-hour soak mostly
  got the cheap way (its CPU dipped to 1–4 % whenever Evening, which follows the screen, was on); this soak got the
  decoding way all three hours. Checked afterwards:
  - **Side by side with the 2-hour soak's version** (both at once for 20 minutes, 23:03–23:23, the same conditions for
    both): the version for Sunday used less, 15.1 % of one core against 19.3 %: the last changes added no CPU.
  - **Where it goes** (measured inside the app, a minute at a time): receiving the picture about 11 %, the engine 0–4 %
    (the most while a video plays), the window almost nothing. Every tick started on time (40 a second; none more than
    5 ms late). The NDI receiving code is the same as in the version you tried on 4 Nov.

**At 1920×1080**, the size Media Background Pane sends since its update tonight (the supervising session asked for
this check):
- **About 20 % of one core either way** (1.2–1.4 % of the PC), against 6–15 % at 960×540. A 16-minute soak on its own
  (23:23–23:39) averaged 19.6 % (at most 24 %), receiving the picture taking about 17 %; an 8-minute one (00:00–00:08)
  averaged 22.0 % (at most 32 %), receiving 15 % and NDI's and OpenCV's own threads 5 %. A bigger picture costs more
  to take in and shrink, whichever way NDI hands it over. Every picture arrived (24 a second, at 1920×1080).
- **The engine didn't notice:** every tick started on time (2,400 a minute; in 22 minutes one was more than 5 ms late,
  by 7 ms), and a tick's work took 0.9 ms typically and about 4.5 ms at the 99th percentile, as at 960×540 (a tick has
  25 ms). No errors; memory and threads as usual.
- **So 1920×1080 is fine for Sunday.** (If its CPU ever matters, the app could ask NDI for its smaller picture: it only
  uses 320×180 of it. Not changed now: no code changes before Sunday.)

## The last reviews (5 Nov, night)

Two more reviewers checked the fixes themselves, and M16 and M17: 18 findings, all fixed, with tests.
- **Fades on the Console, put right for good.** My first fix for "a dimmed light flares on Release" had the dimmer
  jump to full and the colour make up the difference; cutting such a fade short (a Fade to black part-way through a
  brightness fan, say) could flash or dip the light for a moment. Now the **dimmer fades in its own straight line**
  and only the colour makes up the rest: what the light gives still goes straight, no channel jumps at the start or
  the end, and any change part-way through starts the rest of that light's fades again from what it shows. A chase's
  crossfade works the same way, and a Live colour over a "breathing" chase leaves it breathing.
- **Stop** fades a light to what a Live change held before the chase (it snapped); a chase left on none of its lights
  stops; a dimmer used as a colour light fades from what it shows; Cancel in the colour picker after another show
  loaded holds nothing.
- **The page:** Pause and Next step only work on the chase that's playing, while the page shows that one; a tempo from
  Tap or the Stream Deck isn't undone by the next change on the page; a late frame can't bring back steps you've just
  removed.
- **Companion:** when CentraLight goes away every variable empties (some kept their values); changing the
  connection's address doesn't leave the old state looking live, and a connection that doesn't open within
  5 seconds is tried again; at the 8-connection limit it says so (it said CentraLight needed updating).
- **The docs:** the rig checklist's fallback (the old pixel-mapper script went with M17) is now MagicQ, or switching
  back to the version of 4 Nov; its step 21 describes Sunday's update of the main folder (the old git commands would
  now confuse git: the branch is in use by the separate copy); the README's start and Companion lines are right.

## M17: the repo clean-up (5 Nov, evening)

As you asked on 5 Nov ("a nice clean repo that just contains the centralight app"):
- **Gone** (git's history keeps them): `ndi_pixel_mapper.py`, `usb_dmx_test.py`, `usb_dmx_patch_test.py`,
  `artnet_test.py` (the console scripts the app replaced), and `tools\ndi_follow_check.py` (a one-off check).
- **Your decision (5 Nov, 22:06):** the old pixel-mapper script stays removed, not kept as a fallback until after
  Sunday. If CentraLight misbehaves, the fallbacks are MagicQ or switching back to the version of 4 Nov
  (`RIG_CHECKLIST.md`, step 20).
- **Moved into the app's package:** `dmx_output.py` (the Eurolite USB and Art-Net output) and `fixtures.py` (the
  church's patch, now only what a new show starts from) are in `centralight\`. The test that the app sends the same
  bytes as the first scripts did now checks the moved code; the notes file (`fixture_map.csv`) is still read and
  written in the old script's format.
- **The rig checklist** is `docs\RIG_CHECKLIST.md` now (it was `WEDNESDAY_TESTS.md`; older entries in this log use
  the old name).
- **The README** is about the app alone: starting it, what's in it, shows and the AI guide, the Stream Deck and
  Companion, the lights, the fonts.
- `requirements.txt` stays as it was: the app uses all of it. The main folder's own copies of the removed scripts go
  when it's next updated.
- **Checked:** nothing imports a removed file; the full suite twice; the app started from a fresh copy (Simulated, with
  and without the fonts). `run.bat` on a brand-new copy would install the packages from the internet, so that's left
  for when it's next needed.

## A look at the Console page and the Lights element (5 Nov, evening)

Two more reviewers tried the Console page (and its wiring into the window) and the Lights element: 28 findings, all
fixed, with tests. (The full suite: 1083 tests; one speed test, painting with the biggest brush, ran slow while a
soak ran alongside, and passed run again on its own.)
- **A chase loaded onto the page while another plays** stays on the page until you press **Play**: changing its
  speed or steps no longer swaps the playing chase without asking, and the page no longer mixes the two.
- **Typing a speed while the chase plays** works (1.5 s used to come out as 1.00), and the step cards aren't rebuilt
  every frame while you drag Crossfade.
- **Cancel in the colour picker** puts back exactly what was held (just Red held stays just Red; a chase played since
  still wins).
- **With Master below 100 %**, the Advanced faders and the Look show what's set, not what Master lets out (a Red
  dragged to full no longer springs back to half).
- **Ctrl+5** (the Automation tab since the Console came) can't be an input's hotkey any more.
- **Load into Console** shows the loaded look in the Look panel, and the Editor's button loads the Lights element you
  were looking at.
- **Save as input** keeps changes made while its name dialog was open, and its message says that while the Console
  holds the lights, it wins over the input.
- **The header** no longer lays itself out over and over on a narrow window.
- Removing every step of a playing chase stops it; step cards show brightness and what a light left out of a step
  shows; Odd and Even follow the Order; a saved group with none of its lights in the show has no button; a menu leak.
- **The Lights element:** a speed changed while it plays carries on from where it is (dragging its BPM made the lights
  flicker); a saved look goes on the top track, so a background added later goes under it; its picture follows a
  light that's moved; step on each beat starts again when a timed input loops; Load into Console starts from keyed
  speeds; the Editor's picture of it costs a fraction of what it did; an AI's odd values can't break the Editor; the
  AI guide says what a light a step leaves out does (it keeps the step before's look); importing an input and the AI
  check know the lights its steps name.

## M16: the Stream Deck through Companion (5 Nov, evening)

**A CentraLight module for Bitfocus Companion** (5.1 or later), in the `companion` folder. Its keys light up with
what's happening, the moment it happens, and pressing one does what CentraLight's Stream Deck URLs do. Putting it in
Companion: Modules → **Import module package** → `companion\centralight-1.0.0.tgz`, then add a **CentraLight**
connection (127.0.0.1, port 8746). `companion\README.md` has the steps. The module is built as `companion\centralight-1.0.0.tgz`.

- **Actions** (with dropdowns of the loaded show's inputs, shows, lights and automations): an input to Base (its own
  transition, a Cut, or one chosen there), a copy to the Overlay, its copies cleared, Clear the Overlay, the Overlay's
  switch; load a show; Cut / Fade to black (the colour lights, or everything); Master; the Festoon and Key lights
  (level, on / off, fade); a show variable; run / turn on / turn off an automation (the show's or a global one), pause
  or resume them all; the countdown's Do it now / Wait / Skip; the Console's Release all, Release some lights, Tap,
  Next step, Pause / Play on and speed.
- **Feedbacks:** an input on Base (coral) or on the Overlay (lime), a show loaded, the colour lights black, a
  countdown running, automations paused, the Festoon or Key lights on, a show variable's value (equal to, above,
  below), the Console holding lights, and CentraLight connected.
- **Variables:** the show, what's on Base, Master %, the countdown's seconds and what it will do, the next timed
  automation, how many lights the Console holds, each house light's level, each input's copies on the Overlay.
- **Presets:** each input to Base, to the Overlay (with its copy count) and "flash while held"; each show; the four
  Cut / Fade to black keys; the Console's Release all (with its count), Tap and Next step; Pause automations.
- **It looks after itself:** if CentraLight closes, the connection goes red and comes back by itself when CentraLight
  starts again (after 1, 2, 5, then every 10 seconds). When the show changes, the dropdowns and presets follow.

**CentraLight's side, the live connection:** a WebSocket at `/api/ws` on the remote's own port, with the same rules as
every other URL: this PC only, unless "Allow other computers" is on, and then with the key; web pages from other
websites are refused. It sends everything the module shows straight away, then again each time something on it
changes (looked at ten times a second, sent only on a change), and takes commands as the URLs' paths (or as
`POST /api/command`'s JSON). The plain URLs are as they were.

**Tests:** the connection's handshake, frames, pings and refusals (the same as the URLs'); the state at once and on
each change but not every frame; commands both ways; a stopped remote and a silent client closing it; the module's
own tests (with a pretend CentraLight); every one of the module's actions against a real CentraLight remote on a
test port, with Companion's own Node 22; and the built package itself, started the way Companion starts a module (a
stand-in for Companion's module host): it connects, makes its actions from the show, lights the key of the input on
Base, and a press reaches CentraLight. The soak tool has `--live 30`: a live connection all along, a command every 30
seconds and a second client every minute.

**Building the package** (only when the module changes): `npm install` in `companion\centralight` (Bitfocus's
`@companion-module/base` 1.11.3 and `esbuild` 0.28.2 from the npm registry, as you allowed; they stay in
`node_modules`, which isn't in git), then `.venv\Scripts\python.exe tools\build_companion.py`.

**Checked by a reviewer before it went in** (11 findings, all fixed):
- a show variable switch's key now lights for "on" and "off" (CentraLight sends switches as 1 or 0);
- when CentraLight goes away the keys go dark and the variables empty, instead of looking live; a new "CentraLight is
  connected" feedback makes a warning key (inverted);
- a host typed with its port or "http://" is understood, and one that isn't an address shows as a bad setting;
- a refused connection says why: a wrong key, too many connections, or a CentraLight too old to have the live
  connection;
- the countdown keys name the countdown showing (as the phone page does), and a renamed connection's presets follow;
- CentraLight's side shrugs off misbehaving clients: one pinging all the time, a frame trickling in, a connection
  that sends nothing (now closed after 15 s; that was true of the phone page's URLs too), a key with odd characters.
Writing their tests also showed the module never tried again after a first failed connection: fixed.

**A slip, caught:** while making the stand-in Companion test, its first version let the module use its default
address, `127.0.0.1:8746`, the real remote's port, for a few seconds. Nothing was listening there (Windows refused
each try), so nothing reached your CentraLight. The test now refuses that port and checks it's talking to its own
test server.

## M15: the Console page (5 Nov, afternoon on)

A hands-on lighting desk, on its own tab after the Dashboard (you saw it with made-up lights first, said yes, and
added one thing: the chase's "On: …" line). Parts 1 to 3:

**Choosing lights.**
- **On the room map** (as on the Dashboard): click a light's dot; Ctrl- or Shift-click adds or takes one away; drag a box
  round several (from a gap between lights). Chosen lights have a blue ring and their number in the order.
- **Buttons above the map:** All, None, Invert, Odd, Even, each group of the patch (Top Wash, Side Fill, Pillars), your
  saved groups, and **Save as group…** (saved with the show, with undo).
- **Order** below the map: as clicked, left to right or right to left. Fans, chases and Odd / Even follow it.

**The Look** (for the chosen lights): **Brightness** (the light's own dimmer), **Colour** (the picker; with Preview the
lights follow as you pick, and Cancel puts back what they had), the show's **saved colours** (click one to use it, + to
save the colour above, right-click to remove one), **Fan** (a rainbow, from one colour to another, or brightness from one
% to another, across the chosen lights in their order) and **Fade** (how long a colour, a saved colour or a fan takes;
faders follow your hand).

**Advanced** (the switch at the top right): every channel of the chosen lights as a 0–255 fader (Red, Green, Blue,
Amber, Colour macro, Strobe, Program, Dimmer, Dimmer speed), saying "mixed" when the lights differ, with a short
photosensitivity note under Strobe.

**The Console takes over until Release**, like a desk's programmer:
- What you set wins, light by light and channel by channel; what you haven't touched keeps following the inputs.
  Between a change on the page and a chase, the later one wins.
- **Release** (the chosen lights) and **Release all** hand them back over 2 s (Settings → Cut / Fade to black →
  "Console: Release takes"). Strobe, the colour macro, programs and dimmer speed go back at once.
- **Everywhere:** the header chip "Console: 5 lights" on every page while it holds any (click it: Release all), a
  "Console · release all" button on the phone page, `/api/live/release` for the Stream Deck (`?lights=…` for some),
  and an automation action **Release the Console** (show and global), for the end of a service, say.
- **Safety:** Master still scales the Console's colours; **Cut / Fade to black let go of everything** the Console
  holds, cut or faded with the rest; Identify and the Layout tab's test colours show on top; the padlock locks the page.
- **Restarts:** reopening CentraLight carries on with what the Console held (and its chase, where it was). The PC
  having just started, or another show loading, lets it all go.

**Chases** (part 3):
- **Steps:** **Add step** takes the chosen lights as they look now. **Quick** makes the steps for you: the saved colours
  across the lights, one light at a time, or odd / even. Choose a step card to move it (◀ ▶), **Copy** or **Remove** it.
- **Timing:** **Speed** in BPM (a step a beat) or seconds a step, with **Tap**; **Crossfade** (0 % snaps, 100 % glides
  all the way); **Direction** (forward, backward, bounce, random); Loop or Play once; or a step on each beat the audio
  hears (Settings → Audio).
- **Play** puts it on its lights (held until Release), **Pause**, **Next step**; speed and the rest change while it plays.
- **The line above the steps** says which lights the chase is on (your addition): "On: SlimPAR 101, SlimPAR 103", or
  a group's name for a whole group ("On: Pillars"). It follows the chase as steps are added or lights are released.
- **From the Stream Deck:** `/api/live/tap`, `/api/live/next`, `/api/live/pause`, `/api/live/resume` and
  `/api/live/speed?bpm=120`.

**Save as input** (part 4):
- **Save look as input…** (the chosen lights, as they look now) and **Save chase as input…** make a new input on the
  Dashboard with one **Lights** element: it sets just those lights, and the others show what's under it (the tracks
  below, or black). A chase keeps its speed, crossfade, direction, loop or once, and step on each beat. The dialog
  says it: inputs carry colour and brightness only; amber, strobe, the colour macro and programs stay on the Console.
- **Load into Console** (the input's tile menu, or the Editor's Lights settings) brings it back: a look is held as it
  was; a chase goes onto the page, ready to Play. Change it and save it again.
- **In the Editor** a Lights element shows its steps as cards, and its timing settings (speed and crossfade can have
  keys or formulas, like other settings). It's in the AI guide and input.schema.json, so an AI tool can write one too.

## M14: your changes after the rig test (4 Nov, night)

Built in a separate copy of the code (`.claude\worktrees\m14`, the same branch), so the main folder keeps the version
that ran tonight's prayer meeting. Everything was tried with Simulated output and temporary settings only.

The small ones first (3, 1, 2, 7):
- **3. Tick boxes are always visible.** An empty tick box has a grey border on every background (and a lighter one
  under the mouse); a ticked one is filled pale blue with a dark tick, and a part-ticked group (Lights on the
  Selected tab) shows a dash. Lists and trees with ticks look the same. The tick pictures are two tiny files beside
  the style sheet (`centralight\ui\check.svg`, `check_mixed.svg`).
- **1. Rename an input more easily.** A tile's right-click menu has **Rename…**, and the name at the top of the
  Editor ("Editing: Warm white") can be clicked and typed over: Enter or clicking away keeps it, Esc leaves it.
  Both are one undo step, like the Input tab. Stream Deck keys and automations keep working, as they use the
  input's id, not its name.
- **2. The Editor opens paused.** Opening an input (infinite or timed) leaves the playhead at the start; press play
  to see it move. Opening another input pauses again.
- **7. Delete removes the selected shape.** In the Selected view, Delete or Backspace removes the selected shape,
  as Remove on the details panel does (one undo step, "Remove Rectangle S1"). With no shape selected it does
  nothing, it never removes the element (only the timeline's Delete does that), and typing in a box isn't
  affected. Clicking the Selected view gives it the keys.

**4. One Overlay instead of Overlays 1–4.**
- **Base and the Overlay.** Each Send to Overlay puts a new copy of the input on top of what's already there, so
  inputs that touch different lights don't cancel each other out, and one input can be sent again and again (to
  the beat, say). A copy appears with the input's own transition and leaves the same way: when it's cleared, when
  a held Send to Overlay is let go, and, for a timed input, when its timeline has played. Up to 4 copies of one
  input at once.
- **Tiles:** **Send to Base** and **Send to Overlay** (lime) replace the 1–4 buttons; the pencil and the sparkle
  moved up beside the name. Clicking the tile itself does nothing now, and dragging it moves it (a pale-blue line
  shows where it'll go; one undo step, "Move Warm white"). Under the buttons, a **red box for each copy** on the
  Overlay, a quarter of the tile wide: a timed copy's box fills as it plays, an infinite one's is full, and
  clicking a box clears that copy. With 4 boxes, Send to Overlay greys out until one goes.
- **Hold Send to Overlay** for a flash: the copy goes on as soon as it's pressed; let go within half a second and
  it stays (a click), hold it longer and it clears when you let go.
- **The Overlay row** on the Dashboard: how many copies there are (no input names), **Clear all**, its switch
  (off hides it without emptying it; sending to it turns it on again) and its opacity. The right-click menu has
  Send to Overlay and Clear from Overlay.
- **Automations:** Send to Overlay, Clear from Overlay (that input's copies) and Clear the Overlay. "An input is
  live … in the Overlay" replaces "in Overlay 2".
- **Stream Deck and the phone page:** `/api/input/<id>/overlay` sends a copy, `/overlay/clear` clears its copies;
  `/api/overlay/clear`, `/api/overlay/on`, `/off`, `/toggle` and `/opacity`. The phone page has Overlay and Clear
  on each tile, and On / off and Clear all for the Overlay. (The Overlay 1–4 URLs went in the clean-up below.)

**9. A Colour element, and Paint without a fill.**
- **Colour** is first in the Editor's element list (with a swatch icon): a solid colour, or a straight or round
  gradient, across the screen. It's exactly what a Paint element's fill was, so it looks the same on the lights, and
  it has the shared opacity, blend, move / size / rotation and mask; its settings can have keyframes or formulas.
- **Paint** is now shapes and brush painting only: a new Paint element is see-through until something is painted on
  it, so the tracks below show through. For shapes over a colour, put a Colour element on a track below.
- **Older inputs** look exactly as before (checked by tests, on the lights' colours):
  - a Paint element that's only a fill becomes a Colour element when the input is read (tonight's single-colour
    input is one of these), and is saved as one from then on;
  - a Paint element with a fill **and** shapes or painting keeps its fill, shown as a **Background** group in its
    settings (only on those older elements). **Split into Colour + Paint** moves the fill to a Colour element of its
    own directly under it, with the same mask and move / size (one undo step). If the track below isn't free, the
    painting and the tracks above it move up a track into an empty one (as in your two shows' inputs, whose painting
    is on track 1). A line under the button says whether it'll look exactly the same on the lights, or what would
    change; it's greyed out if there's no empty track. Setting the Background to None makes it an ordinary Paint
    element.
- The AI guide (and so the AI prompts) teaches Colour, its examples use it, and the input schema has it.

**8. Masks: black-and-white pictures** *(rebuilt 5 Nov, morning, as you described: "in my head masks should
essentially just look like black and white images")*.
- **A mask is a black-and-white picture over the element:** white shows it, black hides it, and greys show it partly.
  Every mask starts white everywhere. Each light reads the mask at its dot, and the Lights ticks still go on top (an
  unticked light is left alone, whatever the mask says).
- **Mask mode has Paint's tools,** in greys: Select, Rectangle, Ellipse, Brush, Eraser (it rubs painting out, back to
  white), Bucket, Gradient and the eyedropper (it picks a grey from the mask), plus the Light bulb for the ticks. Brush
  size and softness work as in Layer mode.
- **Greys only:** a **Grey** button (0 % black to 100 % white) for the brush, the bucket, the gradient and new shapes,
  with quick **White** and **Black** buttons beside it. It starts on black, which is what does something on a white
  mask.
- **Invert** turns the whole mask round: white becomes black and black white, the greys in between too. That's its
  painting and its shapes, keyframes and formulas included; the ticks stay as they are. It's one undo step, and
  pressing it again puts everything back exactly.
- **Mask shapes:** a rectangle or ellipse drawn in Mask mode (Shift: square, circle), in the current grey, edited on the
  Selected view like a Paint shape: position, size, rotation, softness, its **Grey** and its opacity, each with
  keyframes or a formula. They go over the painting, in the order of the **Mask shapes** list on the details panel
  (+ Rectangle, + Ellipse, Forward / Back, Remove). Delete removes the selected one in the Selected view.
- **The Selected view in Mask mode shows the mask itself,** black and white, with the light dots on top; the Result view
  shows what it does.
- **Masks from before look exactly the same:** checked on copies of both your shows (every light, at 0, 1 and 5
  seconds: no difference at all), including Evening's painted mask and ticks.
- **An animated mask works:** a black rectangle over everything with a white one sliding across reveals the lights one
  by one, and a black rectangle with a fully soft edge fades an element out across the room (both are AI guide
  examples; the guide and the input schema explain the greys).

**6. Which week of the month.**
- **"Days of the week" has "weeks:" now:** **Every week** (the default, as before), or any of **1st**, **2nd**,
  **3rd**, **4th** and **Last**. Wednesday + 1st is the first Wednesday of each month. **Last** is the last one in the
  month, whether that's the 4th or the 5th. The weeks apply to every day ticked (for the 1st Wednesday and the 3rd
  Sunday, use two automations, or two Days conditions with Any).
- **Summary lines** say it: "1st Wed", "1st & 3rd Sun", "Last Fri".
- **The week schedule and Coming up** follow (the schedule's later weeks show only the right Wednesdays), and an
  overnight time range still belongs to the day it started (the 1st Wednesday's 23:00–01:00 carries on into Thursday).
- The AI guides explain `"weeks"`. Automations from before stay "Every week" (the setting is left out of the file).

**5. A default show.**
- **Settings → Starting and quitting → Default show:** **Last loaded** (as before: a PC start opens the last show, and
  nothing loads when a schedule ends), **None (lights off)** (below), or one of the shows. Settings saved before read
  as Last loaded.
- **When the PC starts,** the default show opens instead of the last one. Global automations then run as before, with
  catch-up, and win: one that's under way (Sunday 10:00, say) loads its show.
- **After a schedule ends:** when the global automation that loaded the show stops matching, the default show loads
  again, with the usual countdown (Load now / Wait 10 min / Skip) and freeze. Not if:
  - someone has loaded a show by hand since (the Show menu, the Stream Deck, the phone page): **manual wins**, and a
    show loaded by hand during the default's countdown stops it;
  - that automation's own "When it stops matching" loads a show: **that wins**;
  - that automation's own "When it stops matching" is Cut or Fade to black (your choice, 5 Nov): **the lights stay
    off**, and nothing loads (not a longer schedule's show either, nor the default show later);
  - another automation that loads a show matches then (back-to-back schedules): its show stays, and the default comes
    when that one ends.
- **The week schedule** shows the default show in the gaps, drawn lighter (hover for what it is; it has no automation
  to open).
- **Two shows asked for in the same second** (your "When CentraLight starts → Load a show" at the top, and a schedule
  that's under way): a test checks both load, in the automations' order, and the second stays.
- **None (lights off)** *(your change of 5 Nov, as the mock-up you agreed)*: no show at all.
  - **A PC start opens no show,** and when a schedule ends the show closes: its countdown ("Closing the show (lights
    off)", with Do it now / Wait 10 min / Skip), then the lights fade off over Fade to black's time (the Festoon and
    Key lights too), then the show closes. The same rules as a default show: a show loaded by hand since wins, the
    schedule's own "When it stops matching" wins, and a longer schedule that still matches gets its show back (the show
    closes when that one ends).
  - **With no show loaded,** CentraLight keeps sending, all 512 channels at 0, so no light keeps a colour an earlier
    show sent it. The global automations carry on: one that loads a show brings it in as any load does (the lights
    from black, its "When this show loads" automations run). There are no show automations, inputs, house lights or
    show variables meanwhile.
  - **The header** reads "Show: None"; the Festoon, Key lights, Master and Cut / Fade to black are greyed out, saying
    "Load a show first". Lock, Settings and the status chips work as usual.
  - **Every tab but Automation** shows the card: the logo dimmed, "No show loaded", "The lights are off. Load a show
    to bring them up.", a **Load** button for each show, **New show…** and **Import a show…**, and a line saying
    whether the global automations will load one ("Global automations are still on. Next: Sunday Morning Service,
    Sun 07:00 → Central Church"), or that they're paused, or that none will in the next 7 days.
  - **The Automation tab** still edits the global automations; "This show" says no show is loaded.
  - **The phone page** says "No show loaded", with the show switcher (and the Stream Deck URLs that load a show).
    URLs for inputs, the Overlay, show variables, Master, the house lights, Cut / Fade to black, Identify, the show's
    automations and layouts answer 404 "No show loaded"; loading a show, the global automations, pausing them, the
    countdown, the shows and the state still work.
  - **The week schedule** draws the gaps as "No show (lights off)", in grey.
  - **Reopening** after a quit or a crash carries on with no show.

**10. Remember or reset each show variable.**
- **Each show variable's card** (Editor → Show variables) has **When the show loads: Keep its last value / Go back to
  its starting value**. Keep is the default, as before, so your shows behave the same. The value it goes back to is
  the card's **Starting value** (it said "Starts at" before).
- **"The show loads"** means what the "When this show loads" automations mean: a person or a global automation loading
  it, or the PC having just started. Not CentraLight reopening after a quit or a crash and carrying on, so a crash
  restart doesn't change the lights.
- **The reset comes first,** before the show's "When this show loads" automations run, so one of them can still set a
  variable as the show loads. The Dashboard's Variables panel shows the new value straight away.
- **Files:** show.json has `"on_load": "reset"` only on the ones that reset; older files load as Keep. Import / export
  carry it, and the AI guide's table of show variables says which reset.

**11. The colour picker updates live.**
- **While the colour window is open,** each change (dragging in the square, the hue slider, typing a hex value)
  applies at once: the viewports follow, and if the input is on the lights (on Base, in the Overlay, or Show on
  lights), the lights follow in real time. If it isn't, nothing changes on the lights, as before.
- **"Preview"** under the picker, ticked. Unticked, the colour applies only on OK, as before (and unticking it
  mid-pick puts the colour back until OK). CentraLight remembers the choice until the PC restarts; after a restart
  it's ticked again.
- **One undo step** for the whole pick. **Cancel** (or Esc) puts the colour back with no step left behind; **OK** (or
  Enter) keeps it.
- **At most about 20 changes a second** reach the show and the lights, and the show saves once the window closes.
- **The same for every colour that changes the lights:** element and shape colours, an input's colour variables, and
  the Dashboard's show-variable colours (Cancel puts the variable back). A tile's colour and a show variable's starting
  value don't change the lights, so they keep the plain picker (OK applies it).
- **Two things found on the way (fixed):** Qt's picker, used inside another window, closed just itself on Esc (leaving
  an empty window) and made Enter start "Pick Screen Color"; and if the Editor's settings were rebuilt while the
  window was open (a global automation loading another show, say), saving would have stayed off until CentraLight
  restarted. Tests check both.

**The two small extras.**
- **Every quit is in the session log** now, before CentraLight stops, with the choice made: e.g. "Quit by closing the
  window: turn the lights off (Settings say so)", "Quit from the tray menu: leave the lights as they are (chosen when
  asked, and remembered)", "Quit, as Windows logs off or shuts down: …", and a cancelled quit too. So a quit can be
  told from a crash even if stopping gets stuck ("CentraLight closed" still follows once it's done).
- **The soak tool's own lines** (making its shows, allowing its linked video) now go to the soak's own temporary
  session log, like the app it runs: never your real one.

**A check where 5 and 10 meet: the default show at PC start, and the last show's variables.**
- **The worry** (the supervising session's, 5 Nov 02:25): when the PC starts and the default show opens, live.json
  still holds the last show's variables. The engine starts the show it's given from those, so the default show could
  start from the last show's values (where both have a variable of that name), and the last show's own be lost.
- **What happens:** it doesn't. Since M12, CentraLight gives the engine the saved state *for the show it opens*: the
  last show's variables join the other shows' saved values, the opened show's own come out, and the last show's Base,
  Overlay and automation state stay with it. Nothing else makes an engine from another show's state.
- **A test now checks it,** with Keep and Reset variables of the same names in both shows: the default show starts
  from its own values (or its starting value), and loading the last show again brings that show's own back. The
  engine's description says what it must be given.

**Clean-up: no compatibility with what you never had** (your words: "I never set up any stream deck stuff so you can
remove all the legacy stuff to keep it clean"). A commit of its own, so it's easy to see and undo.
- **Removed:**
  - the Overlay 1–4 links: `/api/input/<id>/overlay/<n>` with `?state=on / off`, `/api/overlay/<n>/off` and
    `/opacity`, and the POST forms with `slot` or `overlay_off` (refused now). Send to Overlay's toggle
    and once went with them, as nothing else used them;
  - the conversions of older automation files: Blackout (condition and action), a show's own "The PC starts",
    "CentraLight starts" and "The PC wakes" (they became "When this show loads" in M12), and Overlay slots;
  - the old live.json keys: a Blackout from before Cut / Fade to black, and Overlays 1–4;
  - the Blur note (an old element's `blur` is just an unknown setting now);
  - their tests (each replaced by one that checks the general rules still catch such a file), and their mentions in
    the build log and the rig checklist. The README and the AI guide had none.
- **Kept:** the general safety handling (an unknown condition switches its automation off and an unknown action is left
  out, each with a warning; bad files are refused), the empty versioned-migration framework, M13's media with no known
  original, and item 9's Paint-fill conversion (your inputs need it).
- **One thing to know:** your live.json is from the version you ran on Wednesday, which had Overlays 1–4. So the first
  time this version opens (if it carries on rather than the PC having just started), Base carries on as usual but the
  Overlay starts empty, even if something was in an overlay when you quit.

**A crash the tests found (fixed).** With item 9's tests added, the whole test suite crashed part-way through. The
cause was older than M14's changes (tests had just never lined up to show it):
- A closed show document still had its once-a-second "did the save work?" check running. Its undo history points
  back at it, so Python frees it later, while tidying up, on whichever thread happens to be running then. Qt can't
  stop a timer from another thread, so the timer stayed, and fired at memory that had been freed.
- Only the tests close documents and carry on. CentraLight keeps its two for as long as it runs and closes them as
  it quits, so the app you ran on Wednesday wasn't affected.
- **Fixed:** closing a document stops all its timers (a new test checks it).
- How it was found: a short run of 75 tests that crashed every time; a small crash locator (a Windows exception hook
  for the test process only) showed a timer firing for an object that had gone; and Qt's own warning, "Timers cannot
  be stopped from another thread", which only shows once Qt's messages are sent to the console.
- **Found on the way, not reachable today:** if Qt ever copied one of CentraLight's drawn icons in order to change
  it, the copy's drawing code would be freed straight away (a PySide quirk), and the app would crash. Nothing in
  CentraLight changes an icon after making it, so it can't happen now. It's noted as a follow-up.

## A soak of no show (5 Nov, 11:27–12:27, and a shorter one after)

The whole app, as it runs for you but with Simulated output and its own temporary settings and shows (never your
lights or files), with the Default show set to **None (lights off)**, for an hour, following a test NDI picture, while:
- global schedules loaded Central Church for 2 minutes, then left 2 minutes with no show, 15 times: each schedule's
  end counted down ("Closing the show (lights off)", 15 s), faded the lights off (2 s) and closed the show, and the
  next one loaded it again from no show, its "Start up" sending Follow screen;
- two copies of a linked video went on the Overlay every 90 seconds (45 copies; while no show was loaded the phone
  page's URL answered "No show loaded", as it should), and the video's original was replaced once.

Results:
- **No errors:** the session log has 156 lines, none of them an error. The show closed 15 times and came back 14 times
  (the first schedule found it loaded already).
- **Threads** went back to 23–24 every time the show closed (up to 50 with the videos playing), so nothing was left
  open.
- **CPU** averaged 10 % of one core (0.6 % of the PC); with no show loaded it's about nothing.
- **Memory** rose steadily through the hour, about 40 MB (with no show loaded: 146, 155, 169 and 180 MB, quarter hour by
  quarter hour), much as in the first hour of the 3-hour soak below, which then stayed level; an hour can't show
  whether this one levels off too.
- **A second, 30-minute soak with only the closing and loading** (every 2 minutes, no video or Overlay; 12:27–12:57):
  14 closes and 14 loads, no errors, and memory with no show loaded at 113, 115 and 116 MB, ten minutes by ten
  minutes. The closing and loading itself costs little, and less as it goes on, so most of the hour's rise came with
  the videos, as in the soaks before.
- **Three small fixes made meanwhile** (one found by closing and loading 150 times in a test, two by reading the code
  again): each close left an empty undo history behind (half a kilobyte); with no show loaded, Settings' **Open show
  folder** and **Export Claude skill…** are greyed out (the first would have opened CentraLight's own folder); and the
  10-minute backup skips (it looked in an empty path and found nothing).

## A 3-hour soak of M14 (5 Nov, 04:16–07:16)

The whole app, as it runs for you but with Simulated output and its own temporary settings and shows (never your
lights or files), for three hours, following a test NDI picture, while the soak tool:
- loaded the other show every 2 minutes (90 loads, every one answered);
- put two copies of a linked video on the Overlay every 90 seconds, a second apart, and cleared them 20 seconds later
  (234 copies, 117 clears): each copy with a video reader of its own, taken over or closed afterwards;
- set a show variable every 10 minutes so an automation counted down and faded to black, then came back (16 times, 26
  runs waited for their countdown);
- replaced the linked video's original every 30 minutes (5 times: 12 copies made and swapped in).

Results:
- **No errors:** the session log has 250 lines, none of them an error.
- **Memory** rose for the first hour as the pictures and videos filled their caches (143 MB to about 205), then stayed
  level: 210 MB, 210, 211 and 212 for the next four half hours, the same slow rise of under 1 MB an hour that every
  soak shows (it's NDI's own).
- **Threads** stayed between 42 and 56 the whole time (44 at the end), so no video reader was left open.
- **CPU** averaged 6.6 % of one core (0.4 % of the PC), at most 17 %.

## A look at M14 (5 Nov, early morning)

Three reviewers read M14's commits (the Overlay and the Dashboard; Colour, Split and mask shapes; the default show,
the weeks, show variables and the extras), and tried what they found with scripts (Simulated output, temporary
folders). 21 findings. Fixed, each with a test:

**The default show (item 5)**
- **A PC restart in the middle of a service left the default show on.** Sunday 09:00–12:30 had loaded Morning; the
  PC restarted at 10:15; the default show opened, and Sunday Morning, still "matched" from before, didn't run again.
  Now, when the default show opens instead of the last one, the global automations under way that load a show run
  again (a catch-up), so Morning comes back.
- **A default show waiting for its countdown loaded over a new schedule** (Wait 10 min pressed, and Youth starting
  meanwhile; or its minute being up just as the next schedule began). Now a schedule starting cancels it, as a show
  loaded by hand does.
- **Back-to-back schedules lost track of each other, depending on their order in the list,** so the default show
  never came after the second. Fixed. And when a schedule inside a longer one ends (Sunday 18:00–20:00 inside "all
  Sunday"), the longer one's show comes back, and the default show waits for that one to end (see the decisions).
- **A quick reopen after a crash forgot the default show** for its first check (the window told the engine a moment
  too late), so a schedule that ended meanwhile left its show on. The engine knows it from the start now.
- **An automation whose conditions depend on the loaded show** ("mood above 0.5 → Load Evening", where Evening's mood
  is 0) flipped back and forth with the default show every few seconds. Only automations with clock, feed or sound
  conditions hand back to the default show now.
- **Run now on a global automation that loads a show** counts as a person's choice now: the default show doesn't
  replace it when the schedule that loaded the show before ends.
- **The week schedule** showed the default show after an automation whose "When it stops matching" loads a show; that
  show stays, so the default isn't drawn there now.
- **"The PC starts" was worked out twice**, a few seconds apart (by the window, to choose the show, and by the engine):
  at about ten minutes after a start they could disagree. It's worked out once now.
- **The session log said "(Settings say so)"** for a quit as Windows shuts down with Settings on Ask: it says "(there's
  no time to ask)" now.

**The Overlay and the Dashboard (items 4 and 3)**
- **Two copies of one video input shared one video reader,** which jumped back and forth between their two moments
  every tick: each showed the wrong picture most of the time, and with a 720p video the engine waited 23 ms a tick for
  it (a tick is 25 ms), so every light could stutter. Each running copy (on Base, or each on the Overlay) has its own
  reader now; a reader nobody has used for 10 seconds closes, as before.
- **A timed copy cleared while it played jumped at its end as it faded out** (one set to Loop went back to its start, one
  set to Stop vanished). On the Overlay a timed input plays once, so now it stops at its end while it fades.
- **A deleted input's copies stayed on the Overlay,** and with its tile gone only Clear all could clear them (a held
  flash whose input was deleted mid-hold stayed too). Now they leave the way they came in. The delete question says
  so.
- **Dragging a tile dropped inputs whose files couldn't be read from the order,** so when such a file was fixed its tile
  came back at the end. They keep their place now.
- **A greyed-out part-ticked box looked empty** (the padlock on, or a read-only show: the Selected tab's light groups).
  It's grey and shows its dash now, like a greyed-out ticked box.
- **"On" and "on / off" Overlay automations from before M14 lost their meaning when converted:** gone with the
  conversions themselves, in the clean-up.
- **Not fixed, a follow-up:** dragging a tile runs Qt's drag from inside the tile's own mouse handler. If a show load (an
  automation, the Stream Deck) replaced the tiles during the drag, the tile could be freed while its handler runs. The
  reviewer couldn't make it happen, and neither could I; it's noted to look at.

**Colour, Split and mask shapes (items 9 and 8)**
- **Split's line said "It looks exactly the same" when it wouldn't:** in a timed input where the Paint element crosses
  the element next to it on its track (the Colour element, on its own track, wouldn't take part in the crossing), and
  when the Background's colour uses random() (it picks again for the new element). The line says so now.
- **A Colour element whose "fill" isn't an object** (an AI writing `"fill": "#FF0000"`) showed the default colour without
  a word, and the timeline's bar text raised an error each time it was drawn. The AI check says what's wrong now, and
  the bar copes.
- **Every new Paint element's bar said "Paint · colour (moving)":** it says "Paint · empty" until something's on it.
- **In Mask mode, while you paint,** the preview showed the strokes alone, so the red of Hide shapes and of "Hidden
  everywhere" went for a moment (and on a hidden mask a stroke looked as if it hid something; strokes only hide on a
  mask that starts shown). The preview has the shapes and the start too now.
- **A Colour element had two rows called "Colour"** (its undo step read "colour Colour → Straight gradient"): the first
  is "Fill" now, as on an older Paint element's Background.
- **After undoing a drawn mask shape,** the Editor still thought it was selected; after a redo, its handles showed with
  nothing selected in the details. Nothing stays selected now.
- **The note about going back to the version before M14** also says it doesn't know mask shapes or Start with.

**A second look** (two more reviewers: item 11, the clean-up, and the fixes above), each fix with a test:
- **Sending a video input to the Overlay again and again** (to the beat, say) opened a new video reader each time,
  inside the engine's tick: tapping twice a second for ten seconds reached 20 readers, 940 threads and nearly 5 GB of
  memory, and the lights hitched at each send; clearing the copies later stalled a tick too. Now:
  - a new copy takes over a reader another copy of that video has finished with (the same tapping peaks at 3 readers,
    33 threads and about 100 MB more);
  - videos are decoded in software with two threads: for these small pictures it's as quick as the graphics card,
    and a reader costs about 15 MB instead of 170, and opens in a few ms instead of about 90;
  - a reader closes on a thread of its own, and a new one shows its first picture straight away (no blank moment
    when a video input is sent again).
- **A waiting "longer schedule's show comes back"** (Wait 10 min pressed on it) still loaded after that schedule had
  ended too, over the default show. Now when a schedule ends, whatever was waiting from before goes first.
- **A PC restart with the default show** ran every schedule that had been under way again, even one that had ended
  meanwhile (so its "When it stops matching" was lost), and even after someone had loaded a show by hand. Now only the
  automation that had loaded the last show runs again, and only if it's still under way.
- **An automation that loads a show and is about the loaded show or the lights being black** (not a schedule, for
  the default show) was overridden by the default show when a schedule ended at the same moment. Its load wins now,
  like a person's choice.
- **With two longer schedules** (November, and Sundays), the one higher in the list came back after an inner one ended,
  rather than the one that had been on. The one that was on comes back now (CentraLight keeps track, over restarts
  too).
- **Split's line** missed random() in an element's move / size, opacity or mask formulas (the Colour element gets those
  too). It says so now.
- **An input written with "shapes" that isn't a list** made the Editor raise an error at every change. It copes now.
- **The AI check refused a Paint element written with "fill": null,** which is fine (see-through).
- **Cancel on a colour with keyframes left a keyframe behind** (with an undo step), so the input played a little
  differently. A pick with Preview now works like dragging a slider: each change shows at once, on the lights too, but
  isn't a step yet. Cancel puts the input back exactly as it was (nothing left behind, and Redo still works); OK makes
  the colour one ordinary step, keyframe and all.
- **Two separate picks of an input's own colour variable became one undo step** after its row had been redrawn. Each
  pick is its own step now.
- **A colour typed into the box without pressing Enter, then the swatch clicked:** the picker started from the old
  colour, so Cancel threw the typed one away. The typed colour counts first now.
- **Preview's "until the PC restarts"** didn't reset after a shut down and power on with Windows' Fast Startup (this PC
  has it on), as Windows' running time carries on. It's ticked again whenever CentraLight counts the PC as just started
  (which includes Windows starting it at sign-in).
- **An old "slot" Overlay action or POST** from before M14 wasn't refused: it was read as Send to Overlay (even an old
  "out of Overlay 1"). They're refused now, with a note (none of your files has one).
- **Not fixed (drawing only):** the week schedule doesn't always draw the default show the way it would load around a
  "When it stops matching" load (one whose show isn't there, or that waits a few minutes). It's noted as a follow-up.

## The overnight soaks (4 Nov, early morning)

Two long soaks ran overnight, each with the whole app (Simulated output, its own temporary settings and shows, and
a 960×540, 24 fps test NDI feed):
- **10 hours of the version before M13** (51bc909, finished 03:10): a show load every 2 minutes (300, every one
  answered), no errors in the session log, CPU 10.6 % of one core. Memory rose about 0.8 MB an hour and didn't level
  off. That's NDI's own: a small script that only receives the same feed rose at the same rate with no CentraLight
  running (details under M1's measurements), and starting a fresh NDI receiver every 10 minutes didn't give any
  back. A week of it is about 130 MB, which this PC can spare.
- **3 hours of M13** (a6171e1, finished 04:16): a show load every 2 minutes; the first show put "Linked video" on
  Base each time it loaded, and every 30 minutes the video's original was replaced by a different video while it
  played; every 10 minutes a show variable set off an automation whose Fade to black waited for its 15-second
  countdown. All 90 show loads were answered, 32 runs waited for their countdown, the original was replaced 5 times
  while the video played (12 copies made, 12 swapped in), and the session log has no errors. Memory rose from
  156 MB to 191 MB (peak 201 MB), about 11 MB an hour, which led to the fix below.
- **What made M13's memory rise:** each new version of a linked video becomes a new file in the show ("Linked clip
  2.mp4", "Linked clip 3.mp4", …), and the newest picture of every video file was kept in two places:
  - for the lights, in a cache that stops at 48 MB (by design; measured: it filled up at 68 files and stayed there);
  - for the Dashboard's live picture and the Editor's, with no limit: about 0.4 MB for every video file ever shown.

  **Fixed** (06b593d): a drawing now keeps the newest picture of only the last 6 videos it drew (a new test checks
  it, and fails without the fix). With the fix, a script drawing 100 different video files one after another with
  CentraLight's own drawing code stopped growing once the lights' cache was full, at the 70th (without it, it was
  still growing at 150). And an hour's soak of the fixed app with a new version of the video every minute (57 in
  all, 180 show loads, 96 countdowns, no errors): memory rose while the caches filled, for about 25 minutes, then
  only 4 MB in the last half hour, 30 versions later. How it was found: 30-minute soaks with one extra each (show
  loads alone and countdowns alone stayed flat; linked-video updates alone rose at each update), then Python's
  memory tracer during a fast soak (all the growth was in these two caches), then a count of the caches with 150
  files.
- **The soak's own variable kept fading the lights:** the soak set its "mood" variable high in one show, and low
  again in whichever show was loaded 40 s later, so the first show's stayed high, and each time that show loaded,
  its automation counted down and faded to black again (47 such runs in one fast soak). That's as designed (a
  show's variables come back as they were when it was last loaded), but it means the lights were black for much of
  that soak.
- **CPU depends on how NDI hands the pictures over** on this PC: the same app read 10–13 % of one core in some soaks
  and under 1 % in others, and a bare NDI receiver anything from 10 % to 0.1 %, with 24 pictures a second arriving
  either way. So the soaks' CPU figures say more about NDI than about CentraLight (step 15b of the checklist notes the
  real figure).
- **The soak tool** can run several soaks at once now: each one's test feed has its own name ("CentraLight Soak 1234
  Source") and Windows picks each one a free remote port. (Two soaks started in the same second once shared a port,
  so one of the 30-minute soaks above never switched shows: its linked-video updates alone made its memory rise.)
- **Old copies of a linked video stay** while the undo history could bring them back, so almost none were tidied
  during the soaks; they go at the first show load after CentraLight starts again.

## A look at M13 (3 Nov, 23:00)

Three reviewers checked M13 before the soak (the countdown and schedule; linked media, importing and notes; .clshow
files). They found about 45 things; everything that mattered is fixed, each with a test that fails without the fix
(all 38 such checks were tried, breaking each rule in a copy of the code: each time a test noticed):
- **Text from files never reaches the screen as HTML.** Qt shows text that looks like HTML as HTML, and reads any
  picture a tag names from the disk: a show, input or automation called `<img src="//./COM3">` (in an imported .clshow
  file, or written by an AI tool) would have opened the Eurolite's port just by being shown. Now "<" and ">" in names
  and settings read from files become "‹" and "›" (formulas and comparisons keep theirs), the import dialogs and
  messages are plain text, and tooltips that show formulas or notes are escaped. NDI feed names from the network too.
- **The countdown is part of the automation's run now**, waiting in the same queue as its "after N s" steps. So
  switching the automation off, deleting it, the loop guard, pausing, Run now, or the automation stopping (when that
  cancels what it was waiting for, as for "feed lost for 10 s") all stop its countdown too; before, a fade could still
  happen at zero after you'd switched its automation off. Skip, Wait 10 min and Now act on the whole run from its
  show load or fade on, its later steps included. A "when it stops matching" fade at the end of a time range starts
  its banner early too, so it lands on time. Skip on an early banner is kept even if the banner flickers. Run now ends
  the automation's countdown. A countdown from before the PC slept is dropped when it wakes. Making the countdown
  shorter in Settings hurries one under way. The phone page's buttons name the countdown they showed (a second tap
  can't skip another), and its banner is in the page's top bar. Pressing Now counts as someone taking charge after a
  "Turn lights off" quit.
- **Linked media:** tidying at a show load can no longer delete a copy a check had just made, or one an update was
  about to use (and an update never points at a file that isn't there). Undoing an automatic update sticks: it's then
  offered with Update now rather than made again, and the Dashboard's Ctrl+Z never picks CentraLight's own update
  step. The AI paste dialog allows only the links it listed. After three stuck checks, Choose… and Copy into the show
  say so instead of "Copying…" for ever. An original that isn't a picture or video isn't copied again until it changes.
  A moment's lock on `media\links.json` can't wipe it. Names are compared as the engine reads them
  ("media\a.png" is "media/a.png"). A Choose… that finishes during a drag waits for the drag's end. Room photos copied
  to a new show or layout keep their record, and big room photos are read at the size shown.
- **Import from another show:** imported automations start from how things are now (one whose conditions are true
  already doesn't run until they next come true: "nothing is sent to the lights" holds). Keep both never gives an
  import an id that another one coming has, and automations that switch a renamed automation follow it. A hotkey
  another input here has is left off (the summary says so). Lights the imported automations use are listed. Files are
  copied on a thread with a progress window.
- **.clshow files:** Replace really replaces (it always kept both). Show loads wait while an import or export runs,
  and one that's waited 30 s stops it (it leaves nothing half done) rather than loading mid-way. One failure in an
  Export everything import no longer drops everything after it. The "Imported…" message holds nothing up. Quitting
  during an import stops it cleanly. The replaced show goes to the Recycle Bin from the window (Windows' questions
  belong to it). Pictures and videos must be stored as they are inside the file, and JSON is limited harder (no zip
  bombs). Names in a file's media\links.json that Windows would read as another file's are refused. Importing settings
  applies them to the running CentraLight properly, and never changes this PC's output, port, phone page or start with
  Windows. A double-clicked file is checked as text first (a full path, no device names), only one import runs at a
  time, and the running CentraLight may come to the front.
- **Notes:** the Dashboard's panel is below Layers, scrolls when long, and remembers being folded. The Show menu shows
  "&" in notes, and their full text in tooltips.
- Asked of you: nothing new. The countdown also applies after the PC wakes from sleep (only start-up is exempt, as
  the plan says).

## M13 Show extras — done (3 Nov, night)

Your six extras of 3 Nov (evening), in the order the plan suggested: linked pictures and videos, importing from
another show, .clshow files, show notes, the week schedule, and the countdown. All with Simulated output.
- **Linked pictures and videos, with CentraLight's own copy in each show.** An Image or Video element (and a
  layout's room photo) remembers the **original** file, wherever it is (Google Drive, Pictures, Videos), and plays
  CentraLight's own copy in the show's `media` folder, never the original, so a slow or offline Google Drive can't
  touch playback.
  - **Choose…** (the Editor's File row, which now shows the original's path) links the element and copies the file
    in. The room photo's **Room photo…** does the same. Each is one undo step, made when the copy is ready.
  - **Changes:** each original's size and modified time are checked when the show loads, when you select the
    element in the Editor, and every 5 minutes. A changed original is copied to a temporary file, checked to open as
    a picture or video, and kept as a new copy ("candles 2.mp4"); the copy in use is never overwritten. It's used
    once its input isn't on the lights (not on Base, an overlay, a transition or solo); until then the File row
    says **Update ready: used when it's not playing · Update now**. The room photo is updated at once.
  - **An original that's gone** (or can't be reached) shows a warning by the File row: "Original not found: G:\…\
    candles.mp4. CentraLight is using its own copy." The copy keeps playing.
  - **Only on its own thread:** checking and copying run on a thread of their own; the engine, the lights, the
    window and playback never touch an original. If a drive stops answering, that check is given up after 45 s and
    the others carry on (after three stuck ones, checking stops until CentraLight restarts).
  - **Safety:** a link is followed only once someone at this PC has chosen it (Choose…, **Allow** on the File row,
    an import from another show, or the AI paste dialog's **Copy into the show**). Links an AI tool writes into the
    show folder, or inside an imported .clshow file, show **Not checked yet · Allow** until then; network paths
    (\\server\…) too. Names Windows keeps for devices (COM3 is the Eurolite) are refused as plain text before
    anything looks at the disk. The list of allowed originals is kept in CentraLight's app data, never in a show.
  - **Tidying:** copies CentraLight made that nothing uses any more (not the show, nor its undo history) are removed
    when a show loads, never while you edit. Files you put in `media` yourself, and copies from before M13, are
    never removed.
  - Pictures from before M13 have no known original: their File row shows the show's own file, with no warning,
    and Choose… links them.
- **Import from another show…** for inputs (the **+ New input** tile → **From another show…**) and show automations
  (the Automation tab → **From another show…**), like the Layout tab's. Pick a show and tick what to bring; a summary comes
  first: ids are kept (so a Stream Deck key fires it in whichever show is loaded), and if this show already has that
  id you choose **Replace** or **Keep both** (the import gets a new id, and automations brought with it point at it).
  Lights the inputs use that this show hasn't got are listed ("SlimPAR 108 isn't in this show, so it's ignored
  here"); show variables they use are added with their defaults; an automation that sends inputs this show lacks
  brings them too (unless you untick it). Their pictures, videos and paintings are copied in, links included.
  Nothing is loaded or sent to the lights, and one Ctrl+Z undoes it.
- **.clshow files.** **Show ▾ → Export “…”…** makes one file (a zip inside) with the show's inputs, automations,
  layouts, room photo, notes and its copies of pictures and videos; **Export everything…** has every show, the global
  automations and the settings, for moving to another PC. **Leave out pictures and videos** makes a small file.
  **Show ▾ → Import…** (or double-clicking a .clshow file: Windows shows them with a CentraLight document icon)
  brings CentraLight forward and asks **Import “Morning” as a new show?**; if the name is taken, Replace (the old one
  goes to the Recycle Bin; its id stays) or Keep both ("Morning (2)"). An imported show isn't loaded. Export
  everything files list what's inside with ticks; the settings start unticked, and this PC always keeps its own
  output (Simulated / USB), USB port, start with Windows and window. Every file in an imported .clshow is checked
  like an AI-made input (data only, formulas checked, every name inside the show folder, device names refused,
  sizes limited) before anything is written. Settings → **Open .clshow files with CentraLight** (on) registers the
  file type for this Windows user only, when CentraLight itself runs (never the tests or the soak tool).
- **Show notes:** **Show ▾ → Notes about “…”…**: a few lines of plain text per show. They show under each show's
  name in the Show menu, in a small **Show notes** panel at the top of the Dashboard's right column (click its title
  to fold it), on the phone page under the show switcher, and in the AI guide ("About this show"). They're in
  show.json, so Duplicate, export and backups carry them. One undo step.
- **Week schedule** (the Automation tab → **Week schedule**, beside **Automation**): a read-only week, Monday to
  Sunday across and hours down, with a coloured block for each show, worked out from the global automations' **Load
  a show** and their time, day, date and sunrise / sunset conditions (with the runner's own rules, clock changes
  included). Two automations loading different shows at the same minute are marked in red; ‹ › go to other weeks
  (Christmas shows in its week); a line marks now; clicking a block opens its automation. Triggers that can't go on
  a calendar (CentraLight starts, a feed lost...) are listed underneath. No inputs in the blocks, as you asked.
- **A countdown before automatic changes:** before a global automation loads a show, and before any automation's
  Cut or Fade to black, a banner under the header (and on the phone page) says "Loading Evening in 0:15 · Load now ·
  Wait 10 min · Skip" (or "Fading to black in 0:15 · Do it now · …"). If nobody touches it, it happens at zero.
  **Skip** skips this run only; **Wait 10 min** moves it 10 minutes later, with the countdown again before it. 15 s
  unless Settings → **Countdown first** says otherwise (0 = none). For a change that comes from the clock (a time,
  day, date or sunrise / sunset, or an "after N s" step) the banner starts early, so it still lands on time; other
  triggers (a feed lost, a sound level) wait for the countdown. No countdown for Run now, in the first minute after
  CentraLight starts, or while automations are paused; the rest of that automation's run (the Key lights after a
  load, say) waits with it, in the same queue as its "after N s" steps (see the look at M13). Stream Deck:
  `/api/countdown/now`, `/api/countdown/wait`, `/api/countdown/skip`. Under the Lock, only Wait and Skip work.

## A second look at M12 (3 Nov, 16:30)

A second reviewer checked the fixes above and found ten more things. Each is fixed, with a test that fails without
the fix (all 21 such checks were tried: each test noticed):
- **A show held for a dialog** (an automation's "Load a show" while a question or Settings is open) ran out the 5 s
  the old show's automations waited for it: they then ran in the old show, and so did the global automation's other
  actions. A dialog left open held the switch for ever. Now the engine is told the show is coming, so everything
  waits for it; only the newest request is kept; and after 30 s CentraLight closes the dialog itself (as if Cancel was
  pressed) and loads the show: automations win. The engine also waits up to 20 s rather than 5 s for a show whose
  window is busy (a slow sign-in, say).
- **Device names in media files.** A picture or video named in a show's files ("media/COM3.jpg", "aux.png", a room
  photo, a painted layer) made CentraLight ask Windows about that name, which opens the device: COM3 is the
  Eurolite's port. Such names are now refused before anything looks at the disk, a copied picture called
  "Aux.png" becomes "Aux media.png", and "COM3:" (with a colon) counts as a device name too. The tests check this with
  plain text only: nothing reached the disk with a device's name.
- **Going back to a show** carrying on where its automations were (from the first look) is undone, as you decided:
  every load catches up, because automations override the freeze. That also removed a problem the reviewer found
  with it (a waiting "after 60 s" action could run when the show was loaded again, even while paused).
- **Notes about lights:** a new show or a copy keeps the light ids, so the same id can be "SlimPAR 101" in one show
  and "Pulpit" in another. Loading the other show wrote one light's note under the other's name. Now the notes are
  matched to the lights by their names each time a show loads.
- **"Load a show" and "This show is loaded"** matched a show's name against another show's id (a show renamed
  "Christmas 2025" and a new "Christmas"). Now a value like `christmas` (lower case, no spaces: what CentraLight
  stores) is only ever a show's id; a name ("Christmas", "Sunday Morning") is matched against names.
- **Saving around a load** could freeze the window for up to 10 s with a locked file, and a save that finished late
  was blamed on the new show. Now it waits 3 s, then 1 s more at most; each save knows its show, and a late one goes
  to the log as the old show's.
- **Stop (Base fading to black), then a load:** the old show's input kept playing as it faded, looking for its files
  in the new show's folder. It's held still as it fades now.
- **A deleted show's variables** came back for a new show made in the same folder. The engine forgets them now.
- **"Couldn't load"** from a person's or the Stream Deck's request could end a global automation's wait for another
  show; and Pause didn't drop the actions waiting for a show to load. Each answer now names its request, and Pause
  drops them.
- **Windows' own question when deleting** (a folder too big for the Recycle Bin) had no owner window, and a show
  could load while it was up. It belongs to CentraLight's window now, and loads wait for it.

## A look at M12 (3 Nov, 13:00)

A reviewer checked M12 and found fourteen things. Each one that could be fixed is fixed, with a test that fails
without the fix:
- **Names Windows keeps for devices.** On Windows 10 a file or folder called "COM3" (or "COM3.anything", "AUX",
  "CON"...) is a device: COM3 is the Eurolite's port. A show called "Aux.Service" made CentraLight look for a folder
  that Windows took to be the port COM1 (the PC's own serial port), and it hung. That was the reviewer's test, in a
  scratch folder, not COM3. Now a show's folder never gets such a name ("Show Aux.Service"), and no input, layout or
  automation id can be one either (an input called "COM3" gets the id `com3-input`; a file or an AI reply with such
  an id is refused). That last part was there before M12 too.
- **Fade to black, then a show loading** (a global automation doing both, say) froze the lights a moment into the
  fade, so they stayed lit. Now a fade to black carries on to black when a show loads.
- **A global automation's actions after "Load a show"** (the Key lights, Master...) now wait until that show has
  loaded, so they happen in it.
- **A question on screen while a show loads** (Delete input?, a layout's new name, "Load the changed file?", a file
  to pick...): what you answered could land in the newly loaded show. Now a show asked for by an automation or the
  Stream Deck while a dialog is open loads when the dialog closes (after the second look: or after 30 s, when
  CentraLight closes it), and the file pickers check the show is still the same one.
- **Saving around a load:** a file that couldn't be saved in the old show could be blamed on (or retried in) the
  new one. Now everything is saved, and waited for (a few seconds at most), before the switch; anything that still
  couldn't be saved is reported in the log as the old show's.
- **A deleted show's undo history** came back if a new show was made in the same folder. It goes with the show now.
- **Starting within 10 minutes of the PC starting** after a crash counted as the PC starting (everything black). Now
  it's only a PC start if CentraLight hasn't run since the PC started; a crash and reopen carries on as it was.
- **Duplicate** opened the copy straight away, so its automations ran (a time range under way, "When this show
  loads") and could change the lights mid-service. Now the copy doesn't open by itself.
- **Going back to a show** ran its automations' catch-up again (e.g. "Sunday 09:00–12:30" a second time over what
  someone had chosen). I made each show's automations carry on where they were; that's undone since, as you decided:
  every load catches up, because automations override the freeze (see the second look).
- **The global automations' save problems** weren't shown in the header. They are now, and so are problems reading
  them when CentraLight starts.
- **A show called "_Rehearsal"** got a folder the list of shows leaves out (the global automations' folder starts
  with "_"). Show folders don't start with "_" now.
- **Delete** now asks Windows to warn before anything would be deleted for good instead of going to the Recycle
  Bin (a folder too big for it, say).
- **Notes about lights** that aren't in the loaded show were dropped from `fixture_map.csv` at the next note change.
  They're kept now.
- **"This show is loaded" and "Load a show"** accept a show's name as well as its id.
- **If the last show's folder has gone,** CentraLight opens one of the other shows instead of making a new one
  there.
- **While a show was loading,** the global automations were checked 40 times a second (for up to 5 s). Once a
  second now.
- Not changed: two show folders whose names differ only in punctuation ("Sunday Morning", "Sunday_Morning") share
  an id; CentraLight never makes such a pair itself.

## M12 Shows and global automations — done (3 Nov)

Your change of 3 Nov: shows you can make, copy and load (a morning and an evening one, say), and automations that
belong to the app rather than a show.
- **A show is everything in the app today:** its inputs, show variables, show automations, lights (the patch),
  layouts (with their room photos), media, the Dashboard's order and its AI guide, in its own folder in
  `Documents\CentraLight Shows`. Changes still save by themselves: there's no Save.
- **The show picker**, after the tabs: **Show: Central Church ▾**. It lists the shows (the loaded one ticked) and has
  **New show…** (this show's lights and layouts, "Follow screen", nothing else), **Duplicate…** (the whole folder,
  media included; every id stays, so Stream Deck keys work in both), **Rename…** and **Delete a show** (asks, then
  the folder goes to the Recycle Bin; the loaded show can't be deleted). A new show opens straight away; a copy
  doesn't (load it from the picker). Lock stops the picker too.
- **Loading a show never sends an input**, whoever loads it. The lights freeze on what they show: Base and the
  overlays become one still look on Base (the overlays are cleared), held until one of the new show's inputs is sent,
  which comes in from it with its own transition. The Dashboard says **Base: frozen (from Morning)**. The old show
  stops: its videos and feeds are let go (a feed the new show uses too stays connected). The Festoon, Key lights
  and Master keep their levels. A light in both shows keeps its colour; one that's only in the new show is unlit
  until something's sent. The new show's variables come back as they were when it was last loaded.
- **Starting up:** when the PC has just started (Windows started CentraLight at sign-in, or within 10 minutes of the
  PC starting), the last loaded show opens with every light black, house lights too (their levels kept), and
  nothing playing. When CentraLight is reopened without a restart (after Quit, or a crash), it carries on exactly
  as it was, a held look included. Then the automations run: the global ones first (one can load another show),
  then the show's. Automations win over both rules.
- **Global automations** (Automation tab, the top list; files in `CentraLight Shows\_Global automations`): app-wide,
  for what isn't about one show. Conditions: The PC starts, CentraLight starts, The PC wakes, time, days, dates,
  sunrise / sunset, an NDI feed, sound, The colour lights are black, and **This show is loaded**. Actions: **Load a
  show**, Cut to black, Fade to black, Master, the Festoon and Key lights (found by name in whichever show is
  loaded; skipped with a note in the log if it has none), and turning another global automation on or off. They
  can't touch a show's inputs, overlays, variables or layouts. The same rules as a show's automations (All / Any,
  Not, "when it stops matching", catch-up, the loop guard, Run now); each second they're checked first. When one
  loads a show, the old show's automations wait for it (at most 20 s; a dialog that's open holds it up to 30 s, then
  CentraLight closes the dialog) and the new show's run as soon as it's loaded.
- **A show's automations** lose The PC starts, CentraLight starts and The PC wakes, and gain **When this show
  loads**: when you, the Stream Deck or a global automation load it, and when the PC starts with it, but not when
  CentraLight reopens (so a crash restart doesn't change the lights). Old automations that used the moved
  conditions are changed to "When this show loads", with a note in the log; they stay switched on.
- **The Automation tab** has the two lists, **Global automations** and **This show (Morning)**. Coming up and
  History show both (the global ones marked "Global:"). Pause pauses both. Undo works on the list you're in: each
  show keeps its own undo histories while CentraLight runs, and the global automations have their own.
- **Layout tab → Layout ▾ → Import from another show…:** pick a show and one of its saved layouts. It brings that
  show's lights (names, types, addresses and groups, and the house lights), their positions in that layout and its
  room photo here, as a new saved layout in use. A summary of what changes (lights added or removed, addresses
  changed) comes first, with Import and Cancel. One Ctrl+Z on the Layout tab undoes it.
- **"Global variables" are "show variables"** everywhere you see them (the Editor's tab is **Show variables**), in
  the docs and the AI guide. The files keep their old field names.
- **Stream Deck and the phone page:** `/api/show/<id or name>` loads a show (its id is its folder's name, e.g.
  `central-church`), `/api/shows` lists them, `/api/global-automation/<id>/run` (`/enable`, `/disable`) works a global
  automation. The input, variable, house-light, automation and layout URLs act on the loaded show; anything that
  isn't in it gets 404 "not in this show (Morning)". The phone page has a list of shows next to the show's name, and
  reloads itself with the new show's inputs when another show loads.
- **live.json is app-wide:** it says which show was loaded last (that one opens next time), what was playing in it
  or the look it held, Master and the house lights, the other shows' variables, and both lists' automation states.
- The global automations' folder gets its own AI_GUIDE.md (the conditions, actions and every show's id), so an AI
  can write them too. Files changed there outside CentraLight are picked up straight away, as a show's are.
- Not built, as agreed: a countdown before an automatic show switch, a schedule view, Copy to show…, export /
  import of shows, show notes, and a shared media folder.
- One empty test folder, "CentraLight Recycle Bin check (safe to delete)", went to the Recycle Bin when I checked
  that Delete really uses it: it's safe to empty.

## M11 Cut to black and Fade to black — done (3 Nov)

Your change of 3 Nov: two one-off buttons instead of the Blackout toggle.
- **Cut to black** and **Fade to black** (red, in the header). They're actions, not a latch: nothing stays "on", and
  the next input sent (by you, the Stream Deck or an automation) lights things again. Master isn't touched.
  - **Click:** the colour lights go to black: the mix as it is (Base and every overlay, as one still look) cuts or
    fades to nothing, and the overlays are cleared. The Festoon and Key lights stay as they are.
  - **Hold** (0.6 s; the button fills up and says "Cut everything" / "Fade everything"), or **Shift-click**:
    everything, the Festoon and Key lights too. Their switches go off and their faders keep their levels.
  - **Switching a house light back on** (its header switch, or a Stream Deck toggle without its own fade) brings it
    back the way it went out: at once after a cut, or over the same time after a fade. Then its switch works as
    normal again. Inputs and automations that set house lights do it their own way, whatever the blackout was.
  - **Fade to black takes 2 s** to start with: Settings → Cut / Fade to black.
  - **Lock** stops both buttons, as it stopped Blackout.
- **Automations:** "Cut to black" and "Fade to black" actions (with a "Festoon and Key lights too" box, and the fade's
  time) replace Blackout on / off, and the condition "Blackout is on" is now "The colour lights are black" (nothing
  on Base or in the overlays). Old automation files are brought up to date with a note in the log: "Blackout on"
  becomes "Fade to black", everything, over half a second (what Blackout did); "Blackout off" is left out, since
  there's nothing to turn off now. *(M14's clean-up removed this: no file of yours needed it.)*
- **Stream Deck and the phone page:** `/api/black/cut` and `/api/black/fade` (`?all=1` for everything; `?seconds=`, or
  else the time set in Settings) replace `/api/blackout/…`. On the phone page a tap is the colour lights and holding
  the button is everything.
- **Quitting with "Turn lights off"** fades everything to black (half a second), then quits. The next start comes up
  black with nothing playing. The fourth and fifth reviews' rule keeps its intent without a latch: until one of an
  automation's time ranges begins after the quit (e.g. Sunday 09:00 after a quit on Saturday), automations don't
  light anything ("CentraLight starts", a feed coming back at 03:00, a whole day such as "Sundays"); anything a
  person sends lights things at once. The quit's own time is still kept through restarts.
- An older live.json that was blacked out starts black (nothing playing, house lights off with their levels kept).
  *(M14's clean-up removed this too.)*

## A look at M10 (3 Nov, 11:00)

A reviewer checked M10 and found five things. Each is fixed, with a test that fails without the fix:
- **A Fill picture moved, shrunk or turned:** lights near its edge, or just outside it, could pick up the strips
  that Fill crops off (such as the top and bottom of a 4:3 picture). Each light's patch now covers only the part of
  the picture that shows, wherever the element is.
- **Smoothing while the Editor is paused:** with Smoothing on, a change made while paused (a new slide, a changed
  setting) didn't reach the Editor's dots until you pressed play. When no time passes, a change now shows at once.
- **A light added or taken away during a transition** (or a fade to black): the old look, frozen for the old set of
  lights, no longer fitted, and every light went black until the transition ended. Now the old look counts as
  nothing for the rest of that transition, and the new look comes in as normal.
- **Memory:** each picture's table for Area was kept for as long as the app ran, and every move of a light left an
  old "nearest light" map behind. The tables now share a store of at most 48 MB (the most recently used are kept; a
  picture's is remade if it's needed again), and only the newest map is kept.
- **The lights come first, while playing too:** the Editor drew less while the engine was late only when it was
  redrawing a moving input by itself, not while you played one; now it draws at most twice a second either way.
  The Dashboard's picture stopped completely while ticks ran late; now it slows to once a second, so it never
  looks stuck.

## M10 Per-light colours — done (3 Nov)

Your change of 3 Nov: work out each light's colour directly, instead of drawing a picture and sampling it.
- **Lights are points.** Each light's colour is worked out at its dot, element by element
  (`centralight\light_elements.py`). **Paint** is read exactly at the dot: its fill, gradients, brush strokes and
  shapes. A shape's **Softness** decides whether a light fades or switches as an edge passes it, and new shapes get a
  little Softness (20 %), so a moving bar fades through a light.
- **Area** replaces Blur on Image, Video and NDI: how much of that element's own picture each light averages, as a
  square patch round its dot. 0 % is the colour under the dot. The default, **25 %**, matches the patch every light
  followed before: on test pictures the lights came out within 1–3 of 255 steps of before (1.2 on average), so
  Follow screen looks the same. 100 % is exactly the whole picture's average for every light (what
  `ndi_pixel_mapper.py` did on Sunday). It's a few lookups in a summed-area table of the picture, so it costs the
  same at any Area. Each element has its own, and like any setting it can be keyed or given an expression.
- **Colour boost** (shown as **Boost**; Image, Video and NDI; 0 % to start with): more saturation for each light
  after averaging, with the same hue and brightness.
- **Smoothing** (Video and NDI, off to start with): each light glides to its new colour, with separate times for
  getting **Brighter** and **Darker**. **On cuts: Snap** (the default) lets a slide change or a video cut land at
  once; **Glide** glides across it too. It's the same at any tick rate, and it's worked out on the element's own
  colours, before opacity, masks, blending and transitions.
- Layers, opacity, blend modes, the painted mask (read at each dot), lights left alone and every transition (wipe
  and iris reach each light as their edge passes it; Random goes light by light) work per light. The sampler, its
  owner map, weights and fixed patch are gone, and so is Blur: an old file's `blur` is ignored with a warning.
  *(Since M14's clean-up, without the warning: no file of yours had one.)*
- **Pictures are only for people now.** The Dashboard's live picture is drawn 5 times a second, only while the
  Dashboard is on screen, and only after the lights' frame has gone out. The Editor's viewports are drawn as before
  (without Blur); their light strip and dots come from the lights' own maths. Patches are drawn only in the Editor's
  Selected view, for a selected Image, Video or NDI element, at its Area at the playhead. The Result view, the
  Dashboard and the Layout tab show dots only (the Layout tab's rings, tinted patches and "use Blur" hint are gone).
- The Light colour tool paints a dab round the dot of the light nearest the pointer (Paint is read at the dot).
- **The lights come first.** The engine records how late each tick starts. While ticks are late, the Dashboard's
  picture slows to once a second and the Editor redraws twice a second instead of eight times. Diagnostics (Ctrl+Shift+D) has a new
  "On time" line with the last minute's figures.
- A small fix found on the way: the AI window's Copy buttons could touch a closed window 2.5 s after a click (it
  was harmless, but logged an error).

**Engine time per tick** (`tests\test_m2_core.py`, one NDI element with a new picture every tick): typical
**0.16 ms** (was 0.26 ms), worst case (a transition and four busy overlays) 2.2 ms (was about 4.7 ms).

**The lights come first: measured** with `tools\tick_timing.py` (the real app, no visible window, Simulated output,
a 960×540, 24 fps test feed on Base; 30 s each, 1,200 ticks). Lateness is how long after its time each tick started;
a tick is the engine's own work. None of the ticks started more than 5 ms late, before M10 or after:

| Situation | Late: median / 99th / worst | Ticks over 5 ms late | Tick: median / 99th / worst | Before M10: tick median |
|---|---|---|---|---|
| No window open | 0.49 / 1.00 / 1.1 ms | 0 | 0.25 / 0.45 / 0.5 ms | 0.38 ms |
| The Dashboard showing | 0.50 / 1.06 / 2.6 ms | 0 | 0.26 / 0.80 / 2.0 ms | 0.41 ms |
| The Editor playing a video (640×360) | 0.52 / 1.24 / 1.6 ms | 0 | 0.29 / 1.21 / 2.3 ms | 0.43 ms |

The Editor's viewports kept up at full size (12.8 pictures a second while the video played; they aim for 8), so
there's no need for a lower preview resolution. The engine never waited noticeably for Python's lock, so Python's
thread switch interval is left as it is (5 ms). How late ticks start varies a little from run to run (medians of
0.3–0.9 ms in other runs): that's Windows' timer, the same before and after.

## A fifth look (3 Nov, morning)

A fifth reviewer checked the fourth round and found one gap, fixed with two tests that fail without it:
- **The quit's own time is kept in live.json.** The Blackout rule compares a time range with the moment of the
  "Turn lights off" quit, but CentraLight took that moment from live.json's last save, which the minute save keeps
  moving. So if CentraLight restarted (a crash, Windows updates, someone restarting it) after Sunday's service range
  had begun but before its feed came, the range counted as "already on at the quit" and the service stayed dark.
  Now the quit's time is saved, and carried through later saves, restarts and quits until the Blackout is lifted.
- It also spotted that the new optional Wednesday check (step 18) didn't say to set the day: a new automation
  starts as "Sun, 09:00–12:00". Fixed (found here too).
- **One full test run crashed** (Windows stopped Python, in a window test that passed 25 times on its own and in every
  other full run). The likeliest cause was in the tests, not the app: their helper for letting Qt catch up never
  freed the widgets Qt had been asked to delete, so old windows from earlier tests could linger, hidden, with their
  timers still running. It now frees them as the app's own event loop does. A suspected app-side cause (Python
  freeing a widget Qt was still using) turned out not to happen with this Qt version; a test now checks that.

## A fourth look (3 Nov, morning)

A fourth reviewer checked the third round. This time the rules were made simpler to explain, so they stop producing
edge cases. Fixed, each with a test that fails without it:
- **A "Turn lights off" quit keeps the lights off until a scheduled time range begins after the quit**: e.g. Sunday
  09:00–12:30 (or "after sunset") after a quit on Saturday. Then the automation that brings the lights up lifts the
  Blackout, even if its feed comes late. Nothing else lifts it: not "CentraLight starts", not a feed coming back at
  03:00, and not a whole day ("Sundays", "1–30 Nov") or a range that was already on at the quit. Before, "Sundays
  and the feed is there" lit the church at 03:05 after a Windows Update restart.
- **A stop cancels the waiting "after N s" steps only when a live condition is what went false** (a feed, sound, a
  variable, an input, Blackout), or when the automation has a "when it stops". So "10:30–10:31 and the feed is
  there → Countdown, then after 120 s Worship blue" carries on when the range ends, and "feed lost → after 10 s
  the fallback" is cancelled when the feed comes back, also in an "Any" automation with "CentraLight starts".
- **At startup, an automation about a feed waits up to 10 s for it** (it was 3 s), and only for its own feeds: one
  that's already sending counts straight away. A restart mid-service with a slow feed no longer runs "when it stops"
  and then the automation again over what someone chose. The lights themselves still wait at most 3 s for the
  feed's first picture.
- **live.json's once-a-minute save stops at shutdown**, so it can't save "lights on" just after a "Turn lights off"
  quit.
- **If Windows closes CentraLight while "Load the changed file?" is on screen**, everything else is saved, and that
  input's file is left as it is on disk (nobody said to write over it).

## A third look (3 Nov, morning)

A third reviewer checked that second round, and found its rules for automations around restarts too blunt in places.
Fixed, each with a test:
- A waiting "after N s" step is cancelled when the automation stops if the automation depends on how things are (a
  feed, sound, a variable, an input on Base, Blackout), so "feed lost for 10 s → …" is a real grace period, or if
  it has a "when it stops" step. Clock-only ones without one ("at 10:30 do this, then 2 minutes later that") carry on.
- A Blackout from a "Turn lights off" quit gives way only to a run made by a clock condition that's true then (e.g.
  Sunday 09:00), not to a feed coming back at 03:00 after a Windows restart. And a Blackout someone clicked stays
  theirs even if CentraLight then quits with "Turn the lights off".
- "CentraLight starts" combined with a feed condition works again (such automations wait for the feeds at startup,
  and now keep the start event), and an "Any" automation with "CentraLight starts" in it catches up at its next
  occurrence again.
- A failed save counts as unsaved when "Load the changed file?" decides whether to ask, and retries wait while that
  question is on screen.
- live.json is saved at least once a minute, so after a crash or power cut CentraLight knows when it last ran (a
  range that ended a minute after a crash still runs its "when it stops" at the restart). And it really does stop
  rewriting live.json every 1.5 s while an overlay is on: half of that earlier fix was missing.
- A deleted input or automation file that's put back (from the Recycle Bin, say) counts as a change.

## A second look at the fixes (3 Nov, morning)

Two more reviewers then checked the fixes themselves, looking for anything they broke. They found more, mostly in
how automations behave around restarts. Each fix again has a test in `tests\test_review_fixes.py`. Fixed:

**Automations and Blackout**
- After an unattended restart, an automation could lift a Blackout that someone had clicked (e.g. Blackout after
  the evening service, then a Windows Update restart in the night). Now CentraLight remembers whether a Blackout
  came from quitting with "Turn lights off". Only that one gives way, and only to a real run of a timed automation
  (e.g. Sunday 09:00), never to "CentraLight starts" or a "when it stops" step.
- An automation that ended while CentraLight was closed ran its "when it stops" steps at the next start, even days
  later. Now that only happens if CentraLight was closed in the last 10 minutes.
- "Key lights → 0 %" counted as bringing the lights up. Fixed.
- An "Any" automation that matched through something other than the clock (a variable, say) ran again after a
  short sleep. Fixed.
- A new automation could take over a deleted one's state (they get the same id), so it didn't run, or showed the
  loop-guard badge. Now only the same automation brought back by Undo gets its state back.
- A "feed lost" automation fired at every start while Media Background Pane's feed was still connecting. Now
  automations about NDI feeds wait until the feeds have reported (at most 3 s).
- A sound condition could be true at once when its automation was turned back on in silence. Fixed.
- "At 10:30 do this, then 2 minutes later that" lost its second step when a short time range ended. Now a stop
  cancels the waiting steps only if there's a "when it stops" to do instead.

**The lights and the files**
- Removing the top light (or undoing adding one) made CentraLight stop sending its channels, so the real light could
  hold its last colour. Now the packet only grows during a session: a removed light gets zeros.
- If a file was put back exactly as CentraLight had written it (an editor's undo, a copy restored), the change was
  ignored. Fixed.
- A delete made while a save was failing never happened, and after one failure every save waited 5 s instead of
  1.5 s. Fixed. A lasting failure now goes in the log once every ten minutes, not at every retry.
- Smaller: a broken video can't cut short the startup wait for the screen feed; an NDI thread that's restarting
  doesn't hold up stopping it; the watcher never reads more than 2 MB of a file.

**The window**
- My mouse-wheel fix still let a real wheel change number boxes and drop-downs: Qt gives them focus from the wheel
  before any check runs, and my tests had used a made-up wheel event that skips that step. One notch over an
  automation's "Days of the week" box could have turned it into a December-only automation. Now number boxes and
  drop-downs only take focus from a click (or Tab), and a test uses a real wheel.
- The Editor started redrawing again after any change, even in the tray. Now it stays idle until you come back.
- A wheel step on an Editor slider left the drag "open", so the next drag rewrote the old keyframe. Fixed.
- Minimising (or hiding to the tray) now also ends the AI window's "Try it on the lights".
- The Layout tab's coloured territories weren't redrawn after a drag. Fixed.
- The Dashboard's Ctrl+Y could redo an older, unrelated step. Now it only redoes what its Ctrl+Z undid.
- The double-click guard on Blackout and the overlay buttons is now 0.35 s, so a quick correction of an accidental
  click still works.

## A review of the whole app (3 Nov, after M9)

Five reviewers (separate Claude agents, read-only, one per area: the lights path, automations and the remote, the
show's files, the Editor, the main window) went through all 23,500 lines looking for things that would go wrong
for you, and reproduced each one with a small script. They found 46 problems. Each fix has a test in
`tests\test_review_fixes.py` that fails without it. Everything worth fixing is fixed (part 1 was committed
first, at 05:21; part 2 afterwards):

**Things that could stop or freeze CentraLight**
- One wrongly typed value in a show or automation file (e.g. an AI writing a list where a word should be) stopped
  CentraLight starting at all. Now that one item is left out with a warning; if the show can't be opened at all,
  CentraLight starts with the standard church lights, read-only, and says why.
- A NaN ("not a number") in an input file froze all DMX, Blackout included. Now it reads as missing, and the lights'
  maths treats it as 0.
- An automation with a wrong value could break the engine's checks. Now that condition just isn't true (logged).
- The session log could fail on an odd character in a message (what Media Background Pane hit on 1 Nov) and take the
  thread that was logging down with it. Logging can't fail now.

**Automations**
- Restarting CentraLight in the middle of a service ran the morning automation again, over whatever someone had
  chosen since. Now an automation only runs again when a new occurrence begins (next Sunday), whether CentraLight was
  closed, asleep or restarted. Deleting one by mistake and pressing Ctrl+Z doesn't run it again either.
- An overnight time range with a day or date ("24 Dec, 23:00–01:00") ran on the wrong night and stopped at midnight,
  mid-service. Now the part after midnight counts as the day it started.
- Delayed actions ("after 5 s: …") still happened after the automation had stopped, been turned off or deleted.
  Now they're dropped.
- "Sound is loud enough" with a "when it stops" action tripped the loop guard during ordinary music. Its stops don't
  count now, and a sound condition only stops after 2 s of quiet (a gap between songs doesn't stop it).
- After a sleep, a range that ended meanwhile ran at wake (e.g. 13:00 for 09:00–12:30), and a new day's range could be
  missed. Fixed. A wake that Windows reports twice counts once.
- "The PC starts" didn't fire after a normal power-on (Fast Startup is on here, so Windows' running time doesn't go
  back to 0) and would have fired on every start after 25 days. Now it also counts when Windows starts CentraLight
  at sign-in, and Windows' running time is read correctly.
- "An NDI feed is there" never became true for the names its own drop-down offers. Names now match the way inputs
  match them, and feeds that only automations look at are kept connected.
- After a "Turn lights off" quit, CentraLight starts blacked out, so an unattended Sunday automation "ran" while the
  room stayed dark. Now a Blackout left from before the restart gives way when an automation sends an input. (A
  Blackout someone chooses is never lifted by an automation.)
- New: the **Sunrise / sunset** condition (see M9).

**The lights**
- A light added on the Layout page (or moved past channel 76) got no DMX until a restart: the packet length was
  fixed at startup. Now it follows the show at once.
- Restarting with Follow screen on Base sent the lights black for 1–2 s while the NDI feed connected (Wednesday's
  step 18 would have failed). Now nothing is sent until the feed's first picture arrives (at most 3 s); the dongle
  keeps showing the old look meanwhile.
- After the PC wakes, the dongle is now really reopened (the "reconnect" did nothing).
- A video whose file is shorter than it claims made its reader spin a whole CPU core near the end. Fixed.
- Shuffle colours and then Ctrl+Z left the lights and the Identify legend different. Fixed.

**The window**
- Closing the window to the tray (or minimising it) left Identify, test colours or the Editor's Show on lights on,
  so nothing sent to Base showed on the lights until someone opened the window again. Now they switch off when the
  window goes out of sight.
- Rolling the mouse wheel over a fader changed live levels (and switched the festoon on), and scrolling the
  Dashboard changed overlay opacities on the way past. Now the wheel only changes a control you've clicked into;
  otherwise it scrolls the page.
- Number boxes couldn't be typed into while the Editor was playing (it plays by default). Fixed.
- The Editor kept drawing about 8 times a second after you left it, all week in the tray. Now it stops.
- A double-click on Blackout (or a tile's overlay button) toggled twice. Now it counts as one click.
- The padlock didn't lock the Layers panel's switches and faders. Now it does.
- After visiting the Editor's Globals tab, Ctrl+Z could undo an unrelated show step (even remove an input). Now
  undo follows what's selected.
- The Automation tab's rows kept showing old values after Undo, and the next edit put the undone value back. Fixed.
- A stuck engine (lights frozen) looked healthy. Now a red **Not responding** chip appears, and Diagnostics says
  how long it's been.
- Ctrl+V on the Dashboard only worked after a tile had been clicked. Fixed.
- Small leaks: closed Settings dialogs (each with a timer still running), right-click menus, live.json rewritten
  every 1.5 s while an overlay was on, and the Layout page redrawing its territories on every step of a drag. Fixed.
- Ticking an automation's days in two steps (untick Sun, then tick Sat) could switch the automation off for good:
  CentraLight read its own in-between save back as an outside change. Now its own saves are recognised by their
  exact bytes. Also, a date's day can't go past the month's end, and "no days ticked" says so.

**The Editor** (part 2)
- Clicking an element in the palette after a timed input had played to its end added an invisible 0.1 s element at
  the end. Now it goes at the start.
- Trimming an element's start past a keyframe made the look jump (two keys at the same moment). Now it keeps the
  look there, as Split does.
- Dragging a keyframe on the timeline ran away from the pointer (the zoom re-fitted at every step). Fixed.
- Dragging a keyframed setting while the Editor played wrote a keyframe at every step of the drag, and shape and
  picture drags scattered keys along the playhead's path. Now a drag puts one key at the moment it began.
- Two separate drags of a slider (or two typed values) merged into one undo step, while unticking a group of lights
  made one step per light. Now each drag is one step, and a group is one step.
- The Selected view kept showing the previous element's picture when the new one had none yet. Fixed.
- Smaller: a rebuild could delete the colour dialog under itself; "Copied ✓" could touch a deleted button; a
  right-click during a drag lost its undo step; the Infinite box stayed ticked after Cancel.

**The show's files** (part 2)
- A save that failed (a file held by another program, read-only, or the disk full) stopped the rest of that save,
  was never tried again, and nothing said so: after a restart the changes were gone. Now each file is saved on its
  own, a failed one is tried again every 5 s, and a red **Not saved** chip in the header says what and why until it
  works.
- While "Load the changed file?" was on screen, the autosave could write over the file, leaving the app and the
  file different. Now saving and checking wait for the answer; the file is read again afterwards, and "keep mine"
  saves at once.
- Files that couldn't be read when the show opened just vanished. Now an amber **File problems** chip lists them
  (click it for the whole list).
- Ctrl+Z on the Dashboard after an AI had changed an input undid something else (even removing an AI-made input).
  Now Ctrl+Z on the Dashboard undoes the newest change to the inputs or the show, including one an AI made to an
  input. (Layout and automation changes are undone on their own tabs.)
- AGENTS.md and AI_GUIDE.md in the show folder were replaced whoever had written them. Now CentraLight only replaces
  the ones it wrote (they start with a marker line).
- The Layout tab's "Where" notes now follow each light by its id: a rename (and its undo) keeps the note with its
  light, and a name another light already has is refused. fixture_map.csv itself is unchanged
  (`usb_dmx_patch_test.py` still reads it).

**The remote and NDI** (part 2)
- The help page copied `?key=` into its script without escaping, so a link could run script on 127.0.0.1. Now only
  the real key is passed on, escaped.
- `/api/overlay/²/off` gave a server error, and HEAD requests ran commands. Fixed.
- An NDI receiver or discovery thread that hit an unexpected error ended for good (Follow screen would have stopped
  until a restart). Now it starts again after 2 s.
- My own slip, caught before you saw it: part 1's mouse-wheel fix also stopped lists and panels scrolling (a scroll
  area hands the wheel to its scroll bar, which never has focus). Now only faders, number boxes and drop-downs wait
  to be clicked into, and a test checks that lists still scroll.

Left as they are, on purpose or because they're too small to matter now: choosing the same big video again
compares the two files on the window's thread (a short pause); old backups can only be restored by hand; a
house-light action can't "leave the switch as it is"; a second launch in the first 1.5 s of startup exits quietly;
and after waking, NDI reconnects by itself (nothing forces it).

**Afterwards** (small extras)
- Deleting an input that automations use now says which ones (they'd skip that step).
- A settings file or live.json with something odd in it can't stop CentraLight starting either: it starts with the
  defaults (Simulated output) instead.
- Editing is lighter: one small change in the Editor (a step of a slider drag) took about 19 ms of the window's time,
  most of it redrawing tabs that weren't on screen. Those tabs now skip a redraw when nothing they show has changed,
  and the Dashboard's previews only redraw while it's on screen: now about 1.6 ms.

## M9 Polish — done

- **CentraLight.exe** in the CentraLight folder: double-click it to start CentraLight without a console window. It
  has the logo as its icon. Put a shortcut to it on the desktop or the taskbar (the exe itself has to stay in the
  folder, next to main.py). The first time, if the app isn't set up yet, it runs `run.bat` instead. To rebuild it
  (e.g. after changing the logo): `.venv\Scripts\python.exe tools\build_launcher.py`. It's built with the C#
  compiler that comes with Windows, like Media Background Pane's. Git leaves the exe out (`.gitignore`): it's
  built from `launcher\Program.cs` and `launcher\icon.ico`, which are in the commit. Tried: it started the app
  (with temporary settings and Simulated output) with no console window, and that copy was stopped afterwards.
- **Diagnostics** (Settings → Diagnostics…, the tray icon's menu, or Ctrl+Shift+D): the engine (ticks a second,
  time per tick, faults, restarts), the output, each NDI feed (pictures a second, size), audio (how often sound
  arrives, loudness), what's playing, the automations, and CentraLight's own CPU and memory, updated every second.
  **Copy** puts it all on the clipboard to paste into a message; **Open the log** opens session.log.
- **Less CPU.** Before each of its 40 ticks a second, the engine used to spin for the last 2 ms to wake exactly on
  time. Now it just sleeps: Windows wakes it at most about 1 ms late (0.3 ms typically), which the lights can't
  show. Following a 960×540, 24 fps feed, the whole app went from 4–7 % of one core to 1–2 % with its window
  open and about 0.5 % in the tray. A 10-minute soak with the window open averaged 1.8 % of one core (2.5 % at
  worst; 0.11 % of the PC), with memory flat. The lag from the screen to the lights is unchanged (median 46 ms).
- **Settings → Screen feed (NDI)**: the feed that new NDI elements start with (any source whose name contains
  it; "Media Background Pane" unless you change it).
- Ctrl+Shift+D and Ctrl+V can't be taken as input hotkeys any more (CentraLight uses them).
- **README**: a new "Always running" section, including how to let Windows sign in by itself after a restart
  nobody signs in to (e.g. Windows Update in the night). The plan asked the README to explain it; CentraLight never
  changes that setting.
- **Switching to the branch**: the docs used to say `git switch claude/centralight-gui`, but on a copy of the
  repository that refuses (the app's files are already in the folder, untracked as far as `main` knows). Step 21
  of `WEDNESDAY_TESTS.md` now has two commands that switch without changing a single file.
- **Sunrise / sunset** (the plan's one optional condition, added after M9): Automation → + Add a condition →
  **Sunrise / sunset**: **After / Before** **sunset / sunrise**, moved by up to 3 hours either way (e.g. "after
  sunset − 30 min" for the festoon at dusk). It uses Bristol's times, worked out on the PC (no internet), within a
  couple of minutes of the published ones; the row shows today's. "After sunset" lasts until midnight and "Before
  sunrise" starts at midnight, so "all night" is the two together with **Any**. Coming up lists them at the right
  minute each day.

## M8 Audio variables — done

What's there:

- **Settings → Audio**: listen to **This PC's output** (whatever it's playing: ProPresenter, a video, music, with
  nothing to plug in) or to one of the PC's inputs (e.g. a feed from the mixer), or Off (the default). **Rise** and
  **Fall** set how quickly the values follow the sound. A meter shows all six values as the engine has them.
- `audio.level` (loudness), `audio.bass`, `audio.mid`, `audio.treble`, `audio.beat` and `audio.pitch`, all 0–1, in
  any expression: e.g. opacity `0.2 + 0.8 * audio.bass`, or a colour `hsv(audio.pitch)`. Loudness has a slow
  automatic gain, and each band is measured against its own recent peak, so a quiet service and a loud one both
  use the whole range. `beat` jumps to 1 on a kick drum and fades away. With no sound they go back to 0.
- The header's **Audio** chip shows when it's listening (its tooltip says to what, and how loud it is now), or red
  if the device is missing.
- **Automation**: a new condition, **Sound is loud enough**: loudness (or bass, middle…) above a level for at least
  N seconds, e.g. "loudness above 60 % for 5 s → key lights to 40 %".
- Library: **PyAudioWPatch** (PortAudio with Windows' loopback capture). I tried the three the plan named; all
  three have wheels for Python 3.14, and this one captures "This PC's output" most directly. `run.bat` installs it
  the next time it starts (it checks the packages first). It listens in its own thread, analyses each 1/50 s block
  and drops it: nothing is recorded or kept.
- Checked with test tones: an 80 Hz tone is bass, 4 kHz is treble, pitch rises with the note, a kick drum every
  half second gives a beat each time that fades in between, silence goes to 0, and Rise / Fall smooth as set.
  Also: the engine's lights follow `audio.bass`, and the automation condition waits its N seconds (starting again
  if it goes quiet).
- Tried on this PC: "This PC's output" is the Behringer X-USB's Line Out 1-2 (what goes to the mixer), and it
  listens without trouble. The X-USB's inputs (the mixer's own channels) are in the list too.
- The engine's timing test now judges the busy worst case by its 90th percentile tick: once, a single tick held up
  by Windows (ProPresenter and MBP run here too) went just over 8 ms, though the engine itself was as quick as ever.

## M7 Painting and masks — done

What's there (the Editor's Selected view and its tools):

- **Painting on a Paint element**: Brush, Eraser, Bucket (fills the area of the same colour around where you click),
  Gradient (drag from where the colour starts to where it's gone; Shift for a round one) and Light colour (click a
  light's dot: the patch of picture it follows is filled, so that light gets exactly that colour). Size, softness
  and colour sit under the tools while one of them is picked; the Eyedropper picks the colour from the picture.
  Painting sits between the element's fill and its shapes, and follows the element if it's moved or turned.
- **Painting a mask** (Mask mode, any element): Show and Hide brushes. A painted mask works together with the
  ticked lights (Lights, as before: click a dot to tick or untick it).
- What you paint shows straight away while you paint (erasing too), drawn by the same code as the lights.
- **Each stroke (or fill) is one undo step, and undo is exact**: a finished stroke is saved as a new picture file in
  the show folder (`paint\<code>.png` or `masks\<code>.png`, named after its contents and never overwritten), and
  undo just goes back to the file from before. They're in the backups too, and survive a restart.
- **A gizmo for pictures** (Image, Video and NDI elements, Select tool): drag the picture to move it, a corner to
  scale it, the knob to turn it (Shift: 15° steps). Keyframed settings get a keyframe at the playhead.
- The AI guide and `input.schema.json` describe painted strokes and masks (and now also the timed settings).
- Measured: with the biggest brush (120 px) on a busy Paint element, each pointer move including the live preview
  takes well under 1/30 s (a test checks it). The engine runs on its own thread, so painting doesn't hold up the
  lights.

## M6 Timed mode, keyframes and video — done

What's there:

- **Timed inputs**: untick **Infinite** (above the timeline, or in the Input tab). A timed input has a length and
  says what happens at its end: **Hold** (it stays on its last moment), **Loop**, or **Stop** (it leaves Base, or
  its overlay switches off). Sending it again starts it again. Its Dashboard tile shows a progress bar while it plays.
- **The timeline** has a ruler and a playhead: click or drag in the ruler, ▶ / ❚❚ (or Space) to play and pause,
  ⏮ (or Home) for the start. Each element is a bar from its start to its end:
  - drag it to move it (up or down to another track), drag an end to trim it, S or right-click to split it at the
    playhead, Delete or right-click to remove it;
  - right-click → **In transition** / **Out transition** (Fade, Dip, Wipe, Iris, Random by light; 0.5–5 s or any
    length). They show as wedges at the ends of the bar;
  - where two bars on a track overlap, the later one crosses in over the earlier one (an X marks the overlap);
  - drop an element from the palette at a time on a track; drop a transition on the start or end of a bar for its
    in or out transition (anywhere else: the input's own transition);
  - it snaps to the playhead, the start, the end and the other bars' ends; Ctrl + wheel zooms, the wheel scrolls,
    double-click the ruler to see the whole input.
  Going back to Infinite asks which element to keep on any track that has more than one.
- **Keyframes**: a ◆ next to every setting that can change over time. Click it to put a keyframe at the playhead
  (with the value there), click it again to take it away. A keyframed setting stays editable: changing it puts a
  keyframe at the playhead. Right-click ◆ to go to the previous or next keyframe, to choose how it gets to the next
  one (smooth, straight, hold), or to remove them all. The selected element's keyframes are diamonds along the
  bottom of its bar: drag one to retime it, click it to go there, right-click for smooth / straight / hold / delete.
  Dragging a shape in the Selected view while its position is keyframed puts a keyframe there too. Keyframe times
  count from the element's own start (in an infinite input, from when the input starts).
- **The Editor plays**: the viewports show the input at the playhead. It plays when you open an input (so formulas
  move); pause it to put keyframes at a moment. A timed input stops at its end, unless it loops.
- **Video** elements (now in the palette): a video file (copied into the show's media folder) that loops for as
  long as the element lasts, with **Speed** and **Blur**. It's decoded small (at most 320×180, 25 pictures a
  second) and only a little ahead of where it's playing, on its own thread, with hardware decoding where Windows
  offers it, so the engine never waits for it. The Editor shows the exact frame at the playhead.
- The AI guide explains timed inputs, element transitions, keyframe times and Video, with a timed example (the
  tests check that it plays as described).
- Checked by the tests with a hand-moved clock: keyframes, which elements show when, element and cross transitions,
  hold / loop / stop, a timed input starting again when it's sent again, and "Bar sweep" giving the same light
  colours in the Editor as the engine sends to the lights, including when it loops.

Problems the tests found and that are fixed:
- Moving a keyframe left a copy at its old time.
- Dragging a keyframe diamond only moved it for the first step of the drag (each step worked on the preview):
  every drag on the timeline now works from the show as it was when you pressed.
- The layout check now opens a fresh window for each size, as when CentraLight opens at a remembered size (it used
  to resize one window through all of them). That found the colour box of a global on the Dashboard 7 px too narrow
  at 1366×768 with long names; the colour code box is now sized for real colour codes, which also left room for ◆.

## Layout positions only, and Blur (your plan change of 3 Nov) — done

You changed the plan at about 01:15 on 3 Nov, while M5 was under way, so this came straight after M5 rather than
before it.

- **The Layout tab is only about where the lights are.** The blue size knob, the size rings and the **Size** box
  are gone, and so are "Resize" undo steps. Every light follows the same-sized patch of the picture around its
  dot (the size every light had by default: 8 % of the picture's width on each side), shown as a faint ring.
  Saved layouts keep positions and the room photo only. Sizes in files from before are ignored, and they aren't
  written again.
- **Blur** on NDI and Image elements (and Video, when it arrives in M6): Editor → click the element → **Blur**.
  - 0 % (the default) is exactly how Follow screen looked before.
  - Higher: each light averages a wider area of that element's picture, so the colours are calmer and smoother.
  - 100 %: every light shows the whole picture's average colour.
  - Like any setting it can be keyframed or given an expression, and it softens only its own element: a sharp
    Paint shape on another track stays sharp. The same Blur gives the same colours in the Editor and on the lights.
- The AI guide explains Blur and the fixed patch size.

A crash found and fixed along the way: once in about ten runs, the layout check crashed outright. The cause was a
real bug in the Editor's details panel: when you pick another element, the panel rebuilds its rows, and it
deleted the old ones straight away. If a text box in them still had the keyboard (e.g. you'd typed in the NDI
source box), deleting it made it report "editing finished" while half gone, and the app could crash. The same
could happen when a choice that changes which rows show (e.g. Fill: Colour → Gradient) rebuilt the panel from
inside its own signal. Now a half-typed edit is finished first (and goes to the element it was typed for), and the
old rows are removed quietly and deleted a moment later. A test types in the source box and clicks away; the
layout check now also prints where it was if it ever crashes.

## M5 AI inputs — done

What's there:

- **Create with AI** (the **+ New input** tile) and **Edit with AI** (the sparkle at the bottom right of every
  tile, the Editor's strip, or right-click on a tile): one window, no API keys, works with any AI chat website.
  1. Describe the look (or what should change) and click **Copy prompt**: a complete request goes on the
     clipboard (what CentraLight is, the condensed guide, your lights and globals, examples, "reply with one JSON
     block", and your words; for an edit, the input itself too).
  2. Paste it into ChatGPT, Claude, Gemini or Copilot, and paste the whole reply back.
  3. It's checked as you paste. If it's fine you get a **live preview** of the lights (using the current values
     of your globals), **Try it on the lights** (shows only that input on the real lights until you close the
     window; nothing is added), and for an edit a plain list of what changes ("Name: … → …", "Transition: fade 1 s →
     fade 2 s", "Paint E1: fill changed"). If not, the problems are listed in plain words, and **Copy error for the
     AI** puts a ready-made "please fix these" message on the clipboard.
  4. **Add** (or **Replace**): one undo step. AI-made inputs get a small sparkle badge.
  - Replies are read the way chat AIs actually write them: words around a ```json block, `//` comments and
    trailing commas are all fine. An AI that changes the input's id in an edit is ignored (the id stays).
  - If the reply uses a picture from elsewhere on this PC (e.g. `C:\Users\…\Pictures\sunrise.jpg`), the window
    shows it and offers **Copy into the show**; clicking that is the OK (inputs only ever read files inside the
    show folder).
- **Ctrl+V on the Dashboard**: if the clipboard holds an input (an AI's reply), the same window opens with it checked.
- **AI tools in the show folder** (Claude Code, Cursor): the show folder now holds `AI_GUIDE.md` (also as
  `AGENTS.md`, the name such tools look for) and `input.schema.json`. They're generated from the app's own
  settings lists, your lights (ids, names, groups, where they are), the house lights and your globals, and
  rewritten about 2 s after any of those change. Settings → **AI** has **Open show folder**, **Open the AI guide**
  and **Export Claude skill…** (a `centralight-inputs` folder for Claude Code's `.claude\skills`, or to upload
  to Claude).
- **Files changed outside CentraLight** (by those tools or by hand) are picked up within a second (Windows'
  change notices, plus a check every second in case one is missed):
  - a new or changed input or automation is used straight away, as an undo step ("Changed outside CentraLight:
    Pillars pulse"); Ctrl+Z puts the old version back and saves it over the file;
  - a file that can't be used keeps the last good version, and its tile gets a red warning badge (the tooltip
    says what's wrong); fixing the file clears it;
  - a deleted file removes the input (undoable);
  - CentraLight's own saves are recognised and ignored;
  - if you're editing that same input in the Editor and your latest change isn't saved yet, it asks which to keep.

Checked with real AI agents: two Claude agents (Haiku and Sonnet), each acting as Claude Code in a scratch copy of
a show folder, were asked for looks in plain words and followed `AI_GUIDE.md` on their own. Both wrote inputs that
CentraLight accepted first time and that do what was asked: the pillars pulsing between deep purple and the accent
colour at the `speed` global, and a sunset gradient drifting across the room with a white glow over the top lights,
key lights at 60 % and a 4-second fade (plus an edit to Follow screen's transition). What they found unclear went
into the guide: which settings each fill type uses, how to move a gradient (with a new example), what a rate means,
how house levels fade, which optional settings there are, and that a new input needs nothing else.

Problems the tests (and those agents) found and that are fixed:
- **Dragging a speed made the lights flicker.** `wave(time, speed)` worked out its place in the cycle from the
  whole time × speed, so any change of speed jumped to somewhere else in the cycle (dragging the slider in
  Wednesday's step 11 would have flickered). `wave`, `pulse`, `saw`, `tri` and `square` now keep their place when
  their rate changes, so the pace changes smoothly; with a steady rate they give exactly the same values as before.
  This works on the lights, in the Editor and in the previews.
- Undoing an outside change didn't write the old version back to the file (CentraLight still thought the file
  held the old version), and a file added from outside then deleted wasn't noticed. Fixed: CentraLight now notes
  what's on disk when it takes in an outside change.
- Two saves close together could have made CentraLight take its own first save for an outside change. It now
  remembers what it wrote for a few seconds.
- The tile's new sparkle didn't fit next to the overlay buttons at 1280×720, which squashed buttons 1–4 (and the
  narrowest possible tile was already a little too narrow before). The overlay buttons are slimmer and tiles are
  at least 200 px wide; a test now checks the narrowest tile.
- Disabled blue buttons (e.g. **Add** before there's a usable reply) still looked clickable. They're dimmed now.
- Under a narrow light preview (the AI window at 200 % scaling), the second house light showed its bulb but no
  name or level. The house lights now share the width, long names are shortened with "…", and the levels
  always show.

## M4 Automations and always running — done

What's there:

- **Automation** tab (fourth tab): "when these conditions are met, do this". Conditions: time of day (overnight
  ranges are fine), days of the week, dates (e.g. 1 Dec – 6 Jan, optionally only one year), an input is live (on
  Base, in an overlay, or anywhere), a global variable (is / is not / above / below / on / off), an NDI feed is
  sending, Blackout is on, CentraLight starts, the PC starts, the PC wakes. All or Any, and **Not** on each.
  Actions: an input to Base (its own transition, a cut or another one), an input in / out of an overlay, clear an
  overlay, festoon / key lights (level, switch, fade), Master, Blackout, set a global, switch layout, turn another
  automation on or off; each can wait first. Optionally "when it stops matching, do this". **Run now**, Duplicate,
  Delete, Up / Down (the order they run in), a **Coming up** list (next 7 days) and a **History** of runs.
- How it runs (tested with a fake clock that follows UK time): once when its conditions become true, not again while
  they stay true; catches up at startup and after sleep ("Sunday 09:00–12:30" runs if the PC starts at 10:00), but a
  quick restart doesn't run it again; top-to-bottom order, later actions win; manual changes win until the next run;
  a pair that keep setting each other off is stopped by the **loop guard** (more than 5 runs in 30 s) with a red
  chip; the clocks going forward run a skipped time at the next minute, and going back runs a repeated time once.
- The header's **Automations** chip: green "on" (tooltip: the next run), amber "paused", red if the loop guard
  stepped in. Click it to pause / resume them all (also on the tab, the tray menu and the Stream Deck).
- Stream Deck: `/api/automation/<id>/run` (`/enable`, `/disable`), `/api/automations/pause` (`/resume`,
  `/toggle`), `/api/layout/<id>`.
- **Always running**: closing the window hides CentraLight in the tray (it keeps the lights, automations and the
  Stream Deck going); double-click the tray icon to bring it back; **Quit** is in the tray menu and asks first.
  Starting it again (run.bat) while it's running shows the running window. **Start with Windows** (Settings):
  it starts in the tray when you sign in; it's off until you switch it on (Wednesday checklist, step 1b). Sleep /
  wake: Windows' own messages, plus the clocks jumping as a back-up, reconnect the dongle and NDI and check the
  automations. While hidden, nothing is drawn.
- Problems the tests found and that are fixed: a second `showEvent` in the main window had quietly replaced the
  first, so remembered panel sizes were never applied (the Automation tab's editor got squeezed); a test now
  fails if any class defines the same method twice. Panels hidden at startup now work out their default sizes
  when first shown. The layout test now also checks lists, tables and trees (no sideways scrolling; text that
  doesn't fit needs a tooltip), which found the Editor's Lights checklist cutting long names off; each run now
  starts with an empty screenshot folder. Two flaky tests were fixed (a port that might be busy; the
  second-launch test now uses a real second program, as Windows does).
- Each automation is `automations\<id>.json` in the show folder (in the backups too), with its own undo history.

## M3 Variables, expressions and the Stream Deck — done

What's there:

- **Expressions**: **=** next to a setting (in the Editor's details, house levels and local variables) opens a
  small editor. You type a formula such as `wave(time, 0.25)`, `value * 2` or `mix(#FF0000, accent, audio.bass)`.
  Names complete as you type, the value it gives "right now" is shown, and mistakes are explained in red. The
  setting's own value stays as `value` and is used whenever the formula can't be worked out. The = is blue when a
  setting has a formula, red when it has a problem.
  Safe by design: the formula is read with Python's own parser and only maths, comparisons, if-else, the listed
  functions and variable names are allowed. Nothing else is reachable (tested with the usual tricks).
- **Global variables** (Editor → **Globals** tab): number (with a range), colour or switch, each with its
  starting value and **Show on the dashboard**. The Dashboard gets a **Variables** panel with a slider, colour or
  switch for those. Moving one changes the lights at once (tested: the engine and the Editor's viewports follow).
  Values survive a restart (`live.json`).
- **Local variables** (Input tab): an input's own numbers or colours, which can use each other (in any order);
  two that use each other in a circle are marked.
- **Time**: `time` (seconds since the input started), `t`, and `audio.*` (0 until M8 adds audio).
- **Stream Deck / phone remote** (`centralight/remote.py`): `http://127.0.0.1:8746/` lists every URL with big
  buttons; each action is a plain GET (`/api/input/<id>`, `/cut`, `/overlay/1`, `/api/var/<name>?value=…`,
  `/api/house/key-lights?level=0.6`, `/api/master`, `/api/blackout/toggle`, `/api/state`…). It runs on its own
  thread, so it works while the window is minimised or a dialog is open (tested). A **Remote :8746** chip in the
  header opens the page; Settings → **Remote** has the port, "Allow other computers and phones" and its key.
- **Hotkeys**: an input can have a key (Input tab), e.g. F5, that sends it to Base.
- **Copy Stream Deck URLs** (Input tab) and **Copy URL** (each global) put ready-made URLs on the clipboard.
- README: how to set up a Stream Deck key (Elgato's Website action, "GET request in background"; Companion).

Problems the tests found and that are fixed:
- Python's web server lets a second program take over the same port on Windows (SO_REUSEADDR): CentraLight's
  remote now claims its port exclusively, and says so on the chip if another program has it.
  (Media Background Pane's own LAN remote, `lan_server.py`, uses Python's default too; I haven't touched MBP.)
- Refusing a clashing hotkey (e.g. Ctrl+Z) briefly showed, then hid, the reason.

## M2 Editor v1 (infinite mode) — done

What's there:

- **Editor** tab (or the pencil on a tile, or the **New input** tile): elements on the left (Paint, Image, NDI;
  Video arrives with M6) and the six transitions; two viewports, **Selected** (the element alone, with its
  shapes' handles) and **Result** (the whole input, with a strip of the colour each light gets); a 4-track
  timeline (track 4 on top); the details panel on the right. Each viewport has a **Lights** toggle.
- **Paint**: a fill (none, colour, straight or round gradient) plus rectangles and ellipses you draw, drag,
  resize and rotate on the Selected view (Shift keeps them square / straight). Opacity, blend (normal, add,
  screen, multiply), position, size and rotation for every element.
- **Image** (the file is copied into the show's `media` folder) and **NDI** (choose the source).
- **Lights** checklist on every element: untick a light and the element leaves it alone (tested: only that
  light changes). In the Selected view's **Mask** mode, click a dot to tick / untick it.
- **Transitions in** (Cut, Fade, Dip, Wipe with direction and softness, Iris, Random light-by-light), set
  per input; drop one onto the timeline or pick it in the Input tab.
- **Dashboard**: Overlays 1–4 (the 1–4 buttons on each tile; the layer stack has a switch and an Opacity
  fader for each), the pencil, **New input**, Duplicate / Delete (right-click), Shift-click = cut.
- **House lights per input**: an input can set the festoon / key lights when it goes on Base; moving the
  header fader wins until the next input.
- **Show on lights** (solo, with a red LIVE banner) switches off when you leave the Editor.
- **Undo**: every input has its own history, and so does the layout (see M1's extras). Steps have plain
  names, e.g. "Paint E2: opacity 80 % → 60 %", "Move SlimPAR 103"; a slider drag is one step.

Measured (no lights): engine 0.25 ms per tick typical, 5.1 ms in the plan's worst case (a transition on Base
and four busy overlays), against 1 ms / 8 ms targets. A test edits the input that's fading in on every tick
and checks the lights match an unedited engine tick for tick: editing never hitches a running fade.

Problems the tests found and that are fixed:
- The first engine took 72 ms in the worst case; storing pictures plane by plane (4 × H × W), only drawing a
  shape's own box, and caching still elements brought it to about 5 ms.
- An overlay at 0.000000000001 % still counted as "on"; a keyframed house level never switched its light on.
- The Editor needed 1285 px at 1280×720; the shape tools now sit on a second row.
- Number boxes showed their text 1 px wide with Qt's style sheet (a Qt quirk); fixed, and the layout test
  now checks number boxes too.
- **Backups**: two backups in the same second shared a file name, so restoring the newest backup could
  overwrite it before reading it. Restoring now reads the backup first, and a backup never reuses a name.

### M1's extras, added 3 Nov (I'd missed them when I first marked M1 done)

- **Saved layouts**: **Layout: Main ▾** at the top of the Layout tab switches between named layouts, with
  **Save as a new layout…**, **Rename…** and **Delete…**. A layout keeps every light's position and size plus
  the room photo and its strength (a new **Photo strength** fader in Map tools). Switching moves the
  lights' sampling straight away (tested), can be undone, and survives a restart (tested). Each layout is
  `layouts\<id>.json` in the show folder; `show.json` says which is active. Existing shows get "Main".
- **Undo / Redo / History** on the Layout and Editor tabs. Undo's tooltip names the step ("Undo: Move
  SlimPAR 103"); History lists every step and clicking one goes back (or forward) to it. Undo only affects
  what you're looking at: the Layout tab has the layout's history, the Editor the history of the input it's
  editing, and the Dashboard the inputs you added or deleted.

## M1 Layout + Identify + Follow screen — done (the Sunday replacement for `ndi_pixel_mapper.py`)

What's there:

- **Follow screen**: the starter input. Every light follows its own patch of Media Background Pane's picture.
  On the very first run it's put on Base automatically.
- **Dashboard**: the Live view (dots over a dim copy of the Base picture), an **Inputs** grid (click = send to
  Base with a fade, Shift-click = cut, right-click = more; tiles preview what each input looks like, coral
  border = on Base), the layer stack and the Output list. An **NDI** chip in the header: green with frames per
  second, amber "no signal" (holding the last colours) or "searching".
- **Layout** page: drag dots to where the lights are; drag the blue knob to change how much picture each
  light follows; tinted patches show which part of the picture each light owns; click a dot to flash it;
  room photo behind the map; snap and "line up"; a lights table (name, group, type, address, where; channel
  overlaps in red; add / remove) and a house-lights table; **Identify** (12 LED-distinct colours, same on
  the dot and the light, with a legend and Shuffle); **test colours** R/G/B/W/Off on the selection or all.
  Identify and test colours switch off when you leave the page (or quit), so the room is never left in a
  test look. "Where" notes go into `fixture_map.csv`, the same file and format as `usb_dmx_patch_test.py`
  (a test checks both read each other's files).
- **Undo / redo** (Ctrl+Z / Ctrl+Y) for every show change; a whole drag is one step. Changes save 1.5 s
  later on a background thread. (Undo / Redo buttons, History and saved layouts were added on 3 Nov: see M2.)

Measured (Simulated output, so no lights):
- **Lag** from a colour change in an NDI source to the DMX frame: median 47 ms, worst 49 ms (target ≤100 ms).
  `tools\ndi_follow_check.py` measures this with a test NDI source.
- **Source restart**: the lights hold their colour while it's gone and follow again 0.3 s after it's back
  (target about 5 s).
- **CPU** for the whole app following a 960×540 24 fps feed: 3.6 % of one core = 0.23 % of this 16-core PC
  (target ≤3 %). Engine: 0.28 ms per tick. Memory steady at about 122 MB.
- **3-hour soak** (`tools\soak.py`, finished 3 Nov 02:02): the app (as it was after M4) followed a 640×360,
  24 fps test feed for 180 minutes. CPU averaged 4.3 % of one core (0.27 % of the PC; worst minute 6.1 %),
  memory stayed flat (121 MB at the start, 124 MB at the end; the second half averaged only 0.4 MB more than
  the first), and the session log has no errors. The CPU was a little over the 3 %-of-one-core target; M9 brought
  it down to about 1 % (see M9).
- **A second 3-hour soak** (finished 3 Nov 09:02): the app as it was at 06:00 (after M9 and the first review's
  fixes) followed a 960×540, 24 fps test feed for 180 minutes. CPU averaged 1.2 % of one core (0.08 % of the PC;
  worst minute 1.9 %), memory stayed flat (141 MB at the start and at the end, peak 143 MB; the second half no
  higher than the first), and the session log has no errors.
- **A third 3-hour soak** (finished 3 Nov 13:31): the app as committed after M10 followed a 960×540, 24 fps test
  feed for 180 minutes. Memory stayed flat (106 MB at the start, 110 MB at the end, peak 111 MB; the second half
  averaged 1 MB more than the first), and the session log has no errors. The CPU averaged 3.6 % of one core (worst
  minute 5.2 %), with the whole test suite running again and again on the same PC for much of the time.
  - **CPU, version against version** (3 Nov, 14:00): each committed version from before M10 to now, run twice, each
    time with a fresh 960×540, 24 fps test feed (three 20-second windows each). Every version read either about
    10–12 % of one core (0.7 % of this 16-core PC) or well under 1 %, and the same version gave both: e.g. M12 read
    12.3 % one time and 0.5 % the next. So no version stands out: M10–M12 didn't raise it. The low runs are most
    likely ones where NDI wasn't delivering pictures at full rate. With pictures flowing, most of the time goes on
    NDI decoding the 960×540 picture and the receiver shrinking it to 320×180 (the earlier decision keeps the full
    NDI stream, so Media Background Pane doesn't have to make a second, smaller one). Step 15b of the Wednesday
    checklist notes the real figure with ProPresenter and Media Background Pane running.
  - A correction: from the soak's figure I first thought M10 had tripled the CPU, and I added a faster way to
    shrink pictures for the lights (in the commit with the M12 review's fixes). It never did anything, because
    NDI and video pictures are already 320×180 by then, so it's taken out again.
- **A fourth 3-hour soak** (finished 3 Nov 17:08): the app as committed after the first look at M12 followed a
  960×540, 24 fps test feed for 180 minutes and loaded the other show every 2 minutes through the remote, as a
  Stream Deck key does, then sent its Follow screen (90 loads, every one answered). The session log has no errors.
  The CPU averaged 11.4 % of one core (0.71 % of the PC; worst minute 12.3 %), steady throughout: the "pictures
  flowing" figure from the version comparison. Memory rose from 108 MB to 111 MB (peak 113 MB; the second half
  averaged 1.1 MB more than the first), about 1 MB an hour, as in the third soak, which loaded no shows: loading
  shows doesn't add to it. CentraLight is meant to run all week, so a 10-hour soak followed (next).
  - The soak tool printed its per-minute memory in a slightly different MB from its summary (decimal against
    Task Manager's); both use Task Manager's MB now, which is what the soaks above report.
- **A 10-hour soak** (finished 4 Nov, 03:10): the app as committed after the second look at M12 (51bc909) followed
  a 960×540, 24 fps test feed for 600 minutes and loaded the other show every 2 minutes through the remote (300
  loads, every one answered). The session log has no errors. The CPU averaged 10.6 % of one core (0.67 % of the PC;
  worst minute 12.4 %). Memory rose from 109 MB to 117 MB (peak 118 MB), steadily, about 0.7 MB an hour in each
  third of the run: it doesn't level off within 10 hours. That rise isn't CentraLight's own code:
  - Python's memory tracer (which counts every Python allocation, the pictures included) found nothing growing in
    35 minutes of following a feed.
  - A small script that does nothing but receive the same feed through NDI, as CentraLight does, rose at the same
    rate (0.87 MB an hour over 1.8 hours) with no CentraLight running.
  - The first soak (after M4) rose at about the same rate.

  At 0.8 MB an hour a week of running adds about 130 MB, which this PC can spare.
- **A 3-hour soak of M13** (finished 4 Nov, 04:16), and the video cache it found: see "The overnight soaks" near
  the top.

### Media Background Pane is only sending one picture per connection

Found while testing (2 Nov, ~23:00): a plain NDI receiver connected to the running Media Background Pane got
one picture and then nothing for 6 seconds, although MBP's code sends 24 a second. So its compose loop looks
stuck (the "playback stall" in its own notes). CentraLight copes: it shows the picture it got, says **no
signal** in amber, and holds the colours. At first CentraLight reconnected every 3 s while the feed was
quiet; now it only reconnects when the source restarts or the connection is lost (plus a gentle retry every
30 s), so a quiet source isn't pestered. I told the session that's fixing MBP (its video speed) what I saw,
and haven't touched MBP. **On Wednesday, restart Media Background Pane before testing if the NDI chip is amber.**

## M0 Foundations — done

What's there (run it with `run.bat`; the output starts as **Simulated**):

- `main.py` (only one copy runs at a time), `run.bat` (sets up `.venv`, copies the church fonts once from
  `G:\My Drive\Video Projection\Brand\Fonts` into `fonts\`, starts the app), `requirements.txt` + PySide6.
- `centralight/` — the app. Everything except `ui/` and `app.py` works without Qt, so it's tested directly:
  - `engine.py`: 40 ticks a second on its own thread. Each tick: commands → colour lights (Base is black until
    M1) → Master → festoon / key lights with their fades → Blackout → DMX. An error in a tick is logged to
    `%LOCALAPPDATA%\CentraLight\session.log` and the next tick carries on; if the thread dies a watchdog
    restarts it.
  - `output.py`: turns colours into DMX bytes (byte-for-byte the same as `fixtures.set_fixture`; a test checks
    200 random looks) and sends them on its own thread: only when something changes plus once a second,
    trimmed to channel 76, retrying every 2 s if the Eurolite is unplugged or another program has it, and
    sending the current look the moment it's back. **Simulated** opens nothing.
  - `show_io.py` / `new_show.py` / `model.py`: the show folder (`Documents\CentraLight Shows\Central Church`):
    `show.json` (made from `fixtures.PATCH` the first time) and one file per input in `inputs\`. Saves are
    atomic, bad files are reported in plain words instead of crashing, a show from a newer version opens
    read-only, and zipped backups are kept (last 20 + one a day for 60 days).
  - `live_state.py`: what the lights were doing, saved while running and on quit, and restored **before** the
    first DMX frame so a restart doesn't flash the lights.
- The window: header with logo, show name, Dashboard / Editor / Layout tabs, status chips, Lock, Settings,
  **Festoon** and **Key lights** (fader, % and on/off switch each), **Master** and **Blackout**; the Dashboard
  with the Live view (a glowing dot per light in the colour being sent), the layer stack and an Output panel
  listing exactly what each light gets; Settings (output, COM port, quit behaviour, folders); the quit
  question; and a Look preview page (Settings → Look preview…) with the palette and fonts.

Tests (all pass): DMX parity; output thread (send on change, keepalive, trim, unplug/replug, busy port,
close with/without blackout, switching outputs); show files (round trip, validation messages, migrations,
read-only, atomic saves that retry when a file is locked, backups); engine (house fades, Master, Blackout and
instant Blackout, restore with an identical first frame, injected error logged while ticking carries on, dead
thread restarted, ~40 ticks/s); window (tabs, faders, switches, Blackout, Lock, Settings → USB without
opening anything, both quit choices, Cancel, Remember); second copy exits; and the **layout and scaling
test**: every page at 1280×720, 1366×768, 1920×1080 and 2560×1440, at 100/125/150/200 %, with normal and very
long names, plus the Segoe UI fallback. Screenshots are in `tests\screens\latest\` (not in git).

Problems the tests found and that are fixed:
- With a long show name and all four chips, the header was 9 px too wide at 1280×720 and squashed two chips.
- The festoon / key-light on/off switches did nothing when clicked (a PySide signal detail).
- Futura PT's own hinting put gaps inside words ("Edit or", "FEST OON") at 200 %; the app now uses
  vertical-only hinting, which keeps the spacing right (checked with the real Windows renderer too).
- Labels that exactly fit were shortened with "…" because of whole-pixel rounding.

## Decisions (made without you, as agreed — easy to change)

- **M14, to check** (the plan's "proposed" points, built as written after you said go, and the choices I made):
  - **Proposed:** when a copy's box clears, the boxes to its right move left, and the newest copy is on top.
  - **Proposed:** automations, the Stream Deck and the phone page have Send to Overlay, Clear from Overlay (that
    input's copies) and Clear the Overlay.
  - **Proposed:** the 1–4 buttons and Shift-click are gone; each input's hotkey still sends it to Base. Cut to Base
    is on the tile's right-click menu.
  - **A timed input on the Overlay plays once** and then leaves, whatever its end (Hold, Loop and Stop are for
    Base), as the plan says it clears itself when it runs out.
  - **A cleared copy's box goes at once**, while the copy itself fades out the way it came in.
  - **The Overlay keeps an opacity fader** (one now, as each overlay had), under its switch and Clear all; its switch
    fades the whole layer over half a second.
  - **At most 16 copies on the Overlay at once** (all inputs together), so a runaway automation can't slow the
    lights; a send past that is skipped, with a line in the session log.
  - **Hold** means longer than half a second, as agreed. **Dragging a tile** starts when the mouse moves with the
    button down (rather than after a timed hold), so a click stays a click.
  - **Tiles are a little wider at their smallest** (228 pixels instead of 200) so both Send buttons fit, so a row
    may hold one tile fewer.
  - **Proposed (Colour):** a Paint element that's only a fill becomes a Colour element; one with a fill and shapes or
    painting keeps it as its Background, with Split into Colour + Paint.
  - **Split into Colour + Paint** puts the Colour element directly under the painting: on the free track just
    below, or else the painting and the tracks above it move up into an empty track (or the tracks below it move
    down into one), so everything keeps its order. The Colour element gets the same mask, move / size, opacity,
    blend and timing.
  - **Split is offered even when it wouldn't look exactly the same,** with a line under the button saying what would
    change (the plan allowed either). The colour and the painting are then worked out one after the other, so they
    match exactly only at full opacity, with Normal blend, without a fade in or out, and with a mask that's either
    on or off at each light. When nothing would change, the line says it looks exactly the same: as it does for your
    two shows' painted inputs (track 1 of "new-input" in Central Church and "default" in Evening are at full opacity,
    with Normal blend and only ticked lights in their masks; checked on copies of both).
  - **An input saved with a Colour element shows nothing for that element in the version before M14** (it doesn't
    know Colour), in case you ever go back to it. Nor does it know mask shapes or Start with: a mask that starts hidden
    everywhere with a Show shape ("only here") shows everywhere there.
  - **Masks are black-and-white pictures** (your change of 5 Nov, replacing Show / Hide shapes, Start with, and the
    Brush that only hid). So the question about the Brush on a "Hidden everywhere" mask doesn't arise any more: for
    "only here", Bucket the mask black (or draw a black rectangle over it all, or press Invert), then paint or draw in
    white.
  - **Proposed:** in Mask mode the Selected view shows the mask itself, black and white, with the light dots on top,
    instead of the red tint; the Result view shows its effect.
  - **A mask shape's grey is a slider** (Grey, 0 % to 100 %), not a colour picker, so it can only be a grey; it can have
    keyframes and formulas like any setting. Shapes also keep a Paint shape's opacity.
  - **New mask shapes and painting are black** until you choose another grey (White, Black, or the Grey button), as black
    is what does something on a white mask. The eyedropper picks a grey from the mask.
  - **The Eraser paints white back in** (a mask starts white), rather than making a hole.
  - **Invert** saves the turned-round painting as a new file (or no painting at all when it comes out white
    everywhere, as a mask starts); a shape's grey becomes 100 % minus it, each keyframe's too, and a formula becomes
    "1 - (formula)", and back again.
  - **Mask shapes are placed on the screen**, like the mask's painting (not in the element's own picture), so moving or
    scaling the element doesn't move its mask.
  - **Mask mode starts on the Light tool** (ticking lights), as it did; drawing a shape, or picking one in the Mask
    shapes list, switches to Select.
  - **A mask shape written without a grey** (by hand, an AI tool, or before this change) is black, or white if it says
    "mode": "show". "Start with" is no longer read (no file of yours used it, nor Show / Hide shapes).
  - **Which weeks:** the 1st week is the 1st to the 7th of the month (so it holds the first of every day), the 2nd the
    8th to the 14th, and so on; Last is the month's last seven days. The buttons are **Every week**, 1st, 2nd, 3rd,
    4th and Last beside the days; picking a week turns Every week off, and Every week clears the picked ones. With
    every day ticked, the summary says e.g. "1st week of the month".
  - **Proposed (default show):** manual wins; the automation's own "When it stops matching" wins; the week schedule
    shows the default show lighter in the gaps; two loads in the same second are tested.
  - **The default show opens when the PC starts, not every time CentraLight starts:** reopening CentraLight after a
    quit or a crash carries on with the show that was loaded (what the app already does for everything else, and what
    item 10 says for show variables), so a crash in the middle of a service doesn't change the lights. "The PC starts"
    is the same moment as the automations' "The PC starts".
  - **Only an automation that loads a show for as long as it matches** (a time range, days, a feed being lost …)
    hands back to the default show when it stops. One that runs at a moment ("When CentraLight starts → Load a show")
    doesn't, so that workaround keeps its show.
  - **The default show waits for the countdown** after a schedule ends (its banner starts then, rather than early), and
    a show that's loaded already isn't loaded again. CentraLight remembers which automation loaded the show over a
    restart.
  - **A show variable's "Starts at" is now "Starting value"** (the plan asked for it to be labelled clearly, as it's
    what Go back to its starting value uses), and its undo steps say "starting value" too.
  - **The colour window is CentraLight's own** (Qt's picker with Preview, OK and Cancel under it), as Windows' own
    colour window can't show changes while it's open. Enter is OK and Esc is Cancel anywhere in it.
  - **Preview's choice is kept with the PC's start time** (within two minutes) in CentraLight's settings, saved as it
    quits. After a restart, or if CentraLight crashed before saving it, Preview is ticked again.
  - **The brush colour** (a Paint element's Brush, Bucket and Gradient) keeps the plain picker too: it changes nothing
    until you paint with it.
  - **If the show changes under an open colour window** (a global automation loads another show, say), the colour
    picked isn't applied to the new show.
  - **After the review (default show):** only an automation whose conditions are about the clock, an NDI feed or
    sound hands back to the default show when it stops. One that's about a show variable, an input being live, which
    show is loaded or the lights being black doesn't (loading a show changes those, so it would flip back and forth).
  - **A schedule inside a longer one** (Sunday 18:00–20:00 inside "all Sunday"): when the inner one ends, the longer
    one's show loads again (with the countdown), and the default show comes when that one ends. The plan only covered
    back-to-back schedules, where the next one's show is already loading.
  - **When the PC restarts mid-service and the default show opens,** the automation that had loaded the last show
    runs again from the top if it's still under way, as a catch-up does: its show loads, and its other actions (an
    input sent to Base, say) happen too. If someone had loaded a show by hand, nothing runs again: the default show
    stays.
  - **Which longer schedule's show comes back** when an inner one ends: the one that was on before it, if it still
    matches; else the lowest in the list that matches. An automation that loads a show and is about the loaded show or
    the lights being black is like a person's choice: its show stays, and nothing comes back over it.
  - **Videos are decoded in software now** (two threads per video), not by the graphics card: for pictures this small it
    was no quicker, and each video took far more memory and time to open. Each copy of a playing video input has its
    own reader, and a new copy takes over one another copy has finished with.
  - **Run now** on a global automation that loads a show is a person's choice (the default show doesn't replace it);
    **Now** on a countdown that started early is still the schedule's own run.
  - **A schedule that ends by going black** (your choice, 5 Nov: its own "When it stops matching" has Cut or Fade to
    black, with any delay) has nothing loaded after it: not the default show, and not a longer schedule's show (loading
    either runs its "When this show loads" automations, and both your shows' "Start Up" would light the church again,
    at midnight for "Dioscesioan Event"). Nothing comes back over it later either: the next show to load is the next
    schedule's, or one you choose. The week schedule draws no default show after such a schedule's block.
  - **None (lights off)** (your change of 5 Nov), the choices I made:
    - **Proposed: the Show menu lists None (lights off)** under the shows, so you can close the show by hand (handy
      for checking this at the rig). It does what a schedule's end does, without the countdown: the lights fade off,
      then the show closes. It's a person's choice, so a schedule ending later brings nothing back over it.
    - **The fade before the close** takes Fade to black's time (Settings; 2 s to start with), the Festoon and Key
      lights too. If the lights are off already, the show closes at once. A show loaded during the fade stops it.
    - **Last loaded, when no show was loaded last:** a PC start carries on with no show (Last loaded is whatever was
      loaded last, none included).
    - **The header keeps the last show's Festoon and Key lights,** greyed out at their levels, and those levels come
      back with the next show. When CentraLight opens with no show (a PC start, say), they're those of the show loaded
      last.
    - **New show… with no show loaded** takes its lights and layouts from the show loaded last (failing that, the
      church's standard lights).
    - **On the card** the shows sit two to a row, as in the mock-up. "Next" is the next global automation that loads
      a show by the clock in the next 7 days ("if its other conditions are true then" when it has other conditions);
      the line is worked out again every 20 seconds, and at once when the automations are paused or resumed.
    - **The phone page with no show** has no Cut / Fade to black buttons (they'd only answer "No show loaded"), and
      says "The lights are off" where it usually says what's on Base.
  - **M16, Companion**, the choices I made:
    - **The module is JavaScript, not TypeScript** (the plan's guess): nothing to compile, and its tests run on
      Companion's own Node 22, which has a WebSocket built in, so the module needs no package but Bitfocus's own.
    - **Commands go as the URLs' paths** (`input/warm-white/cut`), so the module and the Stream Deck URLs can't drift
      apart, and every URL works over the live connection.
    - **What's sent:** only what a key can show changes it: not a transition's progress, levels to the percent, the
      countdown to the second. At most 8 live connections; one silent for 50 seconds (not even answering a ping) is
      closed.
    - **"Flash" on the Stream Deck** is the input's "flash while held" preset (a copy on the Overlay while the key is
      down); the Console has no flash of its own.
    - **Console from the Stream Deck:** Release all, Release some lights, Tap, Next step, Pause / Play on and speed.
      There's no "set a colour" yet: the Console page is where lights are set.
    - **Presets** use the connection's own name in their variables (`$(centralight:copies_warm-white)`), so they work
      however the connection is named.
  - **M15, the Console page** (parts 1 to 3), the choices I made:
    - **The tab is after Dashboard,** so Ctrl+2 opens the Console, and the Editor, Layout and Automation move to Ctrl+3,
      4 and 5.
    - **Clicking a light means its dot** (and a little round it), not its whole glow, so a box can start between lights.
      A box chooses left to right.
    - **Odd and Even** take every other one of the chosen lights, in their order; with none chosen, of all of them.
    - **Faders follow your hand** (no fade); the Fade time is for a colour, a saved colour and a fan.
    - **The colour picker's Cancel** puts back what the lights had: the Console's colour, or the inputs' again.
    - **Fans** go across the chosen lights in their order (the Order below the map); Rainbow goes once round the wheel.
    - **The automation action is called "Release the Console"** (the plan's "Release Live control": "Live" became
      "Console"). It uses Settings' release fade.
    - **The header chip: a click lets everything go** (with the release fade). The phone page has the same button.
    - **Another show loading** lets go with the release fade; the PC starting lets go at once.
    - **Chases:** Add step takes Master back out of the colours (Master scales the chase again when it plays). Quick
      replaces the steps there were; "Colours across" uses your saved colours (four rainbow colours when there are none).
      Release takes the chosen lights out of the chase too; it stops when none are left. Play once stops on its last
      step (bounce: back on the first). Random plays every step once a pass, never the same one twice in a row. Tap
      needs two taps, averages up to five, and starts again after a gap of 2.5 s; it works before Play too.
    - **Saved groups and colours** go in the show's `show.json`, with undo; a show keeps up to 24 colours.
    - **Save as input** names the new input "Console look" / "Console chase" (a number added if it's taken), and puts it
      at the end of the Dashboard. A look's brightness goes in with its colour, so as an input it lights the same.
      Load into Console chooses the input's lights on the map and opens the Console page.
    - **The "On: …" line** names a whole patch group first (Top Wash, Side Fill, Pillars), then a whole saved group,
      then single lights, in the chase's order; "every light" when it's all of them.
- **M13 choices** (all easy to change):
  - **The week schedule is a tab beside the automation's editor** (Automation | Week schedule) rather than squeezed
    in beside the Coming up list: at 1280×720 that panel is too short for a week, and the left column too narrow.
    Clicking a block opens its automation in the Automation tab.
  - **Each new version of an original gets its own copy** ("candles 2.mp4"): the copy in use is never overwritten
    (Windows won't replace a file a video player has open), and undo can go back to the old one. Copies nothing uses
    any more (in the show or its undo history) are removed when the show loads. Only copies CentraLight made are
    ever removed (it keeps a list in `media\links.json`); files you put in `media` yourself never are.
  - **Who said yes to a link** is kept in CentraLight's app data (`allowed_links.json`), not in the show, so a show,
    an AI tool or an imported file can't allow its own links. Importing inputs from another show on this PC counts as
    the OK (its summary lists the links, as the plan says); a link in a .clshow file waits for **Allow** in the
    Editor's File row (or the Layout tab, for a room photo).
  - **An original that stops answering** (a stuck drive) is given up after 45 s with no progress; the other checks
    carry on. After three such, checking stops until CentraLight restarts (the copies keep playing). One that couldn't
    be reached isn't tried again by the 5-minute check for 15 minutes (loading the show, or selecting it, still does).
  - **The countdown:** none in the first minute after CentraLight starts (the start-up rules apply); a run waits for
    it as a whole, from its show load or fade on, in the runner's queue (so anything that drops waiting steps drops it);
    one that started early for a run the clock didn't start after all goes (5 s after it was due at most); **Wait 10
    min** moves it 10 minutes after it was due; a change that wouldn't change anything gets none (the show's loaded
    already, or the colour lights are black already); under the Lock only Wait and Skip work; Run now ends it; a
    countdown isn't kept over a restart, and one from before the PC slept is dropped when it wakes. The button says
    **Load now** for a show and **Do it now** for black.
  - **Text from files:** "<" and ">" in names and settings read from show files become "‹" and "›" (so nothing a file
    says is ever shown as HTML); formulas, comparisons and notes keep theirs.
  - **Imported automations start from how things are now**, as after a restart: one whose conditions are true
    already runs only when they next come true.
  - **Importing settings** (from an Export everything file) keeps this PC's own output (Simulated / USB / Art-Net),
    USB port, start with Windows, phone page (its key, and whether other computers may use it), audio source, shows
    folder and window. Export never puts the phone page's key in the file.
  - **The Dashboard's tile says "From another show…"** (beside Create with AI), a shorter label than the plan's
    "Import from another show…", so it fits the tile at the smallest window. Its tooltip says the rest.
- **M12 choices** (all easy to change):
  - **Renaming a show** changes the name you see, not its folder: its Stream Deck id (from the folder's name) keeps
    working. New and copied shows get a folder named after them.
  - **A new show opens straight away** (it's what you'd do next, and it has no automations); **a copy doesn't**, as
    its automations would run as it opened (you agreed: it stays put until you load it).
  - **Loading a show's automations:** like CentraLight starting, they're checked straight away with catch-up, so at
    10:00 on a Sunday a show whose automation says "Sunday 09:00–12:30 → Warm welcome" sends Warm welcome when it
    loads. Loading the show itself never sends anything. Every load catches up, going back to a show you left
    included (your call: automations override the freeze). For "always do this when the show loads", use "When this
    show loads".
  - **A show asked for while a dialog is open** (by an automation or the Stream Deck) loads when the dialog closes.
    If it's still open after 30 s, CentraLight closes it (as if Cancel was pressed) and loads the show; only the
    newest request counts.
  - **Naming a show in an automation:** a value like `evening` (lower case, no spaces: what CentraLight stores) is
    only ever a show's id; anything else ("Evening", "Sunday Morning") is matched against the shows' names.
  - **After a "Turn lights off" quit,** loading a show by hand takes charge (its "When this show loads" automations
    can light things); a global automation loading one does too if its own time range began after the quit
    (e.g. Sunday 08:30), and otherwise doesn't (e.g. "CentraLight starts").
  - **A show's variables** come back as they were when it was last loaded (kept in live.json); a show loaded for the
    first time starts with their starting values.
  - **Duplicate** doesn't copy the backups folder (they're the old show's history).
- **M10's numbers** (all easy to change):
  - **Area** grows as its square: a patch's half-width is Area² screen widths, so 25 % is 0.0625 (matching the old
    patch), 50 % a quarter of the screen and 100 % the whole screen from anywhere. The patch is a square: it matched
    the old round patch within 1.2 of 255 steps on average, and a square is exact and cheap to average.
  - **Colour boost** 100 % multiplies a light's saturation by up to 4 (never past full).
  - **Snap on cuts:** when more than half of an element's lights change by more than a quarter (of full) between
    two looks at the picture, they jump instead of gliding.
  - **New shapes** get 20 % Softness. Shapes already in your inputs keep theirs (0 % unless you set it).
  - **The Light colour tool** paints a dab up to 8 % of the width across, and never as far as halfway to the next
    light.
- **Output defaults to Simulated.** Settings → Output → Eurolite (USB) drives the lights. Leaving the Eurolite
  (switching to Simulated or Art-Net) blacks its lights out first, so they're never left stuck on.
- **House lights** start off, with their faders at 100 %. Dragging a fader turns that light on; the switch fades
  over half a second; fader moves are smoothed over 80 ms. A held Cut / Fade to black switches them off without
  moving the faders, and their next plain switch on comes back the same way (a cut, or a fade over the same time).
- **Quitting:** "Turn lights off" fades everything to black over half a second, and the next start comes up with
  everything black and nothing on Base; automations leave it so until one of their time ranges begins after the
  quit (anything a person sends lights things at once). "Leave lights as they are" restarts exactly where it left
  off. The choice can be remembered (Settings → When you quit).
- **Live state** lives in `%LOCALAPPDATA%\CentraLight\live.json` (it's about this PC, not a show). Since M12 it's
  app-wide and names the show that was loaded last; if that show has gone, only what's app-wide is used.
- **Lock** blocks the lights controls (faders, switches, Cut / Fade to black, the show picker; the dashboard
  buttons) but not the tabs.
- **Settings** is the gear button next to Lock (the plan didn't say where it goes).
- **USB sends 76 channels** (up to the last SlimPAR's channel), as planned. If the real Eurolite doesn't like
  short packets, Settings → "Send all 512 channels" goes back to full packets (on the Wednesday checklist).
- **A broken `show.json`** never gets overwritten: the app runs with the standard church lights, read-only,
  and says what's wrong. Backups are taken when the show opens and every 10 minutes, only if something changed.
- **Layout test sizes are window sizes in Qt's logical pixels.** Qt's offscreen test platform only knows the
  fonts it's given, so the tests load Segoe UI from `C:\Windows\Fonts` as well as the brand fonts.
- The window's minimum size is 1280×720 (or the screen, if that's smaller). This PC has five 1920×1080 screens
  at 100 %.
- **The only starter input is "Follow screen"** (the plan's first-run Base). Other looks get made in the Editor
  (next milestone) or by AI (M4). A show with no inputs at all gets Follow screen added.
- **Identify and test colours switch off when you leave the Layout page**, and when the app quits (the engine
  does one last tick so this always reaches the lights before it stops).
- **Add / remove** works for colour lights; house lights can be renamed and re-addressed but not added or
  removed (there are just the two dimmers).
- **Lock** also disables the Layout page and the input tiles (tabs still work).
- **NDI** uses the full-quality stream ("HIGHEST" bandwidth) as planned: a low-bandwidth stream would make
  Media Background Pane encode a second, smaller copy, costing it CPU.
- **Tile previews**: tiles of inputs that aren't on Base re-render every 2 s with the engine's own code, so
  they show what the input would look like (including the current NDI picture).
- `tools\` holds two checks that never touch the lights: `ndi_follow_check.py` (lag and reconnect with a test
  NDI source) and `soak.py` (runs the app for a while and records CPU and memory).

- **Saved layouts**: a new photo is stored as `media\room-<code>.jpg` (named from its contents) instead of
  overwriting `room.jpg`, so undo can bring back the previous photo. "Remove photo" keeps the file for the same
  reason. An old `room.jpg` becomes Main's photo. The active layout is remembered in `show.json` (not in
  `live.json` as the plan also mentions: one place is enough, and it's saved with every change).
- **History** is a drop-down panel from the History button (it doesn't take room from the map or the Editor).
  It starts with "Start (before these changes)"; histories last until the app closes, as planned.
- **Which history**: the Dashboard's Ctrl+Z covers adding, duplicating and deleting inputs. A brand-new input
  starts with an empty history. If an input is deleted and the delete is undone, its own history comes back
  with it.
- **Editor**: dropping a transition onto the timeline sets the input's transition in; the Video element is
  shown but greyed out until M6; Lock disables the Editor and the Layout tab as well as the tiles.
- **Start with Windows** stays off until you switch it on (Wednesday checklist, step 1b), as you asked, so a
  half-built version never starts itself at sign-in. The tests check writing and removing the entry on a scratch
  registry key; test copies of the app never touch the real start-up list, and it has no CentraLight entry now.
- **A broken automation file** is loaded switched off rather than with the broken condition dropped (dropping
  "Sunday" from "Sunday 09:00" would make it run every day). Broken actions are left out, with a note.
- **Events** (CentraLight starts, the PC wakes...) are true for one check only. Windows sending two "resumed" messages
  for one wake counts once.
- **Pausing**: things that become true while paused don't run when you resume (you're in charge then).
- **Switching layout** from an automation or the Stream Deck shows up in the Layout tab's history, named after who did it.
- **Remote safety**: it answers this PC only (127.0.0.1) unless "Allow other computers" is ticked; then other
  computers need the key. Requests a web page in a browser makes for another website are refused, and so are
  requests addressed to another host name (a trick called DNS rebinding). The key is made on first run.
- **Hotkeys** work while CentraLight's window is in front, not system-wide, so they can't clash with
  ProPresenter's keys. Keys CentraLight already uses (Ctrl+Z, Ctrl+1…) and keys another input has are refused.
- **Expressions**: the plan's functions plus a few small extras (`ceil`, `sqrt`, `wave`, `pi`); `round` rounds
  halves up. A formula that can't even be read can't be saved; one that uses a name that doesn't exist yet can
  (it shows red and falls back), since you might be about to add that variable.
- **Renaming a global** doesn't rewrite formulas that use the old name; they turn red so you can see them.
- **The API takes names too**: `/api/input/Warm%20white` works as well as the id, and house lights can be
  `festoon` / `key-lights` or their fixture ids.
- **Inputs switch on house lights they mention** when they go on Base (otherwise a keyframed level on a light
  that was switched off would never show).
- **AI without API keys**: CentraLight never talks to an AI service itself. You copy the prompt out and the reply
  back (the plan's route 1), or an AI tool edits the show folder (route 2). Nothing to pay for or sign up to, and
  nothing leaves the PC except what you paste.
- **The AI guide** is only rewritten when its text would change, so tools watching the folder aren't disturbed.
  It also covers automations, so Claude Code can write those too.
- **A clash with the Editor** (a file changes outside while you're editing that input) only asks when your
  latest change there hasn't been saved yet. Changes are saved a second and a half after you make them, so in
  practice it rarely asks; otherwise the file's version is taken, as one undo step.
- **Try it on the lights** shows only the AI's input (like the Editor's "Show on lights") and stops when the AI
  window closes, whichever way it closes.
- **Video decoding** uses the FFmpeg that's built into OpenCV (already part of CentraLight) rather than Media
  Background Pane's ffmpeg.exe: nothing extra to install, and it still decodes on its own thread, paced by playback.
- **Keyframes** are for element and shape settings (the timeline and the ◆). House-light levels and local variables
  can change over time with expressions; keyframes for those can come later if you want them.
- **Stop** at the end of a timed input on Base is a cut (Base goes black). For a fade, give the last element an out
  transition. (On the Overlay, since M14, a timed copy leaves the way it came in once it has played.)
- **The Editor's playhead waits at the start** when you open an input (your change of 4 Nov; it used to play
  straight away). Press play to see expressions and videos move.
- **Painting undo** keeps whole pictures (each stroke a new file named after its contents) rather than the plan's
  32×32 tiles: it's exact by design, fits the undo history every other edit uses, and a 640×360 picture is small.

## Problems

- Commits were blocked by Claude Code's safety check until you gave the go-ahead yourself (3 Nov, 01:10).
- Media Background Pane only sends one picture per connection at the moment (see M1).
- (Not mine) `artnet_test.py`'s docstring has `.\.venv`, which newer Python warns about ("invalid escape
  sequence"). Harmless; a raw string (`r"""`) would silence it.

## Next

M14 is built (all eleven items, the two extras and the clean-up), reviewed twice, and its fixes are in; see the
status table for the tests and the soak. What's left:
- **Your checks on the lights:** section 20b of `docs/WEDNESDAY_TESTS.md`, and the "M14, to check" decisions above.
- **Moving the main folder to M14** (with your OK): copy `Documents\CentraLight Shows` first. Once this version saves
  an input with a Colour element, the version you ran on Wednesday shows that element as nothing.
- **M15, the Console page** (in the plan) is on hold until you say go.
- **The new logo** (5 Nov) shows in the app's window, taskbar and tray straight away, and on the "No show loaded"
  card. CentraLight.exe keeps its old icon until it's built again (`.venv\Scripts\python.exe tools\build_launcher.py`,
  at the main-folder update), and Windows may show .clshow files' old icon until it next refreshes its icons. GitHub's
  main has the old 1024 PNG (6ccfe9d): when main and this branch meet, keep the branch's new logo.
- **Follow-ups, not urgent:** a drawn icon that Qt copies to change it would crash (nothing does that today); a tile
  dragged while a show load replaces the tiles could, in theory, be freed mid-drag (couldn't be made to happen); the
  week schedule's drawing of the default show around a "When it stops matching" load.
