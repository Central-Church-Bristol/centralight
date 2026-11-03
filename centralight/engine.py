"""The engine: works out what every light should do, 40 times a second, on its own thread.

Each tick:
1. apply the commands that arrived (fader moves, Blackout, sending inputs...)
2. render Base (a transition mixes two inputs) and Overlays 1-4 on top, into a
   160x90 picture. "Show on lights" (solo) replaces all of that with one input.
3. sample the picture: each colour light takes the average colour of its patch
4. Master (colour lights only)
5. Identify / test colours / flashes (the Layout page's tools)
6. the house lights (festoon, key lights) with their fades. An input can set them
   when it goes on Base; a manual fader move wins until the next input.
7. Blackout (always wins)
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
    lifts_restored_blackout,
    system_uptime,
)
from .bus import Bus, Instance, OverlaySlot, composite, render_input
from .commands import (
    BLACKOUT_FADE,
    HOUSE_SLEW,
    HOUSE_SWITCH_FADE,
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
    SetBlackout,
    SetHouse,
    SetIdentify,
    SetMaster,
    SetOverlayOpacity,
    SetSolo,
    SetTestColour,
    SetVariable,
    SwapShow,
    SwitchLayout,
)
from .compositor import to_rgb8, transparent
from .elements import RenderContext, ndi_sources_used
from .live_state import HouseSetting, LiveState
from .log import log_exception, log_info
from .model import EMPTY, Input, Show, frozen_map, has_finished, input_progress
from .video_source import videos
from .ndi_source import SourceStatus
from .output import RGB, OutputStatus, encode_dmx
from .params import evaluate, is_dynamic
from .variables import coerce_global, initial_globals, reconcile_globals, saveable, scope_for, use_scope
from .sampler import CANVAS_H, CANVAS_W, identify_colours, sampler_for
from .slot import LatestSlot
from .timing import sleep_until

TICK_RATE = 40
AUDIO_OFF = MappingProxyType({"level": 0.0, "bass": 0.0, "mid": 0.0, "treble": 0.0, "beat": 0.0, "pitch": 0.0})
PUBLISH_INTERVAL = 0.1  # seconds between EngineFrames for the screen
PREVIEW_INTERVAL = 0.2  # seconds between preview pictures for the screen
TIDY_INTERVAL = 1.0
WATCHDOG_INTERVAL = 1.0
STALL_SECONDS = 2.0
FLASH_STEP = 0.15  # a flash is on/off/on/off/on/off in steps this long
FLASH_STEPS = 6
OVERLAY_SLOTS = 4
WAKE_GAP = 60.0  # seconds: a jump this big in the clocks means the PC slept (or the clock was changed)
WAKE_DEBOUNCE = 60.0  # seconds: wake signals this close together are the same wake
STARTUP_HOLD = 3.0  # seconds: at most this long at startup, nothing is sent while the screen feed's first picture comes
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

    def out(self, now: float) -> float:
        return self.level.value(now) * self.switch.value(now)


@dataclass(frozen=True)
class HouseView:
    level: float  # fader position now (moves during fades)
    on: bool  # the switch
    out: float  # what's actually sent, after the switch fade and Blackout


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
    blackout: bool = False
    blackout_amount: float = 0.0  # 0 = normal, 1 = fully black (moves during the fade)
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
        self._blackout = bool(live.blackout)
        self._blackout_ramp = Ramp(1.0 if live.blackout else 0.0)
        # a Blackout from before the restart (e.g. quit with "Turn lights off") gives way to an automation that
        # brings the lights up, so an unattended Sunday morning isn't left dark
        self._blackout_restored = bool(live.blackout and live.blackout_by_quit)  # (never one a person clicked)
        self._blackout_by_quit = self._blackout_restored
        self._house: dict[str, _House] = {}
        self._house_driven: dict[str, str] = {}  # house light id -> the input that set it
        self._sync_house(live.house)
        self._sampler = sampler_for(show.fixtures, None)
        self._ctx = RenderContext(CANVAS_W, CANVAS_H, self.sources, folder=show.folder, sampler=self._sampler)
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
        self._last_canvas = transparent(CANVAS_H, CANVAS_W)
        # At startup the lights keep showing what they showed (the dongle repeats it) until the feeds Base and the
        # overlays use have their first picture, instead of going black for a second or two meanwhile
        self._holding = True
        self._hold_until = now + STARTUP_HOLD
        self._identify = False
        self._identify_seed = show.identify_seed
        self._identify_map: dict[str, RGB] = {}
        self._test_colour: RGB | None = None
        self._test_fixtures: frozenset[str] = frozenset()
        self._flashes: dict[str, float] = {}
        self._seq = 0
        self._preview_seq = 0
        self._preview: np.ndarray | None = None
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
        """Stop ticking. lights_off fades to black first (0.5 s). Returns the state to save."""
        was_dark, was_by_quit = self._blackout, self._blackout_by_quit  # (a Blackout someone clicked stays theirs)
        if lights_off and fade and self._running:
            self.submit(SetBlackout(on=True))
            deadline = time.perf_counter() + BLACKOUT_FADE + 0.5
            while time.perf_counter() < deadline:
                frame = self.frames.get()
                if frame is not None and frame.blackout and frame.blackout_amount >= 1.0:
                    break
                time.sleep(0.02)
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
            self._blackout = True
            # an automation after the restart may lift a quit's Blackout, never one someone had clicked before
            self._blackout_by_quit = was_by_quit if was_dark else True
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
            blackout=self._blackout,
            blackout_by_quit=self._blackout and self._blackout_by_quit,
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
        sampled = self._sampler.sample(canvas)
        master = self._master.value(now)
        colours: dict[str, RGB] = {}
        for light_id, (r, g, b) in zip(self._sampler.ids, sampled):
            colours[light_id] = (clamp01(r) * master, clamp01(g) * master, clamp01(b) * master)
        self._apply_overrides(colours, now)
        keep = 1.0 - self._blackout_ramp.value(now)
        out_colours = {fid: (r * keep, g * keep, b * keep) for fid, (r, g, b) in colours.items()}
        house_out = {fid: h.out(now) * keep for fid, h in self._house.items()}

        if self._holding and not (self._ctx.waiting and now < self._hold_until):
            self._holding = False  # every feed has a picture (or it's been long enough): from now on, always send
        if not self._holding:
            self.output.submit(encode_dmx(self.show.fixtures, out_colours, house_out))

        if now - self._last_tidy >= TIDY_INTERVAL:
            self._last_tidy = now
            self.sources.tidy()
            videos.tidy()
        if now - self._last_preview >= PREVIEW_INTERVAL:
            self._last_preview = now
            self._preview = to_rgb8(canvas)
            self._preview_seq += 1
        elapsed = time.perf_counter() - started
        self._tick_times.append(elapsed)
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
                fade = HOUSE_SWITCH_FADE if command.fade is None else command.fade
                house.switch.set(1.0 if on else 0.0, fade, now)
        elif isinstance(command, SetBlackout):
            target = (not self._blackout) if command.on is None else bool(command.on)
            self._blackout_restored = False  # someone (or an automation) has taken charge of it
            self._blackout_by_quit = False
            if target != self._blackout or command.instant:
                self._blackout = target
                self._blackout_ramp.set(1.0 if target else 0.0, 0.0 if command.instant else BLACKOUT_FADE, now)
        elif isinstance(command, SendToBase):
            inp = self._input(command.input_id)
            transition = command.transition or inp.transition if inp is not None else None
            if inp is not None and self._base.send(inp, now, self._ctx, cut=command.cut, transition=command.transition):
                self._start_input_house(inp, now, 0.0 if command.cut else transition.duration)
        elif isinstance(command, ClearBase):
            self._base.clear(now, self._ctx, cut=command.cut)
            self._house_driven.clear()
        elif isinstance(command, SendToOverlay):
            slot = self._slot(command.slot)
            inp = self._input(command.input_id)
            if slot is not None and inp is not None:
                slot.send(inp, now, self._ctx, command.state)
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
        key = (base, overlays, self._blackout, id(self._ctx.globals), tuple(sorted(self._ndi_sending.items())), id(self.show.automations))
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
            # just after starting, the NDI feeds are still connecting: until they report (or STARTUP_HOLD has
            # passed) "feed lost" isn't true yet, so automations about feeds wait
            ndi_known = now >= self._hold_until or (bool(self._ndi_sending) and all(self._ndi_sending.values()))
            situation = Situation(
                now=wall, local=self._local_time(wall), base=base, overlays=overlays, variables=self._ctx.globals,
                ndi=dict(self._ndi_sending), blackout=self._blackout, audio=dict(self._ctx.audio), events=frozenset(events),
                ndi_known=ndi_known,
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
        phase = action.pop("_phase", "")
        try:
            self._do_action_now(who, action, now, phase)
        except Exception as exc:  # a wrong value in an automation's file must never stop the engine
            log_exception(f"automation {who}: doing {action.get('do')!r}", exc)

    def _do_action_now(self, who: str, action: dict, now: float, phase: str = "") -> None:
        if action.get("do") in GUI_ACTIONS:
            name = self.show.automations[who].name if who in self.show.automations else who
            self.gui_requests.put((name, action))
            return
        command = action_command(action, self.show)
        if command is None:
            log_info(f"Automation {who}: couldn't {describe_action(action, self.show)} (is it still there?)", key=f"automation-skip-{who}")
            return
        lifts = lifts_restored_blackout(self.show.automations.get(who), self._runner.results(who))
        if self._blackout and self._blackout_restored and phase == "run" and lifts and _brings_lights_up(command):
            # a lights-off quit's Blackout gives way to a real run of an automation (e.g. Sunday 09:00), not to
            # "CentraLight starts" or a "when it stops" step
            log_info(f"Automation {who}: lifting the Blackout left from before CentraLight restarted")
            self._apply(SetBlackout(on=False), now)
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
        self._sampler = sampler_for(show.fixtures, self._sampler)
        self._ctx.sampler = self._sampler
        self._ctx.folder = show.folder
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
            blackout=self._blackout,
            blackout_amount=self._blackout_ramp.value(now),
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
