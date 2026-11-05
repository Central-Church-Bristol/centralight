"""Buses: what's playing. Base holds one input; the Overlay stacks copies of inputs on top (M14).

Base:
- Sending an input transitions from what's live now, with the input's transition.
- Re-sending an infinite input that's already there does nothing; a timed one restarts.
- Sending during a transition freezes the picture as it is at that moment, and
  transitions from that.

The Overlay (transparent parts show what's below):
- Each send adds a copy of the input on top, newest on top, appearing with the input's
  transition (dip appears as a fade). Copies that touch different lights don't cancel out.
- An input has at most 4 copies there at once.
- A copy leaves the way it came in: when it's cleared, when a held Send to Overlay button
  is let go, and (a timed input) when its timeline has played once.
- Its switch hides the whole layer without emptying it, and it has an opacity.

Every running copy of an input keeps its own clock, starting at 0 when it's sent.

The same buses work out the lights (a context with per_light: layers (4, lights)) and draw
pictures for people (layers (4, h, w)); a mix frozen mid-transition keeps one of each.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from .compositor import blend, over, transparent
from .elements import RenderContext, blend_mode, render_element
from .log import log_exception
from .model import END_EPSILON, Input, Transition, element_span, input_time
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
    """A mix held still (at the moment a transition was interrupted, a Cut / Fade to black, or a show loading): for the
    lights, and for each picture size that was being drawn then. ids: the lights its "lights" layer is for, so after a
    light is added or taken away (or another show's lights come) each light that's still there keeps its colour; a
    picture that wasn't being drawn is drawn from those colours."""

    canvases: dict
    name: str = ""
    ids: tuple[str, ...] | None = None
    cache: dict = field(default_factory=dict, compare=False, repr=False)  # its colours for other sets of lights


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
        return _frozen_layer(source, ctx)
    return render_input(source, now, ctx)


def frozen_for_lights(frozen: Frozen, ids: tuple[str, ...]) -> np.ndarray | None:
    """A still mix's colours for these lights (4, lights): each light it knew keeps its colour, a new one is unlit.
    None if it has no colours for the lights."""
    canvas = frozen.canvases.get("lights")
    if canvas is None:
        return None
    ids = tuple(ids)
    if frozen.ids is None or tuple(frozen.ids) == ids:
        return canvas if canvas.shape[1] == len(ids) else None
    cached = frozen.cache.get(ids)
    if cached is None:
        column = {light_id: i for i, light_id in enumerate(frozen.ids)}
        cached = np.zeros((canvas.shape[0], len(ids)), dtype=canvas.dtype)
        for i, light_id in enumerate(ids):
            if light_id in column:
                cached[:, i] = canvas[:, column[light_id]]
        frozen.cache.clear()  # (only the newest: lights moving around doesn't pile them up)
        frozen.cache[ids] = cached
    return cached


def _frozen_layer(frozen: Frozen, ctx: RenderContext) -> np.ndarray:
    blank = empty_layer(ctx)
    if ctx.per_light:
        colours = frozen_for_lights(frozen, ctx.lights.ids) if ctx.lights is not None else None
        return colours if colours is not None and colours.shape == blank.shape else blank
    canvas = frozen.canvases.get(layer_kind(ctx))
    if canvas is not None and canvas.shape == blank.shape:
        return canvas
    # a picture that wasn't being drawn when it froze: each pixel takes its nearest light's frozen colour
    owner = _owner(ctx)
    colours = frozen_for_lights(frozen, ctx.lights.ids) if ctx.lights is not None and ctx.lights.ids else None
    if owner is None or colours is None or colours.shape[1] == 0:
        return blank
    return colours[:, owner].astype(blank.dtype, copy=False)


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


def frozen_mix(layers: dict, contexts, name: str = "") -> Frozen:
    """A still mix from layers drawn in these contexts ({layer_kind: layer})."""
    ids = next((tuple(c.lights.ids) for c in contexts if c.per_light and c.lights is not None), None)
    return Frozen(layers, name=name, ids=ids)


class Bus:
    def __init__(self, name: str) -> None:
        self.name = name
        self.current: Instance | None = None
        self.outgoing: Instance | Frozen | None = None
        # a still look shown while nothing plays (another show's mix, frozen when it loaded), until something's sent
        self.held: Frozen | None = None
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
        self.held = None
        return True

    def clear(self, now: float, ctx: RenderContext | None = None, cut: bool = False) -> None:
        """Fade (or cut) to nothing, with the outgoing input's transition."""
        if self.current is None and self.held is None and not self.transitioning(now):
            return
        transition = self.current.input.transition if self.current is not None else Transition()
        self._start_transition(now, ctx, cut, transition, "clear")
        self.current = None
        self.held = None

    def _start_transition(self, now: float, ctx: RenderContext | None, cut: bool, transition: Transition, seed_text: str) -> None:
        if cut or transition.kind == "cut" or transition.duration <= 0:
            self.outgoing = None
            self.transition = Transition("cut", 0.0)
            return
        contexts = _contexts(ctx)
        if self.transitioning(now) and contexts:
            # freeze the mix as it is now
            self.outgoing = frozen_mix({layer_kind(c): self.render(now, c) for c in contexts}, contexts)
        else:
            self.outgoing = self.current if self.current is not None else self.held
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
        current = _render_source(self.current if self.current is not None else self.held, now, ctx)
        if progress is None:
            return current
        before = _render_source(self.outgoing, now, ctx)
        tr = self.transition
        return mix(tr.kind, before, current, progress, direction=tr.direction, softness=tr.softness,
                   owner=_owner(ctx), seed=self._seed, cache=ctx.cache, points=_points(ctx))

    def inputs_in_use(self) -> set[str]:
        return {i.input.id for i in (self.current, self.outgoing) if isinstance(i, Instance)}


COPIES_PER_INPUT = 4  # copies of one input on the Overlay at once (its tile has room for four boxes)
MAX_COPIES = 16  # copies of all inputs at once (each one is drawn every tick)
SWITCH_FADE = 0.5  # seconds: the Overlay's on / off switch fades the whole layer


@dataclass
class OverlayCopy:
    """One send to the Overlay: a running copy of an input, on top of the copies sent before it."""

    number: int  # unique while CentraLight runs: the tiles' copy boxes (and the API) name a copy by it
    instance: Instance
    tag: str = ""  # who sent it: a held Send to Overlay button clears its own copy when it's let go
    transition: Transition = field(default_factory=Transition)  # how it came in, and so how it goes
    reveal_from: float = 0.0  # 0 = hidden, 1 = shown: where its appearing (or leaving) started...
    reveal_to: float = 1.0  # ...and where it's going
    reveal_start: float = 0.0
    leaving: bool = False
    hold_at: float | None = None  # a timed copy that has played: shown at its last moment while it leaves

    @property
    def input(self) -> Input:
        return self.instance.input

    def revealing(self, now: float) -> bool:
        tr = self.transition
        return tr.kind != "cut" and tr.duration > 0 and now < self.reveal_start + tr.duration - FINISHED_MARGIN

    def visibility(self, now: float) -> float:
        """0 = hidden, 1 = fully shown (moving while it appears or leaves)."""
        if not self.revealing(now):
            return self.reveal_to
        progress = min(1.0, max(0.0, (now - self.reveal_start) / self.transition.duration))
        return self.reveal_from + (self.reveal_to - self.reveal_from) * progress

    def gone(self, now: float) -> bool:
        return self.leaving and not self.revealing(now)

    def progress(self, now: float) -> float | None:
        """How far through a timed input this copy is (0-1; it plays once on the Overlay), None for an infinite one."""
        inp = self.input
        if inp.infinite:
            return None
        return min(1.0, max(0.0, self.instance.time(now) / max(float(inp.length), 1e-3)))

    def leave(self, now: float, cut: bool = False) -> None:
        """Go the way it came in (its input's transition), or at once."""
        if self.leaving:
            return
        self.reveal_from = self.visibility(now)
        self.reveal_to = 0.0
        self.reveal_start = now
        if cut:
            self.transition = Transition("cut", 0.0)
        self.leaving = True

    def render(self, now: float, ctx: RenderContext) -> np.ndarray | None:
        visible = self.visibility(now)
        if visible <= 0.0 and not self.revealing(now):
            return None
        hold = self.hold_at
        if hold is None and not self.input.infinite:  # (it plays once: cleared while it plays, it stops at its end)
            hold = max(0.0, float(self.input.length) - END_EPSILON)
        at = now if hold is None else min(now, self.instance.started + hold)
        layer = render_input(self.instance, at, ctx)
        if visible < 1.0:
            tr = self.transition  # (the reveal's own progress, from hidden to shown, whichever way it's going)
            layer = reveal(tr.kind, layer, visible, direction=tr.direction, softness=tr.softness, owner=_owner(ctx),
                           seed=seed_for(f"overlay copy {self.number}"), cache=ctx.cache, points=_points(ctx))
        return layer


class OverlayLayer:
    """The Overlay (M14): copies of inputs stacked on top of Base, oldest at the bottom. Each send adds a copy; each
    copy leaves the way it came in. Its switch hides the whole layer without emptying it (sending turns it on again),
    and it has an opacity."""

    def __init__(self) -> None:
        self.copies: list[OverlayCopy] = []  # bottom first: the newest is on top
        self.opacity = 1.0
        self.on = True
        self._switch_from = 1.0
        self._switch_start = 0.0
        self._numbers = itertools.count(1)

    # ---------------------------------------------------------------- what's there

    def shown(self) -> list[OverlayCopy]:
        """The copies that are there (not on their way out), bottom first."""
        return [copy for copy in self.copies if not copy.leaving]

    def count(self, input_id: str) -> int:
        return sum(1 for copy in self.copies if not copy.leaving and copy.input.id == input_id)

    def input_ids(self) -> frozenset[str]:
        """Inputs with a copy there while the switch is on (what "this input is in the Overlay" means)."""
        return frozenset(copy.input.id for copy in self.shown()) if self.on else frozenset()

    def switch_visibility(self, now: float) -> float:
        """The switch's fade: 1 = on, 0 = hidden."""
        target = 1.0 if self.on else 0.0
        if now >= self._switch_start + SWITCH_FADE:
            return target
        progress = max(0.0, (now - self._switch_start) / SWITCH_FADE)
        return self._switch_from + (target - self._switch_from) * progress

    def switching(self, now: float) -> bool:
        return now < self._switch_start + SWITCH_FADE and self.switch_visibility(now) != (1.0 if self.on else 0.0)

    def lit(self, now: float, including_fades: bool = True) -> bool:
        """Something of it shows on the lights now (including_fades: or is still appearing or fading out)."""
        if self.switch_visibility(now) <= 0.0 and not (including_fades and self.switching(now)):
            return False
        return any(copy.visibility(now) > 0.0 or (including_fades and copy.revealing(now)) for copy in self.copies)

    def inputs_in_use(self, now: float) -> set[str]:
        """Inputs whose pictures and videos are on the lights through it now (appearing or leaving too)."""
        if self.switch_visibility(now) <= 0.0 and not self.switching(now):
            return set()
        return {copy.input.id for copy in self.copies if copy.visibility(now) > 0.0 or copy.revealing(now)}

    # ---------------------------------------------------------------- sending and clearing

    def send(self, inp: Input, now: float, tag: str = "") -> OverlayCopy | None:
        """A new copy on top, appearing with the input's transition (None: the input has 4 there already, or the
        Overlay is full). Sending turns the switch on."""
        self.tidy(now)
        if self.count(inp.id) >= COPIES_PER_INPUT or len(self.copies) >= MAX_COPIES:
            return None
        transition = inp.transition if inp.transition.kind != "cut" and inp.transition.duration > 0 else Transition("cut", 0.0)
        copy = OverlayCopy(next(self._numbers), Instance(inp, now), tag=tag, transition=transition, reveal_start=now,
                           reveal_from=0.0, reveal_to=1.0)
        self.copies.append(copy)
        self.set_on(True, now)
        return copy

    def clear_copy(self, number: int, now: float, cut: bool = False) -> bool:
        return self._clear([c for c in self.copies if c.number == number], now, cut)

    def clear_tag(self, tag: str, now: float, cut: bool = False) -> bool:
        return bool(tag) and self._clear([c for c in self.copies if c.tag == tag], now, cut)

    def clear_input(self, input_id: str, now: float, cut: bool = False) -> bool:
        return self._clear([c for c in self.copies if c.input.id == input_id], now, cut)

    def clear_all(self, now: float, cut: bool = False) -> bool:
        return self._clear(list(self.copies), now, cut)

    def _clear(self, copies: list[OverlayCopy], now: float, cut: bool) -> bool:
        cleared = False
        for copy in copies:
            if not copy.leaving:
                copy.leave(now, cut)
                cleared = True
        self.tidy(now)
        return cleared

    def clear_now(self) -> None:
        """Empty at once (its look went into a Cut / Fade to black's still mix, or another show is loading)."""
        self.copies.clear()

    def set_on(self, on: bool, now: float) -> None:
        if on == self.on:
            return
        self._switch_from = self.switch_visibility(now)
        self._switch_start = now
        self.on = on

    def finish_timed(self, now: float) -> bool:
        """Timed copies leave by themselves once their timeline has played (whatever its end: on the Overlay a timed
        input plays once). True if one started leaving."""
        changed = False
        for copy in self.copies:
            inp = copy.input
            if not copy.leaving and not inp.infinite and copy.instance.time(now) >= inp.length:
                copy.hold_at = max(0.0, float(inp.length) - END_EPSILON)
                copy.leave(now)
                changed = True
        return changed

    def tidy(self, now: float) -> None:
        """Copies that have finished leaving go."""
        if any(copy.gone(now) for copy in self.copies):
            self.copies = [copy for copy in self.copies if not copy.gone(now)]

    def update_inputs(self, inputs, now: float | None = None) -> None:
        """Edited inputs play live; a deleted one's copies leave, the way they came in (nothing else could clear them:
        its tile has gone)."""
        for copy in self.copies:
            if copy.input.id in inputs:
                copy.instance.input = inputs[copy.input.id]
            elif now is not None:
                copy.leave(now)

    # ---------------------------------------------------------------- drawing

    def render(self, now: float, ctx: RenderContext) -> np.ndarray | None:
        self.tidy(now)
        if not self.copies:
            return None
        factor = self.switch_visibility(now) * self.opacity
        if factor <= 0.0:
            return None
        layer = None
        for copy in self.copies:
            part = copy.render(now, ctx)
            if part is not None:
                layer = part if layer is None else over(layer, part)
        if layer is None:
            return None
        return layer * factor if factor < 1.0 else layer


def composite(base: np.ndarray, overlays: list[np.ndarray | None]) -> np.ndarray:
    """Base with the layers on top (the Overlay), the last at the very top."""
    canvas = base
    for layer in overlays:
        if layer is not None:
            canvas = over(canvas, layer)
    return canvas
