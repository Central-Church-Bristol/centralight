"""Commands for the engine.

The screen, the Stream Deck API and the editor never change the lights directly: they
send one of these small, read-only records, and the engine applies it at the start of
its next tick (1/40 s later). Many slider moves in one tick simply leave the last one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import Input, Show, Transition

MASTER_SLEW = 0.08  # seconds: Master moves are smoothed over this long
HOUSE_SLEW = 0.08  # seconds: festoon / key-light fader moves are smoothed over this long
HOUSE_SWITCH_FADE = 0.5  # seconds: the on/off switches fade over this long
BLACK_FADE = 2.0  # seconds: Fade to black's time to start with (Settings changes it)
QUIT_FADE = 0.5  # seconds: "Turn lights off" fades everything to black this fast, then quits


@dataclass(frozen=True)
class SetMaster:
    """Master level for the colour lights, 0-1."""

    level: float
    fade: float = MASTER_SLEW


@dataclass(frozen=True)
class SetHouse:
    """Change a house light (festoon, key lights): its fader level and/or its switch."""

    fixture: str
    level: float | None = None  # new fader level 0-1, or None to leave it
    on: bool | None = None  # switch on / off, or None to leave it
    toggle: bool = False  # flip the switch
    fade: float | None = None  # seconds; None = HOUSE_SLEW for levels, HOUSE_SWITCH_FADE for the switch


@dataclass(frozen=True)
class ToBlack:
    """Cut (seconds 0) or fade the colour lights to black: Base ends up with nothing playing and the overlays
    cleared, from the mix as it is. everything: the Festoon and Key lights too (their switches go off, their
    levels stay). A one-off action, not a latch: the next input sent lights things again."""

    seconds: float = 0.0
    everything: bool = False


@dataclass(frozen=True)
class SwapShow:
    """A new show snapshot (after an edit in the app or on disk)."""

    show: Show


@dataclass(frozen=True)
class LoadShow:
    """Load another show (the window has read it from disk). The lights freeze on what they're showing: Base and the
    overlays become one still look on Base, held until an input of the new show is sent (it transitions from it).
    Nothing of the new show starts by itself; its automations are checked straight away ("When this show loads").
    automation: a global automation asked for it (otherwise a person did: the picker, the Stream Deck)."""

    show: Show
    automation: bool = False


@dataclass(frozen=True)
class RequestShow:
    """Ask the window to load a show (the Stream Deck, the phone page): the window reads it from disk and loads it."""

    folder: str
    who: str = "Stream Deck"


@dataclass(frozen=True)
class ShowLoadFailed:
    """The window couldn't load the show a global automation asked for (wanted: as the automation named it), or it's
    loaded already: the loaded show's automations carry on."""

    wanted: str = ""


@dataclass(frozen=True)
class ShowLoadWaiting:
    """The window has a global automation's request for a show, and loads it within this many seconds (it waits for
    a dialog that's open to close, say): the loaded show's automations wait for it that long."""

    wanted: str = ""
    seconds: float = 40.0


@dataclass(frozen=True)
class ForgetShow:
    """A show was deleted: what the engine remembers about it (its variables) goes too, so a show made later in the
    same folder starts afresh."""

    folder: str = ""


@dataclass(frozen=True)
class SetGlobalAutomations:
    """The global automations changed (in the app, or their files outside it): a Show holding just them, in order."""

    automations: Show


@dataclass(frozen=True)
class SendToBase:
    """Put an input on Base with its own transition (cut=True: switch at once; transition:
    use this one instead, e.g. from the Stream Deck's ?transition=fade&duration=2)."""

    input_id: str
    cut: bool = False
    transition: Transition | None = None


@dataclass(frozen=True)
class ClearBase:
    """Take Base to black."""

    cut: bool = False


@dataclass(frozen=True)
class SetIdentify:
    """Identify mode: every colour light shows its own identify colour (seed picks the colours)."""

    on: bool
    seed: int | None = None


@dataclass(frozen=True)
class FlashLights:
    """Flash these lights white three times, so you can see which is which."""

    fixtures: tuple[str, ...]


@dataclass(frozen=True)
class SetTestColour:
    """Test colours: colour (r, g, b 0-1) on these lights (empty = all); colour None stops testing."""

    colour: tuple[float, float, float] | None
    fixtures: tuple[str, ...] = ()


@dataclass(frozen=True)
class SendToOverlay:
    """Send an input to the Overlay (M14): a new copy on top of what's there, appearing with the input's transition.
    An input has at most 4 copies there at once (a send beyond that is ignored). tag: names the copy, so
    ClearFromOverlay(tag=...) clears just that one (a held Send to Overlay button, let go)."""

    input_id: str
    tag: str = ""


