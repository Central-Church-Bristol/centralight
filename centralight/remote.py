"""The remote: a small web server, so the Stream Deck (or a phone) can work the lights.

Every action is a plain GET URL, so the Elgato Stream Deck app's "Website" action (tick "GET
request in background") or Companion's "Generic HTTP" can fire it. Open http://127.0.0.1:8746/
in a browser for a page listing every URL, with big buttons (it doubles as a phone remote when
"Allow other computers" is on).

    /api/input/<id>                        send to Base with its own transition
                                           (?transition=fade&duration=2 to choose another)
    /api/input/<id>/cut                    straight to Base, no transition
    /api/input/<id>/overlay                send a copy of it to the Overlay, on top of what's there (up to 4
                                           copies of one input at once)
    /api/input/<id>/overlay/clear          clear its copies from the Overlay
    /api/overlay/clear                     clear the Overlay (every copy)
    /api/overlay/on, /off, /toggle         the Overlay's switch (off hides it without emptying it)
    /api/overlay/opacity?value=0.5         (&fade=1)
    /api/var/<name>?value=0.7              a show variable: numbers; colours as FF6F59;
                                           switches on / off / toggle
    /api/master?level=0.8                  (&fade=2)
    /api/house/<light>?level=0.4           festoon or key lights (&fade=3); /on, /off, /toggle
    /api/black/cut, /api/black/fade        the colour lights to black (?all=1: the Festoon and Key
                                           lights too; ?seconds=2 for the fade)
    /api/identify/on, /off
    /api/automation/<id>/run               run one of the show's automations now (/enable, /disable)
    /api/global-automation/<id>/run        ...or a global automation (/enable, /disable)
    /api/automations/pause, /resume, /toggle
    /api/layout/<id>                       switch to a saved layout
    /api/show/<id or name>                 load another show (the lights hold what they show until
                                           one of its inputs is sent)
    /api/countdown/now, /wait, /skip       the countdown before an automatic show load or Cut / Fade
                                           to black: do it now, 10 minutes later, or not this time
    /api/live/release                      the Console lets go of its lights: they fade back to the
                                           inputs (?lights=a,b: just those; ?fade=1)
    /api/live/tap, /next, /pause, /resume  the Console's chase: Tap tempo, the next step, pause, play on
    /api/live/speed?bpm=120                ...and its speed
    /api/shows                             the shows there are, as JSON
    /api/state                             what's happening now, as JSON
    POST /api/command {"op": "input", "id": "warm-white"}   the same things as JSON

Each reply is {"ok": true} straight away (or {"ok": false, "error": "..."} with 404 for an
unknown input, variable or light, 400 for a bad value). Inputs, variables, house lights,
automations and layouts are the loaded show's: one that isn't in it gets 404 "not in this show".
Commands go straight to the engine, so they work while the window is minimised or a dialog is open.

Safety: it listens on 127.0.0.1 (this PC only) unless "Allow other computers" is on, which then
needs ?key=... on every URL. Requests that a browser makes for another website are refused, and
so are requests whose Host isn't this PC (a trick called DNS rebinding).
"""

from __future__ import annotations

import errno
import html
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .commands import (
    BLACK_FADE,
    ClearFromOverlay,
    ClearOverlay,
    ConsoleChase,
    ConsoleRelease,
    CountdownAction,
    RequestShow,
    RunAutomation,
    SendToBase,
    SendToOverlay,
    SetHouse,
    SetIdentify,
    SetMaster,
    SetOverlayOn,
    SetOverlayOpacity,
    SetAutomationEnabled,
    SetAutomationsPaused,
    SetVariable,
    SwitchLayout,
    ToBlack,
)
from .console import BPM_RANGE
from .expressions import describe_value
from .log import log_exception, log_info
from .model import TRANSITION_KINDS, Show, Transition, slugify
from .shows import ShowInfo, find_show, list_shows, same_folder, show_id
from .variables import coerce_global

DEFAULT_PORT = 8746
SHOWS_FRESH = 3.0  # seconds the list of shows is kept before it's read again
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
MAX_BODY = 64_000


def new_key() -> str:
    """A key for "Allow other computers" (short enough to type on a phone)."""
    return secrets.token_urlsafe(9)


def lan_addresses() -> list[str]:
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


