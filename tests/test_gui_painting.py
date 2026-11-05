"""M7 in the window: painting with the mouse in the Selected view (brush, eraser, bucket, gradient, light colour,
the eyedropper), each stroke one undo step that's exact, painting a mask, the picture gizmo for Image / NDI
elements, and the tools showing for the right elements and modes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from centralight.assets import load_mask, load_strokes
from centralight.input_edit import find_element
from centralight.new_show import create_show
from centralight.settings import Settings
from centralight.ui.main_window import MainWindow
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs
from tests.qt_support import process, qt_app


class PaintingTestCase(unittest.TestCase):
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

    def make(self) -> MainWindow:
        show = with_inputs(create_show(Path(self._tmp.name) / "Show"))
        engine = FakeEngine(show)
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                            notes_path=Path(self._tmp.name) / "fixture_map.csv")
        window.resize(1366, 768)
        window.show()
        process(self.app, 5)
        self.windows.append(window)
        return window

    def editor(self, window, input_id: str, element_id: str):
        window.open_editor(input_id)
        page = window.editor_page
        page.set_playing(False)
        page.select_element(element_id)
        process(self.app, 2)
        page.render_sync()
        return page

    def element(self, window, input_id: str, element_id: str):
        return find_element(window.document.show.inputs[input_id], element_id)[2]

    def at(self, canvas, fx: float, fy: float) -> QPoint:
        rect = canvas.canvas_rect()
        return QPoint(round(rect.left() + fx * rect.width()), round(rect.top() + fy * rect.height()))

    def drag(self, widget, points, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        QTest.mousePress(widget, Qt.MouseButton.LeftButton, modifiers, points[0])
        for p in points[1:]:
            QTest.mouseMove(widget, p)
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, modifiers, points[-1])
        process(self.app)

    def pick(self, page, button) -> None:
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        process(self.app)


class BrushTests(PaintingTestCase):
    def test_a_stroke_is_one_exact_undo_step(self) -> None:
        window = self.make()
        page = self.editor(window, "warm-white", "E1")
        panel, canvas = page.selected_panel, page.selected_canvas
        self.assertTrue(panel.brush_tool.isEnabled())
        self.pick(page, panel.brush_tool)
        self.assertTrue(panel.options.isVisibleTo(panel))
        panel.set_brush_colour((1.0, 0.0, 0.0))
        panel.size.setValue(20)
        self.drag(canvas, [self.at(canvas, 0.1, 0.5), self.at(canvas, 0.3, 0.5), self.at(canvas, 0.5, 0.5)])
        first = self.element(window, "warm-white", "E1")["strokes"]
        self.assertTrue(first.startswith("paint/"))
        folder = Path(window.document.show.folder)
        picture = load_strokes(folder / first)
        np.testing.assert_array_equal(picture[180, 192], (255, 0, 0, 255))  # painted along the line
        self.assertEqual(picture[40, 192, 3], 0)
        self.assertEqual(window.document.undo_stack.undoText(), "Paint E1: brush stroke")
        self.pick(page, panel.eraser_tool)
        self.drag(canvas, [self.at(canvas, 0.3, 0.45), self.at(canvas, 0.3, 0.55)])
        second = self.element(window, "warm-white", "E1")["strokes"]
        self.assertNotEqual(second, first)
        self.assertEqual(load_strokes(folder / second)[180, 192, 3], 0)  # rubbed out
        window.undo()
        self.assertEqual(self.element(window, "warm-white", "E1")["strokes"], first)  # exactly the picture before
        np.testing.assert_array_equal(load_strokes(folder / first), picture)
        window.undo()
        self.assertNotIn("strokes", self.element(window, "warm-white", "E1"))

    def test_bucket_gradient_light_and_dropper(self) -> None:
        window = self.make()
        page = self.editor(window, "warm-white", "E1")
        panel, canvas = page.selected_panel, page.selected_canvas
        folder = Path(window.document.show.folder)
        panel.set_brush_colour((0.0, 1.0, 0.0))
        self.pick(page, panel.bucket_tool)
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.at(canvas, 0.5, 0.5))
        process(self.app)
        picture = load_strokes(folder / self.element(window, "warm-white", "E1")["strokes"])
        self.assertTrue(np.all(picture[..., 3] == 255))  # the empty picture is one area: all filled
        self.assertEqual(window.document.undo_stack.undoText(), "Paint E1: bucket fill")
        window.undo()
        self.pick(page, panel.gradient_tool)
        panel.set_brush_colour((0.0, 0.0, 1.0))
        self.drag(canvas, [self.at(canvas, 0.0, 0.5), self.at(canvas, 0.5, 0.5), self.at(canvas, 1.0, 0.5)])
        picture = load_strokes(folder / self.element(window, "warm-white", "E1")["strokes"])
        self.assertGreater(picture[180, 5, 3], 240)
        self.assertLess(picture[180, 630, 3], 15)
        window.undo()
        self.pick(page, panel.light_tool)
        panel.set_brush_colour((1.0, 0.0, 1.0))
        fx = next(f for f in window.document.show.colour_fixtures() if f.id == "slimpar-106").map
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.at(canvas, fx.x, fx.y))
        process(self.app)
        picture = load_strokes(folder / self.element(window, "warm-white", "E1")["strokes"])
        np.testing.assert_array_equal(picture[round(fx.y * 360), round(fx.x * 640)], (255, 0, 255, 255))
        self.assertEqual(picture[20, 20, 3], 0)  # only that light's patch
        page.render_sync()
        self.pick(page, panel.dropper_tool)
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.at(canvas, fx.x, fx.y))
        process(self.app)
        np.testing.assert_allclose(canvas.brush_colour, (1.0, 0.0, 1.0), atol=0.02)

    def test_the_biggest_brush_keeps_up(self) -> None:
        """Above 30 pictures a second while painting with the biggest brush (the live preview included)."""
        import time

        window = self.make()
        page = self.editor(window, "stage-shapes", "E2")  # a busy Paint element: a gradient and two shapes
        panel, canvas = page.selected_panel, page.selected_canvas
        self.pick(page, panel.brush_tool)
        panel.size.setValue(120)
        points = [self.at(canvas, 0.1 + 0.8 * i / 29, 0.3 + 0.4 * (i % 2)) for i in range(30)]
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, points[0])
        started = time.perf_counter()
        for p in points[1:]:
            QTest.mouseMove(canvas, p)
            canvas._preview_at = 0.0  # draw the preview every time (the real thing does it at most 30 times a second)
            canvas.repaint()
        elapsed = time.perf_counter() - started
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, points[-1])
        self.assertLess(elapsed / 29, 1 / 30, f"{elapsed / 29 * 1000:.1f} ms a move")

    def test_tools_for_the_right_elements(self) -> None:
        window = self.make()
        page = self.editor(window, "stage-shapes", "E1")  # an NDI element: no shapes or painting
        panel = page.selected_panel
        self.assertFalse(panel.brush_tool.isEnabled())
        self.assertTrue(panel.select_tool.isEnabled())
        self.assertTrue(panel.dropper_tool.isEnabled())
        panel.mask.setChecked(True)
        process(self.app)
        # Mask mode (M14, changed 5 Nov): Paint's tools, in greys, for any element, and the Light bulb for the ticks
        for button in (panel.select_tool, panel.rect_tool, panel.ellipse_tool, panel.brush_tool, panel.eraser_tool,
                       panel.bucket_tool, panel.gradient_tool, panel.dropper_tool, panel.light_tool):
            self.assertTrue(button.isVisibleTo(panel) and button.isEnabled(), button.toolTip())
        self.assertTrue(panel.light_tool.isChecked())  # (it starts on ticking lights, as Mask mode did)
        self.assertIn("grey", panel.brush_tool.toolTip())
        self.assertIn("tick", panel.light_tool.toolTip())
        panel.layer.setChecked(True)
        process(self.app)
        self.assertIn("Paint element", panel.brush_tool.toolTip())
        self.assertFalse(panel.brush_tool.isEnabled())


class MaskAndGizmoTests(PaintingTestCase):
    def test_painting_a_mask(self) -> None:
        window = self.make()
        page = self.editor(window, "stage-shapes", "E1")
        panel, canvas = page.selected_panel, page.selected_canvas
        panel.mask.setChecked(True)
        self.pick(page, panel.brush_tool)  # (Mask mode's Brush paints the mask in its grey: black, to start with)
        self.assertTrue(panel.size.isVisibleTo(panel))
        self.assertFalse(panel.colour.isVisibleTo(panel))  # (a mask has no colour: greys, by the Grey button)
        self.assertTrue(panel.grey.isVisibleTo(panel))
        panel.size.setValue(40)
        self.drag(canvas, [self.at(canvas, 0.5, 0.2), self.at(canvas, 0.5, 0.8)])
        element = self.element(window, "stage-shapes", "E1")
        name = element["mask"]["paint"]
        mask = load_mask(Path(window.document.show.folder) / name)
        self.assertEqual(mask[180, 320], 0)  # hidden down the middle
        self.assertEqual(mask[180, 20], 255)
        self.assertEqual(element["mask"].get("exclude"), None)  # the ticked lights are untouched
        self.assertEqual(window.document.undo_stack.undoText(), "NDI E1: mask: brush stroke")
        self.pick(page, panel.eraser_tool)  # (and its Eraser rubs that out: white again)
        self.drag(canvas, [self.at(canvas, 0.5, 0.5), self.at(canvas, 0.5, 0.52)])
        mask = load_mask(Path(window.document.show.folder) / self.element(window, "stage-shapes", "E1")["mask"]["paint"])
        self.assertEqual(mask[180, 320], 255)  # shown again where Show painted
        window.undo()
        window.undo()
        self.assertNotIn("paint", self.element(window, "stage-shapes", "E1").get("mask") or {})

    def test_the_picture_gizmo(self) -> None:
        window = self.make()
        page = self.editor(window, "stage-shapes", "E1")
        canvas = page.selected_canvas
        rect = canvas.canvas_rect()
        self.drag(canvas, [self.at(canvas, 0.5, 0.5), self.at(canvas, 0.6, 0.5), self.at(canvas, 0.7, 0.5)])
        transform = self.element(window, "stage-shapes", "E1")["transform"]
        self.assertAlmostEqual(transform["x"], 0.7, delta=0.01)
        self.assertEqual(window.document.undo_stack.undoText(), "Move NDI E1")
        corners = canvas.picture_corners()
        corner = corners["br"]
        centre = corners["centre"]
        half_way = QPointF((corner.x() + centre.x()) / 2, (corner.y() + centre.y()) / 2)
        self.drag(canvas, [corner.toPoint(), half_way.toPoint()])
        self.assertAlmostEqual(self.element(window, "stage-shapes", "E1")["transform"]["scale"], 0.5, delta=0.03)
        knob = canvas.picture_corners()["rotate"]
        right = QPointF(canvas.picture_corners()["centre"].x() + 100, canvas.picture_corners()["centre"].y())
        self.drag(canvas, [knob.toPoint(), right.toPoint()])
        self.assertAlmostEqual(abs(self.element(window, "stage-shapes", "E1")["transform"]["rotation"]), 90.0, delta=3)
        window.undo()
        window.undo()
        window.undo()
        self.assertNotIn("transform", self.element(window, "stage-shapes", "E1"))
        self.assertGreater(rect.width(), 0)


if __name__ == "__main__":
    unittest.main()
