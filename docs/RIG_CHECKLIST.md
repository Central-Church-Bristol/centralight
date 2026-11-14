# The rig checklist: trying CentraLight on the real lights

(First used on Wednesday 4 Nov, 3pm; each change since added its own steps below.)

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
- [ ] Nothing else is using the Eurolite: close MagicQ, QLC+, and any of the old console scripts' windows (the
      pixel mapper or the dongle tests, if the main folder still has them).
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
      festoon and key lights off. (If the PC started less than 10 minutes ago, everything starts black instead: that's
      the "the PC has just started" rule. Send Follow screen from the Dashboard.)
- [ ] The header says **Show: Central Church ▾** after the tabs: that's the loaded show (step 14f).

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
- [ ] **Area**: Editor → Follow screen → click its NDI element. **Area** says 25 %: about the patch each light
      followed before, so the lights look as they did. The Selected view (left) shows each light's patch as a
      dashed square.
  - [ ] Drag Area to **0 %**: each light shows exactly the colour under its dot (busier, more varied).
  - [ ] **50 %**: calmer; the lights get closer to each other in colour.
  - [ ] **100 %**: every light shows the screen's average colour (what `ndi_pixel_mapper.py` did on Sunday).
  - [ ] Set it to what looks best in the room and note the value.
- [ ] **Colour boost**: with something busy and colourful on the screen, drag **Boost** to about 50 %: the lights'
      colours get richer, with the same hues. Note what looks best (0 % is as before).
- [ ] **Smoothing**: under **Smoothing**, set **Brighter** to about 0.3 s and **Darker** to about 0.6 s, and play
      something that moves: the lights glide instead of jumping, and fade down more slowly than up.
  - [ ] Change slide (a cut from one picture to another): with **On cuts: Snap**, the lights change at once.
  - [ ] Set **On cuts** to **Glide** and change slide again: now they glide across the change.
  - [ ] Note the times that look best (0 s is as before).

## 3. Festoon, key lights, Master

- [ ] Drag **Festoon** to about 50 %: the festoon comes on (dragging turns it on) and follows the fader.
- [ ] Click the festoon's switch off and on: it fades over half a second.
- [ ] Same for **Key lights**.
- [ ] Drag **Master** to 50 %: the SlimPARs dim; the festoon and key lights don't change.

## 4. Cut to black and Fade to black

They're one-off buttons, not a switch: the next input you send lights things again.

- [ ] With an input on Base and the Key lights on, click **Cut to black**: the colour lights go black at once; the
      Key lights stay on.
- [ ] Send an input again, then click **Fade to black**: the colour lights fade to black over 2 s.
- [ ] Press and **hold** **Fade to black**: it fills up and says **Fade everything**; when it's full, everything fades
      to black, the Festoon and Key lights too. Their switches go off; their faders keep their levels.
- [ ] Switch the Key lights back on: they fade back up over the same 2 s. (After a held **Cut to black**, they come
      back at once.) Switch them off and on again: now the usual half-second fade.
- [ ] Shift-click **Cut to black**: everything at once, the same as holding it.
- [ ] The time Fade to black takes is in Settings (gear) → **Cut / Fade to black**: note what you like.
- [ ] Click the **padlock**: the faders and both black buttons stop responding (a blue banner says so). Click it
      again.

## 5. Layout and Identify (find out where each light is)

- [ ] **Layout** tab → switch on **Identify**: every SlimPAR shows a different colour, matching the dots and
      the list on the right.
- [ ] Drag each dot to where that light is in the room (the map is the shape of the screen). A light is just its
      dot: how much of a picture it averages is the picture element's **Area** (step 2).
- [ ] Click a dot: that light flashes white three times.
- [ ] Type where each light is in the **Where** column (e.g. "front left, above the drums"). It's saved to
      `fixture_map.csv` (the same file, in the same format, the old patch test used).
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
click the transition in the **Transitions** list, go back to the Dashboard, click **Send to Base** on Warm white,
then on Worship blue (since M14, clicking the tile itself does nothing).

- [ ] Each one looks right on the lights (note any that look odd): Cut is instant; Fade cross-fades; Dip goes
      through black; Wipe sweeps across (Direction in the Input tab turns it); Iris grows from the middle;
      Random changes light by light.
- [ ] Right-click a tile → **Cut to Base**: it cuts straight there, whatever its transition.

## 9. The Overlay: "Pillars purple" over Follow screen

- [ ] Put **Follow screen** on Base.
- [ ] New input → **Paint**, purple (`#9B30FF`). In its **Lights** list untick everything except the pillar
      lights (SlimPAR 106 and 107 in the standard patch). Name it **Pillars purple**.
- [ ] Dashboard: click **Send to Overlay** on its tile: the pillars fade to purple while every other light keeps
      following the screen. A red box appears under the tile's buttons, and the Layers panel's **Overlay** row
      says "1 copy", with **Clear all**, a switch and an **Opacity** fader.
- [ ] Drag the Overlay's opacity to 50 %: the pillars go half-way between purple and the screen colour.
- [ ] Click the red box (or **Clear all**): it fades away, the way it came in.

## 10. House lights set by an input

