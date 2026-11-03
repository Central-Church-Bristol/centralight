"""The remote: a small web server, so the Stream Deck (or a phone) can work the lights.

Every action is a plain GET URL, so the Elgato Stream Deck app's "Website" action (tick "GET
request in background") or Companion's "Generic HTTP" can fire it. Open http://127.0.0.1:8746/
in a browser for a page listing every URL, with big buttons (it doubles as a phone remote when
"Allow other computers" is on).

    /api/input/<id>                        send to Base with its own transition
                                           (?transition=fade&duration=2 to choose another)
    /api/input/<id>/cut                    straight to Base, no transition
    /api/input/<id>/overlay/<1-4>          toggle it in an overlay (?state=on or off to be exact)
    /api/overlay/<1-4>/off                 switch an overlay off
    /api/overlay/<1-4>/opacity?value=0.5   (&fade=1)
    /api/var/<name>?value=0.7              a global variable: numbers; colours as FF6F59;
                                           switches on / off / toggle
    /api/master?level=0.8                  (&fade=2)
    /api/house/<light>?level=0.4           festoon or key lights (&fade=3); /on, /off, /toggle
    /api/blackout/on, /off, /toggle
    /api/identify/on, /off
    /api/automation/<id>/run               run an automation now (/enable, /disable)
    /api/automations/pause, /resume, /toggle
    /api/layout/<id>                       switch to a saved layout
    /api/state                             what's happening now, as JSON
    POST /api/command {"op": "input", "id": "warm-white"}   the same things as JSON

Each reply is {"ok": true} straight away (or {"ok": false, "error": "..."} with 404 for an
unknown input, variable or light, 400 for a bad value). Commands go straight to the engine, so
they work while the window is minimised or a dialog is open.

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .commands import (
    OverlayOff,
    RunAutomation,
    SendToBase,
    SendToOverlay,
    SetBlackout,
    SetHouse,
    SetIdentify,
    SetMaster,
    SetOverlayOpacity,
    SetAutomationEnabled,
    SetAutomationsPaused,
    SetVariable,
    SwitchLayout,
)
from .expressions import describe_value
from .log import log_exception, log_info
from .model import TRANSITION_KINDS, Show, Transition, slugify
from .variables import coerce_global

DEFAULT_PORT = 8746
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
OVERLAYS = (1, 2, 3, 4)
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


def _input_id(show: Show, raw: str) -> str:
    if raw in show.inputs:
        return raw
    for inp in show.inputs.values():  # a name works too: /api/input/Warm%20white
        if inp.name.casefold() == raw.casefold() or slugify(inp.name) == slugify(raw):
            return inp.id
    raise BadRequest(404, f"there's no input called {raw!r}")


def _house_id(show: Show, raw: str) -> str:
    for item in show.house:
        if raw in (item.fixture, slugify(item.label)) or item.label.casefold() == raw.casefold():
            return item.fixture
    names = ", ".join(slugify(item.label) for item in show.house)
    raise BadRequest(404, f"there's no house light called {raw!r} (try {names})")


def _automation_id(show: Show, raw: str) -> str:
    if raw in show.automations:
        return raw
    for automation in show.automations.values():
        if automation.name.casefold() == raw.casefold() or slugify(automation.name) == slugify(raw):
            return automation.id
    raise BadRequest(404, f"there's no automation called {raw!r}")


def _overlay(raw: str) -> int:
    if raw.isdigit() and int(raw) in OVERLAYS:
        return int(raw)
    raise BadRequest(404, "overlays are numbered 1 to 4")


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


def command_for(parts: list[str], params: dict[str, str], show: Show, frame: Any = None) -> Any:
    """The engine command for an API path (split into parts, after /api/). Raises BadRequest."""
    if not parts:
        raise BadRequest(404, "see / for the list of URLs")
    head, rest = parts[0], parts[1:]
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
        if len(rest) == 3 and rest[1] == "overlay":
            state = params.get("state")
            if state not in (None, "on", "off"):
                raise BadRequest(400, "state must be on or off (or leave it out to toggle)")
            return SendToOverlay(_overlay(rest[2]), input_id, state)
    elif head == "overlay" and len(rest) == 2:
        slot = _overlay(rest[0])
        if rest[1] == "off":
            return OverlayOff(slot)
        if rest[1] == "opacity":
            fade = _fade(params)
            level = _number(params, "value")
            return SetOverlayOpacity(slot, level) if fade is None else SetOverlayOpacity(slot, level, fade)
    elif head == "var" and len(rest) == 1:
        name = rest[0]
        var = next((g for g in show.globals if g.name == name), None)
        if var is None:
            raise BadRequest(404, f"there's no global variable called {name!r}")
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
    elif head == "blackout" and len(rest) == 1:
        return SetBlackout(on=_on_off(rest[0]))
    elif head == "identify" and len(rest) == 1:
        on = _on_off(rest[0])
        if on is None:
            raise BadRequest(404, "use /api/identify/on or /api/identify/off")
        return SetIdentify(on, show.identify_seed)
    elif head == "automation" and len(rest) == 2:
        automation_id = _automation_id(show, rest[0])
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
            raise BadRequest(404, f"there's no saved layout called {rest[0]!r}")
        return SwitchLayout(layout.id)
    raise BadRequest(404, "there's no such URL: see / for the list")


def command_from_json(payload: Any, show: Show, frame: Any = None) -> Any:
    """POST /api/command: {"op": "input", "id": "warm-white"} and so on, the same as the URLs."""
    if not isinstance(payload, dict) or not isinstance(payload.get("op"), str):
        raise BadRequest(400, 'send {"op": ...}, e.g. {"op": "input", "id": "warm-white"}')
    op = payload["op"]
    params = {k: str(v) for k, v in payload.items() if k not in ("op", "id", "slot", "name", "state", "light") and v is not None}
    if "state" in payload and payload["state"] is not None:
        params["state"] = str(payload["state"])
    if op in ("input", "cut", "overlay"):
        parts = ["input", str(payload.get("id", ""))]
        if op == "cut":
            parts.append("cut")
        elif op == "overlay":
            parts += ["overlay", str(payload.get("slot", ""))]
    elif op in ("overlay_off", "overlay_opacity"):
        parts = ["overlay", str(payload.get("slot", "")), "off" if op == "overlay_off" else "opacity"]
    elif op == "var":
        parts = ["var", str(payload.get("name", ""))]
    elif op == "master":
        parts = ["master"]
    elif op == "house":
        parts = ["house", str(payload.get("light", ""))]
        if "state" in payload:
            parts.append(str(payload["state"]))
            params.pop("state", None)
    elif op in ("blackout", "identify"):
        parts = [op, str(payload.get("state", "toggle"))]
        params.pop("state", None)
    else:
        raise BadRequest(400, f"unknown op {op!r}")
    return command_for(parts, params, show, frame)


def state_of(engine: Any) -> dict[str, Any]:
    """/api/state: what's happening now."""
    show: Show = engine.show
    frame = engine.frames.get()
    data: dict[str, Any] = {
        "show": show.name,
        "inputs": [{"id": inp.id, "name": inp.name} for inp in show.ordered_inputs()],
        "globals": [{"name": g.name, "type": g.type, "min": g.min, "max": g.max, "dashboard": g.dashboard} for g in show.globals],
        "house": [{"id": h.fixture, "name": h.label, "slug": slugify(h.label)} for h in show.house],
    }
    if frame is not None:
        data.update({
            "base": {"id": frame.base_id, "name": frame.base, "progress": frame.base_progress},
            "overlays": [{"slot": n, "id": o.input_id, "name": o.name, "on": o.on, "opacity": round(o.opacity, 3)}
                         for n, o in enumerate(frame.overlays, start=1)],
            "master": round(frame.master, 3),
            "blackout": frame.blackout,
            "levels": {fid: {"level": round(h.level, 3), "on": h.on} for fid, h in frame.house.items()},
            "variables": {name: describe_value(v) if type(v) is tuple else v for name, v in frame.variables.items()},
            "identify": frame.identify,
            "output": frame.output.label if frame.output is not None else "",
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
        self._handle(None)

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
                command = command_from_json(payload, remote.engine.show, remote.engine.frames.get())
                remote.engine.submit(command)
                remote.count += 1
                self._json(200, {"ok": True})
            elif not path or path == ["index.html"]:
                self._send(200, help_page(remote, params.get("key", "")).encode("utf-8"), "text/html; charset=utf-8")
            elif path[0] != "api":
                raise BadRequest(404, "there's no such page: see /")
            elif path[1:] == ["state"]:
                self._json(200, {"ok": True, **state_of(remote.engine)})
            else:
                command = command_for(path[1:], params, remote.engine.show, remote.engine.frames.get())
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

    def __init__(self, engine: Any, port: int = DEFAULT_PORT, lan: bool = False, key: str = "") -> None:
        self.engine = engine
        self.port = port
        self.lan = lan
        self.key = key
        self.count = 0  # requests handled (for Diagnostics)
        self.error = ""
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

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
        urls += [(f"Overlay {n} on / off", f"{base}/overlay/{n}{key}") for n in OVERLAYS]
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
        overlays = "".join(button(str(n), f"{path}/overlay/{n}", "small") for n in OVERLAYS)
        rows.append(
            f'<div class="tile" data-id="{esc(inp.id)}" style="--tag:{esc(inp.colour)}">'
            f'{button(inp.name, path, "big")}<div class="row">{button("Cut", path + "/cut", "small")}'
            f'<span class="label">Overlay</span>{overlays}</div></div>'
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
    examples = [
        ("Send an input to Base", "/api/input/<id>"),
        ("…with another transition", "/api/input/<id>?transition=fade&duration=2"),
        ("Cut an input to Base", "/api/input/<id>/cut"),
        ("Overlay on / off", "/api/input/<id>/overlay/1"),
        ("Overlay exactly on (or off)", "/api/input/<id>/overlay/1?state=on"),
        ("Overlay off", "/api/overlay/1/off"),
        ("Overlay opacity", "/api/overlay/1/opacity?value=0.5"),
        ("Set a variable", "/api/var/<name>?value=0.7  (colours: FF6F59, switches: on / off / toggle)"),
        ("Master", "/api/master?level=0.8&fade=2"),
        ("House light level", "/api/house/<light>?level=0.4&fade=3"),
        ("House light switch", "/api/house/<light>/toggle  (or /on, /off)"),
        ("Blackout", "/api/blackout/toggle  (or /on, /off)"),
        ("Identify", "/api/identify/on  (or /off)"),
        ("Run an automation", "/api/automation/<id>/run  (or /enable, /disable)"),
        ("Pause all automations", "/api/automations/pause  (or /resume, /toggle)"),
        ("Switch layout", "/api/layout/<id>"),
        ("What's happening now", "/api/state"),
    ]
    url_rows = "".join(f"<tr><td>{esc(what)}</td><td><code>{esc(base + path)}</code></td></tr>" for what, path in examples)
    ids = "".join(
        f"<tr><td>{esc(inp.name)}</td><td><code>{esc(base)}/api/input/{esc(quote(inp.id))}{esc(suffix)}</code></td></tr>"
        for inp in show.ordered_inputs()
    )
    lights = ", ".join(f"<code>{esc(slugify(h.label))}</code>" for h in show.house)
    return PAGE.format(
        show=esc(show.name),
        tiles="".join(rows) or "<p>No inputs yet: make one in CentraLight's Editor.</p>",
        variables="".join(variables) or "<p>No global variables yet (Editor → Globals tab).</p>",
        house=house,
        url_rows=url_rows,
        ids=ids,
        lights=lights,
        key=json.dumps(key),
    )


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
.row {{ display: flex; gap: 4px; margin-top: 6px; align-items: center; flex-wrap: wrap; }}
.label {{ color: var(--dim); font-size: 12px; margin: 0 2px 0 6px; }}
.vars {{ padding: 0 16px; display: grid; gap: 8px; }}
.var {{ display: flex; gap: 10px; align-items: center; background: var(--card); border-radius: 8px; padding: 8px 10px; }}
.var span {{ flex: 0 0 120px; overflow: hidden; text-overflow: ellipsis; }}
.var input[type=range] {{ flex: 1; }}
#blackout {{ background: transparent; border: 2px solid var(--red); color: var(--red); padding: 8px 14px; font-weight: 600; }}
#blackout.on {{ background: var(--red); color: white; }}
#status {{ color: var(--dim); font-size: 13px; }}
table {{ margin: 0 16px 24px; border-collapse: collapse; font-size: 13px; }}
td {{ padding: 4px 10px 4px 0; vertical-align: top; }}
code {{ color: var(--accent); word-break: break-all; }}
p {{ margin: 8px 16px; color: var(--dim); }}
</style></head>
<body>
<header><h1>{show}</h1><span id="status">Connecting…</span><button id="blackout" data-path="/api/blackout/toggle">Blackout</button></header>
<h2>Inputs: tap to send to Base</h2>
<div class="grid">{tiles}</div>
<h2>Variables</h2><div class="vars">{variables}</div>
<h2>House lights</h2><div class="vars">{house}</div>
<h2>Stream Deck URLs</h2>
<p>Elgato Stream Deck app: add a <b>Website</b> action, paste a URL and tick <b>GET request in background</b>.
Companion: use <b>Generic HTTP → GET</b>. House lights are called {lights}.</p>
<table>{url_rows}</table>
<table>{ids}</table>
<script>
const key = {key};
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
document.querySelectorAll("input[data-var]").forEach(input => input.addEventListener("input", () => {{
  const value = input.dataset.colour ? input.value.slice(1) : input.value;
  call(input.dataset.var + "?value=" + encodeURIComponent(value));
}}));
async function refresh() {{
  try {{
    const state = await (await fetch(withKey("/api/state"))).json();
    if (!state.ok) {{ document.getElementById("status").textContent = state.error; return; }}
    document.getElementById("status").textContent = "Base: " + state.base.name;
    document.getElementById("blackout").classList.toggle("on", state.blackout);
    document.querySelectorAll(".tile").forEach(t => t.classList.toggle("base", t.dataset.id === state.base.id));
    document.querySelectorAll(".tile").forEach(t => t.querySelectorAll(".row button[data-path*='/overlay/']").forEach(b => {{
      const slot = Number(b.textContent);
      const o = state.overlays[slot - 1];
      b.classList.toggle("on", !!o && o.on && o.id === t.dataset.id);
    }}));
  }} catch (e) {{ document.getElementById("status").textContent = "Can't reach CentraLight"; }}
}}
refresh(); setInterval(refresh, 1500);
</script>
</body></html>
"""
