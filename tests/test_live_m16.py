"""M16: the live connection (/api/ws), the WebSocket Companion's CentraLight module keeps open. The handshake and the
frames; the state at once and whenever it changes (not every frame); commands as URL paths or as POST /api/command's
JSON; the same refusals as the other URLs (another website, a Host that isn't this PC, the key from another computer);
pings both ways; dead connections and a stopped remote closing it. Then, with Companion's own Node 22 (or any Node),
the module's own tests, and every one of its actions against this server."""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from centralight import live_socket, remote
from centralight.commands import (
    ConsoleChase,
    ConsoleRelease,
    SendToBase,
    SendToOverlay,
    SetAutomationsPaused,
    SetMaster,
    SetVariable,
)
from centralight.console import ConsoleView
from centralight.model import GlobalVar, Show
from centralight.new_show import show_from_patch
from centralight.remote import MAX_LIVE, RemoteServer, live_state_of
from tests import test_remote
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.test_automations import automation

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "companion" / "centralight"
COMPANION_NODE = Path(r"C:\Program Files\Companion\resources\node-runtimes\node22\node.exe")


def automations_show() -> Show:
    """A show with an automation on and one off, and a show variable."""
    end = automation("end-of-service", [{"type": "days", "days": ["sun"]}], [{"do": "release_console"}])
    spare = automation("spare", [{"type": "days", "days": ["mon"]}], [{"do": "release_console"}], enabled=False)
    show = replace(with_inputs(show_from_patch()), globals=(GlobalVar("speed", "number", 0.5, 0.0, 2.0, True),))
    return show.with_automations({end.id: end, spare.id: spare}, (end.id, spare.id))


def node() -> str | None:
    """Companion's own Node 22 if it's installed (the module runs on it), else any Node."""
    return str(COMPANION_NODE) if COMPANION_NODE.exists() else shutil.which("node")