- [ ] Open Warm white → Input tab → **House lights** → tick **Set** for Key lights, 60 %.
- [ ] Send Worship blue, then Warm white: the key lights fade up to 60 % with the input.
- [ ] Move the Key lights fader in the header: your move wins until the next input is sent.

## 11. Variables and expressions

- [ ] Editor → open Worship blue → **Show variables** tab → **+ Add a show variable ▾ → Number**. Rename it
      **speed**, range 0 to 4, starts at 1, tick **Show on the dashboard**. (They were called "global variables";
      each show has its own now.)
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
- [ ] (Optional) Keys with `http://127.0.0.1:8746/api/black/fade` (the colour lights, over the time set in Settings) and
      `http://127.0.0.1:8746/api/black/cut?all=1` (everything, at once).

## 13. A hotkey

- [ ] Editor → Warm white → Input tab → **Hotkey** → press **F6**. Back on the Dashboard press F6: Warm white goes to Base.

## 14. An automation

- [ ] **Automation** tab → **+ New automation**. It starts as "Sun, 09:00–12:00". Change the **Days of the week**
      to today, and the **Time of day** to start 2 minutes from now and end an hour later.
- [ ] **+ Add an action ▾ → Send an input to Base** → pick Worship blue. Add **Festoon or key lights** → Key lights 40 %.
- [ ] Wait: at the start time Worship blue goes to Base and the key lights go to 40 %. The **History** (bottom right)
      says it ran; **Coming up** lists it for next week.
- [ ] Click the green **Automations on** chip at the top: it turns amber, **Automations paused**. Click it again to resume.
- [ ] While that automation's time range is still going, send a different input to Base by hand, then quit (tray →
      **Quit CentraLight…** → **Leave lights as they are**) and start CentraLight again: your input stays on Base. The
      automation doesn't run a second time (it only runs again at its next occurrence, e.g. next week).
- [ ] (Optional) **+ Add a condition → Sunrise / sunset**: the row shows today's sunrise and sunset in Bristol. Check
      they look right (e.g. against the weather app), then delete the condition.

## 14b. Inputs made by AI

- [ ] Dashboard → the **+ New input** tile → **Create with AI**. Describe a look, e.g. "the pillars breathing
      purple, slowly, and leave the other lights alone". Click **Copy prompt**.
- [ ] Open any AI chat website (ChatGPT, Claude, Gemini, Copilot…), paste, send, and copy its whole reply.
- [ ] Back in CentraLight: **Paste from clipboard**. It's checked straight away and the preview shows the lights
      moving. Click **Try it on the lights**: the real lights show it (and stop when you close the window).
      Click **Add**: a new tile appears with a small sparkle by its name.
- [ ] Send it to the Overlay (**Send to Overlay** on its tile) over Follow screen: the pillars breathe over the
      screen's colours.
- [ ] If CentraLight lists problems instead, click **Copy error for the AI**, paste that into the chat, and paste
      the AI's new reply. Note what went wrong (it helps improve the prompt).
- [ ] The sparkle beside a tile's name → **Edit with AI** → "make it a bit faster" → Copy prompt → AI →
      paste. It lists what changes. **Replace**. Then (Editor, that input) Ctrl+Z puts the old version back.
- [ ] Copy an AI reply again, click an empty part of the Dashboard and press **Ctrl+V**: the same window opens with it.
- [ ] (Optional) Claude Code or Cursor: Settings (gear) → AI → **Open show folder**, and start Claude Code (or
      Cursor) in that folder. Ask it: "make an input where the top lights sweep blue from left to right". Within a
      second or two of it saving the file, the tile appears by itself. Ask it to change that input: the change
      shows straight away, and Ctrl+Z (in the Editor, on that input) puts the old version back.

## 14c. Timed sequences, keyframes and video

- [ ] Editor → open **Warm white** → untick **Infinite** (above the timeline). It's now a timed input (10 s). In the
      Input tab set **Length** to 12 s and **At the end: Loop**.
- [ ] Drag **Paint** from the palette onto Track 2 at about 4 s. Drag the bar to move it, drag its ends to trim it,
      right-click → **Split at the playhead**. Ctrl+Z undoes each.
- [ ] Right-click a bar → **In transition → Fade**, and **Out transition → Wipe**: wedges show at its ends. Make two
      bars on one track overlap: an X shows where one crosses into the other.
- [ ] Pause (Space), click the ruler at 2 s, click the ◆ next to **Opacity**; click the ruler at 6 s and set Opacity
      to 20 %. Play: it fades between them. Drag a diamond on the bar to retime it.
- [ ] **Send to Base**: the lights play the sequence and loop it; the tile shows a progress bar; the Editor's Result
      view matches the lights. **Send to Base** again: it starts again.
- [ ] Set **At the end: Stop** and send it: at the end it leaves Base.
- [ ] **Video**: drag Video onto a track, **Choose…** a short video (CentraLight links to it and plays its own copy),
      send it to Base: the lights follow the video. Try **Speed**, **Area** and **Smoothing**. While it plays, glance
      at Task Manager: CentraLight should stay small (note the CPU %).

## 14d. Painting and masks

