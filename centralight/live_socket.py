"""The live connection (M16): a WebSocket on the remote's own port, at /api/ws, for the Stream Deck's Companion module.

It needs the same key as the other URLs when it comes from another computer. CentraLight pushes what's happening the
moment it changes: the loaded show and the lists of shows, inputs, house lights, variables and automations; what's on
Base and the Overlay; Master, the house lights and the variables' values; the colour lights being black; the
countdown; automations being paused; what the Console holds. Commands come in the same way, as POST /api/command takes
them. The HTTP URLs stay as they are.

Messages (JSON text):
    from CentraLight:  {"type": "state", "state": {...}}      (all of it, each time something in it changes)
                       {"type": "result", "id": 7, "ok": true}  (or "ok": false, "error": "...", "status": 404)
    to CentraLight:    {"type": "command", "id": 7, "command": {"op": "input", "id": "warm-white"}}
                       {"type": "ping"}                          (answered with {"type": "pong"})

Only the standard library: the handshake (RFC 6455), text frames, ping / pong and close.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import select
import struct
import time
from typing import Any, Callable
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PUSH_EVERY = 0.1  # seconds: how often the state is looked at (it's sent only when it has changed)
PING_EVERY = 15.0  # seconds of quiet from the client before a ping
DEAD_AFTER = 50.0  # seconds with nothing from the client, not even a pong: it's gone (the connection closes)
FRAME_WAIT = 5.0  # seconds: half a frame and then nothing this long closes the connection
FRAME_LIMIT = 20.0  # seconds a frame may take to arrive in all, however it dribbles in
SEND_WAIT = 5.0  # seconds a send may wait for a client that has stopped reading
MAX_MESSAGE = 256 * 1024  # bytes: a bigger message from a client closes the connection
TEXT, BINARY, CLOSE, PING, PONG, CONTINUATION = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0


class Closed(Exception):
    """The other end has gone (or broke the rules)."""


def accept_key(key: str) -> str:
    """Sec-WebSocket-Accept for a client's Sec-WebSocket-Key."""
    return base64.b64encode(hashlib.sha1((key.strip() + GUID).encode("ascii")).digest()).decode("ascii")


def handshake_problem(headers: Any) -> tuple[int, str] | None:
    """What's wrong with a request to open a WebSocket, (HTTP status, why), or None if it's right."""
    upgrade = (headers.get("Upgrade") or "").strip().lower()
    tokens = [word.strip().lower() for word in (headers.get("Connection") or "").split(",")]
    if upgrade != "websocket" or "upgrade" not in tokens:
        return 426, "this is CentraLight's live connection, a WebSocket (for Companion's CentraLight module)"
    if (headers.get("Sec-WebSocket-Version") or "").strip() != "13":
        return 426, "use WebSocket version 13"
    try:
        right = len(base64.b64decode((headers.get("Sec-WebSocket-Key") or "").strip(), validate=True)) == 16
    except (binascii.Error, ValueError):
        right = False
    return None if right else (400, "Sec-WebSocket-Key is missing or wrong")


def accept_headers(headers: Any) -> list[tuple[str, str]]:
    """The headers of the answer that opens the connection (101 Switching Protocols)."""
    return [("Upgrade", "websocket"), ("Connection", "Upgrade"),
            ("Sec-WebSocket-Accept", accept_key(headers.get("Sec-WebSocket-Key") or ""))]


def same_origin(origin: str, host: str) -> bool:
    """A browser's Origin header names this server itself (a web page CentraLight served), not another website."""
    parsed = urlparse(origin.strip())
    return parsed.scheme in ("http", "https") and bool(host.strip()) and parsed.netloc.lower() == host.strip().lower()


