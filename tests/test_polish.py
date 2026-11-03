"""M9 polish: the launcher (CentraLight.exe with the logo, built with the C# compiler that comes with Windows) and
Diagnostics (what it shows, Copy, and the ways to open it)."""

from __future__ import annotations

import dataclasses
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel

from centralight.new_show import show_from_patch
from centralight.settings import Settings
from centralight.ui.diagnostics import DiagnosticsDialog, ProcessMeter, report
from centralight.ui.main_window import MainWindow
from centralight.ui.settings_dialog import SettingsDialog
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app

ROOT = Path(__file__).resolve().parents[1]
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    @unittest.skipUnless(CSC.is_file(), "needs the C# compiler that comes with Windows")
    def test_it_builds_with_the_logo(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "tools"))
        import build_launcher

        with tempfile.TemporaryDirectory() as folder:
            out, ico_path = Path(folder) / "CentraLight.exe", Path(folder) / "icon.ico"
            committed = (ROOT / "launcher" / "icon.ico").read_bytes()
            self.assertEqual(build_launcher.main(["--out", str(out), "--ico", str(ico_path)]), 0)
            self.assertEqual((ROOT / "launcher" / "icon.ico").read_bytes(), committed)  # the test leaves it alone
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 20_000)  # the icon is in it (seven sizes of the logo)
            data = out.read_bytes()
            self.assertEqual(data[:2], b"MZ")  # a Windows program
            for ico in (ico_path.read_bytes(), committed):
                self.assertEqual(ico[:4], b"\x00\x00\x01\x00")  # an icon file...
                self.assertEqual(int.from_bytes(ico[4:6], "little"), 7)  # ...with seven sizes

    def test_the_source_starts_the_app_quietly(self) -> None:
        source = (ROOT / "launcher" / "Program.cs").read_text(encoding="utf-8")
        self.assertIn("pythonw.exe", source)  # no console window
        self.assertIn("main.py", source)
        self.assertIn("run.bat", source)  # the first time, it sets everything up


class DiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_what_it_reports(self) -> None:
        engine = FakeEngine(with_inputs(show_from_patch()))
        engine.settle(1.0)
        frame = engine.frames.get()
        lines = dict(report(frame, ProcessMeter()))
        for key in ("Engine", "Problems", "Output", "NDI", "Audio", "Playing", "Automations", "CentraLight's CPU", "CentraLight's memory"):
            self.assertIn(key, lines)
        self.assertIn("ticks a second", lines["Engine"])
        self.assertEqual(lines["Problems"], "none")
        self.assertEqual(lines["Audio"], "off")
        listening = dataclasses.replace(frame, audio_state="listening", audio_detail="Speakers", audio_rate=47.0)
        self.assertIn("sound arriving 47 times a second", dict(report(listening))["Audio"])
        self.assertEqual(report(None), [("Engine", "not started yet")])

    def test_process_meter(self) -> None:
        meter = ProcessMeter()
        self.assertIsNone(meter.cpu())  # the first look only starts measuring
        started = time.perf_counter()
        while time.perf_counter() - started < 0.3:  # busy for a moment: that shows
            sum(i * i for i in range(10_000))
        cpu = meter.cpu()
        self.assertIsNotNone(cpu)
        self.assertGreater(cpu, 20.0)
        self.assertGreater(meter.memory_mb(), 20)

    def test_the_window_copy_and_ways_in(self) -> None:
        engine = FakeEngine(with_inputs(show_from_patch()))
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv")
        window.show()
        process(self.app, 3)
        window.bridge.poll()
        dialog = window.open_diagnostics()
        self.assertIsInstance(dialog, DiagnosticsDialog)
        self.assertIs(window.open_diagnostics(), dialog)  # only one
        text = dialog.copy()
        self.assertTrue(text.startswith("CentraLight diagnostics"))
        self.assertEqual(QGuiApplication.clipboard().text(), text)
        self.assertIn("Engine:", text)
        self.assertIn("CentraLight's CPU: measuring", text)  # Copy shows what's on screen: it doesn't restart the measuring
        dialog._rows["NDI Old feed"] = label = QLabel("gone")  # a row from a feed nothing uses any more...
        dialog.form.addRow("NDI Old feed", label)
        dialog.refresh()
        self.assertFalse(label.isVisibleTo(dialog))  # ...is hidden
        self.assertTrue(dialog._rows["Engine"].isVisibleTo(dialog))
        dialog.close()
        settings = SettingsDialog(Settings(), "", None)
        settings._diagnostics()
        self.assertTrue(settings.diagnostics_requested)
        window._shut_down = True
        window.editor_page.shutdown()
        window.document.close()
        window.close()
        window.deleteLater()
        process(self.app)


class EngineSleepTests(unittest.TestCase):
    def test_it_sleeps_instead_of_spinning(self) -> None:
        from centralight.timing import sleep_until

        late = []
        cpu = time.thread_time()
        for _ in range(20):
            deadline = time.perf_counter() + 0.02
            sleep_until(deadline)
            late.append(time.perf_counter() - deadline)
        self.assertGreaterEqual(min(late), 0.0)  # never early
        self.assertLess(sorted(late)[len(late) // 2], 0.004)  # typically 0.3 ms late on the church PC
        self.assertLess(time.thread_time() - cpu, 0.01)  # spinning for the last 2 ms used to cost ~30 ms here

    def test_it_stops_when_asked(self) -> None:
        from centralight.timing import sleep_until

        running = [True]
        threading.Timer(0.05, lambda: running.__setitem__(0, False)).start()
        started = time.perf_counter()
        sleep_until(started + 5.0, lambda: running[0])
        self.assertLess(time.perf_counter() - started, 0.5)


if __name__ == "__main__":
    unittest.main()
