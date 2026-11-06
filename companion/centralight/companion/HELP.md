## CentraLight

Works the church's lights from the Stream Deck through CentraLight, the lighting app. The buttons light up with what
is happening, the moment it happens.

**Connection**

- **CentraLight PC**: `127.0.0.1` when CentraLight runs on this PC (the usual way).
- **Port**: the one in CentraLight's Settings → Remote (8746 unless it was changed).
- **Key**: only from another computer, with "Allow other computers" ticked in Settings → Remote. Copy it from there.

The connection comes back by itself if CentraLight is closed and opened again.

**Actions**

- Inputs: send one to Base (its own transition, a Cut, or one chosen here), send a copy to the Overlay, clear its copies,
  clear the Overlay, and the Overlay's on / off switch.
- An input's Send to Overlay does what the input says in CentraLight (the Editor's Input tab): its copy stays until it's
  cleared, or, **Only while held**, it goes when the key is let go. For that, a key needs **Release (held inputs)** on
  its release step: the Overlay presets have it already. On an input whose copies stay, Release does nothing.
- Load a show; Cut or Fade to black (the colour lights, or everything with the Festoon and Key lights too).
- Master, the Festoon and Key lights (level, on / off, fade), and a show variable.
- Automations: run one now, turn one on or off, pause or resume them all; the countdown's "Do it now", "Wait" and
  "Skip".
- Console: Release all, Release some lights, and its chase's Tap, Next step, Pause / Play on and speed.

**Feedbacks**

An input on Base (coral) or on the Overlay (lime), a show loaded, the colour lights black, a countdown running,
automations paused, the Festoon or Key lights on, a show variable's value, the Console holding lights, and CentraLight
connected (invert it for a key that warns when it isn't). When CentraLight goes away the keys go dark and the variables
empty, so nothing looks live that isn't.

If the connection isn't green, its status says why: CentraLight not answering (is it running?), a wrong key, too many
connections, or a CentraLight too old to have the live connection (it needs updating). A connection that hasn't
opened within 5 seconds is tried again.

**Variables**

`$(centralight:show)`, `base`, `master`, `countdown`, `countdown_text`, `next` (the next timed automation),
`console_held`, `house_<light>` (the Festoon's and Key lights' levels) and `copies_<input>` (its copies on the Overlay).

**Presets**

A button for each input (to Base, and to the Overlay: as the input says, with Release on the key's release), each
show, the four Cut / Fade to black buttons, the Console's Release all, Tap and Next step, and Pause automations. The
input and show buttons appear once CentraLight is connected. (The "flash while held" buttons went: set the input to
Only while held in CentraLight instead. Keys made from them before still work.)
