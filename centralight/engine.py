"""The engine: works out what every light should do, 40 times a second, on its own thread.

Each tick:
1. apply the commands that arrived (fader moves, Cut / Fade to black, sending inputs...)
2. render Base (a transition mixes two inputs) and the Overlay's copies on top, into a
   160x90 picture. "Show on lights" (solo) replaces all of that with one input.
3. sample the picture: each colour light takes the average colour of its patch
4. Master (colour lights only)
5. Identify / test colours / flashes (the Layout page's tools)
6. the house lights (festoon, key lights) with their fades. An input can set them
   when it goes on Base; a manual fader move wins until the next input.
7. (Cut / Fade to black isn't a step here: it's Base going to nothing, and house switches going off)
8. encode the DMX universe and hand it to the output thread
9. publish an EngineFrame for the screen, about 10 times a second

A tick that raises an error is written to the session log and skipped; the loop
carries on. A broken element draws nothing; a broken picture holds its last frame.
If the thread itself dies, a watchdog starts a new one.
"""

from __future__ import annotations

import queue
import threading
import time
from types import MappingProxyType
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import numpy as np

from .automations import (
    EVALUATE_EVERY,
    GUI_ACTIONS,
    STARTUP_WINDOW,
    AutomationRunner,
    AutomationStatus,
    HistoryEntry,
    Situation,
    action_command,
    describe_action,
    show_matches,
    system_uptime,
)
from .bus import (
    COPIES_PER_INPUT,
    MAX_COPIES,
    SWITCH_FADE,
    Bus,
    Frozen,
    Instance,
    OverlayLayer,
    composite,
    frozen_for_lights,
    frozen_mix,
    layer_kind,
    render_input,
)
from .commands import (
    HOUSE_SLEW,
    HOUSE_SWITCH_FADE,
    QUIT_FADE,
    ClearBase,
    ClearFromOverlay,
    ClearOverlay,
    Command,
    CountdownAction,
    FlashLights,
    ForgetShow,
    LoadShow,
    PowerEvent,
    QuietAutomations,
    RequestShow,
    ResetAutomation,
    RunAutomation,
    SendToBase,
    SendToOverlay,
    SetAutomationEnabled,
    SetAutomationsPaused,
    SetCountdownLength,
    SetDefaultShow,
    SetGlobalAutomations,
    SetHouse,
    SetIdentify,
    SetMaster,
    SetOverlayOn,
    SetOverlayOpacity,
    SetSolo,
    SetTestColour,
    SetVariable,
    ShowLoadFailed,
    ShowLoadWaiting,
    SwapShow,
    SwitchLayout,
    ToBlack,
)
from .compositor import to_rgb8
from .countdown import DEFAULT_SECONDS as DEFAULT_COUNTDOWN
from .countdown import MAX_SECONDS as MAX_COUNTDOWN
from .countdown import STARTUP_QUIET
from .countdown import CountdownView, counts_down, describe, next_fire, next_stop
from .elements import RenderContext, ndi_sources_used
from .live_state import HouseSetting, LiveState
from .log import log_exception, log_info
from .model import EMPTY, Input, Show, Transition, frozen_map, has_finished, input_progress, slugify
from .video_source import videos
from .ndi_source import SourceStatus
from .output import RGB, OutputStatus, encode_dmx
from .params import evaluate, is_dynamic
from .variables import coerce_global, initial_globals, reconcile_globals, saveable, scope_for, use_scope
from .lights import CANVAS_H, CANVAS_W, identify_colours, lights_for
from .slot import LatestSlot
from .timing import sleep_until

TICK_RATE = 40


def _delay_of(action) -> float:
    """An automation action's "after N s" (0 = at once)."""
    delay = action.get("delay", 0)
    return float(delay) if isinstance(delay, (int, float)) and not isinstance(delay, bool) else 0.0


