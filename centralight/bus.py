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

The same buses work out the lights (a context with per_light: layers (4, lights)) and draw
pictures for people (layers (4, h, w)); a mix frozen mid-transition keeps one of each.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .compositor import blend, over, transparent
from .elements import RenderContext, blend_mode, render_element
from .log import log_exception
from .model import Input, Transition, element_span, input_time
from .transitions import mix, reveal, seed_for
from .variables import scope_for, use_scope

FINISHED_MARGIN = 1e-9  # seconds: a transition this close to its end counts as finished


@dataclass
class Instance:
    """One running copy of an input."""

    input: Input
    started: float  # engine time when it was sent
    memory: dict = field(default_factory=dict, compare=False, repr=False)  # where its formulas' cycles are

    def time(self, now: float) -> float:
        return max(0.0, now - self.started)


@dataclass
class Frozen:
    """A mix held still (at the moment a transition was interrupted): for the lights and for each picture size that
    was being drawn then (one that wasn't is left out: it shows nothing)."""

    canvases: dict
    name: str = ""


def layer_kind(ctx: RenderContext):
    """What a context's layers are: "lights", or a picture's (width, height)."""
    return "lights" if ctx.per_light else (ctx.width, ctx.height)


def empty_layer(ctx: RenderContext) -> np.ndarray:
    """Nothing: no light lit, or a transparent picture."""
    if ctx.per_light:
        return ctx.lights.empty()
    return transparent(ctx.height, ctx.width)


def track_elements(track, t: float, inp: Input) -> list:
    """The elements showing at input time t on one track: the track's element (infinite), none, one, or
    two while one crosses into the next (the later two, if more overlap)."""
    if not track:
        return []
    if inp.infinite:
        return [track[0]]
    showing = []
    for element in track:
        start, end = element_span(element, inp)
        if start <= t < end:
            showing.append((start, element))
    showing.sort(key=lambda item: item[0])
    return [element for _start, element in showing[-2:]]


def active_element(track, t: float, infinite: bool, inp: Input | None = None):
    """The element showing at input time t on one track (the newer one while two cross)."""
    if inp is None:
        inp = Input(id="-", name="-", infinite=infinite, length=float("inf"))
    showing = track_elements(track, t, inp)
    return showing[-1] if showing else None


def render_input(instance: Instance, now: float, ctx: RenderContext) -> np.ndarray:
    """All tracks of an input at its own time, track 1 at the bottom. Its local variables are
    worked out first; expressions see them, the globals and the time (`time`: the input's;
    `t` and keyframes: each element's own, from its start)."""
    inp = instance.input
    t = input_time(inp, instance.time(now))
    canvas = None
    ctx.memory = instance.memory
    ctx.now = now
    scope = scope_for(inp, t, ctx.globals, ctx.audio, instance.memory)
    with use_scope(scope):
        for track in inp.tracks:
            showing = track_elements(track, t, inp)
            if not showing:
                continue
            layers = [(element, _element_layer(inp, element, t, ctx, scope)) for element in showing]
            if len(layers) == 2:  # the later element crosses in over the earlier one
                (old, old_layer), (new, new_layer) = layers
                old_end = element_span(old, inp)[1]
                new_start = element_span(new, inp)[0]
                progress = (t - new_start) / max(old_end - new_start, 1e-6)
                spec = new.get("in") if hasattr(new.get("in"), "get") else {}
                blank = empty_layer(ctx)
                layer = mix(str(spec.get("kind", "fade")), old_layer if old_layer is not None else blank,
                            new_layer if new_layer is not None else blank, min(1.0, max(0.0, progress)),
                            direction=float(spec.get("direction", 0.0)), softness=float(spec.get("softness", 0.25)),
                            owner=_owner(ctx), seed=seed_for(f"{inp.id}/{new.get('id')}"), cache=ctx.cache,
                            points=_points(ctx))
                mode = blend_mode(new if progress >= 0.5 else old)
            else:
                element, layer = layers[0]
                if layer is None:
                    continue
                if not inp.infinite:
                    layer = _in_and_out(inp, element, layer, t, ctx)
                mode = blend_mode(element)
            canvas = blend(canvas, layer, mode)
    return canvas if canvas is not None else empty_layer(ctx)


