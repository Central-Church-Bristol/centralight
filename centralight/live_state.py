"""Live state: what the lights were doing when CentraLight last closed.

Saved to %LOCALAPPDATA%\\CentraLight\\live.json and restored *before* the first DMX
frame, so restarting the app never flashes the lights.
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
    show: str = ""  # show folder this belongs to
    master: float = 1.0
    blackout: bool = False
    house: dict[str, HouseSetting] = field(default_factory=dict)
    base: dict[str, Any] | None = None  # {"input": id, "time": seconds}
    overlays: list[dict[str, Any] | None] = field(default_factory=lambda: [None, None, None, None])
    globals: dict[str, Any] = field(default_factory=dict)
    automations: dict[str, Any] = field(default_factory=dict)  # paused, each one's state, history
    saved_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "show": self.show,
            "master": self.master,
            "blackout": self.blackout,
            "house": {k: {"level": v.level, "on": v.on} for k, v in self.house.items()},
            "base": self.base,
            "overlays": self.overlays,
            "globals": self.globals,
            "automations": self.automations,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LiveState:
        state = cls()
        state.show = str(data.get("show") or "")
        state.master = _unit(data.get("master"), 1.0)
        state.blackout = bool(data.get("blackout", False))
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
        values = data.get("globals")
        state.globals = dict(values) if isinstance(values, dict) else {}
        automations = data.get("automations")
        state.automations = dict(automations) if isinstance(automations, dict) else {}
        saved = data.get("saved_at", 0.0)
        state.saved_at = float(saved) if isinstance(saved, (int, float)) and not isinstance(saved, bool) else 0.0
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


def _unit(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0.0, min(1.0, float(value)))
