"""M14's two extras: every quit is written in the session log with the choice made (so a quit can be told from a
crash), and the soak tool's own process uses its temporary app data, so its lines never reach the real session.log."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QDialog, QMessageBox

from centralight import paths
from centralight.log import log_info
from centralight.settings import QUIT_ASK, QUIT_LEAVE, QUIT_OFF, Settings
from centralight.ui.quit_dialog import QuitDialog
from tests.test_gui import WindowTestCase


class QuitLogTests(WindowTestCase):
    def log_since(self, start: int) -> str:
        path = paths.session_log_path()
        return path.read_text(encoding="utf-8", errors="replace")[start:] if path.exists() else ""

    def log_size(self) -> int:
        path = paths.session_log_path()
        return len(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else 0

    def test_closing_the_window_with_the_lights_off(self) -> None:
        window, _engine = self.make(Settings(quit_action=QUIT_OFF))
        start = self.log_size()
        window.close()
        text = self.log_since(start)
        self.assertIn("Quit by closing the window: turn the lights off (Settings say so)", text)
        self.assertLess(text.index("Quit by closing"), text.index("CentraLight closed"))  # (before it stops)

    def test_asked_and_remembered(self) -> None:
        window, _engine = self.make(Settings(quit_action=QUIT_ASK))

        def leave_and_remember(dialog: QuitDialog) -> int:
            dialog.remember.setChecked(True)
            dialog.choice = QUIT_LEAVE
            return QDialog.DialogCode.Accepted

        start = self.log_size()
        with mock.patch.object(QuitDialog, "exec", leave_and_remember):
            window.close()
        self.assertIn("Quit by closing the window: leave the lights as they are (chosen when asked, and remembered)",
                      self.log_since(start))

    def test_cancelled(self) -> None:
        window, _engine = self.make(Settings(quit_action=QUIT_ASK))
        start = self.log_size()
        with mock.patch.object(QuitDialog, "exec", lambda _d: QDialog.DialogCode.Rejected):
            window.close()
        self.assertIn("Quit by closing the window: cancelled (CentraLight carries on)", self.log_since(start))
        self.assertNotIn("CentraLight closed", self.log_since(start))

    def test_from_the_tray_menu(self) -> None:
        window, _engine = self.make(Settings(quit_action=QUIT_LEAVE))
        start = self.log_size()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(window.quit_from_tray())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                mock.patch("centralight.ui.main_window.QTimer.singleShot"):  # (not QApplication.quit in a test)
            self.assertTrue(window.quit_from_tray())
        text = self.log_since(start)
        self.assertIn("Quit from the tray menu: cancelled", text)
        self.assertIn("Quit from the tray menu: leave the lights as they are (Settings say so)", text)

    def test_windows_logging_off(self) -> None:
        window, engine = self.make(Settings(quit_action=QUIT_OFF))
        start = self.log_size()
        window.quit_without_asking("as Windows logs off or shuts down")
        window.quit_without_asking("as Windows logs off or shuts down")  # (Qt says it again as it ends: once is enough)
        text = self.log_since(start)
        self.assertEqual(text.count("Quit, as Windows logs off or shuts down: turn the lights off (Settings say so)"), 1)
        self.assertTrue(engine.fake_output.closed_with_blackout)

    def test_windows_logging_off_when_settings_say_ask(self) -> None:
        window, _engine = self.make(Settings(quit_action=QUIT_ASK))
        start = self.log_size()
        window.quit_without_asking("as Windows logs off or shuts down")
        self.assertIn("Quit, as Windows logs off or shuts down: leave the lights as they are (there's no time to ask)",
                      self.log_since(start))


class SoakToolTests(unittest.TestCase):
    def test_its_own_lines_go_to_its_own_session_log(self) -> None:
        spec = importlib.util.spec_from_file_location("soak_tool", Path(__file__).resolve().parents[1] / "tools" / "soak.py")
        soak = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(soak)
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ):  # (the tests' own folders come back)
            home = Path(folder)
            soak.use_soak_folders(home)
            self.assertEqual(paths.app_data_dir(), home / "appdata")
            self.assertEqual(paths.session_log_path(), home / "appdata" / "session.log")
            log_info("a line from the soak tool itself")
            self.assertIn("a line from the soak tool itself", (home / "appdata" / "session.log").read_text(encoding="utf-8"))
        self.assertNotEqual(paths.app_data_dir(), home / "appdata")


if __name__ == "__main__":
    unittest.main()
