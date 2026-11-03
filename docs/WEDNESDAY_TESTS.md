# Wednesday 4 Nov, 3pm — trying the CentraLight app on the real lights

Everything so far was built and tested **without the lights** (Simulated output, fake dongles, test NDI
sources). This is the checklist for the room. Tick things off as you go; if anything misbehaves, note the
step number and look in the session log (step 0 shows where it is).

Open PowerShell in the CentraLight folder for the commands:

```powershell
cd C:\Users\avbri\Documents\Centralight
```

## 0. Before you start

- [ ] **Restart Media Background Pane first.** On 2 Nov its picture froze (its compositor stopped), and the
      copy that's running still has the old video code that plays clips too fast (the other Claude session
      fixed that in its files; a restart picks the fix up). Then play something colourful.
- [ ] Nothing else is using the Eurolite: close MagicQ, QLC+, and any `usb_dmx_test.py`,
      `usb_dmx_patch_test.py` or `ndi_pixel_mapper.py` windows.
- [ ] The Eurolite is plugged in (the lights are currently blacked out: the dongle is repeating an all-zero frame).
- [ ] If something goes wrong, the app's log is here (open it in Notepad):

```powershell
notepad "$env:LOCALAPPDATA\CentraLight\session.log"
```

## 1. Switch the output from Simulated to the Eurolite (USB)

The app starts in **Simulated** mode: it shows what it would send but never opens the dongle.

```powershell
.\run.bat
```

- [ ] The window opens. The first chip at the top right is amber: **Simulated · …/s**.
- [ ] Click the **gear** (top right, next to the padlock) → **Output** → **Eurolite USB-DMX512 PRO** → leave
      COM port on **Find it automatically** → **OK**.
- [ ] The chip turns green: **COM3 · …/s**. (Red **COM3 busy** = another program has the dongle: close it.
      **No dongle** = check the USB cable.)
- [ ] The lights now show what the app is doing. On a first run that's **Follow screen** on Base, with the
      festoon and key lights off.

## 1b. Turn on Start with Windows

It's off until now, so a half-built version could never start itself at sign-in.

- [ ] Gear → **Starting and quitting** → tick **Start CentraLight when I sign in to Windows** → **OK**.
- [ ] Check Windows has the entry (it starts `main.py --minimized`, so it opens in the tray):

```powershell
Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Run -Name CentraLight
```

## 2. Follow screen

- [ ] The SlimPARs follow Media Background Pane's picture (the Dashboard's Live view shows the same colours).
- [ ] The **NDI** chip is green with a frame rate (e.g. **NDI · 24 fps**). Amber **no signal** means Media
      Background Pane isn't sending pictures (on 2 Nov it was stuck sending one picture per connection — see
      the build log); the lights then hold their last colours.
- [ ] Colour changes reach the lights quickly (measured without the lights: about 50 ms).

## 3. Festoon, key lights, Master

- [ ] Drag **Festoon** to about 50 %: the festoon comes on (dragging turns it on) and follows the fader.
- [ ] Click the festoon's switch off and on: it fades over half a second.
- [ ] Same for **Key lights**.
- [ ] Drag **Master** to 50 %: the SlimPARs dim; the festoon and key lights don't change.

## 4. Blackout

- [ ] Click **Blackout**: everything fades to black in half a second and the button turns solid red,
      **Lights off — click to restore**.
- [ ] Click it again: everything comes back as it was.
- [ ] Shift-click: instant, both ways.
- [ ] Click the **padlock**: the faders and Blackout stop responding (a blue banner says so). Click it again.

## 5. Layout and Identify (find out where each light is)

- [ ] **Layout** tab → switch on **Identify**: every SlimPAR shows a different colour, matching the dots and
      the list on the right.
- [ ] Drag each dot to where that light is in the room (the map is the shape of the screen). Drag the blue
      knob on a dot's ring to change how much of the picture that light follows.
- [ ] Click a dot: that light flashes white three times.
- [ ] Type where each light is in the **Where** column (e.g. "front left, above the drums"). It's saved to
      `fixture_map.csv`, the same file `usb_dmx_patch_test.py` uses.