def _unmask(data: bytes, mask: bytes) -> bytes:
    if not data:
        return b""
    key = (mask * (len(data) // 4 + 1))[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(len(data), "big")


def frame(opcode: int, payload: bytes) -> bytes:
    """One unmasked frame (a server's), FIN set."""
    head = bytes([0x80 | opcode])
    size = len(payload)
    if size < 126:
        head += bytes([size])
    elif size < 65536:
        head += bytes([126]) + struct.pack("!H", size)
    else:
        head += bytes([127]) + struct.pack("!Q", size)
    return head + payload


class Connection:
    """One WebSocket client: reads its frames (masked, as clients must send them) and sends text."""

    def __init__(self, sock: Any) -> None:
        self.sock = sock
        try:
            sock.settimeout(SEND_WAIT)  # (reads wait in select, so this only limits sends)
        except (AttributeError, OSError):
            pass
        self._buffer = b""
        self._parts: list[bytes] = []
        self.closed = False
        self.heard_at = time.monotonic()  # the last time anything came from the client
        self._frame_by: float | None = None  # when the frame under way must have arrived by

    def ping(self) -> None:
        self._send(frame(PING, b"centralight"))

    def send_text(self, text: str) -> None:
        self._send(frame(TEXT, text.encode("utf-8")))

    def send_json(self, data: Any) -> None:
        self.send_text(json.dumps(data, separators=(",", ":"), sort_keys=True))

    def _send(self, data: bytes) -> None:
        if self.closed:
            raise Closed()
        try:
            self.sock.sendall(data)
        except OSError as exc:
            self.closed = True
            raise Closed() from exc

    def close(self, code: int = 1000) -> None:
        if not self.closed:
            try:
                self.sock.sendall(frame(CLOSE, struct.pack("!H", code)))
            except OSError:
                pass
            self.closed = True

    def _take(self, count: int, wait: float | None) -> bytes | None:
        """count bytes, waiting up to `wait` seconds for them to start arriving (None back: nothing came). wait None:
        in the middle of a frame, where FRAME_WAIT seconds with nothing closes the connection."""
        while len(self._buffer) < count:
            middle = wait is None or bool(self._buffer)
            if middle and self._frame_by is not None and time.monotonic() > self._frame_by:
                raise Closed()  # (a frame dribbling in, a byte now and then: give up)
            ready, _, _ = select.select([self.sock], [], [], FRAME_WAIT if middle else wait)
            if not ready:
                if middle:
                    raise Closed()  # (half a frame and then nothing: give up)
                return None
            try:
                data = self.sock.recv(65536)
            except OSError as exc:
                raise Closed() from exc
            if not data:
                raise Closed()
            self.heard_at = time.monotonic()
            self._buffer += data
        part, self._buffer = self._buffer[:count], self._buffer[count:]
        return part

    def receive(self, wait: float) -> str | None:
        """The next text message, if one comes within `wait` seconds (None: none yet, or only a ping, answered: the
        caller gets its turn between frames, so a client pinging all the time can't hold up the state or a stop)."""
        while True:
            self._frame_by = None
            head = self._take(2, wait)
            if head is None:
                return None
            self._frame_by = time.monotonic() + FRAME_LIMIT
            fin, opcode = head[0] & 0x80, head[0] & 0x0F
            masked, size = head[1] & 0x80, head[1] & 0x7F
            if size == 126:
                size = struct.unpack("!H", self._take(2, None))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._take(8, None))[0]
            if not masked or size > MAX_MESSAGE:
                self.close(1002 if not masked else 1009)
                raise Closed()
            mask = self._take(4, None)
            payload = _unmask(self._take(size, None) if size else b"", mask)
            if opcode == PING:
                self._send(frame(PONG, payload))
                return None
            if opcode == PONG:
                return None
            if opcode == CLOSE:
                self.close()
                raise Closed()
            if opcode in (TEXT, BINARY, CONTINUATION):
                self._parts.append(payload)
                if sum(len(p) for p in self._parts) > MAX_MESSAGE:
                    self.close(1009)
                    raise Closed()
                if fin:
                    message, self._parts = b"".join(self._parts), []
                    return message.decode("utf-8", errors="replace")
                continue
            self.close(1003)
            raise Closed()


def serve(connection: Connection, state: Callable[[], dict], run: Callable[[dict], dict],
          stopping: Callable[[], bool] = lambda: False) -> None:
    """Look after one client until it goes: the state at once, then each time it changes; its commands run (run:
    a command -> {"ok": ...}). stopping: CentraLight is closing (the connection closes too)."""
    last = ""
    pinged = 0.0
    try:
        while not stopping():
            current = json.dumps(state(), separators=(",", ":"), sort_keys=True, default=str)
            if current != last:
                last = current
                connection.send_text('{"state":' + current + ',"type":"state"}')
            message = connection.receive(PUSH_EVERY)
            if message is not None:
                _answer(connection, message, run)
            now = time.monotonic()
            quiet = now - connection.heard_at
            if quiet > DEAD_AFTER:
                break  # (nothing from it for so long, not even a pong: it's gone)
            if quiet > PING_EVERY and now - pinged > PING_EVERY:
                connection.ping()
                pinged = now
    except Closed:
        pass
    except Exception:
        connection.close(1011)  # (something went wrong here, not "normal": the caller logs it)
        raise
    finally:
        connection.close(1001 if stopping() else 1000)


def _answer(connection: Connection, message: str, run: Callable[[dict], dict]) -> None:
    try:
        data = json.loads(message)
    except ValueError:
        connection.send_json({"type": "result", "ok": False, "error": "send JSON", "status": 400})
        return
    if not isinstance(data, dict):
        connection.send_json({"type": "result", "ok": False, "error": "send a JSON object", "status": 400})
        return
    if data.get("type") == "ping":
        connection.send_json({"type": "pong"})
        return
    if data.get("type") != "command":
        connection.send_json({"type": "result", "id": data.get("id"), "ok": False, "status": 400,
                              "error": 'send {"type": "command", "command": {"op": ...}}'})
        return
    reply = run(data.get("command"))
    connection.send_json({"type": "result", "id": data.get("id"), **reply})
