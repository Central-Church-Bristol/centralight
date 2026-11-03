"""Always running: start with Windows (checked on a scratch registry key, never the real one),
a second launch showing the running window, Windows' sleep / wake messages, and the tray
(closing hides the window; Quit asks first)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
import winreg
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QApplication, QMessageBox

from centralight import startup
from centralight.new_show import show_from_patch
from centralight.settings import QUIT_LEAVE, Settings
from centralight.ui.instance import InstanceServer, ask_running_copy_to_show
from centralight.ui.main_window import MainWindow
from centralight.ui.power import PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND, PBT_APMSUSPEND, WM_POWERBROADCAST, power_event
from centralight.ui.tray import Tray
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app

SCRATCH = rf"Software\CentraLight-tests-{os.getpid()}"
ROOT = Path(__file__).resolve().parents[1]


class StartWithWindowsTests(unittest.TestCase):
    key = SCRATCH + r"\Run"

    def tearDown(self) -> None:
        for path in (self.key, SCRATCH):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass

    def test_written_and_removed(self) -> None:
        self.assertFalse(startup.is_enabled(self.key))
        self.assertEqual(startup.sync(True, self.key), "")
        command = startup.read_command(self.key)
        self.assertIn("main.py", command)
        self.assertTrue(command.endswith("--minimized"))
        self.assertIn("pythonw.exe", command)
        self.assertTrue(startup.is_enabled(self.key))
        startup.set_enabled(True, self.key, command='"old.exe"')  # an out-of-date entry is corrected
        startup.sync(True, self.key)
        self.assertEqual(startup.read_command(self.key), command)
        self.assertEqual(startup.sync(False, self.key), "")
        self.assertFalse(startup.is_enabled(self.key))
        startup.set_enabled(False, self.key)  # removing twice is fine

    def test_tests_never_touch_the_real_entry(self) -> None:
        self.assertTrue(os.environ.get("CENTRALIGHT_INSTANCE"))
        self.assertFalse(startup.managed())


class PowerMessageTests(unittest.TestCase):
    def test_messages(self) -> None:
        self.assertEqual(power_event(WM_POWERBROADCAST, PBT_APMSUSPEND), "sleeping")
        self.assertEqual(power_event(WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC), "woke")
        self.assertEqual(power_event(WM_POWERBROADCAST, PBT_APMRESUMESUSPEND), "woke")
        self.assertEqual(power_event(WM_POWERBROADCAST, 0x000A), "")  # battery level, not ours
        self.assertEqual(power_event(0x0010, PBT_APMSUSPEND), "")


class SecondLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def test_a_second_launch_shows_the_window(self) -> None:
        shown: list[bool] = []
        name = f"CentraLight.Show.test-{os.getpid()}"
        server = InstanceServer(lambda: shown.append(True), name=name)
        self.addCleanup(server.close)
        self.assertTrue(server.listening)
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]);"
            "from centralight.ui.instance import ask_running_copy_to_show;"
            "sys.exit(0 if ask_running_copy_to_show(sys.argv[2]) else 1)"
        )
        launch = subprocess.Popen([sys.executable, "-c", script, str(ROOT), name])  # "a second launch": its own program
        deadline = time.monotonic() + 10
        while (not shown or launch.poll() is None) and time.monotonic() < deadline:
            process(self.app)
            time.sleep(0.01)
        self.assertEqual(launch.wait(5), 0)
        self.assertEqual(shown, [True])
        self.assertFalse(ask_running_copy_to_show(name + ".nobody", timeout_ms=200))


class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = FakeEngine(with_inputs(show_from_patch()))
        self.window = MainWindow(Settings(quit_action=QUIT_LEAVE), self.engine.show, self.engine, self.engine.fake_output,
                                 save_files=False, notes_path=Path(self._tmp.name) / "fixture_map.csv")
        self.tray = Tray(self.window)
        self.window.attach_tray(self.tray)
        self.window.resize(1280, 720)
        self.window.show()
        process(self.app, 3)

    def tearDown(self) -> None:
        self.window._shut_down = True
        self.tray.hide()
        self.window.document.close()
        self.window.close()
        self.window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def test_closing_hides_and_keeps_running(self) -> None:
        self.window.close()
        process(self.app)
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window._shut_down)  # still running
        self.tray.show_requested.emit()  # double-click the tray icon
        process(self.app)
        self.assertTrue(self.window.isVisible())

    def test_hiding_or_minimising_switches_off_identify_tests_and_show_on_lights(self) -> None:
        from PySide6.QtCore import Qt

        from centralight.commands import SendToBase, SetTestColour

        window, engine = self.window, self.engine
        window.show_page("layout")
        window.layout_page.set_identify(True)
        engine.submit(SetTestColour((255, 0, 0), ("slimpar-101",)))
        engine.settle(0.2)
        frame = engine.frames.get()
        self.assertTrue(frame.identify)
        window.close()  # into the tray
        process(self.app)
        engine.settle(0.2)
        frame = engine.frames.get()
        self.assertFalse(frame.identify)
        self.assertFalse(frame.testing)
        engine.submit(SendToBase("worship-blue", cut=True))
        engine.settle(0.2)
        self.assertEqual(engine.frames.get().base_id, "worship-blue")  # and the lights show what's sent
        self.tray.show_requested.emit()
        process(self.app)
        self.assertEqual(window._page, "layout")  # back where it was, with Identify off
        self.assertFalse(window.layout_page.identify_switch.isChecked())
        # the Editor's Show on lights, then minimising
        window.open_editor("warm-white")
        process(self.app)
        window.editor_page.solo_button.setChecked(True)
        engine.settle(0.2)
        self.assertEqual(engine.frames.get().solo, "warm-white")
        window.setWindowState(Qt.WindowState.WindowMinimized)
        process(self.app)
        engine.settle(0.2)
        self.assertEqual(engine.frames.get().solo, "")
        self.assertFalse(window.editor_page.solo_button.isChecked())
        window.setWindowState(Qt.WindowState.WindowNoState)
        process(self.app)
        self.assertTrue(window.editor_page._entered)  # its views draw again

    def test_quit_asks_first(self) -> None:
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(self.window.quit_from_tray())
        self.assertFalse(self.window._shut_down)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(QApplication, "quit"):
            self.assertTrue(self.window.quit_from_tray())
            process(self.app)
        self.assertTrue(self.window._shut_down)
        self.assertFalse(self.tray.isVisible())

    def test_tray_pause_and_status(self) -> None:
        self.tray.pause_action.trigger()
        self.engine.tick()
        frame = self.engine.frames.get()
        self.assertTrue(frame.automations.paused)
        self.window.bridge.poll()
        process(self.app)
        self.assertTrue(self.tray.pause_action.isChecked())
        self.assertIn("automations paused", self.tray.toolTip())


if __name__ == "__main__":
    unittest.main()
