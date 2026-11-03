"""The Editor and the M2 dashboard (overlays, new / duplicate / delete), driven like a person would."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from centralight.input_edit import find_element
from centralight.new_show import create_show, show_from_patch
from centralight.settings import Settings
from centralight.ui.main_window import MainWindow
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app


class EditorTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = qt_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window._shut_down = True
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
        process(self.app)
        self._tmp.cleanup()

    def make(self, show=None, size=(1366, 768)) -> tuple[MainWindow, FakeEngine]:
        engine = FakeEngine(show or with_inputs(show_from_patch()))
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv")
        window.resize(*size)
        window.show()
        process(self.app, 5)
        self.windows.append(window)
        return window, engine

    def element(self, window, input_id: str, element_id: str):
        return find_element(window.document.show.inputs[input_id], element_id)[2]

    def drag(self, widget, start: QPointF, end: QPointF, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        a, b = start.toPoint(), end.toPoint()
        QTest.mousePress(widget, Qt.MouseButton.LeftButton, modifiers, a)
        for step in range(1, 6):
            QTest.mouseMove(widget, a + (b - a) * step / 5)
            process(self.app)
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, modifiers, b)
        process(self.app, 2)


class NewInputTests(EditorTestCase):
    def test_new_input_then_paint_then_colour(self) -> None:
        window, engine = self.make()
        QTest.mouseClick(window.dashboard.grid.new_tile.button, Qt.MouseButton.LeftButton)
        page = window.editor_page
        self.assertEqual(window.current_page(), "editor")
        new_id = page.input_id
        self.assertTrue(new_id.startswith("new-input"))
        self.assertIn(new_id, window.dashboard.grid.tiles)
        page.palette.items["paint"].activated.emit("paint")  # same as clicking it
        element_id = page.element_id
        self.assertEqual(element_id, "E1")
        self.assertEqual(self.element(window, new_id, "E1").get("kind"), "paint")
        self.assertEqual(engine.show.inputs[new_id].tracks[0][0]["id"], "E1")  # the engine has it too
        colour = page.details._fields["element:fill.colour"]
        colour.text.setText("#FF8800")
        colour._typed()
        self.assertEqual(self.element(window, new_id, "E1")["fill"]["colour"], "#FF8800")
        window.undo()
        self.assertEqual(self.element(window, new_id, "E1")["fill"]["colour"], "#86D6FF")

    def test_rename_and_tile_colour_from_the_input_tab(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        name = window.editor_page.details._fields["input:name"]
        name.setText("Warm wash")
        name.editingFinished.emit()
        self.assertEqual(window.document.show.inputs["warm-white"].name, "Warm wash")
        self.assertIn("Warm wash", window.editor_page.editing.full_text())
        self.assertEqual(window.dashboard.grid.tiles["warm-white"].name.full_text(), "Warm wash")


class ShapeEditingTests(EditorTestCase):
    def open_stage(self):
        window, engine = self.make()
        window.open_editor("stage-shapes")
        page = window.editor_page
        page.select_element("E2")
        process(self.app, 3)
        return window, engine, page

    def test_draw_a_rectangle(self) -> None:
        window, _engine, page = self.open_stage()
        canvas = page.selected_canvas
        QTest.mouseClick(page.selected_panel.rect_tool, Qt.MouseButton.LeftButton)
        self.drag(canvas, canvas.to_widget(0.1, 0.1), canvas.to_widget(0.3, 0.4))
        shapes = self.element(window, "stage-shapes", "E2")["shapes"]
        self.assertEqual(len(shapes), 3)
        new = shapes[-1]
        self.assertEqual(new["type"], "rect")
        self.assertAlmostEqual(new["x"], 0.2, delta=0.02)
        self.assertAlmostEqual(new["w"], 0.2, delta=0.02)
        self.assertEqual(page.shape_id, new["id"])
        self.assertTrue(page.selected_panel.select_tool.isChecked())  # back to Select after drawing

    def test_move_a_shape_is_one_undo_step(self) -> None:
        window, _engine, page = self.open_stage()
        canvas = page.selected_canvas
        before = self.element(window, "stage-shapes", "E2")["shapes"][1]  # the ellipse at 0.72, 0.6
        count = window.document.undo_stack.count()
        self.drag(canvas, canvas.to_widget(0.72, 0.6), canvas.to_widget(0.52, 0.4))
        after = self.element(window, "stage-shapes", "E2")["shapes"][1]
        self.assertAlmostEqual(after["x"], before["x"] - 0.2, delta=0.02)
        self.assertAlmostEqual(after["y"], before["y"] - 0.2, delta=0.02)
        self.assertEqual(window.document.undo_stack.count(), count + 1)
        self.assertEqual(page.shape_id, "S2")
        window.undo()
        self.assertEqual(self.element(window, "stage-shapes", "E2")["shapes"][1]["x"], before["x"])

    def test_resize_from_a_corner(self) -> None:
        window, _engine, page = self.open_stage()
        canvas = page.selected_canvas
        page._shape_from_viewport("S2")
        process(self.app)
        shape = self.element(window, "stage-shapes", "E2")["shapes"][1]
        corner = canvas.handle_points(shape)["br"]
        self.drag(canvas, corner, corner + QPointF(40, 30))
        bigger = self.element(window, "stage-shapes", "E2")["shapes"][1]
        self.assertGreater(bigger["w"], shape["w"])
        self.assertGreater(bigger["h"], shape["h"])

    def test_untick_a_light_and_click_one_in_mask_view(self) -> None:
        window, _engine, page = self.open_stage()
        tree = page.details.lights_tree
        found = tree.findItems("SlimPAR 101", Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)[0]
        found.setCheckState(0, Qt.CheckState.Unchecked)
        mask = self.element(window, "stage-shapes", "E2")["mask"]["exclude"]
        self.assertIn("slimpar-101", mask)
        QTest.mouseClick(page.selected_panel.mask, Qt.MouseButton.LeftButton)
        canvas = page.selected_canvas
        fx = window.document.show.fixture("slimpar-108")
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, canvas.to_widget(fx.map.x, fx.map.y).toPoint())
        mask = self.element(window, "stage-shapes", "E2")["mask"]["exclude"]
        self.assertIn("slimpar-108", mask)
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, canvas.to_widget(fx.map.x, fx.map.y).toPoint())
        self.assertNotIn("slimpar-108", self.element(window, "stage-shapes", "E2")["mask"]["exclude"])


class EditorControlsTests(EditorTestCase):
    def test_blur_on_the_screen_element(self) -> None:
        window, _engine = self.make()
        window.open_editor("follow-screen")
        page = window.editor_page
        page.select_element("E1")
        process(self.app)
        field = page.details._fields["element:blur"]
        self.assertIn("whole picture", field.toolTip())  # the help says what it does
        field.spin.setValue(40.0)
        self.assertEqual(self.element(window, "follow-screen", "E1")["blur"], 0.4)
        self.assertIn("blur", window.document.undo_group.undoText().lower())

    def test_typing_then_picking_something_else(self) -> None:
        """The details panel is rebuilt when another element is picked. A half-typed NDI source used to be
        deleted while it still had the keyboard, which could crash the app; now the edit is finished first,
        for the element it belongs to."""
        window, _engine = self.make()
        window.activateWindow()
        window.open_editor("stage-shapes")
        page = window.editor_page
        for _ in range(3):
            page.select_element("E1")  # the NDI element
            process(self.app)
            box = page.details._fields["element:source"].lineEdit()
            box.setFocus()
            box.selectAll()
            QTest.keyClicks(box, "Projector feed")
            page.select_element("E2")  # pick something else without pressing Enter
            process(self.app)
            self.assertEqual(self.element(window, "stage-shapes", "E1")["source"], "Projector feed")
            self.assertNotIn("source", self.element(window, "stage-shapes", "E2"))
            page.select_element("")
            process(self.app)
        # a choice that changes which rows show rebuilds the panel from inside its own signal
        page.select_element("E2")
        for kind in ("colour", "radial", "linear", "none", "radial"):
            fill = page.details._fields["element:fill.type"]  # a new box after each rebuild
            fill.setCurrentIndex(fill.findData(kind))
            process(self.app)
        self.assertEqual(self.element(window, "stage-shapes", "E2")["fill"]["type"], "radial")

    def test_solo_live_banner_and_send(self) -> None:
        window, engine = self.make()
        window.open_editor("worship-blue")
        page = window.editor_page
        QTest.mouseClick(page.solo_button, Qt.MouseButton.LeftButton)
        engine.tick()
        window.bridge.poll()
        process(self.app)
        self.assertEqual(engine.frames.get().solo, "worship-blue")
        self.assertTrue(page.live_banner.isVisible())
        self.assertEqual(engine.fake_output.last[4:7], bytes((0x20, 0x50, 0xFF)))
        QTest.mouseClick(window.header.tabs["dashboard"], Qt.MouseButton.LeftButton)  # leaving the Editor
        engine.tick()
        self.assertEqual(engine.frames.get().solo, "")
        window.open_editor("worship-blue")
        QTest.mouseClick(page.send_button, Qt.MouseButton.LeftButton)
        engine.settle(1.2)
        window.bridge.poll()
        process(self.app)
        self.assertEqual(engine.frames.get().base_id, "worship-blue")
        self.assertTrue(page.live_banner.isVisible())

    def test_drop_replaces_after_asking_and_transitions_set_the_input(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        timeline = window.editor_page.timeline
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.assertEqual(timeline.drop_element(0, "ndi"), "")
        self.assertEqual(window.document.show.inputs["warm-white"].tracks[0][0]["kind"], "paint")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            element_id = timeline.drop_element(0, "ndi")
        self.assertEqual(window.document.show.inputs["warm-white"].tracks[0][0]["kind"], "ndi")
        self.assertEqual(window.editor_page.element_id, element_id)
        window.editor_page.palette.items["transition:iris"].activated.emit("iris")
        self.assertEqual(window.document.show.inputs["warm-white"].transition.kind, "iris")
        timeline.setFocus()
        QTest.keyClick(timeline, Qt.Key.Key_Delete)
        self.assertEqual(window.document.show.inputs["warm-white"].tracks[0], ())

    def test_house_levels_and_transition_length(self) -> None:
        window, _engine = self.make()
        window.open_editor("warm-white")
        details = window.editor_page.details
        check, level = details._fields["house:dimmer-4"]
        check.setChecked(True)
        self.assertEqual(window.document.show.inputs["warm-white"].house["dimmer-4"], 1.0)
        level.spin.setValue(40.0)
        self.assertEqual(window.document.show.inputs["warm-white"].house["dimmer-4"], 0.4)
        check.setChecked(False)
        self.assertNotIn("dimmer-4", window.document.show.inputs["warm-white"].house)
        details._fields["transition:duration"].spin.setValue(3.0)
        self.assertEqual(window.document.show.inputs["warm-white"].transition.duration, 3.0)

    def test_image_files_are_copied_into_the_show(self) -> None:
        folder = Path(self._tmp.name) / "Show"
        window, _engine = self.make(with_inputs(create_show(folder)))
        window.open_editor("warm-white")
        element_id = window.editor_page.timeline.drop_element(1, "image", confirm=False)
        picture = Path(self._tmp.name) / "Sunrise photo.png"
        image = QImage(320, 180, QImage.Format.Format_RGB32)
        image.fill(QColor("#FF8800"))
        image.save(str(picture))
        window.editor_page.details.use_file(picture)
        self.assertEqual(self.element(window, "warm-white", element_id)["file"], "media/Sunrise photo.png")
        self.assertTrue((folder / "media" / "Sunrise photo.png").is_file())

    def test_viewports_draw(self) -> None:
        window, _engine = self.make()
        window.open_editor("stage-shapes")
        page = window.editor_page
        page.select_element("E2")
        page.render_sync()
        self.assertIsNotNone(page.result_canvas._image)
        self.assertIsNotNone(page.selected_canvas._image)
        colours = page.result_panel.strip._colours
        self.assertEqual(len(colours), 8)


class DashboardM2Tests(EditorTestCase):
    def test_overlay_buttons_and_layer_controls(self) -> None:
        window, engine = self.make()
        tile = window.dashboard.grid.tiles["pillars-purple"]
        QTest.mouseClick(tile.overlay_buttons[1], Qt.MouseButton.LeftButton)  # overlay 2
        engine.settle(1.2)
        window.bridge.poll()
        process(self.app)
        frame = engine.frames.get()
        self.assertEqual(frame.overlays[1].input_id, "pillars-purple")
        self.assertTrue(tile.overlay_buttons[1].property("inSlot"))
        row = window.dashboard.layer_rows["overlay2"]
        self.assertTrue(row.switch.isChecked())
        row.opacity._user_set(500)
        engine.settle(0.5)  # opacity glides to the new level
        self.assertAlmostEqual(engine.frames.get().overlays[1].opacity, 0.5, delta=0.01)
        QTest.mouseClick(row.switch, Qt.MouseButton.LeftButton)  # off
        engine.settle(1.2)
        window.bridge.poll()
        process(self.app)
        self.assertEqual(engine.frames.get().overlays[1].input_id, "")
        self.assertFalse(tile.overlay_buttons[1].property("inSlot"))

    def test_pencil_duplicate_and_delete(self) -> None:
        window, _engine = self.make()
        QTest.mouseClick(window.dashboard.grid.tiles["worship-blue"].edit_button, Qt.MouseButton.LeftButton)
        self.assertEqual(window.current_page(), "editor")
        self.assertEqual(window.editor_page.input_id, "worship-blue")
        window.dashboard.duplicate_requested.emit("worship-blue")
        self.assertIn("worship-blue-copy", window.document.show.inputs)
        order = list(window.document.show.input_order)
        self.assertEqual(order.index("worship-blue-copy"), order.index("worship-blue") + 1)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            window.dashboard.delete_requested.emit("worship-blue")
        self.assertNotIn("worship-blue", window.document.show.inputs)
        self.assertEqual(window.editor_page.input_id, "")  # the Editor lets go of a deleted input
        window.undo()
        self.assertIn("worship-blue", window.document.show.inputs)


if __name__ == "__main__":
    unittest.main()
