"""The Show variables window (8 Nov). Show ▾ → Show variables… and the Dashboard's Edit both open it: on top of the main
window and not modal, with no input open, the same controls as the Editor's Show variables tab, and the Dashboard
following what's changed in it. (The first version opened the Editor's Show variables tab: with no input open, the
real window showed the Editor's "Nothing to edit yet" page instead.)"""

from __future__ import annotations

import unittest
from dataclasses import replace

from PySide6.QtCore import QRect
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QPushButton

from centralight.clshow import KEEP_ON_THIS_PC
from centralight.model import NO_SHOW, GlobalVar
from centralight.ui.variables_window import ShowVariablesWindow
from tests.qt_support import process, qt_app
from tests.test_m18_ui_text import make_window


def names(show) -> list[str]:
    return [g.name for g in show.globals]


class ShowVariablesWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def menu_action(self, window):
        picker = window.header.show_picker
        picker.fill_menu()
        return next(a for a in picker.picker_menu.actions() if a.text() == "Show variables…")

    def open_windows(self) -> list[ShowVariablesWindow]:
        return [w for w in QApplication.topLevelWidgets() if isinstance(w, ShowVariablesWindow) and w.isVisible()]

    def test_the_show_menu_opens_it_with_no_input_open(self) -> None:
        window, _engine, _tmp = make_window(self)
        window.show_page("dashboard")
        self.assertIsNone(window.editor_page.details.current_input())  # (no input open)
        action = self.menu_action(window)
        self.assertTrue(action.isEnabled())
        action.trigger()
        process(self.app, 3)
        variables = window.variables_window
        self.assertIsNotNone(variables)
        self.assertTrue(variables.isVisible())
        self.assertTrue(variables.panel.isVisible())  # (really on screen: not inside a page that's hidden)
        self.assertTrue(variables.panel.add_button.isEnabled())
        self.assertFalse(variables.isModal())
        self.assertIs(variables.parent(), window)  # (on top of the main window)
        self.assertEqual(window._page, "dashboard")  # (the Dashboard stays in sight)
        self.assertEqual(variables.windowTitle(), f"Show variables — {window.document.show.name}")

    def test_the_dashboards_edit_opens_the_same_one_and_brings_it_forward(self) -> None:
        window, _engine, _tmp = make_window(self)
        window.show_page("dashboard")
        window.dashboard.variables.edit_button.click()
        process(self.app, 3)
        first = window.variables_window
        self.assertTrue(first.isVisible())
        self.assertEqual(window._page, "dashboard")
        first.close()
        process(self.app, 2)
        self.assertFalse(first.isVisible())
        self.menu_action(window).trigger()
        process(self.app, 2)
        self.assertIs(window.variables_window, first)
        window.dashboard.variables.edit_button.click()  # (open already: it comes forward, no second one)
        process(self.app, 2)
        self.assertEqual(self.open_windows(), [first])

    def test_a_change_here_and_the_dashboard_follows(self) -> None:
        window, engine, _tmp = make_window(self)
        window.show_page("dashboard")
        variables = window.show_variables()
        process(self.app, 2)
        name = variables.panel.add("number")
        process(self.app, 2)
        self.assertIn(name, names(window.document.show))
        self.assertIn(name, names(engine.show))  # (live, as in the Editor)
        card = variables.panel.cards[name]
        self.assertNotIn(name, window.dashboard.variables.controls)
        card.dashboard.click()  # On the dashboard
        process(self.app, 2)
        self.assertIn(name, window.dashboard.variables.controls)
        self.assertFalse(window.dashboard.variables.hint.isVisible())
        variables.panel.cards[name].high.setValue(4.0)  # (its range: the Dashboard's control follows)
        process(self.app, 2)
        self.assertEqual(window.dashboard.variables.controls[name]._max, 4.0)
        card = variables.panel.cards[name]
        card.default.edited.emit(0.75, True)  # (its starting value)
        card.on_load.edited.emit("reset", True)  # (Go back to its starting value)
        var = next(g for g in window.document.show.globals if g.name == name)
        self.assertEqual((var.default, var.on_load), (0.75, "reset"))
        copy = next(b for b in card.findChildren(QPushButton) if b.text() == "Copy URL")
        copy.click()
        self.assertIn(f"/api/var/{name}?value=", QApplication.clipboard().text())
        variables.panel.remove(name)
        process(self.app, 2)
        self.assertNotIn(name, window.dashboard.variables.controls)
        self.assertNotIn(name, variables.panel.cards)

    def test_ctrl_z_here_undoes_in_the_shows_history(self) -> None:
        window, _engine, _tmp = make_window(self)
        variables = window.show_variables()
        process(self.app, 2)
        name = variables.panel.add("switch")
        process(self.app, 2)
        shortcuts = {s.key().toString(): s for s in variables.findChildren(QShortcut)}
        undo = shortcuts[QKeySequence(QKeySequence.StandardKey.Undo).toString()]
        redo = shortcuts[QKeySequence(QKeySequence.StandardKey.Redo).toString()]
        undo.activated.emit()
        process(self.app, 2)
        self.assertNotIn(name, names(window.document.show))
        self.assertNotIn(name, variables.panel.cards)
        redo.activated.emit()
        process(self.app, 2)
        self.assertIn(name, names(window.document.show))

    def test_it_remembers_its_size_and_place(self) -> None:
        window, _engine, _tmp = make_window(self)
        variables = window.show_variables()
        process(self.app, 2)
        variables.setGeometry(QRect(120, 90, 480, 500))
        process(self.app, 2)
        variables.close()
        process(self.app, 2)
        self.assertEqual(window.settings.variables_window, [120, 90, 480, 500])
        self.assertIn("variables_window", KEEP_ON_THIS_PC)  # (importing another PC's settings never moves it)
        again, _engine2, _tmp2 = make_window(self, settings=window.settings)
        reopened = again.show_variables()
        process(self.app, 2)
        self.assertEqual(reopened.geometry_list(), [120, 90, 480, 500])
        reopened.setGeometry(QRect(100, 80, 460, 520))
        again.hide()  # (into the tray: it goes too, remembering where it was)
        process(self.app, 2)
        self.assertFalse(reopened.isVisible())
        self.assertEqual(again.settings.variables_window, [100, 80, 460, 520])
        again.show()
        process(self.app, 2)

    def test_another_show_shows_its_variables_and_no_show_greys_it_out(self) -> None:
        window, _engine, _tmp = make_window(self)
        variables = window.show_variables()
        process(self.app, 2)
        evening = replace(window.document.show, name="Evening", globals=(GlobalVar("mood", "number", 0.5, 0.0, 1.0),))
        window.document.load(evening)
        process(self.app, 2)
        self.assertEqual(set(variables.panel.cards), {"mood"})
        self.assertEqual(variables.windowTitle(), "Show variables — Evening")
        window.document.load(NO_SHOW)
        process(self.app, 2)
        self.assertTrue(variables.isVisible())
        self.assertTrue(variables.none.isVisible())  # No show loaded.
        self.assertFalse(variables.scroll.isVisible())
        self.assertEqual(variables.windowTitle(), "Show variables")
        window.document.load(evening)
        process(self.app, 2)
        self.assertFalse(variables.none.isVisible())
        self.assertTrue(variables.panel.isVisible())

    def test_with_no_show_loaded_it_cant_be_opened(self) -> None:
        window, _engine, _tmp = make_window(self)
        window.document.load(NO_SHOW)
        process(self.app, 2)
        self.assertIsNone(window.show_variables())
        self.assertIsNone(window.variables_window)
        picker = window.header.show_picker
        picker.no_show = True
        self.assertFalse(self.menu_action(window).isEnabled())

    def test_the_padlock_stops_both_and_greys_it(self) -> None:
        window, _engine, _tmp = make_window(self)
        window.show_page("dashboard")
        variables = window.show_variables()
        process(self.app, 2)
        window.set_locked(True)
        process(self.app, 2)
        self.assertFalse(window.dashboard.variables.edit_button.isEnabledTo(window))
        self.assertFalse(window.header.show_picker.isEnabled())
        self.assertFalse(variables.panel.isEnabled())  # (open already: nothing can be changed in it)
        before = window.document.show.globals
        variables.undo()
        self.assertEqual(window.document.show.globals, before)
        variables.close()
        process(self.app, 2)
        self.assertIsNone(window.show_variables())  # (locked: it doesn't open)
        self.assertFalse(variables.isVisible())
        window.set_locked(False)
        self.assertTrue(window.show_variables().panel.isEnabled())


if __name__ == "__main__":
    unittest.main()
