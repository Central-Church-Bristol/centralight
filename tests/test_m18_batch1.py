"""M18, batch 1 (6 Nov): number fields show their arrows, the Countdown timer setting in its own group, and the
automation lists keep their place when an automation is clicked (a rebuild jumped them to the top)."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from centralight.global_automations import GLOBAL_DIR
from centralight.model import Show
from centralight.new_show import create_show
from tests.qt_support import process, qt_app
from tests.test_automations import automation
from tests.test_shows import WindowCase

UI = Path(__file__).resolve().parents[1] / "centralight" / "ui"


def bright_pixels(widget, right: int, skip: int = 3) -> int:
    """How many light pixels (an arrow, or text) there are in the widget's rightmost `right` pixels, inside its frame
    (skip: the rounded focus border's corners)."""
    image = widget.grab().toImage()
    ratio = image.devicePixelRatio()
    count = 0
    for x in range(int((widget.width() - right) * ratio), int((widget.width() - skip) * ratio)):
        for y in range(int(skip * ratio), int((widget.height() - skip) * ratio)):
            if image.pixelColor(x, y).lightness() > 140:
                count += 1
    return count


class SpinArrowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def field(self, widget):
        widget.resize(220, 34)
        widget.show()
        process(self.app)
        self.addCleanup(widget.deleteLater)
        return widget

    def test_number_fields_show_their_arrows(self) -> None:
        from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox, QTimeEdit

        spin = QSpinBox()
        spin.setRange(0, 60)
        spin.setValue(15)
        double = QDoubleSpinBox()
        double.setValue(0.3)
        for widget in (spin, double, QTimeEdit()):
            self.assertGreater(bright_pixels(self.field(widget), 22), 6, type(widget).__name__)  # (the arrows)

    def test_compact_fields_stay_without_buttons(self) -> None:
        from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox

        for kind in (QSpinBox, QDoubleSpinBox):
            spin = kind()
            spin.setObjectName("NumberSpin")  # (the Editor's, the automations' and the variables' small fields)
            spin.setValue(1)
            self.assertEqual(bright_pixels(self.field(spin), 22), 0, kind.__name__)

    def test_the_arrow_pictures_are_there(self) -> None:
        from centralight.ui.theme import stylesheet

        sheet = stylesheet()
        for name in ("spin_up", "spin_down", "spin_up_off", "spin_down_off"):
            self.assertTrue((UI / f"{name}.svg").is_file(), name)
            self.assertIn(f"{name}.svg", sheet)


class CountdownSettingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def test_countdown_timer_is_with_the_automations(self) -> None:
        from PySide6.QtWidgets import QLabel

        from centralight.settings import Settings
        from centralight.ui.settings_dialog import SettingsDialog

        settings = Settings(countdown=15)
        dialog = SettingsDialog(settings, "")
        self.addCleanup(dialog.deleteLater)
        group = dialog.countdown.parentWidget()
        self.assertEqual(group.title(), "Shows and automations")
        self.assertIn("Countdown timer", [label.text() for label in group.findChildren(QLabel)])
        self.assertIs(dialog.default_show.parentWidget(), group)  # (it was under Starting and quitting)
        self.assertEqual(dialog.black_fade.parentWidget().title(), "Cut / Fade to black")  # (Fade to black stays)
        self.assertEqual(dialog.release_fade.parentWidget().title(), "Console")
        self.assertEqual(dialog.clshow_files.parentWidget().title(), "Files and diagnostics")
        self.assertEqual(dialog.start_with_windows.parentWidget().title(), "Starting and quitting")
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertNotIn("Countdown first", texts)
        self.assertIn("When you quit:", texts)
        dialog.countdown.setValue(20)
        dialog.apply_to(settings)
        self.assertEqual(settings.countdown, 20)  # (saved as before)


class AutomationListPlaceTests(WindowCase):
    def make_window(self, count: int = 30):
        show = create_show(self.root / "Morning", "Morning")
        made = [automation(f"auto-{i:02d}", [{"type": "time", "from": "09:00", "to": "10:00"}],
                           [{"do": "load_show", "show": "morning"}]) for i in range(count)]
        globals_ = Show(name="Global automations").with_automations({a.id: a for a in made}, tuple(a.id for a in made))
        globals_ = replace(globals_, folder=str(self.root / GLOBAL_DIR))
        window, engine = self.make(show, global_automations=globals_)
        window.show_page("automation")
        process(self.app)
        page = window.automation_page
        view = page.global_list
        self.assertGreater(view.verticalScrollBar().maximum(), 0)  # (it scrolls)
        return window, page, view

    def test_a_click_in_a_scrolled_list_picks_that_automation(self) -> None:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        window, page, view = self.make_window()
        bar = view.verticalScrollBar()
        bar.setValue(bar.maximum())
        process(self.app)
        scrolled = bar.value()
        target = view.item(view.count() - 2)
        where = view.visualItemRect(target).center() + QPoint(40, 0)  # (on the text, not the tick box)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, where)
        process(self.app)
        self.assertEqual(bar.value(), scrolled)  # (it used to jump to the top here, between press and release)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, where)
        process(self.app)
        self.assertEqual(page.selected, f"auto-{view.count() - 2:02d}")
        self.assertTrue(page.global_selected())
        self.assertEqual(bar.value(), scrolled)

    def test_a_refresh_or_a_new_automation_keeps_the_place(self) -> None:
        window, page, view = self.make_window()
        bar = view.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        process(self.app)
        middle = bar.value()
        page._list_keys = {key: () for key in page._list_keys}
        page._fill_lists()  # (as a status or "last ran" refresh does)
        self.assertEqual(bar.value(), middle)
        page.new_automation("global")  # (a rebuild: one more row)
        process(self.app)
        self.assertEqual(view.count(), 31)
        self.assertGreater(bar.value(), 0)  # (not back at the top)


if __name__ == "__main__":
    unittest.main()
