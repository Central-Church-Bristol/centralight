"""Live state: what the lights were doing when CentraLight last closed.

Saved to %LOCALAPPDATA%\\CentraLight\\live.json and restored *before* the first DMX
frame, so restarting the app never flashes the lights. It's app-wide: it says which show was
loaded last (that one opens next time), what was playing in it (or the look it held, frozen
from the show before), Master and the house lights, and the automations' state, the global
ones included.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .files import read_json, write_json_atomic
from .paths import live_state_path


@dataclass
class HouseSetting:
    level: float = 1.0  # fader position, 0-1
    on: bool = False  # the switch


@dataclass
class LiveState:
    show: str = ""  # the loaded show's folder (the one to open next time)
    master: float = 1.0
    quit_dark: bool = False  # quit with "Turn lights off": automations leave the lights off until a time range begins
    quit_at: float = 0.0  # when that quit was (kept through later saves, restarts and quits), or 0
    house: dict[str, HouseSetting] = field(default_factory=dict)
    base: dict[str, Any] | None = None  # {"input": id, "time": seconds}
    overlays: list[dict[str, Any] | None] = field(default_factory=lambda: [None, None, None, None])
    frozen: dict[str, Any] | None = None  # Base holding the last show's look: {"from": its name, "colours": {id: [r, g, b, a]}}
    globals: dict[str, Any] = field(default_factory=dict)  # the loaded show's variables
    show_globals: dict[str, dict[str, Any]] = field(default_factory=dict)  # other shows' variables, by folder
    automations: dict[str, Any] = field(default_factory=dict)  # the show's: paused, each one's state, history
    global_automations: dict[str, Any] = field(default_factory=dict)  # the global ones' state and history
    saved_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "show": self.show,
            "master": self.master,
            "quit_dark": self.quit_dark,
            "quit_at": self.quit_at,
            "house": {k: {"level": v.level, "on": v.on} for k, v in self.house.items()},
            "base": self.base,
            "overlays": self.overlays,
            "frozen": self.frozen,
            "globals": self.globals,
            "show_globals": self.show_globals,
            "automations": self.automations,
            "global_automations": self.global_automations,
            "saved_at": self.saved_at,
        }

    def for_show(self, folder: str) -> LiveState:
        """This state for opening the show in folder. If it was saved with another show loaded (that one has gone, say),
        only what's app-wide carries over: Master, the house lights, the quit's hold, the other shows' variables and
        the global automations. Nothing plays, and the show's own automations start afresh."""
        if _same_folder(self.show, folder):
            return self
        others = dict(self.show_globals)
        if self.show and self.globals:
            others[self.show] = dict(self.globals)
        return LiveState(show=folder, master=self.master, quit_dark=self.quit_dark, quit_at=self.quit_at,
                         house=dict(self.house), globals=dict(_pop_folder(others, folder)), show_globals=others,
                         global_automations=dict(self.global_automations),
                         automations={"paused": bool(self.automations.get("paused", False)),
                                      "paused_since": self.automations.get("paused_since")},
                         saved_at=self.saved_at)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LiveState:
        state = cls()
        state.show = str(data.get("show") or "")
        state.master = _unit(data.get("master"), 1.0)
        state.quit_dark = bool(data.get("quit_dark", False))
        quit_at = data.get("quit_at", 0.0)
        blacked_out = bool(data.get("blackout", False))  # (a live.json from before Cut / Fade to black)
        if blacked_out and bool(data.get("blackout_by_quit", False)):
            state.quit_dark = True
            quit_at = data.get("blackout_quit_at", 0.0)
        if state.quit_dark and isinstance(quit_at, (int, float)) and not isinstance(quit_at, bool) and quit_at > 0:
            state.quit_at = float(quit_at)
        house = data.get("house") or {}
        if isinstance(house, dict):
            for key, value in house.items():
                if isinstance(value, dict):
                    state.house[str(key)] = HouseSetting(_unit(value.get("level"), 1.0), bool(value.get("on", False)))
        base = data.get("base")
        state.base = base if isinstance(base, dict) else None
        overlays = data.get("overlays")
        if isinstance(overlays, list):
            state.overlays = [o if isinstance(o, dict) else None for o in overlays[:4]]
            state.overlays += [None] * (4 - len(state.overlays))
        frozen = data.get("frozen")
        state.frozen = frozen if isinstance(frozen, dict) and isinstance(frozen.get("colours"), dict) else None
        values = data.get("globals")
        state.globals = dict(values) if isinstance(values, dict) else {}
        others = data.get("show_globals")
        if isinstance(others, dict):
            state.show_globals = {str(k): dict(v) for k, v in others.items() if isinstance(v, dict)}
        automations = data.get("automations")
        state.automations = dict(automations) if isinstance(automations, dict) else {}
        global_automations = data.get("global_automations")
        state.global_automations = dict(global_automations) if isinstance(global_automations, dict) else {}
        saved = data.get("saved_at", 0.0)
        state.saved_at = float(saved) if isinstance(saved, (int, float)) and not isinstance(saved, bool) else 0.0
        if blacked_out:  # it was blacked out then: it starts black now (nothing playing, house lights off, levels kept)
            state.base = None
            state.overlays = [None, None, None, None]
            state.frozen = None
            state.house = {k: HouseSetting(v.level, False) for k, v in state.house.items()}
        return state

    @classmethod
    def load(cls, path: Path | None = None) -> LiveState | None:
        target = path if path is not None else live_state_path()
        try:
            data = read_json(target)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        try:
            return cls.from_dict(data) if isinstance(data, dict) else None
        except Exception:  # an odd value: start afresh rather than not at all
            return None

    def save(self, path: Path | None = None) -> None:
        self.saved_at = time.time()
        write_json_atomic(path if path is not None else live_state_path(), self.to_dict())


def _same_folder(a: str, b: str) -> bool:
    import os

    if not a or not b:
        return a == b
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _pop_folder(table: dict[str, dict], folder: str) -> dict:
    """The entry for folder (however its path is written), taken out of the table."""
    for key in list(table):
        if _same_folder(key, folder):
            return table.pop(key)
    return {}


def _unit(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0.0, min(1.0, float(value)))
