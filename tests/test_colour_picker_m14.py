"""M14 item 11: the colour picker updates live. With Preview ticked (the default, until it's unticked; a PC restart
ticks it again), each change applies at once (at most about 20 a second): the viewports follow, and the lights if the
input is on them. The whole pick is one undo step; Cancel puts the colour back with no step left behind."""

from __future__ import annotations

import unittest
from unittest import mock

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog

from centralight.ui import colour_picker
from centralight.ui.colour_picker import LiveColourDialog, PreviewChoice
from tests.qt_support import process, qt_app


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def test_at_most_one_change_each_50_ms(self) -> None:
        dialog = LiveColourDialog("#FF0000", preview=True)
        self.addCleanup(dialog.deleteLater)
        seen = []
        dialog.changed.connect(seen.append)
        for colour in ("#00FF00", "#0000FF", "#123456"):
            dialog.picker.setCurrentColor(QColor(colour))
        self.assertEqual(seen, [])  # (waiting for its 50 ms)
        dialog.flush()  # (the timer ending)
        self.assertEqual(seen, ["#123456"])  # (only the newest)
        self.assertTrue(dialog.tried())

    def test_preview_off_applies_nothing_until_ok(self) -> None:
        dialog = LiveColourDialog("#FF0000", preview=False)
        self.addCleanup(dialog.deleteLater)
        seen = []
        dialog.changed.connect(seen.append)
        dialog.picker.setCurrentColor(QColor("#00FF00"))
        dialog.flush()
        self.assertEqual(seen, [])
        self.assertEqual(dialog.colour(), "#00FF00")
        self.assertFalse(dialog.tried())

    def test_unticking_preview_puts_it_back(self) -> None:
        dialog = LiveColourDialog("#FF0000", preview=True)
        self.addCleanup(dialog.deleteLater)
        seen = []
        dialog.changed.connect(seen.append)
        dialog.picker.setCurrentColor(QColor("#00FF00"))
        dialog.flush()
        dialog.preview.setChecked(False)
        dialog.preview.setChecked(True)
        self.assertEqual(seen, ["#00FF00", "#FF0000", "#00FF00"])

    def test_enter_and_esc_in_the_picker_are_ok_and_cancel(self) -> None:
        """(Qt's own picker closed just itself on Esc, leaving the window open with only the tick box and buttons.)"""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QSpinBox

        for key, result in ((Qt.Key.Key_Escape, QDialog.DialogCode.Rejected), (Qt.Key.Key_Return, QDialog.DialogCode.Accepted)):
            dialog = LiveColourDialog("#FF0000", preview=True)
            self.addCleanup(dialog.deleteLater)
            dialog.show()
            process(self.app, 2)
            spin = next(w for w in dialog.picker.findChildren(QSpinBox) if w.isVisible())  # (Hue, say)
            spin.setFocus()
            process(self.app)
            QTest.keyClick(QApplication.focusWidget() or spin, key)
            process(self.app, 2)
            self.assertFalse(dialog.isVisible())
            self.assertEqual(dialog.result(), result)
            self.assertFalse(dialog.picker.isHidden())

    def test_the_timer_sends_it_by_itself(self) -> None:
        import time

        dialog = LiveColourDialog("#FF0000", preview=True)
        self.addCleanup(dialog.deleteLater)
        seen = []
        dialog.changed.connect(seen.append)
        dialog.picker.setCurrentColor(QColor("#00FF00"))
        deadline = time.monotonic() + 1.0
        while not seen and time.monotonic() < deadline:
            process(self.app)
        self.assertEqual(seen, ["#00FF00"])


class PreviewChoiceTests(unittest.TestCase):
    def test_ticked_until_unticked_and_again_after_a_restart(self) -> None:
        stored = {}
        choice = PreviewChoice()
        choice.load = lambda: stored.get("data")
        choice.store = lambda data: stored.__setitem__("data", data)
        self.assertTrue(choice.wanted())  # (never chosen: ticked)
        choice.remember(False)
        self.assertFalse(choice.wanted())  # (the same PC session)
        with mock.patch.object(colour_picker, "pc_started_at", lambda: stored["data"]["boot"] + 3600):
            self.assertTrue(choice.wanted())  # (the PC has restarted since: ticked again)
        stored["data"] = {"on": False}  # (no start time: as if never chosen)
        self.assertTrue(choice.wanted())