AUDIO_OFF = MappingProxyType({"level": 0.0, "bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0, "pitch": 0.0})
PUBLISH_INTERVAL = 0.1  # seconds between EngineFrames for the screen
PREVIEW_INTERVAL = 0.2  # seconds between preview pictures for the screen (only while a window shows one)
LATE_PREVIEW_INTERVAL = 1.0  # ...while ticks run late (the lights come first; the picture slows, it doesn't stop)
# commands a person sends (the window, the Stream Deck, the phone page) that mean they've taken charge of the lights:
# after a "Turn lights off" quit, automations are held back only until one of these comes
PERSON_TAKES_CHARGE = (SendToBase, ClearBase, SendToOverlay, ClearFromOverlay, ClearOverlay, SetHouse, ToBlack, RunAutomation,
                       SetSolo)
LATE_TICK = 0.005  # seconds: a tick that starts this much after its time is late (pictures for the screen wait then)
TIMING_WINDOW = 60  # seconds of tick timings kept for the lateness figures
TIDY_INTERVAL = 1.0
WATCHDOG_INTERVAL = 1.0
STALL_SECONDS = 2.0
FLASH_STEP = 0.15  # a flash is on/off/on/off/on/off in steps this long
FLASH_STEPS = 6
WAKE_GAP = 60.0  # seconds: a jump this big in the clocks means the PC slept (or the clock was changed)
WAKE_DEBOUNCE = 60.0  # seconds: wake signals this close together are the same wake
STARTUP_HOLD = 3.0  # seconds: at most this long at startup, nothing is sent while the screen feed's first picture comes
FEED_WAIT = 10.0  # seconds: at startup, an automation about a feed waits at most this long for it to start sending
# seconds: a show a global automation loads; the loaded show's automations wait this long for it at most. The window
# answers within a frame (it's loaded, or it can't be), so this only matters if it's busy (a slow sign-in, say); while
# a dialog that's open holds the load, the window asks for longer (ShowLoadWaiting)
LOAD_WAIT = 20.0
BLACK: RGB = (0.0, 0.0, 0.0)
WHITE: RGB = (1.0, 1.0, 1.0)


def _brings_lights_up(command: Any) -> bool:
    """An automation action that means "lights on": an input to Base or the Overlay, or a house light up."""
    if isinstance(command, (SendToBase, SendToOverlay)):
        return True
    if isinstance(command, SetHouse):
        level_up = command.level is None or command.level > 0.0
        return (command.on is True and level_up) or (command.on is None and (command.level or 0.0) > 0.0)
    return False


def show_id(folder: str) -> str:
    """A show's id, from its folder's name ('...\\Sunday Morning' -> 'sunday-morning'), as shows.show_id."""
    name = str(folder or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return slugify(name, "show") if name else ""


def _folder_key(folder: str) -> str:
    """A folder written one way, for looking things up (case and slashes as Windows sees them)."""
    import os

    return os.path.normcase(os.path.abspath(folder)) if folder else ""


def clamp01(value: float) -> float:
    """0-1 (and NaN -> 0: one bad number must never stop the lights)."""
    if value >= 1.0:
        return 1.0
    return float(value) if value > 0.0 else 0.0


class Ramp:
    """A number that moves in a straight line to its target over a set time."""

    def __init__(self, value: float) -> None:
        self.start = value
        self.target = value
        self.t0 = 0.0
        self.duration = 0.0

    def set(self, target: float, duration: float, now: float) -> None:
        self.start = self.value(now)
        self.target = target
        self.t0 = now
        self.duration = max(0.0, duration)

    def value(self, now: float) -> float:
        if self.duration <= 0.0 or now >= self.t0 + self.duration:
            return self.target
        if now <= self.t0:
            return self.start
        return self.start + (self.target - self.start) * (now - self.t0) / self.duration

    def moving(self, now: float) -> bool:
        return self.duration > 0.0 and now < self.t0 + self.duration


class _House:
    """One house light: its fader level and its on/off switch, each with a fade."""

    def __init__(self, setting: HouseSetting) -> None:
        self.level = Ramp(clamp01(setting.level))
        self.on = bool(setting.on)
        self.switch = Ramp(1.0 if setting.on else 0.0)
        # switched off by a Cut / Fade to black (everything): its first switch on comes back the same way, a cut
        # (0) or a fade over the same seconds; None = switch as normal
        self.mirror: float | None = None

    def out(self, now: float) -> float:
        return self.level.value(now) * self.switch.value(now)


@dataclass(frozen=True)
class HouseView:
    level: float  # fader position now (moves during fades)
    on: bool  # the switch
    out: float  # what's actually sent, after the switch's fade


@dataclass(frozen=True)
class CopyView:
    """One copy of an input on the Overlay (its tile shows a box for it)."""

    number: int
    input_id: str
    name: str = ""
    progress: float | None = None  # a timed input: how far through (0-1); None: infinite
    tag: str = ""  # who sent it (a held Send to Overlay button)


@dataclass(frozen=True)
class OverlayView:
    """The Overlay as the screen shows it (M14: one layer of copies)."""

    on: bool = True  # its switch (off hides it without emptying it)
    visible: float = 1.0  # 0-1, moves while the switch fades
    opacity: float = 1.0
    copies: tuple[CopyView, ...] = ()  # the copies there, bottom first (not the ones on their way out)
    leaving: int = 0  # copies on their way out

    def of(self, input_id: str) -> tuple[CopyView, ...]:
        return tuple(copy for copy in self.copies if copy.input_id == input_id)


@dataclass(frozen=True)
class AutomationsView:
    """The automations as the screen shows them."""

    paused: bool = False
    paused_since: float | None = None  # wall time
    statuses: Mapping[str, AutomationStatus] = EMPTY
    history: tuple[HistoryEntry, ...] = ()  # oldest first


@dataclass(frozen=True)
class EngineFrame:
    """A snapshot of the engine for the screen. Light colours are 'as sent to the lights'."""

    seq: int = 0
    colours: Mapping[str, RGB] = EMPTY  # colour light id -> (r, g, b) 0-1
    house: Mapping[str, HouseView] = EMPTY
    master: float = 1.0
    colour_black: bool = False  # nothing on Base or the Overlay (after a Cut / Fade to black, say)
    output: OutputStatus = OutputStatus()
    base: str = "Black"  # name of what's on Base
    base_id: str = ""  # its input id ("" = nothing)
    base_progress: float | None = None  # 0-1 while Base is transitioning
    playing: Mapping[str, float] = EMPTY  # timed inputs on Base or the Overlay -> how far through (0-1)
    audio: Mapping[str, float] = EMPTY  # audio.level and friends, now
    audio_state: str = "off"  # off, listening, error
    audio_detail: str = ""  # what it's listening to, or what's wrong
    audio_rate: float = 0.0  # blocks of sound analysed a second (0 = none arriving)
    overlay: OverlayView = OverlayView()
    solo: str = ""  # input shown alone on the lights ("" = none)
    live_inputs: frozenset[str] = frozenset()  # inputs on Base or the Overlay right now
    # inputs whose pictures and videos are on the lights now (Base and the Overlay, mid-transition too, and solo):
    # an update to a linked file waits until its input isn't one of these
    in_use: frozenset[str] = frozenset()
    identify: bool = False
    identify_seed: int = 7
    testing: bool = False  # test colours are on
    ndi: Mapping[str, SourceStatus] = EMPTY  # wanted NDI name (lower case) -> status
    preview: np.ndarray | None = field(default=None, compare=False)  # (90, 160, 3) uint8: the picture
    preview_seq: int = 0
    element_faults: Mapping[str, str] = EMPTY  # "input/element" -> what went wrong
    variables: Mapping[str, Any] = EMPTY  # show variables' current values (number or (r, g, b))
    automations: AutomationsView = AutomationsView()  # the loaded show's
    global_automations: AutomationsView = AutomationsView()
    show_folder: str = ""  # the loaded show
    frozen_from: str = ""  # Base holds another show's look, frozen when this one loaded (that show's name)
    countdown: CountdownView | None = None  # the countdown before an automatic change the banner shows (M13)
    tick_ms: float = 0.0  # average work per tick
    tick_max_ms: float = 0.0
    tick_rate: float = 0.0  # ticks in the last second
    faults: int = 0  # ticks that failed (see session.log)
    restarts: int = 0  # times the watchdog restarted the engine thread
    live: LiveState | None = None  # what to restore after a restart


class _NoSources:
    def frame(self, name: str):
        return None

    def set_keep(self, names) -> None:
        return None

    def statuses(self) -> dict:
        return {}

    def tidy(self) -> None:
        return None


class Engine:
    def __init__(
        self,
        show: Show,
        output: Any,
        live: LiveState | None = None,
        *,
        sources: Any | None = None,
        audio: Any | None = None,
        initial_base: str | None = None,
        clock: Callable[[], float] = time.perf_counter,
        rate: int = TICK_RATE,
        wall: Callable[[], float] = time.time,
        local_time: Callable[[float], datetime] = datetime.fromtimestamp,
        uptime: Callable[[], float] = system_uptime,
        started_by_windows: bool = False,
        global_automations: Show | None = None,
    ) -> None:
        """output: a DmxOutputThread (anything with submit(bytes) and status()).
        sources: a SourceManager (NDI feeds), or None for no video.
        audio: an audio.AudioCapture (anything with latest() -> {"level": ..., ...}, state and detail), or None.
        initial_base: an input to put on Base when there's no live state (the very first run).
        wall / local_time / uptime: the real clock, local time (with the clock changes) and how long
        Windows has been running, for automations (tests pass fakes).
        started_by_windows: Windows started CentraLight at sign-in, which counts as "The PC starts" (with Fast
        Startup, a shut-down PC doesn't really restart, so Windows' running time doesn't go back to 0).
        global_automations: a Show holding just the global automations (they're app-wide, not the show's).

        The PC has just started: every light starts black (house lights off, their levels kept) with nothing
        playing, and the show's "When this show loads" automations run. CentraLight reopened without a restart
        (after Quit or a crash): it carries on exactly as it was."""
        self.show = show
        self.output = output
        self.sources = sources if sources is not None else _NoSources()
        self.audio = audio  # replaced when Settings change the audio source (read once per tick)
        self.frames: LatestSlot[EngineFrame] = LatestSlot(EngineFrame())
        self._clock = clock
        self._rate = rate
        self._commands: queue.SimpleQueue[Command] = queue.SimpleQueue()
        now = clock()
        live = live or LiveState()
        self._master = Ramp(clamp01(live.master))
        # After a "Turn lights off" quit the lights start black and stay so against automations until a time range
        # begins after the quit (e.g. Sunday 09:00), so a feed coming back at 03:00 doesn't light the church. A
        # person sending anything takes charge at once. quit_at: when that quit was (an older live.json only says
        # when it was last saved; failing that, at least before this start).
        self._quit_dark = bool(live.quit_dark)
        quit_at = live.quit_at if live.quit_at > 0 else live.saved_at if live.saved_at > 0 else wall()
        self._quit_at: float | None = quit_at if self._quit_dark else None
        # "The PC starts": Windows started CentraLight at sign-in, or it starts within 10 minutes of the PC starting and
        # didn't run since then (a crash and reopen at 10:01 after a 09:52 start carries on, as any reopening does)
        booted_at = wall() - uptime()
        self._pc_start = bool(started_by_windows) or (uptime() < STARTUP_WINDOW and not live.saved_at > booted_at)
        self._house: dict[str, _House] = {}
        self._house_driven: dict[str, str] = {}  # house light id -> the input that set it
        house = live.house
        if self._pc_start:  # the PC has just started: every light starts black (the house lights' levels are kept)
            house = {fid: HouseSetting(setting.level, False) for fid, setting in live.house.items()}
        self._sync_house(house)
        # the lights' colours are worked out at their dots every tick; a picture is drawn only for a window showing one
        self._lights = lights_for(show.fixtures, None)
        self._ctx = RenderContext(0, 0, self.sources, folder=show.folder, lights=self._lights, per_light=True)
        self._picture_ctx = RenderContext(CANVAS_W, CANVAS_H, self.sources, folder=show.folder, lights=self._lights)
        self._globals = initial_globals(show, live.globals)
        self._ctx.globals = frozen_map(self._globals)
        self._show_globals: dict[str, dict] = {_folder_key(k): dict(v) for k, v in live.show_globals.items()}  # other shows' variables
        # automations: the loaded show's, and the global ones (app-wide; they're checked first)
        self._wall = wall
        self._local_time = local_time
        self._uptime = uptime
        self._runner = AutomationRunner()
        self._runner.restore(live.automations, wall(), saved_at=live.saved_at)
        self._after_load: list[tuple[str, dict]] = []  # global actions waiting for the show they loaded
        self.global_automations = global_automations if global_automations is not None else Show(name="Global automations")
        self._global_runner = AutomationRunner()
        self._global_runner.restore(live.global_automations, wall(), saved_at=live.saved_at)
        self._global_runner.paused, self._global_runner.paused_since = self._runner.paused, self._runner.paused_since
        self._first_check = True
        # the countdown before an automatic show load or Cut / Fade to black (countdown.py); none just after starting
        self.countdown_seconds = float(DEFAULT_COUNTDOWN)
        self._quiet_until = now + STARTUP_QUIET
        self._show_events: set[str] = {"show_loads"} if self._pc_start else set()  # for the show's automations' next check
        self._pending_load: tuple[str, float] | None = None  # (show, until when) a global automation is loading
        self._woke = False
        self._last_wake: float | None = None
        self._prev_mono: float | None = None
        self._prev_wall: float | None = None
        self._auto_key: tuple | None = None
        self._ndi_sending: dict[str, bool] = {}
        self._views: dict[str, tuple] = {}  # "show" / "global" -> (version, AutomationsView)
        self.gui_requests: queue.SimpleQueue[tuple[str, dict]] = queue.SimpleQueue()  # (who, action) for the window
        self._base = Bus("base")
        self._overlay = OverlayLayer()
        self._overlay_opacity = Ramp(1.0)
        self._solo: Instance | None = None
        self._solo_fixed = False  # the solo input came with its command (not from the show)
        if self._pc_start:
            log_info("The PC has just started: the lights start black, with nothing playing")
        else:
            self._restore_buses(live, initial_base, now)
        self._last_canvas = self._lights.empty()
        # At startup the lights keep showing what they showed (the dongle repeats it) until the feeds Base and the
        # Overlay use have their first picture, instead of going black for a second or two meanwhile
        self._holding = True
        self._hold_until = now + STARTUP_HOLD
        self._feeds_until = now + FEED_WAIT
        self._identify = False
        self._identify_seed = show.identify_seed
        self._identify_map: dict[str, RGB] = {}
        self._test_colour: RGB | None = None
        self._test_fixtures: frozenset[str] = frozenset()
        self._flashes: dict[str, float] = {}
        self._seq = 0
        self._preview_seq = 0
        self._preview: np.ndarray | None = None
        self._preview_wanted = False  # a window shows the live picture (set_preview_wanted)
        self._lateness: deque[float] = deque(maxlen=rate * TIMING_WINDOW)  # seconds each tick started after its time
        self._work: deque[float] = deque(maxlen=rate * TIMING_WINDOW)  # seconds each tick took
        self._late_until = 0.0  # pictures for the screen wait until then (a tick was late)
        self._last_publish = -1e9
        self._last_preview = -1e9
        self._last_tidy = -1e9
        self._faults = 0
        self._restarts = 0
        self._tick_times: deque[float] = deque(maxlen=rate)  # seconds of work per tick
        self._tick_stamps: deque[float] = deque(maxlen=rate * 4)  # when each tick started
        self._fault_hook: Callable[[], None] | None = None  # tests inject failures here
        self._running = False
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._heartbeat = time.perf_counter()
        self._lock = threading.Lock()
        self.sources.set_keep(self._ndi_names())

    # ------------------------------------------------------------ used by other threads

    def submit(self, command: Command) -> None:
        self._commands.put(command)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._heartbeat = time.perf_counter()
            self._start_loop()
            self._watchdog = threading.Thread(target=self._watch, name="engine-watchdog", daemon=True)
            self._watchdog.start()

    def stop(self, lights_off: bool = False, fade: bool = True) -> LiveState:
        """Stop ticking. lights_off fades everything to black first (QUIT_FADE). Returns the state to save."""
        if lights_off and fade and self._running:
            self.submit(ToBlack(QUIT_FADE, everything=True))
            time.sleep(QUIT_FADE + 0.1)  # (the engine thread fades the lights meanwhile)
        with self._lock:
            was_running = self._running
            self._running = False
            self._stop_event.set()
        for thread in (self._thread, self._watchdog):
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(2.0)
        if was_running:
            try:
                self.tick()  # one last tick, so commands sent just before stopping still reach the lights
            except Exception as exc:
                log_exception("engine final tick", exc)
        if lights_off:
            self._to_black(0.0, True, self._clock())  # (black for sure, faded or not)
            if not self._quit_dark:
                self._quit_at = self._wall()  # a new quit (one still held from an earlier quit keeps that quit's time)
            self._quit_dark = True
        return self.live_state()

    @property
    def running(self) -> bool:
        return self._running

    def live_state(self, now: float | None = None) -> LiveState:
        """What to save so a restart picks up exactly where this left off."""
        now = self._clock() if now is None else now
        base = None
        if self._base.current is not None:
            base = {"input": self._base.current.input.id, "time": round(self._base.current.time(now), 3)}
        overlay = {"on": self._overlay.on, "opacity": self._overlay_opacity.target,
                   "copies": [{"input": copy.input.id, "time": round(copy.instance.time(now), 3)}
                              for copy in self._overlay.shown()]}
        frozen = None
        held = self._base.held
        if self._base.current is None and held is not None:  # another show's look, held since this one loaded
            colours = frozen_for_lights(held, self._lights.ids)
            if colours is not None:
                frozen = {"from": held.name, "colours": {light_id: [round(float(v), 4) for v in colours[:, i]]
                                                         for i, light_id in enumerate(self._lights.ids)}}
        return LiveState(
            show=self.show.folder,
            master=self._master.target,
            quit_dark=self._quit_dark,
            quit_at=(self._quit_at or 0.0) if self._quit_dark else 0.0,
            house={fid: HouseSetting(h.level.target, h.on) for fid, h in self._house.items()},
            base=base,
            overlay=overlay,
            frozen=frozen,
            globals=saveable(self._globals),
            show_globals={k: dict(v) for k, v in self._show_globals.items()},
            automations=self._runner.to_dict(),
            global_automations=self._global_runner.to_dict(),
        )

    # ------------------------------------------------------------ the tick

    def tick(self, now: float | None = None) -> None:
        """One engine step. The engine thread calls this; tests call it directly."""
        started = time.perf_counter()
        now = self._clock() if now is None else now
        applied = self._drain(now)
        if self._fault_hook is not None:
            self._fault_hook()
        audio = self.audio
        self._ctx.audio = MappingProxyType(audio.latest()) if audio is not None else AUDIO_OFF
        applied = self._automations(now) or applied
        applied = self._finish_timed(now) or applied
        self._update_driven_house(now)

        self._ctx.waiting = False
        canvas = self._render(now)
        sampled = canvas[:3].T  # (premultiplied colour is already "over black")
        master = self._master.value(now)
        colours: dict[str, RGB] = {}
        for light_id, (r, g, b) in zip(self._lights.ids, sampled):
            colours[light_id] = (clamp01(r) * master, clamp01(g) * master, clamp01(b) * master)
        self._apply_overrides(colours, now)
        out_colours = colours
        house_out = {fid: h.out(now) for fid, h in self._house.items()}

        if self._holding and not (self._ctx.waiting and now < self._hold_until):
            self._holding = False  # every feed has a picture (or it's been long enough): from now on, always send
        if not self._holding:
            self.output.submit(encode_dmx(self.show.fixtures, out_colours, house_out))

        if now - self._last_tidy >= TIDY_INTERVAL:
            self._last_tidy = now
            self.sources.tidy()
            videos.tidy()
        # the lights' frame is out: only now a picture for the screen, if one's wanted and the engine is on time
        interval = PREVIEW_INTERVAL if time.perf_counter() >= self._late_until else LATE_PREVIEW_INTERVAL
        if self._preview_wanted and now - self._last_preview >= interval:
            self._last_preview = now
            picture = self._render_picture(now)
            if picture is not None:
                self._preview = to_rgb8(picture)
                self._preview_seq += 1
        elapsed = time.perf_counter() - started
        self._tick_times.append(elapsed)
        self._work.append(elapsed)
        self._tick_stamps.append(started)
        if applied or now - self._last_publish >= PUBLISH_INTERVAL:
            self._publish(now, out_colours, house_out)

    def _finish_timed(self, now: float) -> bool:
        """A timed input that stops at its end leaves Base when it gets there; on the Overlay every timed copy leaves
        (the way it came in) once its timeline has played."""
        changed = False
        current = self._base.current
        if current is not None and has_finished(current.input, current.time(now)) and not self._base.transitioning(now):
            self._base.clear(now, cut=True)
            changed = True
        return self._overlay.finish_timed(now) or changed

    def _render(self, now: float) -> np.ndarray:
        """Base and the Overlay (or the solo input). If rendering fails, the last good picture is held."""
        try:
            if self._solo is not None:
                canvas = render_input(self._solo, now, self._ctx)
            else:
                canvas = self._base.render(now, self._ctx)
                self._overlay.opacity = self._overlay_opacity.value(now)
                canvas = composite(canvas, [self._overlay.render(now, self._ctx)])
        except Exception as exc:
            self._faults += 1
            log_exception("rendering", exc)
            return self._last_canvas
        self._last_canvas = canvas
        return canvas

    def _to_black(self, seconds: float, everything: bool, now: float) -> None:
        """Cut or fade the colour lights to black: the mix as it is now (Base and the Overlay, as one still look)
        fades to nothing on Base, and the Overlay is cleared at once (its look is in it). everything: the house
        lights' switches go off too, over the same time, keeping their levels; each remembers how, so its next
        plain switch on brings it back the same way."""
        base = self._base
        solo, self._solo = self._solo, None  # (the Editor's "Show on lights" ends too: black means black)
        if seconds > 0.0 and (not self._black_now(now) or solo is not None):
            base.outgoing = self._still_mix(now, solo)
            base.transition = Transition("fade", seconds)
            base.t_start = now
        else:
            base.outgoing = None
            base.transition = Transition("cut", 0.0)
        base.current = None
        base.held = None
        self._overlay.clear_now()
        self._house_driven.clear()
        if everything:
            for house in self._house.values():
                if house.on:
                    house.on = False
                    house.switch.set(0.0, seconds, now)
                    house.mirror = seconds

    def colour_black(self, now: float | None = None) -> bool:
        """Nothing on Base or the Overlay, and no fade still going: the colour lights are black."""
        now = self._clock() if now is None else now
        base = self._base
        return (base.current is None and base.held is None and not base.transitioning(now)
                and not self._overlay.lit(now))

    def _black_now(self, now: float) -> bool:
        """Nothing shows on the colour lights at this moment (a fade to black may still be ending: not black yet)."""
        base = self._base
        return (base.current is None and base.held is None and not base.transitioning(now)
                and not self._overlay.lit(now, including_fades=False))

    def _still_mix(self, now: float, solo: Instance | None = None) -> Frozen:
        """What the colour lights show now (Base and the Overlay, or the Editor's input), as one still look: for the
        lights, and for the live picture while one is drawn."""
        contexts = self._contexts()
        canvases = {}
        for ctx in contexts:
            ctx.globals, ctx.audio = self._ctx.globals, self._ctx.audio
            if solo is not None:
                canvases[layer_kind(ctx)] = render_input(solo, now, ctx)  # what the lights were showing
            else:
                canvases[layer_kind(ctx)] = composite(self._base.render(now, ctx), [self._overlay.render(now, ctx)])
        return frozen_mix(canvases, contexts, name=self.show.name)

    def _end_quit_dark(self, why: str) -> None:
        if self._quit_dark:
            self._quit_dark = False
            log_info(f"The lights are no longer held off after the \"Turn lights off\" quit: {why}")

    def _render_picture(self, now: float) -> np.ndarray | None:
        """A picture of what's on the lights, for the Dashboard (None if it can't be drawn: the lights carry on)."""
        ctx = self._picture_ctx
        ctx.globals = self._ctx.globals
        ctx.audio = self._ctx.audio
        try:
            if self._solo is not None:
                return render_input(self._solo, now, ctx)
            canvas = self._base.render(now, ctx)
            return composite(canvas, [self._overlay.render(now, ctx)])
        except Exception as exc:
            log_exception("drawing the live picture", exc)
            return None

    def set_preview_wanted(self, wanted: bool) -> None:
        """A window shows the live picture (or stops showing it): it's only drawn while one does."""
        self._preview_wanted = bool(wanted)
        if not wanted:
            self._preview = None

    def _contexts(self) -> tuple:
        """Where a mix interrupted mid-transition is frozen: the lights, and the live picture while it's shown."""
        return (self._ctx, self._picture_ctx) if self._preview_wanted else (self._ctx,)

    def tick_timing(self) -> dict[str, float]:
        """The last minute's ticks: how late they started and how long they took (median, 99th percentile and
        worst, in ms), and how many started over 5 ms late."""
        late = sorted(self._lateness)
        work = sorted(self._work)
        if not late or not work:
            return {}

        def pick(values, share):
            return values[min(len(values) - 1, int(share * (len(values) - 1) + 0.5))] * 1000.0

        return {"ticks": float(len(late)), "late_median_ms": pick(late, 0.5), "late_p99_ms": pick(late, 0.99),
                "late_worst_ms": late[-1] * 1000.0, "late_over_5ms": float(sum(1 for v in late if v > LATE_TICK)),
                "work_median_ms": pick(work, 0.5), "work_p99_ms": pick(work, 0.99), "work_worst_ms": work[-1] * 1000.0}

    def reset_timing(self) -> None:
        """Start the tick timings afresh (for measuring one situation)."""
        self._lateness.clear()
        self._work.clear()

    def is_late(self) -> bool:
        """A tick started late in the last second: pictures for people should wait (the Editor draws fewer)."""
        return time.perf_counter() < self._late_until

    def _apply_overrides(self, colours: dict[str, RGB], now: float) -> None:
        """Identify, test colours and flashes (after Master: they're always full brightness)."""
        if self._identify:
            for light_id in colours:
                colours[light_id] = self._identify_map.get(light_id, WHITE)
        if self._test_colour is not None:
            for light_id in colours:
                if not self._test_fixtures or light_id in self._test_fixtures:
                    colours[light_id] = self._test_colour
        for light_id, start in list(self._flashes.items()):
            step = int((now - start) / FLASH_STEP)
            if step >= FLASH_STEPS or light_id not in colours:
                del self._flashes[light_id]
                continue
            colours[light_id] = WHITE if step % 2 == 0 else BLACK

    def _drain(self, now: float) -> bool:
        applied = False
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return applied
            try:
                if self._quit_dark and (isinstance(command, PERSON_TAKES_CHARGE)
                                        or (isinstance(command, LoadShow) and not command.automation)):
                    self._end_quit_dark("someone took charge")
                self._apply(command, now)
                applied = True
            except Exception as exc:
                log_exception(f"engine command {type(command).__name__}", exc)

    def _apply(self, command: Command, now: float) -> None:
        if isinstance(command, SetMaster):
            self._master.set(clamp01(command.level), command.fade, now)
        elif isinstance(command, SetHouse):
            house = self._house.get(command.fixture)
            if house is None:
                return
            self._house_driven.pop(command.fixture, None)  # a manual move wins until the next input
            if command.level is not None:
                fade = HOUSE_SLEW if command.fade is None else command.fade
                house.level.set(clamp01(command.level), fade, now)
            on = (not house.on) if command.toggle else command.on
            if on is not None and on != house.on:
                house.on = on
                if on and house.mirror is not None and command.fade is None and command.level is None:
                    fade = house.mirror  # back on the way a Cut / Fade to black put it out (a plain switch on)
                else:
                    fade = HOUSE_SWITCH_FADE if command.fade is None else command.fade
                house.switch.set(1.0 if on else 0.0, fade, now)
            if house.on:
                house.mirror = None  # it's on again: its switch works as normal from now on
        elif isinstance(command, ToBlack):
            self._to_black(max(0.0, float(command.seconds)), bool(command.everything), now)
        elif isinstance(command, SendToBase):
            inp = self._input(command.input_id)
            transition = command.transition or inp.transition if inp is not None else None
            if inp is not None and self._base.send(inp, now, self._contexts(), cut=command.cut, transition=command.transition):
                self._start_input_house(inp, now, 0.0 if command.cut else transition.duration)
        elif isinstance(command, ClearBase):
            self._base.clear(now, self._contexts(), cut=command.cut)
            self._house_driven.clear()
        elif isinstance(command, SendToOverlay):
            inp = self._input(command.input_id)
            if inp is not None:
                if command.toggle and self._overlay.count(inp.id):
                    self._overlay.clear_input(inp.id, now)
                elif command.once and self._overlay.count(inp.id):
                    self._overlay.set_on(True, now)  # (it's there already: just make sure it shows)
                elif self._overlay.send(inp, now, command.tag) is None:
                    log_info(f"{inp.name} wasn't sent to the Overlay: it has {COPIES_PER_INPUT} copies there already"
                             if self._overlay.count(inp.id) >= COPIES_PER_INPUT
                             else f"{inp.name} wasn't sent to the Overlay: it's full ({MAX_COPIES} copies)",
                             key=f"overlay-full-{inp.id}")
        elif isinstance(command, ClearFromOverlay):
            if command.copy:
                self._overlay.clear_copy(int(command.copy), now, command.cut)
            elif command.tag:
                self._overlay.clear_tag(command.tag, now, command.cut)
            elif command.input_id:
                self._overlay.clear_input(command.input_id, now, command.cut)
        elif isinstance(command, ClearOverlay):
            self._overlay.clear_all(now, command.cut)
        elif isinstance(command, SetOverlayOn):
            self._overlay.set_on((not self._overlay.on) if command.on is None else bool(command.on), now)
        elif isinstance(command, SetOverlayOpacity):
            self._overlay_opacity.set(clamp01(command.level), command.fade, now)
        elif isinstance(command, SetSolo):
            inp = command.input if command.input is not None else (self._input(command.input_id) if command.input_id else None)
            if inp is None:
                self._solo = None
                self._solo_fixed = False
            elif self._solo is None or self._solo.input.id != inp.id or command.input is not None:
                self._solo = Instance(inp, now)
                self._solo_fixed = command.input is not None  # a trial input: show changes don't replace it
        elif isinstance(command, SwapShow):
            self._swap_show(command.show)
        elif isinstance(command, LoadShow):
            if not command.automation:
                self._global_runner.manual_load()  # (a person's choice: the default show doesn't replace it, M14)
            self._load_show(command.show, now)
            self._after_load_done(now)  # (a global automation's actions after "Load a show": in the show it loaded)
        elif isinstance(command, RequestShow):
            self.gui_requests.put((command.who, {"do": "load_show", "folder": command.folder}))
        elif isinstance(command, ShowLoadFailed):
            if self._pending_load is not None and command.wanted == self._pending_load[0]:  # (that request's, no other)
                self._pending_load = None
                self._after_load_done(now)
                self._auto_key = None
        elif isinstance(command, ShowLoadWaiting):
            if self._pending_load is not None and command.wanted == self._pending_load[0]:
                self._pending_load = (self._pending_load[0], now + max(0.0, float(command.seconds)))
        elif isinstance(command, ForgetShow):
            self._show_globals.pop(_folder_key(command.folder), None)
        elif isinstance(command, SetGlobalAutomations):
            self.global_automations = command.automations
            self.sources.set_keep(self._ndi_names())
        elif isinstance(command, SetVariable):
            self._set_variable(command.name, command.value)
        elif isinstance(command, SetAutomationsPaused):
            paused = (not self._runner.paused) if command.paused is None else bool(command.paused)
            self._runner.set_paused(paused, self._wall())
            self._global_runner.paused, self._global_runner.paused_since = self._runner.paused, self._runner.paused_since
            if paused:
                self._global_runner.pending.clear()  # nothing waiting runs while paused (a countdown included)
                self._global_runner.expected.clear()
                self._after_load.clear()  # (a global automation's actions waiting for a show to load too)
        elif isinstance(command, SetCountdownLength):
            self.countdown_seconds = min(float(MAX_COUNTDOWN), max(0.0, float(command.seconds)))
            for runner in (self._global_runner, self._runner):  # (shorter now: what's counting down happens within it)
                runner.cap_countdowns(now, self.countdown_seconds)
        elif isinstance(command, SetDefaultShow):
            self._global_runner.default_show = str(command.show or "").strip()
        elif isinstance(command, CountdownAction):
            self._countdown_pressed(command.what, command.countdown_id, now)
        elif isinstance(command, QuietAutomations):
            self._runner.quiet(command.automation_ids)
            self._auto_key = None  # (checked at once)
        elif isinstance(command, RunAutomation):
            scope = "global" if command.scope == "global" else "show"
            runner, container = (self._global_runner, self.global_automations) if scope == "global" else (self._runner, self.show)
            automation = container.automations.get(command.automation_id)
            if automation is not None:
                do = self._global_action if scope == "global" else self._do_action  # (a global run's steps after a load wait for it)
                for who, action in runner.run_now(automation, now, self._wall()):
                    do(who, action, now)
        elif isinstance(command, ResetAutomation):
            (self._global_runner if command.scope == "global" else self._runner).reset(command.automation_id)
        elif isinstance(command, SetAutomationEnabled):
            self.gui_requests.put(("Stream Deck", {"do": "automation", "automation": command.automation_id, "enabled": command.enabled,
                                                   "_scope": "global" if command.scope == "global" else "show"}))
        elif isinstance(command, SwitchLayout):
            self.gui_requests.put(("Stream Deck", {"do": "layout", "layout": command.layout_id}))
        elif isinstance(command, PowerEvent) and command.kind == "woke":
            self._on_wake()
        elif isinstance(command, SetIdentify):
            self._identify = bool(command.on)
            if command.seed is not None:
                self._identify_seed = int(command.seed)
            self._refresh_identify()
        elif isinstance(command, FlashLights):
            for light_id in command.fixtures:
                self._flashes[light_id] = now
        elif isinstance(command, SetTestColour):
            self._test_colour = None if command.colour is None else tuple(clamp01(v) for v in command.colour)  # type: ignore[assignment]
            self._test_fixtures = frozenset(command.fixtures)

    def _input(self, input_id: str) -> Input | None:
        inp = self.show.inputs.get(input_id)
        if inp is None:
            log_info(f"There's no input called {input_id!r}", key=f"no-input-{input_id}")
        return inp

    # ------------------------------------------------------------ automations

    def _automations(self, now: float) -> bool:
        """Check the automations (every second, and at once when what they look at changes) and
        do what they say. Also notices waking from sleep by the clocks jumping. True if anything ran."""
        wall = self._wall()
        if self._prev_mono is not None:
            gap = now - self._prev_mono  # the engine's clock: stops while the PC sleeps on some PCs
            drift = (wall - self._prev_wall) - gap  # the wall clock running ahead of it
            if gap > WAKE_GAP or drift > WAKE_GAP:
                self._on_wake()
        self._prev_mono, self._prev_wall = now, wall
        overlay = self._overlay.input_ids()
        base = self._base.current.input.id if self._base.current is not None else ""
        colour_black = self.colour_black(now)
        key = (base, overlay, colour_black, id(self._ctx.globals), tuple(sorted(self._ndi_sending.items())), id(self.show.automations),
               id(self.global_automations.automations), self.show.folder)
        events = set()
        if self._first_check:
            events.add("app_started")
            if self._pc_start:
                events.add("pc_startup")
        if self._woke:
            events.add("pc_woke")
        show_events = frozenset(self._show_events)
        ran = False
        situation = None
        if self._pending_load is not None and now >= self._pending_load[1]:
            log_info(f"The show “{self._pending_load[0]}” a global automation asked for didn't load: the loaded show's "
                     "automations carry on")
            self._pending_load = None
            ran = self._after_load_done(now) or ran
            self._auto_key = None  # (the show's automations are checked now)
        pending = self._pending_load is not None
        # (while a show is loading, its "When this show loads" waits for it: it doesn't make a check every tick)
        if (events or (show_events and not pending) or key != self._auto_key or (self._runner.wants_check(now) and not pending)
                or self._global_runner.wants_check(now)):
            self._auto_key = key
            # just after starting (or a show loading), the NDI feeds are still connecting: until one sends (or
            # FEED_WAIT has passed) "feed lost" isn't true yet, so an automation about it waits
            situation = Situation(
                now=wall, local=self._local_time(wall), base=base, overlay=overlay, variables=self._ctx.globals,
                ndi=dict(self._ndi_sending), colour_black=colour_black, audio=dict(self._ctx.audio), events=frozenset(events),
                ndi_known=now >= self._feeds_until,
                quit_dark_since=self._quit_at if self._quit_dark else None,
                show=show_id(self.show.folder), show_names=self._show_names(),
            )
            self._first_check = False
            self._woke = False
            # the global automations first (one may load another show)...
            self._set_countdown(now)
            for who, action in self._global_runner.check(self.global_automations, situation, now):
                self._global_action(who, action, now)
                ran = True
            self._announce(now, situation, "global", self._global_runner, self.global_automations)
        for who, action in self._global_runner.due(now):
            self._global_action(who, action, now)
            ran = True
        # ...then the show's own, unless a global automation is loading another show: that one's run when it's loaded
        if self._pending_load is not None:
            return ran
        if situation is not None:
            self._show_events.difference_update(show_events)
            for who, action in self._runner.check(self.show, replace(situation, events=show_events), now):
                self._do_action(who, action, now)
                ran = True
            self._announce(now, situation, "show", self._runner, self.show)
        for who, action in self._runner.due(now):
            self._do_action(who, action, now)
            ran = True
        return ran

    # ------------------------------------------------------------ the countdown before automatic changes (M13)

    def _counting(self, now: float) -> bool:
        """Do automatic changes get a countdown now? Not when it's off, just after starting, or while paused."""
        return self.countdown_seconds > 0.0 and now >= self._quiet_until and not self._runner.paused

    def _set_countdown(self, now: float) -> None:
        """Before a check: tell each runner the countdown's length now (0 = none) and which actions get one."""
        seconds = self.countdown_seconds if self._counting(now) else 0.0
        for runner, scope in ((self._global_runner, "global"), (self._runner, "show")):
            runner.countdown = seconds
            runner.counted = lambda action, scope=scope: counts_down(action, scope)
            runner.needless = lambda action: self._needless(action, now)

    def _needless(self, action: Mapping[str, Any], now: float) -> bool:
        """A change that wouldn't change anything gets no countdown: a show that's loaded already, or the colour lights
        to black when they are (and nothing else is asked for)."""
        if action.get("do") == "load_show":
            wanted = str(action.get("show", "")).strip()
            return not wanted or self._is_loaded(wanted)
        return action.get("all") is not True and self.colour_black(now)

    def _announce(self, now: float, situation: Any, scope: str, runner: AutomationRunner, container: Show) -> None:
        """Start the countdown early for runs the clock will start (or end, for "when it stops matching") within its
        length, so their change lands on time. Only when a whole minute is that close: the clock changes nothing
        between them."""
        seconds = runner.countdown
        local = situation.local
        to_minute = 60.0 - (local.second + local.microsecond / 1e6)
        if seconds > 0.0 and to_minute <= seconds + 1.0:
            for automation in container.ordered_automations():
                state = runner.states.get(automation.id)
                if not automation.enabled or state is None or state.tripped:
                    continue
                kind, steps = ("on_end", automation.on_end) if state.matched else ("actions", automation.actions)
                step = next(((a, _delay_of(a)) for a in steps if runner.counted(a)), None)
                if step is None or step[1] >= seconds:
                    continue  # (none, or it's far enough into its run for its banner to start early anyway)
                action, delay = step
                window = seconds - delay
                moment = next_stop(automation, situation, window) if state.matched else next_fire(automation, situation, window)
                if moment is not None:
                    runner.expect(automation.id, kind, moment, now + (moment - local).total_seconds(), action, delay, now)
        runner.settle_expected(now)

    def _countdown_items(self, now: float) -> list[tuple[str, str, str, dict, float]]:
        """(id, scope, automation id, counted step, engine time it happens) for the countdowns on the banner now,
        soonest first: within the countdown's length, and still able to change something."""
        items = []
        for prefix, runner in (("g", self._global_runner), ("s", self._runner)):
            for item, who, action, due in runner.countdown_items():
                if due - now <= self.countdown_seconds + 0.05 and not self._needless(action, now):
                    items.append((f"{prefix}:{item}", "global" if prefix == "g" else "show", who, action, due))
        return sorted(items, key=lambda item: item[4])

    def _countdown_view(self, now: float) -> CountdownView | None:
        items = self._countdown_items(now)
        if not items:
            return None
        item, scope, who, action, due = items[0]
        container = self.global_automations if scope == "global" else self.show
        automation = container.automations.get(who)
        show = str(action.get("show", "")).strip() if action.get("do") == "load_show" else ""
        return CountdownView(item, describe(action), max(0.0, due - now), automation.name if automation is not None else who,
                             len(items) - 1, show)

    def _countdown_pressed(self, what: str, countdown_id: str, now: float) -> None:
        """Load now / Wait 10 min / Skip, on the banner, the phone page or /api/countdown/... ("" = the banner's)."""
        if what not in ("now", "wait", "skip"):
            return
        items = self._countdown_items(now)
        chosen = next((i for i in items if i[0] == countdown_id), None) if countdown_id else (items[0] if items else None)
        if chosen is None:
            log_info(f"Countdown: “{what}” pressed, but that countdown has gone", key="countdown-none")
            return
        if what == "now":
            self._end_quit_dark("someone pressed Now on the countdown")
        prefix, item = chosen[0].split(":", 1)
        scope = "global" if prefix == "g" else "show"
        runner, container = (self._global_runner, self.global_automations) if scope == "global" else (self._runner, self.show)
        early = runner.countdown_pressed(what, item, now, self._wall())
        if early is not None:  # (Now on a run the clock hasn't started yet: done now, as Run now does)
            automation = container.automations.get(early[0])
            if automation is not None:
                do = self._global_action if scope == "global" else self._do_action
                for who, action in runner.run_early(automation, early[1], now, self._wall()):
                    do(who, action, now)

    def _global_action(self, who: str, action: dict, now: float) -> None:
        """A global automation's action. While a show it asked for is loading, its other actions wait for it, so they
        happen in that show (its Festoon and Key lights, say), in their order."""
        if self._pending_load is not None and action.get("do") != "load_show":
            self._after_load.append((who, action))
            return
        self._do_action(who, action, now, "global")

    def _after_load_done(self, now: float) -> bool:
        """The show a global automation asked for has loaded (or couldn't): the actions that waited for it now."""
        waiting, self._after_load = self._after_load, []
        for who, action in waiting:
            self._global_action(who, action, now)
        return bool(waiting)

    def _show_names(self) -> frozenset[str]:
        """What else a global automation can call the loaded show, besides its id: its folder's name and its name."""
        folder = str(self.show.folder or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return frozenset(name for name in (folder.casefold(), self.show.name.casefold()) if name)

    def _is_loaded(self, wanted: str) -> bool:
        return show_matches(wanted, show_id(self.show.folder), self._show_names())

    def _do_action(self, who: str, action: dict, now: float, scope: str = "show") -> None:
        action = dict(action)
        lifts = bool(action.pop("_lifts", False))
        try:
            self._do_action_now(who, action, now, lifts, scope)
        except Exception as exc:  # a wrong value in an automation's file must never stop the engine
            log_exception(f"automation {who}: doing {action.get('do')!r}", exc)

    def _do_action_now(self, who: str, action: dict, now: float, lifts: bool = False, scope: str = "show") -> None:
        container = self.global_automations if scope == "global" else self.show
        if action.get("do") in GUI_ACTIONS:
            name = container.automations[who].name if who in container.automations else who
            if action.get("do") == "load_show":
                wanted = str(action.get("show", "")).strip()
                if not wanted or self._is_loaded(wanted):
                    return  # (that show is loaded already: nothing to do)
                self._pending_load = (wanted, now + LOAD_WAIT)
                if lifts:  # its time range began after a "Turn lights off" quit: the show it loads may light things
                    self._end_quit_dark(f"global automation {who} began its time")
            self.gui_requests.put((name, {**action, "_scope": scope}))
            return
        command = action_command(action, self.show, scope)
        if command is None:
            what = describe_action(action, self.show, scope)
            log_info(f"Automation {who}: couldn't {what} (is it still there?)", key=f"automation-skip-{who}")
            return
        if self._quit_dark and _brings_lights_up(command):
            if not lifts:
                # after a "Turn lights off" quit, only a run made by a time range that began after the quit (e.g.
                # Sunday 09:00) lights things: not "CentraLight starts", a feed coming back or "when it stops"
                log_info(f"Automation {who}: left the lights off (CentraLight was quit with \"Turn lights off\"; "
                         "a scheduled time that begins after that lights them)", key=f"quit-dark-{who}")
                return
            self._end_quit_dark(f"automation {who} began its time")
        if isinstance(command, SetHouse):
            house = self._house.get(command.fixture)
            if house is not None:
                house.mirror = None  # automations set house lights their own way, whatever the blackout was
        self._apply(command, now)

    def _on_wake(self) -> None:
        """After sleep: reconnect the dongle and NDI, and check every automation ("The PC wakes")."""
        wall = self._wall()
        if self._last_wake is not None and 0.0 <= wall - self._last_wake < WAKE_DEBOUNCE:
            return  # the same wake noticed again (Windows sends two messages, and the clocks jump too)
        self._last_wake = wall
        self._woke = True
        log_info("The PC woke up: reconnecting, and checking the automations")
        for runner in (self._global_runner, self._runner):  # (a countdown from before the sleep is stale)
            runner.drop_countdowns("the PC slept")
        for owner in (self.output, self.sources):
            reconnect = getattr(owner, "reconnect", None)
            if reconnect is not None:
                try:
                    reconnect()
                except Exception as exc:
                    log_exception("reconnecting after sleep", exc)

    def _automations_view(self) -> AutomationsView:
        return self._view_of(self._runner, "show")

    def _view_of(self, runner: AutomationRunner, which: str) -> AutomationsView:
        """The automations as the screen shows them (made again only when something changed)."""
        version = (id(runner), runner.paused, len(runner.history), runner.history[-1].when if runner.history else 0.0,
                   tuple((aid, s.matched, s.last_run, s.tripped) for aid, s in runner.states.items()))
        cached = self._views.get(which)
        if cached is None or cached[0] != version:
            cached = (version, AutomationsView(runner.paused, runner.paused_since, frozen_map(runner.statuses()), tuple(runner.history)))
            self._views[which] = cached
        return cached[1]

    # ------------------------------------------------------------ house lights set by inputs

    def _start_input_house(self, inp: Input, now: float, fade: float) -> None:
        """An input that goes on Base takes over the house lights it mentions: their switch goes on
        and their level goes to the input's value (with its transition's fade)."""
        self._house_driven = {}
        for fixture_id, setting in inp.house.items():
            house = self._house.get(fixture_id)
            if house is None:
                continue
            with use_scope(scope_for(inp, 0.0, self._ctx.globals, self._ctx.audio)) as scope:
                scope.element = f"{inp.id}/house/{fixture_id}"
                level = evaluate(setting, 0.0, 0.0)
            house.level.set(clamp01(level), fade, now)
            if not house.on:
                house.on = True
                house.switch.set(1.0, fade, now)
            self._house_driven[fixture_id] = inp.id

    def _update_driven_house(self, now: float) -> None:
        """Keyframed or expression house levels keep following the input on Base."""
        current = self._base.current
        if not self._house_driven or current is None:
            return
        for fixture_id, input_id in list(self._house_driven.items()):
            if input_id != current.input.id:
                del self._house_driven[fixture_id]
                continue
            setting = current.input.house.get(fixture_id)
            house = self._house.get(fixture_id)
            if house is not None and setting is not None and is_dynamic(setting) and not house.level.moving(now):
                t = current.time(now)
                with use_scope(scope_for(current.input, t, self._ctx.globals, self._ctx.audio, current.memory)) as scope:
                    scope.element = f"{current.input.id}/house/{fixture_id}"
                    level = evaluate(setting, t, 0.0)
                house.level.set(clamp01(level), 0.0, now)

    # ------------------------------------------------------------ show variables ("globals" in the files)

    def _set_variable(self, name: str, raw: Any) -> None:
        var = next((g for g in self.show.globals if g.name == name), None)
        if var is None:
            log_info(f"There's no show variable called {name!r} (in {self.show.name})", key=f"no-variable-{name}")
            return
        value = coerce_global(var, raw, current=self._globals.get(name))
        if value is None:
            log_info(f"{raw!r} doesn't fit the show variable {name!r} ({var.type})", key=f"bad-variable-{name}")
            return
        if self._globals.get(name) != value:
            self._globals[name] = value
            self._ctx.globals = frozen_map(self._globals)

    # ------------------------------------------------------------ show changes

    def _swap_show(self, show: Show) -> None:
        old, self.show = self.show, show
        if show.globals != old.globals:
            self._globals = reconcile_globals(show, old, self._globals)
            self._ctx.globals = frozen_map(self._globals)
        self._sync_house({})
        lights = lights_for(show.fixtures, self._lights)
        if lights is not self._lights:
            self._lights = lights
            self._last_canvas = lights.empty()
        self._ctx.lights = self._picture_ctx.lights = lights
        self._ctx.folder = self._picture_ctx.folder = show.folder
        self._base.update_inputs(show.inputs)
        self._overlay.update_inputs(show.inputs)
        if self._solo is not None and self._solo.input.id in show.inputs and not self._solo_fixed:
            self._solo.input = show.inputs[self._solo.input.id]
        self.sources.set_keep(self._ndi_names())
        if show.identify_seed != old.identify_seed:
            self._identify_seed = show.identify_seed  # (Shuffle, and its undo / redo: the lights match the legend)
        self._refresh_identify()

    def _load_show(self, show: Show, now: float) -> None:
        """Another show: the lights freeze on what they show (Base and the Overlay become one still look on Base,
        held until one of the new show's inputs is sent, which transitions from it). Nothing of the old show plays
        on (its videos and feeds are let go; a feed the new show uses too stays connected). The house lights and
        Master keep their levels. The new show's variables come back as they were when it was last loaded, and its
        automations are checked straight away, "When this show loads" with them. A fade to black that's under way
        carries on to black."""
        old = self.show
        if show.folder and _folder_key(show.folder) == _folder_key(old.folder):
            self._swap_show(show)  # (the same show again: just its latest version)
            return
        solo, self._solo = self._solo, None
        self._solo_fixed = False
        base = self._base
        fading_to_black = (solo is None and base.current is None and base.held is None and base.transitioning(now)
                           and not self._overlay.lit(now, including_fades=False))
        if fading_to_black:  # (a Fade to black, say: frozen now, the lights would stay as they are this moment)
            held = None
            if isinstance(base.outgoing, Instance):  # (Stop: the old show's input fading out, held still as it fades)
                outgoing, contexts = base.outgoing, self._contexts()
                for ctx in contexts:
                    ctx.globals, ctx.audio = self._ctx.globals, self._ctx.audio
                base.outgoing = frozen_mix({layer_kind(ctx): render_input(outgoing, now, ctx) for ctx in contexts}, contexts,
                                           name=old.name)
        else:
            held = None if self._black_now(now) and solo is None else self._still_mix(now, solo)
            base.current = None
            base.outgoing = None
            base.transition = Transition("cut", 0.0)
        base.held = held
        self._overlay.clear_now()
        self._house_driven.clear()
        if old.folder:
            self._show_globals[_folder_key(old.folder)] = saveable(self._globals)
        self._swap_show(show)
        self._globals = initial_globals(show, self._show_globals.pop(_folder_key(show.folder), {}))
        self._ctx.globals = frozen_map(self._globals)
        # the new show's automations start afresh, every time it loads: checked straight away, with catch-up (so a
        # time range that's under way runs: automations override the freeze)
        paused, since = self._global_runner.paused, self._global_runner.paused_since
        self._runner = AutomationRunner()
        self._runner.paused, self._runner.paused_since = paused, since
        self._show_events = {"show_loads"}
        self._pending_load = None
        self._auto_key = None
        self._feeds_until = now + FEED_WAIT  # (its feeds are still connecting)
        log_info(f"Show “{show.name}” loaded ({show.folder}); the lights hold what they showed until one of its inputs is sent"
                 if held is not None else f"Show “{show.name}” loaded ({show.folder})")

    def _refresh_identify(self) -> None:
        colours = identify_colours([fx.id for fx in self.show.colour_fixtures()], self._identify_seed)
        self._identify_map = {light_id: rgb for light_id, (_name, rgb) in colours.items()}

    def _restore_buses(self, live: LiveState, initial_base: str | None, now: float) -> None:
        def instance(saved) -> Instance | None:
            if not isinstance(saved, dict) or not isinstance(saved.get("input"), str):
                return None
            inp = self.show.inputs.get(saved["input"])
            if inp is None:
                return None
            offset = saved.get("time", 0.0)
            offset = float(offset) if isinstance(offset, (int, float)) and not isinstance(offset, bool) else 0.0
            return Instance(inp, now - max(0.0, offset))

        if live.base:
            self._base.current = instance(live.base)
        elif live.frozen:  # another show's look, held when this one loaded (no input of this show sent since)
            self._base.held = self._frozen_from_live(live.frozen)
        elif not live.show and initial_base and initial_base in self.show.inputs:
            self._base.current = Instance(self.show.inputs[initial_base], now)  # the very first run
        saved = live.overlay if isinstance(live.overlay, Mapping) else {}
        opacity = saved.get("opacity", 1.0)
        self._overlay_opacity.set(clamp01(float(opacity)) if isinstance(opacity, (int, float)) and not isinstance(opacity, bool)
                                  else 1.0, 0.0, now)
        for item in saved.get("copies") or ():
            restored = instance(item)
            if restored is not None:
                copy = self._overlay.send(restored.input, now)
                if copy is not None:  # (as it was: shown at once, its clock carrying on)
                    copy.instance = restored
                    copy.reveal_from = copy.reveal_to = 1.0
        self._overlay.set_on(saved.get("on", True) is not False, now - SWITCH_FADE)

    @staticmethod
    def _frozen_from_live(saved: Any) -> Frozen | None:
        """A held look from live.json: {"from": show name, "colours": {light id: [r, g, b, a]}}."""
        colours = saved.get("colours") if isinstance(saved, Mapping) else None
        if not isinstance(colours, Mapping):
            return None
        ids, columns = [], []
        for light_id, values in colours.items():
            if isinstance(values, (list, tuple)) and len(values) == 4 and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                ids.append(str(light_id))
                columns.append([clamp01(float(v)) for v in values])
        if not ids:
            return None
        canvas = np.array(columns, dtype=np.float32).T  # (4, lights)
        return Frozen({"lights": canvas}, name=str(saved.get("from") or ""), ids=tuple(ids))

    def _ndi_names(self) -> set[str]:
        """The NDI feeds to keep connected: the show's inputs', and those its automations and the global ones look at."""
        show = self.show
        names: set[str] = set()
        for inp in show.inputs.values():
            names |= ndi_sources_used(inp.tracks)
        # feeds that automations look at ("An NDI feed is there") and no input uses are kept connected too
        lowered = {n.strip().lower() for n in names}
        for automation in (*show.automations.values(), *self.global_automations.automations.values()):
            for cond in automation.conditions if automation.enabled else ():
                wanted = str(cond.get("source") or "").strip() if cond.get("type") == "ndi" else ""
                key = wanted.lower()
                if key and not any(key in n or n in key for n in lowered):
                    names.add(wanted)
                    lowered.add(key)
        return names

    def _sync_house(self, settings: Mapping[str, HouseSetting]) -> None:
        """Match the house-light list to the show (new ones start off, at full)."""
        wanted = [fx.id for fx in self.show.house_fixtures()]
        for fid in wanted:
            if fid not in self._house:
                self._house[fid] = _House(settings.get(fid, HouseSetting(level=1.0, on=False)))
        for fid in list(self._house):
            if fid not in wanted:
                del self._house[fid]

    # ------------------------------------------------------------ publishing

    def is_running(self) -> bool:
        """The engine thread has been started and not stopped (the window watches it for getting stuck)."""
        thread = getattr(self, "_thread", None)
        return bool(getattr(self, "_running", False)) and thread is not None and thread.is_alive()

    def _audio_rate(self) -> float:
        rate = getattr(self.audio, "rate", None)
        try:
            return float(rate()) if callable(rate) else 0.0
        except Exception:
            return 0.0

    def _publish(self, now: float, colours: dict[str, RGB], house_out: dict[str, float]) -> None:
        self._last_publish = now
        self._seq += 1
        cutoff = time.perf_counter() - 1.0
        while self._tick_stamps and self._tick_stamps[0] < cutoff:
            self._tick_stamps.popleft()
        times = list(self._tick_times)
        house = {
            fid: HouseView(level=h.level.value(now), on=h.on, out=house_out.get(fid, 0.0))
            for fid, h in self._house.items()
        }
        current = self._base.current
        live_inputs = set()
        playing: dict[str, float] = {}
        if current is not None:
            live_inputs.add(current.input.id)
            progress = input_progress(current.input, current.time(now))
            if progress is not None:
                playing[current.input.id] = progress
        layer = self._overlay
        copies = []
        for copy in layer.shown():
            progress = copy.progress(now)
            copies.append(CopyView(copy.number, copy.input.id, copy.input.name, progress, copy.tag))
            if layer.on:
                live_inputs.add(copy.input.id)
                if progress is not None:
                    playing.setdefault(copy.input.id, progress)
        overlay = OverlayView(layer.on, layer.switch_visibility(now), self._overlay_opacity.value(now), tuple(copies),
                              sum(1 for copy in layer.copies if copy.leaving))
        try:
            ndi = self.sources.statuses()
        except Exception as exc:
            log_exception("NDI status", exc)
            ndi = {}
        sending: dict[str, bool] = {}
        for name, status in ndi.items():  # by the name asked for, and by the full name found on the network
            on = getattr(status, "state", "") == "connected"
            for key in (str(name).lower(), str(getattr(status, "found_name", "") or "").lower()):
                if key:
                    sending[key] = sending.get(key, False) or on
        self._ndi_sending = sending
        in_use = self._base.inputs_in_use() | self._overlay.inputs_in_use(now)
        if self._solo is not None:
            in_use.add(self._solo.input.id)
        held = self._base.held if current is None else None
        if current is not None:
            base_name = current.input.name
        elif held is not None:
            base_name = f"frozen (from {held.name})" if held.name else "frozen"
        else:
            base_name = "Black"
        frame = EngineFrame(
            seq=self._seq,
            colours=frozen_map(colours),
            house=frozen_map(house),
            master=self._master.value(now),
            colour_black=self.colour_black(now),
            output=self.output.status(),
            base=base_name,
            base_id=current.input.id if current is not None else "",
            base_progress=self._base.progress(now),
            playing=frozen_map(playing),
            audio=self._ctx.audio,
            audio_state=getattr(self.audio, "state", "off") if self.audio is not None else "off",
            audio_detail=getattr(self.audio, "detail", "") if self.audio is not None else "",
            audio_rate=self._audio_rate(),
            overlay=overlay,
            solo=self._solo.input.id if self._solo is not None else "",
            live_inputs=frozenset(live_inputs),
            in_use=frozenset(in_use),
            identify=self._identify,
            identify_seed=self._identify_seed,
            testing=self._test_colour is not None,
            ndi=frozen_map(ndi),
            preview=self._preview,
            preview_seq=self._preview_seq,
            element_faults=frozen_map(dict(self._ctx.faults)),
            variables=self._ctx.globals,
            automations=self._automations_view(),
            global_automations=self._view_of(self._global_runner, "global"),
            show_folder=self.show.folder,
            frozen_from=held.name if held is not None else "",
            countdown=self._countdown_view(now),
            tick_ms=1000.0 * sum(times) / len(times) if times else 0.0,
            tick_max_ms=1000.0 * max(times) if times else 0.0,
            tick_rate=float(len(self._tick_stamps)),
            faults=self._faults,
            restarts=self._restarts,
            live=self.live_state(now),
        )
        self.frames.put(frame)

    # ------------------------------------------------------------ threads

    def _start_loop(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        period = 1.0 / self._rate
        next_tick = time.perf_counter()
        while self._running:
            late = time.perf_counter() - next_tick
            self._lateness.append(max(0.0, late))
            if late > LATE_TICK:
                self._late_until = time.perf_counter() + 1.0
            try:
                self.tick()
            except Exception as exc:
                self._faults += 1
                log_exception("engine tick", exc)
            self._heartbeat = time.perf_counter()
            next_tick += period
            now = time.perf_counter()
            if next_tick < now - 0.25:  # the PC was busy: carry on from now instead of rushing
                next_tick = now
            sleep_until(next_tick, lambda: self._running)

    def _watch(self) -> None:
        while not self._stop_event.wait(WATCHDOG_INTERVAL):
            with self._lock:
                if not self._running:
                    return
                thread = self._thread
                if thread is not None and not thread.is_alive():
                    self._restarts += 1
                    log_info("The engine thread stopped unexpectedly, so it was restarted.")
                    self._heartbeat = time.perf_counter()
                    self._start_loop()
                    continue
            if time.perf_counter() - self._heartbeat > STALL_SECONDS:
                log_info("The engine hasn't ticked for over 2 seconds.", key="engine-stall")