class BadRequest(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- turning requests into commands


def _number(params: dict[str, str], name: str, low: float = 0.0, high: float = 1.0, default: float | None = None) -> float:
    raw = params.get(name)
    if raw is None:
        if default is None:
            raise BadRequest(400, f"add ?{name}=… (a number from {low:g} to {high:g})")
        return default
    try:
        value = float(raw)
    except ValueError:
        raise BadRequest(400, f"{name} must be a number, not {raw!r}") from None
    if value != value:
        raise BadRequest(400, f"{name} must be a number")
    return min(high, max(low, value))


def _fade(params: dict[str, str]) -> float | None:
    return _number(params, "fade", 0.0, 600.0) if "fade" in params else None


def _in_this_show(show: Show) -> str:
    return f"not in this show ({show.name})" if show.name else "not in this show"


def _input_id(show: Show, raw: str) -> str:
    if raw in show.inputs:
        return raw
    for inp in show.inputs.values():  # a name works too: /api/input/Warm%20white
        if inp.name.casefold() == raw.casefold() or slugify(inp.name) == slugify(raw):
            return inp.id
    raise BadRequest(404, f"there's no input called {raw!r}: {_in_this_show(show)}")


def _house_id(show: Show, raw: str) -> str:
    for item in show.house:
        if raw in (item.fixture, slugify(item.label)) or item.label.casefold() == raw.casefold():
            return item.fixture
    names = ", ".join(slugify(item.label) for item in show.house)
    raise BadRequest(404, f"there's no house light called {raw!r}: {_in_this_show(show)} (try {names})")


def _automation_id(show: Show, raw: str, what: str = "automation", where: str = "") -> str:
    if raw in show.automations:
        return raw
    for automation in show.automations.values():
        if automation.name.casefold() == raw.casefold() or slugify(automation.name) == slugify(raw):
            return automation.id
    raise BadRequest(404, f"there's no {what} called {raw!r}" + (f": {where}" if where else ""))


def _light_ids(show: Show, raw: str) -> tuple[str, ...]:
    """?lights=slimpar-101,Pillar left: colour lights by id or name."""
    found: list[str] = []
    for word in (w.strip() for w in raw.split(",")):
        if not word:
            continue
        fx = next((f for f in show.colour_fixtures() if word in (f.id, slugify(f.name)) or f.name.casefold() == word.casefold()), None)
        if fx is None:
            raise BadRequest(404, f"there's no colour light called {word!r}: {_in_this_show(show)}")
        if fx.id not in found:
            found.append(fx.id)
    if not found:
        raise BadRequest(400, "add ?lights=… (light ids, with commas between)")
    return tuple(found)


def _opacity(params: dict[str, str]) -> SetOverlayOpacity:
    fade = _fade(params)
    level = _number(params, "value")
    return SetOverlayOpacity(level) if fade is None else SetOverlayOpacity(level, fade)


def _on_off(word: str) -> bool | None:
    """True / False, or None for toggle."""
    word = word.lower()
    if word in ("on", "true", "1"):
        return True
    if word in ("off", "false", "0"):
        return False
    if word == "toggle":
        return None
    raise BadRequest(404, "use on, off or toggle")


# what needs a show loaded (M14): with none, these answer 404 "No show loaded"; loading a show, the global automations,
# pausing them, the countdown, the shows and the state still work
NEEDS_A_SHOW = ("input", "overlay", "var", "master", "house", "black", "identify", "automation", "layout")


def command_for(parts: list[str], params: dict[str, str], show: Show, frame: Any = None, black_fade: float = BLACK_FADE,
                shows: list[ShowInfo] | None = None, global_automations: Show | None = None) -> Any:
    """The engine command for an API path (split into parts, after /api/). Raises BadRequest. show: the loaded
    show; shows: the shows there are (for /api/show/...); global_automations: a Show holding just them."""
    if not parts:
        raise BadRequest(404, "see / for the list of URLs")
    head, rest = parts[0], parts[1:]
    if getattr(show, "no_show", False) and head in NEEDS_A_SHOW:
        raise BadRequest(404, "No show loaded")  # (M14: load one first, /api/show/<id or name>)
    if head == "show" and len(rest) == 1:
        info = find_show(Path("."), rest[0], shows or [])
        if info is None:
            names = ", ".join(s.id for s in shows or []) or "none yet"
            raise BadRequest(404, f"there's no show called {rest[0]!r} (the shows: {names})")
        return RequestShow(info.folder)
    if head == "global-automation" and len(rest) == 2:
        container = global_automations if global_automations is not None else Show(name="Global automations")
        automation_id = _automation_id(container, rest[0], "global automation")
        if rest[1] == "run":
            return RunAutomation(automation_id, "global")
        if rest[1] in ("enable", "disable"):
            return SetAutomationEnabled(automation_id, rest[1] == "enable", "global")
        raise BadRequest(404, "use /run, /enable or /disable")
    if head == "input" and rest:
        input_id = _input_id(show, rest[0])
        if len(rest) == 1:
            transition = None
            if "transition" in params or "duration" in params:
                kind = params.get("transition", show.inputs[input_id].transition.kind)
                if kind not in TRANSITION_KINDS:
                    raise BadRequest(400, f"transition must be one of {', '.join(TRANSITION_KINDS)}")
                duration = _number(params, "duration", 0.0, 60.0, show.inputs[input_id].transition.duration)
                transition = Transition(kind, duration, show.inputs[input_id].transition.direction, show.inputs[input_id].transition.softness)
            return SendToBase(input_id, transition=transition)
        if rest[1:] == ["cut"]:
            return SendToBase(input_id, cut=True)
        if rest[1:] == ["overlay"]:
            return SendToOverlay(input_id)
        if rest[1:] == ["overlay", "clear"]:
            return ClearFromOverlay(input_id=input_id)
    elif head == "overlay" and len(rest) == 1:
        if rest[0] == "clear":
            return ClearOverlay()
        if rest[0] in ("on", "off", "toggle"):
            return SetOverlayOn(_on_off(rest[0]))
        if rest[0] == "opacity":
            return _opacity(params)
        raise BadRequest(404, "use /api/overlay/clear, /on, /off, /toggle or /opacity?value=0.5")
    elif head == "var" and len(rest) == 1:
        name = rest[0]
        var = next((g for g in show.globals if g.name == name), None)
        if var is None:
            raise BadRequest(404, f"there's no show variable called {name!r}: {_in_this_show(show)}")
        raw = params.get("value")
        if raw is None:
            raise BadRequest(400, "add ?value=… (a number, a colour like FF6F59, or on / off / toggle)")
        current = frame.variables.get(name) if frame is not None and getattr(frame, "variables", None) else None
        if coerce_global(var, raw, current) is None:
            raise BadRequest(400, f"{raw!r} doesn't fit {name} (a {var.type})")
        return SetVariable(name, raw.strip())
    elif head == "master" and not rest:
        fade = _fade(params)
        level = _number(params, "level")
        return SetMaster(level) if fade is None else SetMaster(level, fade)
    elif head == "house" and rest and len(rest) <= 2:
        fixture = _house_id(show, rest[0])
        if len(rest) == 2:
            on = _on_off(rest[1])
            return SetHouse(fixture, on=on, toggle=on is None, fade=_fade(params))
        level = _number(params, "level") if "level" in params else None
        on = _on_off(params["on"]) if "on" in params else (True if level is not None else None)
        if level is None and on is None:
            raise BadRequest(400, "add ?level=0.5, or use /on, /off or /toggle")
        return SetHouse(fixture, level=level, on=on, toggle=False, fade=_fade(params))
    elif head == "black" and len(rest) == 1 and rest[0] in ("cut", "fade"):
        everything = params.get("all", "").strip().lower() in ("1", "true", "yes", "on")
        if rest[0] == "cut":
            return ToBlack(0.0, everything)
        return ToBlack(_number(params, "seconds", 0.0, 60.0, default=black_fade), everything)
    elif head == "countdown" and len(rest) == 1:
        if rest[0] not in ("now", "wait", "skip"):
            raise BadRequest(404, "use /api/countdown/now, /api/countdown/wait or /api/countdown/skip")
        countdown = getattr(frame, "countdown", None) if frame is not None else None
        wanted = params.get("id", "").strip()  # (the phone page names the one it showed: a second tap can't hit another)
        if countdown is None and not wanted:
            raise BadRequest(409, "there's no countdown now")
        return CountdownAction(rest[0], wanted or countdown.id)
    elif head == "live" and len(rest) == 1:  # (M15: the Console page)
        if rest[0] == "release":  # everything it holds fades back (?lights=a,b: just those)
            return ConsoleRelease(lights=_light_ids(show, params["lights"]) if "lights" in params else None,
                                  fade=_fade(params))
        if rest[0] in ("tap", "next", "pause", "resume"):  # its chase
            return ConsoleChase(rest[0])
        if rest[0] == "speed":
            return ConsoleChase("speed", bpm=_number(params, "bpm", *BPM_RANGE))
        raise BadRequest(404, "use /api/live/release, /tap, /next, /pause, /resume or /speed?bpm=120")
    elif head == "identify" and len(rest) == 1:
        on = _on_off(rest[0])
        if on is None:
            raise BadRequest(404, "use /api/identify/on or /api/identify/off")
        return SetIdentify(on, show.identify_seed)
    elif head == "automation" and len(rest) == 2:
        automation_id = _automation_id(show, rest[0], where=_in_this_show(show))
        if rest[1] == "run":
            return RunAutomation(automation_id)
        if rest[1] in ("enable", "disable"):
            return SetAutomationEnabled(automation_id, rest[1] == "enable")
    elif head == "automations" and len(rest) == 1:
        words = {"pause": True, "resume": False, "toggle": None}
        if rest[0] in words:
            return SetAutomationsPaused(words[rest[0]])
    elif head == "layout" and len(rest) == 1:
        layout = show.layout(rest[0]) or next((lay for lay in show.layouts if lay.name.casefold() == rest[0].casefold()), None)
        if layout is None:
            raise BadRequest(404, f"there's no saved layout called {rest[0]!r}: {_in_this_show(show)}")
        return SwitchLayout(layout.id)
    raise BadRequest(404, "there's no such URL: see / for the list")


def command_from_json(payload: Any, show: Show, frame: Any = None, black_fade: float = BLACK_FADE,
                      shows: list[ShowInfo] | None = None, global_automations: Show | None = None) -> Any:
    """POST /api/command: {"op": "input", "id": "warm-white"} and so on, the same as the URLs."""
    if not isinstance(payload, dict) or not isinstance(payload.get("op"), str):
        raise BadRequest(400, 'send {"op": ...}, e.g. {"op": "input", "id": "warm-white"}')
    op = payload["op"]
    params = {k: str(v) for k, v in payload.items() if k not in ("op", "id", "name", "state", "light") and v is not None}
    if "state" in payload and payload["state"] is not None:
        params["state"] = str(payload["state"])
    if op in ("input", "cut", "overlay"):
        parts = ["input", str(payload.get("id", ""))]
        if op == "cut":
            parts.append("cut")
        elif op == "overlay":
            if "slot" in payload or "state" in payload:  # (Overlays 1-4, before M14: refused rather than misread)
                raise BadRequest(400, 'there is one Overlay now: send {"op": "overlay", "id": ...} (no "slot" or "state")')
            parts.append("overlay")
    elif op == "overlay_clear":  # {"op": "overlay_clear", "id": optional: that input's copies, else every copy}
        parts = ["input", str(payload["id"]), "overlay", "clear"] if payload.get("id") else ["overlay", "clear"]
    elif op == "overlay_switch":  # {"op": "overlay_switch", "state": "on" | "off" | "toggle"}
        parts = ["overlay", str(payload.get("state", "toggle"))]
        params.pop("state", None)
    elif op == "overlay_opacity":
        parts = ["overlay", "opacity"]
    elif op == "var":
        parts = ["var", str(payload.get("name", ""))]
    elif op == "master":
        parts = ["master"]
    elif op == "house":
        parts = ["house", str(payload.get("light", ""))]
        if "state" in payload:
            parts.append(str(payload["state"]))
            params.pop("state", None)
    elif op == "black":
        parts = ["black", str(payload.get("state", "fade"))]
        params.pop("state", None)
    elif op == "identify":
        parts = [op, str(payload.get("state", "toggle"))]
        params.pop("state", None)
    elif op == "live":  # {"op": "live", "state": "release"}: the Console lets its lights go (M15); or its chase's
        parts = ["live", str(payload.get("state", "release"))]  # tap / next / pause / resume, or speed with "bpm"
        params.pop("state", None)
        if isinstance(payload.get("lights"), (list, tuple)):  # ("lights": [...] for Release of some)
            params["lights"] = ",".join(str(light) for light in payload["lights"])
    elif op == "show":
        parts = ["show", str(payload.get("id") or payload.get("name") or "")]
    elif op == "countdown":  # {"op": "countdown", "state": "now" | "wait" | "skip", "id": optional}
        parts = ["countdown", str(payload.get("state", ""))]
        params.pop("state", None)
        if payload.get("id"):
            params["id"] = str(payload["id"])
    else:
        raise BadRequest(400, f"unknown op {op!r}")
    return command_for(parts, params, show, frame, black_fade, shows, global_automations)


def shows_of(shows: list[ShowInfo], loaded: str) -> list[dict[str, Any]]:
    """/api/shows (and /api/state's "shows"): each show's id and name, and which is loaded."""
    return [{"id": s.id, "name": s.name, "loaded": same_folder(s.folder, loaded), "notes": s.notes} for s in shows]


def _countdown_state(countdown: Any, shows: list[ShowInfo]) -> dict[str, Any] | None:
    """The countdown banner's text for the phone page: "Loading Evening in 0:15"."""
    if countdown is None:
        return None
    text = countdown.text
    if countdown.show:
        info = find_show(Path("."), countdown.show, shows)
        text = f"Loading {info.name if info is not None else countdown.show}"
    return {"id": countdown.id, "text": text, "seconds": round(countdown.seconds, 1), "clock": countdown.clock,
            "automation": countdown.automation, "load": bool(countdown.show), "more": countdown.more}


def state_of(engine: Any, shows: list[ShowInfo] | None = None) -> dict[str, Any]:
    """/api/state: what's happening now."""
    show: Show = engine.show
    frame = engine.frames.get()
    data: dict[str, Any] = {
        "show": show.name,
        "show_id": show_id(show.folder) if show.folder else "",
        "no_show": show.no_show,
        "notes": show.notes,
        "shows": shows_of(shows or [], show.folder),
        "inputs": [{"id": inp.id, "name": inp.name} for inp in show.ordered_inputs()],
        "globals": [{"name": g.name, "type": g.type, "min": g.min, "max": g.max, "dashboard": g.dashboard} for g in show.globals],
        "house": [{"id": h.fixture, "name": h.label, "slug": slugify(h.label)} for h in show.house],
    }
    if frame is not None:
        data.update({
            "base": {"id": frame.base_id, "name": frame.base, "progress": frame.base_progress},
            "overlay": {"on": frame.overlay.on, "opacity": round(frame.overlay.opacity, 3),
                        "copies": [{"copy": c.number, "id": c.input_id, "name": c.name,
                                    "progress": None if c.progress is None else round(c.progress, 3)}
                                   for c in frame.overlay.copies]},
            "master": round(frame.master, 3),
            "colour_black": frame.colour_black,
            "levels": {fid: {"level": round(h.level, 3), "on": h.on} for fid, h in frame.house.items()},
            "variables": {name: describe_value(v) if type(v) is tuple else v for name, v in frame.variables.items()},
            "identify": frame.identify,
            "output": frame.output.label if frame.output is not None else "",
            "countdown": _countdown_state(frame.countdown, shows or []),
            "console": {"held": frame.console.held_count, "lights": sorted(light for light, attrs in frame.console.held.items() if attrs)},
        })
    return data


# ---------------------------------------------------------------- the server


class _Handler(BaseHTTPRequestHandler):
    server_version = "CentraLightRemote/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    @property
    def remote(self) -> RemoteServer:
        return self.server.remote  # type: ignore[attr-defined]

    # --- replies

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    # --- checks

    def _allowed(self, params: dict[str, str]) -> bool:
        remote = self.remote
        host = (self.headers.get("Host") or "").strip().lower()
        hostname = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        local = hostname in LOCAL_HOSTS and self.client_address[0] in ("127.0.0.1", "::1")
        if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            self._error(403, "requests from other websites are blocked")
            return False
        if not remote.lan:
            if not local:  # e.g. a web page that renamed itself to 127.0.0.1 ("DNS rebinding")
                self._error(403, "this remote only answers to http://127.0.0.1")
                return False
        elif not local and not (remote.key and secrets.compare_digest(params.get("key", ""), remote.key)):
            self._error(403, "add ?key=… (the key is in CentraLight's Settings → Remote)")
            return False
        return True

    # --- requests

    def do_GET(self) -> None:  # noqa: N802
        self._handle(None)

    def do_HEAD(self) -> None:  # noqa: N802
        # (HEAD mustn't do anything: only GET and POST run commands)
        self.send_response(405)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._error(400, "the request is too big")
            return
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(400, "the body must be JSON")
            return
        self._handle(payload)

    def _handle(self, payload: Any) -> None:
        parsed = urlparse(self.path)
        params = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        if not self._allowed(params):
            return
        path = [unquote(p) for p in parsed.path.split("/") if p]
        remote = self.remote
        try:
            if self.command == "POST":
                if path != ["api", "command"]:
                    raise BadRequest(404, "POST goes to /api/command")
                command = command_from_json(payload, remote.engine.show, remote.engine.frames.get(), remote.black_fade(),
                                            remote.shows(), remote.global_automations())
                remote.engine.submit(command)
                remote.count += 1
                self._json(200, {"ok": True})
            elif not path or path == ["index.html"]:
                key = params.get("key", "")
                key = key if remote.key and key == remote.key else ""  # only ever echo the real key
                self._send(200, help_page(remote, key).encode("utf-8"), "text/html; charset=utf-8")
            elif path[0] != "api":
                raise BadRequest(404, "there's no such page: see /")
            elif path[1:] == ["state"]:
                self._json(200, {"ok": True, **state_of(remote.engine, remote.shows())})
            elif path[1:] == ["shows"]:
                self._json(200, {"ok": True, "shows": shows_of(remote.shows(fresh=True), remote.engine.show.folder)})
            else:
                command = command_for(path[1:], params, remote.engine.show, remote.engine.frames.get(), remote.black_fade(),
                                      remote.shows(), remote.global_automations())
                remote.engine.submit(command)
                remote.count += 1
                self._json(200, {"ok": True})
        except BadRequest as exc:
            self._error(exc.status, str(exc))
        except Exception as exc:  # never let one bad request stop the remote
            log_exception("remote request", exc)
            self._error(500, "something went wrong (see session.log)")


class _Server(ThreadingHTTPServer):
    # Python's default (SO_REUSEADDR) lets a second program take over the same port on Windows
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class RemoteServer:
    """The remote's web server, on its own thread ("remote")."""

    def __init__(self, engine: Any, port: int = DEFAULT_PORT, lan: bool = False, key: str = "",
                 black_fade: Any = None, shows_root: Path | None = None) -> None:
        """black_fade: gives Fade to black's time (Settings); 2 s if not given. shows_root: the folder the shows are
        in (Documents\\CentraLight Shows if not given)."""
        self.engine = engine
        self._black_fade = black_fade
        self.port = port
        self.lan = lan
        self.key = key
        self.count = 0  # requests handled (for Diagnostics)
        self.error = ""
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.shows_root = Path(shows_root) if shows_root is not None else None
        self._shows: tuple[float, list[ShowInfo]] = (-1e9, [])
        self._shows_lock = threading.Lock()

    @classmethod
    def from_settings(cls, engine: Any, settings: Any, shows_root: Path | None = None) -> RemoteServer:
        """The remote as Settings set it up: its port, other computers and their key, and Fade to black's time (read
        each time, so a change in Settings counts at once)."""
        return cls(engine, settings.remote_port, settings.remote_lan, settings.remote_key,
                   black_fade=lambda: settings.black_fade, shows_root=shows_root)

    def shows(self, fresh: bool = False) -> list[ShowInfo]:
        """The shows there are (read from disk at most every few seconds: the phone page asks every 1.5 s)."""
        with self._shows_lock:
            now = time.monotonic()
            if fresh or now - self._shows[0] > SHOWS_FRESH:
                from .paths import shows_root

                try:
                    found = list_shows(self.shows_root if self.shows_root is not None else shows_root())
                except Exception as exc:  # (never let it stop the remote)
                    log_exception("listing the shows", exc)
                    found = []
                self._shows = (now, found)
            return list(self._shows[1])

    def global_automations(self) -> Show | None:
        return getattr(self.engine, "global_automations", None)

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def black_fade(self) -> float:
        try:
            return max(0.0, float(self._black_fade())) if self._black_fade is not None else BLACK_FADE
        except Exception:
            return BLACK_FADE

    def start(self) -> bool:
        """Start listening. False (with .error set) if the port is taken."""
        self.stop()
        address = "0.0.0.0" if self.lan else "127.0.0.1"
        try:
            httpd = _Server((address, self.port), _Handler)
        except OSError as exc:
            code = getattr(exc, "winerror", None) or exc.errno
            if code in (errno.EADDRINUSE, 10048, 98):
                self.error = f"port {self.port} is in use by another program"
            elif code in (errno.EACCES, 10013):
                self.error = f"Windows won't let CentraLight use port {self.port}"
            else:
                self.error = str(exc)
            log_info(f"Remote couldn't start on {address}:{self.port}: {exc}")
            return False
        httpd.remote = self  # type: ignore[attr-defined]
        self.port = httpd.server_address[1]  # (port 0 picks a free one, for tests)
        self._httpd = httpd
        self.error = ""
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.25}, name="remote", daemon=True)
        self._thread.start()
        log_info(f"Remote listening on http://{address}:{self.port}/")
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def base_url(self, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}"

    def urls_for_input(self, input_id: str, name: str = "") -> list[tuple[str, str]]:
        """(what it does, URL) for one input's Stream Deck keys."""
        base = f"{self.base_url()}/api/input/{quote(input_id)}"
        key = f"?key={self.key}" if self.lan and self.key else ""
        urls = [("Send to Base", base + key), ("Cut to Base", base + "/cut" + key)]
        urls += [("Send to Overlay", f"{base}/overlay{key}"), ("Clear from Overlay", f"{base}/overlay/clear{key}")]
        return urls


