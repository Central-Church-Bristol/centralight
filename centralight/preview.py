"""Previews: what an input would make the lights do, without putting it on air.

Uses exactly the same per-light maths as the engine, so a tile's preview matches what the
lights will show. NDI elements use the newest picture from the feed. (Smoothing is left
out: a preview is a single moment.)
"""

from __future__ import annotations

from typing import Any

from .bus import Instance, render_input
from .elements import RenderContext
from .lights import lights_for
from .model import Input, Show
from .output import RGB


class Previewer:
    """Works out inputs' light colours (keeps its own cache, so call it from one thread)."""

    def __init__(self, sources: Any | None = None) -> None:
        self._ctx = RenderContext(0, 0, sources, video_threaded=False, per_light=True, smoothing=False)
        self._memories: dict[str, dict] = {}  # per input: its formulas' cycle positions

    def colours(self, show: Show, inp: Input, t: float = 0.0, variables=None) -> dict[str, RGB]:
        ctx = self._ctx
        ctx.lights = lights_for(show.fixtures, ctx.lights)
        ctx.folder = show.folder
        if variables is not None:
            ctx.globals = variables
        try:
            layer = render_input(Instance(inp, 0.0, self._memories.setdefault(inp.id, {})), t, ctx)
        except Exception:
            return {}
        return ctx.lights.colours(layer)
