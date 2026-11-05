"""M15: the Console page's own behaviour: choosing lights on the map (click, Ctrl / Shift-click, a box), All, None,
Invert, Odd, Even, the patch's groups and saved ones, and the order. (What it does to the lights is in
test_console_m15.py: the engine's side.)"""

from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from centralight.new_show import show_from_patch
from centralight.show_edit import save_group
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app

TOP = ["slimpar-101", "slimpar-102", "slimpar-103", "slimpar-104"]


class ConsolePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        from centralight.ui.console_page import ConsolePage

        self.show = with_inputs(show_from_patch())
        self.page = ConsolePage(self.show)
        self.page.resize(1400, 820)
        self.page.show()
        process(self.app, 3)
        self.emitted: list[list[str]] = []
        self.page.selection_changed.connect(lambda ids: self.emitted.append(list(ids)))

    def tearDown(self) -> None:
        self.page.close()
        self.page.deleteLater()
        process(self.app)

    def dot(self, light: str) -> QPoint:
        view = self.page.map
        fx = next(f for f in self.show.colour_fixtures() if f.id == light)
        centre, _radius = view.dot_geometry(fx, view.canvas_rect())
        return centre.toPoint()

    def click(self, light: str, modifier=Qt.KeyboardModifier.NoModifier) -> None:
        QTest.mouseClick(self.page.map, Qt.MouseButton.LeftButton, modifier, self.dot(light))
        process(self.app)

    def test_click_ctrl_click_and_a_box(self) -> None:
        map_ = self.page.map
        self.click("slimpar-101")
        self.assertEqual(map_.selection(), ["slimpar-101"])
        self.click("slimpar-103", Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(map_.selection(), ["slimpar-101", "slimpar-103"])
        self.click("slimpar-101", Qt.KeyboardModifier.ShiftModifier)  # (again: taken away)
        self.assertEqual(map_.selection(), ["slimpar-103"])
        self.click("slimpar-104")  # (a plain click: just that one)
        self.assertEqual(map_.selection(), ["slimpar-104"])
        self.assertEqual(self.emitted[-1], ["slimpar-104"])
        start = self.dot("slimpar-101") + QPoint(-40, -40)
        end = self.dot("slimpar-104") + QPoint(40, 40)
        QTest.mousePress(map_, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        for step in range(1, 11):
            QTest.mouseMove(map_, start + (end - start) * step / 10)
        QTest.mouseRelease(map_, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        process(self.app)
        self.assertEqual(map_.selection(), TOP)  # (a box: left to right)
        empty = QPoint(map_.canvas_rect().center().toPoint().x(), round(map_.canvas_rect().bottom() - 4))
        QTest.mouseClick(map_, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, empty)
        process(self.app)
        self.assertEqual(map_.selection(), [])  # (a click on nothing: nothing chosen)

    def test_the_buttons_and_groups(self) -> None:
        page = self.page
        everything = page.map_order()
        self.assertEqual(len(everything), 8)
        page.choose_buttons["all"].click()
        self.assertEqual(page.map.selection(), everything)
        page.choose_buttons["invert"].click()
        self.assertEqual(page.map.selection(), [])
        page.choose_buttons["odd"].click()
        self.assertEqual(page.map.selection(), everything[0::2])
        page.choose_buttons["none"].click()
        page.choose_buttons["even"].click()
        self.assertEqual(page.map.selection(), everything[1::2])
        self.assertEqual({page.groups()[k][0] for k in page.group_buttons}, {"Top Wash", "Side Fill", "Pillars"})
        page.group_buttons["patch:Top Wash"].click()
        self.assertEqual(page.map.selection(), TOP)
        page.set_show(save_group(self.show, "Front pair", ["slimpar-102", "slimpar-101"]))
        process(self.app)
        self.assertIn("saved:Front pair", page.group_buttons)
        page.group_buttons["saved:Front pair"].click()
        self.assertEqual(page.map.selection(), ["slimpar-101", "slimpar-102"])  # (in map order)

    def test_the_order(self) -> None:
        page = self.page
        self.click("slimpar-104")
        self.click("slimpar-101", Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(page.map.selection(), ["slimpar-104", "slimpar-101"])  # (as clicked)
        page.order.setCurrentIndex(page.order.findData("left"))
        self.assertEqual(page.map.selection(), ["slimpar-101", "slimpar-104"])
        page.order.setCurrentIndex(page.order.findData("right"))
        self.assertEqual(page.map.selection(), ["slimpar-104", "slimpar-101"])
        self.click("slimpar-102", Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(page.map.selection(), ["slimpar-104", "slimpar-102", "slimpar-101"])  # (kept in that order)

    def test_the_chase_says_which_lights_it_is_on(self) -> None:
        """(The user's addition of 5 Nov: a line above the step cards, a group's name for a whole group.)"""
        from types import MappingProxyType

        from centralight.console import Chase, ChaseStep, ChaseView, ConsoleView
        from centralight.ui.console_page import NO_CHASE_LIGHTS

        page = self.page
        pillars = ["slimpar-106", "slimpar-107"]
        self.assertEqual(page.on_text([]), NO_CHASE_LIGHTS)
        self.assertEqual(page.on_text(pillars), "On: Pillars")
        self.assertEqual(page.on_text(["slimpar-106"]), "On: SlimPAR 106")
        self.assertEqual(page.on_text(["slimpar-101", *pillars]), "On: SlimPAR 101, Pillars")
        self.assertEqual(page.on_text([*TOP, *pillars]), "On: Top Wash, Pillars")
        self.assertEqual(page.on_text([*TOP, "slimpar-105", *pillars, "slimpar-108"]), "On: every light (8)")
        page.set_show(save_group(self.show, "Front", ["slimpar-101", "slimpar-102"]))
        self.assertEqual(page.on_text(["slimpar-102", "slimpar-101", "slimpar-105"]), "On: Front, SlimPAR 105")
        self.assertEqual(page.chase_lights.text(), NO_CHASE_LIGHTS)  # (no chase yet)
        step = ChaseStep(MappingProxyType({light: (1.0, 0.0, 0.0, 1.0) for light in pillars}))
        chase = Chase(tuple(pillars), (step,))
        page.set_chase(chase)
        self.assertEqual(page.chase_lights.text(), "On: Pillars")
        self.assertIn("SlimPAR 106, SlimPAR 107", page.chase_lights.toolTip())
        page.set_view(ConsoleView(chase=ChaseView(chase, playing=True, lights=("slimpar-107",))))  # (106 released)
        self.assertEqual(page.chase_lights.text(), "On: SlimPAR 107")

    def test_the_padlock(self) -> None:
        self.page.set_locked(True)
        self.click("slimpar-101")
        self.assertEqual(self.page.map.selection(), [])
        self.page.set_locked(False)
        self.click("slimpar-101")
        self.assertEqual(self.page.map.selection(), ["slimpar-101"])


class WiredTests(unittest.TestCase):
    """The page in the window, wired to a (fake) engine: what you do reaches the lights, the header chip, the padlock,
    and the show's groups and saved colours."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine

        self.show = with_inputs(show_from_patch())
        self.engine = FakeEngine(self.show)
        self.window = MainWindow(Settings(), self.show, self.engine, self.engine.fake_output, save_files=False)
        if not self.window._console_wired:
            self.window._wire_console()
        self.window.resize(1366, 768)
        self.window.show()
        self.window.show_page("console")
        process(self.app, 3)
        self.page = self.window.console_page

    def tearDown(self) -> None:
        self.window._shut_down = True  # (as the other window tests: closing it then asks nothing)
        self.window.editor_page.shutdown()
        self.window.document.close()
        self.window.global_document.close()
        self.window.close()
        self.window.deleteLater()
        process(self.app)

    def frame(self) -> None:
        self.engine.settle(0.1)
        self.window.bridge.poll()
        process(self.app, 2)

    def chans(self, light: str) -> tuple[int, ...]:
        fx = next(f for f in self.show.fixtures if f.id == light)
        frame = self.engine.fake_output.frames[-1]
        return tuple(frame[fx.address - 1:fx.address - 1 + fx.channels])

    def test_a_colour_and_brightness_reach_the_lights_and_the_chip_lets_go(self) -> None:
        self.page.group_buttons["patch:Top Wash"].click()
        self.page.use_colour("#FF0000")
        self.page._brightness_moved(0.5)
        self.frame()
        for light in TOP:
            self.assertEqual(self.chans(light)[:3], (255, 0, 0))
            self.assertEqual(self.chans(light)[7], 128)
        self.assertEqual(self.chans("slimpar-105")[:3], self.chans("slimpar-108")[:3])  # (the rest follow the input)
        chip = self.window.header.chips["console"]
        self.assertTrue(chip.isVisibleTo(self.window.header))
        self.assertIn("Console: 4 lights", chip.text() if hasattr(chip, "text") else chip.toolTip() + "Console: 4 lights")
        self.assertEqual(self.page.map._held, frozenset(TOP))
        chip.clicked.emit()
        self.engine.settle(2.2)
        self.frame()
        self.assertFalse(self.engine._console.active)
        self.assertFalse(chip.isVisibleTo(self.window.header))

    def test_cancel_in_the_picker_puts_back(self) -> None:
        self.page.group_buttons["patch:Pillars"].click()
        pillars = self.page.map.selection()
        self.page.use_colour("#00FF00")
        self.frame()
        before = {light: self.chans(light) for light in TOP}
        self.window._console_restore({pillars[0]: {"red": 0, "green": 255, "blue": 0}}, [pillars[1]])
        self.frame()
        self.assertEqual(self.chans(pillars[0])[:3], (0, 255, 0))  # (held before: held again)
        self.assertNotEqual(self.chans(pillars[1])[:3], (0, 255, 0))  # (let go: the inputs' again)
        self.assertEqual({light: self.chans(light) for light in TOP}, before)

    def test_making_and_playing_a_chase(self) -> None:
        page = self.page
        page.group_buttons["patch:Pillars"].click()
        pillars = page.map.selection()
        page.use_colour("#FF0000")
        self.frame()
        page.add_chase_step()
        page.use_colour("#0000FF")
        self.frame()
        page.add_chase_step()
        self.assertEqual(len(page.chase().steps), 2)
        self.assertEqual(page.chase().lights, tuple(pillars))
        self.assertAlmostEqual(page.chase().steps[0].looks[pillars[0]][0], 1.0, places=2)
        page.choose_step(0)
        page.copy_chosen_step()
        self.assertEqual(len(page.chase().steps), 3)
        page.move_step(1)  # (the copy, chosen now, moves later)
        page.remove_chosen_step()
        self.assertEqual(len(page.chase().steps), 2)
        page.speed_unit.setCurrentIndex(page.speed_unit.findData("seconds"))
        page.speed.setValue(1.0)
        self.assertEqual(page.chase().seconds, 1.0)
        page.play.click()
        self.frame()
        self.assertIsNotNone(self.engine._console.chase)
        self.assertEqual(self.chans(pillars[0])[:3], (255, 0, 0))
        page.next_step.click()
        self.frame()
        self.assertEqual(self.chans(pillars[0])[:3], (0, 0, 255))
        page.pause.setChecked(True)
        self.engine.settle(3.0)
        self.frame()
        self.assertEqual(self.chans(pillars[0])[:3], (0, 0, 255))  # (paused: it stays)
        page.crossfade.set_level(0.5)
        page._timing_changed()
        self.frame()
        self.assertEqual(self.engine._console.chase.chase.crossfade, 0.5)  # (a change while it plays reaches it)
        self.assertTrue(page.step_cards[1].current)
        from unittest import mock

        with mock.patch("time.monotonic", side_effect=[100.0, 100.5, 101.0]):
            for _ in range(3):
                page.tap.click()
        self.assertEqual((page.speed_unit.currentData(), page.speed.value()), ("bpm", 120.0))  # (Tap: 120 BPM)
        self.frame()
        self.assertAlmostEqual(self.engine._console.chase.chase.step_seconds(), 0.5)  # (and the playing chase took it)
        page.build_chase("running")
        self.assertEqual(len(page.chase().steps), len(pillars))
        page.release_all_button.click()
        self.engine.settle(2.2)
        self.frame()
        self.assertIsNone(self.engine._console.chase)

    def test_the_padlock_and_the_show_groups_and_colours(self) -> None:
        from unittest import mock

        self.window.set_locked(True)
        self.assertFalse(self.page.isEnabled())
        self.window._console_set(["slimpar-101"], {"red": 255}, 0.0)
        self.frame()
        self.assertFalse(self.engine._console.active)
        self.window.set_locked(False)
        self.page.group_buttons["patch:Top Wash"].click()
        with mock.patch("centralight.ui.main_window.QInputDialog.getText", return_value=("Front four", True)):
            self.page.save_group_button.click()
        self.assertEqual([g.name for g in self.window.document.show.groups], ["Front four"])
        process(self.app, 2)
        self.assertIn("saved:Front four", self.page.group_buttons)
        self.page.add_saved.click()
        self.assertEqual(len(self.window.document.show.saved_colours), 1)
        self.assertEqual(len(self.page.saved_buttons), 1)
        self.window.document.undo()
        process(self.app, 2)
        self.assertEqual(self.window.document.show.saved_colours, ())


if __name__ == "__main__":
    unittest.main()
