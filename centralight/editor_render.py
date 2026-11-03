"""Draws the input being edited for the Editor's two viewports, on its own thread.

- Result: the whole input at 640x360, plus each light's colour worked out exactly as
  the engine does it (at 160x90, with the same sampler), for the light strip.
- Selected: the selected element on its own (before its mask), and its mask.

Requests are "latest wins": while a slider is being dragged only the newest state is
drawn. An input that moves (a live feed, keyframes) is redrawn about 8 times a second.
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
from .elements import RenderContext, element_is_dynamic, element_keep, render_element
from .input_edit import find_element
from .log import log_exception
from .model import Show
from .output import RGB
from .sampler import CANVAS_H, CANVAS_W, EDITOR_H, EDITOR_W, sampler_for
from .slot import LatestSlot
from .variables import scope_for, use_scope

LIVE_REFRESH = 1 / 8  # seconds between redraws of a moving input


@dataclass(frozen=True)
class EditorRequest:
    show: Show
    input_id: str
    element_id: str | None
    started: float  # time.monotonic() when the input's clock started
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


def _lower_thread_priority() -> None:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
    except (AttributeError, OSError):
        pass


class EditorRenderer:
    def __init__(self, sources: Any | None = None, width: int = EDITOR_W, height: int = EDITOR_H) -> None:
        self.pictures: LatestSlot[EditorPicture] = LatestSlot()
        self._requests: LatestSlot[EditorRequest] = LatestSlot()
        self._sources = sources
        self._big = RenderContext(width, height, sources)
        self._small = RenderContext(CANVAS_W, CANVAS_H, sources)
        self._running = False
        self._thread: threading.Thread | None = None
        self._seq = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="editor-render", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._requests.put(None)  # wake the thread
        if self._thread is not None:
            self._thread.join(2.0)

    def request(self, show: Show, input_id: str, element_id: str | None, started: float,
                globals_: Mapping[str, Any] = MappingProxyType({})) -> None:
        self._requests.put(EditorRequest(show, input_id, element_id, started, globals_))

    def render_now(self, request: EditorRequest) -> EditorPicture | None:
        """Draw one request straight away (the thread uses this; so can tests)."""
        inp = request.show.inputs.get(request.input_id)
        if inp is None:
            return None
        show = request.show
        for ctx in (self._big, self._small):
            ctx.folder = show.folder
            ctx.sampler = sampler_for(show.fixtures, ctx.sampler, ctx.width, ctx.height)
            ctx.globals = request.globals
        now = time.monotonic()
        instance = Instance(inp, request.started)
        result = render_input(instance, now, self._big)
        small = render_input(instance, now, self._small)
        colours = self._small.sampler.colours(small)
        selected = keep = None
        found = find_element(inp, request.element_id) if request.element_id else None
        if found is not None:
            element = found[2]
            t = instance.time(now)
            self._big.time = t
            scope = scope_for(inp, t, request.globals, self._big.audio)
            scope.element = f"{inp.id}/{element.get('id', '?')}"
            with use_scope(scope):
                layer = render_element(element, self._big, use_mask=False)
                mask = element_keep(element, self._big)
            if layer is not None:
                selected = to_rgba8(layer)
            if mask is not None:
                keep = (np.clip(mask, 0.0, 1.0) * 255 + 0.5).astype(np.uint8)
        self._seq += 1
        return EditorPicture(self._seq, inp.id, request.element_id, to_rgb8(result), selected, keep, colours, instance.time(now))

    def _run(self) -> None:
        _lower_thread_priority()
        version = 0
        current: EditorRequest | None = None
        while self._running:
            timeout = LIVE_REFRESH if current is not None and self._moving(current) else None
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

    @staticmethod
    def _moving(request: EditorRequest) -> bool:
        inp = request.show.inputs.get(request.input_id)
        if inp is None:
            return False
        return any(element_is_dynamic(element) for track in inp.tracks for element in track)
