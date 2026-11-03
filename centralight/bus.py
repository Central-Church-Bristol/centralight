"""Buses: what's playing. Base holds one input; Overlays 1-4 each hold one on top.

Base:
- Sending an input transitions from what's live now, with the input's transition.
- Re-sending an infinite input that's already there does nothing; a timed one restarts.
- Sending during a transition freezes the picture as it is at that moment, and
  transitions from that.

Overlays (4 is the top layer; transparent parts show the layers below):
- Sending an input to an empty slot reveals it with its transition (dip reveals as a fade).
- Sending the input that's already in the slot turns it off again (a toggle).
- Sending a different input to a slot that's on transitions between the two.
- Each slot has an opacity.

Every running copy of an input keeps its own clock, starting at 0 when it's sent.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .compositor import blend, over, transparent
from .elements import RenderContext, blend_mode, render_element
from .log import log_exception
from .model import Input, Transition
from .transitions import mix, reveal, seed_for
from .variables import scope_for, use_scope

FINISHED_MARGIN = 1e-9  # seconds: a transition this close to its end counts as finished


@dataclass
class Instance:
    """One running copy of an input."""

    input: Input
    started: float  # engine time when it was sent

    def time(self, now: float) -> float:
        return max(0.0, now - self.started)


@dataclass
class Frozen:
    """A picture held still (the mix at the moment a transition was interrupted)."""

    canvas: np.ndarray
    name: str = ""


def active_element(track, t: float, infinite: bool):
    """The element showing at input time t on one track (infinite mode: the track's element)."""
    if not track:
        return None
    if infinite:
        return track[0]
    for element in track:
        start = element.get("start", 0.0)
        end = element.get("end", float("inf"))
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and start <= t < end:
            return element
    return None


def render_input(instance: Instance, now: float, ctx: RenderContext) -> np.ndarray:
    """All tracks of an input at its own time, track 1 at the bottom. Its local variables are
    worked out first; expressions see them, the globals and the time."""
    t = instance.time(now)
    canvas = None
    scope = scope_for(instance.input, t, ctx.globals, ctx.audio)
    with use_scope(scope):
        for track in instance.input.tracks:
            element = active_element(track, t, instance.input.infinite)
            if element is None:
                continue
            ctx.time = t
            key = f"{instance.input.id}/{element.get('id', '?')}"
            scope.element = key
            try:
                layer = render_element(element, ctx)
            except Exception as exc:  # a broken element draws nothing; the rest of the input carries on
                if key not in ctx.faults:
                    log_exception(f"element {key}", exc)
                ctx.faults[key] = str(exc) or type(exc).__name__
                continue
            if layer is not None:
                canvas = blend(canvas, layer, blend_mode(element))
    return canvas if canvas is not None else transparent(ctx.height, ctx.width)


def _render_source(source: Instance | Frozen | None, now: float, ctx: RenderContext) -> np.ndarray:
    if source is None:
        return transparent(ctx.height, ctx.width)
    if isinstance(source, Frozen):
        return source.canvas
    return render_input(source, now, ctx)


def _owner(ctx: RenderContext) -> np.ndarray | None:
    return ctx.sampler.owner if ctx.sampler is not None else None


class Bus:
    def __init__(self, name: str) -> None:
        self.name = name
        self.current: Instance | None = None
        self.outgoing: Instance | Frozen | None = None
        self.transition = Transition("cut", 0.0)
        self.t_start = 0.0
        self._seed = 0

    def send(self, inp: Input, now: float, ctx: RenderContext | None = None, cut: bool = False,
             transition: Transition | None = None) -> bool:
        """Start inp on this bus (with its own transition, or the one given). Returns False if
        nothing changed (an infinite input re-sent)."""
        if self.current is not None and self.current.input.id == inp.id and inp.infinite and not self.transitioning(now):
            return False
        self._start_transition(now, ctx, cut, transition or inp.transition, inp.id)
        self.current = Instance(inp, now)
        return True

    def clear(self, now: float, ctx: RenderContext | None = None, cut: bool = False) -> None:
        """Fade (or cut) to nothing, with the outgoing input's transition."""
        if self.current is None and not self.transitioning(now):
            return
        transition = self.current.input.transition if self.current is not None else Transition()
        self._start_transition(now, ctx, cut, transition, "clear")
        self.current = None

    def _start_transition(self, now: float, ctx: RenderContext | None, cut: bool, transition: Transition, seed_text: str) -> None:
        if cut or transition.kind == "cut" or transition.duration <= 0:
            self.outgoing = None
            self.transition = Transition("cut", 0.0)
            return
        if self.transitioning(now) and ctx is not None:
            self.outgoing = Frozen(self.render(now, ctx))  # freeze the mix as it is now
        else:
            self.outgoing = self.current
        self.transition = transition
        self.t_start = now
        self._seed = seed_for(seed_text)

    def transitioning(self, now: float) -> bool:
        # (the tiny margin stops floating-point rounding leaving a transition 99.9999999 % done)
        return self.transition.duration > 0 and now < self.t_start + self.transition.duration - FINISHED_MARGIN

    def progress(self, now: float) -> float | None:
        """0-1 while a transition runs, else None."""
        if not self.transitioning(now):
            return None
        return (now - self.t_start) / self.transition.duration

    def update_inputs(self, inputs) -> None:
        """Edited inputs play live. A deleted input keeps playing until it's replaced."""
        for instance in (self.current, self.outgoing):
            if isinstance(instance, Instance) and instance.input.id in inputs:
                instance.input = inputs[instance.input.id]

    def render(self, now: float, ctx: RenderContext) -> np.ndarray:
        progress = self.progress(now)
        if progress is None:
            self.outgoing = None
        current = _render_source(self.current, now, ctx)
        if progress is None:
            return current
        before = _render_source(self.outgoing, now, ctx)
        tr = self.transition
        return mix(tr.kind, before, current, progress, direction=tr.direction, softness=tr.softness,
                   owner=_owner(ctx), seed=self._seed, cache=ctx.cache)

    def inputs_in_use(self) -> set[str]:
        return {i.input.id for i in (self.current, self.outgoing) if isinstance(i, Instance)}


class OverlaySlot:
    """One overlay slot: an input on top of Base, with on/off reveals and an opacity."""

    def __init__(self, number: int) -> None:
        self.number = number
        self.content = Bus(f"overlay{number}")
        self.opacity = 1.0
        self.on = False
        self.reveal_from = 0.0  # where the on/off animation started (0 = hidden, 1 = shown)
        self.reveal_to = 0.0
        self.reveal_start = 0.0
        self.reveal_transition = Transition("cut", 0.0)

    def input_id(self) -> str:
        return self.content.current.input.id if self.content.current is not None else ""

    def send(self, inp: Input, now: float, ctx: RenderContext | None = None, state: str | None = None) -> None:
        """Toggle rules: the same input again turns it off; another input replaces it."""
        already = self.on and self.content.current is not None and self.content.current.input.id == inp.id
        if state == "off" or (state is None and already):
            self.off(now)
            return
        if already:
            return  # state "on" and it's already on
        if self.on and self.content.current is not None:
            self.content.send(inp, now, ctx)  # swap inputs with the new one's transition
            return
        self.content.send(inp, now, ctx, cut=True)
        self._start_reveal(1.0, now, inp.transition)

    def off(self, now: float) -> None:
        if not self.on:
            return
        transition = self.content.current.input.transition if self.content.current is not None else Transition()
        self._start_reveal(0.0, now, transition)

    def _start_reveal(self, target: float, now: float, transition: Transition) -> None:
        self.reveal_from = self.visibility(now)
        self.reveal_to = target
        self.reveal_start = now
        self.reveal_transition = transition
        self.on = target > 0.0

    def visibility(self, now: float) -> float:
        """0 = hidden, 1 = fully shown (moves during the on/off transition)."""
        if not self.revealing(now):
            return self.reveal_to
        progress = min(1.0, max(0.0, (now - self.reveal_start) / self.reveal_transition.duration))
        return self.reveal_from + (self.reveal_to - self.reveal_from) * progress

    def revealing(self, now: float) -> bool:
        tr = self.reveal_transition
        return tr.kind != "cut" and tr.duration > 0 and now < self.reveal_start + tr.duration - FINISHED_MARGIN

    def render(self, now: float, ctx: RenderContext) -> np.ndarray | None:
        visible = self.visibility(now)
        if visible <= 0.0 and not self.revealing(now):
            if not self.on:
                self.content.current = None
                self.content.outgoing = None
            return None
        layer = self.content.render(now, ctx)
        tr = self.reveal_transition
        if visible < 1.0:
            # the reveal's own progress, from hidden (0) to shown (1), whichever way it's going
            layer = reveal(tr.kind, layer, visible, direction=tr.direction, softness=tr.softness,
                           owner=_owner(ctx), seed=seed_for(f"overlay{self.number}"), cache=ctx.cache)
        if self.opacity < 1.0:
            layer = layer * self.opacity
        return layer

    def update_inputs(self, inputs) -> None:
        self.content.update_inputs(inputs)

    def inputs_in_use(self) -> set[str]:
        return self.content.inputs_in_use()


def composite(base: np.ndarray, overlays: list[np.ndarray | None]) -> np.ndarray:
    """Base with the overlays on top, Overlay 1 first and Overlay 4 at the very top."""
    canvas = base
    for layer in overlays:
        if layer is not None:
            canvas = over(canvas, layer)
    return canvas
