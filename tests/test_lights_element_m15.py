"""M15 part 4: the Lights element, what "Save look / chase as input" makes. It sets its lights' colours straight
(colour times brightness); lights it doesn't set are see-through, so what's under it shows. A chase steps with its
timing (BPM or seconds, crossfade, direction, play once, each beat), and speed with keys never jumps."""

from __future__ import annotations

import unittest
from types import MappingProxyType

from centralight.commands import SendToBase, SendToOverlay
from centralight.console import Chase, ChaseStep
from centralight.input_edit import lights_elements, new_lights_input
from centralight.lights_element import element_chase, lights_element, looks_at, position
from centralight.new_show import show_from_patch
from tests.fakes import FakeEngine
from tests.gui_fixtures import with_inputs

A, B, C = "slimpar-101", "slimpar-102", "slimpar-103"


def chans(engine, light: str) -> tuple[int, ...]:
    fx = next(f for f in engine.show.fixtures if f.id == light)
    frame = engine.fake_output.frames[-1]
    return tuple(frame[fx.address - 1:fx.address - 1 + fx.channels])


def look(*pairs) -> ChaseStep:
    return ChaseStep(MappingProxyType({light: rgbl for light, rgbl in pairs}))


class Ctx:
    """Just enough of a RenderContext for the element's timing."""

    def __init__(self, t: float, memory: dict | None = None, beat: float = 0.0) -> None:
        self.time = t
        self.memory = memory
        self.audio = {"beat": beat}
        self.element_key = "input/E1"


