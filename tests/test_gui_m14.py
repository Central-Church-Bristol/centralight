"""M14's smaller changes, driven like a person would: renaming an input from its tile or the Editor's title."""

from __future__ import annotations

from dataclasses import replace
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog, QMenu

from centralight.new_show import show_from_patch
from tests.gui_fixtures import with_inputs
from tests.qt_support import process
from tests.test_gui_editor import EditorTestCase


class RenameTests(EditorTestCase):
    def tile_menu(self, window, input_id: str) -> QMenu:
        menu = window.dashboard.tile_menu(input_id)  # (what a right-click shows)
        return menu

    def test_rename_from_the_tile_menu(self) -> None:
        window, engine = self.make()
        menu = self.tile_menu(window, "warm-white")
        texts = [action.text() for action in menu.actions()]
        self.assertIn("Rename…", texts)
        self.assertLess(texts.index("Edit with AI…"), texts.index("Rename…"))
        rename = next(action for action in menu.actions() if action.text() == "Rename…")
        with mock.patch.object(QInputDialog, "getText", return_value=("  Warm wash ", True)) as asked:
            rename.trigger()
        self.assertEqual(asked.call_args.kwargs.get("text"), "Warm white")  # (it starts from the old name)
        self.assertEqual(window.document.show.inputs["warm-white"].name, "Warm wash")
        self.assertEqual(engine.show.inputs["warm-white"].name, "Warm wash")
        process(self.app)
        self.assertEqual(window.dashboard.grid.tiles["warm-white"].name.full_text(), "Warm wash")
        window.undo()
        self.assertEqual(window.document.show.inputs["warm-white"].name, "Warm white")

    def test_cancel_or_nothing_typed_changes_nothing(self) -> None:
        window, _engine = self.make()
        for answer in (("Warm wash", False), ("   ", True), ("Warm white", True)):
            with mock.patch.object(QInputDialog, "getText", return_value=answer):
                window.ask_rename_input("warm-white")
        self.assertEqual(window.document.show.inputs["warm-white"].name, "Warm white")
        self.assertFalse(window.document.undo_group.canUndo())

    def test_locked_or_read_only_asks_nothing(self) -> None:
        window, _engine = self.make()
        window._locked = True
        with mock.patch.object(QInputDialog, "getText", return_value=("Warm wash", True)) as asked:
            window.ask_rename_input("warm-white")
        asked.assert_not_called()
        self.assertEqual(window.document.show.inputs["warm-white"].name, "Warm white")

    def test_click_the_editor_title_to_rename(self) -> None:
        window, engine = self.make()
        window.open_editor("warm-white")
        process(self.app)
        title = window.editor_page.editing
        self.assertEqual(title.full_text(), "Editing: Warm white")
        QTest.mouseClick(title.label, Qt.MouseButton.LeftButton)
        process(self.app)
        self.assertTrue(title.is_editing())
        self.assertTrue(title.edit.isVisible())
        self.assertEqual(title.edit.text(), "Warm white")
        title.edit.selectAll()
        QTest.keyClicks(title.edit, "Warm wash")
        QTest.keyClick(title.edit, Qt.Key.Key_Return)
        process(self.app)
        self.assertFalse(title.is_editing())
        self.assertEqual(window.document.show.inputs["warm-white"].name, "Warm wash")
        self.assertEqual(engine.show.inputs["warm-white"].name, "Warm wash")
        self.assertEqual(title.full_text(), "Editing: Warm wash")
        window.undo()
        self.assertEqual(window.document.show.inputs["warm-white"].name, "Warm white")
        self.assertEqual(title.full_text(), "Editing: Warm white")

    def test_esc_leaves_the_name_alone(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        title = window.editor_page.editing
        self.assertTrue(title.start())
        title.edit.selectAll()
        QTest.keyClicks(title.edit, "Something else")
        QTest.keyClick(title.edit, Qt.Key.Key_Escape)
        process(self.app)
        self.assertFalse(title.is_editing())
        self.assertEqual(window.document.show.inputs["warm-white"].name, "Warm white")
        self.assertFalse(window.document.undo_group.canUndo())

    def test_nothing_to_rename_or_read_only(self) -> None:
        window, _engine = self.make()
        title = window.editor_page.editing
        self.assertFalse(title.start())  # (no input open: "Editing: nothing")
        read_only = replace(with_inputs(show_from_patch()), read_only=True)
        window, _engine = self.make(read_only)
        window.open_editor("warm-white")
        self.assertFalse(window.editor_page.editing.start())


class OpensPausedTests(EditorTestCase):
    def test_an_input_opens_paused(self) -> None:
        """Infinite or timed, the playhead stays at the start until play is pressed (the user: "infinite mode
        shouldn't really have the playhead move")."""
        import time

        window, _engine = self.make()
        page = window.editor_page
        for input_id in ("warm-white", "christmas"):  # (infinite, then a timed one that loops)
            window.open_editor(input_id)
            process(self.app)
            self.assertFalse(page.playing, input_id)
            self.assertFalse(page.timeline.playing, input_id)
            time.sleep(0.15)
            page._tick()
            self.assertEqual(page.playhead, 0.0, input_id)
        page.set_playing(True)  # (play still works)
        self.assertTrue(page.timeline.playing)
        time.sleep(0.15)
        page._tick()
        self.assertGreater(page.playhead, 0.1)
        window.open_editor("warm-white")  # (another input: paused again, at its start)
        self.assertFalse(page.playing)
        self.assertEqual(page.playhead, 0.0)

class DeleteKeyTests(EditorTestCase):
    """Delete or Backspace in the Selected view removes the selected shape (never the element)."""

    def open_stage(self):
        window, engine = self.make()
        window.open_editor("stage-shapes")
        page = window.editor_page
        page.select_element("E2")  # (a Paint element with a rectangle S1 and an ellipse S2)
        process(self.app, 3)
        return window, engine, page

    def shapes(self, window) -> list[str]:
        return [shape["id"] for shape in self.element(window, "stage-shapes", "E2").get("shapes", ())]

    def click_shape(self, page, x: float, y: float) -> None:
        canvas = page.selected_canvas
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, canvas.to_widget(x, y).toPoint())
        process(self.app)

    def test_delete_removes_the_selected_shape(self) -> None:
        window, engine, page = self.open_stage()
        self.click_shape(page, 0.72, 0.6)  # (the ellipse)
        self.assertEqual(page.shape_id, "S2")
        self.assertTrue(page.selected_canvas.hasFocus())
        steps = window.document.undo_stack.count()
        QTest.keyClick(page.selected_canvas, Qt.Key.Key_Delete)
        process(self.app)
        self.assertEqual(self.shapes(window), ["S1"])
        self.assertEqual([s["id"] for s in find_shapes(engine, "E2")], ["S1"])
        self.assertEqual(page.shape_id, "")
        self.assertEqual(window.document.undo_stack.count(), steps + 1)
        self.assertEqual(window.document.undo_stack.undoText(), "Remove Ellipse S2")
        self.assertIn("E2", [e["id"] for track in window.document.show.inputs["stage-shapes"].tracks for e in track])
        window.undo()
        self.assertEqual(self.shapes(window), ["S1", "S2"])

    def test_backspace_too(self) -> None:
        window, _engine, page = self.open_stage()
        page._shape_from_viewport("S1")
        page.selected_canvas.setFocus()
        process(self.app)
        QTest.keyClick(page.selected_canvas, Qt.Key.Key_Backspace)
        process(self.app)
        self.assertEqual(self.shapes(window), ["S2"])

    def test_with_no_shape_selected_nothing_goes(self) -> None:
        window, _engine, page = self.open_stage()
        self.click_shape(page, 0.05, 0.08)  # (empty: nothing selected, but the view has the keys)
        self.assertEqual(page.shape_id, "")
        steps = window.document.undo_stack.count()
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            QTest.keyClick(page.selected_canvas, key)
        process(self.app)
        self.assertEqual(self.shapes(window), ["S1", "S2"])
        self.assertEqual(page.element_id, "E2")  # (the element stays: only the timeline's Delete removes it)
        self.assertEqual(window.document.undo_stack.count(), steps)

    def test_not_while_typing_in_a_field(self) -> None:
        from PySide6.QtWidgets import QLineEdit

        window, _engine, page = self.open_stage()
        self.click_shape(page, 0.72, 0.6)
        self.assertEqual(page.shape_id, "S2")
        field = next(w for w in page.details.findChildren(QLineEdit) if w.isVisible() and w.isEnabled())
        field.setFocus()
        process(self.app)
        QTest.keyClick(field, Qt.Key.Key_Delete)
        QTest.keyClick(field, Qt.Key.Key_Backspace)
        process(self.app)
        self.assertEqual(self.shapes(window), ["S1", "S2"])

    def test_in_mask_mode_it_removes_the_selected_mask_shape(self) -> None:
        """Mask mode has shapes of its own (item 8): Delete removes the selected one there, never a Paint shape."""
        window, _engine, page = self.open_stage()
        page._shape_from_viewport("S2")
        QTest.mouseClick(page.selected_panel.mask, Qt.MouseButton.LeftButton)
        page.selected_canvas.setFocus()
        process(self.app)
        QTest.keyClick(page.selected_canvas, Qt.Key.Key_Delete)  # (no mask shape selected: nothing goes)
        process(self.app)
        self.assertEqual(self.shapes(window), ["S1", "S2"])
        page.selected_panel.rect_tool.setChecked(True)
        canvas = page.selected_canvas
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, canvas.to_widget(0.1, 0.2).toPoint())
        QTest.mouseMove(canvas, canvas.to_widget(0.3, 0.8).toPoint())
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, canvas.to_widget(0.3, 0.8).toPoint())
        process(self.app)
        element = next(e for track in window.document.show.inputs["stage-shapes"].tracks for e in track if e["id"] == "E2")
        mask_ids = [s["id"] for s in element["mask"]["shapes"]]
        self.assertEqual(len(mask_ids), 1)
        self.assertEqual(page.mask_shape_id, mask_ids[0])
        canvas.setFocus()
        QTest.keyClick(canvas, Qt.Key.Key_Delete)
        process(self.app)
        element = next(e for track in window.document.show.inputs["stage-shapes"].tracks for e in track if e["id"] == "E2")
        self.assertNotIn("shapes", element["mask"])
        self.assertEqual(self.shapes(window), ["S1", "S2"])
        self.assertEqual(window.document.undo_stack.undoText(), f"Remove mask rectangle {mask_ids[0]}")


def find_shapes(engine, element_id: str) -> list:
    from centralight.input_edit import find_element

    return list(find_element(engine.show.inputs["stage-shapes"], element_id)[2].get("shapes", ()))

if __name__ == "__main__":
    import unittest

    unittest.main()
