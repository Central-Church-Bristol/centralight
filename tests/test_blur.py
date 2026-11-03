"""Blur on picture elements (NDI, Image, later Video): how much of the picture around each light that light
averages. Every light follows the same-sized patch; Blur is how you get calmer, smoother colours."""

from __future__ import annotations

import unittest

import numpy as np

from centralight.bus import Instance, render_input
from centralight.compositor import blur
from centralight.elements import RenderContext, element_is_dynamic
from centralight.model import Input
from centralight.new_show import show_from_patch
from centralight.sampler import CANVAS_H, CANVAS_W, Sampler, sampler_for
from centralight.schema import element_specs
from centralight.show_io import freeze
from tests.fakes import StubSources
from tests.gui_fixtures import rainbow_canvas
from tests.test_follow_screen import split_picture


class BlurPictureTests(unittest.TestCase):
    def test_none_leaves_it_alone(self) -> None:
        layer = rainbow_canvas()
        self.assertIs(blur(layer, 0.0), layer)

    def test_full_is_the_whole_pictures_average(self) -> None:
        layer = rainbow_canvas()
        out = blur(layer, 1.0)
        mean = layer.reshape(4, -1).mean(axis=1)
        for y, x in ((0, 0), (45, 80), (89, 159)):
            np.testing.assert_allclose(out[:, y, x], mean, rtol=1e-5)

    def test_more_blur_is_calmer(self) -> None:
        layer = rainbow_canvas()
        spread = [float(blur(layer, amount)[:3].std(axis=(1, 2)).mean()) for amount in (0.0, 0.2, 0.4, 0.6, 0.8, 0.95, 1.0)]
        self.assertEqual(spread, sorted(spread, reverse=True))
        self.assertLess(spread[-1], 1e-6)

    def test_no_jump_on_the_way_to_full(self) -> None:
        layer = rainbow_canvas()
        steps = [blur(layer, amount) for amount in np.linspace(0.0, 1.0, 41)]
        biggest = max(float(np.abs(b - a).max()) for a, b in zip(steps, steps[1:]))
        self.assertLess(biggest, 0.12)

    def test_it_looks_the_same_at_any_size(self) -> None:
        """The Editor draws at 640x360 and the lights at 160x90: the same blur gives the lights the same colours."""
        fixtures = show_from_patch().fixtures
        small, big = Sampler(fixtures, 160, 90), Sampler(fixtures, 640, 360)
        for amount in (0.3, 0.7):
            a = small.sample(blur(rainbow_canvas(160, 90), amount))
            b = big.sample(blur(rainbow_canvas(640, 360), amount))
            np.testing.assert_allclose(a, b, atol=0.05, err_msg=f"blur {amount}")

    def test_transparent_parts_stay_transparent_at_the_edges(self) -> None:
        layer = np.zeros((4, 90, 160), dtype=np.float32)  # all clear
        np.testing.assert_array_equal(blur(layer, 0.5), layer)


class BlurElementTests(unittest.TestCase):
    """Follow screen's picture: the left half red, the right half blue."""

    def setUp(self) -> None:
        self.show = show_from_patch()
        self.sources = StubSources()
        self.sources.put("Media Background Pane", split_picture())

    def colours(self, *tracks) -> dict:
        inp = Input(id="look", name="Look", tracks=freeze([list(t) for t in tracks] + [[]] * (4 - len(tracks))))
        ctx = RenderContext(CANVAS_W, CANVAS_H, self.sources)
        ctx.sampler = sampler_for(self.show.fixtures, None)
        canvas = render_input(Instance(inp, 0.0), 0.5, ctx)
        return dict(zip(ctx.sampler.ids, ctx.sampler.sample(canvas)))

    def screen(self, blur_amount=None) -> dict:
        element = {"id": "E1", "kind": "ndi", "source": "Media Background Pane"}
        if blur_amount is not None:
            element["blur"] = blur_amount
        return element

    def test_the_setting_is_there_for_pictures_only(self) -> None:
        for kind in ("ndi", "image", "video"):
            self.assertIn("blur", [s.path for s in element_specs(kind)], kind)
        self.assertNotIn("blur", [s.path for s in element_specs("paint")])

    def test_follow_screen_calms_down_with_blur(self) -> None:
        sharp = self.colours([self.screen()])
        self.assertGreater(sharp["slimpar-105"][0], 0.95)  # on the left: red
        self.assertLess(sharp["slimpar-105"][2], 0.05)
        half = self.colours([self.screen(0.5)])
        self.assertGreater(half["slimpar-102"][2], 0.15)  # left of the middle: it takes in blue from the right...
        self.assertGreater(half["slimpar-102"][0], half["slimpar-102"][2])  # ...but it's still mostly red
        self.assertLess(half["slimpar-105"][2], half["slimpar-102"][2])  # the far left takes in less
        full = self.colours([self.screen(1.0)])
        for light_id, rgb in full.items():
            np.testing.assert_allclose(rgb, (0.5, 0.0, 0.5), atol=0.02, err_msg=light_id)  # all the average

    def test_it_only_softens_its_own_element(self) -> None:
        shape = {"id": "E2", "kind": "paint", "fill": {"type": "none"},
                 "shapes": [{"id": "S1", "type": "rect", "x": 0.2, "y": 0.2, "w": 0.3, "h": 0.5, "colour": "#FFFFFF"}]}
        colours = self.colours([self.screen(1.0)], [shape])
        np.testing.assert_allclose(colours["slimpar-101"], (1.0, 1.0, 1.0), atol=0.01)  # the sharp white shape
        np.testing.assert_allclose(colours["slimpar-104"], (0.5, 0.0, 0.5), atol=0.02)  # the blurred screen

    def test_it_can_change_over_time(self) -> None:
        element = self.screen({"keys": [[0, 0.0], [2, 1.0]]})
        self.assertTrue(element_is_dynamic(element))
        self.assertTrue(element_is_dynamic(self.screen({"expr": "wave(time, 0.1)"})))


if __name__ == "__main__":
    unittest.main()