- [ ] Select some dots and try **Test colours** (Red, Green, Blue, White, Off), then **Stop test**.
- [ ] Go back to the **Dashboard**: Identify and test colours switch off by themselves.
- [ ] Back on the Dashboard, with Follow screen on Base, play something with different colours on the left
      and right of the screen: the lights on each side should follow their side.

## 6. Saved layouts and undo on the Layout tab

- [ ] **Layout** tab → **Layout: Main ▾** (top left) → **Save as a new layout…** → call it "Test" → OK.
- [ ] Drag two dots somewhere silly. The lights follow from their new spots straight away.
- [ ] **Layout: Test ▾** → **Main**: the dots (and the lights' sampling) jump back at once.
- [ ] Click **Undo** (the curved arrow, top right) or press Ctrl+Z: back on "Test". Hover over Undo/Redo: the
      tooltip names the step ("Undo: Switch to layout Main").
- [ ] Click **History** (the list icon next to Redo) and click an older step: the page goes back to it.
- [ ] Switch back to **Main**, then **Layout: Main ▾** → pick **Test** → **Delete this layout…** → Yes.

## 7. Make two inputs in the Editor

- [ ] Dashboard → **New input** tile: the Editor opens on "New input".
- [ ] Click **Paint** in the Elements list. In the details on the right set the colour to a warm white
      (e.g. `#FFB070`). With nothing selected on the timeline (click empty space in a track), rename it
      **Warm white** on the Input tab.
- [ ] **Show on lights**: only this input is on the lights while you edit, with a red **LIVE** banner.
      Click **Done**: Show on lights switches off by itself and the lights go back to what's on Base.
- [ ] Make a second one, **Worship blue** (`#2050FF`).
- [ ] Ctrl+Z in the Editor only undoes changes to the input you're editing.

## 8. The six transitions

For each of **Cut, Fade, Dip, Wipe, Iris, Random**: open Worship blue in the Editor (the pencil on its tile),
click the transition in the **Transitions** list, go back to the Dashboard, click Warm white, then click
Worship blue.

- [ ] Each one looks right on the lights (note any that look odd): Cut is instant; Fade cross-fades; Dip goes
      through black; Wipe sweeps across (Direction in the Input tab turns it); Iris grows from the middle;
      Random changes light by light.
- [ ] Shift-click a tile: it cuts straight there, whatever its transition.

## 9. Overlays: "Pillars purple" over Follow screen

- [ ] Put **Follow screen** on Base.
- [ ] New input → **Paint**, purple (`#9B30FF`). In its **Lights** list untick everything except the pillar
      lights (SlimPAR 106 and 107 in the standard patch). Name it **Pillars purple**.
- [ ] Dashboard: click the **1** on its tile: the pillars fade to purple while every other light keeps
      following the screen. The layer stack shows it on Overlay 1, with a switch and an **Opacity** fader.
- [ ] Drag Overlay 1's opacity to 50 %: the pillars go half-way between purple and the screen colour.
- [ ] Click the **1** again (or the switch): it fades away.

## 10. House lights set by an input

- [ ] Open Warm white → Input tab → **House lights** → tick **Set** for Key lights, 60 %.
- [ ] Send Worship blue, then Warm white: the key lights fade up to 60 % with the input.
- [ ] Move the Key lights fader in the header: your move wins until the next input is sent.

## 11. Variables and expressions

- [ ] Editor → open Worship blue → **Globals** tab → **+ Add a global variable ▾ → Number**. Rename it
      **speed**, range 0 to 4, starts at 1, tick **Show on the dashboard**.
- [ ] Click the Paint element → **=** next to **Opacity** → type `0.3 + 0.7 * wave(time, speed)` → **Use it**.
      The **=** turns blue. Send Worship blue to Base: the lights breathe.
- [ ] Dashboard → **Variables** → drag **speed**: the breathing speeds up and slows down straight away.
- [ ] Type a mistake on purpose (e.g. `wave(time, sped)`): the **=** turns red and says why; the lights keep the
      plain opacity. Ctrl+Z puts it back.

## 12. Stream Deck and the remote page

- [ ] The header shows a blue **Remote :8746** chip. Click it: a browser page opens with a button for every input.
      Tap a few: they go to Base.
- [ ] Editor → Input tab → **Copy Stream Deck URLs**. In the Stream Deck app add a **Website** action, paste the
      "Send to Base" URL, tick **GET request in background**. Press the key: that input goes to Base.
- [ ] Minimise CentraLight and press the key again: it still works. Open Settings (gear) and press it: still works.
- [ ] Another key with `http://127.0.0.1:8746/api/var/speed?value=3`: the breathing in step 11 speeds up.
- [ ] (Optional) A key with `http://127.0.0.1:8746/api/blackout/toggle`.

## 13. A hotkey

- [ ] Editor → Warm white → Input tab → **Hotkey** → press **F6**. Back on the Dashboard press F6: Warm white goes to Base.

## 14. An automation

- [ ] **Automation** tab → **+ New automation**. It starts as "Sun, 09:00–12:00". Change the **Days of the week**
      to today, and the **Time of day** to start 2 minutes from now and end an hour later.
- [ ] **+ Add an action ▾ → Send an input to Base** → pick Worship blue. Add **Festoon or key lights** → Key lights 40 %.
- [ ] Wait: at the start time Worship blue goes to Base and the key lights go to 40 %. The **History** (bottom right)
      says it ran; **Coming up** lists it for next week.
- [ ] Click the green **Automations on** chip at the top: it turns amber, **Automations paused**. Click it again to resume.

## 15. Always running: the tray, a second launch, sleep

- [ ] Close the window (✕): CentraLight keeps running in the tray by the clock (a message says so the first time);
      the lights carry on. Double-click the tray icon: the window comes back.
- [ ] With the window closed, double-click `run.bat` again: the running CentraLight shows its window (no second copy).
- [ ] Put the PC to sleep for a minute and wake it: the output chip goes green again and the lights carry on.
- [ ] (Optional) Sign out and back in: CentraLight starts in the tray (you switched that on in step 1b) and the
      lights come back as they were. Untick **Start CentraLight when I sign in to Windows** in Settings to stop that.
- [ ] Quitting is now in the tray icon's menu (right-click → **Quit CentraLight…**); it asks first.

## 16. Unplug and replug the Eurolite (while it's running)

- [ ] Pull the USB cable: the output chip goes red within a couple of seconds.
- [ ] Plug it back in: within about 2 seconds the chip is green again and the lights carry on.

## 17. Restart Media Background Pane

- [ ] Quit Media Background Pane: the lights hold their colours; the NDI chip goes amber.
- [ ] Start it again: within a few seconds the lights follow it again.

## 18. Quitting and starting again

- [ ] Tray icon → **Quit CentraLight…** → Yes → **Leave lights as they are**: the lights stay exactly as they were.
- [ ] `.\run.bat` again: the lights don't flicker or change when the app opens, overlays included.
- [ ] Tray icon → **Quit CentraLight…** → Yes → **Turn lights off**: everything fades to black.
- [ ] `.\run.bat` again: it opens blacked out (**Lights off — click to restore**); one click brings it all back.

## 19. If the lights behave oddly

The app sends only the first 76 DMX channels (up to the last SlimPAR), which is faster. If the lights flicker,
miss changes or do anything strange: gear → Output → tick **Send all 512 channels** → OK, and try again.
Please note whether that made a difference.

## 20. The console script still works (the fallback)

Close the app first (it's holding the dongle), then:

```powershell
.\.venv\Scripts\python.exe ndi_pixel_mapper.py
```

- [ ] The lights follow the screen as before. Press **Esc** to stop.

## 21. Afterwards: the commits

Each finished milestone is already committed on the branch `claude/centralight-gui` (nothing on `main`, nothing
pushed). Your checkout is still on `main`, with the same files. To see the commits, and to make the branch the
checked-out one:

```powershell
git log --oneline claude/centralight-gui
```

```powershell
git switch claude/centralight-gui
```

Pushing it to GitHub, or merging it into `main`, is up to you (or ask Claude).
