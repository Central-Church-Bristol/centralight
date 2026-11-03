"""M2 engine pieces: compositing, elements, shapes, transitions, buses, overlays, solo, house levels."""

from __future__ import annotations

import math
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from centralight.bus import Bus, Frozen, Instance, OverlaySlot, render_input
from centralight.commands import (
    OverlayOff,
    SendToBase,
    SendToOverlay,
    SetHouse,
    SetOverlayOpacity,
    SetSolo,
    SwapShow,
)
from centralight.compositor import blend, scale_alpha
from centralight.elements import RenderContext, render_content, render_element, shape_coverage
from centralight.engine import Engine
from centralight.live_state import LiveState
from centralight.model import Input, Transition
from centralight.new_show import FOLLOW_SCREEN_ID, follow_screen_input, show_from_patch
from centralight.params import base_value, evaluate, is_dynamic
from centralight.sampler import Sampler
from centralight.schema import element_specs, get_path, set_path, visible
from centralight.show_io import freeze, input_from_dict, input_to_dict
from centralight.transitions import mix, mix_map, reveal
from tests.fakes import FakeClock, FakeOutput, StubSources

W, H = 160, 90


def ctx(**kwargs) -> RenderContext:
    return RenderContext(W, H, **kwargs)


def solid(r: float, g: float, b: float, a: float = 1.0) -> np.ndarray:
    canvas = np.empty((4, H, W), dtype=np.float32)
    for plane, value in enumerate((r * a, g * a, b * a, a)):
        canvas[plane].fill(value)
    return canvas


def paint_input(input_id: str, element: dict, transition: Transition = Transition(), **kwargs) -> Input:
    element = {"id": "E1", "kind": "paint", **element}
    return Input(id=input_id, name=input_id, transition=transition, tracks=freeze([[element], [], [], []]), **kwargs)


def fill(colour: str) -> dict:
    return {"fill": {"type": "colour", "colour": colour}}


class CompositorTests(unittest.TestCase):
    def test_opacity_is_a_uniform_mask(self) -> None:
        element = freeze({"id": "E1", "kind": "paint", **fill("#FF8000"), "opacity": 0.5})
        with_opacity = render_element(element, ctx())
        plain = render_element(freeze({"id": "E1", "kind": "paint", **fill("#FF8000")}), ctx())
        np.testing.assert_allclose(with_opacity, scale_alpha(plain, np.full((H, W), 0.5, np.float32)), atol=1e-6)

    def test_blend_formulas(self) -> None:
        grey = solid(0.5, 0.5, 0.5)
        white = solid(1, 1, 1)
        black = solid(0, 0, 0)
        np.testing.assert_allclose(blend(grey, white, "normal"), white)
        np.testing.assert_allclose(blend(grey, grey, "add")[:3, 0, 0], (1.0, 1.0, 1.0))  # clipped
        np.testing.assert_allclose(blend(grey, white, "multiply")[:3, 0, 0], (0.5, 0.5, 0.5))
        np.testing.assert_allclose(blend(grey, black, "multiply")[:3, 0, 0], (0, 0, 0))
        np.testing.assert_allclose(blend(grey, black, "screen")[:3, 0, 0], (0.5, 0.5, 0.5))
        np.testing.assert_allclose(blend(grey, white, "screen")[:3, 0, 0], (1, 1, 1))
        clear = np.zeros((4, H, W), np.float32)
        for mode in ("normal", "add", "multiply", "screen"):
            with self.subTest(mode=mode):
                np.testing.assert_allclose(blend(grey, clear, mode), grey, atol=1e-6)  # nothing on top: unchanged
                np.testing.assert_allclose(blend(None, grey, mode), grey)

    def test_half_transparent_over(self) -> None:
        out = blend(solid(0, 0, 1), solid(1, 0, 0, 0.5), "normal")
        np.testing.assert_allclose(out[:, 0, 0], (0.5, 0, 0.5, 1.0), atol=1e-6)


