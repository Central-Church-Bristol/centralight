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
BLACKOUT_FADE = 0.5  # seconds: Blackout fades over this long (Shift-click = instant)


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
class SetBlackout:
    """Blackout on or off (None = flip it). instant skips the fade."""

    on: bool | None = None
    instant: bool = False


@dataclass(frozen=True)
class SwapShow:
    """A new show snapshot (after an edit in the app or on disk)."""

    show: Show


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
    """Send an input to Overlay 1-4. state None = the toggle rule (the same input again turns
    it off); "on" or "off" says exactly what to do."""

    slot: int
    input_id: str
    state: str | None = None


@dataclass(frozen=True)
class OverlayOff:
    slot: int


@dataclass(frozen=True)
class SetOverlayOpacity:
    slot: int
    level: float
    fade: float = MASTER_SLEW


@dataclass(frozen=True)
class SetVariable:
    """Set a global variable. value: a number, a colour ("#FF6F59", "FF6F59" or (r, g, b)), or for a
    switch on / off / toggle (or true / false). Unknown names and values that don't fit are ignored."""

    name: str
    value: Any


@dataclass(frozen=True)
class SetAutomationsPaused:
    """Pause or resume all automations (None = flip)."""

    paused: bool | None = None


@dataclass(frozen=True)
class RunAutomation:
    """Run an automation's actions now, whatever its conditions (the Run now button)."""

    automation_id: str


@dataclass(frozen=True)
class ResetAutomation:
    """Let an automation the loop guard paused run again."""

    automation_id: str


@dataclass(frozen=True)
class SetAutomationEnabled:
    """Turn an automation on or off (the window saves it; from the Stream Deck)."""

    automation_id: str
    enabled: bool


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


Command = (
    SetMaster
    | SetHouse
    | SetBlackout
    | SwapShow
    | SendToBase
    | ClearBase
    | SetIdentify
    | FlashLights
    | SetTestColour
    | SendToOverlay
    | OverlayOff
    | SetOverlayOpacity
    | SetSolo
    | SetVariable
    | SetAutomationsPaused
    | RunAutomation
    | ResetAutomation
    | SetAutomationEnabled
    | SwitchLayout
    | PowerEvent
)
