"""Follow screen and the Base bus, under a hand-moved clock; plus Identify and test colours."""

from __future__ import annotations

import unittest
from dataclasses import replace

import numpy as np

from centralight.commands import (
    ClearBase,
    FlashLights,
    SendToBase,
    SetIdentify,
    SetMaster,
    SetTestColour,
    SwapShow,
)
from centralight.engine import Engine
from centralight.live_state import LiveState
from centralight.model import Input, MapSpot, Transition
from centralight.new_show import FOLLOW_SCREEN_ID, follow_screen_input, show_from_patch
from centralight.lights import identify_colours
from centralight.show_io import freeze
from tests.fakes import FakeClock, FakeOutput, StubSources

SLIMPAR_105_RED = 40  # channel 41
SLIMPAR_108_BLUE = 67 + 2  # channel 70
SLIMPAR_101 = 4  # channel 5


def paint(input_id: str, colour: str, transition: Transition = Transition()) -> Input:
    return Input(id=input_id, name=input_id.title(), transition=transition,
                 tracks=freeze([[{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": colour}}], [], [], []]))


def split_picture() -> np.ndarray:
    """Left half red, right half blue (RGB uint8, 320x180 like a shrunk NDI frame)."""
    image = np.zeros((180, 320, 3), dtype=np.uint8)
    image[:, :160] = (255, 0, 0)
    image[:, 160:] = (0, 0, 255)
    return image


class BaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        inputs = {i.id: i for i in (follow_screen_input(), paint("white", "#FFFFFF"), paint("snap", "#00FF00", Transition("cut", 0)))}
        self.show = show_from_patch().with_inputs(inputs, (FOLLOW_SCREEN_ID, "white", "snap"))
        self.sources = StubSources()
        self.sources.put("Media Background Pane", split_picture())
        self.clock = FakeClock()
        self.output = FakeOutput()

    def make(self, live: LiveState | None = None, initial_base: str | None = FOLLOW_SCREEN_ID) -> Engine:
        return Engine(self.show, self.output, live, sources=self.sources, initial_base=initial_base, clock=self.clock)

    def run_for(self, engine: Engine, seconds: float, step: float = 0.025) -> None:
        for _ in range(int(round(seconds / step))):
            self.clock.advance(step)
            engine.tick()

    def rgb(self, light_start: int) -> tuple[int, int, int]:
        frame = self.output.last
        return tuple(frame[light_start - 1:light_start + 2])


class FollowScreenTests(BaseTestCase):
    def test_first_run_puts_follow_screen_on_base(self) -> None:
        engine = self.make()
        engine.tick()
        self.assertEqual(self.rgb(41), (255, 0, 0))  # SlimPAR 105, left wall: red
        self.assertEqual(self.rgb(68), (0, 0, 255))  # SlimPAR 108, right wall: blue
        self.assertEqual(self.output.last[41 - 1 + 7], 255)  # its dimmer channel at full
        frame = engine.frames.get()
        self.assertEqual((frame.base, frame.base_id), ("Follow screen", FOLLOW_SCREEN_ID))
        self.assertIsNone(frame.preview)  # no window shows the live picture: none is drawn
        engine.set_preview_wanted(True)  # the Dashboard is on screen
        self.clock.advance(0.25)
        engine.tick()
        self.assertEqual(engine.frames.get().preview.shape, (90, 160, 3))
        self.assertEqual(self.sources.keep, {"media background pane"})

    def test_lights_follow_the_newest_picture(self) -> None:
        engine = self.make()
        engine.tick()
        image = np.zeros((180, 320, 3), dtype=np.uint8)
        image[:, :] = (0, 255, 0)
        self.sources.put("Media Background Pane", image)
        engine.tick()
        self.assertEqual(self.rgb(41), (0, 255, 0))

    def test_no_picture_yet_holds_then_is_black_and_master_scales(self) -> None:
        self.sources.drop("Media Background Pane")
        engine = self.make()
        engine.tick()
        self.assertEqual(self.output.frames, [])  # at startup nothing is sent while the feed's first picture comes...
        self.run_for(engine, 3.1)
        self.assertEqual(self.rgb(41), (0, 0, 0))  # ...for at most 3 s: then no picture is black
        self.sources.put("Media Background Pane", split_picture())
        engine.submit(SetMaster(0.5))
        self.run_for(engine, 0.2)
        self.assertEqual(self.rgb(41), (128, 0, 0))

    def test_restores_base_and_its_time_after_a_restart(self) -> None:
        engine = self.make(LiveState(show="x", base={"input": "white", "time": 12.5}))
        engine.tick()
        self.assertEqual(self.rgb(41), (255, 255, 255))
        self.assertAlmostEqual(engine.live_state().base["time"], 12.5, places=2)
        engine2 = self.make(LiveState(show="x", base=None))  # Base was black: stays black
        engine2.tick()
        self.assertEqual(self.rgb(41), (0, 0, 0))


class BaseBusTests(BaseTestCase):
    def test_send_fades_with_the_input_transition(self) -> None:
        engine = self.make()
        engine.tick()
        engine.submit(SendToBase("white"))
        engine.tick()  # the fade starts now
        self.run_for(engine, 0.5)
        r, g, b = self.rgb(41)  # red -> white, half way (eased)
        self.assertEqual(r, 255)
        self.assertTrue(120 <= g <= 135, g)
        self.assertAlmostEqual(engine.frames.get().base_progress, 0.5, delta=0.03)
        self.run_for(engine, 0.7)
        self.assertEqual(self.rgb(41), (255, 255, 255))
        self.assertIsNone(engine.frames.get().base_progress)

    def test_cut_and_cut_transition(self) -> None:
        engine = self.make()
        engine.submit(SendToBase("white", cut=True))
        engine.tick()
        self.assertEqual(self.rgb(41), (255, 255, 255))
        engine.submit(SendToBase("snap"))  # its own transition is a cut
        engine.tick()
        self.assertEqual(self.rgb(41), (0, 255, 0))

    def test_resending_an_infinite_input_does_nothing(self) -> None:
        engine = self.make()
        engine.tick()
        before = engine._base.current
        engine.submit(SendToBase(FOLLOW_SCREEN_ID))
        engine.tick()
        self.assertIs(engine._base.current, before)
        self.assertIsNone(engine.frames.get().base_progress)

    def test_clear_base_fades_to_black(self) -> None:
        engine = self.make()
        engine.tick()
        engine.submit(ClearBase())
        self.run_for(engine, 1.1)
        self.assertEqual(self.rgb(41), (0, 0, 0))
        self.assertEqual(engine.frames.get().base, "Black")

    def test_deleted_input_keeps_playing_until_replaced(self) -> None:
        engine = self.make()
        engine.submit(SendToBase("white", cut=True))
        engine.tick()
        fewer = self.show.with_inputs({FOLLOW_SCREEN_ID: self.show.inputs[FOLLOW_SCREEN_ID]})
        engine.submit(SwapShow(fewer))
        self.run_for(engine, 0.1)
        self.assertEqual(self.rgb(41), (255, 255, 255))
        engine.submit(SendToBase(FOLLOW_SCREEN_ID, cut=True))
        engine.tick()
        self.assertEqual(self.rgb(41), (255, 0, 0))

    def test_edits_to_the_running_input_show_at_once(self) -> None:
        engine = self.make()
        engine.submit(SendToBase("white", cut=True))
        engine.tick()
        edited = self.show.with_inputs({**self.show.inputs, "white": paint("white", "#FF00FF")})
        engine.submit(SwapShow(edited))
        engine.tick()
        self.assertEqual(self.rgb(41), (255, 0, 255))

    def test_a_broken_element_draws_nothing_and_is_reported(self) -> None:
        broken = Input(id="broken", name="Broken", tracks=freeze([
            [{"id": "E1", "kind": "paint", "fill": {"type": "colour", "colour": "#0000FF"}}],
            [{"id": "E2", "kind": "ndi", "source": 12345}],  # not a name: falls back, still fine
            [{"id": "E3", "kind": "paint", "fill": {"type": "colour", "colour": "#00FF00", "boom": True}}],
            [],
        ]))
        import centralight.light_elements as light_elements

        original = light_elements.light_paint

        def fragile(element, ctx, u, v):
            if element.get("fill", {}).get("boom"):
                raise ValueError("bad paint")
            return original(element, ctx, u, v)

        light_elements.light_paint = fragile
        try:
            show = self.show.with_inputs({**self.show.inputs, "broken": broken})
            engine = Engine(show, self.output, None, sources=self.sources, initial_base="broken", clock=self.clock)
            engine.tick()
        finally:
            light_elements.light_paint = original
        # track 2's NDI picture covers track 1, and the broken track 3 is skipped
        self.assertEqual(self.rgb(41), (255, 0, 0))
        self.assertIn("broken/E3", engine._ctx.faults)

    def test_moving_a_light_changes_what_it_follows(self) -> None:
        engine = self.make()
        engine.tick()
        self.assertEqual(self.rgb(41), (255, 0, 0))
        moved = replace(self.show, fixtures=tuple(replace(fx, map=MapSpot(0.9, 0.5)) if fx.id == "slimpar-105" else fx for fx in self.show.fixtures))
        engine.submit(SwapShow(moved))
        engine.tick()
        self.assertEqual(self.rgb(41), (0, 0, 255))


class IdentifyAndTestTests(BaseTestCase):
    def test_identify_shows_each_lights_colour_at_full(self) -> None:
        engine = self.make()
        engine.submit(SetMaster(0.2, fade=0))
        engine.submit(SetIdentify(True, seed=3))
        engine.tick()
        expected = identify_colours([fx.id for fx in self.show.colour_fixtures()], 3)
        name, rgb = expected["slimpar-101"]
        self.assertEqual(self.rgb(5), tuple(round(v * 255) for v in rgb))
        self.assertTrue(engine.frames.get().identify)
        from centralight.commands import ToBlack

        engine.submit(ToBlack(0.0))
        engine.tick()
        self.assertEqual(self.rgb(5), tuple(round(v * 255) for v in rgb))  # Identify still shows which light is which
        engine.submit(SetIdentify(False))
        engine.tick()
        self.assertEqual(self.rgb(41), (0, 0, 0))  # and then the lights are black (nothing on Base)
        engine.submit(SendToBase(FOLLOW_SCREEN_ID, cut=True))
        engine.tick()
        self.assertEqual(self.rgb(41), (51, 0, 0))  # back to the picture, at Master 20 %

    def test_test_colours_on_some_lights(self) -> None:
        engine = self.make()
        engine.submit(SetTestColour((0.0, 1.0, 0.0), ("slimpar-108",)))
        engine.tick()
        self.assertEqual(self.rgb(68), (0, 255, 0))
        self.assertEqual(self.rgb(41), (255, 0, 0))
        self.assertTrue(engine.frames.get().testing)
        engine.submit(SetTestColour((0.0, 0.0, 0.0)))  # "Off" on every light
        engine.tick()
        self.assertEqual(self.rgb(41), (0, 0, 0))
        engine.submit(SetTestColour(None))
        engine.tick()
        self.assertEqual(self.rgb(41), (255, 0, 0))

    def test_flash_blinks_three_times_then_stops(self) -> None:
        engine = self.make()
        engine.submit(SetTestColour((0.0, 0.0, 0.0)))
        engine.submit(FlashLights(("slimpar-101",)))
        seen = []
        for _ in range(48):
            engine.tick()
            seen.append(self.rgb(5)[0])
            self.clock.advance(0.025)
        ons = [i for i in range(1, len(seen)) if seen[i] == 255 and seen[i - 1] == 0]
        self.assertEqual(seen[0], 255)
        self.assertEqual(len(ons), 2)  # on at the start, then twice more
        self.assertEqual(seen[-1], 0)  # finished


if __name__ == "__main__":
    unittest.main()
