"""Draws the input being edited for the Editor's two viewports, on its own thread.

- Result: the whole input at 640x360, plus each light's colour worked out exactly as
  the engine does it (the same per-light maths), for the light strip and the dots.
- Selected: the selected element on its own (before its mask), and its mask; for an
  Image, Video or NDI element, also each light's patch at the element's Area.

Requests are "latest wins": while a slider is being dragged only the newest state is
drawn. An input that moves (a live feed, keyframes) is redrawn about 8 times a second,
and about twice a second while the engine is running late: the lights always come first.
The thread runs below normal priority, so the engine and ProPresenter always come first.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import numpy as np

from .bus import Instance, render_input
from .compositor import to_rgb8, to_rgba8
from .elements import RenderContext, element_is_dynamic, element_keep, param, render_element
from .input_edit import find_element
from .light_elements import PICTURE_KINDS, patch_boxes
from .lights import ASPECT, DEFAULT_AREA, EDITOR_H, EDITOR_W, lights_for
from .log import log_exception
from .model import Show, element_span, input_time
from .output import RGB
from .slot import LatestSlot
from .variables import scope_for, use_scope

LIVE_REFRESH = 1 / 8  # seconds between redraws of a moving input
LATE_REFRESH = 1 / 2  # ...while the engine is running late (the lights come first)


@dataclass(frozen=True)
class EditorRequest:
    show: Show
    input_id: str
    element_id: str | None
    at: float  # the time to draw, in seconds since the input started (the Editor's playhead)
    globals: Mapping[str, Any] = MappingProxyType({})  # global variables' current values (from the engine)


@dataclass(frozen=True)
class EditorPicture:
    seq: int
    input_id: str
    element_id: str | None
    result: np.ndarray  # (360, 640, 3) uint8
    selected: np.ndarray | None  # (360, 640, 4) premultiplied uint8, or None
    keep: np.ndarray | None  # (360, 640) uint8 mask (255 = shown), or None for no mask
    colours: dict[str, RGB]  # each light's colour from this input alone
    time: float  # the input's time when drawn
    patches: tuple = ()  # the selected picture element's patch for each light: (id, x0, y0, x1, y1), 0-1 of the screen


def _lower_thread_priority() -> None:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
    except (AttributeError, OSError):
        pass


class EditorRenderer:
    def __init__(self, sources: Any | None = None, width: int = EDITOR_W, height: int = EDITOR_H,
                 late: Any | None = None) -> None:
        """late: tells whether the engine is running late (then fewer pictures are drawn)."""
        self.pictures: LatestSlot[EditorPicture] = LatestSlot()
        self._requests: LatestSlot[EditorRequest] = LatestSlot()
        self._sources = sources
        self._late = late
        self._big = RenderContext(width, height, sources, video_threaded=False)  # exact video frames at the playhead
        self._lights = RenderContext(0, 0, sources, video_threaded=False, per_light=True)
        self._running = False
        self._thread: threading.Thread | None = None
        self._seq = 0
        self._memory_key: str | None = None  # the input whose formulas' cycle positions are remembered
        self._stopping = threading.Event()  # (wakes a back-off wait when stopping)
        self._memory: dict = {}
        self._lock = threading.Lock()  # the thread and render_sync() share the drawing contexts

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="editor-render", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._stopping.set()
        self._requests.put(None)  # wake the thread
        if self._thread is not None:
            self._thread.join(2.0)

    def idle(self) -> None:
        """Nothing to draw (the Editor isn't on screen): the thread waits until the next request."""
        self._requests.put(None)

    def request(self, show: Show, input_id: str, element_id: str | None, at: float,
                globals_: Mapping[str, Any] = MappingProxyType({})) -> None:
        self._requests.put(EditorRequest(show, input_id, element_id, at, globals_))

    def render_now(self, request: EditorRequest) -> EditorPicture | None:
        """Draw one request straight away (the thread uses this; so can tests)."""
        with self._lock:
            return self._render(request)

    def _render(self, request: EditorRequest) -> EditorPicture | None:
        inp = request.show.inputs.get(request.input_id)
        if inp is None:
            return None
        show = request.show
        lights = lights_for(show.fixtures, self._lights.lights)
        for ctx in (self._big, self._lights):
            ctx.folder = show.folder
            ctx.lights = lights
            ctx.globals = request.globals
        at = max(0.0, request.at)
        if self._memory_key != inp.id:
            self._memory_key, self._memory = inp.id, {}
        instance = Instance(inp, 0.0, self._memory)
        result = render_input(instance, at, self._big)
        colours = lights.colours(render_input(instance, at, self._lights))
        selected = keep = None
        patches: tuple = ()
        found = find_element(inp, request.element_id) if request.element_id else None
        if found is not None:
            element = found[2]
            t = input_time(inp, at)
            self._big.time = t - element_span(element, inp)[0] if not inp.infinite else t
            scope = scope_for(inp, t, request.globals, self._big.audio, self._memory)
            scope.element = f"{inp.id}/{element.get('id', '?')}"
            self._big.element_key = scope.element  # (its own memory: a keyed speed, say, not the last element drawn)
            with use_scope(scope):
                layer = render_element(element, self._big, use_mask=False)
                mask = element_keep(element, self._big)
                if element.get("kind") in PICTURE_KINDS:
                    area = param(element, "area", self._big.time, DEFAULT_AREA)
                    x0, y0, x1, y1 = patch_boxes(lights.x, lights.y, area)
                    patches = tuple((light_id, float(x0[i]), float(y0[i] / ASPECT), float(x1[i]), float(y1[i] / ASPECT))
                                    for i, light_id in enumerate(lights.ids))
            if layer is not None:
                selected = to_rgba8(layer)
            if mask is not None:
                keep = (np.clip(mask, 0.0, 1.0) * 255 + 0.5).astype(np.uint8)
        self._seq += 1
        return EditorPicture(self._seq, inp.id, request.element_id, to_rgb8(result), selected, keep, colours, input_time(inp, at),
                             patches)

    def _run(self) -> None:
        _lower_thread_priority()
        version = 0
        current: EditorRequest | None = None
        while self._running:
            try:
                timeout = (LATE_REFRESH if self._engine_late() else LIVE_REFRESH) if current is not None and self._moving(current) else None
            except Exception as exc:  # never let the thread end: the viewports would freeze until a restart
                log_exception("editor render (is it moving?)", exc)
                timeout = LIVE_REFRESH
            new_version, request = self._requests.wait_newer(version, timeout)
            if not self._running:
                return
            if new_version != version:
                version = new_version
                current = request
            if current is None:
                continue
            try:
                picture = self.render_now(current)
            except Exception as exc:
                log_exception("editor render", exc)
                time.sleep(0.2)
                continue
            if picture is not None:
                self.pictures.put(picture)
            if self._engine_late():  # the lights come first: while the engine runs late, draw at most twice a second
                self._stopping.wait(LATE_REFRESH)

    def _engine_late(self) -> bool:
        try:
            return bool(self._late()) if self._late is not None else False
        except Exception:
            return False

    @staticmethod
    def _moving(request: EditorRequest) -> bool:
        inp = request.show.inputs.get(request.input_id)
        if inp is None:
            return False
        return any(element_is_dynamic(element) for track in inp.tracks for element in track)