class EditorTests(unittest.TestCase):
    """A Colour element's colour picked with Preview: the engine follows while the picker is open."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from centralight.new_show import show_from_patch
        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.gui_fixtures import with_inputs

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.engine = FakeEngine(with_inputs(show_from_patch()))
        self.window = MainWindow(Settings(), self.engine.show, self.engine, self.engine.fake_output, save_files=False,
                                 notes_path=Path(self._tmp.name) / "fixture_map.csv")
        self.window.resize(1366, 768)
        self.window.show()
        process(self.app, 3)
        self.window.open_editor("warm-white")
        self.page = self.window.editor_page
        self.page.select_element("E1")  # (Warm white's Colour element)
        process(self.app)
        self.field = self.page.details._fields["element:fill.colour"]

    def tearDown(self) -> None:
        self.window._shut_down = True
        self.window.editor_page.shutdown()
        self.window.document.close()
        self.window.close()
        self.window.deleteLater()
        process(self.app)

    def colour_in(self, show) -> str:
        return str(show.inputs["warm-white"].tracks[0][0]["fill"]["colour"]).upper()

    def pick(self, colours: list[str], accept: bool, preview: bool = True) -> list[str]:
        """Open the field's picker and pick these colours one after another; the engine's colour after each."""
        seen = []

        def run(dialog: LiveColourDialog) -> int:
            dialog.preview.setChecked(preview)
            for colour in colours:
                dialog.picker.setCurrentColor(QColor(colour))
                dialog.flush()
                process(self.app)
                seen.append(self.colour_in(self.engine.show))
            return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

        with mock.patch.object(LiveColourDialog, "exec", run):
            self.field.swatch.clicked.emit()
            process(self.app)
        return seen

    def test_ok_keeps_it_as_one_undo_step(self) -> None:
        before = self.colour_in(self.engine.show)
        steps = self.window.document.undo_stack.count()
        seen = self.pick(["#FF0000", "#00FF00", "#2040FF"], accept=True)
        self.assertEqual(seen, ["#FF0000", "#00FF00", "#2040FF"])  # (the engine followed while it was open)
        self.assertEqual(self.colour_in(self.window.document.show), "#2040FF")
        self.assertEqual(self.window.document.undo_stack.count(), steps + 1)
        self.window.undo()
        self.assertEqual(self.colour_in(self.window.document.show), before)

    def test_cancel_puts_it_back_with_no_step(self) -> None:
        before = self.colour_in(self.engine.show)
        steps = self.window.document.undo_stack.count()
        seen = self.pick(["#FF0000", "#00FF00"], accept=False)
        self.assertEqual(seen, ["#FF0000", "#00FF00"])
        self.assertEqual(self.colour_in(self.engine.show), before)
        self.assertEqual(self.colour_in(self.window.document.show), before)
        self.assertEqual(self.window.document.undo_stack.count(), steps)

    def test_without_preview_it_changes_only_on_ok(self) -> None:
        before = self.colour_in(self.engine.show)
        seen = self.pick(["#FF0000", "#00FF00"], accept=True, preview=False)
        self.assertEqual(seen, [before, before])
        self.assertEqual(self.colour_in(self.engine.show), "#00FF00")

    def test_cancel_on_a_keyframed_colour_leaves_no_keyframe(self) -> None:
        """(Review: Cancel left the key the live changes had added, with an undo step, and the input played differently.)"""
        from centralight.input_edit import set_element_value

        document = self.window.document
        keys = {"keys": [[0.0, "#FF0000", "linear"], [4.0, "#0000FF"]]}
        document.apply(set_element_value(document.show, "warm-white", "E1", "fill.colour", keys), "Keyframes")
        process(self.app)
        self.page.seek(2.0)
        process(self.app)
        self.field = self.page.details._fields["element:fill.colour"]
        before = self.engine.show.inputs["warm-white"].tracks[0][0]["fill"]["colour"]
        steps = document.undo_stack.count()
        self.pick(["#00FF00", "#FFFF00"], accept=False)
        self.assertEqual(self.engine.show.inputs["warm-white"].tracks[0][0]["fill"]["colour"], before)
        self.assertEqual(document.show.inputs["warm-white"].tracks[0][0]["fill"]["colour"], before)
        self.assertEqual(document.undo_stack.count(), steps)
        self.pick(["#00FF00"], accept=True)  # (OK: one keyframe at the playhead, one step)
        colour = document.show.inputs["warm-white"].tracks[0][0]["fill"]["colour"]
        self.assertEqual([round(k[0], 3) for k in colour["keys"]], [0.0, 2.0, 4.0])
        self.assertEqual(document.undo_stack.count(), steps + 1)

    def test_a_cancelled_pick_keeps_redo(self) -> None:
        """(Review: the first live change cleared Redo, and Cancel then dropped its step, so Redo was gone.)"""
        document = self.window.document
        self.pick(["#FF0000"], accept=True)
        self.window.undo()
        self.assertTrue(document.undo_stack.canRedo())
        self.pick(["#00FF00", "#0000FF"], accept=False)
        self.assertTrue(document.undo_stack.canRedo())

    def test_a_colour_typed_but_not_entered_counts_first(self) -> None:
        """(Review: the picker started from the old colour, so Cancel threw the typed one away.)"""
        self.field.text.setFocus()
        self.field.text.setText("#FF0000")
        process(self.app)
        started = []

        def run(dialog: LiveColourDialog) -> int:
            started.append(dialog.colour())
            return QDialog.DialogCode.Rejected

        with mock.patch.object(LiveColourDialog, "exec", run):
            self.field.swatch.clicked.emit()
            process(self.app)
        self.assertEqual(started, ["#FF0000"])
        self.assertEqual(self.colour_in(self.window.document.show), "#FF0000")

    def test_two_picks_of_an_inputs_own_colour_are_two_steps(self) -> None:
        """(Review: the picks of a local colour variable merged into one undo step once its row had been rebuilt.)"""
        details, document = self.page.details, self.window.document
        self.page.select_element("")  # (the Input tab, with its local variables)
        process(self.app)
        panel = details.locals_panel
        self.assertIsNotNone(panel)
        name = panel.add("colour")
        process(self.app)
        steps = document.undo_stack.count()
        for colour in ("#00FF00", "#0000FF"):
            details.refresh(document.show)  # (its row may be rebuilt between picks)
            details._rebuild()
            process(self.app)
            self.field = details.locals_panel.rows[name].value
            self.pick([colour], accept=True)
        self.assertEqual(document.undo_stack.count(), steps + 2)

    def test_saving_waits_for_the_picker_to_close(self) -> None:
        document = self.window.document
        held = []

        def run(dialog: LiveColourDialog) -> int:
            dialog.picker.setCurrentColor(QColor("#FF0000"))
            dialog.flush()
            held.append(document.saving_held())
            return QDialog.DialogCode.Accepted

        with mock.patch.object(LiveColourDialog, "exec", run):
            self.field.swatch.clicked.emit()
        self.assertEqual(held, [True])
        self.assertFalse(document.saving_held())

    def test_saving_comes_back_even_if_the_panel_rebuilds_meanwhile(self) -> None:
        """(A global automation loading another show while the picker is open, say: the old field's signals are
        blocked and it's deleted, but saving must not stay held.)"""
        document = self.window.document

        def run(dialog: LiveColourDialog) -> int:
            dialog.picker.setCurrentColor(QColor("#FF0000"))
            dialog.flush()
            self.page.details._rebuild()
            process(self.app)
            return QDialog.DialogCode.Accepted

        with mock.patch.object(LiveColourDialog, "exec", run):
            self.field.swatch.clicked.emit()
        process(self.app)
        self.assertFalse(document.saving_held())


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def test_a_show_variable_colour_follows_live(self) -> None:
        from centralight.model import GlobalVar
        from centralight.ui.variables_panel import VariablesPanel

        panel = VariablesPanel()
        self.addCleanup(panel.deleteLater)
        sent = []
        panel.changed.connect(lambda name, value: sent.append((name, value)))
        panel.set_globals((GlobalVar("accent", "colour", "#FF0000", dashboard=True),))
        process(self.app)
        from centralight.ui.editor_widgets import ColourField

        field = panel.findChildren(ColourField)[0]
        field.set_value("#FF0000")

        def run(dialog: LiveColourDialog) -> int:
            for colour in ("#00FF00", "#0000FF"):
                dialog.picker.setCurrentColor(QColor(colour))
                dialog.flush()
            return QDialog.DialogCode.Rejected

        with mock.patch.object(LiveColourDialog, "exec", run):
            field.swatch.clicked.emit()
        self.assertEqual(sent, [("accent", "#00FF00"), ("accent", "#0000FF"), ("accent", "#FF0000")])  # (back on Cancel)


if __name__ == "__main__":
    unittest.main()