class ShapeTests(unittest.TestCase):
    def coverage(self, **shape) -> np.ndarray:
        cov, _rgb = shape_coverage(freeze(shape), ctx())
        return cov

    def test_rectangle_area_and_edges(self) -> None:
        cov = self.coverage(type="rect", x=0.5, y=0.5, w=0.5, h=0.5)
        self.assertAlmostEqual(float(cov.sum()) / (W * H), 0.25, delta=0.01)
        self.assertEqual(cov[45, 80], 1.0)
        self.assertEqual(cov[5, 5], 0.0)

    def test_ellipse_area(self) -> None:
        cov = self.coverage(type="ellipse", x=0.5, y=0.5, w=0.5, h=0.8)
        self.assertAlmostEqual(float(cov.sum()) / (W * H), math.pi / 4 * 0.5 * 0.8, delta=0.01)

    def test_rotation_turns_the_shape(self) -> None:
        wide = self.coverage(type="rect", x=0.5, y=0.5, w=0.5, h=0.1)  # 80 x 9 px
        turned = self.coverage(type="rect", x=0.5, y=0.5, w=0.5, h=0.1, rotation=90)
        self.assertEqual(int((wide[45] > 0.5).sum()), 80)
        self.assertLessEqual(int((turned[45] > 0.5).sum()), 10)
        self.assertGreaterEqual(int((turned[:, 80] > 0.5).sum()), 79)  # now 80 px tall (clipped to 90)

    def test_softness_widens_the_edge_and_opacity_scales(self) -> None:
        hard = self.coverage(type="rect", x=0.5, y=0.5, w=0.5, h=0.5)
        soft = self.coverage(type="rect", x=0.5, y=0.5, w=0.5, h=0.5, softness=1.0)
        partial = lambda c: int(((c > 0.01) & (c < 0.99)).sum())  # noqa: E731
        self.assertGreater(partial(soft), partial(hard) * 5)
        faint = self.coverage(type="rect", x=0.5, y=0.5, w=0.5, h=0.5, opacity=0.25)
        self.assertAlmostEqual(float(faint.max()), 0.25)

    def test_tiny_or_invisible_shapes_draw_nothing(self) -> None:
        self.assertIsNone(self.coverage(type="rect", w=0.0, h=0.5))
        self.assertIsNone(self.coverage(type="rect", opacity=0.0))