# ---------------------------------------------------------------- the help page


def help_page(remote: RemoteServer, key: str = "") -> str:
    """Every URL, plus big buttons: a remote control for a phone or tablet."""
    show: Show = remote.engine.show
    base = remote.base_url()
    suffix = f"?key={quote(key)}" if key else ""
    esc = html.escape

    def button(label: str, path: str, extra: str = "") -> str:
        return f'<button class="{extra}" data-path="{esc(path)}">{esc(label)}</button>'

    rows = []
    for inp in show.ordered_inputs():
        path = f"/api/input/{quote(inp.id)}"
        rows.append(
            f'<div class="tile" data-id="{esc(inp.id)}" style="--tag:{esc(inp.colour)}">'
            f'{button(inp.name, path, "big")}<div class="row">{button("Cut", path + "/cut", "small")}'
            f'{button("Overlay", path + "/overlay", "small overlay")}'
            f'{button("Clear", path + "/overlay/clear", "small clear")}</div></div>'
        )
    variables = []
    for var in show.globals:
        name = esc(var.name)
        path = f"/api/var/{quote(var.name)}"
        if var.type == "number":
            control = (f'<input type="range" min="{var.min:g}" max="{var.max:g}" step="{(var.max - var.min) / 100:g}" '
                       f'data-var="{esc(path)}">')
        elif var.type == "colour":
            control = f'<input type="color" data-var="{esc(path)}" data-colour="1">'
        else:
            control = button("Toggle", path + "?value=toggle", "small")
        variables.append(f'<div class="var"><span>{name}</span>{control}</div>')
    house = "".join(
        f'<div class="var"><span>{esc(h.label)}</span>{button("On", f"/api/house/{slugify(h.label)}/on", "small")}'
        f'{button("Off", f"/api/house/{slugify(h.label)}/off", "small")}</div>'
        for h in show.house
    )
    shows = remote.shows()
    show_options = "".join(
        f'<option value="{esc(s.id)}"{" selected" if same_folder(s.folder, show.folder) else ""}>{esc(s.name)}</option>'
        for s in shows)
    if show.no_show or not any(same_folder(s.folder, show.folder) for s in shows):
        show_options = f'<option value="" selected>{esc("None (lights off)" if show.no_show else show.name)}</option>' + show_options
    examples = [
        ("Send an input to Base", "/api/input/<id>"),
        ("…with another transition", "/api/input/<id>?transition=fade&duration=2"),
        ("Cut an input to Base", "/api/input/<id>/cut"),
        ("Send an input to the Overlay (a copy on top)", "/api/input/<id>/overlay"),
        ("Clear an input from the Overlay", "/api/input/<id>/overlay/clear"),
        ("Clear the Overlay", "/api/overlay/clear"),
        ("The Overlay's switch", "/api/overlay/toggle  (or /on, /off: off hides it without emptying it)"),
        ("Overlay opacity", "/api/overlay/opacity?value=0.5"),
        ("Set a variable", "/api/var/<name>?value=0.7  (colours: FF6F59, switches: on / off / toggle)"),
        ("Master", "/api/master?level=0.8&fade=2"),
        ("House light level", "/api/house/<light>?level=0.4&fade=3"),
        ("House light switch", "/api/house/<light>/toggle  (or /on, /off)"),
        ("Cut the colour lights to black", "/api/black/cut  (?all=1: the Festoon and Key lights too)"),
        ("Fade them to black", "/api/black/fade  (?seconds=2, ?all=1)"),
        ("Identify", "/api/identify/on  (or /off)"),
        ("Release the Console's lights", "/api/live/release  (?fade=1: over a second; ?lights=a,b: just those)"),
        ("The Console's chase", "/api/live/tap  (or /next, /pause, /resume, /speed?bpm=120)"),
        ("Run an automation", "/api/automation/<id>/run  (or /enable, /disable)"),
        ("Run a global automation", "/api/global-automation/<id>/run  (or /enable, /disable)"),
        ("Pause all automations", "/api/automations/pause  (or /resume, /toggle)"),
        ("Switch layout", "/api/layout/<id>"),
        ("Load a show", "/api/show/<id or name>"),
        ("The countdown before an automatic change", "/api/countdown/now  (or /wait: 10 minutes later, /skip)"),
        ("The shows", "/api/shows"),
        ("What's happening now", "/api/state"),
    ]
    show_urls = "".join(
        f"<tr><td>Load {esc(s.name)}</td><td><code>{esc(base)}/api/show/{esc(quote(s.id))}{esc(suffix)}</code></td></tr>"
        for s in shows)
    url_rows = "".join(f"<tr><td>{esc(what)}</td><td><code>{esc(base + path)}</code></td></tr>" for what, path in examples)
    ids = "".join(
        f"<tr><td>{esc(inp.name)}</td><td><code>{esc(base)}/api/input/{esc(quote(inp.id))}{esc(suffix)}</code></td></tr>"
        for inp in show.ordered_inputs()
    )
    lights = ", ".join(f"<code>{esc(slugify(h.label))}</code>" for h in show.house)
    def script_value(value: Any) -> str:  # (safe inside <script>)
        return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    if show.no_show:  # (M14: no show loaded, the lights off: the show switcher, and the URLs to load one)
        return PAGE.format(
            show="No show loaded", notes="", show_options=show_options, black="",
            body=NO_SHOW_BODY.format(url_rows=url_rows, show_urls=show_urls),
            key=script_value(key), show_id=script_value(""),
        )
    return PAGE.format(
        show=esc(show.name),
        notes=esc(show.notes),
        show_options=show_options,
        black=BLACK_BUTTONS,
        body=SHOW_BODY.format(tiles="".join(rows) or "<p>No inputs yet: make one in CentraLight's Editor.</p>",
                              variables="".join(variables) or "<p>No show variables yet (Editor → Show variables tab).</p>",
                              house=house, url_rows=url_rows, ids=ids, show_urls=show_urls, lights=lights),
        key=script_value(key),
        show_id=script_value(show_id(show.folder) if show.folder else ""),
    )


