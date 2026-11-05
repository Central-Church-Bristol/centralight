"""App settings: %LOCALAPPDATA%\\CentraLight\\settings.json.

These belong to this PC (which output to use, window size...). Everything about the
lights and looks lives in the show folder instead.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .files import read_json, write_json_atomic
from .paths import settings_path

# Where the DMX goes. Simulated opens nothing: the app only shows what it would send.
OUTPUT_SIMULATED = "simulated"
OUTPUT_USB = "usb"
OUTPUT_ARTNET = "artnet"
OUTPUT_KINDS = (OUTPUT_SIMULATED, OUTPUT_USB, OUTPUT_ARTNET)

# What Quit does to the lights
QUIT_ASK = "ask"
QUIT_OFF = "off"
QUIT_LEAVE = "leave"
QUIT_ACTIONS = (QUIT_ASK, QUIT_OFF, QUIT_LEAVE)

DEFAULT_NDI_SOURCE = "Media Background Pane"
DEFAULT_REMOTE_PORT = 8746


@dataclass
class Settings:
    output: str = OUTPUT_SIMULATED
    usb_port: str = ""  # "" = find the Eurolite automatically
    artnet_ip: str = "192.168.1.16"
    artnet_universe: int = 0
    full_universe: bool = False  # send all 512 channels instead of stopping after the last light
    show_folder: str = ""  # "" = Documents\CentraLight Shows\Central Church
    ndi_default: str = DEFAULT_NDI_SOURCE
    quit_action: str = QUIT_ASK
    remote_port: int = DEFAULT_REMOTE_PORT
    remote_lan: bool = False
    remote_key: str = ""
    start_with_windows: bool = False  # in the tray at sign-in; switched on by hand (WEDNESDAY_TESTS.md) once it has been tried
    clshow_files: bool = True  # Windows opens .clshow files (exported shows) with CentraLight (filetype.py)
    audio_source: str = ""  # "" off, "output" this PC's output, or an input's name (audio.AUDIO sources)
    audio_attack: float = 0.05  # seconds for audio values to rise
    audio_release: float = 0.3  # ... and to fall
    black_fade: float = 2.0  # seconds Fade to black takes (the header's button, and the API's default)
    countdown: int = 15  # seconds of countdown before an automation loads a show or goes to black (0 = none)
    default_show: str = ""  # M14: the show that opens when the PC starts, and comes back after a schedule ("" = none)
    geometry: list[int] | None = None
    maximized: bool = False
    splitters: dict[str, list[int]] = field(default_factory=dict)
    page: str = "dashboard"
    ui: dict[str, Any] = field(default_factory=dict)  # small remembered choices (e.g. viewport toggles)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def update(self, data: dict[str, Any]) -> None:
        """Take values from a loaded file, ignoring unknown keys and fixing bad values."""
        known = {f.name for f in fields(self)}
        for key, value in data.items():
            if key in known:
                setattr(self, key, value)
        self._validate()

    def _validate(self) -> None:
        defaults = Settings()
        if self.output not in OUTPUT_KINDS:
            self.output = defaults.output
        if self.quit_action not in QUIT_ACTIONS:
            self.quit_action = defaults.quit_action
        self.usb_port = str(self.usb_port or "").strip()
        self.artnet_ip = str(self.artnet_ip or defaults.artnet_ip).strip()
        self.artnet_universe = _int(self.artnet_universe, 0, 0, 32767)
        self.full_universe = bool(self.full_universe)
        self.show_folder = str(self.show_folder or "")
        self.ndi_default = str(self.ndi_default or DEFAULT_NDI_SOURCE)
        self.remote_port = _int(self.remote_port, DEFAULT_REMOTE_PORT, 1024, 65535)
        self.remote_lan = bool(self.remote_lan)
        self.remote_key = str(self.remote_key or "")
        self.start_with_windows = bool(self.start_with_windows)
        self.clshow_files = bool(self.clshow_files)
        self.audio_source = str(self.audio_source or "")
        for name, default in (("audio_attack", defaults.audio_attack), ("audio_release", defaults.audio_release)):
            value = getattr(self, name)
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 3.0
            setattr(self, name, float(value) if ok else default)
        value = self.black_fade
        self.black_fade = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 60.0 else defaults.black_fade
        self.countdown = _int(self.countdown, defaults.countdown, 0, 60)
        self.default_show = str(self.default_show).strip() if isinstance(self.default_show, str) else ""
        self.maximized = bool(self.maximized)
        geometry = self.geometry
        if not (isinstance(geometry, list) and len(geometry) == 4 and all(isinstance(v, int) for v in geometry)):
            self.geometry = None
        self.splitters = _clean_splits(self.splitters)
        self.page = str(self.page or "dashboard")
        if not isinstance(self.ui, dict):
            self.ui = {}
        self.ui = {str(k): v for k, v in self.ui.items() if isinstance(v, (bool, int, float, str))}

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        settings = cls()
        target = path if path is not None else settings_path()
        if target.exists():
            try:
                data = read_json(target)
                if isinstance(data, dict):
                    settings.update(data)
            except Exception:  # whatever is wrong with it: start with the defaults (Simulated output), never not at all
                settings = cls()
        return settings

    def save(self, path: Path | None = None) -> None:
        write_json_atomic(path if path is not None else settings_path(), self.to_dict())


def _clean_splits(value: Any) -> dict:
    """Splitter sizes: {name: [sizes]} or {name: {part: [sizes]}}; anything else is dropped."""
    if not isinstance(value, dict):
        return {}
    clean: dict = {}
    for key, sizes in value.items():
        if isinstance(sizes, list) and sizes and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in sizes):
            clean[str(key)] = [int(x) for x in sizes]
        elif isinstance(sizes, dict):
            inner = _clean_splits(sizes)
            if inner:
                clean[str(key)] = inner
    return clean


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))
