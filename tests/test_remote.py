"""The Stream Deck remote: each URL becomes the right engine command, mistakes get clear
answers, and requests from other websites or other computers are refused."""

from __future__ import annotations

import http.client
import json
import socket
import unittest
from dataclasses import replace
from types import MappingProxyType

from centralight.commands import (
    OverlayOff,
    SendToBase,
    SendToOverlay,
    SetHouse,
    SetIdentify,
    SetMaster,
    SetOverlayOpacity,
    SetVariable,
    ToBlack,
)
from centralight.engine import EngineFrame, HouseView, OverlayView
from centralight.model import GlobalVar, Input, Transition
from centralight.new_show import show_from_patch
from centralight.remote import RemoteServer, new_key
from centralight.slot import LatestSlot
from tests.gui_fixtures import with_inputs


class RecordingEngine:
    def __init__(self, show) -> None:
        self.show = show
        self.commands: list = []
        self.frames: LatestSlot[EngineFrame] = LatestSlot(EngineFrame(
            base="Warm white", base_id="warm-white",
            overlays=(OverlayView("pillars-purple", "Pillars purple", True, 1.0, 0.5), OverlayView(), OverlayView(), OverlayView()),
            house=MappingProxyType({"dimmer-4": HouseView(0.4, True, 0.4)}),
            variables=MappingProxyType({"speed": 0.5, "accent": (1.0, 0.0, 0.0), "party": 0.0}),
        ))

    def submit(self, command) -> None:
        self.commands.append(command)


class RemoteTestCase(unittest.TestCase):
    lan = False

    def setUp(self) -> None:
        show = with_inputs(show_from_patch())
        show = replace(show, globals=(
            GlobalVar("speed", "number", 0.5, 0.0, 2.0, True),
            GlobalVar("accent", "colour", "#FF0000", dashboard=True),
            GlobalVar("party", "switch", False, dashboard=True),
        ))
        nasty = Input(id="nasty", name='<script>alert("hi")</script>')
        self.show = show.with_inputs({**show.inputs, "nasty": nasty}, (*show.input_order, "nasty"))
        self.engine = RecordingEngine(self.show)
        self.key = new_key()
        self.server = RemoteServer(self.engine, port=0, lan=self.lan, key=self.key)
        self.assertTrue(self.server.start())
        self.addCleanup(self.server.stop)

    def request(self, path: str, method: str = "GET", body: object = None, headers: dict | None = None) -> tuple[int, object]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            data = json.dumps(body).encode() if body is not None else None
            connection.request(method, path, body=data, headers=headers or {})
            reply = connection.getresponse()
            raw = reply.read()
            kind = reply.getheader("Content-Type", "")
            return reply.status, json.loads(raw) if "json" in kind else raw.decode("utf-8")
        finally:
            connection.close()

    def sent(self, path: str):
        """The command a GET sends (and that it answered ok)."""
        before = len(self.engine.commands)
        status, reply = self.request(path)
        self.assertEqual((status, reply), (200, {"ok": True}), path)
        self.assertEqual(len(self.engine.commands), before + 1)
        return self.engine.commands[-1]