class LightsElementTests(unittest.TestCase):
    def engine_with(self, chase: Chase, name: str = "Saved look"):
        show, input_id = new_lights_input(with_inputs(show_from_patch()), name, chase)
        return FakeEngine(show), input_id

    def test_a_look_on_base_and_on_the_overlay(self) -> None:
        engine, saved = self.engine_with(Chase((A, B), (look((A, (1.0, 0.5, 0.0, 1.0)), (B, (0.0, 0.0, 1.0, 0.5))),)))
        engine.submit(SendToBase(saved, cut=True))
        engine.settle(0.1)
        self.assertEqual(chans(engine, A)[:3], (255, 128, 0))
        self.assertEqual(chans(engine, B)[:3], (0, 0, 128))  # (brightness scales the colour: inputs carry no dimmer)
        self.assertEqual(chans(engine, C)[:3], (0, 0, 0))  # (on Base, see-through is black)
        engine.submit(SendToBase("worship-blue", cut=True))
        engine.settle(0.1)
        under = chans(engine, C)
        engine.submit(SendToOverlay(saved))
        engine.settle(2.0)
        self.assertEqual(chans(engine, A)[:3], (255, 128, 0))
        self.assertEqual(chans(engine, C), under)  # (on the Overlay, what's under shows where it sets nothing)

    def test_a_chase_steps_with_its_timing(self) -> None:
        red, blue = (1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)
        engine, saved = self.engine_with(Chase((A,), (look((A, red)), look((A, blue))), bpm=60))
        engine.submit(SendToBase(saved, cut=True))
        engine.settle(0.2)
        self.assertEqual(chans(engine, A)[:3], (255, 0, 0))
        engine.settle(1.0)
        self.assertEqual(chans(engine, A)[:3], (0, 0, 255))
        engine.settle(1.0)
        self.assertEqual(chans(engine, A)[:3], (255, 0, 0))

    def test_crossfade_direction_and_play_once(self) -> None:
        chase = Chase((A,), (look((A, (0.0, 0.0, 0.0, 1.0))), look((A, (1.0, 0.0, 0.0, 1.0))), look((A, (0.0, 1.0, 0.0, 1.0)))),
                      bpm=60, crossfade=1.0, direction="backward", loop=False)
        element = lights_element(chase)
        self.assertAlmostEqual(looks_at(element, Ctx(1.5))[A][0], 0.5, places=3)  # (backward: the last step, then the middle)
        self.assertEqual(looks_at(element, Ctx(30.0))[A][:3], (0.0, 0.0, 0.0))  # (once: it stays on its last, the first)

    def test_a_speed_with_keys_never_jumps(self) -> None:
        chase = Chase((A,), (look((A, (1.0, 0.0, 0.0, 1.0))), look((A, (0.0, 0.0, 1.0, 1.0)))), bpm=60)
        element = dict(lights_element(chase))
        element["bpm"] = {"keys": [[0.0, 60], [10.0, 120]]}
        memory: dict = {}
        places = []
        for tick in range(0, 401):
            places.append(position(element, Ctx(tick * 0.025, memory)))
        jumps = [b - a for a, b in zip(places, places[1:])]
        self.assertTrue(all(0.0 < j < 0.06 for j in jumps))  # (always onwards, never a leap)
        self.assertAlmostEqual(places[-1], 15.0, delta=0.3)  # (from 1 to 2 steps a second over 10 s: about 15 steps)

    def test_each_beat(self) -> None:
        chase = Chase((A,), (look((A, (1.0, 0.0, 0.0, 1.0))), look((A, (0.0, 0.0, 1.0, 1.0)))), on_beat=True)
        element = lights_element(chase)
        memory: dict = {}
        for moment, level in ((0.1, 0.0), (0.5, 1.0), (0.6, 0.5), (1.0, 1.0)):
            position(element, Ctx(moment, memory, level))
        self.assertEqual(int(position(element, Ctx(1.05, memory, 1.0))), 2)  # (two beats, however long each stays up)

    def test_saved_and_read_back(self) -> None:
        chase = Chase((A, B), (look((A, (1.0, 0.5, 0.25, 1.0))), look((B, (0.0, 1.0, 0.0, 0.5)))), bpm=96, crossfade=0.3,
                      direction="bounce", loop=False, on_beat=True)
        show, input_id = new_lights_input(with_inputs(show_from_patch()), "Pillars chase", chase)
        inp = show.inputs[input_id]
        self.assertEqual((inp.name, inp.infinite), ("Pillars chase", True))
        [element] = lights_elements(inp)
        again = element_chase(element)
        self.assertEqual((again.lights, again.steps, again.bpm, again.crossfade, again.direction, again.loop, again.on_beat),
                         (chase.lights, chase.steps, 96.0, 0.3, "bounce", False, True))
        seconds = element_chase(lights_element(Chase((A,), chase.steps[:1], seconds=2.5)))
        self.assertEqual(seconds.seconds, 2.5)

    def test_opacity_and_the_picture(self) -> None:
        import numpy as np

        from centralight.elements import RenderContext, render_element
        from centralight.lights import LightSet

        show = show_from_patch()
        element = dict(lights_element(Chase((A,), (look((A, (1.0, 0.0, 0.0, 1.0))),))), opacity=0.5)
        lights = LightSet(show.fixtures)
        per_light = render_element(element, RenderContext(0, 0, lights=lights, per_light=True))
        index = lights.ids.index(A)
        self.assertAlmostEqual(float(per_light[0, index]), 0.5, places=3)
        self.assertAlmostEqual(float(per_light[3, lights.ids.index(B)]), 0.0)
        picture = render_element(dict(element, opacity=1.0), RenderContext(320, 180, lights=lights))
        x, y = round(lights.x[index] * 320), round(lights.y[index] * 180)
        self.assertAlmostEqual(float(picture[0, y, x]), 1.0, places=3)
        self.assertAlmostEqual(float(picture[3, 0, 0]), 0.0)
        self.assertTrue(np.all(picture[1] == 0.0))


    def test_in_the_ai_guide_and_the_schema(self) -> None:
        """An AI tool can write one too: the guide explains it, and input.schema.json checks its steps."""
        from centralight.ai_guide import guide_text, input_schema

        show = with_inputs(show_from_patch())
        text = guide_text(show)
        self.assertIn('### Lights (`"kind": "lights"`)', text)
        self.assertIn("colour and brightness only", text)
        for setting in ("`crossfade`", "`direction`", "`on_beat`"):
            self.assertIn(setting, text)
        kinds = input_schema(show)["properties"]["tracks"]["items"]["items"]["oneOf"]
        lights = next(kind for kind in kinds if kind["properties"]["kind"]["const"] == "lights")
        steps = lights["properties"]["steps"]
        self.assertEqual(steps["items"]["propertyNames"]["enum"], [fx.id for fx in show.colour_fixtures()])
        self.assertEqual(steps["items"]["additionalProperties"]["minItems"], 4)
        saved = lights_element(Chase(("slimpar-101",), (ChaseStep(MappingProxyType({"slimpar-101": (1.0, 0.5, 0.0, 1.0)})),)))
        self.assertLessEqual(set(saved), set(lights["properties"]))  # (what Save as input writes is all in the schema)


if __name__ == "__main__":
    unittest.main()