@dataclass(frozen=True)
class ClearFromOverlay:
    """Clear copies from the Overlay, each leaving the way it came in: one copy (copy: its number), the copy a tag
    sent, or all of an input's copies (input_id). cut: at once."""

    input_id: str = ""
    copy: int = 0
    tag: str = ""
    cut: bool = False


@dataclass(frozen=True)
class ClearOverlay:
    """Clear every copy from the Overlay (Clear all), each the way it came in (cut: at once)."""

    cut: bool = False


@dataclass(frozen=True)
class SetOverlayOn:
    """The Overlay's switch: hide it without emptying it (False), show it again (True), or flip it (None)."""

    on: bool | None = None


@dataclass(frozen=True)
class SetOverlayOpacity:
    level: float
    fade: float = MASTER_SLEW


@dataclass(frozen=True)
class SetVariable:
    """Set a show variable. value: a number, a colour ("#FF6F59", "FF6F59" or (r, g, b)), or for a
    switch on / off / toggle (or true / false). Unknown names and values that don't fit are ignored."""

    name: str
    value: Any


@dataclass(frozen=True)
class SetAutomationsPaused:
    """Pause or resume all automations (None = flip)."""

    paused: bool | None = None


@dataclass(frozen=True)
class RunAutomation:
    """Run an automation's actions now, whatever its conditions (the Run now button). scope: "show" (the loaded
    show's automations) or "global"."""

    automation_id: str
    scope: str = "show"


@dataclass(frozen=True)
class ResetAutomation:
    """Let an automation the loop guard paused run again."""

    automation_id: str
    scope: str = "show"


@dataclass(frozen=True)
class SetAutomationEnabled:
    """Turn an automation on or off (the window saves it; from the Stream Deck)."""

    automation_id: str
    enabled: bool
    scope: str = "show"


@dataclass(frozen=True)
class SwitchLayout:
    """Switch to a saved layout (the window does it, as it changes the show; from the Stream Deck)."""

    layout_id: str


@dataclass(frozen=True)
class PowerEvent:
    """The PC is going to sleep ("sleeping") or has woken up ("woke")."""

    kind: str


@dataclass(frozen=True)
class SetSolo:
    """Show only this input on the lights (the Editor's "Show on lights"); None ends it. input: an
    input that isn't in the show (yet), e.g. one an AI just wrote, to try on the lights."""

    input_id: str | None
    input: Input | None = None


@dataclass(frozen=True)
class SetCountdownLength:
    """Seconds of countdown before an automatic show load or Cut / Fade to black (Settings; 0 = none)."""

    seconds: float


@dataclass(frozen=True)
class SetDefaultShow:
    """The default show (Settings, M14): its id, "" for Last loaded (nothing comes back), or "_none" for None (lights
    off: the show closes). It loads when the global automation that loaded a show stops matching, unless a person has
    loaded another since, or that automation's "when it stops matching" loads one. fade: with None, how long the lights
    take to fade off before the show closes (Settings' Fade to black)."""

    show: str = ""
    fade: float = BLACK_FADE


@dataclass(frozen=True)
class CloseShow:
    """No show from now on (M14, the show picker's None (lights off)): the lights fade off over `seconds` (the Festoon
    and Key lights too), then the window closes the show. A show loaded meanwhile stops it."""

    seconds: float = BLACK_FADE
    who: str = "Show menu"


@dataclass(frozen=True)
class CountdownAction:
    """The countdown banner's buttons (and /api/countdown/...): "now" (Load now), "wait" (Wait 10 min) or "skip".
    countdown_id "" = the one the banner shows."""

    what: str
    countdown_id: str = ""


@dataclass(frozen=True)
class QuietAutomations:
    """These show automations were just imported: at the next check, what their conditions say is taken as it is
    (as when CentraLight starts again), so one that's true already doesn't run until it next comes true."""

    automation_ids: tuple[str, ...]


Command = (
    SetMaster
    | SetHouse
    | ToBlack
    | SwapShow
    | LoadShow
    | RequestShow
    | ShowLoadFailed
    | ShowLoadWaiting
    | ForgetShow
    | SetGlobalAutomations
    | SendToBase
    | ClearBase
    | SetIdentify
    | FlashLights
    | SetTestColour
    | SendToOverlay
    | ClearFromOverlay
    | ClearOverlay
    | SetOverlayOn
    | SetOverlayOpacity
    | SetSolo
    | SetVariable
    | SetAutomationsPaused
    | RunAutomation
    | ResetAutomation
    | SetAutomationEnabled
    | SwitchLayout
    | PowerEvent
    | SetCountdownLength
    | CountdownAction
    | QuietAutomations
    | CloseShow
)
