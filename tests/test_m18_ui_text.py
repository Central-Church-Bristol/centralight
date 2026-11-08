"""8 Nov: short on-screen text (explanations in tooltips, labels that say what they refer to), and the show variables
a click away (Show ▾ → Show variables…, and Edit on the Dashboard's Variables)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.qt_support import process, qt_app


class ShortTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def test_settings_keeps_explanations_in_tooltips(self) -> None:
        from PySide6.QtWidgets import QGroupBox, QLabel

        from centralight.settings import Settings
        from centralight.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(Settings(), "")
        self.addCleanup(dialog.deleteLater)
        hints = [label.text() for label in dialog.findChildren(QLabel) if label.objectName() == "Hint" and label.text()]
        self.assertEqual(hints, ["Only one program can use the Eurolite at a time."])  # (the one safety line)
        for control in (dialog.black_fade, dialog.release_fade, dialog.countdown, dialog.default_show, dialog.quit_action):
            self.assertGreater(len(control.toolTip()), 30, control)
        groups = {box.title().split(":")[0]: box for box in dialog.findChildren(QGroupBox)}
        self.assertIn("Companion", groups["Remote"].toolTip())
        self.assertIn("AI_GUIDE.md", groups["AI"].toolTip())

    def test_the_input_tabs_overlay_button_is_labelled(self) -> None:
        from PySide6.QtWidgets import QLabel

        window, _engine, _tmp = make_window(self)
        window.open_editor("worship-blue")
        process(self.app, 3)
        details = window.editor_page.details
        choice = details._fields["input:overlay"]
        self.assertIn("Stays until cleared", choice.toolTip())
        labels = [label for label in details.findChildren(QLabel) if label.text() == "Overlay button"]
        self.assertEqual(len(labels), 1)
        self.assertIn("Send to Overlay button", labels[0].toolTip())
        long_hints = [label.text() for label in details.findChildren(QLabel)
                      if label.objectName() == "Hint" and len(label.text()) > 70 and label.isVisibleTo(details)]
        self.assertEqual(long_hints, [])  # (no paragraphs on the Input tab)


class ShowVariablesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def menu_action(self, window):
        picker = window.header.show_picker
        picker.fill_menu()
        return next(a for a in picker.picker_menu.actions() if a.text() == "Show variables…")

    def test_the_show_menu_opens_the_show_variables_with_nothing_open(self) -> None:
        window, _engine, _tmp = make_window(self)
        action = self.menu_action(window)
        self.assertTrue(action.isEnabled())
        action.trigger()
        process(self.app, 3)
        self.assertEqual(window._page, "editor")
        panel = window.editor_page.details.globals_panel
        self.assertIsNotNone(panel)
        self.assertTrue(panel.isVisibleTo(window.editor_page.details))

    def test_with_an_element_selected_it_goes_to_the_tab_with_nothing_selected(self) -> None:
        window, _engine, _tmp = make_window(self)
        window.open_editor("worship-blue")
        inp = window.document.show.inputs["worship-blue"]
        window.editor_page.select_element(str(inp.tracks[0][0]["id"]))
        process(self.app, 3)
        window.show_page("dashboard")
        self.menu_action(window).trigger()
        process(self.app, 3)
        self.assertEqual(window._page, "editor")
        self.assertEqual(window.editor_page.element_id, "")
        self.assertTrue(window.editor_page.details.globals_open())

    def test_the_dashboards_edit_does_the_same_and_the_padlock_stops_both(self) -> None:
        window, _engine, _tmp = make_window(self)
        window.show_page("dashboard")
        button = window.dashboard.variables.edit_button
        self.assertEqual(button.text(), "Edit")
        self.assertTrue(button.toolTip())
        button.click()
        process(self.app, 3)
        self.assertEqual(window._page, "editor")
        window.show_page("dashboard")
        window.set_locked(True)
        self.assertFalse(button.isEnabledTo(window))
        self.assertFalse(window.header.show_picker.isEnabled())
        window.show_variables()
        self.assertEqual(window._page, "dashboard")  # (locked: nothing happens)

    def test_with_no_show_loaded_its_greyed_out(self) -> None:
        window, _engine, _tmp = make_window(self)
        picker = window.header.show_picker
        picker.no_show = True
        self.assertFalse(self.menu_action(window).isEnabled())


def make_window(case: unittest.TestCase):
    from centralight.settings import Settings
    from centralight.ui.main_window import MainWindow
    from tests.fakes import FakeEngine
    from tests.test_overlay_m14 import held_show

    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    engine = FakeEngine(held_show("worship-blue"))
    window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                        notes_path=Path(tmp.name) / "fixture_map.csv")
    window.resize(1366, 768)
    window.show()
    process(case.app, 3)

    def close() -> None:
        window._shut_down = True
        window.editor_page.shutdown()
        window.document.close()
        window.close()
        window.deleteLater()
        process(case.app, 2)

    case.addCleanup(close)
    return window, engine, tmp


if __name__ == "__main__":
    unittest.main()