- [ ] Editor → open **Warm white** → click its Paint element. In the Selected view pick the **Brush**, choose a
      colour and size under the tools, and paint across the top lights: the lights there take the colour as soon
      as you let go (if Warm white is on Base). Ctrl+Z takes the stroke away; Ctrl+Shift+Z puts it back.
- [ ] Try the **Eraser**, the **Bucket** (click an empty area), the **Gradient** (drag across) and **Light colour**
      (click a light's dot: just that light changes).
- [ ] **Mask** (top of the Selected view) → the **Brush**, paint over half the picture: that half of the element is
      gone (red in the view). The **Eraser** paints it back. (Since M14 Mask mode has the same tools as Layer mode.)
- [ ] Click Follow screen's NDI element → **Select**: drag the picture, drag a corner to make it smaller, drag the
      knob to turn it. The lights follow. Ctrl+Z puts it back.

## 14e. Sound

- [ ] Settings (gear) → **Audio** → Listen to **This PC's output** → OK. The header shows **Audio**. Play some music
      (or a video in ProPresenter): Settings → Audio's meter moves.
- [ ] Editor → New input → Paint element → **=** next to **Opacity** → `0.15 + 0.85 * audio.bass` → **Use it**. Send
      it to Base: the lights pulse with the bass. Try `hsv(audio.pitch)` on the colour, and `audio.beat` on opacity.
- [ ] If it's too twitchy or too slow, change **Rise** / **Fall** in Settings → Audio and note what you prefer.
- [ ] (Optional) If there's a feed from the mixer into the PC, pick it in Settings → Audio instead and try again.
- [ ] Note the CPU in Task Manager while it listens.

## 14f. Shows and global automations

A show is everything above (inputs, show variables, automations, the lights and layouts, media), in its own folder
in `Documents\CentraLight Shows`. Changes save by themselves: there's no Save.

- [ ] Send Follow screen (or anything colourful) to Base and put an input in an overlay.
- [ ] **Show: Central Church ▾** → **Duplicate “Central Church”…** → call it **Evening** → OK. It says where the copy
      is; it doesn't open by itself. **Show: Central Church ▾** → **Evening**: the header says **Show: Evening**, and
      the lights don't change: they hold what they showed, overlay included. The Dashboard says **Base: frozen (from
      Central Church)**; the overlays are off; Festoon, Key lights and Master are as they were.
- [ ] Send any input: it comes in from the held colours with its own transition.
- [ ] **Show: Evening ▾** → **Central Church**: the lights hold again until you send something. In each show, Ctrl+Z
      only undoes that show's changes.
- [ ] (Optional) **Show ▾ → Rename “Evening”…**: the name changes; its folder (and its Stream Deck id `evening`) stays.
- [ ] Layout tab → **Layout: Main ▾** → **Import from another show…** → pick the other show and its **Main** layout:
      the summary says what would change (nothing: the same lights) → **Import**. A new layout "Main (from …)" is in
      use. Ctrl+Z on the Layout tab undoes it.
- [ ] Automation tab: two lists, **Global automations** (app-wide) and **This show (…)**. **+ New global
      automation** → **Days of the week**: today; **Time of day**: from 2 minutes from now, for 10 minutes → **+ Add
      an action ▾ → Load a show** → the other show; **+ Add an action ▾ → Festoon or key lights** → Key lights 40 %.
- [ ] In the show it will load, **+ New automation** → remove its two conditions, **+ Add a condition ▾ → When this
      show loads** → **+ Add an action ▾ → Send an input to Base** → Warm white.
- [ ] Switch to the first show and wait: 15 seconds before the time a banner counts down (**Loading … in 0:15**,
      see 14g); at the time, the other show loads by itself (the lights hold for a moment), Warm white comes in (its
      "When this show loads") and the key lights go to 40 %. **History** shows both, the global one marked
      "Global:". **Coming up** lists the global one, marked too.
