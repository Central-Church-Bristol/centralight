"""The Automation tab, driven like a person would: make one, edit it, run it, pause them all,
undo in its own history, and the header's Automations chip."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from centralight.layouts import save_layout_as
from centralight.new_show import show_from_patch
from centralight.settings import Settings
from centralight.ui.document import SCOPE_AUTOMATIONS
from centralight.ui.main_window import MainWindow
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app


class AutomationPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        show, self.christmas = save_layout_as(with_inputs(show_from_patch()), "Christmas")
        self.engine = FakeEngine(show)
        self.window = MainWindow(Settings(), self.engine.show, self.engine, self.engine.fake_output, save_files=False,
                                 notes_path=Path(self._tmp.name) / "fixture_map.csv")
        self.window.resize(1366, 768)
        self.window.show()
        process(self.app, 4)
        QTest.mouseClick(self.window.header.tabs["automation"], Qt.MouseButton.LeftButton)
        process(self.app, 2)
        self.page = self.window.automation_page

    def tearDown(self) -> None:
        self.window._shut_down = True
        self.window.editor_page.shutdown()
        self.window.document.close()
        self.window.close()
        self.window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def refresh(self) -> None:
        self.engine.tick()
        self.window.bridge.poll()
        process(self.app)

    def automation(self, automation_id: str):
        return self.window.document.show.automations[automation_id]

    def test_make_and_edit_one(self) -> None:
        self.assertEqual(self.window.document.scope, SCOPE_AUTOMATIONS)
        new_id = self.page.new_automation()
        process(self.app)
        self.assertEqual(self.page.selected, new_id)
        self.assertEqual([c["type"] for c in self.automation(new_id).conditions], ["days", "time"])
        self.page.name_edit.setText("Carols")
        self.page.name_edit.editingFinished.emit()
        self.assertEqual(self.automation(new_id).name, "Carols")
        self.page.add_rule("actions", "base")
        process(self.app)
        row = self.page.rows["actions"][0]
        self.assertEqual(self.automation(new_id).actions[0]["do"], "base")
        row.changed.emit({"do": "base", "input": "christmas", "transition": "cut"})
        self.assertEqual(self.automation(new_id).actions[0]["input"], "christmas")
        self.page.add_rule("conditions", "variable")
        self.page.remove_rule("conditions", 0)  # the days condition goes
        self.assertEqual([c["type"] for c in self.automation(new_id).conditions], ["time", "variable"])
        self.assertIn("Carols", self.page.list.currentItem().text())
        # every step is in the automations' own history
        stack = self.window.document.stack(SCOPE_AUTOMATIONS)
        self.assertEqual(stack.count(), 6)
        self.window.undo()
        self.assertEqual([c["type"] for c in self.automation(new_id).conditions], ["days", "time", "variable"])

    def test_tick_in_the_list_turns_it_off(self) -> None:
        item = self.page.list.item(0)
        automation_id = item.data(Qt.ItemDataRole.UserRole)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(self.automation(automation_id).enabled)

    def test_run_now_and_a_layout_action(self) -> None:
        self.assertEqual(self.window.document.show.active_layout, self.christmas)
        self.page.new_automation()
        self.page.change("actions", actions=[{"do": "base", "input": "warm-white", "transition": "cut"},
                                             {"do": "layout", "layout": "main"}])
        process(self.app)
        QTest.mouseClick(next(b for b in self.page.editor.findChildren(type(self.page.new_button)) if b.text() == "Run now"),
                         Qt.MouseButton.LeftButton)
        self.refresh()
        self.assertEqual(self.engine.frames.get().base_id, "warm-white")
        self.assertEqual(self.window.document.show.active_layout, "main")  # the window did the layout switch
        self.assertIn("run by hand", self.page.history.item(0).text())

    def test_pause_from_the_page_and_the_chip(self) -> None:
        QTest.mouseClick(self.page.pause_switch, Qt.MouseButton.LeftButton)
        self.refresh()
        self.assertTrue(self.engine.frames.get().automations.paused)
        self.assertEqual(self.window.header.chips["automations"].text(), "Automations paused")
        self.window.header.chips["automations"].clicked.emit()  # click the chip: resume
        self.refresh()
        self.assertFalse(self.engine.frames.get().automations.paused)
        self.assertFalse(self.page.pause_switch.isChecked())
        self.assertEqual(self.window.header.chips["automations"].text(), "Automations on")

    def test_delete_asks_and_undo_brings_it_back(self) -> None:
        automation_id = self.page.selected
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.page.delete()
        self.assertNotIn(automation_id, self.window.document.show.automations)
        self.window.undo()
        self.assertIn(automation_id, self.window.document.show.automations)

    def test_loop_guard_badge_and_reset(self) -> None:
        from centralight.automations import AutomationState

        self.engine._runner.states["sunday-welcome"] = AutomationState(tripped=True)  # as if the loop guard had stepped in
        self.engine.settle(0.2)
        self.window.bridge.poll()
        process(self.app)
        self.page.list.setCurrentRow(0)
        process(self.app)
        self.assertTrue(self.page.reset_button.isVisibleTo(self.page))
        self.assertIn("loop guard", self.page.list.item(0).text())
        self.assertEqual(self.window.header.chips["automations"].text(), "Automations: check")
        QTest.mouseClick(self.page.reset_button, Qt.MouseButton.LeftButton)
        self.refresh()
        self.assertFalse(self.engine.frames.get().automations.statuses["sunday-welcome"].tripped)

    def test_locked_page(self) -> None:
        self.window.set_locked(True)
        self.assertFalse(self.page.isEnabled())


if __name__ == "__main__":
    unittest.main()