class RouteTests(RemoteTestCase):
    def test_inputs(self) -> None:
        self.assertEqual(self.sent("/api/input/warm-white"), SendToBase("warm-white"))
        self.assertEqual(self.sent("/api/input/Warm%20white"), SendToBase("warm-white"))  # the name works too
        self.assertEqual(self.sent("/api/input/warm-white/cut"), SendToBase("warm-white", cut=True))
        self.assertEqual(self.sent("/api/input/warm-white?transition=wipe&duration=2"),
                         SendToBase("warm-white", transition=Transition("wipe", 2.0)))
        self.assertEqual(self.sent("/api/input/pillars-purple/overlay/2"), SendToOverlay(2, "pillars-purple"))
        self.assertEqual(self.sent("/api/input/pillars-purple/overlay/2?state=off"), SendToOverlay(2, "pillars-purple", "off"))

    def test_overlays_master_house_black_identify(self) -> None:
        self.assertEqual(self.sent("/api/overlay/3/off"), OverlayOff(3))
        self.assertEqual(self.sent("/api/overlay/1/opacity?value=0.25"), SetOverlayOpacity(1, 0.25))
        self.assertEqual(self.sent("/api/overlay/1/opacity?value=0.25&fade=2"), SetOverlayOpacity(1, 0.25, 2.0))
        self.assertEqual(self.sent("/api/master?level=0.8&fade=2"), SetMaster(0.8, 2.0))
        self.assertEqual(self.sent("/api/master?level=7"), SetMaster(1.0))  # kept in range
        self.assertEqual(self.sent("/api/house/key-lights?level=0.4&fade=3"), SetHouse("dimmer-4", level=0.4, on=True, fade=3.0))
        self.assertEqual(self.sent("/api/house/dimmer-1/toggle"), SetHouse("dimmer-1", toggle=True))
        self.assertEqual(self.sent("/api/house/festoon/off"), SetHouse("dimmer-1", on=False))
        self.assertEqual(self.sent("/api/black/cut"), ToBlack(0.0, False))
        self.assertEqual(self.sent("/api/black/cut?all=1"), ToBlack(0.0, True))
        self.assertEqual(self.sent("/api/black/fade"), ToBlack(2.0, False))  # (Settings' time: 2 s to start with)
        self.assertEqual(self.sent("/api/black/fade?seconds=4&all=1"), ToBlack(4.0, True))
        self.assertEqual(self.sent("/api/identify/on"), SetIdentify(True, self.show.identify_seed))

    def test_automations_and_layouts(self) -> None:
        from centralight.commands import RunAutomation, SetAutomationEnabled, SetAutomationsPaused, SwitchLayout
        from centralight.layouts import save_layout_as
        from centralight.model import Automation

        show, christmas = save_layout_as(self.show, "Christmas")
        show = show.with_automations({"welcome": Automation("welcome", "Warm welcome")}, ("welcome",))
        self.engine.show = show
        self.assertEqual(self.sent("/api/automation/welcome/run"), RunAutomation("welcome"))
        self.assertEqual(self.sent("/api/automation/Warm%20welcome/disable"), SetAutomationEnabled("welcome", False))
        self.assertEqual(self.sent("/api/automations/pause"), SetAutomationsPaused(True))
        self.assertEqual(self.sent("/api/automations/toggle"), SetAutomationsPaused(None))
        self.assertEqual(self.sent(f"/api/layout/{christmas}"), SwitchLayout(christmas))
        self.assertEqual(self.sent("/api/layout/Main"), SwitchLayout("main"))
        self.assertEqual(self.request("/api/automation/nothing/run")[0], 404)
        self.assertEqual(self.request("/api/layout/nothing")[0], 404)

    def test_variables(self) -> None:
        self.assertEqual(self.sent("/api/var/speed?value=0.7"), SetVariable("speed", "0.7"))
        self.assertEqual(self.sent("/api/var/accent?value=FF6F59"), SetVariable("accent", "FF6F59"))
        self.assertEqual(self.sent("/api/var/party?value=toggle"), SetVariable("party", "toggle"))

    def test_mistakes_get_clear_answers(self) -> None:
        for path, status in (
            ("/api/input/nothing-here", 404),
            ("/api/input/warm-white/overlay/5", 404),
            ("/api/var/nothing?value=1", 404),
            ("/api/var/speed?value=fast", 400),
            ("/api/var/accent?value=purple", 400),
            ("/api/var/speed", 400),
            ("/api/master", 400),
            ("/api/master?level=loud", 400),
            ("/api/house/chandelier/on", 404),
            ("/api/identify/toggle", 404),
            ("/api/input/warm-white?transition=spin", 400),
            ("/api/teleport", 404),
            ("/nothing", 404),
        ):
            with self.subTest(path=path):
                got, reply = self.request(path)
                self.assertEqual(got, status)
                self.assertFalse(reply["ok"])
                self.assertTrue(reply["error"])
        self.assertEqual(self.engine.commands, [])

    def test_post_command(self) -> None:
        status, reply = self.request("/api/command", "POST", {"op": "overlay", "id": "warm-white", "slot": 4, "state": "on"})
        self.assertEqual((status, reply), (200, {"ok": True}))
        self.assertEqual(self.engine.commands[-1], SendToOverlay(4, "warm-white", "on"))
        self.request("/api/command", "POST", {"op": "var", "name": "speed", "value": 1.5})
        self.assertEqual(self.engine.commands[-1], SetVariable("speed", "1.5"))
        self.request("/api/command", "POST", {"op": "house", "light": "key-lights", "state": "toggle"})
        self.assertEqual(self.engine.commands[-1], SetHouse("dimmer-4", toggle=True))
        self.assertEqual(self.request("/api/command", "POST", {"op": "explode"})[0], 400)
        self.assertEqual(self.request("/api/command", "POST", [1, 2])[0], 400)

    def test_state(self) -> None:
        status, state = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["base"]["id"], "warm-white")
        self.assertEqual(state["overlays"][0], {"slot": 1, "id": "pillars-purple", "name": "Pillars purple", "on": True, "opacity": 0.5})
        self.assertEqual(state["variables"], {"speed": 0.5, "accent": "#FF0000", "party": 0.0})
        self.assertIn({"id": "warm-white", "name": "Warm white"}, state["inputs"])

    def test_help_page(self) -> None:
        status, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Warm white", page)
        self.assertIn("/api/input/warm-white", page)
        self.assertIn("GET request in background", page)
        self.assertNotIn('<script>alert("hi")</script>', page)  # names are escaped
        self.assertIn("&lt;script&gt;", page)

    def test_urls_for_an_input(self) -> None:
        urls = dict(self.server.urls_for_input("warm-white"))
        self.assertEqual(urls["Send to Base"], f"http://127.0.0.1:{self.server.port}/api/input/warm-white")
        self.assertEqual(urls["Overlay 2 on / off"], f"http://127.0.0.1:{self.server.port}/api/input/warm-white/overlay/2")


