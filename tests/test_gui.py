"""The window, driven like a person would (offscreen; a fake engine, no hardware)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from centralight import paths
from centralight.commands import SendToBase, SetHouse
from centralight.live_state import LiveState
from centralight.settings import OUTPUT_SIMULATED, OUTPUT_USB, QUIT_ASK, QUIT_LEAVE, QUIT_OFF, Settings
from centralight.ui import main_window as main_window_module
from centralight.ui.main_window import MainWindow
from centralight.ui.preview_page import LookPreview
from centralight.ui.quit_dialog import QuitDialog
from centralight.ui.settings_dialog import SettingsDialog
from tests.fakes import FakeEngine
from tests.qt_support import process, qt_app

KEY = 3  # DMX channel 4 (key lights), 0-based
FESTOON = 0


class WindowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self.windows: list[MainWindow] = []
        self._tmp = tempfile.TemporaryDirectory()
        self.notes_path = Path(self._tmp.name) / "fixture_map.csv"

    def tearDown(self) -> None:
        for window in self.windows:
            window._shut_down = True  # close without the quit flow
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self, settings: Settings | None = None, save_files: bool = False, show=None) -> tuple[MainWindow, FakeEngine]:
        engine = FakeEngine(show)
        window = MainWindow(settings or Settings(), engine.show, engine, engine.fake_output, save_files=save_files, notes_path=self.notes_path)
        window.resize(1280, 720)
        window.show()
        process(self.app, 4)
        self.windows.append(window)
        return window, engine

    def refresh(self, window: MainWindow) -> None:
        window.bridge.poll()
        process(self.app)

    def drag_fader(self, fader, fraction: float) -> None:
        y = fader.height() // 2
        start = QPoint(fader.width() // 2, y)
        r = fader._knob_radius()
        end = QPoint(round(r + 1 + (fader.width() - 2 * r - 2) * fraction), y)
        QTest.mousePress(fader, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(fader, end)
        QTest.mouseRelease(fader, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)


class HeaderTests(WindowTestCase):
    def test_tabs_switch_pages(self) -> None:
        window, _engine = self.make()
        for key in ("editor", "layout", "dashboard"):
            QTest.mouseClick(window.header.tabs[key], Qt.MouseButton.LeftButton)
            self.assertEqual(window.current_page(), key)
            self.assertTrue(window.header.tabs[key].isChecked())
        self.assertEqual(window.settings.page, "dashboard")

    def test_dragging_a_house_fader_turns_it_on(self) -> None:
        window, engine = self.make()
        control = window.header.house_controls["dimmer-1"]
        self.assertFalse(control.switch.isChecked())
        self.drag_fader(control.fader, 0.5)
        engine.settle(0.6)
        self.refresh(window)
        frame = engine.frames.get()
        self.assertTrue(frame.house["dimmer-1"].on)
        self.assertAlmostEqual(frame.house["dimmer-1"].level, 0.5, delta=0.02)
        self.assertTrue(120 <= engine.fake_output.last[FESTOON] <= 135)
        self.assertTrue(control.switch.isChecked())
        self.assertEqual(control.percent.text(), "50%")

    def test_switch_turns_the_light_off_and_back_on(self) -> None:
        window, engine = self.make()
        switch = window.header.house_controls["dimmer-4"].switch
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
        engine.settle(0.6)
        self.assertEqual(engine.fake_output.last[KEY], 255)  # on at its (full) level
        self.refresh(window)
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
        engine.settle(0.6)
        self.assertEqual(engine.fake_output.last[KEY], 0)
        self.refresh(window)
        self.assertFalse(switch.isChecked())

    def test_master_fader(self) -> None:
        window, engine = self.make()
        self.drag_fader(window.header.master.fader, 0.25)
        engine.settle(0.3)
        self.assertAlmostEqual(engine.frames.get().master, 0.25, delta=0.02)

    def test_cut_and_fade_to_black_click_is_the_colour_lights_shift_click_is_everything(self) -> None:
        from centralight.new_show import show_from_patch
        from tests.gui_fixtures import with_inputs

        window, engine = self.make(show=with_inputs(show_from_patch()))
        engine.submit(SendToBase("warm-white", cut=True))
        engine.submit(SetHouse("dimmer-4", on=True, fade=0))
        engine.settle(0.1)
        self.assertFalse(engine.frames.get().colour_black)  # Warm white is on
        QTest.mouseClick(window.header.cut_black, Qt.MouseButton.LeftButton)  # a click: the colour lights, at once
        engine.settle(0.05)
        self.assertTrue(engine.frames.get().colour_black)
        self.assertEqual(engine.fake_output.last[KEY], 255)  # the key lights stay as they are
        engine.submit(SendToBase("warm-white", cut=True))
        engine.settle(0.05)
        window.settings.black_fade = 1.0
        QTest.mouseClick(window.header.fade_black, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)  # everything
        engine.settle(0.5)
        self.assertTrue(0 < engine.fake_output.last[KEY] < 255)  # fading over Settings' time
        engine.settle(0.6)
        self.assertEqual(engine.fake_output.last[KEY], 0)
        self.assertTrue(engine.frames.get().colour_black)
        self.assertFalse(engine.frames.get().house["dimmer-4"].on)
        self.assertEqual(window.header.fade_black.text(), "Fade to black")  # one-off: no "on" state to show

    def test_holding_a_black_button_does_everything(self) -> None:
        window, engine = self.make()
        engine.submit(SetHouse("dimmer-4", on=True, fade=0))
        engine.settle(0.05)
        button = window.header.cut_black
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        QTest.qWait(250)
        self.assertEqual(button.text(), "Cut everything")  # while held: it says what letting it fill up does
        self.assertGreater(button.progress, 0.0)
        QTest.qWait(500)  # full (0.6 s): it fires
        engine.settle(0.05)
        self.assertEqual(engine.fake_output.last[KEY], 0)
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
        self.assertEqual(button.text(), "Cut to black")
        engine.submit(SetHouse("dimmer-4", on=True))  # and a quick click only does the colour lights
        engine.settle(0.05)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        engine.settle(0.05)
        self.assertEqual(engine.fake_output.last[KEY], 255)

    def test_engine_fades_show_on_the_faders(self) -> None:
        window, engine = self.make()
        engine.submit(SetHouse("dimmer-4", level=0.6, on=True, fade=2.0))
        engine.settle(1.0)
        self.refresh(window)
        control = window.header.house_controls["dimmer-4"]
        self.assertAlmostEqual(control.fader.level(), 0.8, delta=0.03)  # 1.0 -> 0.6, half way
        engine.settle(1.2)
        self.refresh(window)
        self.assertEqual(control.percent.text(), "60%")

    def test_lock_blocks_the_lights_controls(self) -> None:
        from centralight.new_show import show_from_patch
        from tests.gui_fixtures import with_inputs

        window, engine = self.make(show=with_inputs(show_from_patch()))
        QTest.mouseClick(window.header.lock_button, Qt.MouseButton.LeftButton)
        self.assertTrue(window.locked)
        self.assertTrue(window.lock_banner.isVisible())
        engine.submit(SendToBase("warm-white", cut=True))
        QTest.mouseClick(window.header.cut_black, Qt.MouseButton.LeftButton)
        QTest.mouseClick(window.header.fade_black, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
        QTest.mouseClick(window.header.house_controls["dimmer-4"].switch, Qt.MouseButton.LeftButton)
        engine.settle(0.6)
        frame = engine.frames.get()
        self.assertFalse(frame.colour_black)  # (the black buttons were locked)
        self.assertFalse(frame.house["dimmer-4"].on)
        QTest.mouseClick(window.header.tabs["layout"], Qt.MouseButton.LeftButton)  # tabs still work
        self.assertEqual(window.current_page(), "layout")
        QTest.mouseClick(window.header.lock_button, Qt.MouseButton.LeftButton)
        self.assertFalse(window.locked)
        self.assertTrue(window.header.cut_black.isEnabled() and window.header.fade_black.isEnabled())

    def test_output_chip_says_simulated(self) -> None:
        window, _engine = self.make()
        self.refresh(window)
        self.assertTrue(window.header.chips["output"].text().startswith("Simulated"))
        self.assertIn("nothing is sent", window.header.chips["output"].toolTip())

    def test_dashboard_lists_what_is_sent(self) -> None:
        window, engine = self.make()
        engine.submit(SetHouse("dimmer-4", level=0.8, on=True, fade=0))
        self.refresh(window)
        self.assertEqual(window.dashboard.output_rows["dimmer-4"].value.text(), "80%")
        self.assertEqual(window.dashboard.output_rows["slimpar-101"].value.text(), "0 0 0")
        view = window.dashboard.light_view
        fx = engine.show.fixture("slimpar-103")
        centre, _radius = view.dot_geometry(fx, view.canvas_rect())
        self.assertEqual(view.fixture_at(centre).id, "slimpar-103")


class SettingsTests(WindowTestCase):
    def test_choosing_usb_switches_the_output_without_opening_it_here(self) -> None:
        window, engine = self.make()

        def accept_with_usb(dialog: SettingsDialog) -> int:
            dialog.usb.setChecked(True)
            dialog.port.setCurrentIndex(0)
            return QDialog.DialogCode.Accepted

        with mock.patch.object(SettingsDialog, "exec", accept_with_usb):
            window.open_settings()
        self.assertEqual(window.settings.output, OUTPUT_USB)
        spec = engine.fake_output.specs[-1]
        self.assertEqual(spec.kind, OUTPUT_USB)
        self.assertEqual(spec.trim, 76)

    def test_cancel_changes_nothing(self) -> None:
        window, engine = self.make()

        def cancel(dialog: SettingsDialog) -> int:
            dialog.usb.setChecked(True)
            return QDialog.DialogCode.Rejected

        with mock.patch.object(SettingsDialog, "exec", cancel):
            window.open_settings()
        self.assertEqual(window.settings.output, OUTPUT_SIMULATED)
        self.assertEqual(engine.fake_output.specs, [])

    def test_dialog_lists_ports_and_keeps_an_unplugged_one(self) -> None:
        dialog = SettingsDialog(Settings(usb_port="COM7"), "C:/shows/x", None, ports=[("COM3", "USB Serial Port (looks like the Eurolite)")])
        items = [dialog.port.itemData(i) for i in range(dialog.port.count())]
        self.assertEqual(items, ["", "COM3", "COM7"])
        self.assertEqual(dialog.port.currentData(), "COM7")
        dialog.deleteLater()

    def test_look_preview_builds(self) -> None:
        preview = LookPreview()
        preview.show()
        process(self.app)
        self.assertTrue(preview.isVisible())
        preview.close()
        preview.deleteLater()


class QuitTests(WindowTestCase):
    def test_quit_turning_lights_off(self) -> None:
        window, engine = self.make(Settings(quit_action=QUIT_OFF), save_files=True)
        engine.submit(SetHouse("dimmer-4", on=True, fade=0))
        window.close()
        self.assertEqual(engine.fake_output.closed_with_blackout, True)
        state = LiveState.load(paths.live_state_path())
        self.assertTrue(state.quit_dark)
        self.assertIsNone(state.base)  # everything black: nothing playing...
        self.assertFalse(state.house["dimmer-4"].on)  # ...the key lights off, their level kept

    def test_quit_leaving_lights(self) -> None:
        window, engine = self.make(Settings(quit_action=QUIT_LEAVE), save_files=True)
        engine.submit(SetHouse("dimmer-4", on=True, fade=0))
        window.close()
        self.assertEqual(engine.fake_output.closed_with_blackout, False)
        self.assertEqual(engine.fake_output.last[KEY], 255)
        state = LiveState.load(paths.live_state_path())
        self.assertFalse(state.quit_dark)
        self.assertTrue(state.house["dimmer-4"].on)

    def test_cancel_keeps_running(self) -> None:
        window, engine = self.make(Settings(quit_action=QUIT_ASK))
        with mock.patch.object(QuitDialog, "exec", lambda _d: QDialog.DialogCode.Rejected):
            window.close()
        self.assertTrue(window.isVisible())
        self.assertIsNone(engine.fake_output.closed_with_blackout)

    def test_remember_my_choice(self) -> None:
        window, engine = self.make(Settings(quit_action=QUIT_ASK))

        def leave_and_remember(dialog: QuitDialog) -> int:
            dialog.remember.setChecked(True)
            dialog.choice = QUIT_LEAVE
            return QDialog.DialogCode.Accepted

        with mock.patch.object(QuitDialog, "exec", leave_and_remember):
            window.close()
        self.assertEqual(window.settings.quit_action, QUIT_LEAVE)
        self.assertEqual(engine.fake_output.closed_with_blackout, False)

    def test_live_state_is_saved_while_running(self) -> None:
        window, engine = self.make(save_files=True)
        path = paths.live_state_path()
        if path.exists():
            path.unlink()
        engine.submit(SetHouse("dimmer-1", level=0.4, on=True, fade=0))
        self.refresh(window)
        self.assertTrue(window._live_timer.isActive())
        window._live_timer.stop()
        window._save_live()
        state = LiveState.load(path)
        self.assertAlmostEqual(state.house["dimmer-1"].level, 0.4)
        self.assertTrue(state.house["dimmer-1"].on)


class ShortcutTests(WindowTestCase):
    def test_minimum_size_and_pages_exist(self) -> None:
        window, _engine = self.make()
        self.assertLessEqual(window.minimumWidth(), main_window_module.MIN_WIDTH)
        self.assertEqual(set(window.pages), {"dashboard", "console", "editor", "layout", "automation"})  # (Console: M15)


if __name__ == "__main__":
    unittest.main()