class PaintTests(unittest.TestCase):
    def test_gradients(self) -> None:
        linear = render_element(freeze({"id": "E", "kind": "paint", "fill": {"type": "linear", "from": "#FF0000", "to": "#0000FF", "angle": 0}}), ctx())
        self.assertGreater(linear[0, 45, 0], 0.98)
        self.assertGreater(linear[2, 45, -1], 0.98)
        down = render_element(freeze({"id": "E", "kind": "paint", "fill": {"type": "linear", "from": "#FF0000", "to": "#0000FF", "angle": 90}}), ctx())
        self.assertGreater(down[0, 0, 80], 0.98)
        self.assertGreater(down[2, -1, 80], 0.98)
        radial = render_element(freeze({"id": "E", "kind": "paint", "fill": {"type": "radial", "from": "#FFFFFF", "to": "#000000", "radius": 0.5}}), ctx())
        self.assertGreater(radial[0, 45, 80], 0.97)
        self.assertLess(radial[0, 0, 0], 0.05)

    def test_shapes_sit_on_the_fill(self) -> None:
        element = freeze({"id": "E", "kind": "paint", "fill": {"type": "none"},
                          "shapes": [{"id": "S1", "type": "rect", "x": 0.25, "y": 0.5, "w": 0.2, "h": 0.5, "colour": "#00FF00"}]})
        layer = render_element(element, ctx())
        np.testing.assert_allclose(layer[:, 45, 40], (0, 1, 0, 1))
        np.testing.assert_allclose(layer[:, 45, 120], (0, 0, 0, 0))  # no fill: transparent elsewhere

    def test_transform_moves_and_turns(self) -> None:
        element = freeze({"id": "E", "kind": "paint", "fill": {"type": "none"},
                          "shapes": [{"id": "S1", "type": "rect", "x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}],
                          "transform": {"x": 0.25}})
        layer = render_element(element, ctx())
        self.assertGreater(layer[3, 45, 40], 0.99)  # moved left by a quarter of the screen
        self.assertLess(layer[3, 45, 80], 0.01)

    def test_static_elements_are_cached_until_edited(self) -> None:
        context = ctx()
        element = freeze({"id": "E", "kind": "paint", **fill("#123456")})
        first = render_content(element, context)
        self.assertIs(render_content(element, context), first)
        edited = set_path(element, "fill.colour", "#654321")
        self.assertIsNot(render_content(edited, context), first)
        moving = set_path(element, "fill.colour", {"keys": [[0, "#000000"], [1, "#FFFFFF"]]})
        context.time = 0.5
        self.assertIsNot(render_content(moving, context), render_content(moving, context))  # dynamic: drawn each time


class MediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        (self.folder / "media").mkdir()
        import cv2

        picture = np.zeros((90, 160, 3), np.uint8)
        picture[:, :80] = (0, 0, 255)  # BGR: left half red
        cv2.imwrite(str(self.folder / "media" / "split.png"), picture)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_image_from_the_media_folder(self) -> None:
        layer = render_element(freeze({"id": "E", "kind": "image", "file": "media/split.png"}), ctx(folder=str(self.folder)))
        np.testing.assert_allclose(layer[:, 45, 10], (1, 0, 0, 1), atol=1e-6)
        np.testing.assert_allclose(layer[:, 45, 150], (0, 0, 0, 1), atol=1e-6)

    def test_files_outside_the_show_are_never_read(self) -> None:
        outside = render_element(freeze({"id": "E", "kind": "image", "file": "../elsewhere.png"}), ctx(folder=str(self.folder / "media")))
        self.assertIsNone(outside)
        missing = render_element(freeze({"id": "E", "kind": "image", "file": "media/none.png"}), ctx(folder=str(self.folder)))
        self.assertIsNone(missing)

    def test_fit_letterboxes(self) -> None:
        import cv2

        square = np.full((100, 100, 3), 255, np.uint8)
        cv2.imwrite(str(self.folder / "media" / "square.png"), square)
        layer = render_element(freeze({"id": "E", "kind": "image", "file": "media/square.png", "transform": {"fit": "fit"}}), ctx(folder=str(self.folder)))
        self.assertEqual(layer[3, 45, 5], 0.0)  # transparent bars at the sides
        self.assertEqual(layer[3, 45, 80], 1.0)


class MaskTests(unittest.TestCase):
    def test_unticking_a_light_affects_only_that_light(self) -> None:
        show = show_from_patch()
        sources = StubSources()
        image = np.zeros((180, 320, 3), np.uint8)
        image[:, :] = (255, 0, 0)
        sources.put("Media Background Pane", image)
        purple = Input(id="pillars", name="Pillars purple", tracks=freeze([[], [{"id": "E1", "kind": "paint", **fill("#7A3CFF"), "mask": {"exclude": ["slimpar-106"]}}], [], []]))
        follow = follow_screen_input()
        layered = Input(id="both", name="Both", tracks=(follow.tracks[0],) + purple.tracks[1:])
        show = show.with_inputs({"both": layered}, ("both",))
        output = FakeOutput()
        engine = Engine(show, output, None, sources=sources, initial_base="both", clock=FakeClock())
        engine.tick()
        frame = output.last
        self.assertEqual(tuple(frame[49:52]), (255, 0, 0))  # SlimPAR 106 (ch 50): still follows the screen exactly
        self.assertEqual(tuple(frame[58:61]), (0x7A, 0x3C, 0xFF))  # SlimPAR 107 (ch 59): purple
        self.assertEqual(tuple(frame[4:7]), (0x7A, 0x3C, 0xFF))


class TransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old = solid(1, 0, 0)
        self.new = solid(0, 0, 1)
        self.owner = Sampler(show_from_patch().fixtures).owner

    def test_every_kind_starts_old_and_ends_new(self) -> None:
        for kind in ("cut", "fade", "dip", "wipe", "iris", "random"):
            with self.subTest(kind=kind):
                start = mix(kind, self.old, self.new, 0.0, owner=self.owner, seed=3)
                end = mix(kind, self.old, self.new, 1.0, owner=self.owner, seed=3)
                np.testing.assert_allclose(start, self.old, atol=1e-6)
                np.testing.assert_allclose(end, self.new, atol=1e-6)

    def test_half_way(self) -> None:
        np.testing.assert_allclose(mix("fade", self.old, self.new, 0.5)[:, 0, 0], (0.5, 0, 0.5, 1))
        np.testing.assert_allclose(mix("dip", self.old, self.new, 0.5)[:3, 0, 0], (0, 0, 0))
        np.testing.assert_allclose(mix("cut", self.old, self.new, 0.5), self.new)
        wipe = mix("wipe", self.old, self.new, 0.5, softness=0.05)
        self.assertGreater(wipe[2, 45, 10], 0.99)  # left: new already
        self.assertGreater(wipe[0, 45, 150], 0.99)  # right: still old
        left_to_right_up = mix("wipe", self.old, self.new, 0.5, direction=-90, softness=0.05)
        self.assertGreater(left_to_right_up[2, 85, 80], 0.99)  # wiping upwards: the bottom changes first
        iris = mix("iris", self.old, self.new, 0.5, softness=0.05)
        self.assertGreater(iris[2, 45, 80], 0.99)  # middle opens first
        self.assertGreater(iris[0, 0, 0], 0.99)  # corners last

    def test_random_changes_light_by_light(self) -> None:
        amount = mix_map("random", 0.5, W, H, owner=self.owner, seed=11)
        per_light = [np.unique(amount[self.owner == i]) for i in range(int(self.owner.max()) + 1)]
        for values in per_light:
            self.assertEqual(len(values), 1)  # each light's patch changes all at once
        levels = sorted(float(v[0]) for v in per_light)
        self.assertLess(levels[0], 0.1)
        self.assertGreater(levels[-1], 0.9)
        np.testing.assert_array_equal(amount, mix_map("random", 0.5, W, H, owner=self.owner, seed=11))  # same seed, same order

    def test_overlay_reveal(self) -> None:
        layer = solid(1, 1, 1)
        np.testing.assert_allclose(reveal("fade", layer, 0.5)[:, 0, 0], (0.5, 0.5, 0.5, 0.5))
        np.testing.assert_allclose(reveal("dip", layer, 0.5)[:, 0, 0], (0.5, 0.5, 0.5, 0.5))  # dip appears as a fade


class BusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.red = paint_input("red", fill("#FF0000"))
        self.blue = paint_input("blue", fill("#0000FF"))
        self.timed = paint_input("timed", fill("#00FF00"), infinite=False, length=5.0)
        self.ctx = ctx()

    def test_send_resend_and_restart(self) -> None:
        bus = Bus("base")
        self.assertTrue(bus.send(self.red, 0.0, self.ctx))
        self.assertFalse(bus.send(self.red, 2.0, self.ctx))  # infinite, already there: nothing
        bus.send(self.timed, 3.0, self.ctx, cut=True)
        first = bus.current
        self.assertTrue(bus.send(self.timed, 4.0, self.ctx))  # timed: restarts
        self.assertIsNot(bus.current, first)
        self.assertEqual(bus.current.started, 4.0)

    def test_sending_mid_transition_freezes_the_mix(self) -> None:
        bus = Bus("base")
        bus.send(self.red, 0.0, self.ctx, cut=True)
        bus.send(self.blue, 10.0, self.ctx)  # 1 s fade
        before = bus.render(10.5, self.ctx).copy()
        bus.send(self.red, 10.5, self.ctx)
        self.assertIsInstance(bus.outgoing, Frozen)
        np.testing.assert_allclose(bus.render(10.5, self.ctx), before, atol=1e-6)  # no jump
        np.testing.assert_allclose(bus.render(11.6, self.ctx)[:, 0, 0], (1, 0, 0, 1))

    def test_overlay_toggle_rules(self) -> None:
        slot = OverlaySlot(1)
        slot.send(self.red, 0.0, self.ctx)
        self.assertTrue(slot.on)
        self.assertAlmostEqual(slot.visibility(0.5), 0.5)
        slot.send(self.red, 2.0, self.ctx)  # the same input again: toggles off
        self.assertFalse(slot.on)
        self.assertIsNotNone(slot.render(2.5, self.ctx))  # still fading out
        self.assertIsNone(slot.render(3.1, self.ctx))
        self.assertEqual(slot.input_id(), "")
        slot.send(self.red, 4.0, self.ctx, state="on")
        slot.send(self.red, 6.0, self.ctx, state="on")  # explicit on: stays on
        self.assertTrue(slot.on)
        slot.send(self.blue, 7.0, self.ctx)  # another input: replaces it
        self.assertTrue(slot.on)
        np.testing.assert_allclose(slot.render(8.1, self.ctx)[:, 0, 0], (0, 0, 1, 1))
        slot.send(self.blue, 9.0, self.ctx, state="off")
        self.assertFalse(slot.on)

    def test_render_input_blends_tracks_in_order(self) -> None:
        tracks = freeze([
            [{"id": "E1", "kind": "paint", **fill("#808080")}],
            [{"id": "E2", "kind": "paint", **fill("#FFFFFF"), "blend": "multiply"}],
            [], [],
        ])
        canvas = render_input(Instance(Input(id="x", name="x", tracks=tracks), 0.0), 0.0, self.ctx)
        self.assertAlmostEqual(float(canvas[0, 0, 0]), 0x80 / 255, places=5)


class EngineM2Tests(unittest.TestCase):
    def setUp(self) -> None:
        inputs = {
            "red": paint_input("red", fill("#FF0000")),
            "glow": paint_input("glow", {"fill": {"type": "none"}, "shapes": [{"id": "S1", "type": "rect", "x": 0.2, "y": 0.2, "w": 0.2, "h": 0.4, "colour": "#00FF00"}]}),
            "keylit": paint_input("keylit", fill("#0000FF"), house=freeze({"dimmer-4": 0.6})),
            "blue": paint_input("blue", fill("#0000FF")),
        }
        self.show = show_from_patch().with_inputs(inputs, tuple(inputs))
        self.clock = FakeClock()
        self.output = FakeOutput()
        self.engine = Engine(self.show, self.output, None, clock=self.clock)

    def run_for(self, seconds: float) -> None:
        for _ in range(int(round(seconds / 0.025))):
            self.clock.advance(0.025)
            self.engine.tick()

    def rgb(self, channel: int) -> tuple:
        return tuple(self.output.last[channel - 1:channel + 2])

    def test_overlay_on_top_of_base_with_see_through_parts(self) -> None:
        self.engine.submit(SendToBase("red", cut=True))
        self.engine.submit(SendToOverlay(1, "glow"))
        self.run_for(1.1)
        self.assertEqual(self.rgb(5), (0, 255, 0))  # SlimPAR 101 (top left) is under the green box
        self.assertEqual(self.rgb(41), (255, 0, 0))  # SlimPAR 105: the overlay is clear there, Base shows
        frame = self.engine.frames.get()
        self.assertEqual(frame.overlays[0].input_id, "glow")
        self.assertEqual(frame.live_inputs, frozenset({"red", "glow"}))
        self.engine.submit(SetOverlayOpacity(1, 0.5, fade=0))
        self.run_for(0.1)
        self.assertEqual(self.rgb(5), (128, 128, 0))
        self.engine.submit(OverlayOff(1))
        self.run_for(1.1)
        self.assertEqual(self.rgb(5), (255, 0, 0))
        self.assertEqual(self.engine.frames.get().overlays[0].input_id, "")

    def test_solo_replaces_the_result(self) -> None:
        self.engine.submit(SendToBase("red", cut=True))
        self.engine.submit(SetSolo("blue"))
        self.engine.tick()
        self.assertEqual(self.rgb(41), (0, 0, 255))
        self.assertEqual(self.engine.frames.get().solo, "blue")
        self.engine.submit(SetSolo(None))
        self.engine.tick()
        self.assertEqual(self.rgb(41), (255, 0, 0))

    def test_input_house_levels_and_manual_override(self) -> None:
        self.engine.submit(SendToBase("keylit"))
        self.run_for(0.5)
        self.assertTrue(85 <= self.output.last[3] <= 115, self.output.last[3])  # half way through the 1 s fade up
        self.run_for(0.6)
        self.assertEqual(self.output.last[3], 153)  # 60 %
        self.engine.submit(SetHouse("dimmer-4", level=0.2))
        self.run_for(0.2)
        self.assertEqual(self.output.last[3], 51)  # the manual move wins
        self.engine.submit(SendToBase("blue"))
        self.run_for(1.1)
        self.assertEqual(self.output.last[3], 51)  # an input without house levels leaves them alone

    def test_editing_never_hitches_a_running_fade(self) -> None:
        """The Editor sends the engine a new show on every change. A fade that's running carries
        straight on: the colours match, tick for tick, an engine that wasn't edited at all.
        Handling the new show fits easily inside one tick."""
        reference_output = FakeOutput()
        reference = Engine(self.show, reference_output, None, clock=self.clock)
        for engine in (self.engine, reference):
            engine.submit(SendToBase("red", cut=True))
            engine.tick()
            engine.submit(SendToBase("blue"))  # a 1 s fade

        def tick_both() -> None:
            self.clock.advance(0.025)
            self.engine.tick()
            reference.tick()

        for _ in range(20):  # half way
            tick_both()
        timings = []
        for step in range(8):  # an edit every tick, like dragging a slider in the Editor
            edited = paint_input("blue", fill("#0000FF"), house=freeze({"dimmer-4": 0.1 * step}))
            self.engine.submit(SwapShow(self.show.with_inputs({**self.show.inputs, "blue": edited})))
            self.clock.advance(0.025)
            started = time.perf_counter()
            self.engine.tick()
            timings.append((time.perf_counter() - started) * 1000)
            reference.tick()
            self.assertEqual(self.output.last[4:], reference_output.last[4:])  # every colour light, every tick
        self.assertTrue(0 < self.rgb(41)[0] < 255)  # really still mid-fade
        self.assertLess(max(timings), 8.0)
        for _ in range(20):
            tick_both()
        self.assertEqual(self.rgb(41), (0, 0, 255))

    def test_keyframed_house_level_follows_the_input(self) -> None:
        moving = paint_input("swell", fill("#000000"), house=freeze({"dimmer-1": {"keys": [[0, 0.0], [4, 1.0, "linear"]]}}))
        self.engine.submit(SwapShow(self.show.with_inputs({**self.show.inputs, "swell": moving})))
        self.engine.submit(SendToBase("swell", cut=True))
        self.run_for(2.0)
        self.assertTrue(120 <= self.output.last[0] <= 135, self.output.last[0])

    def test_overlays_come_back_after_a_restart(self) -> None:
        self.engine.submit(SendToBase("red", cut=True))
        self.engine.submit(SendToOverlay(2, "glow"))
        self.engine.submit(SetOverlayOpacity(2, 0.5, fade=0))
        self.run_for(1.2)
        state = LiveState.from_dict(self.engine.stop().to_dict())
        engine2 = Engine(self.show, FakeOutput(), state, clock=FakeClock())
        engine2.tick()
        self.assertEqual(engine2.frames.get().overlays[1].input_id, "glow")
        self.assertEqual(engine2.frames.get().overlays[1].opacity, 0.5)

    def test_engine_time_per_tick(self) -> None:
        """Typical (one NDI element) and worst case (a transition and four busy overlays)."""
        sources = StubSources()
        rng = np.random.default_rng(1)
        sources.put("Media Background Pane", rng.integers(0, 255, (180, 320, 3), dtype=np.uint8))
        busy = {
            "id": "E1", "kind": "paint", "fill": {"type": "linear", "from": "#FF0000", "to": "#0000FF", "angle": {"keys": [[0, 0], [10, 360]]}},
            "shapes": [{"id": f"S{i}", "type": "ellipse", "x": {"keys": [[0, 0.1], [10, 0.9]]}, "y": 0.5, "w": 0.2, "h": 0.4, "softness": 0.5, "rotation": {"keys": [[0, 0], [10, 90]]}} for i in range(3)],
            "transform": {"rotation": {"keys": [[0, 0], [10, 30]]}, "scale": 1.1}, "opacity": 0.8, "blend": "screen",
            "mask": {"exclude": ["slimpar-105"]},
        }
        bar = {
            "id": "E2", "kind": "paint", "fill": {"type": "none"},
            "shapes": [{"id": "S9", "type": "rect", "x": {"keys": [[0, 0.0], [10, 1.0, "linear"]]}, "y": 0.5, "w": 0.15, "h": 1.2,
                        "softness": 0.3, "colour": {"keys": [[0, "#FF0000"], [10, "#0000FF"]]}}],
        }
        inputs = {
            "follow": follow_screen_input(),
            # the plan's worst case: a transition on Base and four overlays, everything moving
            "busy": Input(id="busy", name="Busy", transition=Transition("random", 2.0), tracks=freeze([[busy], [bar], [], []])),
        }
        show = show_from_patch().with_inputs(inputs, tuple(inputs))
        engine = Engine(show, FakeOutput(), None, sources=sources, initial_base="follow")

        def measure(ticks: int) -> list[float]:
            times = []
            for i in range(ticks):
                sources.put("Media Background Pane", rng.integers(0, 255, (180, 320, 3), dtype=np.uint8))
                started = time.perf_counter()
                engine.tick()
                times.append((time.perf_counter() - started) * 1000)
            return times

        import cv2

        threads = cv2.getNumThreads()
        cv2.setNumThreads(1)  # as the app runs it (gentle on the PC)
        self.addCleanup(cv2.setNumThreads, threads)
        measure(10)
        typical = float(np.median(measure(80)))
        engine.submit(SendToBase("busy"))
        for slot in range(1, 5):
            engine.submit(SendToOverlay(slot, "busy"))
        worst = max(measure(40)[5:])
        print(f"\n    engine: typical {typical:.2f} ms per tick, worst case {worst:.2f} ms")
        self.assertLess(typical, 2.0)
        self.assertLess(worst, 8.0)


class ParamsAndSchemaTests(unittest.TestCase):
    def test_keyframes(self) -> None:
        keys = {"keys": [[0, 0.0], [2, 1.0, "linear"], [4, 0.0, "hold"], [6, 1.0]]}
        self.assertTrue(is_dynamic(freeze(keys)))
        self.assertEqual(base_value(keys, -1, 9.0), 0.0)
        self.assertAlmostEqual(base_value(keys, 1.0, 9.0), 0.5)  # the first key is smooth: eased half way is 0.5
        self.assertAlmostEqual(base_value(keys, 3.0, 9.0), 0.5)  # linear
        self.assertEqual(base_value(keys, 5.0, 9.0), 0.0)  # hold
        self.assertEqual(base_value(keys, 9.0, 9.0), 1.0)  # after the last key
        self.assertAlmostEqual(base_value({"keys": [[0, 0.0], [1, 1.0]]}, 0.25, 0.0), 0.15625)  # smooth easing
        colour = base_value({"keys": [[0, "#000000", "linear"], [1, "#FFFFFF"]]}, 0.5, (0, 0, 0), "colour")
        np.testing.assert_allclose(colour, (0.5, 0.5, 0.5))
        self.assertEqual(evaluate({"value": 0.3, "expr": "never used yet"}, 0, 0.0), 0.3)
        self.assertEqual(evaluate("#FF0000", 0, (0, 0, 0), "colour"), (1.0, 0.0, 0.0))

    def test_schema_paths(self) -> None:
        element = freeze({"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#FFFFFF"}})
        changed = set_path(element, "transform.x", 0.25)
        self.assertEqual(get_path(changed, "transform.x"), 0.25)
        self.assertEqual(get_path(changed, "fill.colour"), "#FFFFFF")
        self.assertIsNone(get_path(set_path(changed, "transform.x", None), "transform.x"))
        specs = element_specs("paint")
        angle = next(s for s in specs if s.path == "fill.angle")
        self.assertFalse(visible(angle, changed, specs))
        self.assertTrue(visible(angle, set_path(changed, "fill.type", "linear"), specs))
        for kind in ("paint", "image", "ndi", "video"):
            paths = [s.path for s in element_specs(kind)]
            self.assertEqual(len(paths), len(set(paths)), kind)

    def test_transition_settings_round_trip(self) -> None:
        inp = Input(id="w", name="W", transition=Transition("wipe", 2.0, 90.0, 0.5))
        data = input_to_dict(inp)
        self.assertEqual(data["transition"], {"kind": "wipe", "duration": 2.0, "direction": 90.0, "softness": 0.5})
        self.assertEqual(input_from_dict(data)[0], inp)
        self.assertNotIn("transition", input_to_dict(Input(id="f", name="F")))


if __name__ == "__main__":
    unittest.main()
