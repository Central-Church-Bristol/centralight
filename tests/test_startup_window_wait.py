"""At startup the automations wait for the window (6 Nov): building and showing it can hold Python up for seconds just
after the PC starts, and a start-up fade begun meanwhile froze, then jumped to its end."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from centralight.commands import WindowReady
from centralight.engine import WINDOW_WAIT, Engine
from centralight.model import Show
from centralight.new_show import show_from_patch
from tests.fakes import FakeOutput
from tests.test_automations import Clocks, automation, paint, uk_local

ROOT = Path(__file__).resolve().parents[1]
START_UP = automation("start-up", [{"type": "show_loads"}], [{"do": "base", "input": "evening", "transition": "cut"}])


class EngineWaitTests(unittest.TestCase):
    def make(self, wait_for_window: bool) -> Engine:
        inputs = {"evening": paint("evening")}
        show = show_from_patch().with_inputs(inputs, tuple(inputs)).with_automations({START_UP.id: START_UP}, (START_UP.id,))
        self.clocks = Clocks(datetime(2026, 11, 6, 19, 33))
        self.clocks.uptime_value = 60.0  # (a minute after the PC started)
        self.engine = Engine(show, FakeOutput(), None, clock=lambda: self.clocks.mono, wall=lambda: self.clocks.wall,
                             local_time=uk_local, uptime=lambda: self.clocks.uptime_value,
                             global_automations=Show(name="Global automations"), wait_for_window=wait_for_window)
        return self.engine

    def base(self) -> str:
        current = self.engine._base.current
        return current.input.id if current is not None else ""

    def run_for(self, seconds: float) -> None:
        for _ in range(int(seconds / 0.025)):
            self.clocks.advance(0.025)
            self.engine.tick()

    def test_the_start_up_automations_wait_for_the_window(self) -> None:
        self.make(wait_for_window=True)
        self.run_for(3.0)  # (the window being built)
        self.assertEqual(self.base(), "")
        self.engine.submit(WindowReady())
        self.run_for(0.05)
        self.assertEqual(self.base(), "evening")  # (at the next tick: "When this show loads" ran, once)
        self.assertEqual([h.what for h in self.engine._runner.history if h.automation == "start-up"].count("ran"), 1)

    def test_they_run_without_the_window_after_a_while(self) -> None:
        self.make(wait_for_window=True)
        self.run_for(WINDOW_WAIT - 1.0)
        self.assertEqual(self.base(), "")
        self.run_for(1.5)  # (a window that never says so: a fault in it can't stop the automations)
        self.assertEqual(self.base(), "evening")

    def test_without_a_window_they_run_at_once(self) -> None:
        self.make(wait_for_window=False)  # (as the tests' engines and tools have it)
        self.run_for(0.05)
        self.assertEqual(self.base(), "evening")


CHILD = r'''
import json, os, socket, sys, time
root, home, out = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
os.environ["CENTRALIGHT_TEST_HOME"] = home
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from tests import guard
guard.install()  # (no COM port; the test's own app data and shows)
os.environ["CENTRALIGHT_INSTANCE"] = f"tests-start-{os.getpid()}"
from centralight.new_show import create_show
folder = os.path.join(os.environ["CENTRALIGHT_SHOWS"], "Central Church")
show = create_show(__import__("pathlib").Path(folder))
inputs = os.path.join(folder, "inputs")
data = json.load(open(os.path.join(inputs, "follow-screen.json"), encoding="utf-8"))
data["tracks"][0][0]["source"] = "start-up test: no such source"
json.dump(data, open(os.path.join(inputs, "follow-screen.json"), "w", encoding="utf-8"))
json.dump({"format": "centralight-input", "version": 1, "id": "default", "name": "Default",
           "tracks": [[{"id": "E1", "kind": "colour"}], [], [], []], "transition": {"kind": "fade", "duration": 4}},
          open(os.path.join(inputs, "default.json"), "w", encoding="utf-8"))
os.makedirs(os.path.join(folder, "automations"), exist_ok=True)
json.dump({"format": "centralight-automation", "version": 1, "id": "start-up", "name": "Start Up", "logic": "all",
           "conditions": [{"type": "show_loads"}], "actions": [{"do": "base", "input": "default"}]},
          open(os.path.join(folder, "automations", "start-up.json"), "w", encoding="utf-8"))
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
os.makedirs(os.environ["CENTRALIGHT_APPDATA"], exist_ok=True)
json.dump({"output": "simulated", "remote_port": port}, open(os.path.join(os.environ["CENTRALIGHT_APPDATA"], "settings.json"), "w"))
import centralight.app as app
import centralight.ui.main_window as main_window
app.pc_just_started = lambda live, started_by_windows: True  # (a minute after the PC started)
seen = {}
def base(window):
    current = window.engine._base.current
    return current.input.id if current is not None else ""
show_window = main_window.MainWindow.show
def show(self):
    seen["base_before_show"] = base(self)
    show_window(self)
    seen["shown"] = time.monotonic()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    def look():
        if base(self) == "default" and "ran" not in seen:
            seen["ran"] = time.monotonic()
        if "ran" in seen or time.monotonic() - seen["shown"] > 10:
            self.quit_without_asking("as the test ends")
            QApplication.quit()
        else:
            QTimer.singleShot(20, look)
    QTimer.singleShot(0, look)
main_window.MainWindow.show = show
seen["code"] = app.main(["main.py"])
json.dump(seen, open(out, "w"))
'''


class AppStartTests(unittest.TestCase):
    def test_the_app_lets_them_run_once_its_window_is_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "seen.json"
            script = Path(tmp) / "child.py"
            script.write_text(CHILD, encoding="utf-8")
            run = subprocess.run([sys.executable, str(script), str(ROOT), str(Path(tmp) / "home"), str(out)],
                                 env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, capture_output=True, text=True,
                                 timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            seen = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(seen["code"], 0)
        self.assertNotEqual(seen["base_before_show"], "default")  # (it waited while the window was built)
        self.assertIn("ran", seen)
        self.assertLess(seen["ran"] - seen["shown"], 3.0)  # (soon after the window showed, not after WINDOW_WAIT)


if __name__ == "__main__":
    unittest.main()
