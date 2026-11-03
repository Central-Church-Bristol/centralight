"""M7 painting: brush maths, the eraser, mask brushes, bucket, gradient and light-colour fills on the 640x360
pictures; saving them as files named after their contents (so undo is exact); Paint elements drawing their
strokes and painted masks hiding parts of elements, on the lights; and both surviving a reload."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from centralight.assets import MASKS_DIR, PAINT_DIR, load_mask, load_strokes, save_asset
from centralight.bus import Instance, render_input
from centralight.elements import RenderContext
from centralight.input_edit import set_element_value
from centralight.model import Input
from centralight.new_show import create_show
from centralight.painting import Canvas, brush_kernel
from centralight.sampler import CANVAS_H, CANVAS_W, Sampler, sampler_for
from centralight.show_io import freeze, load_show, save_whole_show


class BrushTests(unittest.TestCase):
    def test_the_kernel(self) -> None:
        hard = brush_kernel(10, 0.0)
        self.assertEqual(hard.shape, (21, 21))
        self.assertEqual(hard[10, 10], 1.0)
        self.assertEqual(hard[10, 0], 1.0)  # a hard brush is solid to its edge
        self.assertEqual(hard[0, 0], 0.0)
        soft = brush_kernel(10, 1.0)
        self.assertEqual(soft[10, 10], 1.0)
        self.assertLess(soft[10, 2], 0.5)  # a soft one fades towards its edge
        self.assertIs(brush_kernel(10, 1.0), soft)  # remembered

    def test_paint_and_erase(self) -> None:
        canvas = Canvas()
        self.assertTrue(canvas.is_empty())
        canvas.stroke_to(100, 100, 12, 0.0, (1.0, 0.0, 0.0))
        canvas.stroke_to(300, 100, 12, 0.0, (1.0, 0.0, 0.0))
        canvas.end_stroke()
        np.testing.assert_allclose(canvas.data[100, 200], (1, 0, 0, 1))  # along the whole line
        np.testing.assert_allclose(canvas.data[100, 320], (0, 0, 0, 0))
        np.testing.assert_allclose(canvas.data[150, 200], (0, 0, 0, 0))
        canvas.stroke_to(200, 100, 6, 0.0, mode="erase")
        canvas.end_stroke()
        np.testing.assert_allclose(canvas.data[100, 200], (0, 0, 0, 0))
        np.testing.assert_allclose(canvas.data[100, 150], (1, 0, 0, 1))
        half = Canvas()
        half.dab(50, 50, 8, 0.0, (0.0, 0.0, 1.0), strength=0.5)
        np.testing.assert_allclose(half.data[50, 50], (0, 0, 0.5, 0.5))  # premultiplied
        np.testing.assert_allclose(half.to_asset()[50, 50], (0, 0, 255, 128))  # straight, for the file

    def test_edges_are_safe(self) -> None:
        canvas = Canvas()
        canvas.dab(-5, -5, 20, 0.5, (1, 1, 1))
        canvas.dab(645, 365, 20, 0.0, (1, 1, 1))  # past the corner, but reaching it
        canvas.dab(-100, 50, 20, 0.5, (1, 1, 1))  # wholly outside: nothing
        self.assertGreater(canvas.data[0, 0, 3], 0.5)
        self.assertGreater(canvas.data[359, 639, 3], 0.5)

    def test_mask_brushes(self) -> None:
        mask = Canvas(mask=True)
        self.assertTrue(mask.is_empty())  # a new mask shows everything
        mask.dab(320, 180, 30, 0.0, mode="hide")
        self.assertEqual(mask.data[180, 320], 0.0)
        mask.dab(320, 180, 10, 0.0, mode="show")
        self.assertEqual(mask.data[180, 320], 1.0)
        self.assertEqual(mask.data[180, 340], 0.0)
        self.assertEqual(mask.to_asset().dtype, np.uint8)

    def test_bucket(self) -> None:
        canvas = Canvas()
        canvas.data[:, 300:310] = (1.0, 1.0, 1.0, 1.0)  # a white wall down the middle
        canvas.bucket(100, 100, (0.0, 1.0, 0.0))
        np.testing.assert_allclose(canvas.data[50, 50], (0, 1, 0, 1))  # the clear area on the left fills...
        np.testing.assert_allclose(canvas.data[50, 500], (0, 0, 0, 0))  # ...but not past the wall
        np.testing.assert_allclose(canvas.data[50, 305], (1, 1, 1, 1))
        canvas.bucket(305, 10, (1.0, 0.0, 0.0))  # clicking the wall fills the wall
        np.testing.assert_allclose(canvas.data[300, 305], (1, 0, 0, 1))

    def test_gradient_and_light_colour(self) -> None:
        canvas = Canvas()
        canvas.gradient(0, 180, 640, 180, (1.0, 0.5, 0.0))
        self.assertGreater(canvas.data[180, 10, 3], 0.95)
        self.assertLess(canvas.data[180, 630, 3], 0.05)
        round_one = Canvas()
        round_one.gradient(320, 180, 420, 180, (1.0, 1.0, 1.0), radial=True)
        self.assertGreater(round_one.data[180, 320, 3], 0.95)
        self.assertLess(round_one.data[180, 425, 3], 0.01)
        self.assertAlmostEqual(float(round_one.data[130, 320, 3]), float(round_one.data[180, 370, 3]), places=2)
        show = create_show(Path(tempfile.mkdtemp()) / "Show")
        sampler = Sampler(show.fixtures, 640, 360)
        patch = sampler.owner == sampler.ids.index("slimpar-106")
        lit = Canvas()
        lit.fill_area(patch, (0.5, 0.0, 1.0))
        colours = dict(zip(sampler.ids, sampler.sample(np.ascontiguousarray(lit.data.transpose(2, 0, 1)))))
        np.testing.assert_allclose(colours["slimpar-106"], (0.5, 0.0, 1.0), atol=1e-3)
        np.testing.assert_allclose(colours["slimpar-107"], (0.0, 0.0, 0.0), atol=1e-3)

    def test_the_biggest_brush_is_quick(self) -> None:
        canvas = Canvas()
        started = time.perf_counter()
        for i in range(30):  # a second's worth of pointer moves at 30 a second
            canvas.stroke_to(100 + i * 15, 180 + (i % 5) * 10, 120, 0.6, (1, 1, 1))
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0 / 2)  # well under the time it takes to draw them (leaves room to show them)


class AssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_files_named_after_their_contents(self) -> None:
        canvas = Canvas()
        canvas.dab(320, 180, 40, 0.5, (1.0, 0.5, 0.25), strength=0.8)
        first_picture = canvas.to_asset()
        first = save_asset(self.folder, PAINT_DIR, first_picture)
        self.assertTrue(first.startswith("paint/") and first.endswith(".png"))
        self.assertEqual(save_asset(self.folder, PAINT_DIR, canvas.to_asset()), first)  # the same picture: the same file
        before = (self.folder / first).stat().st_mtime_ns
        canvas.dab(100, 100, 10, 0.0, (0, 0, 1))
        second = save_asset(self.folder, PAINT_DIR, canvas.to_asset())
        self.assertNotEqual(second, first)
        self.assertEqual((self.folder / first).stat().st_mtime_ns, before)  # never overwritten
        np.testing.assert_array_equal(load_strokes(self.folder / first), first_picture)  # undo can go back to it exactly
        mask = Canvas(mask=True)
        mask.dab(320, 180, 30, 0.0, mode="hide")
        name = save_asset(self.folder, MASKS_DIR, mask.to_asset())
        np.testing.assert_array_equal(load_mask(self.folder / name), mask.to_asset())  # exact

    def test_a_saved_picture_comes_back_exactly(self) -> None:
        canvas = Canvas()
        canvas.stroke_to(50, 50, 20, 0.7, (0.2, 0.6, 1.0), strength=0.6)
        canvas.stroke_to(400, 250, 20, 0.7, (0.2, 0.6, 1.0), strength=0.6)
        straight = canvas.to_asset()
        name = save_asset(self.folder, PAINT_DIR, straight)
        np.testing.assert_array_equal(load_strokes(self.folder / name), straight)  # PNG is lossless
        reopened = Canvas(start=load_strokes(self.folder / name))
        np.testing.assert_array_equal(reopened.to_asset(), straight)  # painting on carries on from exactly there


class PaintedElementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.show = create_show(Path(self._tmp.name) / "Show")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def colours(self, element: dict) -> dict:
        inp = Input(id="painted", name="Painted", tracks=freeze([[element], [], [], []]))
        ctx = RenderContext(CANVAS_W, CANVAS_H)
        ctx.sampler = sampler_for(self.show.fixtures, None)
        ctx.folder = self.show.folder
        canvas = render_input(Instance(inp, 0.0), 0.0, ctx)
        return dict(zip(ctx.sampler.ids, ctx.sampler.sample(canvas)))

    def test_strokes_and_masks_on_the_lights(self) -> None:
        strokes = Canvas()
        strokes.data[:, :320] = (1.0, 0.0, 0.0, 1.0)  # the left half red
        name = save_asset(self.show.folder, PAINT_DIR, strokes.to_asset())
        element = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#0000FF"}, "strokes": name}
        colours = self.colours(element)
        np.testing.assert_allclose(colours["slimpar-105"], (1, 0, 0), atol=0.01)  # left: the strokes
        np.testing.assert_allclose(colours["slimpar-108"], (0, 0, 1), atol=0.01)  # right: the fill
        mask = Canvas(mask=True)
        mask.data[:, 320:] = 0.0  # hide the right half
        mask_name = save_asset(self.show.folder, MASKS_DIR, mask.to_asset())
        masked = dict(element, mask={"paint": mask_name, "exclude": ["slimpar-101"]})
        colours = self.colours(masked)
        np.testing.assert_allclose(colours["slimpar-105"], (1, 0, 0), atol=0.01)
        np.testing.assert_allclose(colours["slimpar-108"], (0, 0, 0), atol=0.01)  # painted out
        np.testing.assert_allclose(colours["slimpar-101"], (0, 0, 0), atol=0.01)  # unticked
        missing = dict(element, strokes="paint/nothing-here.png", mask={"paint": "masks/nothing.png"})
        np.testing.assert_allclose(self.colours(missing)["slimpar-105"], (0, 0, 1), atol=0.01)  # missing files: as if none

    def test_they_survive_a_reload(self) -> None:
        strokes = Canvas()
        strokes.dab(320, 180, 60, 0.5, (0.0, 1.0, 0.0))
        name = save_asset(self.show.folder, PAINT_DIR, strokes.to_asset())
        mask = Canvas(mask=True)
        mask.dab(100, 100, 40, 0.0, mode="hide")
        mask_name = save_asset(self.show.folder, MASKS_DIR, mask.to_asset())
        show = self.show.with_inputs({**self.show.inputs, "painted": Input(id="painted", name="Painted", tracks=freeze([
            [{"id": "E1", "kind": "paint", "fill": {"type": "none"}}], [], [], []]))}, self.show.input_order + ("painted",))
        show = set_element_value(show, "painted", "E1", "strokes", name)
        show = set_element_value(show, "painted", "E1", "mask.paint", mask_name)
        save_whole_show(show)
        again = load_show(Path(self.show.folder))
        element = again.inputs["painted"].tracks[0][0]
        self.assertEqual(element["strokes"], name)
        self.assertEqual(element["mask"]["paint"], mask_name)
        np.testing.assert_array_equal(load_strokes(Path(again.folder) / name), strokes.to_asset())


if __name__ == "__main__":
    unittest.main()