class SafetyTests(RemoteTestCase):
    def test_listens_on_this_pc_only(self) -> None:
        self.assertEqual(self.server._httpd.server_address[0], "127.0.0.1")

    def test_other_websites_are_refused(self) -> None:
        status, reply = self.request("/api/black/cut", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertEqual(self.engine.commands, [])
        # the help page itself (same-origin) and the Stream Deck app (no such header) are fine
        self.assertEqual(self.request("/api/black/cut", headers={"Sec-Fetch-Site": "same-origin"})[0], 200)

    def test_another_host_name_is_refused(self) -> None:
        status, _reply = self.request("/api/black/cut", headers={"Host": f"evil.example:{self.server.port}"})
        self.assertEqual(status, 403)
        self.assertEqual(self.request("/api/state", headers={"Host": f"localhost:{self.server.port}"})[0], 200)

    def test_a_busy_port_is_reported(self) -> None:
        other = RemoteServer(self.engine, port=self.server.port)
        self.assertFalse(other.start())
        self.assertIn(str(self.server.port), other.error)


class LanTests(RemoteTestCase):
    lan = True

    def test_other_computers_need_the_key(self) -> None:
        self.assertEqual(self.server._httpd.server_address[0], "0.0.0.0")
        lan_host = {"Host": f"192.168.1.20:{self.server.port}"}  # as a phone would ask
        self.assertEqual(self.request("/api/black/cut", headers=lan_host)[0], 403)
        self.assertEqual(self.request("/api/black/cut?key=wrong", headers=lan_host)[0], 403)
        self.assertEqual(self.request(f"/api/black/cut?key={self.key}", headers=lan_host)[0], 200)
        self.assertEqual(self.request("/api/black/cut")[0], 200)  # the Stream Deck on this PC doesn't need it
        page = self.request(f"/?key={self.key}", headers=lan_host)[1]
        self.assertIn(json.dumps(self.key), page)  # the page's buttons carry the key


if __name__ == "__main__":
    unittest.main()
