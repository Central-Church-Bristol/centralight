"""M15 part 4 in the window: Save look / Save chase as input on the Console make an input that sets just those lights
(the save dialog says what stays on the Console); Load into Console, on that input's tile menu or in the Editor,
brings a look back held and a chase back onto the page, ready to Play."""

from __future__ import annotations

import unittest
from types import MappingProxyType
from unittest import mock

from centralight.commands import ConsoleRelease, ConsoleSet, SendToBase
from centralight.console import Chase, ChaseStep
from centralight.input_edit import lights_elements
from centralight.new_show import show_from_patch
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app

A, B = "slimpar-101", "slimpar-102"
GET_TEXT = "centralight.ui.main_window.QInputDialog.getText"


class LightsInputWindowTests(unittest.TestCase):
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
        self.menus: list = []
        self.frame()  # (the lights as they are, for Save look)

    def tearDown(self) -> None:
        for menu in self.menus:
            menu.deleteLater()
        self.window._shut_down = True
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

    def saved(self, name: str):
        return next(inp for inp in self.window.document.show.inputs.values() if inp.name == name)

    def menu_action(self, input_id: str, text: str):
        menu = self.window.dashboard.tile_menu(input_id)
        self.menus.append(menu)
        return next((action for action in menu.actions() if action.text() == text), None)

    def test_save_a_look_and_load_it_back(self) -> None:
        self.engine.submit(ConsoleSet({A: {"red": 255, "green": 0, "blue": 0, "dimmer": 128},
                                       B: {"red": 0, "green": 0, "blue": 255, "dimmer": 255}}))
        self.frame()
        self.page.set_selection([A, B])
        with mock.patch(GET_TEXT, return_value=("Red and blue", True)) as asked:
            self.page.save_look.click()
        question = asked.call_args.args[2]
        self.assertIn("colour and brightness only", question)  # (the save dialog says what stays on the Console)
        self.assertIn("amber, strobe", question)
        inp = self.saved("Red and blue")
        [element] = lights_elements(inp)
        self.assertEqual(set(element["steps"][0]), {A, B})
        self.assertEqual(tuple(element["steps"][0][A][:3]), (1.0, 0.0, 0.0))
        self.assertAlmostEqual(element["steps"][0][A][3], 128 / 255, places=2)
        self.engine.submit(ConsoleRelease(fade=0.0))
        self.engine.submit(SendToBase(inp.id, cut=True))
        self.frame()
        self.assertAlmostEqual(self.chans(A)[0], 128, delta=2)  # (as an input: its brightness is in the colour)
        self.assertEqual(self.chans(A)[1:3], (0, 0))
        self.assertEqual(self.chans("slimpar-103")[:3], (0, 0, 0))  # (a light it doesn't set: what's under, black)
        self.window.show_page("dashboard")
        self.menu_action(inp.id, "Load into Console").trigger()
        self.frame()
        held = self.engine._console.held
        self.assertEqual(set(held), {A, B})
        self.assertAlmostEqual(held[A]["dimmer"].value, 128, delta=1)
        self.assertEqual(held[A]["red"].value, 255.0)
        self.assertEqual(self.window._page, "console")
        self.assertEqual(sorted(self.page.map.selection()), [A, B])

    def test_save_a_chase_and_load_it_back(self) -> None:
        looks = [MappingProxyType({A: (1.0, 0.0, 0.0, 1.0)}), MappingProxyType({A: (0.0, 1.0, 0.0, 1.0)}),
                 MappingProxyType({A: (0.0, 0.0, 1.0, 0.5)})]
        self.page.set_chase(Chase((A,), tuple(ChaseStep(look) for look in looks), bpm=90.0, crossfade=0.5))
        with mock.patch(GET_TEXT, return_value=("Three colours", True)):
            self.page.save_chase.click()
        inp = self.saved("Three colours")
        [element] = lights_elements(inp)
        self.assertEqual(len(element["steps"]), 3)
        self.assertEqual((element["bpm"], element["crossfade"]), (90.0, 0.5))
        self.page.set_chase(Chase((B,), (ChaseStep(MappingProxyType({B: (1.0, 1.0, 1.0, 1.0)})),)))  # (another one)
        self.window.show_page("dashboard")
        self.menu_action(inp.id, "Load into Console").trigger()
        self.frame()
        self.assertEqual(len(self.page.step_cards), 3)  # (back on the page, ready to Play)
        self.assertEqual(self.page._chase.bpm, 90.0)
        self.assertIsNone(self.engine._console.chase)  # (not playing until Play)
        self.assertEqual(self.page.map.selection(), [A])

    def test_the_editor_loads_it_too(self) -> None:
        self.engine.submit(ConsoleSet({A: {"red": 0, "green": 255, "blue": 0}}))
        self.frame()
        self.page.set_selection([A])
        with mock.patch(GET_TEXT, return_value=("Green", True)):
            self.page.save_look.click()
        self.engine.submit(ConsoleRelease(fade=0.0))
        self.frame()
        self.window.editor_page.console_requested.emit(self.saved("Green").id, "E1")
        self.frame()
        self.assertEqual(self.engine._console.held[A]["green"].value, 255.0)

    def test_loading_shows_the_look(self) -> None:
        self.engine.submit(ConsoleSet({A: {"red": 255, "green": 128, "blue": 0, "dimmer": 77}}))
        self.frame()
        self.page.set_selection([A])
        with mock.patch(GET_TEXT, return_value=("Amber-ish", True)):
            self.page.save_look.click()
        self.engine.submit(ConsoleRelease(fade=0.0))
        self.page.set_selection([B])
        self.frame()
        self.window.load_into_console(self.saved("Amber-ish").id)
        self.assertEqual(self.page.map.selection(), [A])
        self.assertAlmostEqual(self.page.brightness.level(), 77 / 255, places=2)  # (the loaded look, not B's)
        self.assertEqual(self.page._colour, "#FF8000")

    def test_a_change_made_while_naming_it_stays(self) -> None:
        from centralight.input_edit import new_input

        self.page.set_selection([A])

        def meanwhile(*_args, **_kwargs):
            self.window.document.apply(new_input(self.window.document.show, "Made meanwhile")[0], "test")
            return ("Kept", True)

        with mock.patch(GET_TEXT, side_effect=meanwhile):
            self.page.save_look.click()
        names = [inp.name for inp in self.window.document.show.inputs.values()]
        self.assertIn("Made meanwhile", names)
        self.assertIn("Kept", names)

    def test_only_inputs_from_the_console_can_be_loaded(self) -> None:
        self.assertIsNone(self.menu_action("warm-white", "Load into Console"))
        self.window.load_into_console("warm-white")
        self.assertIn("Only an input saved from the Console", self.window.last_message.text())
        self.assertFalse(self.engine._console.active)

    def test_nothing_chosen_or_cancelled_saves_nothing(self) -> None:
        before = set(self.window.document.show.inputs)
        self.page.set_selection([])
        with mock.patch(GET_TEXT) as asked:
            self.page.save_look.click()
        asked.assert_not_called()
        self.assertIn("Choose the lights first", self.window.last_message.text())
        self.page.set_selection([A])
        with mock.patch(GET_TEXT, return_value=("Anything", False)):
            self.page.save_look.click()
        with mock.patch(GET_TEXT, return_value=("Anything", True)):
            self.page.save_chase.click()  # (no chase made yet)
        self.assertEqual(set(self.window.document.show.inputs), before)

    def test_a_name_in_use_gets_a_number(self) -> None:
        self.page.set_selection([A])
        for _ in range(2):
            with mock.patch(GET_TEXT, return_value=("Warm white", True)):
                self.page.save_look.click()
        names = sorted(inp.name for inp in self.window.document.show.inputs.values() if inp.name.startswith("Warm white"))
        self.assertEqual(names, ["Warm white", "Warm white 2", "Warm white 3"])

    def test_undo_takes_it_away(self) -> None:
        self.page.set_selection([A])
        with mock.patch(GET_TEXT, return_value=("Undo me", True)):
            self.page.save_look.click()
        self.assertIn("Undo me", [inp.name for inp in self.window.document.show.inputs.values()])
        self.window.document.undo()
        self.assertNotIn("Undo me", [inp.name for inp in self.window.document.show.inputs.values()])


if __name__ == "__main__":
    unittest.main()