class Client:
    """A bare WebSocket client on a raw socket (what Companion's module does, by hand)."""

    def __init__(self, port: int, path: str = "/api/ws", headers: dict[str, str | None] | None = None) -> None:
        self.key = base64.b64encode(os.urandom(16)).decode()
        wanted = {"Host": f"127.0.0.1:{port}", "Upgrade": "websocket", "Connection": "Upgrade",
                  "Sec-WebSocket-Key": self.key, "Sec-WebSocket-Version": "13", **(headers or {})}
        request = f"GET {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in wanted.items() if v is not None)
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.sendall((request + "\r\n").encode())
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            data = self.sock.recv(4096)
            if not data:
                break
            self.buffer += data
        head, _, self.buffer = self.buffer.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        self.status = int(lines[0].split()[1])
        self.headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        if self.status != 101:  # (read the refusal's text too, so closing is tidy)
            self.body = self._read(int(self.headers.get("content-length") or 0))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def send_frame(self, opcode: int, payload: bytes, masked: bool = True, size: int | None = None) -> None:
        size = len(payload) if size is None else size
        bit = 0x80 if masked else 0
        head = bytes([0x80 | opcode])
        if size < 126:
            head += bytes([bit | size])
        elif size < 65536:
            head += bytes([bit | 126]) + struct.pack("!H", size)
        else:
            head += bytes([bit | 127]) + struct.pack("!Q", size)
        if masked:
            mask = os.urandom(4)
            head += mask
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + payload)

    def send_json(self, data: object) -> None:
        self.send_frame(0x1, json.dumps(data).encode())

    def _read(self, count: int) -> bytes:
        while len(self.buffer) < count:
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionError("closed")
            self.buffer += data
        part, self.buffer = self.buffer[:count], self.buffer[count:]
        return part

    def read_frame(self, timeout: float = 3.0) -> tuple[int, bytes]:
        self.sock.settimeout(timeout)
        head = self._read(2)
        size = head[1] & 0x7F
        if size == 126:
            size = struct.unpack("!H", self._read(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", self._read(8))[0]
        return head[0] & 0x0F, self._read(size)

    def message(self, kind: str, timeout: float = 3.0) -> dict:
        """The next message of this type (others, and pings, skipped)."""
        deadline = time.monotonic() + timeout
        while True:
            opcode, payload = self.read_frame(max(0.05, deadline - time.monotonic()))
            if opcode == 0x8:
                raise ConnectionError(f"closed: {payload!r}")
            if opcode == 0x1 and json.loads(payload).get("type") == kind:
                return json.loads(payload)

    def command(self, number: int, command: object) -> dict:
        self.send_json({"type": "command", "id": number, "command": command})
        return self.message("result")


class HandshakeTests(unittest.TestCase):
    def test_the_accept_key(self) -> None:
        self.assertEqual(live_socket.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")  # (RFC 6455)

    def test_what_a_handshake_needs(self) -> None:
        good = {"Upgrade": "websocket", "Connection": "keep-alive, Upgrade", "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": base64.b64encode(b"0123456789abcdef").decode()}
        self.assertIsNone(live_socket.handshake_problem(good))
        self.assertEqual(live_socket.handshake_problem({**good, "Upgrade": ""})[0], 426)
        self.assertEqual(live_socket.handshake_problem({**good, "Connection": "keep-alive"})[0], 426)
        self.assertEqual(live_socket.handshake_problem({**good, "Sec-WebSocket-Version": "8"})[0], 426)
        self.assertEqual(live_socket.handshake_problem({**good, "Sec-WebSocket-Key": "short"})[0], 400)
        self.assertEqual(live_socket.handshake_problem({**good, "Sec-WebSocket-Key": "!!!!"})[0], 400)
        self.assertIn(("Sec-WebSocket-Accept", live_socket.accept_key(good["Sec-WebSocket-Key"])),
                      live_socket.accept_headers(good))

    def test_same_origin(self) -> None:
        self.assertTrue(live_socket.same_origin("http://127.0.0.1:8746", "127.0.0.1:8746"))
        self.assertTrue(live_socket.same_origin("http://192.168.1.20:8746", "192.168.1.20:8746"))
        self.assertFalse(live_socket.same_origin("https://evil.example", "127.0.0.1:8746"))
        self.assertFalse(live_socket.same_origin("null", "127.0.0.1:8746"))
        self.assertFalse(live_socket.same_origin("http://127.0.0.1:8746", ""))

    def test_frames(self) -> None:
        self.assertEqual(live_socket.frame(live_socket.TEXT, b"x" * 125)[:2], b"\x81\x7d")
        self.assertEqual(live_socket.frame(live_socket.TEXT, b"x" * 126)[:4], b"\x81\x7e\x00\x7e")
        self.assertEqual(live_socket.frame(live_socket.TEXT, b"x" * 65536)[:10], b"\x81\x7f" + struct.pack("!Q", 65536))
        self.assertEqual(live_socket._unmask(live_socket._unmask(b"hello there", b"\x01\x02\x03\x04"), b"\x01\x02\x03\x04"),
                         b"hello there")


class LiveTests(test_remote.RemoteTestCase):
    def connect(self, path: str = "/api/ws", headers: dict | None = None) -> Client:
        client = Client(self.server.port, path, headers)
        self.addCleanup(client.close)
        return client

    def test_the_state_comes_at_once_then_when_it_changes(self) -> None:
        client = self.connect()
        self.assertEqual(client.status, 101)
        self.assertEqual(client.headers["sec-websocket-accept"], live_socket.accept_key(client.key))
        state = client.message("state")["state"]
        self.assertEqual(state["show"], self.show.name)
        self.assertEqual(state["base"], {"id": "warm-white", "name": "Warm white"})  # (no progress: it changes every frame)
        self.assertEqual([c["id"] for c in state["overlay"]["copies"]], ["pillars-purple", "christmas"])
        self.assertFalse(any("progress" in c for c in state["overlay"]["copies"]))
        self.assertEqual(state["automations"], [{"id": a.id, "name": a.name, "enabled": a.enabled}
                                                for a in self.show.ordered_automations()])
        self.assertEqual(state["global_automations"], [])
        self.assertIs(state["paused"], False)
        self.assertEqual(state["console"], {"held": 0, "lights": []})
        self.assertEqual([light["id"] for light in state["lights"]], [fx.id for fx in self.show.colour_fixtures()])
        frame = self.engine.frames.get()
        self.engine.frames.put(replace(frame, base_progress=0.4))
        with self.assertRaises(socket.timeout):
            client.read_frame(0.4)  # (a transition moving on isn't news)
        self.engine.frames.put(replace(frame, base="Worship blue", base_id="worship-blue"))
        self.assertEqual(client.message("state", 2.0)["state"]["base"]["id"], "worship-blue")
        self.engine.frames.put(replace(frame, console=ConsoleView(held={"slimpar-101": {"red": 255}})))
        self.assertEqual(client.message("state", 2.0)["state"]["console"], {"held": 1, "lights": ["slimpar-101"]})

    def test_commands_as_paths_and_as_json(self) -> None:
        client = self.connect()
        client.message("state")
        self.assertEqual(client.command(1, {"path": "input/warm-white/cut"}), {"type": "result", "id": 1, "ok": True})
        self.assertEqual(self.engine.commands[-1], SendToBase("warm-white", cut=True))
        self.assertTrue(client.command(2, {"path": "/api/master", "params": {"level": 0.5, "fade": 2}})["ok"])
        self.assertEqual(self.engine.commands[-1], SetMaster(0.5, 2.0))
        self.assertTrue(client.command(3, {"op": "overlay", "id": "pillars-purple"})["ok"])
        self.assertEqual(self.engine.commands[-1], SendToOverlay("pillars-purple"))
        self.assertTrue(client.command(4, {"path": "var/speed", "params": {"value": "0.7"}})["ok"])
        self.assertEqual(self.engine.commands[-1], SetVariable("speed", "0.7"))
        self.assertTrue(client.command(5, {"path": "live/speed", "params": {"bpm": "90"}})["ok"])
        self.assertEqual(self.engine.commands[-1], ConsoleChase("speed", bpm=90.0))
        self.assertTrue(client.command(6, {"path": "live/release"})["ok"])
        self.assertEqual(self.engine.commands[-1], ConsoleRelease())
        count = len(self.engine.commands)
        refused = client.command(7, {"path": "input/nobody"})
        self.assertEqual((refused["id"], refused["ok"], refused["status"]), (7, False, 404))
        self.assertIn("no input called", refused["error"])
        self.assertEqual(client.command(8, {"path": "state"})["status"], 404)  # (only commands here)
        self.assertEqual(client.command(9, None)["status"], 400)
        client.send_frame(0x1, b"not json")
        self.assertEqual(client.message("result")["status"], 400)
        client.send_json({"type": "hello"})
        self.assertEqual(client.message("result")["status"], 400)
        client.send_json({"type": "ping"})
        self.assertEqual(client.message("pong"), {"type": "pong"})
        self.assertEqual(len(self.engine.commands), count)  # (nothing refused reached the engine)

    def test_its_pings_are_answered(self) -> None:
        client = self.connect()
        client.message("state")
        client.send_frame(0x9, b"hi")
        self.assertEqual(client.read_frame(), (0xA, b"hi"))

    def test_refused_like_the_other_urls(self) -> None:
        self.assertEqual(self.connect(headers={"Sec-Fetch-Site": "cross-site"}).status, 403)
        self.assertEqual(self.connect(headers={"Origin": "https://evil.example"}).status, 403)
        self.assertEqual(self.connect(headers={"Origin": f"http://127.0.0.1:{self.server.port}"}).status, 101)
        self.assertEqual(self.connect(headers={"Host": "evil.example"}).status, 403)  # (DNS rebinding)
        self.assertEqual(self.connect(headers={"Sec-WebSocket-Version": "8"}).status, 426)
        self.assertEqual(self.connect(headers={"Sec-WebSocket-Key": "nope"}).status, 400)
        self.assertEqual(self.request("/api/ws")[0], 426)  # (a plain GET)
        self.server.live = MAX_LIVE
        self.assertEqual(self.connect().status, 503)
        self.server.live = 0

    def test_broken_frames_close_it(self) -> None:
        client = self.connect()
        client.message("state")
        client.send_frame(0x1, b"{}", masked=False)  # (clients must mask)
        self.assertEqual(client.read_frame(), (0x8, struct.pack("!H", 1002)))
        client = self.connect()
        client.message("state")
        client.send_frame(0x1, b"", size=live_socket.MAX_MESSAGE + 1)  # (too big: closed before it's read)
        self.assertEqual(client.read_frame(), (0x8, struct.pack("!H", 1009)))

    def test_closing_it(self) -> None:
        client = self.connect()
        client.message("state")
        self.assertEqual(self.server.live, 1)
        client.send_frame(0x8, struct.pack("!H", 1000))
        self.assertEqual(client.read_frame()[0], 0x8)
        deadline = time.monotonic() + 2
        while self.server.live and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.server.live, 0)

    def test_stopping_the_remote_closes_it(self) -> None:
        client = self.connect()
        client.message("state")
        self.server.stop()
        self.assertEqual(client.read_frame(2.0), (0x8, struct.pack("!H", 1001)))

    def test_a_connection_that_has_gone_quiet_is_closed(self) -> None:
        with mock.patch.object(live_socket, "PING_EVERY", 0.2), mock.patch.object(live_socket, "DEAD_AFTER", 0.8):
            client = self.connect()
            client.message("state")
            self.assertEqual(client.read_frame(2.0), (0x9, b"centralight"))  # (a ping first; this client never answers)
            opcode = 0x9
            while opcode == 0x9:
                opcode, payload = client.read_frame(2.0)
            self.assertEqual((opcode, payload), (0x8, struct.pack("!H", 1000)))


class LiveLanTests(test_remote.RemoteTestCase):
    lan = True

    def test_another_computer_needs_the_key(self) -> None:
        lan_host = {"Host": f"192.168.1.20:{self.server.port}"}
        for path, status in (("/api/ws", 403), ("/api/ws?key=wrong", 403), (f"/api/ws?key={self.key}", 101)):
            client = Client(self.server.port, path, lan_host)
            self.addCleanup(client.close)
            self.assertEqual(client.status, status, path)
        client = Client(self.server.port)  # (this PC doesn't need it)
        self.addCleanup(client.close)
        self.assertEqual(client.status, 101)
        client = Client(self.server.port, "/api/ws?key=%C3%A9t%C3%A9", lan_host)  # (any characters: refused, not a crash)
        self.addCleanup(client.close)
        self.assertEqual(client.status, 403)


def masked(opcode: int, payload: bytes) -> bytes:
    mask = b"\x01\x02\x03\x04"
    return bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class ConnectionTests(unittest.TestCase):
    """The connection's own rules, on a pair of sockets (the review of 5 Nov)."""

    def pair(self):
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close)
        self.addCleanup(theirs.close)
        return live_socket.Connection(ours), theirs

    def test_a_ping_gives_the_loop_its_turn(self) -> None:
        connection, client = self.pair()
        client.sendall(masked(0x9, b"hi") + masked(0x9, b"again"))
        self.assertIsNone(connection.receive(1.0))  # (answered, and back to the caller: state and stop checks go on)
        self.assertIsNone(connection.receive(1.0))
        client.settimeout(2)
        self.assertEqual(client.recv(64), live_socket.frame(live_socket.PONG, b"hi") + live_socket.frame(live_socket.PONG, b"again"))

    def test_a_frame_dribbling_in_is_given_up(self) -> None:
        connection, client = self.pair()
        whole = masked(0x1, b"0123456789")
        client.sendall(whole[:6])  # (the head and the mask, then a byte now and then)

        def dribble() -> None:
            for byte in whole[6:]:
                time.sleep(0.15)
                try:
                    client.sendall(bytes([byte]))
                except OSError:
                    return

        import threading

        threading.Thread(target=dribble, daemon=True).start()
        started = time.monotonic()
        with mock.patch.object(live_socket, "FRAME_LIMIT", 0.5), self.assertRaises(live_socket.Closed):
            connection.receive(1.0)
        self.assertLess(time.monotonic() - started, 1.2)

    def test_an_unexpected_error_closes_with_1011(self) -> None:
        connection, client = self.pair()

        def broken() -> dict:
            raise RuntimeError("broken")

        with self.assertRaises(RuntimeError):
            live_socket.serve(connection, broken, lambda command: {"ok": True})
        client.settimeout(2)
        self.assertEqual(client.recv(64), live_socket.frame(live_socket.CLOSE, struct.pack("!H", 1011)))


class IdleConnectionTests(test_remote.RemoteTestCase):
    def test_a_connection_that_sends_nothing_is_closed(self) -> None:
        with mock.patch.object(remote._Handler, "timeout", 0.5):
            idle = socket.create_connection(("127.0.0.1", self.server.port), timeout=5)
            self.addCleanup(idle.close)
            started = time.monotonic()
            self.assertEqual(idle.recv(64), b"")  # (closed by CentraLight: no thread held for ever)
            self.assertLess(time.monotonic() - started, 3.0)


class LiveStateTests(unittest.TestCase):
    def test_from_a_real_engine(self) -> None:
        engine = FakeEngine(automations_show())
        engine.submit(SetMaster(0.333, 0.0))
        engine.submit(SendToBase("worship-blue"))
        engine.settle(0.05)  # (mid-transition)
        state = live_state_of(engine, [], "Sun 09:30 Morning")
        self.assertEqual(state["master"], 0.33)
        self.assertNotIn("progress", state["base"])
        self.assertEqual(state["automations"], [{"id": "end-of-service", "name": "End Of Service", "enabled": True},
                                                {"id": "spare", "name": "Spare", "enabled": False}])
        self.assertIs(state["paused"], False)
        self.assertEqual(state["next"], "Sun 09:30 Morning")
        self.assertEqual(len(state["lights"]), len(engine.show.colour_fixtures()))
        engine.submit(SetAutomationsPaused(True))
        engine.settle(0.05)
        self.assertIs(live_state_of(engine)["paused"], True)
        json.dumps(live_state_of(engine))  # (all of it can be sent)

    def test_the_next_automation_is_worked_out_now_and_then(self) -> None:
        engine = FakeEngine(automations_show())
        server = RemoteServer(engine, port=0)
        moment = remote.datetime(2026, 11, 8, 9, 30)
        found = [(moment, engine.show.automations["end-of-service"], False)]
        with mock.patch.object(remote, "upcoming", return_value=found) as upcoming:
            self.assertEqual(server.next_automation(), "Sun 09:30 End Of Service")
            each = upcoming.call_count  # (the show's, and the global automations')
            self.assertEqual(each, 2)
            self.assertEqual(server.next_automation(), "Sun 09:30 End Of Service")
            self.assertEqual(upcoming.call_count, each)  # (kept)
            engine.show = replace(engine.show, name="Another")
            server.next_automation()
            self.assertEqual(upcoming.call_count, 2 * each)  # (another show: worked out again)
            with mock.patch.object(remote, "NEXT_FRESH", 0.0):
                server.next_automation()
            self.assertEqual(upcoming.call_count, 3 * each)  # (and every NEXT_FRESH seconds)
            self.assertEqual(len(found), 1)  # (what upcoming() gave isn't changed)
        with mock.patch.object(remote, "upcoming", side_effect=ValueError("broken")), \
                mock.patch.object(remote, "NEXT_FRESH", 0.0), mock.patch.object(remote, "log_exception"):
            self.assertEqual(server.next_automation(), "")  # (never stops the live connection)


@unittest.skipUnless(node(), "needs Node (Companion's own, or any)")
class CompanionModuleTests(unittest.TestCase):
    def test_the_modules_own_tests(self) -> None:
        result = subprocess.run([node(), "--test", "test/lib.test.js", "test/module.test.js"], cwd=MODULE,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-2000:])
        self.assertIn("# fail 0", result.stdout)

    def test_every_action_against_this_remote(self) -> None:
        show = automations_show()
        engine = test_remote.RecordingEngine(show)
        morning = automation("sunday-morning", [{"type": "days", "days": ["sun"]}], [{"do": "release_console"}])
        engine.global_automations = Show(name="Global automations").with_automations({morning.id: morning}, (morning.id,))
        shows = Path(tempfile.mkdtemp(prefix="cl-live-"))
        self.addCleanup(shutil.rmtree, shows, True)
        (shows / "Evening").mkdir()
        (shows / "Evening" / "show.json").write_text(json.dumps({"name": "Evening"}), encoding="utf-8")
        server = RemoteServer(engine, port=0, shows_root=shows)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)
        result = subprocess.run([node(), str(MODULE / "test" / "interop.js"), str(server.port)], capture_output=True,
                                text=True, timeout=60)
        lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first = lines[0]
        self.assertEqual((first["step"], first["show"], first["base"]), ("state", show.name, "warm-white"))
        answers = {line["action"]: (line["ok"], line["status"]) for line in lines if line["step"] == "result"}
        refused = {action: answer for action, answer in answers.items() if answer != (True, 200)}
        self.assertEqual(refused, {"countdown": (False, 409)})  # (no countdown now: the only one refused)
        self.assertEqual(len(answers), next(line["count"] for line in lines if line["step"] == "pressed"))
        self.assertEqual(len(answers), 19)  # (every action: none of them needs a light chosen first)
        self.assertEqual(lines[-1], {"step": "closed", "code": 1000})
        self.assertEqual(len(engine.commands), len(answers) - 1)


    @unittest.skipUnless(list((ROOT / "companion").glob("centralight-*.tgz")), "the module isn't built")
    def test_the_built_module_in_a_stand_in_companion(self) -> None:
        """The .tgz Companion imports: its bundled main.js, started as Companion starts a module (an IPC channel, the
        register and init calls), connects, makes its actions from the show, lights a key, and a press reaches here."""
        import tarfile

        package = sorted((ROOT / "companion").glob("centralight-*.tgz"))[-1]
        unpacked = Path(tempfile.mkdtemp(prefix="cl-module-"))
        self.addCleanup(shutil.rmtree, unpacked, True)
        with tarfile.open(package) as tar:
            tar.extractall(unpacked, filter="data")
        engine = test_remote.RecordingEngine(automations_show())
        server = RemoteServer(engine, port=0)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)
        result = subprocess.run([node(), str(MODULE / "test" / "host_check.js"), str(unpacked / "pkg"), str(server.port)],
                                capture_output=True, text=True, timeout=60)
        lines = {line["step"]: line for line in map(json.loads, result.stdout.splitlines()) if line}
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("warm-white", lines["actions"]["inputs"])
        self.assertEqual(lines["actions"]["count"], 19)
        self.assertIs(lines["feedback"]["value"], True)  # (Warm white is on Base: its key lights)
        self.assertEqual(lines["variables"]["master"], 100)
        self.assertEqual(lines["variables"]["show"], engine.show.name)  # (connected to this test's server, not another)
        self.assertGreater(lines["presets"]["count"], 10)
        self.assertEqual(lines["config"]["fields"], ["about", "host", "port", "key"])
        self.assertEqual(lines["done"]["warnings"], [])
        self.assertIn(SendToBase("worship-blue", cut=True), engine.commands)


if __name__ == "__main__":
    unittest.main()
