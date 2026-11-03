"""M10: each light's colour worked out at its dot (Paint), or over its patch of a picture (Image, Video, NDI:
the element's Area), then Colour boost and Smoothing; layers, masks and transitions per light."""

from __future__ import annotations

import colorsys
import time
import unittest

import numpy as np

from centralight.bus import Instance, render_input
from centralight.compositor import blend
from centralight.elements import RenderContext, element_is_dynamic
from centralight.light_elements import AreaTable, colour_boost, light_picture, patch_boxes, shape_cover
from centralight.lights import ASPECT, DEFAULT_AREA, IDENTIFY_PALETTE, area_half_size, identify_colours, lights_for
from centralight.model import Fixture, Input, MapSpot, Transition
from centralight.new_show import show_from_patch
from centralight.schema import element_specs
from centralight.show_io import freeze, input_from_dict, input_to_dict
from centralight.transitions import KINDS, mix
from tests.fakes import StubSources
from tests.test_follow_screen import split_picture


def light(fid: str, x: float, y: float) -> Fixture:
    return Fixture(fid, fid, "slimpar9", 5, map=MapSpot(x, y))


def smooth_picture(seed: int, cells: tuple[int, int] = (16, 9)) -> np.ndarray:
    """A soft random picture (RGB uint8, 320x180), like a worship background."""
    import cv2

    rng = np.random.default_rng(seed)
    small = rng.random((cells[1], cells[0], 3)).astype(np.float32)
    return (np.clip(cv2.resize(small, (320, 180), interpolation=cv2.INTER_CUBIC), 0, 1) * 255).astype(np.uint8)


def lights_ctx(fixtures, sources=None) -> RenderContext:
    return RenderContext(0, 0, sources, lights=lights_for(fixtures), per_light=True)


def old_follow_screen(image: np.ndarray, lights) -> np.ndarray:
    """How each light read the picture before M10 (for comparison): the picture shrunk to 160x90, then each light's
    circle of radius 0.08 screen widths, nearest light first, weighted 1 - 0.75 d² towards the middle. -> (3, n)."""
    import cv2

    w, h = 160, 90
    picture = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA).astype(np.float64) / 255.0
    xs = (np.arange(w) + 0.5) / w
    ys = (np.arange(h) + 0.5) / h
    distance = np.stack([np.sqrt((xs[None, :] - x) ** 2 + ((ys[:, None] - y) * h / w) ** 2) / 0.08 for x, y in zip(lights.x, lights.y)])
    nearest = np.argmin(distance, axis=0)
    out = np.zeros((3, lights.count))
    for i in range(lights.count):
        weights = np.where((nearest == i) & (distance[i] <= 1.0), 1.0 - 0.75 * distance[i] ** 2, 0.0)
        out[:, i] = (picture * weights[..., None]).sum(axis=(0, 1)) / weights.sum()
    return out


class AreaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.show = show_from_patch()
        self.ctx = lights_ctx(self.show.fixtures)

    def read(self, image, **settings) -> np.ndarray:
        return light_picture({"kind": "ndi", **settings}, self.ctx, AreaTable(image))

    def test_zero_is_the_colour_under_the_dot(self) -> None:
        image = np.zeros((180, 320, 3), dtype=np.uint8)
        image[:, :, 0] = np.arange(320, dtype=np.uint8)[None, :] * 0 + (np.arange(320) * 255 // 319).astype(np.uint8)
        layer = self.read(image, area=0.0)
        for i, x in enumerate(self.ctx.lights.x):  # between pixels: read between them (here a smooth ramp)
            px = x * 320 - 0.5
            left = int(np.floor(px))
            expected = (image[0, left, 0] * (1 - (px - left)) + image[0, left + 1, 0] * (px - left)) / 255.0
            self.assertAlmostEqual(layer[0, i], expected, places=6)
        self.assertTrue(np.allclose(layer[3], 1.0))

    def test_full_is_exactly_the_whole_pictures_average_for_every_light(self) -> None:
        image = smooth_picture(1)
        layer = self.read(image, area=1.0)
        np.testing.assert_allclose(layer[:3], np.repeat((image.reshape(-1, 3).mean(axis=0) / 255.0)[:, None], 8, axis=1), atol=1e-9)

    def test_the_default_is_close_to_follow_screen_before(self) -> None:
        self.assertEqual(DEFAULT_AREA, 0.25)
        differences = [np.abs(self.read(image)[:3] - old_follow_screen(image, self.ctx.lights)).mean()
                       for image in (smooth_picture(seed, cells) for seed in range(3) for cells in ((8, 5), (16, 9), (32, 18)))]
        self.assertLess(max(differences) * 255, 4.0)  # under 4 of 255 steps for any picture
        self.assertLess(float(np.mean(differences)) * 255, 2.0)

    def test_it_grows_evenly_and_costs_the_same_at_any_area(self) -> None:
        sizes = [area_half_size(a / 10) for a in range(11)]
        self.assertEqual(sizes[0], 0.0)
        self.assertEqual(sizes[-1], 1.0)
        self.assertTrue(all(b > a for a, b in zip(sizes, sizes[1:])))
        table = AreaTable(smooth_picture(2))

        def cost(area):
            started = time.perf_counter()
            for step in range(200):
                light_picture({"kind": "ndi", "area": area, "transform": {"x": 0.5 + step * 1e-6}}, self.ctx, table)  # (no shortcut)
            return time.perf_counter() - started

        cost(0.5)
        small, large = min(cost(0.02) for _ in range(3)), min(cost(1.0) for _ in range(3))
        self.assertLess(large, small * 1.5)

    def test_transforms_and_patches_partly_off_the_picture(self) -> None:
        image = split_picture()  # left half red, right half blue
        moved = self.read(image, area=0.0, transform={"rotation": 180.0})  # turned round: blue on the left
        self.assertGreater(moved[2, 4], 0.99)  # slimpar-105, on the left
        self.assertGreater(moved[0, 7], 0.99)  # slimpar-108, on the right
        small = self.read(image, area=0.0, transform={"scale": 0.8})  # 80 % in the middle (0.1-0.9): the edges are empty
        self.assertEqual(small[3, 4], 0.0)  # slimpar-105 (x 0.07) is off the picture: transparent
        self.assertEqual(small[3, 1], 1.0)  # slimpar-102 (0.4, 0.2) is on it...
        self.assertGreater(small[0, 1], 0.99)  # ...on its red half (0.4 is 0.375 of the way across the picture)
        right = self.read(image, area=0.0, transform={"x": 0.75})  # moved right by a quarter
        self.assertGreater(right[2, 3], 0.99)  # slimpar-104 (x 0.8) sees the picture's x 0.55: its blue half
        self.assertGreater(right[0, 1], 0.99)  # slimpar-102 (x 0.4) sees its x 0.15: red
        self.assertEqual(right[3, 4], 0.0)  # slimpar-105 (x 0.07): left of the moved picture
        tall = np.zeros((180, 135, 3), dtype=np.uint8)  # a 3:4 picture, fitted: empty bars at the sides
        tall[:] = (0, 255, 0)
        fitted = self.read(tall, area=0.0, transform={"fit": "fit"})
        self.assertEqual(fitted[3, 4], 0.0)  # slimpar-105 is in the bar at the left
        self.assertAlmostEqual(fitted[1, 1], 1.0)  # slimpar-102 sees the picture
        edge = self.read(image, area=0.5)  # slimpar-105 (x 0.07): its patch reaches past the screen's left edge
        x0, y0, x1, y1 = patch_boxes(self.ctx.lights.x, self.ctx.lights.y, 0.5)
        self.assertEqual(x0[4], 0.0)  # cut to the screen...
        self.assertAlmostEqual(edge[3, 4], 1.0)  # ...so it isn't darkened by what's past the edge
        red_share = (min(x1[4], 0.5) - x0[4]) / (x1[4] - x0[4])
        self.assertAlmostEqual(edge[0, 4], red_share, places=6)

    def test_two_picture_elements_can_have_different_areas(self) -> None:
        sources = StubSources()
        sources.put("Media Background Pane", split_picture())
        ctx = lights_ctx(self.show.fixtures, sources)
        calm = {"id": "E1", "kind": "ndi", "area": 1.0}  # every light the average: purple
        sharp = {"id": "E2", "kind": "ndi", "area": 0.0, "mask": {"exclude": ["slimpar-101", "slimpar-104"]}}
        inp = Input(id="two", name="Two", tracks=freeze([[calm], [sharp], [], []]))
        colours = ctx.lights.colours(render_input(Instance(inp, 0.0), 0.5, ctx))
        np.testing.assert_allclose(colours["slimpar-101"], (0.5, 0.0, 0.5), atol=0.01)  # left alone by E2: E1's average
        np.testing.assert_allclose(colours["slimpar-104"], (0.5, 0.0, 0.5), atol=0.01)
        np.testing.assert_allclose(colours["slimpar-102"], (1.0, 0.0, 0.0), atol=0.01)  # E2's own sharp colours
        np.testing.assert_allclose(colours["slimpar-103"], (0.0, 0.0, 1.0), atol=0.01)

    def test_it_can_change_over_time(self) -> None:
        self.assertTrue(element_is_dynamic({"kind": "image", "area": {"keys": [[0, 0.0], [2, 1.0]]}}))
        self.assertTrue(element_is_dynamic({"kind": "image", "boost": {"expr": "audio.level"}}))


class PaintTests(unittest.TestCase):
    def test_a_crisp_edge_is_in_or_out_and_softness_fades(self) -> None:
        u = np.array([0.449, 0.451, 0.5])
        v = np.array([0.5, 0.5, 0.5])
        bar = {"type": "rect", "x": 0.55, "y": 0.5, "w": 0.2, "h": 1.0}  # its left edge at x 0.45
        cover, _colour = shape_cover(bar, 0.0, u, v)
        self.assertEqual(list(cover), [0.0, 1.0, 1.0])
        soft, _colour = shape_cover({**bar, "softness": 0.5}, 0.0, u, v)
        self.assertTrue(0.0 < soft[0] < 0.5 < soft[1] < 1.0)  # fading across the edge
        self.assertEqual(soft[2], 1.0)

    def test_fill_and_gradients_at_the_dots(self) -> None:
        show = show_from_patch()
        ctx = lights_ctx(show.fixtures)
        element = {"id": "E1", "kind": "paint", "fill": {"type": "linear", "from": "#000000", "to": "#FFFFFF", "angle": 0}}
        inp = Input(id="g", name="G", tracks=freeze([[element], [], [], []]))
        layer = render_input(Instance(inp, 0.0), 0.0, ctx)
        np.testing.assert_allclose(layer[0], ctx.lights.x, atol=1e-6)  # left (black) to right (white), at each dot

    def test_new_shapes_get_a_little_softness(self) -> None:
        from centralight.input_edit import NEW_SHAPE_SOFTNESS, add_shape, find_element

        paint = {"id": "E1", "kind": "paint", "fill": {"type": "none"}}
        show = show_from_patch().with_inputs({"bar": Input(id="bar", name="Bar", tracks=freeze([[paint], [], [], []]))}, ("bar",))
        show, shape_id = add_shape(show, "bar", "E1", "rect")
        shape = find_element(show.inputs["bar"], "E1")[2]["shapes"][0]
        self.assertEqual(shape["softness"], NEW_SHAPE_SOFTNESS)
        self.assertTrue(0.0 < NEW_SHAPE_SOFTNESS <= 0.3)  # small


class PerLightLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.show = show_from_patch()
        self.ctx = lights_ctx(self.show.fixtures)

    def look(self, *tracks, t: float = 0.0, transition=None):
        inp = Input(id="look", name="Look", tracks=freeze([list(track) for track in tracks] + [[]] * (4 - len(tracks))))
        return render_input(Instance(inp, 0.0), t, self.ctx)

    def test_blend_formulas_work_per_light_as_per_pixel(self) -> None:
        rng = np.random.default_rng(4)
        below, above = rng.random((4, 8)).astype(np.float32), rng.random((4, 8)).astype(np.float32)
        below[:3] *= below[3]
        above[:3] *= above[3]
        for mode in ("normal", "add", "multiply", "screen"):
            np.testing.assert_allclose(blend(below, above, mode), blend(below[:, None, :], above[:, None, :], mode)[:, 0, :], err_msg=mode)

    def test_opacity_painted_masks_and_lights_left_alone(self) -> None:
        red = {"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FF0000"}, "opacity": 0.5,
               "mask": {"exclude": ["slimpar-103"]}}
        layer = self.look([red])
        self.assertAlmostEqual(float(layer[0, 0]), 0.5, places=6)  # half see-through
        self.assertAlmostEqual(float(layer[3, 0]), 0.5, places=6)
        self.assertEqual(float(layer[3, 2]), 0.0)  # slimpar-103 is left alone

    def test_a_painted_mask_is_read_at_each_dot(self) -> None:
        import tempfile
        from pathlib import Path

        from centralight.assets import save_asset

        folder = Path(tempfile.mkdtemp(prefix="per-light-"))
        grey = np.full((360, 640), 255, dtype=np.uint8)
        grey[:, :320] = 0  # the left half hidden
        name = save_asset(folder, "masks", grey)
        self.ctx.folder = str(folder)
        layer = self.look([{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FFFFFF"}, "mask": {"paint": name}}])
        self.assertEqual(float(layer[3, 0]), 0.0)  # slimpar-101 (x 0.2): hidden
        self.assertEqual(float(layer[3, 3]), 1.0)  # slimpar-104 (x 0.8): shown

    def test_every_transition_works_per_light(self) -> None:
        old = np.zeros((4, 8), dtype=np.float32)
        old[0] = old[3] = 1.0  # red
        new = np.zeros((4, 8), dtype=np.float32)
        new[2] = new[3] = 1.0  # blue
        points = (self.ctx.lights.x, self.ctx.lights.y)
        for kind in KINDS:
            start = mix(kind, old, new, 0.0, owner=self.ctx.lights.indices, seed=3, points=points)
            end = mix(kind, old, new, 1.0, owner=self.ctx.lights.indices, seed=3, points=points)
            np.testing.assert_array_equal(start if kind != "cut" else old, old, err_msg=kind)
            np.testing.assert_array_equal(end, new, err_msg=kind)
        wipe = mix("wipe", old, new, 0.5, softness=0.1, points=points)  # left to right: the left has changed first
        self.assertGreater(wipe[2, 4], 0.99)  # slimpar-105, far left: blue already
        self.assertLess(wipe[2, 7], 0.01)  # slimpar-108, far right: still red
        iris = mix("iris", old, new, 0.4, softness=0.1, points=points)  # from the middle out
        self.assertGreater(iris[2, 1], iris[2, 4])  # slimpar-102 (near the middle) before slimpar-105 (the edge)
        random = mix("random", old, new, 0.5, owner=self.ctx.lights.indices, seed=11, points=points)
        self.assertGreater(len({round(float(v), 3) for v in random[2]}), 2)  # each light at its own moment
        np.testing.assert_array_equal(random, mix("random", old, new, 0.5, owner=self.ctx.lights.indices, seed=11, points=points))

    def test_a_sent_input_transitions_per_light_on_the_engine(self) -> None:
        from centralight.commands import SendToBase
        from centralight.engine import Engine
        from centralight.model import Transition
        from tests.fakes import FakeClock, FakeOutput

        red = Input(id="red", name="Red", tracks=freeze([[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FF0000"}}], [], [], []]))
        blue = Input(id="blue", name="Blue", transition=Transition("wipe", 2.0, softness=0.05),
                     tracks=freeze([[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#0000FF"}}], [], [], []]))
        show = self.show.with_inputs({"red": red, "blue": blue}, ("red", "blue"))
        clock = FakeClock()
        output = FakeOutput()
        engine = Engine(show, output, None, initial_base="red", clock=clock)
        engine.tick()
        engine.submit(SendToBase("blue"))
        engine.tick()
        clock.advance(1.0)
        engine.tick()  # half way: the left side is blue, the right side still red
        colours = engine._lights.colours(engine._render(clock()))
        self.assertGreater(colours["slimpar-105"][2], 0.99)
        self.assertGreater(colours["slimpar-108"][0], 0.99)


class SmoothingTests(unittest.TestCase):
    """A feed on Base: the lights' targets come from its picture; time is the engine's clock."""

    def setUp(self) -> None:
        self.show = show_from_patch()
        self.sources = StubSources()
        self.ctx = lights_ctx(self.show.fixtures, self.sources)
        self.memory: dict = {}

    def element(self, **smooth):
        element = {"id": "E1", "kind": "ndi", "area": 1.0}  # every light the whole picture's average: easy to follow
        if smooth:
            element["smooth"] = smooth
        return element

    def at(self, element, now: float, grey: int) -> float:
        self.sources.put("Media Background Pane", np.full((180, 320, 3), grey, dtype=np.uint8))
        inp = Input(id="feed", name="Feed", tracks=freeze([[element], [], [], []]))
        layer = render_input(Instance(inp, 0.0, self.memory), now, self.ctx)
        return float(layer[0, 0])

    def test_brighter_and_darker_times(self) -> None:
        element = self.element(up=0.3, down=0.6, snap=False)
        self.assertEqual(self.at(element, 0.0, 0), 0.0)  # starts at the first colour: no fade up from black
        value = 0.0
        for step in range(1, 13):  # 0.3 s at 40 a second, the picture now white
            value = self.at(element, step / 40, 255)
        self.assertAlmostEqual(value, 1.0 - np.exp(-1.0), places=2)  # 63 % of the way after its brighter time
        for step in range(13, 13 + 24):  # 0.6 s back to black
            value = self.at(element, step / 40, 0)
        self.assertAlmostEqual(value, (1.0 - np.exp(-1.0)) * np.exp(-1.0), places=2)  # its darker time: 63 % down

    def test_the_same_at_20_40_and_60_a_second(self) -> None:
        results = []
        for rate in (20, 40, 60):
            self.memory = {}
            element = self.element(up=0.5, down=0.5, snap=False)
            self.at(element, 0.0, 0)
            value = 0.0
            for step in range(1, rate + 1):  # one second of a white picture
                value = self.at(element, step / rate, 255)
            results.append(value)
        np.testing.assert_allclose(results, [1.0 - np.exp(-2.0)] * 3, atol=1e-6)

    def test_snap_on_cuts(self) -> None:
        snapping = self.element(up=1.0, down=1.0)  # Snap on cuts is on unless it's switched off
        self.at(snapping, 0.0, 0)
        self.assertAlmostEqual(self.at(snapping, 0.025, 255), 1.0)  # the whole picture changed at once: it jumps
        self.memory = {}
        gliding = self.element(up=1.0, down=1.0, snap=False)
        self.at(gliding, 0.0, 0)
        self.assertLess(self.at(gliding, 0.025, 255), 0.1)  # without it: it glides

    def test_off_is_the_same_as_none_and_images_arent_smoothed(self) -> None:
        self.at(self.element(), 0.0, 0)
        self.assertEqual(self.at(self.element(), 0.025, 255), 1.0)
        self.memory = {}
        off = self.element(up=0.0, down=0.0)
        self.at(off, 0.0, 0)
        self.assertEqual(self.at(off, 0.025, 255), 1.0)
        self.assertNotIn("feed/E1", self.memory.get("smoothing", {}))  # nothing remembered either
        for kind, smoothed in (("image", False), ("video", True), ("ndi", True), ("paint", False)):
            self.assertEqual("smooth.up" in [s.path for s in element_specs(kind)], smoothed, kind)


class ColourBoostTests(unittest.TestCase):
    def test_it_keeps_the_hue_and_stays_in_range(self) -> None:
        rng = np.random.default_rng(5)
        layer = rng.random((4, 64))
        layer[3] = rng.uniform(0.2, 1.0, 64)
        layer[:3] *= layer[3]
        for amount in (0.25, 0.5, 1.0):
            boosted = colour_boost(layer, amount)
            self.assertTrue(((boosted[:3] >= -1e-12) & (boosted[:3] <= boosted[3] + 1e-12)).all())
            np.testing.assert_array_equal(boosted[3], layer[3])
            for i in range(64):
                before = colorsys.rgb_to_hsv(*(layer[:3, i] / layer[3, i]))
                after = colorsys.rgb_to_hsv(*(boosted[:3, i] / boosted[3, i]))
                if before[1] > 0.01:
                    self.assertAlmostEqual(before[0], after[0], places=6)  # the same hue
                    self.assertGreaterEqual(after[1] + 1e-9, before[1])  # more saturated (or as much)
                    self.assertAlmostEqual(before[2], after[2], places=6)  # as bright
        grey = np.array([[0.4], [0.4], [0.4], [1.0]])
        np.testing.assert_array_equal(colour_boost(grey, 1.0), grey)  # grey stays grey
        np.testing.assert_array_equal(colour_boost(layer, 0.0), layer)  # 0 = as it was


class OldFilesTests(unittest.TestCase):
    def test_blur_from_before_is_ignored_with_a_warning(self) -> None:
        data = {"format": "centralight-input", "version": 1, "id": "old", "name": "Old",
                "tracks": [[{"id": "E1", "kind": "ndi", "source": "Media Background Pane", "blur": 0.4}], [], [], []]}
        inp, warnings = input_from_dict(data, expected_id="old")
        self.assertNotIn("blur", inp.tracks[0][0])
        self.assertTrue(any('"blur"' in w for w in warnings))
        self.assertNotIn("blur", str(input_to_dict(inp)))  # and the next save leaves it out
        for kind in ("ndi", "image", "video", "paint"):
            self.assertNotIn("blur", [s.path for s in element_specs(kind)], kind)


class LightSetTests(unittest.TestCase):
    def test_lights_are_points_in_the_shows_order(self) -> None:
        lights = lights_for([light("a", 0.1, 0.2), light("b", 0.9, 0.8)])
        self.assertEqual(lights.ids, ("a", "b"))
        np.testing.assert_array_equal(lights.x, [0.1, 0.9])
        self.assertIs(lights_for([light("a", 0.1, 0.2), light("b", 0.9, 0.8)], lights), lights)  # nothing moved: reused
        self.assertIsNot(lights_for([light("a", 0.1, 0.25), light("b", 0.9, 0.8)], lights), lights)
        nearest = lights.nearest_map(16, 9)
        self.assertEqual(int(nearest[0, 0]), 0)
        self.assertEqual(int(nearest[8, 15]), 1)
        self.assertAlmostEqual(ASPECT, 9 / 16)

    def test_identify_palette_is_twelve_different_colours_and_stable(self) -> None:
        self.assertEqual(len({rgb for _name, rgb in IDENTIFY_PALETTE}), 12)
        ids = [f"slimpar-10{i}" for i in range(1, 9)]
        first = identify_colours(ids, 7)
        self.assertEqual(first, identify_colours(ids, 7))
        self.assertEqual(len({rgb for _name, rgb in first.values()}), 8)
        self.assertNotEqual(first, identify_colours(ids, 8))


class LightsFirstTests(unittest.TestCase):
    """A heavy picture for people must never hold up the lights."""

    def engine(self):
        from centralight.engine import Engine
        from centralight.new_show import FOLLOW_SCREEN_ID
        from tests.fakes import FakeClock, FakeOutput

        sources = StubSources()
        sources.put("Media Background Pane", split_picture())
        self.clock = FakeClock()
        from centralight.new_show import follow_screen_input

        show = show_from_patch().with_inputs({FOLLOW_SCREEN_ID: follow_screen_input()}, (FOLLOW_SCREEN_ID,))
        return Engine(show, FakeOutput(), None, sources=sources, initial_base=FOLLOW_SCREEN_ID, clock=self.clock)

    def test_the_live_picture_is_drawn_only_when_wanted_and_waits_while_the_engine_is_late(self) -> None:
        engine = self.engine()
        engine.tick()
        self.assertIsNone(engine.frames.get().preview)  # nobody's looking: no picture
        engine.set_preview_wanted(True)
        self.clock.advance(0.25)
        engine.tick()
        seq = engine.frames.get().preview_seq
        self.assertIsNotNone(engine.frames.get().preview)
        engine._late_until = time.perf_counter() + 10.0  # a tick just started late
        for _ in range(3):
            self.clock.advance(0.25)
            engine.tick()
        self.assertEqual(engine.frames.get().preview_seq, seq)  # the picture waited; the lights carried on
        self.clock.advance(0.3)
        engine.tick()
        self.assertEqual(engine.frames.get().preview_seq, seq + 1)  # ...it slows to once a second, it doesn't stop
        self.assertTrue(engine.is_late())
        engine.set_preview_wanted(False)
        self.assertIsNone(engine._preview)

    def test_the_editor_draws_less_while_the_engine_is_late(self) -> None:
        from centralight.editor_render import LATE_REFRESH, LIVE_REFRESH, EditorRenderer

        self.assertGreaterEqual(LATE_REFRESH, 2 * LIVE_REFRESH)
        self.assertTrue(EditorRenderer(None, late=lambda: True)._engine_late())
        self.assertFalse(EditorRenderer(None, late=lambda: False)._engine_late())
        self.assertFalse(EditorRenderer(None)._engine_late())

    def test_tick_timings_are_kept(self) -> None:
        from centralight.engine import Engine
        from centralight.ui.diagnostics import report
        from tests.fakes import FakeOutput

        engine = Engine(show_from_patch(), FakeOutput(), None)
        engine.start()
        try:
            time.sleep(0.4)
            timing = engine.tick_timing()
        finally:
            engine.stop()
        self.assertGreater(timing["ticks"], 5)
        self.assertGreaterEqual(timing["late_p99_ms"], timing["late_median_ms"])
        self.assertGreaterEqual(timing["work_worst_ms"], timing["work_median_ms"])
        engine.reset_timing()
        self.assertEqual(engine.tick_timing(), {})
        lines = dict(report(engine.frames.get(), timing=timing))
        self.assertIn("over 5 ms late", lines["On time"])  # Diagnostics shows it

    def test_the_window_wants_the_live_picture_only_while_the_dashboard_shows(self) -> None:
        import tempfile
        from pathlib import Path

        from centralight.settings import Settings
        from centralight.ui.main_window import MainWindow
        from tests.fakes import FakeEngine
        from tests.gui_fixtures import with_inputs
        from tests.qt_support import process, qt_app

        app = qt_app()
        folder = tempfile.TemporaryDirectory()
        engine = FakeEngine(with_inputs(show_from_patch()))
        window = MainWindow(Settings(), engine.show, engine, engine.fake_output, save_files=False,
                            notes_path=Path(folder.name) / "notes.csv")
        try:
            window.resize(1366, 768)
            window.show()
            window.show_page("dashboard")
            process(app)
            self.assertTrue(engine._preview_wanted)
            window.show_page("editor")
            self.assertFalse(engine._preview_wanted)  # the Editor draws its own pictures
            window.show_page("dashboard")
            self.assertTrue(engine._preview_wanted)
            window.hide()  # into the tray
            process(app)
            self.assertFalse(engine._preview_wanted)
        finally:
            window._shut_down = True
            window.editor_page.shutdown()
            window.document.close()
            window.close()
            window.deleteLater()
            process(app)
            folder.cleanup()


class QuickTableTests(unittest.TestCase):
    """Every NDI and video frame is shrunk for the lights: by halving, OpenCV's quickest way (it was most of the CPU
    the app used after M10). A still picture keeps the one-step shrink, with more detail."""

    def test_moving_pictures_are_halved_and_average_the_same(self) -> None:
        from centralight.light_elements import _frame_table

        frame = (np.random.default_rng(5).random((540, 960, 3)) * 255).astype(np.uint8)
        quick, still = AreaTable(frame, quick=True), AreaTable(frame)
        self.assertEqual((quick.width, quick.height), (240, 135))
        self.assertEqual((still.width, still.height), (320, 180))
        ctx = lights_ctx([light("a", 0.3, 0.4), light("b", 0.8, 0.7), light("c", 0.05, 0.1)])
        for area in (0.25, 0.5, 1.0):
            np.testing.assert_allclose(light_picture({"kind": "ndi", "area": area}, ctx, quick),
                                       light_picture({"kind": "ndi", "area": area}, ctx, still), atol=1.5 / 255)
        sources = StubSources()
        sources.put("Media Background Pane", frame)
        table = _frame_table({"kind": "ndi", "source": "Media Background Pane"}, lights_ctx([light("a", 0.5, 0.5)], sources))
        self.assertEqual(table.width, 240)  # (the NDI frame took the quick way)


class M10ReviewTests(unittest.TestCase):
    """What a review of M10 found."""

    def test_a_fill_pictures_cropped_strips_stay_hidden_when_it_moves(self) -> None:
        image = np.zeros((240, 320, 3), dtype=np.uint8)  # 4:3: Fill crops a strip off the top and the bottom
        image[:30] = (255, 0, 0)  # red, cropped away
        image[-30:] = (0, 0, 255)  # blue, cropped away
        image[30:-30] = (0, 255, 0)
        ctx = lights_ctx([light("top", 0.5, 0.21), light("bottom", 0.5, 0.79), light("middle", 0.5, 0.5)])
        small = light_picture({"kind": "image", "area": 0.0, "transform": {"scale": 0.5}}, ctx, AreaTable(image))
        np.testing.assert_allclose(small[:, 0], 0.0)  # (0.5, 0.21) is above the shrunk picture: nothing, not red
        np.testing.assert_allclose(small[:, 1], 0.0)  # and below it: nothing, not blue
        np.testing.assert_allclose(small[:3, 2], (0.0, 1.0, 0.0))
        moved = light_picture({"kind": "image", "area": 0.0, "transform": {"y": 0.62}}, ctx, AreaTable(image))
        self.assertEqual(float(moved[0, 0]), 0.0)  # moved down: no red strip shows at the top either

    def test_the_editors_dots_follow_changes_while_paused_with_smoothing_on(self) -> None:
        from centralight.editor_render import EditorRenderer, EditorRequest
        from centralight.input_edit import set_element_value
        from centralight.new_show import FOLLOW_SCREEN_ID, follow_screen_input

        sources = StubSources()
        sources.put("Media Background Pane", np.full((180, 320, 3), (255, 0, 0), dtype=np.uint8))
        inp = follow_screen_input()
        show = show_from_patch().with_inputs({FOLLOW_SCREEN_ID: inp}, (FOLLOW_SCREEN_ID,))
        show = set_element_value(show, FOLLOW_SCREEN_ID, "E1", "smooth", {"up": 1.0, "down": 1.0, "snap": False})
        renderer = EditorRenderer(sources)
        renderer.render_now(EditorRequest(show, FOLLOW_SCREEN_ID, None, 5.0))
        sources.put("Media Background Pane", np.full((180, 320, 3), (0, 0, 255), dtype=np.uint8))
        picture = renderer.render_now(EditorRequest(show, FOLLOW_SCREEN_ID, None, 5.0))  # paused: the same playhead
        np.testing.assert_allclose(picture.colours["slimpar-101"], (0.0, 0.0, 1.0), atol=1e-6)  # it shows, not stuck

    def test_the_editor_draws_less_while_the_engine_is_late_even_while_playing(self) -> None:
        from centralight.editor_render import EditorRenderer
        from centralight.new_show import FOLLOW_SCREEN_ID, follow_screen_input

        sources = StubSources()
        sources.put("Media Background Pane", np.full((180, 320, 3), (255, 0, 0), dtype=np.uint8))
        show = show_from_patch().with_inputs({FOLLOW_SCREEN_ID: follow_screen_input()}, (FOLLOW_SCREEN_ID,))

        def pictures_while_playing(late: bool) -> int:
            renderer = EditorRenderer(sources, late=lambda: late)
            renderer.start()
            try:
                began = time.perf_counter()
                while time.perf_counter() - began < 0.8:  # playing: a new playhead every 20 ms
                    renderer.request(show, FOLLOW_SCREEN_ID, None, time.perf_counter() - began)
                    time.sleep(0.02)
            finally:
                renderer.stop()
            return renderer._seq

        self.assertLessEqual(pictures_while_playing(True), 3)  # at most twice a second while the lights run late
        self.assertGreaterEqual(pictures_while_playing(False), 6)  # (and many more when they don't)

    def test_a_light_added_mid_transition_doesnt_black_out_the_rest(self) -> None:
        from dataclasses import replace

        from centralight.commands import SendToBase, SwapShow
        from centralight.engine import Engine
        from tests.fakes import FakeClock, FakeOutput

        def solid(input_id, hex_colour):
            return Input(id=input_id, name=input_id, transition=Transition("fade", 2.0),
                         tracks=freeze([[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": hex_colour}}], [], [], []]))

        show = show_from_patch().with_inputs({"red": solid("red", "#FF0000"), "blue": solid("blue", "#0000FF")}, ("red", "blue"))
        clock = FakeClock()
        output = FakeOutput()
        engine = Engine(show, output, None, initial_base="red", clock=clock)
        engine.tick()
        engine.submit(SendToBase("blue"))
        engine.tick()
        clock.advance(0.5)
        engine.submit(SendToBase("red"))  # sent mid-fade: the mix is frozen, per light
        engine.tick()
        fewer = replace(show, fixtures=tuple(fx for fx in show.fixtures if fx.id != "slimpar-108"))
        engine.submit(SwapShow(fewer))  # a light taken off the map meanwhile
        clock.advance(0.5)
        engine.tick()
        self.assertEqual(engine._faults, 0)  # no tick failed
        self.assertGreater(output.last[40], 0)  # SlimPAR 105 still lit (fading to red)
        engine.submit(SendToBase("blue", cut=True))  # and a send now isn't lost
        engine.tick()
        self.assertEqual(engine.frames.get().base_id, "blue")

    def test_pictures_tables_are_kept_in_a_size_limited_store(self) -> None:
        from centralight import light_elements

        self.assertIsNotNone(light_elements._tables.max_bytes)
        ctx = RenderContext(160, 90, lights=lights_for(show_from_patch().fixtures))
        from centralight.bus import _owner

        for step in range(5):  # the lights moving: only the newest nearest-light map is kept
            ctx.lights = lights_for([light("a", 0.1 + step * 0.1, 0.5)])
            _owner(ctx)
        self.assertEqual(sum(1 for key in ctx.cache if key == "nearest" or (isinstance(key, tuple) and key[:1] == ("nearest",))), 1)


if __name__ == "__main__":
    unittest.main()
