"""Previews: what an input would make the lights do, without putting it on air.

Uses exactly the same rendering and sampling as the engine, so a tile's preview matches
what the lights will show. NDI elements use the newest picture from the feed.
"""

from __future__ import annotations

from typing import Any

from .bus import Instance, render_input
from .elements import RenderContext
from .model import Input, Show
from .output import RGB
from .sampler import CANVAS_H, CANVAS_W, Sampler, sampler_for


class Previewer:
    """Renders inputs to light colours (keeps its own cache, so call it from one thread)."""

    def __init__(self, sources: Any | None = None) -> None:
        self._ctx = RenderContext(CANVAS_W, CANVAS_H, sources)
        self._sampler: Sampler | None = None
        self._memories: dict[str, dict] = {}  # per input: its formulas' cycle positions

    def colours(self, show: Show, inp: Input, t: float = 0.0, variables=None) -> dict[str, RGB]:
        self._sampler = sampler_for(show.fixtures, self._sampler)
        self._ctx.sampler = self._sampler
        self._ctx.folder = show.folder
        if variables is not None:
            self._ctx.globals = variables
        try:
            canvas = render_input(Instance(inp, 0.0, self._memories.setdefault(inp.id, {})), t, self._ctx)
        except Exception:
            return {}
        return self._sampler.colours(canvas)
