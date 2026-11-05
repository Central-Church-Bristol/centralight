"""M14 item 8: shapes in masks. A mask starts shown (or hidden) everywhere; its painted strokes hide; then its shapes,
in order, each show or hide the element inside them (by their softness, at each light's dot); the per-light ticks go on
top. Masks from before work out exactly as they did. In the Editor, Mask mode has Layer mode's tools."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from centralight.assets import MASKS_DIR, save_asset
from centralight.elements import RenderContext, element_keep
from centralight.light_elements import light_mask
from centralight.lights import lights_for
from centralight.new_show import show_from_patch
from centralight.show_io import freeze, input_from_dict, input_to_dict


def rect(shape_id: str, mode: str, x, w: float, softness: float = 0.0, **more) -> dict:
    return {"id": shape_id, "type": "rect", "mode": mode, "x": x, "y": 0.5, "w": w, "h": 3.0, "softness": softness, **more}


class MaskShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.show = show_from_patch()
        self.lights = lights_for(self.show.fixtures)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def at_lights(self, mask: dict | None, t: float = 0.0):
        ctx = RenderContext(0, 0, lights=self.lights, per_light=True)
        ctx.folder = self.folder.name
        ctx.time = t
        element = freeze({"id": "E1", "kind": "colour", **({"mask": mask} if mask is not None else {})})
        return light_mask(element, ctx)

    def picture(self, mask: dict, t: float = 0.0):
        ctx = RenderContext(160, 90)
        ctx.folder = self.folder.name
        ctx.time = t
        return element_keep(freeze({"id": "E1", "kind": "colour", "mask": mask}), ctx)

    def by_light(self, keep) -> dict[str, float]:
        return {light_id: float(v) for light_id, v in zip(self.lights.ids, keep)}

    def left_and_right(self) -> tuple[list[str], list[str]]:
        left = [i for i, x in zip(self.lights.ids, self.lights.x) if x < 0.45]
        right = [i for i, x in zip(self.lights.ids, self.lights.x) if x > 0.55]
        self.assertTrue(left and right)
        return left, right

    def painted_left_half(self) -> str:
        grey = np.full((360, 640), 255, dtype=np.uint8)
        grey[:, :320] = 0  # the left half hidden
        return save_asset(self.folder.name, MASKS_DIR, grey)

    def test_the_brushs_preview_has_the_shapes_and_start_too(self) -> None:
        """(Review: in Mask mode, while painting, the preview showed the strokes alone: the red of Hide shapes and of
        "Hidden everywhere" went, and a stroke on a hidden mask looked as if it hid something.)"""
        ctx = RenderContext(160, 90)
        ctx.folder = self.folder.name
        stroke = np.ones((90, 160), dtype=np.float32)
        stroke[:, 120:] = 0.0  # (the right quarter painted over: hidden)
        hide_left = {"id": "M1", "type": "rect", "mode": "hide", "x": 0.125, "y": 0.5, "w": 0.25, "h": 1.0}
        keep = element_keep(freeze({"id": "E1", "kind": "colour", "mask": {"shapes": [hide_left]}}), ctx, painting=stroke)
        self.assertLess(float(keep[45, 10]), 0.1)  # (the Hide shape's left quarter)
        self.assertGreater(float(keep[45, 80]), 0.9)  # (the middle shows)
        self.assertLess(float(keep[45, 150]), 0.1)  # (the stroke)
        show_left = {**hide_left, "mode": "show"}
        hidden = element_keep(freeze({"id": "E1", "kind": "colour", "mask": {"start": "hidden", "shapes": [show_left]}}),
                              ctx, painting=np.ones((90, 160), dtype=np.float32))
        self.assertGreater(float(hidden[45, 10]), 0.9)  # (only there...)
        self.assertLess(float(hidden[45, 80]), 0.1)  # (...and a stroke changes nothing on a hidden mask)
        plain = element_keep(freeze({"id": "E1", "kind": "colour"}), ctx, painting=stroke)
        np.testing.assert_array_equal(plain, stroke)  # (no mask yet: the first strokes are the mask)

    def test_old_masks_are_unchanged(self) -> None:
        self.assertIsNone(self.at_lights(None))
        self.assertIsNone(self.at_lights({}))
        some = list(self.lights.ids[:2])
        keep = self.by_light(self.at_lights({"exclude": some}))
        self.assertEqual({k for k, v in keep.items() if v == 0.0}, set(some))
        left, right = self.left_and_right()
        keep = self.by_light(self.at_lights({"paint": self.painted_left_half(), "exclude": some}))
        for light in left:
            self.assertEqual(keep[light], 0.0)
        for light in right:
            self.assertEqual(keep[light], 0.0 if light in some else 1.0)
        self.assertIsNone(self.picture({"exclude": some}))  # (the ticks show on the dots, not the picture)

    def test_hide_and_show_shapes(self) -> None:
        left, right = self.left_and_right()
        keep = self.by_light(self.at_lights({"shapes": [rect("M1", "hide", 0.25, 0.5)]}))
        self.assertTrue(all(keep[i] == 0.0 for i in left) and all(keep[i] == 1.0 for i in right))
        keep = self.by_light(self.at_lights({"shapes": [rect("M1", "show", 0.25, 0.5)]}))
        self.assertTrue(all(v == 1.0 for v in keep.values()))  # (shown everywhere already: a Show shape changes nothing)
        keep = self.by_light(self.at_lights({"start": "hidden", "shapes": [rect("M1", "show", 0.25, 0.5)]}))
        self.assertTrue(all(keep[i] == 1.0 for i in left) and all(keep[i] == 0.0 for i in right))  # "only here"
        self.assertTrue(np.all(self.at_lights({"start": "hidden"}) == 0.0))
        no_mode = {"id": "M1", "type": "rect", "x": 0.25, "y": 0.5, "w": 0.5, "h": 3.0}  # (written by hand: it hides)
        keep = self.by_light(self.at_lights({"shapes": [no_mode]}))
        self.assertTrue(all(keep[i] == 0.0 for i in left))

    def test_shapes_in_order(self) -> None:
        left, _right = self.left_and_right()
        hide_then_show = self.by_light(self.at_lights({"shapes": [rect("M1", "hide", 0.5, 2.0), rect("M2", "show", 0.25, 0.5)]}))
        show_then_hide = self.by_light(self.at_lights({"shapes": [rect("M2", "show", 0.25, 0.5), rect("M1", "hide", 0.5, 2.0)]}))
        self.assertTrue(all(hide_then_show[i] == 1.0 for i in left))
        self.assertTrue(all(v == 0.0 for v in show_then_hide.values()))

    def test_softness_at_the_dot(self) -> None:
        light = self.lights.ids[0]
        x = float(self.lights.x[0])
        hard = self.by_light(self.at_lights({"shapes": [rect("M1", "hide", x - 0.1, 0.2)]}))[light]  # (its edge on the dot)
        soft = self.by_light(self.at_lights({"shapes": [rect("M1", "hide", x - 0.1, 0.2, softness=1.0)]}))[light]
        self.assertIn(hard, (0.0, 1.0))
        self.assertGreater(soft, 0.2)
        self.assertLess(soft, 0.8)

    def test_shapes_go_over_the_painted_strokes(self) -> None:
        left, right = self.left_and_right()
        keep = self.by_light(self.at_lights({"paint": self.painted_left_half(), "shapes": [rect("M1", "show", 0.25, 0.5)]}))
        self.assertTrue(all(keep[i] == 1.0 for i in left))  # (the strokes hid the left; the Show shape after them shows it)
        keep = self.by_light(self.at_lights({"start": "hidden", "paint": self.painted_left_half()}))
        self.assertTrue(all(v == 0.0 for v in keep.values()))  # (strokes only hide: nothing to undo on a hidden mask)

    def test_a_keyframed_shape_reveals_the_lights_one_by_one(self) -> None:
        sweep = {"start": "hidden", "shapes": [rect("M1", "show", {"keys": [[0, -0.3, "linear"], [10, 1.3]]}, 0.2)]}
        order = np.argsort(self.lights.x)
        first, last = self.lights.ids[order[0]], self.lights.ids[order[-1]]
        over_first = (float(self.lights.x[order[0]]) + 0.3) / 0.16  # (when the band's middle is over each one)
        over_last = (float(self.lights.x[order[-1]]) + 0.3) / 0.16
        early = self.by_light(self.at_lights(sweep, t=over_first))
        late = self.by_light(self.at_lights(sweep, t=over_last))
        self.assertEqual((early[first], early[last]), (1.0, 0.0))
        self.assertEqual((late[first], late[last]), (0.0, 1.0))
        self.assertFalse(np.array_equal(self.picture(sweep, t=over_first), self.picture(sweep, t=over_last)))

    def test_the_ticks_go_on_top(self) -> None:
        light = self.lights.ids[0]
        keep = self.by_light(self.at_lights({"start": "hidden", "exclude": [light], "shapes": [rect("M1", "show", 0.5, 3.0)]}))
        self.assertEqual(keep[light], 0.0)
        self.assertTrue(all(v == 1.0 for k, v in keep.items() if k != light))

    def test_the_picture_agrees_with_the_lights(self) -> None:
        mask = {"start": "hidden", "shapes": [rect("M1", "show", 0.3, 0.3, softness=0.6),
                                              {"id": "M2", "type": "ellipse", "mode": "hide", "x": 0.3, "y": 0.5,
                                               "w": 0.12, "h": 0.3, "softness": 0.6}]}
        picture = self.picture(mask)
        lights = self.at_lights(mask)
        for value, x, y in zip(lights, self.lights.x, self.lights.y):
            px = int(min(159, max(0, x * 160)))
            py = int(min(89, max(0, y * 90)))
            self.assertAlmostEqual(float(picture[py, px]), float(value), delta=0.2)

    def test_files_keep_them(self) -> None:
        mask = {"exclude": ["slimpar-101"], "start": "hidden",
                "shapes": [rect("M1", "show", {"keys": [[0, 0.2], [4, 0.8]]}, 0.3, softness=0.4)]}
        data = {"format": "centralight-input", "version": 1, "id": "m", "name": "M",
                "tracks": [[{"id": "E1", "kind": "ndi", "mask": mask}], [], [], []]}
        loaded, problems = input_from_dict(data)
        self.assertEqual(problems, [])
        saved = input_to_dict(loaded)
        self.assertEqual(saved["tracks"][0][0]["mask"], mask)


class SchemaAndGuideTests(unittest.TestCase):
    def test_schema_and_guide(self) -> None:
        import json

        from centralight.ai_guide import check_input_data, examples, guide_text, input_schema

        show = show_from_patch()
        schema = input_schema(show)
        ndi = next(k for k in schema["properties"]["tracks"]["items"]["items"]["oneOf"] if k["properties"]["kind"]["const"] == "ndi")
        mask = ndi["properties"]["mask"]["properties"]
        self.assertEqual(mask["start"]["enum"], ["shown", "hidden"])
        shape = mask["shapes"]["items"]["properties"]
        self.assertEqual(shape["mode"]["enum"], ["show", "hide"])
        for key in ("x", "y", "w", "h", "rotation", "softness"):
            self.assertIn(key, shape)
        self.assertNotIn("colour", shape)
        text = guide_text(show)
        self.assertIn('"start": "hidden"', text)
        self.assertIn("mask shapes", text.lower())
        found = [data for _what, data in examples(show) for track in data["tracks"] for e in track if (e.get("mask") or {}).get("shapes")]
        self.assertTrue(found)  # (an example with mask shapes)
        for data in found:
            _inp, problems, _notes = check_input_data(json.loads(json.dumps(data)), show)
            self.assertEqual(problems, [])


class EditorTests(unittest.TestCase):
    """Mask mode in the Editor: draw a mask shape, Show / Hide, Start with, order, drag, Remove (one undo step each)."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.qt_support import qt_app

        cls.app = qt_app()

    def setUp(self) -> None:
        from centralight.new_show import create_show
        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.gui_fixtures import with_inputs
        from tests.qt_support import process

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        engine = FakeEngine(with_inputs(create_show(Path(self._tmp.name) / "Show")))
        self.window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                                 notes_path=Path(self._tmp.name) / "fixture_map.csv")
        self.window.resize(1366, 768)
        self.window.show()
        process(self.app, 3)
        self.window.open_editor("stage-shapes")
        self.page = self.window.editor_page
        self.page.select_element("E1")  # (the NDI element: masks are for every kind)
        process(self.app)

    def tearDown(self) -> None:
        from tests.qt_support import process

        self.window._shut_down = True
        self.window.editor_page.shutdown()
        self.window.document.close()
        self.window.close()
        self.window.deleteLater()
        process(self.app)

    def element(self):
        return next(e for track in self.window.document.show.inputs["stage-shapes"].tracks for e in track if e["id"] == "E1")

    def draw(self, x0: float, y0: float, x1: float, y1: float) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from tests.qt_support import process

        canvas = self.page.selected_canvas
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, canvas.to_widget(x0, y0).toPoint())
        QTest.mouseMove(canvas, canvas.to_widget(x1, y1).toPoint())
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, canvas.to_widget(x1, y1).toPoint())
        process(self.app)

    def button(self, text: str):
        from PySide6.QtWidgets import QPushButton

        return next(b for b in self.page.details.findChildren(QPushButton) if b.text() == text and b.isVisible())

    def test_shapes_that_arent_a_list_dont_stop_the_editor(self) -> None:
        """(Review: an input written with "shapes": {...} made the Editor raise an error at every change.)"""
        from centralight.input_edit import set_element_value
        from tests.qt_support import process

        document = self.window.document
        self.page.shape_id = "S1"
        document.apply(set_element_value(document.show, self.page.input_id, self.page.element_id, "shapes", {"S1": {}}), "Odd")
        process(self.app)
        self.assertEqual(self.page.shape_id, "")
        self.assertIsNone(self.page.details.current_shape())

    def test_undoing_a_drawn_mask_shape_leaves_nothing_selected(self) -> None:
        """(Review: the Editor kept "M1" selected after its undo, while the details had none; after a redo the Selected
        view showed its handles with nothing selected in the details.)"""
        from tests.qt_support import process

        panel, document = self.page.selected_panel, self.window.document
        panel.mask.setChecked(True)
        panel.rect_tool.setChecked(True)
        self.draw(0.1, 0.2, 0.4, 0.8)
        self.assertEqual(self.page.mask_shape_id, "M1")
        document.undo()
        process(self.app)
        self.assertEqual(self.page.mask_shape_id, "")
        document.redo()
        process(self.app)
        self.assertEqual(self.page.mask_shape_id, "")  # (as in the details: nothing selected)

    def test_drawing_show_hide_order_and_remove(self) -> None:
        from PySide6.QtWidgets import QListWidget

        from tests.qt_support import process

        panel, document = self.page.selected_panel, self.window.document
        panel.mask.setChecked(True)
        panel.rect_tool.setChecked(True)
        self.draw(0.1, 0.2, 0.4, 0.8)
        shapes = self.element()["mask"]["shapes"]
        self.assertEqual([(s["id"], s["type"], s["mode"]) for s in shapes], [("M1", "rect", "hide")])  # (a shown mask: hides)
        self.assertAlmostEqual(shapes[0]["x"], 0.25, delta=0.01)
        self.assertEqual(document.undo_stack.undoText(), "Draw mask rectangle M1")
        self.assertEqual(self.page.mask_shape_id, "M1")
        self.assertTrue(panel.select_tool.isChecked())
        lists = [w for w in self.page.details.findChildren(QListWidget) if w.count() and w.item(0).text().startswith("Hide ·")]
        self.assertEqual(lists[0].item(0).text(), "Hide · Rectangle M1")
        self.assertIn("mask_shape:softness", self.page.details._fields)

        self.button("Show").click()
        process(self.app)
        self.assertEqual(self.element()["mask"]["shapes"][0]["mode"], "show")
        self.assertEqual(document.undo_stack.undoText(), "Mask rectangle M1: show the element inside it")

        start = self.page.details._fields["mask:start"]
        start.setCurrentIndex(start.findData("hidden"))
        process(self.app)
        self.assertEqual(self.element()["mask"]["start"], "hidden")
        self.assertEqual(document.undo_stack.undoText(), "NDI E1: mask starts hidden everywhere")

        panel.ellipse_tool.setChecked(True)
        self.draw(0.6, 0.3, 0.8, 0.6)
        self.assertEqual([(s["id"], s["mode"]) for s in self.element()["mask"]["shapes"]], [("M1", "show"), ("M2", "show")])

        self.button("Back").click()  # (M2 first now)
        process(self.app)
        self.assertEqual([s["id"] for s in self.element()["mask"]["shapes"]], ["M2", "M1"])
        self.assertEqual(document.undo_stack.undoText(), "Move mask ellipse M2 back")

        self.button("Remove").click()
        process(self.app)
        self.assertEqual([s["id"] for s in self.element()["mask"]["shapes"]], ["M1"])
        self.assertEqual(self.page.mask_shape_id, "")
        self.window.undo()
        self.assertEqual([s["id"] for s in self.element()["mask"]["shapes"]], ["M2", "M1"])

    def test_dragging_a_mask_shape_and_the_tint(self) -> None:
        from tests.qt_support import process

        panel = self.page.selected_panel
        panel.mask.setChecked(True)
        panel.rect_tool.setChecked(True)
        self.draw(0.1, 0.2, 0.3, 0.8)  # (Hide, from 0.1 to 0.3 across)
        self.page.render_sync()
        keep = self.page._renderer.render_now(self._request()).keep
        h, w = keep.shape
        self.assertEqual(int(keep[h // 2, int(w * 0.2)]), 0)  # (the red tint: hidden inside it)
        self.assertEqual(int(keep[h // 2, int(w * 0.7)]), 255)
        steps = self.window.document.undo_stack.count()
        self.draw(0.2, 0.5, 0.6, 0.5)  # (Select: drag it right)
        self.assertAlmostEqual(self.element()["mask"]["shapes"][0]["x"], 0.6, delta=0.01)
        self.assertEqual(self.window.document.undo_stack.count(), steps + 1)
        self.assertEqual(self.window.document.undo_stack.undoText(), "Move mask rectangle M1")
        process(self.app)

    def _request(self):
        from centralight.editor_render import EditorRequest

        page = self.page
        return EditorRequest(page.show_data, page.input_id, page.element_id, page.playhead, page.variables())

    def test_picking_a_mask_shape_in_the_details_goes_to_mask_mode(self) -> None:
        from tests.qt_support import process

        self.button("+ Rectangle").click()
        process(self.app)
        self.assertEqual(self.element()["mask"]["shapes"][0]["id"], "M1")
        self.assertTrue(self.page.selected_canvas.mask_mode)
        self.assertTrue(self.page.selected_panel.select_tool.isChecked())
        self.assertEqual(self.page.selected_canvas.selected_shape, "M1")