BLACK_BUTTONS = """<button class="black" data-black="/api/black/cut" title="Tap: the colour lights. Hold: everything, Festoon and Key lights too">Cut to black</button>
<button class="black" data-black="/api/black/fade" title="Tap: the colour lights. Hold: everything, Festoon and Key lights too">Fade to black</button>
"""

SHOW_BODY = """<h2>Inputs: tap to send to Base</h2>
<div class="grid">{tiles}</div>
<div class="row" style="padding: 0 16px"><span class="label">The Overlay</span>
<button class="small" data-path="/api/overlay/toggle" title="On / off: off hides it without emptying it">On / off</button>
<button class="small clear" data-path="/api/overlay/clear">Clear all</button></div>
<h2>Variables</h2><div class="vars">{variables}</div>
<h2>House lights</h2><div class="vars">{house}</div>
<h2>Stream Deck URLs</h2>
<p>Elgato Stream Deck app: add a <b>Website</b> action, paste a URL and tick <b>GET request in background</b>.
Companion: use <b>Generic HTTP → GET</b>. House lights are called {lights}.</p>
<table>{url_rows}</table>
<table>{ids}</table>
<table>{show_urls}</table>"""

# no show loaded (M14): the lights are off until a show is loaded (the switcher at the top, or a URL below)
NO_SHOW_BODY = """<p class="noshow">The lights are off. Load a show to bring them up: pick one at the top.</p>
<h2>Stream Deck URLs</h2>
<p>Elgato Stream Deck app: add a <b>Website</b> action, paste a URL and tick <b>GET request in background</b>.
Companion: use <b>Generic HTTP → GET</b>. While no show is loaded, only loading a show, the global automations and
the countdown work (the others answer “No show loaded”).</p>
<table>{show_urls}</table>
<table>{url_rows}</table>"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CentraLight remote</title>
<style>
:root {{ color-scheme: dark; --bg: #0B0F17; --panel: #121926; --card: #192233; --text: #EEF3F8;
        --dim: #A3ADBD; --accent: #86D6FF; --coral: #FF6F59; --lime: #D6FF79; --red: #FF3B5C; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 15px/1.4 "Segoe UI", system-ui, sans-serif; }}
header {{ display: flex; gap: 12px; align-items: center; padding: 14px 16px; background: var(--panel);
         position: sticky; top: 0; z-index: 1; flex-wrap: wrap; }}
h1 {{ font-size: 18px; margin: 0; flex: 1; }}
h2 {{ font-size: 13px; letter-spacing: 1px; text-transform: uppercase; color: var(--dim); margin: 24px 16px 8px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; padding: 0 16px; }}
.tile {{ background: var(--card); border-radius: 10px; padding: 8px; border-left: 4px solid var(--tag); }}
.tile.base {{ outline: 2px solid var(--coral); }}
button {{ background: var(--panel); color: var(--text); border: 1px solid #26324A; border-radius: 8px; cursor: pointer; font: inherit; }}
button:active {{ background: var(--accent); color: #06202E; }}
button.big {{ width: 100%; min-height: 64px; font-size: 17px; font-weight: 600; }}
button.small {{ min-width: 38px; min-height: 34px; padding: 0 8px; }}
button.on {{ background: var(--lime); color: #1B2600; }}
button.overlay {{ border-color: var(--lime); }}
button.clear {{ border-color: var(--red); color: var(--red); }}
.row {{ display: flex; gap: 4px; margin-top: 6px; align-items: center; flex-wrap: wrap; }}
.label {{ color: var(--dim); font-size: 12px; margin: 0 2px 0 6px; }}
.vars {{ padding: 0 16px; display: grid; gap: 8px; }}
.var {{ display: flex; gap: 10px; align-items: center; background: var(--card); border-radius: 8px; padding: 8px 10px; }}
.var span {{ flex: 0 0 120px; overflow: hidden; text-overflow: ellipsis; }}
.var input[type=range] {{ flex: 1; }}
button.black {{ background: transparent; border: 2px solid var(--red); color: var(--red); padding: 8px 12px; font-weight: 600;
  min-height: 40px; }}
button.black.held {{ background: var(--red); color: white; }}
#status {{ color: var(--dim); font-size: 13px; }}
select#shows {{ background: var(--card); color: var(--text); border: 1px solid #26324A; border-radius: 8px; font: inherit;
  min-height: 40px; padding: 0 8px; max-width: 60vw; }}
table {{ margin: 0 16px 24px; border-collapse: collapse; font-size: 13px; }}
td {{ padding: 4px 10px 4px 0; vertical-align: top; }}
code {{ color: var(--accent); word-break: break-all; }}
p {{ margin: 8px 16px; color: var(--dim); }}
.notes {{ margin: 12px 16px 0; padding: 10px 12px; background: var(--card); border-radius: 10px; color: var(--dim);
  white-space: pre-wrap; font-size: 14px; }}
.notes:empty {{ display: none; }}
.countdown {{ flex-basis: 100%; padding: 10px 12px; background: #3A2A10; border: 1px solid #FFB547; border-radius: 10px;
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
.countdown[hidden] {{ display: none; }}
#countdown-text {{ flex: 1; font-weight: 600; color: #FFB547; }}
p.noshow {{ margin: 24px 16px; font-size: 17px; color: var(--text); }}
button.console {{ border-color: #FFB547; color: #FFB547; }}
button.console[hidden] {{ display: none; }}
</style></head>
<body>
<header><h1>{show}</h1>
<select id="shows" title="Load another show (the lights hold what they show until one of its inputs is sent)">{show_options}</select>
<span id="status">Connecting…</span>
{black}<button class="small console" id="console" data-path="/api/live/release" hidden title="The Console page holds these lights: tap to let them all go (they fade back)">Console: release all</button>
<div id="countdown" class="countdown" hidden><span id="countdown-text"></span>
<button class="small" data-countdown="now" id="countdown-now">Do it now</button>
<button class="small" data-countdown="wait">Wait 10 min</button>
<button class="small" data-countdown="skip">Skip</button></div></header>
<div id="notes" class="notes">{notes}</div>
{body}
<script>
const key = {key};
const pageShow = {show_id};
function withKey(path) {{
  if (!key) return path;
  return path + (path.includes("?") ? "&" : "?") + "key=" + encodeURIComponent(key);
}}
async function call(path) {{
  try {{
    const reply = await fetch(withKey(path));
    const data = await reply.json();
    document.getElementById("status").textContent = data.ok ? "Sent" : data.error;
  }} catch (e) {{ document.getElementById("status").textContent = "Can't reach CentraLight"; }}
}}
document.querySelectorAll("button[data-path]").forEach(b => b.addEventListener("click", () => call(b.dataset.path)));
// the countdown's buttons name the countdown they showed, and the banner goes at once (a second tap can't hit another)
let countdownId = "";
document.querySelectorAll("button[data-countdown]").forEach(b => b.addEventListener("click", () => {{
  if (!countdownId) return;
  call("/api/countdown/" + b.dataset.countdown + "?id=" + encodeURIComponent(countdownId));
  countdownId = "";
  document.getElementById("countdown").hidden = true;
}}));
// Cut / Fade to black: a tap is the colour lights; holding it (0.6 s) is everything, the Festoon and Key lights too
document.querySelectorAll("button[data-black]").forEach(b => {{
  const label = b.textContent;
  let timer = null;
  const start = e => {{
    e.preventDefault();
    b.classList.add("held");
    timer = setTimeout(() => {{ timer = null; b.textContent = label.replace("to black", "everything"); call(b.dataset.black + "?all=1"); }}, 600);
  }};
  const end = () => {{
    b.classList.remove("held");
    if (timer !== null) {{ clearTimeout(timer); timer = null; call(b.dataset.black); }}
    setTimeout(() => {{ b.textContent = label; }}, 800);
  }};
  b.addEventListener("pointerdown", start);
  b.addEventListener("pointerup", end);
  b.addEventListener("pointerleave", () => {{ if (timer !== null) {{ clearTimeout(timer); timer = null; b.classList.remove("held"); }} }});
}});
document.querySelectorAll("input[data-var]").forEach(input => input.addEventListener("input", () => {{
  const value = input.dataset.colour ? input.value.slice(1) : input.value;
  call(input.dataset.var + "?value=" + encodeURIComponent(value));
}}));
// the show switcher: load another show (this page then shows its inputs)
document.getElementById("shows").addEventListener("change", e => {{
  if (e.target.value) call("/api/show/" + encodeURIComponent(e.target.value));
}});
async function refresh() {{
  try {{
    const state = await (await fetch(withKey("/api/state"))).json();
    if (!state.ok) {{ document.getElementById("status").textContent = state.error; return; }}
    if (state.show_id !== pageShow) {{ location.reload(); return; }}  // another show is loaded: its inputs and variables
    const banner = document.getElementById("countdown");  // the countdown before an automatic change
    banner.hidden = !state.countdown;
    countdownId = state.countdown ? state.countdown.id : "";
    if (state.countdown) {{
      const more = state.countdown.more ? " (and " + state.countdown.more + " more)" : "";
      document.getElementById("countdown-text").textContent = state.countdown.text + " in " + state.countdown.clock + more;
      document.getElementById("countdown-now").textContent = state.countdown.load ? "Load now" : "Do it now";
    }}
    const consoleButton = document.getElementById("console");  // (M15: the Console page holds lights)
    const held = state.console ? state.console.held : 0;
    consoleButton.hidden = !held;
    if (held) consoleButton.textContent = "Console: " + held + " light" + (held === 1 ? "" : "s") + " · release all";
    const notes = document.getElementById("notes");
    if (notes.textContent !== (state.notes || "")) notes.textContent = state.notes || "";  // (changed in CentraLight)
    document.getElementById("status").textContent = state.no_show ? "The lights are off" : "Base: " + state.base.name;
    document.querySelectorAll(".tile").forEach(t => t.classList.toggle("base", t.dataset.id === state.base.id));
    document.querySelectorAll(".tile").forEach(t => {{  // (the Overlay button lights up while it has copies there)
      const copies = state.overlay.copies.filter(c => c.id === t.dataset.id).length;
      const send = t.querySelector("button.overlay");
      if (send) {{ send.classList.toggle("on", copies > 0 && state.overlay.on); send.textContent = copies ? "Overlay ×" + copies : "Overlay"; }}
    }});
  }} catch (e) {{ document.getElementById("status").textContent = "Can't reach CentraLight"; }}
}}
refresh(); setInterval(refresh, 1000);
</script>
</body></html>
"""
