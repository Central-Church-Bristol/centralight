"""The engine: works out what every light should do, 40 times a second, on its own thread.

Each tick:
1. apply the commands that arrived (fader moves, Cut / Fade to black, sending inputs...)
2. render Base (a transition mixes two inputs) and Overlays 1-4 on top, into a
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
from dataclasses import dataclass, field
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
    system_uptime,
)
from .bus import Bus, Frozen, Instance, OverlaySlot, composite, layer_kind, render_input
from .commands import (
    HOUSE_SLEW,
    HOUSE_SWITCH_FADE,
    QUIT_FADE,
    ClearBase,
    Command,
    FlashLights,
    OverlayOff,
    PowerEvent,
    ResetAutomation,
    RunAutomation,
    SendToBase,
    SendToOverlay,
    SetAutomationEnabled,
    SetAutomationsPaused,
    SetHouse,
    SetIdentify,
    SetMaster,
    SetOverlayOpacity,
    SetSolo,
    SetTestColour,
    SetVariable,
    SwapShow,
    SwitchLayout,
    ToBlack,
)
from .compositor import to_rgb8
from .elements import RenderContext, ndi_sources_used
from .live_state import HouseSetting, LiveState
from .log import log_exception, log_info
from .model import EMPTY, Input, Show, Transition, frozen_map, has_finished, input_progress
from .video_source import videos
from .ndi_source import SourceStatus
from .output import RGB, OutputStatus, encode_dmx
from .params import evaluate, is_dynamic
from .variables import coerce_global, initial_globals, reconcile_globals, saveable, scope_for, use_scope
from .lights import CANVAS_H, CANVAS_W, identify_colours, lights_for
from .slot import LatestSlot
from .timing import sleep_until

TICK_RATE = 40
AUDIO_OFF = MappingProxyType({"level": 0.0, "bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0, "pitch": 0.0})
PUBLISH_INTERVAL = 0.1  # seconds between EngineFrames for the screen
PREVIEW_INTERVAL = 0.2  # seconds between preview pictures for the screen (only while a window shows one)
LATE_PREVIEW_INTERVAL = 1.0  # ...while ticks run late (the lights come first; the picture slows, it doesn't stop)
# commands a person sends (the window, the Stream Deck, the phone page) that mean they've taken charge of the lights:
# after a "Turn lights off" quit, automations are held back only until one of these comes
PERSON_TAKES_CHARGE = (SendToBase, ClearBase, SendToOverlay, SetHouse, ToBlack, RunAutomation, SetSolo)
LATE_TICK = 0.005  # seconds: a tick that starts this much after its time is late (pictures for the screen wait then)
TIMING_WINDOW = 60  # seconds of tick timings kept for the lateness figures
TIDY_INTERVAL = 1.0
WATCHDOG_INTERVAL = 1.0
STALL_SECONDS = 2.0
FLASH_STEP = 0.15  # a flash is on/off/on/off/on/off in steps this long
FLASH_STEPS = 6
OVERLAY_SLOTS = 4
WAKE_GAP = 60.0  # seconds: a jump this big in the clocks means the PC slept (or the clock was changed)
WAKE_DEBOUNCE = 60.0  # seconds: wake signals this close together are the same wake
STARTUP_HOLD = 3.0  # seconds: at most this long at startup, nothing is sent while the screen feed's first picture comes
FEED_WAIT = 10.0  # seconds: at startup, an automation about a feed waits at most this long for it to start sending
BLACK: RGB = (0.0, 0.0, 0.0)
WHITE: RGB = (1.0, 1.0, 1.0)


def _brings_lights_up(command: Any) -> bool:
    """An automation action that means "lights on": an input to Base or an overlay, or a house light up."""
    if isinstance(command, SendToBase):
        return True
    if isinstance(command, SendToOverlay):
        return getattr(command, "state", None) != "off"
    if isinstance(command, SetHouse):
        level_up = command.level is None or command.level > 0.0
        return (command.on is True and level_up) or (command.on is None and (command.level or 0.0) > 0.0)
    return False


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
class OverlayView:
    input_id: str = ""
    name: str = ""
    on: bool = False
    visible: float = 0.0  # 0-1, moves while it turns on or off
    opacity: float = 1.0


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
    colour_black: bool = False  # nothing on Base or in the overlays (after a Cut / Fade to black, say)
    output: OutputStatus = OutputStatus()
    base: str = "Black"  # name of what's on Base
    base_id: str = ""  # its input id ("" = nothing)
    base_progress: float | None = None  # 0-1 while Base is transitioning
    playing: Mapping[str, float] = EMPTY  # timed inputs on Base or in an overlay -> how far through (0-1)
    audio: Mapping[str, float] = EMPTY  # audio.level and friends, now
    audio_state: str = "off"  # off, listening, error
    audio_detail: str = ""  # what it's listening to, or what's wrong
    audio_rate: float = 0.0  # blocks of sound analysed a second (0 = none arriving)
    overlays: tuple[OverlayView, ...] = ()
    solo: str = ""  # input shown alone on the lights ("" = none)
    live_inputs: frozenset[str] = frozenset()  # inputs on Base or in an overlay right now
    identify: bool = False
    identify_seed: int = 7
    testing: bool = False  # test colours are on
    ndi: Mapping[str, SourceStatus] = EMPTY  # wanted NDI name (lower case) -> status
    preview: np.ndarray | None = field(default=None, compare=False)  # (90, 160, 3) uint8: the picture
    preview_seq: int = 0
    element_faults: Mapping[str, str] = EMPTY  # "input/element" -> what went wrong
    variables: Mapping[str, Any] = EMPTY  # global variables' current values (number or (r, g, b))
    automations: AutomationsView = AutomationsView()
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
    ) -> None:
        """output: a DmxOutputThread (anything with submit(bytes) and status()).
        sources: a SourceManager (NDI feeds), or None for no video.
        audio: an audio.AudioCapture (anything with latest() -> {"level": ..., ...}, state and detail), or None.
        initial_base: an input to put on Base when there's no live state (the very first run).
        wall / local_time / uptime: the real clock, local time (with the clock changes) and how long
        Windows has been running, for automations (tests pass fakes).
        started_by_windows: Windows started CentraLight at sign-in, which counts as "The PC starts" (with Fast
        Startup, a shut-down PC doesn't really restart, so Windows' running time doesn't go back to 0)."""
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
        self._house: dict[str, _House] = {}
        self._house_driven: dict[str, str] = {}  # house light id -> the input that set it
        self._sync_house(live.house)
        # the lights' colours are worked out at their dots every tick; a picture is drawn only for a window showing one
        self._lights = lights_for(show.fixtures, None)
        self._ctx = RenderContext(0, 0, self.sources, folder=show.folder, lights=self._lights, per_light=True)
        self._picture_ctx = RenderContext(CANVAS_W, CANVAS_H, self.sources, folder=show.folder, lights=self._lights)
        self._globals = initial_globals(show, live.globals)
        self._ctx.globals = frozen_map(self._globals)
        # automations
        self._wall = wall
        self._local_time = local_time
        self._uptime = uptime
        self._runner = AutomationRunner()
        self._runner.restore(live.automations, wall(), saved_at=live.saved_at)
        self._first_check = True
        self._woke = False
        self._last_wake: float | None = None
        self._started_by_windows = started_by_windows
        self._prev_mono: float | None = None
        self._prev_wall: float | None = None
        self._auto_key: tuple | None = None
        self._ndi_sending: dict[str, bool] = {}
        self._automation_view = AutomationsView()
        self._automation_version: tuple | None = None
        self.gui_requests: queue.SimpleQueue[tuple[str, dict]] = queue.SimpleQueue()  # (who, action) for the window
        self._base = Bus("base")
        self._overlays = [OverlaySlot(n) for n in range(1, OVERLAY_SLOTS + 1)]
        self._overlay_opacity = [Ramp(1.0) for _ in range(OVERLAY_SLOTS)]
        self._solo: Instance | None = None
        self._solo_fixed = False  # the solo input came with its command (not from the show)
        self._restore_buses(live, initial_base, now)
        self._last_canvas = self._lights.empty()
        # At startup the lights keep showing what they showed (the dongle repeats it) until the feeds Base and the
        # overlays use have their first picture, instead of going black for a second or two meanwhile
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
        self.sources.set_keep(self._ndi_names(show))

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
        overlays: list[dict | None] = []
        for slot, ramp in zip(self._overlays, self._overlay_opacity):
            current = slot.content.current
            if slot.on and current is not None:
                overlays.append({"input": current.input.id, "time": round(current.time(now), 3), "opacity": ramp.target})
            else:
                overlays.append(None)
        return LiveState(
            show=self.show.folder,
            master=self._master.target,
            quit_dark=self._quit_dark,
            quit_at=(self._quit_at or 0.0) if self._quit_dark else 0.0,
            house={fid: HouseSetting(h.level.target, h.on) for fid, h in self._house.items()},
            base=base,
            overlays=overlays,
            globals=saveable(self._globals),
            automations=self._runner.to_dict(),
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
        """A timed input that stops at its end leaves Base (or its overlay) when it gets there."""
        changed = False
        current = self._base.current
        if current is not None and has_finished(current.input, current.time(now)) and not self._base.transitioning(now):
            self._base.clear(now, cut=True)
            changed = True
        for slot in self._overlays:
            inside = slot.content.current
            if slot.on and inside is not None and has_finished(inside.input, inside.time(now)):
                slot.off(now, cut=True)
                changed = True
        return changed

    def _render(self, now: float) -> np.ndarray:
        """Base and the overlays (or the solo input). If rendering fails, the last good picture is held."""
        try:
            if self._solo is not None:
                canvas = render_input(self._solo, now, self._ctx)
            else:
                canvas = self._base.render(now, self._ctx)
                layers = []
                for slot, ramp in zip(self._overlays, self._overlay_opacity):
                    slot.opacity = ramp.value(now)
                    layers.append(slot.render(now, self._ctx))
                canvas = composite(canvas, layers)
        except Exception as exc:
            self._faults += 1
            log_exception("rendering", exc)
            return self._last_canvas
        self._last_canvas = canvas
        return canvas

    def _to_black(self, seconds: float, everything: bool, now: float) -> None:
        """Cut or fade the colour lights to black: the mix as it is now (Base and the overlays, as one still look)
        fades to nothing on Base, and the overlays are cleared at once (their look is in it). everything: the house
        lights' switches go off too, over the same time, keeping their levels; each remembers how, so its next
        plain switch on brings it back the same way."""
        base = self._base
        showing = base.current is not None or base.transitioning(now) or any(slot.visibility(now) > 0.0 for slot in self._overlays)
        solo, self._solo = self._solo, None  # (the Editor's "Show on lights" ends too: black means black)
        if seconds > 0.0 and (showing or solo is not None):
            canvases = {}
            for ctx in self._contexts():
                ctx.globals, ctx.audio = self._ctx.globals, self._ctx.audio
                if solo is not None:
                    canvases[layer_kind(ctx)] = render_input(solo, now, ctx)  # what the lights were showing
                else:
                    canvases[layer_kind(ctx)] = composite(base.render(now, ctx), [slot.render(now, ctx) for slot in self._overlays])
            base.outgoing = Frozen(canvases)
            base.transition = Transition("fade", seconds)
            base.t_start = now
        else:
            base.outgoing = None
            base.transition = Transition("cut", 0.0)
        base.current = None
        for slot in self._overlays:
            slot.clear_now()
        self._house_driven.clear()
        if everything:
            for house in self._house.values():
                if house.on:
                    house.on = False
                    house.switch.set(0.0, seconds, now)
                    house.mirror = seconds

    def colour_black(self, now: float | None = None) -> bool:
        """Nothing on Base or in any overlay, and no fade still going: the colour lights are black."""
        now = self._clock() if now is None else now
        base = self._base
        return (base.current is None and not base.transitioning(now)
                and not any(slot.visibility(now) > 0.0 or slot.revealing(now) for slot in self._overlays))

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
            return composite(canvas, [slot.render(now, ctx) for slot in self._overlays])
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
                if self._quit_dark and isinstance(command, PERSON_TAKES_CHARGE):
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
            slot = self._slot(command.slot)
            inp = self._input(command.input_id)
            if slot is not None and inp is not None:
                slot.send(inp, now, self._contexts(), command.state)
        elif isinstance(command, OverlayOff):
            slot = self._slot(command.slot)
            if slot is not None:
                slot.off(now)
        elif isinstance(command, SetOverlayOpacity):
            if 1 <= command.slot <= OVERLAY_SLOTS:
                self._overlay_opacity[command.slot - 1].set(clamp01(command.level), command.fade, now)
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
        elif isinstance(command, SetVariable):
            self._set_variable(command.name, command.value)
        elif isinstance(command, SetAutomationsPaused):
            paused = (not self._runner.paused) if command.paused is None else bool(command.paused)
            self._runner.set_paused(paused, self._wall())
        elif isinstance(command, RunAutomation):
            automation = self.show.automations.get(command.automation_id)
            if automation is not None:
                for who, action in self._runner.run_now(automation, now, self._wall()):
                    self._do_action(who, action, now)
        elif isinstance(command, ResetAutomation):
            self._runner.reset(command.automation_id)
        elif isinstance(command, SetAutomationEnabled):
            self.gui_requests.put(("Stream Deck", {"do": "automation", "automation": command.automation_id, "enabled": command.enabled}))
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

    def _slot(self, number: int) -> OverlaySlot | None:
        return self._overlays[number - 1] if 1 <= number <= OVERLAY_SLOTS else None

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
        runner = self._runner
        overlays = tuple(
            slot.content.current.input.id if slot.on and slot.content.current is not None else ""
            for slot in self._overlays
        )
        base = self._base.current.input.id if self._base.current is not None else ""
        colour_black = self.colour_black(now)
        key = (base, overlays, colour_black, id(self._ctx.globals), tuple(sorted(self._ndi_sending.items())), id(self.show.automations))
        events = set()
        if self._first_check:
            events.add("app_started")
            if self._started_by_windows or self._uptime() < STARTUP_WINDOW:
                events.add("pc_startup")
        if self._woke:
            events.add("pc_woke")
        ran = False
        if events or key != self._auto_key or runner.wants_check(now):
            self._auto_key = key
            # just after starting, the NDI feeds are still connecting: until one sends (or FEED_WAIT has passed)
            # "feed lost" isn't true yet, so an automation about it waits
            situation = Situation(
                now=wall, local=self._local_time(wall), base=base, overlays=overlays, variables=self._ctx.globals,
                ndi=dict(self._ndi_sending), colour_black=colour_black, audio=dict(self._ctx.audio), events=frozenset(events),
                ndi_known=now >= self._feeds_until,
                quit_dark_since=self._quit_at if self._quit_dark else None,
            )
            self._first_check = False
            self._woke = False
            for who, action in runner.check(self.show, situation, now):
                self._do_action(who, action, now)
                ran = True
        for who, action in runner.due(now):
            self._do_action(who, action, now)
            ran = True
        return ran

    def _do_action(self, who: str, action: dict, now: float) -> None:
        action = dict(action)
        lifts = bool(action.pop("_lifts", False))
        try:
            self._do_action_now(who, action, now, lifts)
        except Exception as exc:  # a wrong value in an automation's file must never stop the engine
            log_exception(f"automation {who}: doing {action.get('do')!r}", exc)

    def _do_action_now(self, who: str, action: dict, now: float, lifts: bool = False) -> None:
        if action.get("do") in GUI_ACTIONS:
            name = self.show.automations[who].name if who in self.show.automations else who
            self.gui_requests.put((name, action))
            return
        command = action_command(action, self.show)
        if command is None:
            log_info(f"Automation {who}: couldn't {describe_action(action, self.show)} (is it still there?)", key=f"automation-skip-{who}")
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
        for owner in (self.output, self.sources):
            reconnect = getattr(owner, "reconnect", None)
            if reconnect is not None:
                try:
                    reconnect()
                except Exception as exc:
                    log_exception("reconnecting after sleep", exc)

    def _automations_view(self) -> AutomationsView:
        runner = self._runner
        version = (runner.paused, len(runner.history), runner.history[-1].when if runner.history else 0.0,
                   tuple((aid, s.matched, s.last_run, s.tripped) for aid, s in runner.states.items()))
        if version != self._automation_version:
            self._automation_version = version
            self._automation_view = AutomationsView(runner.paused, runner.paused_since, frozen_map(runner.statuses()), tuple(runner.history))
        return self._automation_view

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

    # ------------------------------------------------------------ global variables

    def _set_variable(self, name: str, raw: Any) -> None:
        var = next((g for g in self.show.globals if g.name == name), None)
        if var is None:
            log_info(f"There's no global variable called {name!r}", key=f"no-variable-{name}")
            return
        value = coerce_global(var, raw, current=self._globals.get(name))
        if value is None:
            log_info(f"{raw!r} doesn't fit the global variable {name!r} ({var.type})", key=f"bad-variable-{name}")
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
        for slot in self._overlays:
            slot.update_inputs(show.inputs)
        if self._solo is not None and self._solo.input.id in show.inputs and not self._solo_fixed:
            self._solo.input = show.inputs[self._solo.input.id]
        self.sources.set_keep(self._ndi_names(show))
        if show.identify_seed != old.identify_seed:
            self._identify_seed = show.identify_seed  # (Shuffle, and its undo / redo: the lights match the legend)
        self._refresh_identify()

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
        elif not live.show and initial_base and initial_base in self.show.inputs:
            self._base.current = Instance(self.show.inputs[initial_base], now)  # the very first run
        for slot, ramp, saved in zip(self._overlays, self._overlay_opacity, live.overlays):
            restored = instance(saved)
            if restored is not None:
                slot.content.current = restored
                slot.on = True
                slot.reveal_from = slot.reveal_to = 1.0
                opacity = saved.get("opacity", 1.0)
                ramp.set(clamp01(float(opacity)) if isinstance(opacity, (int, float)) else 1.0, 0.0, now)

    @staticmethod
    def _ndi_names(show: Show) -> set[str]:
        names: set[str] = set()
        for inp in show.inputs.values():
            names |= ndi_sources_used(inp.tracks)
        # feeds that automations look at ("An NDI feed is there") and no input uses are kept connected too
        lowered = {n.strip().lower() for n in names}
        for automation in show.automations.values():
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
        overlays = []
        live_inputs = set()
        playing: dict[str, float] = {}
        if current is not None:
            live_inputs.add(current.input.id)
            progress = input_progress(current.input, current.time(now))
            if progress is not None:
                playing[current.input.id] = progress
        for slot, ramp in zip(self._overlays, self._overlay_opacity):
            inside = slot.content.current
            visible = slot.visibility(now)
            if inside is not None and (slot.on or visible > 0.0):
                live_inputs.add(inside.input.id)
                progress = input_progress(inside.input, inside.time(now))
                if progress is not None:
                    playing.setdefault(inside.input.id, progress)
                overlays.append(OverlayView(inside.input.id, inside.input.name, slot.on, visible, ramp.value(now)))
            else:
                overlays.append(OverlayView(opacity=ramp.value(now)))
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
        frame = EngineFrame(
            seq=self._seq,
            colours=frozen_map(colours),
            house=frozen_map(house),
            master=self._master.value(now),
            colour_black=self.colour_black(now),
            output=self.output.status(),
            base=current.input.name if current is not None else "Black",
            base_id=current.input.id if current is not None else "",
            base_progress=self._base.progress(now),
            playing=frozen_map(playing),
            audio=self._ctx.audio,
            audio_state=getattr(self.audio, "state", "off") if self.audio is not None else "off",
            audio_detail=getattr(self.audio, "detail", "") if self.audio is not None else "",
            audio_rate=self._audio_rate(),
            overlays=tuple(overlays),
            solo=self._solo.input.id if self._solo is not None else "",
            live_inputs=frozenset(live_inputs),
            identify=self._identify,
            identify_seed=self._identify_seed,
            testing=self._test_colour is not None,
            ndi=frozen_map(ndi),
            preview=self._preview,
            preview_seq=self._preview_seq,
            element_faults=frozen_map(dict(self._ctx.faults)),
            variables=self._ctx.globals,
            automations=self._automations_view(),
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