def _element_layer(inp: Input, element, t: float, ctx: RenderContext, scope) -> np.ndarray | None:
    """One element drawn at input time t (its keyframes and `t` count from its own start)."""
    ctx.time = t - element_span(element, inp)[0] if not inp.infinite else t
    key = f"{inp.id}/{element.get('id', '?')}"
    scope.element = key
    ctx.element_key = key
    try:
        return render_element(element, ctx)
    except Exception as exc:  # a broken element draws nothing; the rest of the input carries on
        if key not in ctx.faults:
            log_exception(f"element {key}", exc)
        ctx.faults[key] = str(exc) or type(exc).__name__
        return None


def _in_and_out(inp: Input, element, layer: np.ndarray, t: float, ctx: RenderContext) -> np.ndarray:
    """An element of a timed input appearing ("in") and going ("out") with its own transitions."""
    start, end = element_span(element, inp)
    for which, into in (("in", t - start), ("out", end - t)):
        spec = element.get(which)
        if not hasattr(spec, "get"):
            continue
        duration = spec.get("duration", 0.0)
        if not isinstance(duration, (int, float)) or duration <= 0 or into >= duration:
            continue
        layer = reveal(str(spec.get("kind", "fade")), layer, max(0.0, into / duration),
                       direction=float(spec.get("direction", 0.0)), softness=float(spec.get("softness", 0.25)),
                       owner=_owner(ctx), seed=seed_for(f"{inp.id}/{element.get('id')}/{which}"), cache=ctx.cache,
                       points=_points(ctx))
    return layer


def _render_source(source: Instance | Frozen | None, now: float, ctx: RenderContext) -> np.ndarray:
    if source is None:
        return empty_layer(ctx)
    if isinstance(source, Frozen):
        canvas = source.canvases.get(layer_kind(ctx))
        blank = empty_layer(ctx)
        # (frozen before a light was added or taken away, it doesn't fit the lights now: nothing, for the rest of it)
        return canvas if canvas is not None and canvas.shape == blank.shape else blank
    return render_input(source, now, ctx)


def _owner(ctx: RenderContext) -> np.ndarray | None:
    """Which light each light (or each pixel of a picture: its nearest light) goes with, for Random."""
    lights = ctx.lights
    if lights is None:
        return None
    if ctx.per_light:
        return lights.indices
    key = (lights.key, ctx.width, ctx.height)
    cached = ctx.cache.get("nearest")
    if cached is None or cached[0] != key:  # (only the newest: the lights moving doesn't pile them up)
        cached = (key, lights.nearest_map(ctx.width, ctx.height))
        ctx.cache["nearest"] = cached
    return cached[1]


def _points(ctx: RenderContext) -> tuple[np.ndarray, np.ndarray] | None:
    return (ctx.lights.x, ctx.lights.y) if ctx.per_light else None


def _contexts(ctx) -> tuple:
    if ctx is None:
        return ()
    return tuple(ctx) if isinstance(ctx, (tuple, list)) else (ctx,)


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
        nothing changed (an infinite input re-sent). ctx: the context, or contexts (the lights' and the
        pictures'), to freeze a mix in if a transition is interrupted."""
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
        contexts = _contexts(ctx)
        if self.transitioning(now) and contexts:
            self.outgoing = Frozen({layer_kind(c): self.render(now, c) for c in contexts})  # freeze the mix as it is now
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
                   owner=_owner(ctx), seed=self._seed, cache=ctx.cache, points=_points(ctx))

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

    def clear_now(self) -> None:
        """Off at once, with nothing left in it (its look went into a Cut / Fade to black's still mix)."""
        self.content.current = None
        self.content.outgoing = None
        self.content.transition = Transition("cut", 0.0)
        self.on = False
        self.reveal_from = self.reveal_to = 0.0
        self.reveal_transition = Transition("cut", 0.0)

    def off(self, now: float, cut: bool = False) -> None:
        if not self.on:
            return
        transition = self.content.current.input.transition if self.content.current is not None else Transition()
        self._start_reveal(0.0, now, Transition("cut", 0.0) if cut else transition)

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
                           owner=_owner(ctx), seed=seed_for(f"overlay{self.number}"), cache=ctx.cache, points=_points(ctx))
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