- [ ] (Optional) Set the global automation's time again, and when it comes, have a dialog open (**Show ▾ →
      Rename…**, say): the show waits for you to close it. Leave it 30 s and CentraLight closes it for you (as if
      you'd pressed Cancel), then loads the show.
- [ ] The **Remote** chip's page (or a phone): the list next to the show's name switches shows; the page then shows
      that show's inputs. A Stream Deck key with `http://127.0.0.1:8746/api/show/central-church` loads Central Church.
- [ ] (Optional) **Show ▾ → Delete a show → Evening…** → Yes: its folder goes to the Recycle Bin (you can put it back
      from there). The loaded show can't be deleted. Delete the test automations afterwards.

## 14g. Show extras (M13): the countdown, the schedule, importing, .clshow files, notes, linked videos

- [ ] **The countdown** (the rig test the plan asks for): Automation tab → **+ New automation** (this show's) →
      **Time of day**: from 2 minutes from now, for 5 minutes → **+ Add an action ▾ → Fade to black**. Send something
      colourful to Base and wait. 15 seconds before the time a banner comes up under the header: **Fading to black in
      0:15 · Do it now · Wait 10 min · Skip**, counting down, and the lights fade **at** the time, not 15 s later.
      (The phone page shows the same banner.)
- [ ] Set its time again and try the buttons when the banner shows: **Skip** (nothing happens this time; it runs
      as normal next time), **Wait 10 min** (the banner goes, and comes back 10 minutes later), **Do it now**.
      Settings → Shows and automations → **Countdown timer** changes the 15 s (None = none). **Run now** never counts down. Set its time once more
      and, while the banner counts, switch the automation off (its tick in the list): the banner goes, and nothing
      fades. Delete it afterwards.
- [ ] **Week schedule**: Automation tab → the **Week schedule** tab (beside **Automation**): the global automation
      that loads a show (from 14f) is a coloured block on today. ‹ › move a week; clicking the block opens it.
- [ ] **Import from another show**: Dashboard → the **+ New input** tile → **From another show…** → pick the other
      show, tick an input: the summary lists what changes (lights it uses that this show lacks, show variables added,
      Replace or Keep both if it's already here) → **Import**. Nothing is sent to the lights; Ctrl+Z undoes it. The
      Automation tab's **From another show…** does the same for automations (and the inputs they send).
- [ ] **Show notes**: **Show ▾ → Notes about “…”…** → type two lines → Save. They show on the Dashboard (a small
      panel at the top right: click its title to fold it), under the show's name in the Show menu, and on the phone
      page.
- [ ] **.clshow files**: **Show ▾ → Export “…”…** (try ticking **Leave out pictures and videos** once) → save it on
      the Desktop. Explorer shows it with CentraLight's document icon. Double-click it: CentraLight comes forward and
      asks **Import “…” as a new show?** → Keep both → **Import**: a new show "… (2)" is in the Show menu, not loaded.
      Delete it afterwards (**Show ▾ → Delete a show**).
- [ ] **A video linked to its original** (the second rig test the plan asks for): Editor → a Video element →
      **Choose…** a video in your Google Drive folder. The File row shows the original's path; CentraLight plays its
      own copy. Send the input to Base. Now save a different video over the original in Google Drive (same name):
      within 5 minutes (or click the element again) the File row says **Update ready: used when it's not playing ·
      Update now**, and the lights carry on with the old one. Take the input off Base (or press **Update now**): the
      new video plays from then on. Rename the original in Drive: the File row shows a warning, **Original not
      found**, and the video still plays.

## 15. Always running: the tray, a second launch, sleep

- [ ] Close the window (✕): CentraLight keeps running in the tray by the clock (a message says so the first time);
      the lights carry on. Double-click the tray icon: the window comes back.
- [ ] With the window closed, double-click `run.bat` again: the running CentraLight shows its window (no second copy).
- [ ] Put the PC to sleep for a minute and wake it: the output chip goes green again and the lights carry on.
- [ ] (Optional) Sign out and back in: CentraLight starts in the tray (you switched that on in step 1b). Windows
      starting it counts as the PC starting: the last show loads with every light black (house lights off, their
      levels kept) and nothing playing, then the automations run (global ones first; the show's "When this show loads"
      too). Untick **Start CentraLight when I sign in to Windows** in Settings to stop it starting by itself.
- [ ] Quitting is now in the tray icon's menu (right-click → **Quit CentraLight…**); it asks first.

## 15b. The launcher and Diagnostics

- [ ] Quit CentraLight (tray → Quit), then double-click **CentraLight.exe** in the CentraLight folder: it starts
      without a black console window, with the logo on the taskbar. If you like it, right-click it → Send to →
      Desktop (create shortcut).
- [ ] Ctrl+Shift+D (or the tray icon's menu → Diagnostics): the engine runs at about 40 ticks a second, the output
      shows the Eurolite sending, and CentraLight's CPU: up to about 12 % of one core (under 1 % of the PC) while
      it follows Media Background Pane. Please note the figure, with ProPresenter and Media Background Pane running.
      Click **Copy** and paste it
      somewhere: it's all there as text.
- [ ] Its **On time** line: the engine's ticks start about 1 ms late, and none (or almost none) over 5 ms late,
      even with the Editor playing a video. That's the lights coming first; the pictures on screen give way.

## 16. Unplug and replug the Eurolite (while it's running)

- [ ] Pull the USB cable: the output chip goes red within a couple of seconds.
- [ ] Plug it back in: within about 2 seconds the chip is green again and the lights carry on.

## 17. Restart Media Background Pane

- [ ] Quit Media Background Pane: the lights hold their colours; the NDI chip goes amber.
- [ ] Start it again: within a few seconds the lights follow it again.

## 18. Quitting and starting again

- [ ] Tray icon → **Quit CentraLight…** → Yes → **Leave lights as they are**: the lights stay exactly as they were.
- [ ] `.\run.bat` again: the lights don't flicker or change when the app opens, overlays included. (Reopening
      carries on as it was: the same show, and its "When this show loads" automations don't run again.)
- [ ] Tray icon → **Quit CentraLight…** → Yes → **Turn lights off**: everything fades to black.
- [ ] `.\run.bat` again: it opens with everything black and nothing on Base. Send an input: the lights come back.
      (Automations leave the lights off after a "Turn lights off" quit until one of their time ranges begins after
      the quit, e.g. Sunday 09:00–12:30 after a quit on Saturday, so an unattended Sunday morning isn't left dark.
      "CentraLight starts", a feed coming back or a whole day such as "Sundays" doesn't light them. Anything you
      send yourself lights them at once.)
- [ ] Optional, to see that happen: Automation tab → **+ New automation** → change **Days of the week** to today and
      **Time of day** to start two minutes from now, for five minutes → **+ Add an action ▾ → Send an input to Base**:
      Warm white. Straight away, quit with **Turn lights off** and start again: when the time range begins, the
      lights come back by themselves. Delete the automation afterwards.

## 19. If the lights behave oddly

The app sends only the first 76 DMX channels (up to the last SlimPAR), which is faster. If the lights flicker,
miss changes or do anything strange: gear → Output → tick **Send all 512 channels** → OK, and try again.
Please note whether that made a difference.

## 20. If CentraLight misbehaves: the fallbacks

The old console script (`ndi_pixel_mapper.py`) went with the clean-up (M17), so the fallbacks are:
- **MagicQ**, as before CentraLight. Quit CentraLight first (tray icon → **Quit**): only one program can use the
  dongle at a time.
- **The version you tried on 4 Nov:** updating to this one keeps a copy of it and of your shows. Ask Claude to
  "switch back".

(The old script is still in git's history, if it's ever wanted.)

## 20b. M14: your changes of 4 Nov (try these on the lights next time)

- [ ] **Tick boxes**: the Editor's **Infinite** box (and every other empty tick box) has a grey border you can see;
      ticked ones are pale blue.
- [ ] **Rename**: right-click a tile → **Rename…**. And in the Editor, click the name in "Editing: …", type a new
      one, press Enter. Ctrl+Z puts the old name back.
- [ ] **The Editor opens paused**: open an input: the playhead stays at the start until you press play.
- [ ] **Delete a shape**: Editor → a Paint element with a rectangle → click the rectangle in the Selected view →
      press Delete: it goes (Ctrl+Z brings it back). With nothing selected, Delete does nothing there.
- [ ] **The Overlay** (one now, instead of Overlays 1–4):
  - [ ] Put Follow screen on Base, then click **Send to Overlay** on Pillars purple three times: three red boxes
        under its buttons, and the lights look the same as with one (they're the same input).
  - [ ] Send a different input that lights other lights to the Overlay too: both show, neither cancels the other.
  - [ ] Click the middle red box: that copy fades away the way it came in, and the boxes close up.
  - [ ] With 4 boxes, **Send to Overlay** greys out until you clear one.
  - [ ] **Hold** Send to Overlay: the pillars come on at once and go when you let go (a flash). A quick click stays.
  - [ ] A **timed** input sent to the Overlay: its box fills as it plays, and it leaves by itself at the end.
  - [ ] Click its box while it plays: it fades out from where it was (it doesn't jump back to its start).
  - [ ] An input with a **video**: Send to Overlay twice, a few seconds apart. Both copies play smoothly, each at
        its own moment, and the other lights don't stutter. Then tap Send to Overlay quickly, ten times: no stutter,
        and Diagnostics shows nothing unusual.
  - [ ] Delete an input that's on the Overlay (Ctrl+Z afterwards): its copies fade away with it.
  - [ ] The **Overlay** row's switch off: the Overlay hides but its boxes stay (dimmed); switch on: it's back.
        **Clear all** empties it.
  - [ ] Drag a tile to another place: a pale-blue line shows where it'll go. Ctrl+Z puts it back.
- [ ] **Colour element**: tonight's single-colour input looks the same as before (it's a Colour element now: open
      it in the Editor). New input → **Colour** (first in the list): the lights take its colour; try a gradient.
      Then **Paint** on the track above: nothing changes until you draw a rectangle on it.
- [ ] Your inputs with brush painting over a colour on track 1 (`new-input` in Central Church, `default` in
      Evening): open one and select that Paint element. It shows a **Background** group, and under **Split into
      Colour + Paint** a line saying whether it'll look exactly the same, or what would change. Split it: the new
      Colour element is on track 1 and the other elements have each moved up a track; the lights look the same
      (or change only as that line said). Ctrl+Z undoes it.
- [ ] **Masks** (black and white now): put Warm white on Base, open it, click its Colour element, then **Mask** at the
      top of the Selected view. The view shows the mask itself, white everywhere, with the light dots on top. The tools
      are Paint's (Select, Rectangle, Ellipse, Brush, Eraser, Bucket, Gradient, the eyedropper) and the Light bulb;
      hover each for what it does here. Beside them: **Grey**, **White**, **Black** and **Invert**.
  - [ ] **Rectangle**, drag over the left half: that part of the view goes black and the lights there go dark. The
        details panel lists it under **Mask shapes** ("Rectangle M1"), with its **Grey** at 0 %. Drag Grey to 50 %: the
        lights there come back half way.
  - [ ] **Brush** in black down the middle, then **White** and paint over part of it: dark where it's black, lit where
        it's white. **Eraser** rubs painting back to white.
  - [ ] **Bucket** in black on an empty mask: everything goes dark. Then paint in white: "only there".
  - [ ] **Gradient** in black from the left to the right: the lights fade across the room.
  - [ ] **Invert**: everything turns round (lit where it was dark). Invert again: back as it was. Ctrl+Z undoes it.
  - [ ] Drag a rectangle across slowly with **Select**: the lights change one by one as it passes (try Softness 0 % and
        60 %: switching vs fading).
  - [ ] Untick a light in **Lights**: it stays off whatever the mask says.
  - [ ] Your Evening input with a painted mask looks exactly as before.
- [ ] **Show variables, keep or reset**: Editor → Show variables → a number variable on the Dashboard → **When the show
      loads: Go back to its starting value**. Set it to something else on the Dashboard, load another show, then load
      this one again: it's back at its starting value. One left on **Keep its last value** keeps what you set.
- [ ] **Default show**: Settings → Shows and automations → **Default show: Central Church** → OK.
  - [ ] Automation tab → **Week schedule**: Central Church fills the gaps between the scheduled shows, lighter.
  - [ ] Make a global automation "Days: today, from now+1 min to now+3 min → Load a show: Evening". When it starts,
        Evening loads (after its countdown); when it ends, the countdown says "Loading Central Church" and it loads.
  - [ ] The same again, but load another show from the Show menu while Evening is on: when the automation ends,
        nothing changes (manual wins).
  - [ ] The same again, with **When it stops matching: Cut to black**: when it ends the lights go black and stay
        black (no countdown for Central Church, and Start Up doesn't light anything).
  - [ ] Restart the PC: Central Church opens. (Quitting and reopening CentraLight carries on with the show that was on.)
  - [ ] Restart the PC while a global automation that loads another show is under way (one for the next hour, say):
        Central Church opens, then that automation's show loads straight after.
- [ ] **No show (None (lights off))**: Settings → Shows and automations → **Default show**: it lists **Last loaded**,
      **None (lights off)** and your shows. Choose **None (lights off)** → OK.
  - [ ] Show ▾ → **None (lights off)**: the lights fade off (the Festoon and Key lights too), then every tab but
        Automation says **No show loaded**, with a Load button for each show, New show… and Import a show…, and a line
        about the global automations. Every light is off (nothing glows at all).
  - [ ] The header says **Show: None**; the Festoon, Key lights, Master and Cut / Fade to black are greyed out, and
        hovering one says "Load a show first". The padlock and Settings still work.
  - [ ] Automation tab: the global automations are there to edit; "This show" says no show is loaded. Week schedule:
        the gaps say "No show (lights off)", in grey.
  - [ ] The phone page says "No show loaded"; pick Central Church in its switcher: it loads (the lights come up from
        black, Start Up runs) and the page shows its inputs again.
  - [ ] Make a global automation "Days: today, from now+1 min to now+3 min → Load a show: Evening". When it starts,
        Evening loads; when it ends, the countdown says **Closing the show (lights off)**, then the lights fade off
        and every tab says No show loaded.
  - [ ] Quit and reopen CentraLight while no show is loaded: it carries on with no show. Restart the PC: no show
        opens (unless a schedule is under way: then its show loads).
  - [ ] Set **Default show** back to what you want (Last loaded is how it was before).
- [ ] **The colour picker updates live**: put Warm white on Base, open it in the Editor, click its Colour element, then
      the colour's swatch. Drag in the colour square: the lights follow as you drag.
  - [ ] **Cancel**: they go back. Again, then **OK**: they keep it, and one Ctrl+Z puts the old colour back.
  - [ ] Type a hex value in the colour window and press Enter: it's OK. Pick again and press Esc: it's Cancel.
  - [ ] Untick **Preview**: nothing changes until OK. Pick again: it's still unticked (after a PC restart it's ticked
        again).
  - [ ] An input that isn't on the lights: picking its colour changes nothing on the lights.
  - [ ] Dashboard → a colour show variable: the lights follow as you pick, and Cancel puts it back.
- [ ] **Which week of the month**: Automation tab → a global automation with Days of the week → tick **Wed**, then
      **1st** beside "weeks:". Its summary says "1st Wed". The **Week schedule** tab shows its block on the first
      Wednesday only: step on with the arrows (the first Wednesday of December is the 2nd). **Every week** puts it
      back as it was.

## 20c. M15: the Console page (try these on the lights)

- [ ] **Console** tab (after Dashboard). Click SlimPAR 101's dot: a blue ring with **1**. Ctrl-click 103: **2**. Drag a box
      round the top four from a gap: all four, numbered left to right. **None**, then **Top Wash**: the same four.
- [ ] **Colour** → pick a warm orange with Preview ticked: the four follow as you drag. **Cancel**: they go back. Again,
      **OK**: they keep it, with an amber dot on each on the map, and the header says **Console: 4 lights**.
- [ ] **Brightness** to 30 %: they dim (their own dimmer). Send another input on the Dashboard: the four stay orange,
      the others change.
- [ ] **+** saves the colour; click it later on other lights. **Save as group…** "Front": a **Front** button appears.
- [ ] **Fan → Rainbow** on **All**: a rainbow across the room, left to right. **Fade 2 s**, then a saved colour: they fade.
- [ ] **Release** (with two chosen): those two fade back to the input in 2 s. **Release all**: the rest do.
- [ ] **Advanced**: the channel faders. Choose one light, **Amber** up: warmer. **Strobe** a little: it flashes (read
      the note first). **Release**: strobe stops at once, the rest fades back.
- [ ] Hold some lights, then **Fade to black**: they fade with everything, and the header chip goes.
- [ ] Hold a light at 30 % brightness and **Release** it: it fades back smoothly, with no blink at the start or
      the end (if it blinks, tell Claude: the SlimPAR's own dimmer may smooth changes).
- [ ] Hold some lights, quit and reopen CentraLight: they come back as they were.
- [ ] The phone page: **Console · release all** while it holds lights. An automation **Release the Console** at a set
      time: the lights go back at that time.
- [ ] **Chase:** choose the Pillars, **Quick → Colours across the lights**, **Play**: they step through your saved
      colours, and the line above the steps says **On: Pillars**. **Tap** four times in time: the speed follows. **Crossfade** 100 %: they glide. **Pause**, **Next step**.
      Direction **Bounce**, then **Random**. **Release all**: they fade back.
- [ ] **Save as input:** with some lights held, **Save look as input…** "Front red" (read the note about amber and
      strobe). On the Dashboard, **Front red** is a new tile: send it, and only those lights change. Its tile's menu →
      **Load into Console**: the Console opens with those lights held and chosen. Make a chase, **Save chase as
      input…**, send that tile: it plays. Undo (Ctrl+Z) takes a saved input away again.
- [ ] Lock the padlock: the page can't be changed. Unlock.

## 20d. M16: the Stream Deck through Companion

- [ ] In Companion (Launch GUI): **Modules** → **Import module package** → `companion\centralight-1.0.0.tgz` in the
      CentraLight folder. **Connections** → add **CentraLight** → leave 127.0.0.1 and 8746 → Save. It goes green
      within a few seconds.
- [ ] **Presets** → **Inputs to Base**: drag Warm white and Worship blue onto two keys. Press each: the lights change,
      and the key of the input on Base turns coral.
- [ ] **Inputs to the Overlay**: each press adds a copy (lime, "+1", "+2"); **Flash while held**: on while the key is
      down, gone when you let go.
- [ ] **Black → Fade to black**: the key turns red while the colour lights are black. Send an input: it goes back.
- [ ] **Shows**: load another show from its key. That key turns sky blue, and the input keys' dropdowns list the new
      show's inputs.
- [ ] Hold some lights on the Console page: **Release all** turns amber with the count; press it: they fade back.
- [ ] Quit CentraLight: the connection goes red. Start it again: green within about 10 seconds, and the keys light up.

## 20e. The start-up fade (the fix of 6 Nov)

- [ ] Restart the PC, with **Start CentraLight when I sign in to Windows** ticked (step 1b), or start CentraLight by
      hand within 10 minutes of the PC starting. Nothing changes on the lights until the window appears (or the tray
      icon, when Windows starts it); then the start-up automations run, and Default's "Start Up" fades its input in
      smoothly: no stop part-way, no jump to full.

## 20f. M18, batch 1 (6 Nov, evening)

- [ ] Settings (the gear): every number field with up / down buttons shows clear arrows (Audio's **Rise** and **Fall**,
      **Fade to black takes**, **Countdown timer**…), and clicking them changes the number.
- [ ] The groups: **Cut / Fade to black** (just its time), **Shows and automations** (**Countdown timer**, **Default
      show**), **Console** (**Release takes**), **Starting and quitting** (**When you quit:**), **Files and
      diagnostics** (the .clshow tick). Change the Countdown timer, **OK**, open Settings again: it kept it.
- [ ] Automation tab: with enough automations to scroll (make a few if needed), scroll down and click one near the
      bottom: that one opens, and the list stays where it was. Try the show's list too.

## 20g. M18, batches 2–4 (6 Nov, night)

- [ ] **Overlay, per input:** Editor → an input's Input tab: **Send to Overlay** shows "Stays until cleared"; change
      it to "Only while held"; Ctrl+Z puts it back.
- [ ] Dashboard, a "Stays until cleared" input: hold **Send to Overlay** 2 seconds and let go: the copy stays (a red
      box); click the box to clear it.
- [ ] Dashboard, an "Only while held" input: hold **Send to Overlay**: the lights show it while held, and it goes when
      let go; a quick click flashes it; nothing stays.
- [ ] Companion: remove the old CentraLight module, import `companion\centralight-1.0.0.tgz`, and drag a held input's
      Overlay preset onto a key: holding the key shows it, letting go clears it; a "stays" input's key leaves its copy.
- [ ] Phone page: a held input's button reads "Overlay (hold)": hold it and it shows; let go and it goes.
- [ ] **Dates:** Automation tab → Global automations → "Dioscesioan Event" (look only, don't change it): the condition
      shows Day 7, Month Nov and Year 2026, all ticked with one value each, and the summary reads "7 Nov 2026".
- [ ] Coming up lists "Sat 07 Nov 00:00 Global: Dioscesioan Event", and the week schedule shows it all day on Saturday.
- [ ] In a test automation (not the Saturday one), add a Dates condition: it starts at today's date with Day, Month and
      Year ticked. Untick Year: the summary says "every <day> <month>". Click **to…** next to Day, type an end day
      (14) and press Enter: the summary shows the range. **From … to …** 24 Dec to 6 Jan: "24 Dec – 6 Jan". Then delete
      the test automation.
- [ ] **Shutdown:** Global automations: add one with **The PC shuts down or restarts** → **Fade to black**, 3 s,
      "Festoon and Key lights too" ticked. The note under the condition says it can't catch a power cut, the power
      button or a crash.
- [ ] With the lights on, Start → **Restart**: the lights fade to black (about 3 s) before Windows restarts. After the
      restart they come up as at any PC start (the default show or the schedule's show, and its "When this show loads").
- [ ] Add **The PC goes to sleep** → Fade to black, then Start → **Sleep**: the lights go black within about a second.
      Wake the PC: the "The PC wakes from sleep" automations run.
- [ ] Settings → Starting and quitting shows the line about fading the lights when Windows shuts down, restarts or
      sleeps.
- [ ] Tonight: shut the PC down without quitting CentraLight first. Saturday's PC start loads Central Church for the
      event, and its Start Up runs.

## 20h. The shutdown fade (fixed 6 Nov, late)

- [ ] With "On PC Shutdown" (The PC shuts down or restarts → Fade to black) on and the lights on, Start → **Shut down**:
      the lights fade before the PC goes off. After the next start, session.log has "Windows is shutting down,
      restarting or signing out", "Quit, as Windows logs off or shuts down" and "CentraLight closed".

## 20i. A new show starts with no inputs (6 Nov, late)

- [ ] Show ▾ → **New show…**: the new show's Dashboard shows only **+ New input** (no Follow screen). Load your show
      again afterwards, and delete the test show if you like.

## 20j. Short text, and Show variables (8 Nov)

- [ ] Editor → an input's Input tab: the Overlay panel shows **Overlay button** and its choice; hover it for the
      explanation. Settings: no paragraphs, just the Eurolite line; hover a setting for its explanation.
- [ ] Show ▾ → **Show variables…**: now a window of its own (20l).

## 20k. No blink at a PC start (8 Nov)

- [ ] With the lights on, quit CentraLight from the tray with **Leave the lights as they are**, then sign out and back
      in: Windows starts CentraLight, and the lights keep their look while it starts. When the schedule's show comes
      back they stay as they were (no blink). With nothing to load, they fade to black instead.

## 20l. The Show variables window (8 Nov)

- [ ] With no input open, Show ▾ → **Show variables…**: a window opens on top, the Dashboard still in sight. Tick **On
      the dashboard** for a variable: it appears in the Dashboard's Variables panel at once.
- [ ] The Dashboard's Variables **Edit** brings the same window forward. Move and resize it, close it and open it
      again: it comes back where you left it.

## 20m. The festoons and the NDI input (8 Nov)

With the festoons on, send "Follow Media Background" (the evening show) to Base and watch the festoons for a minute,
four times:
- [ ] Before the update, as now. Do they flicker?
- [ ] Before the update, with **Send all 512 channels** ticked (Settings → Output: where the DMX goes).
- [ ] After the update, with it unticked (30 packets a second, 76 channels).
- [ ] After the update, with it ticked (30 a second, all 512 channels). Leave it ticked if this one is steady.

## 20n. CentraLight in the taskbar (8 Nov, night)

- [ ] Start CentraLight from its pin: one button, not two. If there are still two, unpin it, start CentraLight, then
      right-click its button → **Pin to taskbar**.
- [ ] Close the window: the "still running" message says CentraLight. Task Manager lists it as CentraLight.
- [ ] Its tray icon may now be among the hidden icons (^): drag it back beside the clock if you want it there.

## 20o. The Betopper LPC018s (14 Nov)

- [ ] On each LPC018's menu: `7CH`, and its start channel: `A077`, `A084`, `A091`, `A098`, `A105`, `A112`.
- [ ] Layout → **Add a light**, then **Type** → **Betopper LPC018 (7 channels)**; its Address should match the light.
      Rename it, give it a group, drag its dot to where it is. Once per light.
- [ ] Click each new dot: that light flashes white three times.
- [ ] Follow screen on Base: the new lights follow the picture under their dots, like the SlimPARs.
- [ ] Console → Advanced, with an LPC018 chosen: no Amber; **Program** above 50 runs its own colour jump / fade /
      pulse / sound, and **Program speed** changes how fast. Release: it's back to the input's colour.

## 21. Afterwards: updating the main folder

Everything is committed on the branch `claude/centralight-gui` (nothing on `main`, nothing pushed). The work was
done in a separate copy of the code (`.claude\worktrees\m14`), which has the branch checked out. Your main folder
still runs the version you tried on 4 Nov. It's updated with your OK, as planned for Sunday morning:
1. Quit CentraLight (tray icon → **Quit**).
2. Your shows (`Documents\CentraLight Shows`) and settings (`%LOCALAPPDATA%\CentraLight`) are backed up first.
3. The main folder moves to the branch's newest commit. Then start CentraLight.
4. To go back, say "switch back": the old files and the backed-up shows come back.

Don't switch branches in the main folder by hand (`git symbolic-ref`, `git switch`) meanwhile: git would then have
the branch checked out twice. Pushing the branch to GitHub, or merging it into `main`, is up to you (or ask Claude).
